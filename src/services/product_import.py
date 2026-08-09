"""商品信息导入服务。

支持 CSV / JSON 格式批量导入商品信息到知识库。
自动将每个商品生成一篇知识库文档，包含商品名称、描述、规格、价格等。
"""

import csv
import io
import json
import os
from typing import Dict, List, Optional, Tuple

from src.utils.logger import get_logger

logger = get_logger(__name__)

# 预置 FAQ 模板
FAQ_TEMPLATES = {
    "return_policy": {
        "name": "退换货流程",
        "description": "退换货政策、流程、条件、时效",
        "documents": [
            {
                "title": "退换货政策",
                "content": """## 退换货政策

### 7天无理由退货
- 自签收之日起7天内，商品完好未使用，可申请无理由退货
- 退货商品需保持原包装完好
- 不影响二次销售

### 换货政策
- 自签收之日起15天内，商品存在质量问题，可申请换货
- 换货产生的运费由我们承担

### 退货流程
1. 登录账号 → 我的订单 → 申请售后
2. 选择退货/换货 → 填写原因
3. 等待审核（1-3个工作日）
4. 审核通过 → 寄回商品
5. 仓库验收 → 退款/换货发出

### 退款说明
- 退货审核通过后，退款将在3-5个工作日内原路返回
- 使用优惠券的订单，退款金额按实际支付金额计算""",
                "tags": ["退货", "换货", "退款", "政策"],
            },
            {
                "title": "退换货条件",
                "content": """## 退换货条件

### 支持退换货的情况
- 商品质量问题
- 商品与描述不符
- 运输过程中损坏
- 发错商品
- 7天无理由退货

### 不支持退换货的情况
- 商品已使用或穿戴
- 商品吊牌已拆除
- 个人卫生用品（内衣、袜子等）
- 定制商品
- 超过退换货期限
- 赠品（非质量问题）""",
                "tags": ["退货条件", "换货条件", "规则"],
            },
        ],
    },
    "shipping": {
        "name": "物流配送",
        "description": "物流方式、运费、配送时间、国际物流",
        "documents": [
            {
                "title": "配送说明",
                "content": """## 配送说明

### 配送方式
- 标准快递：3-5个工作日
- 加急快递：1-2个工作日
- 国际物流：7-15个工作日

### 运费标准
- 满99元包邮（标准快递）
- 不满99元：运费8元
- 加急快递：15元
- 国际物流：按实际重量计算

### 配送范围
- 全国范围（港澳台地区除外）
- 支持 200+ 国家和地区国际配送

### 物流查询
- 订单发货后，可在「我的订单」中查看物流信息
- 物流异常请联系客服""",
                "tags": ["物流", "配送", "运费", "快递"],
            },
        ],
    },
    "payment": {
        "name": "支付方式",
        "description": "支付方式、发票、优惠券使用",
        "documents": [
            {
                "title": "支付方式说明",
                "content": """## 支付方式

### 支持的支付方式
- 微信支付
- 支付宝
- 银行卡（借记卡/信用卡）
- PayPal（国际用户）

### 发票说明
- 可开具电子普通发票
- 下单时填写发票信息
- 发票将在订单完成后发送至邮箱

### 优惠券使用
- 每个订单限用一张优惠券
- 优惠券不可叠加使用
- 部分商品不支持优惠券""",
                "tags": ["支付", "发票", "优惠券"],
            },
        ],
    },
    "after_sale": {
        "name": "售后服务",
        "description": "售后流程、维修、投诉",
        "documents": [
            {
                "title": "售后服务",
                "content": """## 售后服务

### 保修政策
- 电子类商品：1年质保
- 服装类商品：3个月质保
- 质保期内非人为损坏免费维修

### 维修流程
1. 联系客服 → 提交维修申请
2. 寄回商品
3. 检测维修（3-7个工作日）
4. 寄回完成

### 投诉建议
- 客服电话：400-xxx-xxxx
- 在线客服：9:00-21:00
- 投诉邮箱：complaint@example.com""",
                "tags": ["售后", "保修", "维修", "投诉"],
            },
        ],
    },
}


