# openAsk 后端多租户改造清单

> 项目：openAsk 智能知识库问答系统  
> 目标：从单租户 → 完整多租户  
> 最后更新：2026-07-31

---

## 阶段 P0 — 核心改造（8-12 天）

### 1. 租户模型与 API Key 管理

#### 1.1 新增 domain/models.py — Tenant 实体

```python
class Tenant:
    """实体：多租户，由 tenant_id 标识。"""

    def __init__(
        self,
        tenant_id: str,              # "tenant_001"
        api_key: str,                # "sk_xxx"
        name: str,                   # "某电商站"
        status: str = "active",      # active / suspended / trial
        knowledge_path: str = "",    # 向量存储路径（可选）
        # LLM 配置（每个租户可不同）
        llm_api_key: str = "",
        llm_api_base: str = "",
        llm_model: str = "",
        llm_timeout: int = 30,
        # 限流
        rate_limit_per_user: str = "60/minute",
        rate_limit_global: str = "1000/minute",
        # Prompt 定制
        system_prompt: str = "",
        # 元数据
        created_at: int = 0,
        updated_at: int = 0,
    ):
        ...
```

**涉及文件：**
- `domain/models.py` — 新增 Tenant 类
- `domain/models.py` — Document 加 `tenant_id` 字段
- `domain/models.py` — SearchResult 加 `tenant_id` 字段
- `domain/exceptions.py` — 新增 `TenantNotFoundError`

#### 1.2 新增 services/tenant_service.py — 租户管理服务

```python
class TenantService:
    """租户管理：CRUD + API Key 鉴权 + 配置读取。"""

    def create_tenant(self, ...) -> Tenant
    def get_by_id(self, tenant_id: str) -> Tenant
    def get_by_api_key(self, api_key: str) -> Tenant
    def list_tenants(self) -> List[Tenant]
    def update_tenant(self, tenant_id: str, ...) -> Tenant
    def delete_tenant(self, tenant_id: str) -> bool
    def rotate_api_key(self, tenant_id: str) -> str   # 轮换 key
    def update_rate_limit(self, tenant_id: str, ...) -> None
```

**存储方案：优先 SQLite（小团队）→ 后改 MySQL/PostgreSQL**

**涉及文件：**
- `services/tenant_service.py` — 新建
- `utils/config.py` — 加 TenantStorageSettings

#### 1.3 API Key 鉴权改造

**改前（固定 key）：**
```python
# routes.py
@router.post("/chat/stream")
@limiter.limit("60/minute")
async def chat_stream(request: Request, body: ChatRequest, ...):
    if settings.api.api_key and request.headers["X-API-Key"] != settings.api.api_key:
        raise HTTPException(401)
```

**改后（多 key → resolve_tenant）：**
```python
async def resolve_tenant(request: Request) -> Tenant:
    """FastAPI Depends：从 X-API-Key 解析租户，注入 request.state"""
    key = request.headers.get("X-API-Key")
    tenant = tenant_service.get_by_api_key(key)
    if not tenant or tenant.status != "active":
        raise HTTPException(401, detail="Unauthorized")
    request.state.tenant = tenant
    return tenant

@router.post("/chat/stream")
@limiter.dynamic_limit()  # 改为动态限流
async def chat_stream(
    request: Request,
    body: ChatRequest,
    tenant: Tenant = Depends(resolve_tenant),   # ← 租户上下文注入
):
    ...
```

**涉及文件：**
- `api/routes.py` — 重构 verify_api_key → resolve_tenant，所有路由加 Depends
- `api/routes.py` — health 端点保留免鉴权（`@router.get("/health")` 不加 tenant）

---

### 2. 知识库隔离

#### 2.1 方案选择

| 方案 | 做法 | 改动量 |
|---|---|---|
| **A. 按 tenant_id filter** | Zvec 所有查询加 `filter_expr=f"tenant_id = '{tenant.id}'"` | **推荐 ✅** 改动最小 |
| B. 按 tenant 切 data 目录 | 每个租户独立 `data/zvec/{tenant_id}` 目录 | 大改，需改 schema |

**采用方案 A：filter_expr 隔离。**

#### 2.2 改动清单

**`domain/models.py` — Document：**
```python
class Document:
    def __init__(self, ..., tenant_id: str = "", ...):
        self._tenant_id = tenant_id
    @property
    def tenant_id(self) -> str:
        return self._tenant_id
```

