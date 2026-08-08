"""管理后台 API 路由。

需要管理员权限（is_admin=True）的所有端点。
提供平台级数据：用户列表、项目列表、聚合统计。

端点：
  - GET /api/admin/stats     平台概览统计
  - GET /api/admin/users     用户列表（分页）
  - GET /api/admin/projects  项目列表（分页）
"""

from fastapi import APIRouter, Depends, Query
from typing import Optional

from src.api.dependencies import require_admin
from src.domain.user import User
from src.services.user_service import UserService
from src.services.project_service import ProjectService
from src.services.plan_service import PlanService
from src.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin")
_user_service = UserService()
_project_service = ProjectService()
_plan_service = PlanService()


@router.get("/stats")
async def get_admin_stats(current_user: User = Depends(require_admin)):
    """平台概览统计。

    返回：总用户数、总项目数、总调用次数、月度调用量、活跃订阅数、今日注册/创建数。
    """
    total_users = _user_service.count_users()
    total_projects = _project_service.count_projects()
    all_stats = _project_service.get_all_stats()
    users_today = _user_service.count_users_today()
    projects_today = _project_service.count_projects_today()

    # 统计各套餐项目数
    # 遍历所有项目查询 plan 太慢，用数据库聚合
    # 简化：统计总项目数，前端可展示
    stats = {
        "total_users": total_users,
        "total_projects": total_projects,
        "total_calls": all_stats["total_calls"],
        "prompt_tokens": all_stats["prompt_tokens"],
        "completion_tokens": all_stats["completion_tokens"],
        "cache_hits": all_stats["cache_hits"],
        "cache_hit_rate": all_stats["cache_hit_rate"],
        "users_today": users_today,
        "projects_today": projects_today,
    }

    # 尝试获取活跃订阅数（从 billing.db 统计）
    try:
        stats["active_subscriptions"] = _plan_service.count_active_subscriptions()
    except Exception:
        stats["active_subscriptions"] = 0

    return stats


@router.get("/users")
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str = Query(""),
    current_user: User = Depends(require_admin),
):
    """用户列表（分页，可选搜索邮箱/名称）。"""
    return _user_service.list_all(page=page, page_size=page_size, search=search)


@router.get("/projects")
async def list_projects(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str = Query(""),
    current_user: User = Depends(require_admin),
):
    """项目列表（分页，可选搜索名称/ID）。"""
    return _project_service.list_all(page=page, page_size=page_size, search=search)