class ProductImportService:
    """商品导入服务。"""

    @staticmethod
    def parse_csv(content: str) -> List[Dict]:
        """解析 CSV 商品数据。

        CSV 格式要求：
            商品名称, 商品描述, 规格, 价格, 库存, 标签
        """
        reader = csv.DictReader(io.StringIO(content))
        products = []
        for row in reader:
            # 兼容中英文列名
            product = {
                "name": row.get("商品名称") or row.get("name", ""),
                "description": row.get("商品描述") or row.get("description", ""),
                "spec": row.get("规格") or row.get("spec", ""),
                "price": row.get("价格") or row.get("price", ""),
                "stock": row.get("库存") or row.get("stock", "0"),
                "tags": row.get("标签") or row.get("tags", ""),
            }
            if product["name"]:
                products.append(product)
        return products

    @staticmethod
    def parse_json(content: str) -> List[Dict]:
        """解析 JSON 商品数据。"""
        data = json.loads(content)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "products" in data:
            return data["products"]
        return []

    @staticmethod
    def product_to_document(product: Dict) -> Dict:
        """将商品信息转为知识库文档。"""
        tags = []
        if product.get("tags"):
            tags = [t.strip() for t in product["tags"].split(",") if t.strip()]
        tags.extend(["商品"])

        # 构建文档内容
        parts = []
        if product.get("description"):
            parts.append(product["description"])
        if product.get("spec"):
            parts.append(f"规格：{product['spec']}")
        if product.get("price"):
            parts.append(f"价格：{product['price']}")
        if product.get("stock"):
            parts.append(f"库存：{product['stock']}")

        content = "\n\n".join(parts) if parts else product.get("description", "")

        return {
            "title": product["name"],
            "content": content,
            "tags": tags,
            "source": "product_import",
        }

    def import_products(
        self,
        file_content: str,
        filename: str,
        knowledge_service,
        project_id: str,
    ) -> dict:
        """导入商品到知识库。

        Args:
            file_content: 文件内容
            filename: 文件名（用于判断格式）
            knowledge_service: KnowledgeService 实例
            project_id: 项目 ID

        Returns:
            {"success": int, "failed": int, "errors": [str]}
        """
        import asyncio

        # 解析
        ext = os.path.splitext(filename)[1].lower()
        if ext == ".csv":
            products = self.parse_csv(file_content)
        elif ext == ".json":
            products = self.parse_json(file_content)
        else:
            return {"success": 0, "failed": 0, "errors": [f"不支持的文件格式: {ext}"]}

        if not products:
            return {"success": 0, "failed": 0, "errors": ["未找到商品数据"]}

        # 导入
        success = 0
        failed = 0
        errors = []

        for product in products:
            try:
                doc = self.product_to_document(product)
                # 同步调用
                import asyncio
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(
                        knowledge_service.create_document_from_text(
                            title=doc["title"],
                            content=doc["content"],
                            tags=doc["tags"],
                            source=doc["source"],
                            project_id=project_id,
                        )
                    )
                    success += 1
                finally:
                    loop.close()
            except Exception as e:
                failed += 1
                errors.append(f"{product.get('name', 'unknown')}: {str(e)[:50]}")

        logger.info(f"商品导入完成: {success} 成功, {failed} 失败")
        return {"success": success, "failed": failed, "errors": errors[:10]}


class FAQTemplateService:
    """FAQ 模板服务。

    模板的"应用状态"基于知识库中的实际文档动态判断（方案B）：
    当项目的 source="template" 文档标题覆盖模板的所有文档标题时，
    该模板被视为已应用。删除模板文档后状态自动回落，可重新应用。
    """

    @staticmethod
    async def _get_applied_titles(
        project_id: str,
        knowledge_service,
    ) -> set:
        """查询项目中 source="template" 的文档标题集合。"""
        if not knowledge_service:
            return set()
        docs = await knowledge_service.list_documents_by_source(
            "template", project_id=project_id
        )
        return {d.title for d in docs}

    @staticmethod
    async def list_templates(
        project_id: Optional[str] = None,
        knowledge_service=None,
    ) -> List[Dict]:
        """列出所有可用模板。

        传入 project_id 和 knowledge_service 时，通过查询知识库中
        source="template" 的文档来计算每个模板的应用状态（方案B）。
        删掉模板文档后状态自动变为未应用。

        Returns:
            每个模板包含 applied_count（已存在的文档数）和
            applied（全部文档已存在时为 True）。
        """
        applied_titles = set()
        if project_id and knowledge_service:
            applied_titles = await FAQTemplateService._get_applied_titles(
                project_id, knowledge_service
            )

        return [
            {
                "id": tid,
                "name": tmpl["name"],
                "description": tmpl["description"],
                "document_count": len(tmpl["documents"]),
                "applied_count": len(
                    {d["title"] for d in tmpl["documents"]} & applied_titles
                ),
                "applied": (
                    len(tmpl["documents"]) > 0
                    and {d["title"] for d in tmpl["documents"]}.issubset(applied_titles)
                ),
            }
            for tid, tmpl in FAQ_TEMPLATES.items()
        ]

    @staticmethod
    def get_template(template_id: str) -> Optional[Dict]:
        """获取模板详情。"""
        return FAQ_TEMPLATES.get(template_id)

    @staticmethod
    async def apply_template(
        template_id: str,
        knowledge_service,
        project_id: str,
    ) -> dict:
        """应用模板到项目（幂等）。

        逐个检查模板文档在知识库中是否已存在（按标题 + source="template" 匹配），
        已存在的跳过，缺失的才创建。全部已存在时返回 already_applied。
        删掉部分文档后重新应用，会补全缺失的文档（部分应用恢复）。
        """
        template = FAQ_TEMPLATES.get(template_id)
        if not template:
            return {"success": 0, "failed": 0, "errors": [f"模板不存在: {template_id}"]}

        # 查询项目里已有的模板文档标题
        existing_docs = await knowledge_service.list_documents_by_source(
            "template", project_id=project_id
        )
        existing_titles = {d.title for d in existing_docs}

        doc_titles = {d["title"] for d in template["documents"]}
        if doc_titles and doc_titles.issubset(existing_titles):
            logger.info(
                f"模板文档已全部存在，跳过: {template_id} → {project_id}"
            )
            return {
                "success": 0,
                "failed": 0,
                "already_applied": True,
                "errors": ["该模板已应用到当前项目"],
            }

        # 逐文档导入：已存在的跳过，缺失的创建
        success = 0
        failed = 0
        errors = []

        for doc in template["documents"]:
            if doc["title"] in existing_titles:
                # 已存在（可能是上次部分应用），跳过避免重复导入
                logger.debug(f"模板文档已存在，跳过: {doc['title']}")
                continue
            try:
                await knowledge_service.create_document_from_text(
                    title=doc["title"],
                    content=doc["content"],
                    tags=doc["tags"],
                    source="template",
                    project_id=project_id,
                    skip_duplicate_check=True,
                )
                success += 1
            except Exception as e:
                failed += 1
                errors.append(f"{doc['title']}: {str(e)[:50]}")

        logger.info(
            f"模板应用完成: {template_id} → {success} 新建, {failed} 失败"
        )
        return {"success": success, "failed": failed, "errors": errors[:10]}