**`domain/models.py` — SearchResult：**
```python
class SearchResult:
    def __init__(self, ..., tenant_id: str = ""):
        self._tenant_id = tenant_id
    @property
    def tenant_id(self) -> str:
        return self._tenant_id
```

**`api/schemas.py` — DocumentResponse：**
```python
class DocumentResponse(BaseModel):
    tenant_id: str = Field("", description="租户 ID")
    ...
```

**`infrastructure/zvec_store.py` — 全部 CRUD 方法加 tenant 过滤：**

| 方法 | 改动 |
|---|---|
| `_build_schema()` | 新增 `tenant_id` 字段（STRING, InvertIndexParam） |
| `insert()` / `upsert()` | 插入时写入 `tenant_id` 字段；默认值 `"default"` 兼容旧数据 |
| `search()` | 加 `tenant_id=...` 参数 → `filter_expr=f"tenant_id = '{tid}'"` |
| `get()` | 加 tenant 过滤 |
| `list()` | 加 tenant 过滤 |
| `list_paginated()` | 加 tenant 过滤 |
| `count()` | 加 tenant 过滤 |
| `delete()` | 加 tenant 过滤 |

**`services/knowledge_service.py` — 所有方法加 tenant 参数：**
```python
async def create_document_from_text(
    self, title, content, ..., tenant_id: str = "default"
) -> Document:
    doc = DomainDocument(..., tenant_id=tenant_id)
    embedding = await self._embedding_service.encode(doc.content)
    await self._vector_store.ainsert(doc, embedding, tenant_id=tenant_id)
```

**`core/retriever.py` — _vector_search 传 tenant：**
```python
async def _vector_search(self, query_vector, top_k, tenant_id: str):
    return await self._vector_store.asearch(
        query_vector, top_k=top_k, tenant_id=tenant_id
    )
```

**`api/routes.py` — 所有知识库路由传 tenant：**
```python
async def get_knowledge_service(request: Request) -> KnowledgeService:
    tenant = request.state.tenant
    return knowledge_service_factory.get_for_tenant(tenant)
```

**涉及文件（共 6 个）：**
- `domain/models.py`
- `domain/exceptions.py`
- `api/schemas.py`
- `infrastructure/interfaces/vector_store.py`
- `infrastructure/zvec_store.py`
- `services/knowledge_service.py`
- `core/retriever.py`
- `api/routes.py`

---

### 3. LLM 配置隔离

#### 3.1 SenseNovaClient 改造

**改前（单实例，全局配置）：**
```python
class SenseNovaClient:
    def __init__(self):
        self._api_key = settings.llm.api_key
        self._api_base = settings.llm.api_base
        self._model = settings.llm.model
```

**改后（支持运行时传入租户配置）：**
```python
class SenseNovaClient:
    def __init__(
        self,
        api_key: Optional[str] = None,       # 优先参数，降级 settings
        api_base: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
    ):
        self._api_key = api_key or settings.llm.api_key
        self._api_base = api_base or settings.llm.api_base
        self._model = model or settings.llm.model
        self._timeout = timeout or settings.llm.timeout
```

#### 3.2 main.py — 单例改工厂分发

**改前（lifespan 单例）：**
```python
# 启动时创建一次
llm_client = SenseNovaClient()
retriever = Retriever(
    embedding_service=embedding_service,
    vector_store=vector_store,
    cache_backend=cache_backend,
    llm_client=llm_client,
)
app.state.retriever = retriever
```

**改后（按租户创建）：**
```python
# 全局共享资源
app.state.embedding_service = embedding_service
app.state.embedding_cache = EmbeddingCache()

def create_retriever_for_tenant(tenant: Tenant) -> Retriever:
    llm_client = SenseNovaClient(
        api_key=tenant.llm_api_key,
        api_base=tenant.llm_api_base,
        model=tenant.llm_model,
        timeout=tenant.llm_timeout,
    )
    vector_store = ZvecStore(tenant_id=tenant.id)
    cache_backend = LLMResponseCache(cache_path=f"data/zvec_llm_cache/{tenant.id}")
    return Retriever(
        embedding_service=app.state.embedding_service,
        vector_store=vector_store,
        cache_backend=cache_backend,
        llm_client=llm_client,
        embedding_cache=app.state.embedding_cache,
    )

# 每个请求按需创建（或加 LRUCache 复用）
async def get_retriever(request: Request) -> Retriever:
    tenant = request.state.tenant
    key = tenant.id
    if key not in app.state._retriever_cache:
        app.state._retriever_cache[key] = create_retriever_for_tenant(tenant)
    return app.state._retriever_cache[key]
```

