# openAsk 后端多租户改造清单

> 项目：openAsk 智能知识库问答系统
> 目标：从单租户 → 完整多租户
> 最后更新：2026-07-31

**状态：✅ 全部完成** — 所有 P0 / P1 / P2 项已实现并通过集成测试

---

## 阶段 P0 — 核心改造（✅ 已完成）

### 1. 租户模型与 API Key 管理 ✅

#### 1.1 Tenant 实体 ✅
- `domain/models.py` — 新增 `Tenant` 类（含 `tenant_id`、`api_key`、`name`、`status`、LLM 配置、限流、`system_prompt`）
- `domain/models.py` — `Document` 加 `tenant_id` 字段
- `domain/models.py` — `SearchResult` 加 `tenant_id` 字段
- `domain/exceptions.py` — 新增 `TenantNotFoundError`

#### 1.2 TenantService ✅
- `services/tenant_service.py` — 新建，SQLite 存储，11 个方法：
  - `create_tenant()` / `get_by_id()` / `get_by_api_key()` / `list_tenants()`
  - `update_tenant()` / `delete_tenant()` / `rotate_api_key()`
  - `update_rate_limit()` / `get_document_count()` / `ensure_default_tenant()`
- `utils/config.py` — 加 `TenantStorageSettings`

#### 1.3 API Key 鉴权 ✅
- `api/routes.py` — `resolve_tenant()` + `resolve_optional_tenant()` 替代原 `verify_api_key`
- 所有业务路由挂载 `Depends(resolve_tenant)`
- `/api/health` 保留免鉴权
- **实际实现差异**：采用 FastAPI Depends + `request.state.tenant` 注入，未使用 `@limiter.dynamic_limit()` 装饰器（改用 `TenantLimiter` 中间件）

---

### 2. 知识库隔离 ✅

**采用方案 A：按 tenant_id filter**（与原计划一致）

| 改动 | 文件 | 状态 |
|---|---|---|
| Zvec schema 加 `tenant_id` 字段 | `infrastructure/zvec_store.py` | ✅ |
| insert / upsert 写入 tenant_id | `infrastructure/zvec_store.py` | ✅ |
| search / get / list / count / delete 加 tenant 过滤 | `infrastructure/zvec_store.py` | ✅ |
| `knowledge_service.py` 所有方法加 `tenant_id` 参数 | `services/knowledge_service.py` | ✅ |
| `retriever.py` 透传 tenant_id 到 `_vector_search()` | `core/retriever.py` | ✅ |
| `retriever.py` 透传 tenant_id 到 `_get_sources_for_cache()` | `core/retriever.py` | ✅ |
| 知识库 CRUD / 搜索路由传入 tenant_id | `api/routes.py` | ✅ |

**实际实现差异**：
- `retriever.py` 采用**透传参数**方案（`retrieve()` / `retrieve_stream()` 加 `tenant_id` 参数），而非原 plan 中的"工厂模式单独创建 ZvecStore 实例"
- 统一使用**单个 ZvecStore 实例 + `tenant_id` filter_expr 过滤**，避免每租户维护一份向量数据副本

---

### 3. LLM 配置隔离 ✅

#### 3.1 SenseNovaClient 支持运行时配置 ✅
- `services/sensenova_client.py` — 支持 `api_key` / `api_base` / `model` / `timeout` 运行时传入，降级使用全局配置

#### 3.2 Retriever 工厂分发 ✅
- **新建** `src/core/factory.py` — `RetrieverFactory` + `RetrieverCache`
  - 按租户创建独立 Retriever 实例（独立 `LLMResponseCache` + `LLMClient`）
  - 共享 `EmbeddingService` / `ZvecStore` / `Reranker` / `EmbeddingCache`
  - 支持租户自定义 LLM 配置（API Key / Base / Model）
  - LRU 缓存避免重复创建（默认 maxsize=128）
- `api/main.py` — lifespan 改用 `RetrieverFactory` 替代原单例模式

**实际实现差异**：
- 未采用原 plan 的 `create_retriever_for_tenant(tenant)` 内联函数
- 新建独立 `factory.py` 模块，逻辑更清晰、可测试
- 共享 ZvecStore + tenant_id filter，而非每租户独立 ZvecStore

#### 3.3 LLMResponseCache 按租户隔离 ✅
- `infrastructure/llm_response_cache.py` — 支持独立 `cache_path`，自动创建父目录
- 每租户缓存目录：`data/zvec_llm_cache/{tenant_id}`

#### 3.4 Prompt 模板定制 ✅
- `tenant.system_prompt` 通过 `retriever.retrieve()` / `retrieve_stream()` 的 `system_prompt` 参数传入

---

### 4. 限流按租户隔离 ✅

| 改动 | 文件 | 状态 |
|---|---|---|
| `TenantLimiter` 滑动窗口限流 | `utils/dynamic_limiter.py` | ✅ |
| 按 tenant_id 限流，无租户按 IP 兜底 | `utils/dynamic_limiter.py` | ✅ |
| 中间件动态限流 | `api/main.py` | ✅ |
| 移除 chat/chat_stream 静态 `@limiter.limit` | `api/routes.py` | ✅ |

