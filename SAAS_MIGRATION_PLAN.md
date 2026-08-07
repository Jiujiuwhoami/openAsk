# OpenAsk SaaS 改造清单

> 目标：从单层多租户管理后台 → 用户自助注册 + 多项目（Project）的 SaaS 平台
> 架构：User → Project（类似 Supabase 的用户 → Project 模型）
> 数据库：全程 SQLite，先跑通流程
> 设计原则：不保留旧版本兼容代码，一步到位全部使用新标准

---

## 目录

1. [清理清单](#1-清理清单)
2. [Step 1：后端用户系统 + Project 体系](#2-step-1-后端用户系统--project-体系)
3. [Step 2：现有 API 适配](#3-step-2-现有-api-适配)
4. [Step 3：前端重构](#4-step-3-前端重构)
5. [Step 4：嵌入脚本](#5-step-4-嵌入脚本)
6. [Step 5：数据迁移](#6-step-5-数据迁移)
7. [文件变更总清单](#7-文件变更总清单)

---

## 1. 清理清单

改造前先清理旧代码，移除所有与 Tenant 相关的内容。

### 删除的文件

| 文件 | 原因 |
|------|------|
| `src/domain/models.py` | 删除 Tenant 类，User 和 Project 分别建新文件 |
| `src/services/tenant_service.py` | 替换为 ProjectService |
| `src/services/tenant_stats.py` | 替换为 ProjectStats 集成到 ProjectService |
| `src/domain/exceptions.py` 中的 Tenant 相关异常 | 不需要了 |

### 从现有文件中移除的内容

**`src/api/routes.py`**:
- 删除 `admin_router` 和所有 `/api/admin/tenants` 路由
- 删除 `_tenant_service` 全局变量
- 删除 `_verify_admin_key` 函数
- 删除 `resolve_tenant` → 替换为 `resolve_project`
- 删除 `resolve_optional_tenant`
- 删除所有 Tenant schema 的 import

**`src/api/main.py`**:
- 删除 `TenantService` 的 import 和初始化
- 删除 `ensure_default_tenant()` 调用
- 删除 `app.state.tenant_service`

**`src/utils/config.py`**:
- 删除 `TenantStorageSettings` 类
- `settings.tenant` 引用全部移除

**`src/api/schemas.py`**:
- 删除所有 Tenant 相关的 Schema（`TenantResponse`, `CreateTenantRequest`, `UpdateTenantRequest`, `TenantKeyResponse`, `TenantStatsResponse`）

**前端**:
- 删除 `src/views/auth/TenantSelect.vue`
- 删除 `src/views/admin/TenantList.vue`
- 删除 `src/api/tenant.ts`
- 删除 `src/stores/app.ts`（或大幅精简，只保留通用状态）
- 从 `src/api/types.ts` 删除 Tenant 相关类型

### 保留但修改的文件

| 文件 | 修改内容 |
|------|----------|
| `src/api/routes.py` | `resolve_tenant` → `resolve_project`，所有 `tenant_id` → `project_id` |
| `src/api/schemas.py` | 删除 Tenant schema，新增 Project schema |
| `src/api/main.py` | 删除 TenantService 初始化 |
| `src/utils/config.py` | 删除 TenantStorageSettings，新增 AuthSettings |
| `src/infrastructure/zvec_store.py` | `tenant_id` → `project_id` |
| `src/services/knowledge_service.py` | `tenant_id` → `project_id` |
| `src/core/factory.py` | `tenant_id` → `project_id` |
| `src/core/retriever.py` | `tenant_id` → `project_id` |
| `src/utils/limiter.py` | `tenant_id` → `project_id` |

---

## 2. Step 1：后端用户系统 + Project 体系

### 2.1 新增依赖

**文件**: `requirements.txt`

```diff
+ passlib[bcrypt]>=1.9.0          # 密码哈希（标准库）
+ python-jose[cryptography]>=3.3.0 # JWT 标准实现
+ email-validator>=2.2.0          # 邮箱格式校验
```

### 2.2 User 领域模型

**新建文件**: `src/domain/user.py`

```python
"""领域模型：User（平台用户）。"""

import time
from typing import Optional


class User:
    """实体：平台用户，由 email 唯一标识。"""

    def __init__(
        self,
        user_id: str,
        email: str,
        password_hash: str,
        name: str = "",
        is_verified: bool = False,
        is_active: bool = True,
        created_at: Optional[int] = None,
        updated_at: Optional[int] = None,
    ):
        self._user_id = user_id
        self._email = email
        self._password_hash = password_hash
        self._name = name
        self._is_verified = is_verified
        self._is_active = is_active
        now = int(time.time())
        self._created_at = created_at if created_at is not None else now
        self._updated_at = updated_at if updated_at is not None else now

    @property
    def user_id(self) -> str: return self._user_id
    @property
    def email(self) -> str: return self._email
    @property
    def password_hash(self) -> str: return self._password_hash
    @property
    def name(self) -> str: return self._name
    @property
    def is_verified(self) -> bool: return self._is_verified
    @property
    def is_active(self) -> bool: return self._is_active
    @property
    def created_at(self) -> int: return self._created_at
    @property
    def updated_at(self) -> int: return self._updated_at

    def verify(self) -> None:
        self._is_verified = True
        self._updated_at = int(time.time())
```

### 2.3 Project 领域模型

**新建文件**: `src/domain/project.py`

```python
"""领域模型：Project（用户的项目/知识库空间）。"""

import time
from typing import Optional


class Project:
    """实体：项目，一个用户可创建多个项目，每个项目有独立 API Key 和知识库。"""

    def __init__(
        self,
        project_id: str,
        user_id: str,
        api_key: str,
        name: str,
        status: str = "active",
        llm_api_key: str = "",
        llm_api_base: str = "",
        llm_model: str = "",
        llm_timeout: int = 30,
        rate_limit_per_user: str = "60/minute",
        rate_limit_global: str = "1000/minute",
        system_prompt: str = "",
        created_at: Optional[int] = None,
        updated_at: Optional[int] = None,
    ):
        self._project_id = project_id
        self._user_id = user_id
        self._api_key = api_key
        self._name = name
        self._status = status
        self._llm_api_key = llm_api_key
        self._llm_api_base = llm_api_base
        self._llm_model = llm_model
        self._llm_timeout = llm_timeout
        self._rate_limit_per_user = rate_limit_per_user
        self._rate_limit_global = rate_limit_global
        self._system_prompt = system_prompt
        now = int(time.time())
        self._created_at = created_at if created_at is not None else now
        self._updated_at = updated_at if updated_at is not None else now

    @property
    def project_id(self) -> str: return self._project_id
    @property
    def user_id(self) -> str: return self._user_id
    @property
    def api_key(self) -> str: return self._api_key
    @property
    def name(self) -> str: return self._name
    @property
    def status(self) -> str: return self._status
    @property
    def llm_api_key(self) -> str: return self._llm_api_key
    @property
    def llm_api_base(self) -> str: return self._llm_api_base
    @property
    def llm_model(self) -> str: return self._llm_model
    @property
    def llm_timeout(self) -> int: return self._llm_timeout
    @property
    def rate_limit_per_user(self) -> str: return self._rate_limit_per_user
    @property
    def rate_limit_global(self) -> str: return self._rate_limit_global
    @property
    def system_prompt(self) -> str: return self._system_prompt
    @property
    def created_at(self) -> int: return self._created_at
    @property
    def updated_at(self) -> int: return self._updated_at

    @property
    def is_active(self) -> bool:
        return self._status == "active"
```

### 2.4 异常定义

**修改文件**: `src/domain/exceptions.py`

```python
# 删除 Tenant 相关异常，新增：

class UserNotFoundError(AppError):
    """用户不存在。"""
    pass

class UserAlreadyExistsError(AppError):
    """用户已存在（邮箱重复）。"""
    pass

class InvalidCredentialsError(AppError):
    """邮箱或密码错误。"""
    pass

class UserNotVerifiedError(AppError):
    """邮箱未验证。"""
    pass

class UserSuspendedError(AppError):
    """用户已被禁用。"""
    pass

class ProjectNotFoundError(AppError):
    """项目不存在。"""
    pass

class ProjectSuspendedError(AppError):
    """项目已被禁用。"""
    pass
```

### 2.5 User Service

**新建文件**: `src/services/user_service.py`

```python
"""用户服务：注册、登录、JWT 管理。"""

from passlib.context import CryptContext
from jose import JWTError, jwt
from datetime import datetime, timedelta
from src.utils.config import settings

# passlib 标准密码上下文（自动管理哈希算法和盐值）
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class UserService:
    """用户管理服务：注册、登录、JWT Token 生成与验证。"""

    def __init__(self, db_path: str = None):
        self._db_path = db_path or "data/users.db"
        self._ensure_db()

    # ---- 密码管理 ----

    @staticmethod
    def hash_password(password: str) -> str:
        """passlib 标准哈希。"""
        return pwd_context.hash(password)

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """passlib 标准验证。"""
        return pwd_context.verify(plain_password, hashed_password)

    # ---- JWT 管理 ----

    @staticmethod
    def create_access_token(user_id: str, expires_delta: timedelta = None) -> str:
        """生成符合 OAuth2 标准的 JWT access token。"""
        expire = datetime.utcnow() + (expires_delta or timedelta(minutes=settings.auth.access_token_expire_minutes))
        claims = {
            "sub": user_id,
            "exp": expire,
            "iat": datetime.utcnow(),
            "type": "access",
        }
        return jwt.encode(claims, settings.auth.secret_key, algorithm=settings.auth.algorithm)

    @staticmethod
    def decode_access_token(token: str) -> dict:
        """解码并验证 JWT token。"""
        try:
            return jwt.decode(token, settings.auth.secret_key, algorithms=[settings.auth.algorithm])
        except JWTError:
            return None

    # ---- 用户 CRUD ----

    def register(self, email: str, password: str, name: str = "") -> User:
        """注册新用户。"""
        existing = self.get_by_email(email)
        if existing:
            raise UserAlreadyExistsError(f"邮箱已注册: {email}")
        user_id = f"user_{secrets.token_hex(8)}"
        password_hash = self.hash_password(password)
        now = int(time.time())
        # SQL INSERT ...
        return self.get_by_id(user_id)

    def authenticate(self, email: str, password: str) -> User:
        """验证用户凭证（OAuth2 标准流程）。"""
        user = self.get_by_email(email)
        if not user or not self.verify_password(password, user.password_hash):
            raise InvalidCredentialsError("邮箱或密码错误")
        if not user.is_active:
            raise UserSuspendedError("用户已被禁用")
        return user

    def get_by_id(self, user_id: str) -> Optional[User]: ...
    def get_by_email(self, email: str) -> Optional[User]: ...
    def verify_email(self, user_id: str) -> User: ...
    def change_password(self, user_id: str, old_password: str, new_password: str) -> User: ...
```

### 2.6 Project Service

**新建文件**: `src/services/project_service.py`

```python
"""项目服务：CRUD、API Key 鉴权、统计。"""

import secrets
import sqlite3
import time
from typing import List, Optional
from src.domain.project import Project
from src.domain.exceptions import ProjectNotFoundError, ProjectSuspendedError


class ProjectService:
    """项目管理服务。"""

    def __init__(self, db_path: str = None):
        self._db_path = db_path or "data/projects.db"
        self._ensure_db()

    def create_project(
        self,
        user_id: str,
        name: str,
        api_key: str = None,
        **kwargs
    ) -> Project:
        """创建新项目（自动生成 API Key）。"""
        project_id = f"proj_{secrets.token_hex(8)}"
        key = api_key or f"sk_{secrets.token_hex(24)}"
        now = int(time.time())
        # SQL INSERT ...
        return self.get_by_id(project_id)

    def get_by_id(self, project_id: str) -> Optional[Project]: ...
    def get_by_api_key(self, api_key: str) -> Optional[Project]: ...
    def list_by_user(self, user_id: str) -> List[Project]: ...
    def update_project(self, project_id: str, **kwargs) -> Project: ...
    def delete_project(self, project_id: str) -> bool: ...
    def rotate_api_key(self, project_id: str) -> str: ...
    def get_stats(self, project_id: str) -> dict: ...
    def record_call(self, project_id: str, prompt_tokens: int, completion_tokens: int, cache_hit: bool): ...
```

**SQLite 表结构**:

```sql
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name TEXT DEFAULT '',
    is_verified INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    api_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    llm_api_key TEXT DEFAULT '',
    llm_api_base TEXT DEFAULT '',
    llm_model TEXT DEFAULT '',
    llm_timeout INTEGER DEFAULT 30,
    rate_limit_per_user TEXT DEFAULT '60/minute',
    rate_limit_global TEXT DEFAULT '1000/minute',
    system_prompt TEXT DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS project_stats (
    project_id TEXT PRIMARY KEY,
    total_calls INTEGER DEFAULT 0,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    cache_hits INTEGER DEFAULT 0,
    last_call_at INTEGER DEFAULT 0,
    FOREIGN KEY (project_id) REFERENCES projects(project_id)
);
```

### 2.7 Auth 配置

**追加到**: `src/utils/config.py`

```python
class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(**_SETTINGS_BASE_CONFIG, env_prefix="AUTH_")
    secret_key: str = "change-me-in-production"  # 生产环境务必修改
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24  # 24 hours
```

**删除** `TenantStorageSettings` 类。

### 2.8 Auth API 路由（OAuth2 标准）

**新建文件**: `src/api/auth.py`

```python
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from src.api.dependencies import get_current_user
from src.services.user_service import UserService
from src.services.project_service import ProjectService
from src.domain.user import User
from src.domain.exceptions import UserAlreadyExistsError, InvalidCredentialsError, UserSuspendedError

router = APIRouter(prefix="/api/auth")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")


@router.post("/register")
async def register(body: RegisterRequest):
    """
    用户注册 + 自动登录 + 自动创建第一个 Project。
    
    请求: { "email": "...", "password": "...", "name": "..." }
    响应: {
        "access_token": "eyJ...",
        "token_type": "bearer",
        "user": { "user_id", "email", "name" },
        "project": { "project_id", "name", "api_key" }
    }
    
    测试标准:
      - 正常注册 → 200 + 返回 token + 用户信息 + 项目信息
      - 重复邮箱 → 409
      - 密码太短(<8) → 422
      - 无效邮箱格式 → 422
    """
    try:
        user = user_service.register(body.email, body.password, body.name)
        project = project_service.create_project(user.user_id, f"{user.name} 的项目")
        token = UserService.create_access_token(user.user_id)
        return {
            "access_token": token,
            "token_type": "bearer",
            "user": {"user_id": user.user_id, "email": user.email, "name": user.name},
            "project": {"project_id": project.project_id, "name": project.name, "api_key": project.api_key},
        }
    except UserAlreadyExistsError:
        raise HTTPException(status_code=409, detail="邮箱已被注册")


@router.post("/token")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """
    OAuth2 标准 Token 端点。
    
    请求: application/x-www-form-urlencoded
          username=<email>&password=<password>
    响应: {
        "access_token": "eyJ...",
        "token_type": "bearer",
        "user": { "user_id", "email", "name" }
    }
    
    测试标准:
      - 正确凭证 → 200
      - 错误密码 → 401（统一错误信息）
      - 不存在的邮箱 → 401
      - 已禁用用户 → 403
    """
    try:
        user = user_service.authenticate(form_data.username, form_data.password)
        token = UserService.create_access_token(user.user_id)
        return {
            "access_token": token,
            "token_type": "bearer",
            "user": {"user_id": user.user_id, "email": user.email, "name": user.name},
        }
    except (InvalidCredentialsError, UserNotFoundError):
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    except UserSuspendedError:
        raise HTTPException(status_code=403, detail="账户已被禁用")


@router.get("/me")
async def get_me(current_user: User = Depends(get_current_user)):
    """
    获取当前用户信息。
    请求头: Authorization: Bearer <token>
    响应: { "user_id", "email", "name", "is_verified", "created_at" }
    """
    return {
        "user_id": current_user.user_id,
        "email": current_user.email,
        "name": current_user.name,
        "is_verified": current_user.is_verified,
        "created_at": current_user.created_at,
    }
```

### 2.9 Auth 依赖注入

**新建文件**: `src/api/dependencies.py`

```python
from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
from src.services.user_service import UserService
from src.services.project_service import ProjectService
from src.domain.user import User
from src.domain.project import Project

# OAuth2 标准：自动从 Authorization: Bearer 提取 token
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")


async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    """
    OAuth2 标准依赖：从 JWT token 解析当前用户。
    
    用于 /api/projects/* 等需要用户登录的路由。
    
    测试标准:
      - 有效 token → 返回 User
      - 无 token → 401
      - 无效 token → 401
      - 过期 token → 401
      - 用户已禁用 → 403
    """
    payload = UserService.decode_access_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="无效的访问令牌")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="无效的访问令牌")
    
    user = UserService().get_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账户已被禁用")
    return user


async def get_current_project(request: Request) -> Project:
    """
    从 X-API-Key 解析当前 Project。
    
    用于 /api/chat, /api/knowledge, /api/search（现有业务路由）。
    
    测试标准:
      - 有效 API Key → 返回 Project
      - 无效 API Key → 401
      - 已禁用项目 → 403
    """
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise HTTPException(status_code=401, detail="缺少 X-API-Key")
    
    project = ProjectService().get_by_api_key(api_key)
    if project is None:
        raise HTTPException(status_code=401, detail="无效的 API Key")
    if not project.is_active:
        raise HTTPException(status_code=403, detail="项目已被禁用")
    return project
```

### 2.10 Project API 路由

**新建文件**: `src/api/projects.py`

```python
from fastapi import APIRouter, Depends, HTTPException
from src.api.dependencies import get_current_user
from src.services.project_service import ProjectService
from src.domain.user import User
from src.domain.project import Project

router = APIRouter(prefix="/api/projects")
project_service = ProjectService()


@router.get("")
async def list_projects(current_user: User = Depends(get_current_user)):
    """获取当前用户的所有项目列表。
    
    测试标准:
      - 已登录用户 → 200 + 项目列表
      - 新注册用户 → 200 + 1 个项目
      - 未登录 → 401
    """
    projects = project_service.list_by_user(current_user.user_id)
    return [
        {
            "project_id": p.project_id,
            "name": p.name,
            "status": p.status,
            "llm_model": p.llm_model,
            "created_at": p.created_at,
        }
        for p in projects
    ]


@router.post("")
async def create_project(body: CreateProjectRequest, current_user: User = Depends(get_current_user)):
    """创建新项目。
    
    测试标准:
      - 创建成功 → 200 + 返回项目信息（含完整 API Key）
      - 名称为空 → 422
      - 未登录 → 401
    """
    project = project_service.create_project(
        user_id=current_user.user_id,
        name=body.name,
    )
    return {
        "project_id": project.project_id,
        "name": project.name,
        "api_key": project.api_key,
        "status": project.status,
        "created_at": project.created_at,
    }


@router.get("/{project_id}")
async def get_project(project_id: str, current_user: User = Depends(get_current_user)):
    """获取项目详情。"""
    project = project_service.get_by_id(project_id)
    if not project or project.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="项目不存在")
    return {
        "project_id": project.project_id,
        "name": project.name,
        "status": project.status,
        "llm_api_base": project.llm_api_base,
        "llm_model": project.llm_model,
        "llm_timeout": project.llm_timeout,
        "rate_limit_per_user": project.rate_limit_per_user,
        "rate_limit_global": project.rate_limit_global,
        "system_prompt": project.system_prompt,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }


@router.put("/{project_id}")
async def update_project(project_id: str, body: UpdateProjectRequest, current_user: User = Depends(get_current_user)):
    """更新项目配置。"""
    project = project_service.get_by_id(project_id)
    if not project or project.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="项目不存在")
    updated = project_service.update_project(project_id, **body.dict(exclude_none=True))
    return {"success": True, "project_id": project_id}


@router.delete("/{project_id}")
async def delete_project(project_id: str, current_user: User = Depends(get_current_user)):
    """删除项目（软删除）。"""
    project = project_service.get_by_id(project_id)
    if not project or project.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="项目不存在")
    project_service.delete_project(project_id)
    return {"success": True, "message": "项目已删除"}


@router.post("/{project_id}/rotate-key")
async def rotate_api_key(project_id: str, current_user: User = Depends(get_current_user)):
    """轮换 API Key。"""
    project = project_service.get_by_id(project_id)
    if not project or project.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="项目不存在")
    new_key = project_service.rotate_api_key(project_id)
    return {"api_key": new_key}


@router.get("/{project_id}/stats")
async def get_project_stats(project_id: str, current_user: User = Depends(get_current_user)):
    """获取项目使用统计。"""
    project = project_service.get_by_id(project_id)
    if not project or project.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="项目不存在")
    stats = project_service.get_stats(project_id)
    return {
        "project_id": project_id,
        "document_count": stats.get("document_count", 0),
        "total_calls": stats.get("total_calls", 0),
        "prompt_tokens": stats.get("prompt_tokens", 0),
        "completion_tokens": stats.get("completion_tokens", 0),
        "cache_hit_rate": stats.get("cache_hit_rate", 0.0),
        "created_at": project.created_at,
        "last_request": stats.get("last_call_at", 0),
    }


@router.get("/{project_id}/embed-script")
async def get_embed_script(project_id: str, current_user: User = Depends(get_current_user)):
    """获取嵌入脚本代码。"""
    project = project_service.get_by_id(project_id)
    if not project or project.user_id != current_user.user_id:
        raise HTTPException(status_code=404, detail="项目不存在")
    script = generate_embed_script(project_id)
    return {"script": script}
```

### 2.11 注册新路由到 main.py

**修改**: `src/api/main.py`

```python
from src.api.auth import router as auth_router
from src.api.projects import router as projects_router

# 删除：admin_router 的 import 和注册
# 删除：TenantService 的 import 和初始化
# 删除：ensure_default_tenant() 调用

app.include_router(auth_router)
app.include_router(projects_router)
app.include_router(router)  # 现有业务路由（chat, knowledge, search）
```

### ✅ Step 1 测试标准

| 测试项 | 预期 | 方法 |
|--------|------|------|
| 注册成功 | 200 + 返回 access_token + 用户信息 + 项目信息 | POST /api/auth/register |
| 重复邮箱 | 409 | 两次用同一邮箱注册 |
| 密码太短(<8) | 422 | POST { email, password: "123" } |
| 无效邮箱格式 | 422 | POST { email: "not-an-email", password: "12345678" } |
| Token 登录成功 | 200 + 返回 access_token + token_type=bearer | POST /api/auth/token (form-data) |
| Token 登录密码错误 | 401（统一错误信息） | 正确邮箱 + 错误密码 |
| Token 登录不存在邮箱 | 401 | 未注册的邮箱 |
| 已禁用用户登录 | 403 | 修改用户 is_active=0 后再登录 |
| /me 有效 token | 200 + 返回用户信息 | GET /api/auth/me + Bearer token |
| /me 无 token | 401 | GET /api/auth/me |
| /me 无效 token | 401 | GET /api/auth/me + Bearer "invalid" |
| 注册后自动创建 1 个项目 | 1 个 Project | GET /api/projects |
| 创建项目 | 200 + 返回 API Key | POST /api/projects |
| 创建项目（未登录） | 401 | POST /api/projects |
| 列表自己的项目 | 200 + 项目列表 | GET /api/projects + JWT |
| 查看别人的项目 | 404 | 用另一个用户的 JWT 访问 |
| 更新项目 | 200 | PUT /api/projects/{id} + JWT |
| 删除项目 | 200 + 软删除 | DELETE /api/projects/{id} + JWT |
| 删除后 API Key 失效 | 401 | DELETE 后用旧 key 调 /api/chat |
| 轮换 Key | 新 key 可用，旧 key 失效 | POST .../rotate-key |
| 项目统计 | 返回正确的调用次数 | GET .../{id}/stats + JWT |
| 一个用户创建 5 个项目 | 全部成功，列表返回 5 个 | 循环创建 |
| 项目间数据隔离 | 项目 A 的文档在项目 B 不可见 | 在两个项目分别上传文档，交叉查询 |
| Token 端点接受 form-data | 200 | 用 application/x-www-form-urlencoded 请求 |
| JWT 包含标准字段 | sub + exp + iat | 解码 token 检查 |
| Token 过期后拒绝 | 401 | 用过期 token 调 /me |
| 密码哈希不存明文 | 数据库中 password_hash 不是原始密码 | 直查 SQLite 表 |

---

## 3. Step 2：现有 API 适配

### 3.1 修改 resolve_tenant → resolve_project

**修改**: `src/api/routes.py`

```python
# 删除全部 Tenant 相关代码，替换为 Project:

async def resolve_project(request: Request) -> Project:
    """
    从 X-API-Key 解析当前 Project。
    用于所有业务路由（chat, knowledge, search）。
    """
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise HTTPException(status_code=401, detail="缺少 X-API-Key")
    
    project = ProjectService().get_by_api_key(api_key)
    if project is None:
        raise HTTPException(status_code=401, detail="无效的 API Key")
    if not project.is_active:
        raise HTTPException(status_code=403, detail="项目已被禁用")
    
    request.state.project = project
    return project
```

### 3.2 修改所有业务路由

将 `Depends(resolve_tenant)` 替换为 `Depends(resolve_project)`，所有 `tenant.tenant_id` 替换为 `project.project_id`。

**修改清单**:

| 路由 | 修改 |
|------|------|
| POST /api/chat | `tenant=Depends(resolve_tenant)` → `project=Depends(resolve_project)` |
| POST /api/chat/stream | 同上 |
| POST /api/knowledge | 同上 |
| POST /api/knowledge/upload | 同上 |
| GET /api/knowledge/{doc_id} | 同上 |
| PUT /api/knowledge/{doc_id} | 同上 |
| DELETE /api/knowledge/{doc_id} | 同上 |
| POST /api/search | 同上 |
| POST /api/search/batch | 同上 |
| GET /api/knowledge | 同上 |

### 3.3 修改知识库 Service

**修改**: `src/services/knowledge_service.py`

所有方法中 `tenant_id` 参数 → `project_id`。Zvec 存储的字段名也改为 `project_id`。

### 3.4 修改 Zvec 存储

**修改**: `src/infrastructure/zvec_store.py`

所有 `tenant_id` 过滤条件 → `project_id`。

### 3.5 修改 Retriever Factory

**修改**: `src/core/factory.py`

```python
class RetrieverFactory:
    def get_retriever_for_project(self, project_id: str, project: Project = None) -> Retriever:
        """按 project 获取隔离的 Retriever 实例。"""
        ...
```

### 3.6 删除的安全检查

**`src/api/main.py`** 中删除：

```python
# 删除以下全部内容：
from src.services.tenant_service import TenantService
app.state.tenant_service = tenant_svc
tenant_svc.ensure_default_tenant()
```

### 3.7 删除的限流关联

**`src/utils/limiter.py`** 中 `tenant_id` → `project_id`。

### ✅ Step 2 测试标准

| 测试项 | 预期 | 方法 |
|--------|------|------|
| 用 Project API Key 调 /api/chat | 200 | POST /api/chat + X-API-Key |
| 无 API Key 调 /api/chat | 401 | POST /api/chat |
| 无效 API Key 调 /api/chat | 401 | POST /api/chat + 假 key |
| 已删除项目的 Key 调 /api/chat | 401 | 删除项目后调 /api/chat |
| 知识库 CRUD 用 Project Key | 200 | 用 Project Key 操作知识库 |
| 项目 A 的 Key 不能看项目 B 的文档 | 0 结果 | 交叉查询 |
| 健康检查免鉴权 | 200 | GET /api/health |
| 旧 /api/admin/tenants 不存在 | 404 | GET /api/admin/tenants |

---

## 4. Step 3：前端重构

### 4.1 删除的旧文件

| 文件 | 删除原因 |
|------|----------|
| `src/views/auth/TenantSelect.vue` | 替换为 ProjectList.vue |
| `src/views/admin/TenantList.vue` | 不再需要 admin 租户管理 |
| `src/api/tenant.ts` | 不再需要 |
| `src/stores/app.ts` 中的租户相关代码 | 替换为 auth + project store |

### 4.2 新增 API 文件

**新建文件**: `admin-panel/src/api/auth.ts`

```typescript
import request from './request'
import type { User } from './types'

export interface LoginRequest {
  email: string
  password: string
}

export interface RegisterRequest {
  email: string
  password: string
  name: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
  user: User
}

export const authApi = {
  /** OAuth2 标准：表单登录 */
  login: (data: LoginRequest) =>
    request.post<TokenResponse>('/api/auth/token',
      new URLSearchParams({
        username: data.email,
        password: data.password,
      }),
      {
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      },
    ).then(r => r.data),

  /** 注册 + 自动登录 */
  register: (data: RegisterRequest) =>
    request.post<TokenResponse>('/api/auth/register', data).then(r => r.data),

  /** 获取当前用户 */
  getMe: () =>
    request.get<User>('/api/auth/me').then(r => r.data),
}
```

**新建文件**: `admin-panel/src/api/project.ts`

```typescript
import request from './request'

export const projectApi = {
  list: () => request.get<any[]>('/api/projects').then(r => r.data),
  get: (id: string) => request.get<any>(`/api/projects/${id}`).then(r => r.data),
  create: (data: { name: string }) => request.post<any>('/api/projects', data).then(r => r.data),
  update: (id: string, data: any) => request.put<any>(`/api/projects/${id}`, data).then(r => r.data),
  delete: (id: string) => request.delete(`/api/projects/${id}`).then(r => r.data),
  rotateKey: (id: string) => request.post(`/api/projects/${id}/rotate-key`).then(r => r.data),
  stats: (id: string) => request.get<any>(`/api/projects/${id}/stats`).then(r => r.data),
  embedScript: (id: string) => request.get<any>(`/api/projects/${id}/embed-script`).then(r => r.data),
}
```

### 4.3 修改 types.ts

**修改**: `admin-panel/src/api/types.ts`

```typescript
// 删除：Tenant, CreateTenantRequest, UpdateTenantRequest, TenantKeyResponse, TenantStats
// 新增：

/** 用户 */
export interface User {
  user_id: string
  email: string
  name: string
  is_verified: boolean
  created_at: number
}

/** 项目 */
export interface Project {
  project_id: string
  user_id: string
  api_key: string
  name: string
  status: string
  llm_api_base: string
  llm_model: string
  llm_timeout: number
  rate_limit_per_user: string
  rate_limit_global: string
  system_prompt: string
  created_at: number
  updated_at: number
}

/** 项目统计 */
export interface ProjectStats {
  project_id: string
  document_count: number
  total_calls: number
  prompt_tokens: number
  completion_tokens: number
  cache_hit_rate: number
  created_at: number
  last_request: number
}
```

### 4.4 修改 request.ts

**修改**: `admin-panel/src/api/request.ts`

```typescript
// 请求拦截器
request.interceptors.request.use((config) => {
  const isAuthRoute = (config.url || '').startsWith('/api/auth/')
  
  if (!isAuthRoute) {
    // OAuth2 标准：优先使用 JWT Bearer token
    const token = localStorage.getItem('openask_token')
    if (token) {
      config.headers['Authorization'] = `Bearer ${token}`
    }
    // 没有 JWT 但有 project API Key（嵌入脚本场景）
    else {
      const apiKey = localStorage.getItem('openask_api_key')
      if (apiKey) {
        config.headers['X-API-Key'] = apiKey
      }
    }
  }
  return config
})

// 响应拦截器：401 时自动跳转登录页
request.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('openask_token')
      localStorage.removeItem('openask_user')
      window.location.href = '/login'
    }
    return Promise.reject(error)
  }
)
```

### 4.5 新增 Store

**新建文件**: `admin-panel/src/stores/auth.ts`

```typescript
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { authApi, type LoginRequest, type RegisterRequest } from '@/api/auth'
import router from '@/router'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('openask_token') || '')
  const user = ref<any>(JSON.parse(localStorage.getItem('openask_user') || 'null'))

  const isLoggedIn = computed(() => !!token.value && !!user.value)
  const currentUser = computed(() => user.value)

  async function login(data: LoginRequest) {
    const res = await authApi.login(data)
    token.value = res.access_token
    user.value = res.user
    localStorage.setItem('openask_token', res.access_token)
    localStorage.setItem('openask_user', JSON.stringify(res.user))
  }

  async function register(data: RegisterRequest) {
    const res = await authApi.register(data)
    token.value = res.access_token
    user.value = res.user
    localStorage.setItem('openask_token', res.access_token)
    localStorage.setItem('openask_user', JSON.stringify(res.user))
  }

  function logout() {
    token.value = ''
    user.value = null
    localStorage.removeItem('openask_token')
    localStorage.removeItem('openask_user')
    router.push('/login')
  }

  return { token, user, isLoggedIn, currentUser, login, register, logout }
})
```

**新建文件**: `admin-panel/src/stores/project.ts`

```typescript
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { projectApi } from '@/api/project'

export const useProjectStore = defineStore('project', () => {
  const projects = ref<any[]>([])
  const currentProject = ref<any>(null)
  const loading = ref(false)

  async function fetchProjects() {
    loading.value = true
    try {
      projects.value = await projectApi.list()
    } finally {
      loading.value = false
    }
  }

  function setCurrentProject(project: any) {
    currentProject.value = project
    localStorage.setItem('openask_api_key', project.api_key)
  }

  return { projects, currentProject, loading, fetchProjects, setCurrentProject }
})
```

### 4.6 路由改造

**修改**: `admin-panel/src/router/index.ts`

```typescript
const routes = [
  // 公开路由（无需登录）
  {
    path: '/login',
    name: 'Login',
    meta: { public: true },
    component: () => import('@/views/auth/LoginView.vue'),
  },
  {
    path: '/register',
    name: 'Register',
    meta: { public: true },
    component: () => import('@/views/auth/RegisterView.vue'),
  },
  {
    path: '/',
    component: MainLayout,
    redirect: '/projects',
    children: [
      {
        path: 'projects',
        name: 'Projects',
        component: () => import('@/views/projects/ProjectList.vue'),
        meta: { title: '我的项目', icon: 'Opportunity' },
      },
      {
        path: 'project/:id/dashboard',
        name: 'ProjectDashboard',
        component: () => import('@/views/dashboard/DashboardView.vue'),
        meta: { title: '数据看板', icon: 'Odometer' },
      },
      {
        path: 'project/:id/knowledge',
        redirect: to => `/project/${to.params.id}/knowledge/list`,
        children: [
          { path: 'list', component: () => import('@/views/knowledge/KnowledgeList.vue'), meta: { title: '文档列表' } },
          { path: 'create', component: () => import('@/views/knowledge/KnowledgeCreate.vue'), meta: { title: '创建文档' } },
          { path: 'upload', component: () => import('@/views/knowledge/KnowledgeUpload.vue'), meta: { title: '上传文件' } },
          { path: ':docId/edit', component: () => import('@/views/knowledge/KnowledgeEdit.vue'), meta: { title: '编辑文档', hidden: true } },
        ],
      },
      {
        path: 'project/:id/search',
        name: 'ProjectSearch',
        component: () => import('@/views/search/SearchTest.vue'),
        meta: { title: '搜索测试', icon: 'Search' },
      },
      {
        path: 'project/:id/logs',
        name: 'ProjectLogs',
        component: () => import('@/views/logs/ChatLogs.vue'),
        meta: { title: '问答日志', icon: 'ChatDotSquare' },
      },
      {
        path: 'project/:id/settings',
        name: 'ProjectSettings',
        component: () => import('@/views/projects/ProjectSettings.vue'),
        meta: { title: '项目设置', icon: 'Setting' },
      },
    ],
  },
]

// 路由守卫：检查登录状态
router.beforeEach(async (to) => {
  const { useAuthStore } = await import('@/stores/auth')
  const authStore = useAuthStore()
  
  if (to.meta.public) return
  
  if (!authStore.isLoggedIn) {
    return { name: 'Login' }
  }
})
```

### 4.7 新建登录/注册页面

**新建文件**: `admin-panel/src/views/auth/LoginView.vue`

```
页面内容：
- 应用 Logo 和名称
- 邮箱输入框
- 密码输入框
- 登录按钮（加载状态）
- 注册链接
- 错误提示（"邮箱或密码错误"）
- 登录成功 → 跳转 /projects
- 空状态：无
- 加载状态：按钮 loading
- 错误状态：红色提示文字
```

**新建文件**: `admin-panel/src/views/auth/RegisterView.vue`

```
页面内容：
- 应用 Logo 和名称
- 名称输入框
- 邮箱输入框
- 密码输入框（显示密码强度要求 ≥8 位）
- 确认密码输入框（前端校验与密码一致）
- 注册按钮（加载状态）
- 登录链接
- 注册成功 → 自动登录 → 跳转 /projects
- 错误状态：重复邮箱提示 / 校验错误提示
```

### 4.8 新建项目列表页

**新建文件**: `admin-panel/src/views/projects/ProjectList.vue`

```
功能：
- 卡片列表展示所有项目（名称、状态、模型、创建时间）
- 点击进入项目（进入数据看板）
- 创建新项目按钮 → 弹出对话框填写名称
- 每个项目卡片有设置入口
- 空状态：还没有项目，引导创建（"创建你的第一个项目"）
- 加载状态：骨架屏
- 错误状态：错误信息 + 重试按钮
```

### 4.9 新建项目设置页

**新建文件**: `admin-panel/src/views/projects/ProjectSettings.vue`

```
功能：
- 项目基本信息：名称、状态
- LLM 配置：API Key / Base / Model / Timeout
- 限流配置：per-user / global
- System Prompt 编辑
- API Key 管理：显示（可复制）、轮换
- 嵌入脚本：显示完整 HTML 代码（高亮、复制按钮）
- 删除项目：二次确认弹窗
- 保存：PUT /api/projects/{id}
- 加载状态：骨架屏
- 错误状态：提示信息
```

### 4.10 改造 Navbar

**修改**: `admin-panel/src/layouts/components/Navbar.vue`

```
变更：
- 删除租户切换下拉
- 增加项目切换下拉（显示当前项目名称，切换项目）
- 添加用户信息显示（邮箱）
- 添加退出登录按钮
- 保留连接状态显示
```

### 4.11 改造 Sidebar

**修改**: `admin-panel/src/layouts/components/Sidebar.vue`

```
变更：
- 菜单结构改为项目内导航：
  ├── 数据看板
  ├── 知识库管理
  │   ├── 文档列表
  │   ├── 创建文档
  │   └── 上传文件
  ├── 搜索测试
  ├── 问答日志
  └── 项目设置
- 顶部增加项目名称显示
- 底部增加"返回项目列表"按钮
```

### 4.12 适配现有页面

**修改**: 所有现有页面（DashboardView, KnowledgeList, SearchTest, ChatLogs）

```
变更：
- 从 store 获取 project_id 代替 tenant_id
- 路由参数中读取 project_id
- 所有 API 调用改用 X-API-Key（来自当前 project）
```

### ✅ Step 3 测试标准

| 测试项 | 预期 | 方法 |
|--------|------|------|
| 注册页渲染 | 正常渲染表单 | 访问 /register |
| 注册成功 | 自动登录，跳转到 /projects | 填写表单提交 |
| 重复邮箱注册 | 显示错误提示 | 用已注册邮箱 |
| 登录页渲染 | 正常渲染表单 | 访问 /login |
| 登录成功 | 跳转到 /projects | 正确凭证 |
| 登录失败 | 显示"邮箱或密码错误" | 错误凭证 |
| 未登录访问受保护页面 | 重定向到 /login | 直接访问 /projects |
| 退出登录 | 清空 token，跳转 /login | 点击退出按钮 |
| 项目列表 | 显示所有项目 | 登录后查看 |
| 创建项目 | 对话框 → 创建成功 → 列表刷新 | 点击创建按钮 |
| 进入项目 | 跳转到项目数据看板 | 点击项目卡片 |
| 项目设置 | 修改保存成功 | 修改后保存 |
| 导航栏显示用户信息 | 显示邮箱 | 登录后查看 |
| 导航栏显示当前项目 | 显示项目名称 | 进入项目后查看 |
| 切换项目 | 路由和页面数据刷新 | 切换不同项目 |
| 401 时自动跳转登录 | 重定向到 /login | 清除 token 后操作 |

---

## 5. Step 4：嵌入脚本

### 5.1 嵌入脚本 API

**新建文件**: `src/api/embed.py`

```python
from fastapi import APIRouter, HTTPException, Response
from src.services.project_service import ProjectService

router = APIRouter()


@router.get("/api/embed/{project_id}/chat.js")
async def embed_chat_js(project_id: str):
    """返回嵌入聊天组件的 JS 脚本。"""
    project = ProjectService().get_by_id(project_id)
    if not project or not project.is_active:
        return Response(status_code=404, content="/* Project not found */", media_type="application/javascript")
    
    js_content = generate_embed_script(project_id)
    return Response(
        content=js_content,
        media_type="application/javascript",
        headers={
            "Cache-Control": "public, max-age=3600",
            "Access-Control-Allow-Origin": "*",
        },
    )
```

### 5.2 嵌入脚本生成

**新建文件**: `src/services/embed_script.py`

```python
def generate_embed_script(project_id: str, api_base: str = "") -> str:
    """生成嵌入脚本。"""
    return f"""
(function() {{
    var projectId = '{project_id}';
    var apiBase = '{api_base}' || window.location.origin;
    // 创建聊天浮动按钮和窗口
    // 通过 X-API-Key 调用 /api/chat 接口
    // 显示来源文档
}})();
"""
```

嵌入脚本运行时需要用到的 API Key 获取方式：嵌入脚本不包含 API Key，用户在项目设置中复制完整的嵌入代码（含 API Key）或嵌入脚本在加载时从后端获取临时 token。

**设计决策**: 采用"用户在项目设置页复制完整代码"的方式，嵌入代码中包含 API Key。这样最简单，无需额外 token 交换。

### 5.3 注册嵌入路由

**修改**: `src/api/main.py`

```python
from src.api.embed import router as embed_router
app.include_router(embed_router)
```

### ✅ Step 4 测试标准

| 测试项 | 预期 | 方法 |
|--------|------|------|
| 返回 JS 内容 | 200 + content-type: application/javascript | GET /api/embed/{id}/chat.js |
| 包含 project_id | JS 内容中包含 project_id | 检查响应体 |
| 不存在项目 | 404 | GET /api/embed/nonexistent/chat.js |
| 已删除项目 | 404 | 删除后请求 |
| 项目设置页显示嵌入代码 | 可复制的 HTML 代码 | 访问项目设置 |
| 复制嵌入代码 | 复制到剪贴板 | 点击复制按钮 |

---

## 6. Step 5：数据迁移

### 6.1 迁移脚本

**新建文件**: `scripts/migrate_v1_to_v2.py`

```python
"""
v1 → v2 数据迁移脚本。

迁移内容：
1. 读取旧 tenants.db 中的所有 tenant 记录
2. 为每个 tenant 创建一个对应的 user（email: "migrated-{tenant_id}@local"）
3. 为每个 user 创建一个 project，使用原 tenant 的 API Key
4. 将旧 knowledge_base 中的文档重新关联到新 project
5. 删除旧 tenants.db（可选）

注意：这是一个一次性迁移脚本。迁移完成后，旧代码将不再兼容。
如果迁移失败，可从备份恢复。

运行方式：
    python scripts/migrate_v1_to_v2.py
    python scripts/migrate_v1_to_v2.py --dry-run  # 预览模式，不实际写入
"""
```

### 6.2 完整的 .env 配置

```bash
# === Auth 配置（OAuth2 标准） ===
AUTH_SECRET_KEY=your-jwt-secret-key-here   # 生产环境：openssl rand -hex 32
AUTH_ALGORITHM=HS256
AUTH_ACCESS_TOKEN_EXPIRE_MINUTES=1440       # 24 小时
```

### 6.3 测试总覆盖要求

```bash
# 后端测试
cd /home/jiujiuwhoami/openAsk
python -m pytest tests/ -v --cov=src --cov-report=term-missing

# 通过标准：
# - 所有测试通过（无 FAILED）
# - 覆盖率 >= 80%（核心逻辑 >= 90%）
# - 新增测试 >= 50 个

# 前端构建
cd /home/jiujiuwhoami/admin-panel
npm run build

# 通过标准：
# - 无 TypeScript 错误
# - 无构建警告
```

---

## 7. 文件变更总清单

### 删除的文件

| 文件 | 说明 |
|------|------|
| `src/domain/models.py` | 删除 Tenant 类，替换为 user.py + project.py |
| `src/services/tenant_service.py` | 替换为 project_service.py |
| `src/services/tenant_stats.py` | 合并到 project_service.py |
| `admin-panel/src/views/auth/TenantSelect.vue` | 替换为 ProjectList.vue |
| `admin-panel/src/views/admin/TenantList.vue` | 不再需要 |
| `admin-panel/src/api/tenant.ts` | 替换为 project.ts |
| `admin-panel/src/stores/app.ts` | 替换为 auth.ts + project.ts |

### 新建的文件

| 文件 | 说明 |
|------|------|
| `src/domain/user.py` | User 领域模型 |
| `src/domain/project.py` | Project 领域模型 |
| `src/services/user_service.py` | 用户注册、登录、JWT 管理 |
| `src/services/project_service.py` | 项目 CRUD + API Key 鉴权 + 统计 |
| `src/services/embed_script.py` | 嵌入脚本生成 |
| `src/api/auth.py` | OAuth2 认证路由 |
| `src/api/projects.py` | 项目 CRUD 路由 |
| `src/api/embed.py` | 嵌入脚本路由 |
| `src/api/dependencies.py` | OAuth2 依赖注入 |
| `scripts/migrate_v1_to_v2.py` | 数据迁移脚本 |
| `admin-panel/src/api/auth.ts` | 登录/注册 API |
| `admin-panel/src/api/project.ts` | 项目 API |
| `admin-panel/src/stores/auth.ts` | 用户认证状态管理 |
| `admin-panel/src/stores/project.ts` | 项目状态管理 |
| `admin-panel/src/views/auth/LoginView.vue` | 登录页 |
| `admin-panel/src/views/auth/RegisterView.vue` | 注册页 |
| `admin-panel/src/views/projects/ProjectList.vue` | 项目列表页 |
| `admin-panel/src/views/projects/ProjectSettings.vue` | 项目设置页 |

### 修改的文件

| 文件 | 修改内容 |
|------|----------|
| `requirements.txt` | 新增 passlib, python-jose, email-validator |
| `src/domain/exceptions.py` | 删除 Tenant 异常，新增 User/Project 异常 |
| `src/utils/config.py` | 删除 TenantStorageSettings，新增 AuthSettings |
| `src/api/schemas.py` | 删除 Tenant schema，新增 Project/User schema |
| `src/api/routes.py` | resolve_tenant → resolve_project，删除 admin_router |
| `src/api/main.py` | 删除 TenantService，注册新路由 |
| `src/core/factory.py` | tenant_id → project_id |
| `src/core/retriever.py` | tenant_id → project_id |
| `src/services/knowledge_service.py` | tenant_id → project_id |
| `src/infrastructure/zvec_store.py` | tenant_id → project_id |
| `src/utils/limiter.py` | tenant_id → project_id |
| `admin-panel/src/api/request.ts` | 简化，删除 admin key 逻辑 |
| `admin-panel/src/api/types.ts` | 删除 Tenant 类型，新增 User/Project 类型 |
| `admin-panel/src/router/index.ts` | 新路由结构 + 登录守卫 |
| `admin-panel/src/layouts/components/Navbar.vue` | 用户信息 + 项目切换 |
| `admin-panel/src/layouts/components/Sidebar.vue` | 新菜单结构 |
| `admin-panel/src/views/dashboard/DashboardView.vue` | 适配 project 上下文 |
| `admin-panel/src/views/knowledge/*.vue` | 适配 project 上下文 |
| `admin-panel/src/views/search/SearchTest.vue` | 适配 project 上下文 |
| `admin-panel/src/views/logs/ChatLogs.vue` | 适配 project 上下文 |

### 测试文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `tests/test_user_service.py` | 新建 | 用户服务单元测试 |
| `tests/test_auth_api.py` | 新建 | Auth API 端到端测试 |
| `tests/test_projects.py` | 新建 | Project API 端到端测试 |
| `tests/test_embed_script.py` | 新建 | 嵌入脚本测试 |
| `tests/test_api.py` | 修改 | 适配 Project 模式 |

---

*版本：v2.0 · 2026-08-06*