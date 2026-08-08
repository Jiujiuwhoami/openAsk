# OpenAsk API 接口清单

> 基于知识库的智能问答系统 — FastAPI 后端  
> 版本: v2.0 (SaaS) | 基础路径: `/api`

---

## 目录

1. [鉴权方式](#1-鉴权方式)
2. [认证 API](#2-认证-api-apiauth)
3. [项目管理 API](#3-项目管理-api-apiprojects)
4. [问答 API](#4-问答-api-apichat)
5. [知识库 API](#5-知识库-api-apiknowledge)
6. [搜索 API](#6-搜索-api-apisearch)
7. [分析 API](#7-分析-api-apiprojectsid)
8. [会话管理 API](#8-会话管理-api-apiprojectsidconversations)
9. [电商 API](#9-电商-api)
10. [计费 API](#10-计费-api-apibilling)
11. [管理后台 API](#11-管理后台-api-apiadmin)
12. [系统 API](#12-系统-api)
13. [套餐与限制](#13-套餐与限制)
14. [全局中间件](#14-全局中间件)
15. [错误码汇总](#15-错误码汇总)

---

## 1. 鉴权方式

该系统使用 **两种鉴权方式**，通过不同的路由区分：

| 方式 | 鉴权头 | 适用范围 | 说明 |
|------|--------|----------|------|
| **JWT Bearer Token** | `Authorization: Bearer <token>` | `/api/auth/*`, `/api/projects/*`, `/api/billing/*`, `/api/admin/*`, `/api/projects/{id}/*` 的分析/会话/电商等 | 用户登录后获取，有效期 24h，用户管理端使用 |
| **X-API-Key** | `X-API-Key: sk_<hex>` | `/api/chat/*`, `/api/knowledge/*`, `/api/search/*`, `/api/projects/{id}/handoff` | 每个项目自动生成，嵌入脚本/第三方客户端使用 |

**优先级**：API Key 鉴权的路由在 `resolve_project` 依赖中按 project_id 过滤，实现租户隔离。

---

## 2. 认证 API (`/api/auth`)

### 2.1 用户注册

```http
POST /api/auth/register
Content-Type: application/json
```

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `email` | string | 是 | 用户邮箱，最长 255 |
| `password` | string | 是 | 密码，8~128 位 |
| `name` | string | 否 | 用户名称，最长 100 |

**响应 200：** `RegisterTokenResponse`

```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "user": {
    "user_id": "str",
    "email": "str",
    "name": "str",
    "is_verified": false,
    "is_admin": false,
    "created_at": 1234567890
  },
  "project": {
    "project_id": "str",
    "name": "str",
    "api_key": "sk_..."
  }
}
```

**特性：** 注册成功自动创建第一个项目，自动生成 JWT token（免去注册后手动登录）。

| 场景 | 状态码 |
|------|--------|
| 正常注册 | 200 |
| 重复邮箱 | 409 |
| 密码太短 / 无效邮箱 | 422 |

---

### 2.2 用户登录

```http
POST /api/auth/token
Content-Type: application/x-www-form-urlencoded
```

**限流：** 10 次/分钟

**请求体（OAuth2 Password Flow 标准）：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `username` | string | 是 | 邮箱 |
| `password` | string | 是 | 密码 |

**响应 200：** `TokenResponse`

```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "user": { "user_id": "str", "email": "str", "name": "str", "is_verified": false, "is_admin": false, "created_at": 1234567890 }
}
```

| 场景 | 状态码 |
|------|--------|
| 正确凭证 | 200 |
| 错误密码 / 不存在邮箱 | 401（统一提示"邮箱或密码错误"） |
| 账户已禁用 | 403 |

---

### 2.3 获取当前用户

```http
GET /api/auth/me
Authorization: Bearer <token>
```

**响应 200：** `MeResponse`

```json
{
  "user_id": "str",
  "email": "str",
  "name": "str",
  "is_verified": false,
  "is_admin": false,
  "created_at": 1234567890
}
```

| 场景 | 状态码 |
|------|--------|
| 有效 token | 200 |
| 无/无效/过期 token | 401 |

---

### 2.4 发送邮箱验证

```http
POST /api/auth/send-verification
```

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `email` | string | 是 | 用户邮箱 |

**响应 200：** `{"message": "验证邮件已发送，请检查邮箱"}`

| 场景 | 状态码 |
|------|--------|
| 发送成功 | 200 |
| 未注册邮箱 | 404 |
| 已验证邮箱 | 200（提示已验证） |
| 频率限制 | 429 |

---

### 2.5 验证邮箱

```http
POST /api/auth/verify-email
```

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `token` | string | 是 | 验证 token（邮件中获取） |

**响应 200：** `{"message": "邮箱验证成功"}`

| 场景 | 状态码 |
|------|--------|
| 验证成功 | 200 |
| 无效/过期 token | 401 |

---

### 2.6 发送密码重置

```http
POST /api/auth/forgot-password
```

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `email` | string | 是 | 用户邮箱 |

**响应 200：** `{"message": "如果该邮箱已注册，你将收到密码重置邮件"}`

**安全：** 无论邮箱是否存在均返回 200（防止邮箱枚举）。

---

### 2.7 重置密码

```http
POST /api/auth/reset-password
```

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `token` | string | 是 | 重置 token（邮件中获取，15 分钟有效） |
| `password` | string | 是 | 新密码，8~128 位 |

**响应 200：** `{"message": "密码重置成功，请使用新密码登录"}`

| 场景 | 状态码 |
|------|--------|
| 重置成功 | 200 |
| 无效/过期 token | 401 |
| 密码太短 | 422 |

---

### 2.8 修改密码（需登录）

```http
POST /api/auth/change-password
Authorization: Bearer <token>
```

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `old_password` | string | 是 | 旧密码 |
| `new_password` | string | 是 | 新密码，8~128 位 |

**响应 200：** `{"message": "密码修改成功"}`

| 场景 | 状态码 |
|------|--------|
| 修改成功 | 200 |
| 旧密码错误 | 401 |
| 新密码太短 | 422 |
| 未登录 | 401 |

---

## 3. 项目管理 API (`/api/projects`)

> 鉴权：所有端点需 `Authorization: Bearer <token>`（JWT 用户登录）

### 3.1 项目列表

```http
GET /api/projects
```

**响应 200：** `ProjectResponse[]`

```json
[
  {
    "project_id": "str",
    "name": "str",
    "api_key": "sk_...",
    "status": "active",
    "created_at": 1234567890
  }
]
```

### 3.2 创建项目

```http
POST /api/projects
```

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 项目名称，1~200 字符 |

**响应 200：** `ProjectResponse`（含完整 API Key）

### 3.3 项目详情

```http
GET /api/projects/{project_id}
```

**响应 200：** `ProjectDetailResponse`

```json
{
  "project_id": "str",
  "name": "str",
  "status": "active",
  "llm_api_base": "str",
  "llm_model": "str",
  "llm_timeout": 30,
  "rate_limit_per_user": "10/minute",
  "rate_limit_global": "100/minute",
  "system_prompt": "str",
  "language": "zh",
  "created_at": 1234567890,
  "updated_at": 1234567890
}
```

| 场景 | 状态码 |
|------|--------|
| 自己的项目 | 200 |
| 别人的项目 / 不存在 | 404 |

### 3.4 更新项目

```http
PUT /api/projects/{project_id}
```

**请求体（全部可选，部分更新）：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 项目名称，最长 200 |
| `llm_api_key` | string | LLM API Key |
| `llm_api_base` | string | LLM API 地址 |
| `llm_model` | string | LLM 模型名 |
| `llm_timeout` | int | LLM 超时秒数，5~120 |
| `rate_limit_per_user` | string | 每用户限流，如 `"10/minute"` |
| `rate_limit_global` | string | 全局限流 |
| `system_prompt` | string | 自定义系统 Prompt |
| `language` | string | `"zh"` 或 `"en"` |

**响应 200：** `{"success": true, "project_id": "str"}`

### 3.5 删除项目

```http
DELETE /api/projects/{project_id}
```

**响应 200：** `{"success": true, "message": "项目已删除"}`

> 软删除，删除后 API Key 立即失效。

### 3.6 轮换 API Key

```http
POST /api/projects/{project_id}/rotate-key
```

**响应 200：** `{"api_key": "sk_..."}`

> 旧 key 立即失效。

### 3.7 项目使用统计

```http
GET /api/projects/{project_id}/stats
```

**响应 200：** `ProjectStatsResponse`

```json
{
  "project_id": "str",
  "document_count": 0,
  "total_calls": 0,
  "prompt_tokens": 0,
  "completion_tokens": 0,
  "cache_hit_rate": 0.0,
  "created_at": 1234567890,
  "last_request": 0
}
```

### 3.8 获取嵌入脚本

```http
GET /api/projects/{project_id}/embed-script
```

**响应 200：** `EmbedScriptResponse`

```json
{
  "script": "<script>...</script>"
}
```

> 自动从请求头推导 API 地址（兼容反向代理）。

---

## 4. 问答 API (`/api/chat`)

> 鉴权：`X-API-Key: sk_<hex>`  
> 限流：按项目配置（Per-User Rate Limit）+ 套餐用量限制

### 4.1 非流式问答

```http
POST /api/chat
X-API-Key: sk_...
Content-Type: application/json
```

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | string | 是 | 用户问题，1~2000 字符 |
| `top_k` | int | 否 | 返回文档数，1~20，默认 10 |
| `conversation_id` | string | 否 | 会话 ID，续传时传此值 |
| `messages` | ChatMessage[] | 否 | 多轮对话历史（优先于 `conversation_id` 的历史） |
| `language` | string | 否 | `"zh"` / `"en"`，覆盖项目设置 |

**`ChatMessage`：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `role` | string | `"user"` 或 `"assistant"` |
| `content` | string | 消息内容，最长 10000 |

**响应 200：** `ChatResponse`

```json
{
  "answer": "生成的回答内容",
  "sources": [
    {
      "doc_id": "str",
      "title": "文档标题",
      "content": "文档内容预览",
      "score": 0.9234
    }
  ],
  "cache_hit": false,
  "llm_used": true,
  "conversation_id": "str",
  "handoff_suggested": false
}
```

| 场景 | 状态码 |
|------|--------|
| 正常回答 | 200 |
| 包含敏感词 | 400 |
| API Key 无效 | 401 |
| 项目已禁用 | 403 |
| 超出套餐用量 | 402 |
| 超限流 | 429 |

---

### 4.2 流式问答（SSE）

```http
POST /api/chat/stream
X-API-Key: sk_...
Content-Type: application/json
```

**请求体：** 同非流式 `ChatRequest`

**响应：** `text/event-stream`

**SSE 事件格式：**

```
data: {"event": "conversation_id", "data": "str"}
data: {"event": "sources", "data": [{"doc_id": "...", "title": "...", "content": "...", "score": 0.0}]}
data: {"event": "answer_delta", "data": "逐字回复片段"}
data: {"event": "cache_hit", "data": true}
data: {"event": "handoff_suggested", "data": false}
data: {"event": "done", "data": null}
data: {"event": "error", "data": "错误信息"}
```

**事件类型：**

| 事件 | 说明 |
|------|------|
| `conversation_id` | 会话 ID（新对话自动创建，前端保存用于续传） |
| `sources` | 引用的来源文档列表 |
| `answer_delta` | 回答的逐字片段 |
| `cache_hit` | 是否命中缓存 |
| `handoff_suggested` | 是否建议转人工客服 |
| `done` | 流式完成 |
| `error` | 错误信息 |

**响应头：** `Cache-Control: no-cache`, `Connection: keep-alive`, `X-Accel-Buffering: no`

---

## 5. 知识库 API (`/api/knowledge`)

> 鉴权：`X-API-Key: sk_<hex>`  
> 创建/上传/批量删除有额外限流

### 5.1 创建文档

```http
POST /api/knowledge
X-API-Key: sk_...
```

**限流：** 30 次/分钟

**请求体：** `DocumentRequest`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `title` | string | 是 | 文档标题，1~200 字符 |
| `content` | string | 是 | 文档内容 |
| `tags` | string[] | 否 | 标签列表 |
| `source` | string | 否 | 来源 |

**响应 200：** `DocumentResponse`

### 5.2 上传文档文件

```http
POST /api/knowledge/upload
X-API-Key: sk_...
Content-Type: multipart/form-data
```

**限流：** 10 次/分钟

**请求体：** `file` (UploadFile)

| 字段 | 说明 |
|------|------|
| 支持格式 | `.md`, `.txt`, `.pdf`, `.docx`, `.html` |
| 编码 | UTF-8 |

**响应 200：** `DocumentResponse`

### 5.3 获取文档

```http
GET /api/knowledge/{doc_id}
X-API-Key: sk_...
```

**响应 200：** `DocumentResponse`

| 场景 | 状态码 |
|------|--------|
| 文档存在 | 200 |
| 不存在 | 404 |

### 5.4 更新文档

```http
PUT /api/knowledge/{doc_id}
X-API-Key: sk_...
```

**限流：** 30 次/分钟

**请求体：** `UpdateDocumentRequest`（全部可选）

| 字段 | 类型 | 说明 |
|------|------|------|
| `title` | string | 标题 |
| `content` | string | 内容 |
| `tags` | string[] | 标签 |
| `source` | string | 来源 |

### 5.5 删除文档

```http
DELETE /api/knowledge/{doc_id}
X-API-Key: sk_...
```

**响应 200：** `DeleteResponse`

### 5.6 批量删除文档

```http
POST /api/knowledge/batch-delete
X-API-Key: sk_...
```

**限流：** 10 次/分钟

**请求体：** `BatchDeleteRequest`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `doc_ids` | string[] | 是 | 文档 ID 列表 |

### 5.7 文档列表（分页）

```http
GET /api/knowledge?page=1&page_size=10
X-API-Key: sk_...
```

**查询参数：**

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `page` | int | 1 | 页码 |
| `page_size` | int | 10 | 每页数量 |

**响应 200：** `PaginatedResponse`

```json
{
  "items": [DocumentResponse, ...],
  "total": 100,
  "page": 1,
  "page_size": 10
}
```

---

## 6. 搜索 API (`/api/search`)

> 鉴权：`X-API-Key: sk_<hex>`

### 6.1 语义搜索

```http
POST /api/search
X-API-Key: sk_...
```

**限流：** 按项目配置

**请求体：** `SearchRequest`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | string | 是 | 搜索关键词，1~2000 字符 |
| `top_k` | int | 否 | 返回数量，1~50，默认 10 |

**响应 200：** `SearchResultResponse[]`

```json
[
  {
    "doc_id": "str",
    "title": "str",
    "content": "预览内容 (截断 500 字)...",
    "score": 0.9234
  }
]
```

### 6.2 批量搜索

```http
POST /api/search/batch
X-API-Key: sk_...
```

**限流：** 30 次/分钟

**请求体：** `BatchSearchRequest`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `queries` | string[] | 是 | 搜索关键词列表，1~50 个 |
| `top_k` | int | 否 | 每个查询返回数量，1~50，默认 10 |

**响应 200：** `BatchSearchResultItem[]`

```json
[
  {
    "query_index": 0,
    "query": "搜索内容",
    "results": [SearchResultResponse, ...]
  }
]
```

---

## 7. 分析 API (`/api/projects/{id}`)

> 鉴权：`Authorization: Bearer <token>`（JWT 用户登录）  
> 但 `POST /handoff` 使用 `X-API-Key` 鉴权

### 7.1 问答日志列表

```http
GET /api/projects/{project_id}/logs?page=1&page_size=20&search=
```

**查询参数：**

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `page` | int | 1 | 页码 |
| `page_size` | int | 20 | 每页数量，最大 100 |
| `search` | string | "" | 搜索关键词 |

### 7.2 导出日志

```http
GET /api/projects/{project_id}/logs/export?format=csv
```

**查询参数：**

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `format` | string | `"csv"` | `"csv"` 或 `"json"` |

**响应：** 文件下载（`Content-Disposition: attachment`）

### 7.3 问答量趋势

```http
GET /api/projects/{project_id}/analytics/trends?days=30
```

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `days` | int | 30 | 天数范围，1~365 |

### 7.4 热门问题

```http
GET /api/projects/{project_id}/analytics/top-questions?limit=10&days=30
```

### 7.5 满意度统计

```http
GET /api/projects/{project_id}/analytics/satisfaction?days=30
```

### 7.6 知识库缺口分析

```http
GET /api/projects/{project_id}/analytics/gaps?days=30&limit=20
```

> 返回 AI 答不上来的问题列表（用于发现知识库缺失内容）。

### 7.7 提交反馈

```http
POST /api/projects/{project_id}/feedback
```

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `log_id` | int | 是 | 日志 ID |
| `rating` | string | 是 | `"good"` 或 `"bad"` |

### 7.8 提交人工转接

```http
POST /api/projects/{project_id}/handoff
X-API-Key: sk_...
```

**鉴权：** X-API-Key（嵌入脚本/管理面板均可调用）

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `conversation_id` | string | 否 | 会话 ID |
| `query` | string | 是 | 用户问题，最长 2000 |
| `contact_email` | string | 否 | 联系邮箱 |
| `contact_phone` | string | 否 | 联系电话 |
| `note` | string | 否 | 补充说明，最长 1000 |

**特性：** 提交后自动发送邮件通知项目所有者。

### 7.9 转接请求列表

```http
GET /api/projects/{project_id}/handoffs?page=1&page_size=20&status=pending
```

**查询参数：**

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `status` | string | 全部 | `"pending"` / `"resolved"` / `"closed"` |

### 7.10 标记转接已处理

```http
POST /api/projects/{project_id}/handoffs/{handoff_id}/resolve
```

---

## 8. 会话管理 API (`/api/projects/{id}/conversations`)

> 鉴权：`Authorization: Bearer <token>`（JWT 用户登录）

### 8.1 会话列表

```http
GET /api/projects/{project_id}/conversations?page=1&page_size=20
```

**响应：** 按更新时间倒序的会话列表。

### 8.2 会话详情

```http
GET /api/projects/{project_id}/conversations/{conversation_id}?page=1&page_size=50
```

**查询参数：**

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `page` | int | 1 | 消息页码 |
| `page_size` | int | 50 | 每页消息数，最大 200 |

**响应：**

```json
{
  "conversation_id": "str",
  "project_id": "str",
  "title": "str",
  "status": "str",
  "message_count": 0,
  "created_at": 1234567890,
  "updated_at": 1234567890,
  "messages": [{"role": "user/assistant", "content": "str", ...}],
  "total_messages": 0
}
```

### 8.3 删除会话

```http
DELETE /api/projects/{project_id}/conversations/{conversation_id}
```

> 物理删除，含所有消息。

### 8.4 更新会话标题

```http
PUT /api/projects/{project_id}/conversations/{conversation_id}
```

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `title` | string | 是 | 新标题，最长 200 |

---

## 9. 电商 API

### 9.1 导入商品

```http
POST /api/projects/{project_id}/import-products
Authorization: Bearer <token>
Content-Type: multipart/form-data
```

**请求体：** `file` (UploadFile)

| 说明 | 详情 |
|------|------|
| 支持格式 | CSV / JSON |
| CSV 列 | 商品名称, 商品描述, 规格, 价格, 库存, 标签 |
| JSON 格式 | `[{ "name": "...", "description": "...", "price": "..." }]` |
| 编码 | UTF-8 |

### 9.2 FAQ 模板列表

```http
GET /api/templates
```

**公开接口，无需鉴权。**

### 9.3 模板详情

```http
GET /api/templates/{template_id}
```

### 9.4 应用模板

```http
POST /api/projects/{project_id}/templates/{template_id}
Authorization: Bearer <token>
```

> 将 FAQ 模板内容批量导入项目知识库。

---

## 10. 计费 API (`/api/billing`)

> 鉴权：`Authorization: Bearer <token>`（JWT 用户登录）

### 10.1 获取套餐信息

```http
GET /api/billing/plan?project_id={project_id}
```

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `project_id` | string | 否 | 项目 ID，不传则取第一个项目 |

**响应 200：** `PlanInfo`

```json
{
  "plan": "free",
  "limits": {
    "max_documents": 100,
    "max_monthly_calls": 1000,
    "max_projects": 3,
    "rate_per_minute": 10,
    "custom_llm": false,
    "team_members": 1,
    "support": "community"
  },
  "usage": {
    "call_count": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0
  },
  "stripe_customer_id": ""
}
```

### 10.2 创建 Stripe Checkout

```http
POST /api/billing/create-checkout
```

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `project_id` | string | 是 | 项目 ID |
| `plan` | string | 是 | `"pro"` 或 `"enterprise"` |

**响应 200：** `{"url": "https://checkout.stripe.com/..."}`

### 10.3 创建 Stripe 客户门户

```http
POST /api/billing/portal
```

**响应 200：** `{"url": "https://billing.stripe.com/..."}`

> 用于管理订阅、查看账单、更新支付方式。

### 10.4 Stripe Webhook

```http
POST /api/stripe/webhook
```

**请求头：** `stripe-signature: <signature>`

**处理事件：**

| 事件类型 | 行为 |
|----------|------|
| `checkout.session.completed` | 激活订阅，取消旧订阅（如有） |
| `invoice.paid` | 续费成功（无操作，日志记录） |
| `customer.subscription.deleted` | 取消订阅，降级为 Free |
| `customer.subscription.updated` | 同步套餐切换（Stripe 门户操作） |

### 10.5 获取账单列表

```http
GET /api/billing/invoices
```

**响应：** 最近 12 条已支付发票（从 Stripe 拉取）。

---

## 11. 管理后台 API (`/api/admin`)

> 鉴权：`Authorization: Bearer <token>` + **管理员权限**（`is_admin=True`）

### 11.1 平台概览统计

```http
GET /api/admin/stats
```

**响应 200：**

```json
{
  "total_users": 0,
  "total_projects": 0,
  "total_calls": 0,
  "prompt_tokens": 0,
  "completion_tokens": 0,
  "cache_hits": 0,
  "cache_hit_rate": 0.0,
  "users_today": 0,
  "projects_today": 0,
  "active_subscriptions": 0
}
```

### 11.2 用户列表

```http
GET /api/admin/users?page=1&page_size=20&search=
```

**查询参数：**

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `page` | int | 1 | 页码 |
| `page_size` | int | 20 | 每页数量，最大 100 |
| `search` | string | "" | 搜索邮箱或名称 |

### 11.3 项目列表

```http
GET /api/admin/projects?page=1&page_size=20&search=
```

**查询参数：** 同用户列表，搜索名称或 ID。

---

## 12. 系统 API

### 12.1 健康检查

```http
GET /api/health
```

**无需鉴权。**

**响应 200：** `HealthResponse`

```json
{
  "status": "healthy",
  "version": "1.0.0",
  "timestamp": "2026-08-07T00:00:00",
  "zvec_status": "healthy",
  "embedding_status": "healthy",
  "llm_status": "healthy",
  "cache_status": "healthy",
  "document_count": 0
}
```

### 12.2 Sitemap

```http
GET /sitemap.xml
```

> SEO 站点地图，含 `/`、`/login`、`/register`、`/forgot-password`。

### 12.3 Robots

```http
GET /robots.txt
```

```
User-agent: *
Allow: /
Sitemap: https://openask.dev/sitemap.xml
```

---

## 13. 套餐与限制

### 套餐定义

| 套餐 | 月费 | 文档数 | 月调用量 | 项目数 | 频率 | 自定义 LLM | 团队成员 | 支持 |
|------|------|--------|----------|--------|------|-----------|----------|------|
| **Free** | $0 | 100 | 1,000 | 3 | 10/min | ❌ | 1 | 社区 |
| **Pro** | $29 | 1,000 | 10,000 | 10 | 60/min | ✅ | 3 | 邮件 |
| **Enterprise** | $99 | 10,000 | 100,000 | 999 | 300/min | ✅ | 10 | 优先 |

### 限流层级

| 层级 | 限制范围 | 说明 |
|------|----------|------|
| 全局慢速限流 | `limiter`（slowapi） | 认证/操作类接口限流 |
| 项目级动态限流 | `project_limiter` | 按项目配置的 `rate_limit_per_user` 动态限流 |
| 套餐用量限制 | `usage_limit_middleware` | 检查月调用量、文档数是否超出套餐 |
| 路由级限流 | `@limiter.limit()` | 特定接口额外限流（如注册 10/min、上传 10/min） |

### 用量限制中间件覆盖路径

| 路径 | 限制行为 |
|------|----------|
| `/api/chat` | 检查月调用量 + 文档数 |
| `/api/search` | 检查月调用量 + 文档数 |
| `/api/search/batch` | 检查月调用量 + 文档数 |
| `POST /api/knowledge` | 路由层单独检查文档数 |
| `POST /api/knowledge/upload` | 路由层单独检查文档数 |

---

## 14. 全局中间件

| 中间件 | 顺序 | 功能 |
|--------|------|------|
| **CORS** | 1 | 允许跨域，来源从 `settings.api.cors_origins` 配置 |
| **动态限流** | 2 | 按项目 API Key 限流，无租户时按 IP |
| **用量限制** | 3 | 检查业务 API 是否超出套餐限制（chat/search） |
| **请求计数** | 4 | 跟踪处理中的请求数，支持优雅关闭 |

### 优雅关闭

- 监听 `SIGTERM` / `SIGINT`
- 收到信号后设置关闭事件，新请求返回 503
- 等待最多 30 秒让正在处理的请求完成
- 按顺序关闭：RetrieverFactory → Reranker → KnowledgeService

---

## 15. 错误码汇总

### HTTP 状态码

| 状态码 | 含义 | 常见场景 |
|--------|------|----------|
| **200** | 成功 | 正常响应 |
| **400** | 请求错误 | 敏感词、无效格式、不支持的格式 |
| **401** | 未授权 | 无效/过期 token、无效 API Key |
| **402** | 需要付费 | 超出套餐用量限制 |
| **403** | 禁止访问 | 账户禁用、项目禁用、非管理员 |
| **404** | 资源不存在 | 文档/项目/会话/用户不存在 |
| **409** | 冲突 | 邮箱已注册 |
| **422** | 参数校验失败 | 密码太短、无效邮箱、参数格式错误 |
| **429** | 请求过频 | 超过限流配额 |
| **500** | 服务器错误 | 内部异常 |
| **503** | 服务不可用 | Stripe 未配置、服务关闭中、依赖服务异常 |

### 错误响应格式

```json
{
  "error": "ErrorType",
  "message": "人类可读的错误信息",
  "timestamp": "2026-08-07T00:00:00"
}
```

### 应用异常类型

| 异常类型 | HTTP 状态码 | 说明 |
|----------|------------|------|
| `DocumentNotFoundError` | 404 | 文档不存在 |
| `KnowledgeBaseError` | 400 | 知识库错误 |
| `MultiModalError` | 400 | 多模态错误 |
| `EmbeddingError` | 503 | 嵌入服务异常 |
| `VectorStoreError` | 503 | 向量数据库异常 |
| `SenseNovaAPIError` | 503 | LLM API 异常 |
| `UserAlreadyExistsError` | 409 | 用户已存在 |
| `InvalidCredentialsError` | 401 | 凭证无效 |
| `UserSuspendedError` | 403 | 用户已禁用 |
| `RateLimitExceeded` | 429 | 请求过频 |
| `UsageLimitExceeded` | 402 | 套餐用量超限 |
| `ServiceUnavailable` | 503 | 服务关闭中 |

---

> 文档版本: v1.0 | 最后更新: 2026-08-07  
> 项目: [OpenAsk](https://github.com/Jiujiuwhoami/openAsk) — 基于知识库的智能问答系统