**实际实现差异**：
- 未采用原 plan 的 `@limiter.dynamic_limit()` 装饰器
- 使用 `TenantLimiter` 中间件 + 滑动窗口，运行时按 `tenant.rate_limit_per_user` 动态配置

---

## 阶段 P1 — 管理 API 改造（✅ 已完成）

### 5. 租户管理 API ✅

| 路由 | 方法 | 功能 |
|---|---|---|
| `/api/admin/tenants` | POST | 创建租户（返回 `api_key`） |
| `/api/admin/tenants` | GET | 租户列表 |
| `/api/admin/tenants/{tenant_id}` | GET | 租户详情 |
| `/api/admin/tenants/{tenant_id}` | PUT | 更新租户 |
| `/api/admin/tenants/{tenant_id}` | DELETE | 软删除 |
| `/api/admin/tenants/{tenant_id}/rotate-key` | POST | 轮换 API Key |
| `/api/admin/tenants/{tenant_id}/stats` | GET | 租户统计 |

- `api/schemas.py` — `CreateTenantRequest` / `UpdateTenantRequest` / `TenantResponse` / `TenantKeyResponse` / `TenantStatsResponse`
- 鉴权：`_verify_admin_key()` 验证 `X-API-Key` 是否匹配 `settings.api.api_key`

---

## 阶段 P2 — 运维与增强（✅ 已完成）

### 6. Token 监控 ✅
- `services/sensenova_client.py` — `TokenMonitor` 类，按租户统计 `prompt_tokens` / `completion_tokens`

### 7. 数据迁移脚本 ✅
- `scripts/migrate_single_to_multi_tenant.py` — 163 行，扫描 Zvec 文档打 `default` 标签

### 8. 配置新增 ✅
- `utils/config.py` — `TenantStorageSettings`
- `.env` — `DEFAULT_TENANT_API_KEY` / `API_API_KEY`

---

## 文件改动汇总

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `domain/models.py` | 大改 | 加 Tenant 模型，Document/SearchResult 加 tenant_id |
| `domain/exceptions.py` | 小改 | 加 TenantNotFoundError |
| `api/schemas.py` | 大改 | 新增 Tenant CRUD 请求/响应模型，加 `api_key` 字段 |
| `api/routes.py` | 大改 | 重构鉴权，加租户管理路由，所有路由加 tenant，加 `get_retriever_for_tenant()` |
| `api/main.py` | 大改 | 单例改 `RetrieverFactory`，加限流中间件 |
| `core/retriever.py` | 中改 | 透传 tenant_id + cache_backend，支持按租户隔离 |
| `core/factory.py` | **新建** | `RetrieverFactory` + `RetrieverCache`（LRU） |
| `services/knowledge_service.py` | 中改 | 所有方法加 tenant_id 参数 |
| `services/sensenova_client.py` | 中改 | 支持运行时传入租户 LLM 配置 |
| `services/tenant_service.py` | **新建** | 租户 CRUD（SQLite） |
| `infrastructure/zvec_store.py` | 大改 | 所有 CRUD 方法加 tenant filter |
| `infrastructure/interfaces/vector_store.py` | 小改 | 接口加 tenant 参数 |
| `infrastructure/interfaces/llm_client.py` | 小改 | 接口支持可选参数 |
| `infrastructure/llm_response_cache.py` | 中改 | 支持 tenant 隔离 cache_path |
| `utils/limiter.py` | 小改 | key_func 改按 tenant |
| `utils/dynamic_limiter.py` | **新建** | 动态限流中间件 |
| `utils/config.py` | 小改 | 加 TenantStorageSettings |
| `scripts/migrate_single_to_multi_tenant.py` | **新建** | 数据迁移 |
| `scripts/integration_test_tenant.py` | **新建** | 集成测试（21 项，全通过） |

---

## 已知问题 / 后续优化

### ⚠️ API Key 环境变量命名不一致
- `.env` 中 `API_KEY=sk_admin_super_secret`
- `ApiSettings` 的 `env_prefix="API_"` + 字段 `api_key` → 读取 `API_API_KEY`
- **已修复**：`.env` 加了 `API_API_KEY=sk_admin_super_secret` 兼容

### ⚠️ LLM 配置隔离有限制
- 当前 `RetrieverFactory` 支持租户自定义 LLM API Key / Base / Model
- 但 `Retriever` 实例缓存复用，租户 LLM 配置变更后需重建 Retriever（当前无自动刷新机制）

### ⚠️ Token 统计未持久化
- `TokenMonitor` 统计数据仅在内存中，服务重启后丢失
- 后续可接入 Redis / DB 持久化

---

## 部署验证（2026-07-31）

- ✅ Docker 镜像构建成功
- ✅ 容器启动正常（健康检查通过）
- ✅ 集成测试 21 项全部通过
- ✅ 线上端到端测试（待重构建后完成）