#### 3.3 LLMResponseCache 隔离

```python
# 改前
cache_backend = LLMResponseCache()
cache_path = "data/zvec_llm_cache"   # 全局共享

# 改后
cache_backend = LLMResponseCache(
    cache_path=f"data/zvec_llm_cache/{tenant.id}"   # 按租户隔离
)
```

#### 3.4 Prompt 模板定制

```python
# services/sensenova_client.py
class PromptBuilder:
    @classmethod
    def build_qa_prompt(cls, query, context, system_prompt=None):
        instructions = system_prompt or "你是一位专业的知识库问答助手..."
        return f"""{instructions}
...
```

```python
# routes.py — 从 tenant 取 system_prompt
async def chat_stream(request, body, tenant=Depends(resolve_tenant)):
    retriever = get_retriever(request)
    retriever.set_system_prompt(tenant.system_prompt)  # 或传入
    ...
```

**涉及文件（共 4 个）：**
- `infrastructure/interfaces/llm_client.py`
- `services/sensenova_client.py`
- `api/main.py`
- `core/retriever.py`

---

### 4. 限流按租户隔离

#### 4.1 limiter.py 改造

**改前（按 IP）：**
```python
def _key_func(request):
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)

limiter = Limiter(key_func=_key_func)
```

**改后（按 tenant api_key）：**
```python
def _key_func(request):
    """优先按租户 key 限流，无租户则按 IP 兜底"""
    tenant = getattr(request.state, "tenant", None)
    if tenant:
        return f"tenant:{tenant.api_key}"
    # 无 key 的公开端点（如 health）按 IP 限流
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    return get_remote_address(request)

limiter = Limiter(key_func=_key_func)
```

#### 4.2 动态限流值

slowapi 的 `@limiter.limit("60/minute")` 是静态装饰器，**无法按租户动态切换**。

**方案：自定义中间件替代 slowapi 装饰器**

```python
# utils/dynamic_limiter.py
from collections import defaultdict
import time

class TenantLimiter:
    """按租户动态限流（LRU + 滑动窗口）。"""

    def __init__(self, storage_uri: str = "memory://"):
        self._requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, tenant: Tenant, endpoint: str = "chat") -> bool:
        # 解析 tenant.rate_limit 如 "100/minute"
        count, unit = self._parse_rate(tenant.rate_limit_per_user)
        window_seconds = {"minute": 60, "hour": 3600, "second": 1}[unit]

        key = f"{tenant.api_key}:{endpoint}"
        now = time.monotonic()
        # 滑动窗口：移除过期请求
        self._requests[key] = [
            ts for ts in self._requests[key] if ts > now - window_seconds
        ]
        if len(self._requests[key]) >= count:
            return False
        self._requests[key].append(now)
        return True
```

**替代方案（更简单）：直接用 Redis 限流**

```python
# .env
RATE_LIMIT_STORAGE_URI=redis://localhost:6379/0

# 用 slowapi 的 Redis 后端，key_func 改为 tenant key
```

#### 4.3 routes.py — 移除 @limiter.limit 装饰器，改用中间件

```python
# 新增中间件
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    tenant = getattr(request.state, "tenant", None)
    if tenant and not limiter.is_allowed(tenant):
        return JSONResponse(
            status_code=429,
            content={"error": "Rate limit exceeded", "detail": tenant.rate_limit_per_user},
        )
    return await call_next(request)
```

**涉及文件（共 3 个）：**
- `utils/limiter.py`
- `utils/dynamic_limiter.py` — 新建
- `api/main.py` — 加中间件

---

## 阶段 P1 — 管理 API 改造（3-5 天）

### 5. 租户管理 API 路由

**`api/routes.py` — 新增 `/api/admin/tenants` 路由组：**

