"""计费 API 路由。

端点：
  - GET  /api/billing/plan           获取当前套餐信息
  - POST /api/billing/create-checkout  创建 Stripe Checkout 会话
  - POST /api/billing/portal          跳转 Stripe 客户门户
  - POST /api/stripe/webhook          接收 Stripe Webhook 事件
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from src.api.dependencies import get_current_user
from src.domain.user import User
from src.services.plan_service import PlanService, PLAN_LIMITS
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/billing")
_plan_service = PlanService()


# ================================================================
# 请求/响应模型
# ================================================================


class PlanInfo(BaseModel):
    plan: str
    limits: dict
    usage: dict
    stripe_customer_id: str = ""


class CheckoutRequest(BaseModel):
    project_id: str
    plan: str = Field(..., pattern="^(pro|enterprise)$")


# ================================================================
# 路由
# ================================================================


@router.get("/plan", response_model=PlanInfo)
async def get_plan(
    project_id: str = Query("", description="项目 ID"),
    current_user: User = Depends(get_current_user),
):
    """获取项目套餐信息。

    优先使用 project_id 参数；未传时兼容旧调用（取第一个项目）。
    """
    from src.services.project_service import ProjectService
    project_svc = ProjectService()

    if project_id:
        project = project_svc.get_by_id(project_id)
        if not project or project.user_id != current_user.user_id:
            raise HTTPException(status_code=404, detail="项目不存在")
        target_id = project_id
    else:
        projects = project_svc.list_by_user(current_user.user_id)
        if not projects:
            raise HTTPException(status_code=404, detail="没有项目")
        target_id = projects[0].project_id

    plan = _plan_service.get_plan(target_id)
    limits = _plan_service.get_plan_limits(target_id)
    usage = _plan_service.get_usage(target_id)
    customer_id = _plan_service.get_stripe_customer_id(target_id)

    return PlanInfo(
        plan=plan,
        limits=limits,
        usage=usage,
        stripe_customer_id=customer_id,
    )


@router.post("/create-checkout")
async def create_checkout(
    body: CheckoutRequest,
    current_user: User = Depends(get_current_user),
):
    """创建 Stripe Checkout 结账会话。

    需要配置 STRIPE_SECRET_KEY。
    返回 url 供前端跳转。
    """
    if not settings.stripe.secret_key:
        raise HTTPException(status_code=503, detail="Stripe 未配置")

    try:
        import stripe
        stripe.api_key = settings.stripe.secret_key

        # 映射套餐到 Stripe Price ID
        price_map = {
            "pro": settings.stripe.price_pro,
            "enterprise": settings.stripe.price_enterprise,
        }
        price_id = price_map.get(body.plan)
        if not price_id:
            raise HTTPException(status_code=400, detail=f"未知套餐: {body.plan}")

        # 获取或创建 Stripe Customer
        customer_id = _plan_service.get_stripe_customer_id(body.project_id)
        if not customer_id:
            customer = stripe.Customer.create(
                email=current_user.email,
                metadata={"project_id": body.project_id, "user_id": current_user.user_id},
            )
            customer_id = customer.id

        # 检测是否有已有订阅（用于套餐切换 + 按比例退款/扣费）
        existing_sub_id = _plan_service.get_stripe_subscription_id(body.project_id)
        subscription_data = {}
        if existing_sub_id:
            subscription_data = {
                "proration_behavior": "create_prorations",
            }
            # 已有订阅时，Stripe 会自动处理订阅切换

        # 创建 Checkout Session
        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            subscription_data=subscription_data if subscription_data else None,
            success_url=f"{settings.api.frontend_url}/project/{body.project_id}/settings?checkout=success",
            cancel_url=f"{settings.api.frontend_url}/project/{body.project_id}/settings?checkout=cancel",
            metadata={"project_id": body.project_id, "plan": body.plan},
        )

        return {"url": session.url}

    except ImportError:
        raise HTTPException(status_code=503, detail="Stripe 库未安装")
    except Exception as e:
        logger.error(f"创建 Stripe Checkout 失败: {e}")
        raise HTTPException(status_code=500, detail="创建结账会话失败")


@router.post("/portal")
async def create_portal_session(
    current_user: User = Depends(get_current_user),
):
    """创建 Stripe 客户门户会话（管理订阅、查看账单）。"""
    if not settings.stripe.secret_key:
        raise HTTPException(status_code=503, detail="Stripe 未配置")

    try:
        import stripe
        stripe.api_key = settings.stripe.secret_key

        from src.services.project_service import ProjectService
        projects = ProjectService().list_by_user(current_user.user_id)
        if not projects:
            raise HTTPException(status_code=404, detail="没有项目")

        customer_id = _plan_service.get_stripe_customer_id(projects[0].project_id)
        if not customer_id:
            raise HTTPException(status_code=400, detail="没有 Stripe 客户 ID")

        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=f"{settings.api.frontend_url}/project/{projects[0].project_id}/settings",
        )
        return {"url": session.url}

    except ImportError:
        raise HTTPException(status_code=503, detail="Stripe 库未安装")
    except Exception as e:
        logger.error(f"创建门户会话失败: {e}")
        raise HTTPException(status_code=500, detail="创建门户会话失败")


# ================================================================
# Stripe Webhook
# ================================================================


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    """接收 Stripe Webhook 事件。

    需要配置 STRIPE_WEBHOOK_SECRET。
    在 Stripe Dashboard 中配置 webhook 指向此端点。
    """
    if not settings.stripe.webhook_secret:
        raise HTTPException(status_code=503, detail="Stripe Webhook 未配置")

    try:
        import stripe
        stripe.api_key = settings.stripe.secret_key

        payload = await request.body()
        sig_header = request.headers.get("stripe-signature", "")

        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.stripe.webhook_secret
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的请求体")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="无效的签名")
    except ImportError:
        raise HTTPException(status_code=503, detail="Stripe 库未安装")

    # 处理事件
    event_type = event["type"]
    logger.info(f"收到 Stripe Webhook: {event_type}")

    # Stripe Price ID → plan 映射
    price_to_plan = {}
    for plan_name, price_id in [
        ("pro", settings.stripe.price_pro),
        ("enterprise", settings.stripe.price_enterprise),
    ]:
        if price_id:
            price_to_plan[price_id] = plan_name

    if event_type == "checkout.session.completed":
        session = event["data"]["object"]
        project_id = session.get("metadata", {}).get("project_id", "")
        plan = session.get("metadata", {}).get("plan", "free")
        subscription_id = session.get("subscription", "")
        customer_id = session.get("customer", "")

        if project_id:
            # 取消旧订阅（避免切换套餐时新旧两个订阅并存导致重复扣费）
            old_sub_id = _plan_service.get_stripe_subscription_id(project_id)
            if old_sub_id and old_sub_id != subscription_id:
                try:
                    import stripe
                    stripe.api_key = settings.stripe.secret_key
                    stripe.Subscription.cancel(old_sub_id)
                    logger.info(f"旧订阅已取消: {old_sub_id}")
                except Exception as e:
                    logger.warning(f"取消旧订阅失败（可能已取消）: {e}")

            _plan_service.set_plan(
                project_id=project_id,
                plan=plan,
                stripe_subscription_id=subscription_id,
                stripe_customer_id=customer_id,
            )
            logger.info(f"订阅完成: {project_id} → {plan}")

    elif event_type == "invoice.paid":
        # 续费成功，无需额外操作
        pass

    elif event_type == "customer.subscription.deleted":
        # 订阅取消，降级为 Free
        subscription = event["data"]["object"]
        sub_id = subscription.get("id", "")
        if sub_id:
            project_id = _plan_service.get_project_id_by_subscription(sub_id)
            if project_id:
                _plan_service.set_plan(project_id, "free")
                logger.info(f"订阅取消，已降级: {project_id} → free")

    elif event_type == "customer.subscription.updated":
        """订阅更新（用户在 Stripe 门户切换套餐时触发）。

        根据 subscription.items[0].price.id 映射到 plan，
        同步更新 project_plans 表。
        """
        subscription = event["data"]["object"]
        sub_id = subscription.get("id", "")
        items = subscription.get("items", {}).get("data", [])
        if not sub_id or not items:
            return {"status": "ok"}

        price_id = items[0].get("price", {}).get("id", "")
        new_plan = price_to_plan.get(price_id, "")
        if not new_plan:
            logger.warning(f"未知 Price ID: {price_id}，跳过套餐更新")
            return {"status": "ok"}

        project_id = _plan_service.get_project_id_by_subscription(sub_id)
        if project_id:
            _plan_service.set_plan(
                project_id=project_id,
                plan=new_plan,
                stripe_subscription_id=sub_id,
            )
            logger.info(f"订阅更新: {project_id} → {new_plan}")

    return {"status": "ok"}


# ================================================================
# 账单查询
# ================================================================


@router.get("/invoices")
async def get_invoices(
    current_user: User = Depends(get_current_user),
):
    """获取最近账单列表（从 Stripe 拉取）。

    取当前用户第一个项目的 Stripe 客户 ID，返回最近 12 条发票。
    """
    if not settings.stripe.secret_key:
        raise HTTPException(status_code=503, detail="Stripe 未配置")

    try:
        import stripe
        stripe.api_key = settings.stripe.secret_key

        from src.services.project_service import ProjectService
        projects = ProjectService().list_by_user(current_user.user_id)
        if not projects:
            raise HTTPException(status_code=404, detail="没有项目")

        customer_id = _plan_service.get_stripe_customer_id(projects[0].project_id)
        if not customer_id:
            return {"items": [], "total": 0}

        invoices = stripe.Invoice.list(
            customer=customer_id,
            limit=12,
            status="paid",
        )

        items = []
        for inv in invoices.auto_paging_iter():
            items.append({
                "id": inv.id,
                "amount_paid": inv.amount_paid,
                "currency": inv.currency,
                "status": inv.status,
                "created": inv.created,
                "invoice_pdf": inv.invoice_pdf,
                "number": inv.number,
            })

        return {"items": items, "total": len(items)}
    except ImportError:
        raise HTTPException(status_code=503, detail="Stripe 库未安装")
    except Exception as e:
        logger.error(f"获取账单失败: {e}")
        raise HTTPException(status_code=500, detail="获取账单失败")