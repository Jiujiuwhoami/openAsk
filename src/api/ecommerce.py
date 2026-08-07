"""电商相关 API 路由。

端点：
  - POST  /api/projects/{id}/import-products  导入商品
  - GET   /api/templates                       FAQ 模板列表
  - POST  /api/projects/{id}/templates/{tid}   应用模板
"""

import os
import tempfile

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from pydantic import BaseModel

from src.api.dependencies import get_current_user, get_current_project
from src.domain.user import User
from src.domain.project import Project
from src.services.product_import import ProductImportService, FAQTemplateService
from src.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()
_import_service = ProductImportService()
_template_service = FAQTemplateService()


# ================================================================
# 商品导入
# ================================================================


@router.post("/api/projects/{project_id}/import-products")
async def import_products(
    project_id: str,
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """导入商品到知识库。

    支持 CSV / JSON 格式：
    - CSV: 商品名称, 商品描述, 规格, 价格, 库存, 标签
    - JSON: [{ "name": "...", "description": "...", "price": "..." }]
    """
    # 验证项目所有权
    from src.services.project_service import ProjectService
    project = ProjectService().get_by_id(project_id)
    if not project or project.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 读取文件
    content = await file.read()
    try:
        text_content = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="文件编码不支持，请使用 UTF-8")

    # 获取 knowledge_service
    knowledge_service = getattr(request.app.state, "knowledge_service", None)
    if not knowledge_service:
        raise HTTPException(status_code=503, detail="知识库服务未初始化")

    # 导入
    result = _import_service.import_products(
        file_content=text_content,
        filename=file.filename or "import.csv",
        knowledge_service=knowledge_service,
        project_id=project_id,
    )

    return result


# ================================================================
# FAQ 模板
# ================================================================


@router.get("/api/templates")
async def list_templates():
    """获取 FAQ 模板列表。"""
    return _template_service.list_templates()


@router.get("/api/templates/{template_id}")
async def get_template(template_id: str):
    """获取模板详情。"""
    template = _template_service.get_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="模板不存在")
    return template


@router.post("/api/projects/{project_id}/templates/{template_id}")
async def apply_template(
    project_id: str,
    template_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """应用 FAQ 模板到项目。"""
    # 验证项目所有权
    from src.services.project_service import ProjectService
    project = ProjectService().get_by_id(project_id)
    if not project or project.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 验证模板存在
    template = _template_service.get_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="模板不存在")

    # 获取 knowledge_service
    knowledge_service = getattr(request.app.state, "knowledge_service", None)
    if not knowledge_service:
        raise HTTPException(status_code=503, detail="知识库服务未初始化")

    # 应用模板
    result = await _template_service.apply_template(
        template_id=template_id,
        knowledge_service=knowledge_service,
        project_id=project_id,
    )

    return result