```python
admin_router = APIRouter(prefix="/api/admin", tags=["租户管理"])

@admin_router.post("/tenants")
async def create_tenant(body: CreateTenantRequest, ...) -> TenantResponse

@admin_router.get("/tenants")
async def list_tenants() -> List[TenantResponse]

@admin_router.get("/tenants/{tenant_id}")
async def get_tenant(tenant_id: str) -> TenantResponse

@admin_router.put("/tenants/{tenant_id}")
async def update_tenant(tenant_id: str, body: UpdateTenantRequest) -> TenantResponse

@admin_router.delete("/tenants/{tenant_id}")
async def delete_tenant(tenant_id: str) -> DeleteResponse

@admin_router.post("/tenants/{tenant_id}/rotate-key")
async def rotate_api_key(tenant_id: str) -> TenantKeyResponse

@admin_router.get("/tenants/{tenant_id}/stats")
async def get_tenant_stats(tenant_id: str) -> TenantStatsResponse
```

**TenantStats 统计：**
```python
class TenantStatsResponse(BaseModel):
    tenant_id: str
    document_count: int
    total_calls: int
    prompt_tokens: int
    completion_tokens: int
    created_at: datetime
    last_request: datetime
```

**`api/schemas.py` — 新增：**
- `CreateTenantRequest`
- `UpdateTenantRequest`
- `TenantResponse`
- `TenantKeyResponse`
- `TenantStatsResponse`

---

## 阶段 P2 — 运维与增强（2-3 天）

### 6. Token 使用监控（按租户）

```python
# services/sensenova_client.py
class TokenMonitor:
    def __init__(self, tenant_id: str = ""):
        self._tenant_id = tenant_id
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0

    def get_stats(self):
        return {
            "tenant_id": self._tenant_id,
            "total_calls": self._total_calls,
            "total_prompt_tokens": self._total_prompt_tokens,
            "total_completion_tokens": self._total_completion_tokens,
        }
```

### 7. 数据迁移脚本

```python
# scripts/migrate_single_to_multi_tenant.py
"""
单租户 → 多租户迁移：
1. 扫描现有 Zvec 所有文档，读 tenant_id 字段
2. 未标记的标记为 "default"
3. 在 tenant_service 中创建 default 租户
4. 验证数据完整性
"""
```

### 8. 配置新增

```yaml
# .env 新增
# --- 租户存储 ---
TENANT_STORAGE_TYPE=sqlite
TENANT_STORAGE_PATH=data/tenants.db
# --- 默认租户 ---
DEFAULT_TENANT_ID=tenant_001
DEFAULT_TENANT_API_KEY=sk_default_001
# --- 限流存储（多实例用 Redis）---
RATE_LIMIT_STORAGE_URI=redis://localhost:6379/0
```

---

## 文件改动汇总

| 文件 | 改动类型 | 工作量 |
|---|---|---|
| `domain/models.py` | **大改** — 加 Tenant 模型，Document 加 tenant_id | 2h |
| `domain/exceptions.py` | **小改** — 加 TenantNotFoundError | 0.5h |
| `api/schemas.py` | **大改** — 新增 Tenant CRUD 请求/响应模型 | 3h |
| `api/routes.py` | **大改** — 重构鉴权，加租户管理路由，所有路由加 tenant | 6h |
| `api/main.py` | **大改** — 单例改工厂分发，加限流中间件 | 4h |
| `core/retriever.py` | **中改** — 支持 tenant_id 透传，LLM client 按租户创建 | 3h |
| `services/knowledge_service.py` | **中改** — 所有方法加 tenant 参数 | 3h |
| `services/sensenova_client.py` | **中改** — 支持运行时传入租户 LLM 配置 | 2h |
| `services/tenant_service.py` | **新建** — 租户 CRUD | 4h |
| `infrastructure/zvec_store.py` | **大改** — 所有方法加 tenant filter | 5h |
| `infrastructure/interfaces/vector_store.py` | **小改** — 接口加 tenant 参数 | 1h |
| `infrastructure/interfaces/llm_client.py` | **小改** | 0.5h |
| `infrastructure/llm_response_cache.py` | **中改** — 支持 tenant 隔离 cache_path | 2h |
| `utils/limiter.py` | **中改** — key_func 改按 tenant | 2h |
| `utils/dynamic_limiter.py` | **新建** — 动态限流中间件 | 3h |
| `utils/config.py` | **小改** — 加 TenantStorageSettings | 1h |
| `scripts/migrate_single_to_multi_tenant.py` | **新建** — 数据迁移 | 3h |

**总计：约 48 人时 = 6 个工作日**
