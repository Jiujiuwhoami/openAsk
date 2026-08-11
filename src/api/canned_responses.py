"""话术库 API 路由。

端点：
  - GET    /api/projects/{id}/canned-responses        列表
  - POST   /api/projects/{id}/canned-responses        创建
  - PUT    /api/projects/{id}/canned-responses/{rid}  更新
  - DELETE /api/projects/{id}/canned-responses/{rid}  删除
  - GET    /api/projects/{id}/canned-responses/categories  分类列表
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.api.dependencies import get_current_user
from src.domain.user import User
from src.services.canned_response_service import CannedResponseService
from src.services.project_service import ProjectService
from src.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter()
_canned_service = CannedResponseService()
_project_service = ProjectService()


class CannedResponseCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=100)
    content: str = Field(..., min_length=1, max_length=5000)
    category: str = Field("", max_length=50)
    shortcut: str = Field("", max_length=50)
    is_global: bool = Field(False)


class CannedResponseUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=100)
    content: Optional[str] = Field(None, min_length=1, max_length=5000)
    category: Optional[str] = Field(None, max_length=50)
    shortcut: Optional[str] = Field(None, max_length=50)
    is_global: Optional[bool] = None


def _verify_project_owner(project_id: str, user: User):
    """验证用户是项目所有者。"""
    project = _project_service.get_by_id(project_id)
    if not project or project.user_id != user.user_id:
        raise HTTPException(status_code=404, detail="项目不存在")


@router.get("/api/projects/{project_id}/canned-responses")
async def list_canned_responses(
    project_id: str,
    category: str = Query(""),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
):
    """获取话术列表（项目级 + 个人级）。"""
    _verify_project_owner(project_id, current_user)
    return _canned_service.list(
        project_id,
        user_id=current_user.user_id,
        category=category,
        page=page,
        page_size=page_size,
    )


@router.post("/api/projects/{project_id}/canned-responses")
async def create_canned_response(
    project_id: str,
    body: CannedResponseCreate,
    current_user: User = Depends(get_current_user),
):
    """创建话术。"""
    _verify_project_owner(project_id, current_user)
    response_id = _canned_service.create(
        project_id=project_id,
        user_id=current_user.user_id,
        title=body.title,
        content=body.content,
        category=body.category,
        shortcut=body.shortcut,
        is_global=body.is_global,
    )
    response = _canned_service.get_by_id(response_id)
    return {"success": True, "item": response}


@router.put("/api/projects/{project_id}/canned-responses/{response_id}")
async def update_canned_response(
    project_id: str,
    response_id: int,
    body: CannedResponseUpdate,
    current_user: User = Depends(get_current_user),
):
    """更新话术。"""
    _verify_project_owner(project_id, current_user)
    existing = _canned_service.get_by_id(response_id)
    if not existing:
        raise HTTPException(status_code=404, detail="话术不存在")
    # 权限检查：只有创建者或项目所有者可修改
    if not existing["is_global"] and existing["user_id"] != current_user.user_id:
        raise HTTPException(status_code=403, detail="无权修改该话术")

    kwargs = {k: v for k, v in body.model_dump().items() if v is not None}
    updated = _canned_service.update(response_id, **kwargs)
    if not updated:
        raise HTTPException(status_code=400, detail="话术不存在")
    return {"success": True, "item": _canned_service.get_by_id(response_id)}


@router.delete("/api/projects/{project_id}/canned-responses/{response_id}")
async def delete_canned_response(
    project_id: str,
    response_id: int,
    current_user: User = Depends(get_current_user),
):
    """删除话术。"""
    _verify_project_owner(project_id, current_user)
    existing = _canned_service.get_by_id(response_id)
    if not existing:
        raise HTTPException(status_code=404, detail="话术不存在")
    # 权限检查：只有创建者或项目所有者可删除
    if not existing["is_global"] and existing["user_id"] != current_user.user_id:
        raise HTTPException(status_code=403, detail="无权删除该话术")

    _canned_service.delete(response_id)
    return {"success": True, "message": "话术已删除"}


@router.get("/api/projects/{project_id}/canned-responses/categories")
async def list_canned_categories(
    project_id: str,
    current_user: User = Depends(get_current_user),
):
    """获取话术分类列表。"""
    _verify_project_owner(project_id, current_user)
    categories = _canned_service.list_categories(project_id)
    return {"items": _canned_service.DEFAULT_CATEGORIES + [
        c for c in categories if c not in _canned_service.DEFAULT_CATEGORIES
    ]}