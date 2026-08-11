# 人工客服转接（Handoff）系统优化修复计划

> 基于 OpenAsk v2.1.1 现有 AI/人工回复流程分析，对标行业标准（Intercom / Zendesk / Freshdesk / LiveChat）设计
> 版本：v1.0 | 日期：2026-08-10

---

## 目录

1. [概述与目标](#1-概述与目标)
2. [现有架构问题矩阵](#2-现有架构问题矩阵)
3. [目标架构设计](#3-目标架构设计)
4. [优化清单（优先级排列）](#4-优化清单优先级排列)
5. [数据模型设计](#5-数据模型设计)
6. [API 设计](#6-api-设计)
7. [前端组件设计](#7-前端组件设计)
8. [WebSocket 实时通信方案](#8-websocket-实时通信方案)
9. [迁移路径](#9-迁移路径)
10. [附录：行业标准对照表](#10-附录行业标准对照表)

---

## 1. 概述与目标

### 1.1 当前状态

OpenAsk 已实现基础的 AI 问答 + 人工转接流程：
- 用户通过 Widget 提问 → AI 自动回复（RAG）
- 系统在检索质量不足时建议转人工
- 客服可在管理后台接管会话并回复
- 会话状态在 `active`（AI）和 `agent`（人工）之间切换

### 1.2 优化目标

| 维度 | 当前 | 目标 |
|------|------|------|
| 转接触发 | 仅系统被动检测（相似度阈值） | 用户主动发起 + 系统多维度检测 + 规则引擎 |
| 实时通信 | HTTP 轮询（2s/5s） | WebSocket 长连接（或 SSE） |
| 客服分配 | 先到先得（手动） | 智能分配（轮询/技能组/最少繁忙） |
| 客服工作台 | 基础聊天 | 带话术库、客户信息、内部笔记、标签 |
| 用户体验 | 无等待提示、无输入状态 | 排队位置、预计等待、正在输入、FAQ推荐 |
| 事后闭环 | 无 | 满意度评价（CSAT）+ 会话标签 + 分析报表 |

---

## 2. 现有架构问题矩阵

| ID | 问题 | 严重程度 | 影响范围 | 涉及模块 |
|----|------|---------|---------|---------|
| P1 | 用户无法主动发起转接 | 🔴 严重 | 用户体验 | Widget / API |
| P2 | 纯轮询实时通信 | 🔴 严重 | 性能/体验 | 整个 agent chat |
| P3 | Handoff 触发逻辑单一 | 🔴 严重 | 转接效率 | Retriever |
| P4 | 无客服在线状态 | 🟡 重要 | 可用性 | Agent 系统 |
| P5 | 无智能分配 | 🟡 重要 | 效率 | Agent 系统 |
| P6 | 无 typing indicator | 🟡 重要 | 用户体验 | Widget / AgentChat |
| P7 | 无话术库 | 🟡 重要 | 客服效率 | AgentChat |
| P8 | 无排队等待机制 | 🟡 重要 | 用户体验 | Widget |
| P9 | 无法客服间转接 | 🟡 重要 | 协作 | Agent 系统 |
| P10 | 客服消息仅纯文本 | 🟢 优化 | 体验 | AgentChat / Widget |
| P11 | 无 CSAT 评价 | 🟢 优化 | 数据闭环 | Widget / Analytics |
| P12 | 无会话标签 | 🟢 优化 | 管理 | Agent 系统 |
| P13 | 无客服工作台统计 | 🟢 优化 | 管理 | Dashboard |
| P14 | 无自动回复/离开消息 | 🟢 优化 | 体验 | Agent 系统 |

---

## 3. 目标架构设计

### 3.1 完整流程

```
用户侧 (Widget)                   后端 (FastAPI)                       客服侧 (Admin Panel)
─────────────────                ────────────────                     ─────────────────────

┌─────────────────────┐           ┌─────────────────────┐             ┌─────────────────────┐
│  AI 自动回复         │           │  RAG 引擎            │             │  在线状态设置        │
│  · 知识库检索        │◄─────────►│  · 检索 → 生成       │            │  · 在线/忙碌/离开    │
│  · 多轮对话          │           │  · handoff 检测      │             │  · 自动/手动切换     │
│  · 流式回答          │           │  · 敏感词过滤        │             │                     │
└──────────┬──────────┘           └─────────────────────┘             └──────────┬──────────┘
           │                                                                     │
           │ 用户主动 / 系统检测                                                  │ 智能分配引擎
           ▼                                                                     ▼
┌─────────────────────┐           ┌─────────────────────┐             ┌─────────────────────┐
│  排队等待             │           │  排队队列             │             │  客服工作台          │
│  · 排队位置           │◄─────────►│  · FIFO + 优先级     │◄───────────►│  · 待接单列表        │
│  · 预计等待时间       │           │  · 技能组匹配        │             │  · 进行中会话        │
│  · 等待中FAQ推荐      │           │  · 超时自动升级      │             │  · 历史记录          │
│  · 可取消排队         │           └─────────────────────┘             │  · 话术库            │
└──────────┬──────────┘                                                 │  · 内部笔记          │
           │                                                             │  · 客户信息侧栏      │
           │ WebSocket 连接                                              │  · 转接同事          │
           ▼                                                             └─────────────────────┘
┌─────────────────────┐           ┌─────────────────────┐
│  人工对话             │◄─────────►│  WebSocket 消息路由   │
│  · 富文本消息         │           │  · 实时消息推送       │
│  · 商品卡片/链接      │           │  · typing 事件       │
│  · typing indicator  │           │  · 已读回执          │
│  · 已读状态           │           └─────────────────────┘
└──────────┬──────────┘
           │
           │ 对话结束
           ▼
┌─────────────────────┐
│  CSAT 满意度评价     │
│  · 评分 (1-5)       │
│  · 评价标签          │
│  · 文字反馈          │
└─────────────────────┘
```

### 3.2 角色模型（扩展）

| 角色 | 说明 | 消息权限 |
|------|------|---------|
| `user` | 终端用户 | 发送消息 |
| `assistant` | AI 自动回复 | 自动生成 |
| `agent` | 客服人员 | 发送消息、转接、结束 |
| `system` | 系统事件（接管、转接、结束通知） | 自动生成 |
| `bot` | 自动回复机器人（排队中FAQ推荐） | 自动生成 |

### 3.3 会话状态机（扩展）

```
                  ┌──────────┐
                  │  active   │  ← AI 自动回复模式
                  └────┬─────┘
                       │
             ┌─────────┴─────────┐
             │                   │
     用户发起转接         系统建议转接
             │                   │
             ▼                   ▼
        ┌──────────┐     ┌──────────────┐
        │ queuing  │ ──► │ 排队中        │  等待客服分配
        └────┬─────┘     └──────────────┘
             │
             │ 客服分配成功
             ▼
        ┌──────────┐
        │  agent   │  ← 客服接管中
        └────┬─────┘
             │
     ┌───────┴────────┐
     │                │
  客服释放         客服转接
     │                │
     ▼                ▼
  ┌───────┐     ┌──────────┐
  │active │     │  agent   │  ← 新客服接管
  └───────┘     └──────────┘
     │
     │ 客服结束 / 超时
     ▼
  ┌──────────┐
  │ closed   │  ← 会话关闭（不再可回复）
  └──────────┘
```

---

## 4. 优化清单（优先级排列）

### 🔴 P0 — 必须修复（影响核心功能）

#### P0-1: 用户主动发起转接

**现状：** 用户只能等系统建议转接，无法主动要求人工服务。

**改造：**
- Widget 增加"转人工"按钮（始终可见，或 AI 连续 N 轮不满意后突出显示）
- 点击后发送 `POST /api/chat/handoff` 请求
- 后端创建转接请求，将会话状态转 `queuing`
- 用户可填写转接原因（可选，降低发起门槛）

**涉及文件：**
- 前端：`widget/` 新增 handoff button + 转接原因弹窗
- 后端：`routes.py` 新增 `POST /api/chat/handoff` 端点
- 后端：`analytics_service.py` 扩展 handoff 模型

---

#### P0-2: WebSocket 实时通信

**现状：** 客服端 5s 轮询 + 对话 2s 轮询，延迟高、资源浪费。

**改造：**
- 引入 WebSocket 连接（或 SSE 长连接）
- 事件类型：
  - `message.new` — 新消息
  - `message.typing` — 正在输入
  - `message.read` — 已读回执
  - `conversation.status` — 状态变更
  - `handoff.new` — 新转接请求
  - `agent.status` — 客服在线状态变更
- 后端连接管理：`ConnectionManager` 维护 `{user_id: [WebSocket]}` 映射
- 连接生命周期：建立 → 鉴权 → 心跳 → 重连

**技术选型对比：**

| 方案 | 优点 | 缺点 | 推荐度 |
|------|------|------|--------|
| **WebSocket** (fastapi-websocket) | 全双工、低延迟、生态成熟 | 需要额外处理心跳/重连 | ⭐⭐⭐⭐⭐ |
| **SSE** (Server-Sent Events) | 简单、兼容好、自动重连 | 单向、客服发消息仍需 POST | ⭐⭐⭐ |
| **长轮询** (Long Polling) | 兼容性最好 | 复杂度位于两者之间 | ⭐⭐ |

**推荐：** WebSocket，后端用 `fastapi.WebSocket` + `redis` 做广播（多实例部署时）

**涉及文件：**
- 后端：新增 `src/api/ws.py` — WebSocket 路由
- 后端：新增 `src/services/ws_manager.py` — 连接管理
- 前端：新增 `src/composables/useWebSocket.ts` — WebSocket Hook
- 前端：`AgentChat.vue` 改用 WebSocket 接收消息
- 前端：Widget 改用 WebSocket 而非轮询

---

#### P0-3: 多维 Handoff 触发引擎

**现状：** 仅靠 `max_score < 0.35` 一个维度决定是否建议转接。

**改造：** 实现 Handoff 判定引擎，综合多个维度：

```python
class HandoffJudge:
    """
    转接判定引擎 — 综合多维度打分，超过阈值触发建议。

    权重配置（可项目级覆盖）：
    - retrieval_score_weight: 0.3  — 检索质量
    - repetition_weight: 0.2       — 重复提问
    - sentiment_weight: 0.2        — 情感分析
    - complexity_weight: 0.15      — 问题复杂度
    - round_weight: 0.15           — 对话轮次
    """

    async def evaluate(
        self,
        query: str,
        sources: List[SearchResult],
        conversation_history: List[Message],
        project_config: dict,
    ) -> HandoffDecision:
        """综合评估是否需要转接。返回决策及原因。"""
        ...
```

**判定维度：**

| 维度 | 检测方式 | 权重 | 触发条件 |
|------|---------|------|---------|
| 🔍 检索质量 | 向量相似度分数 | 30% | 最高分 < 0.35 或 无来源 |
| 🔄 重复提问 | 与历史 N 条 query 的语义相似度 | 20% | 连续 3 轮相似度 > 0.85 |
| 😠 情感分析 | 轻量情感分类（积极/消极/愤怒） | 20% | 检测到负面/愤怒情绪 |
| 📊 问题复杂度 | 问题长度 + 实体数量 + 问句类型 | 15% | 涉及多实体/开放性问题/价格订单 |
| 🔢 对话轮次 | 当前轮次 vs 项目设置阈值 | 15% | 超过 N 轮未解决（默认 8 轮） |

**涉及文件：**
- 新增：`src/services/handoff_judge.py` — 判定引擎
- 修改：`src/core/retriever.py` — 替换 `_check_handoff_needed`
- 新增：`src/services/sentiment_analyzer.py` — 轻量情感分析

---

### 🟡 P1 — 重要优化（显著提升体验）

#### P1-1: 客服在线状态

**现状：** 无法知道客服是否在线，用户可能提交转接但无人接。

**改造：**
```
Agent 状态枚举：
- online    — 在线，可接单
- busy      — 忙碌，不接新单（但可继续现有对话）
- away      — 离开，自动拒单
- offline   — 离线

存储：Redis 或 DB + 心跳
切换：手动切换 + 自动检测（离开自动置 away）
展示：Widget 显示"当前有客服在线" / "当前客服离线"
```

**涉及文件：**
- 后端：新增 `src/services/agent_service.py`
- 后端：`analytics.py` 或新增表 `agent_status`
- 前端：`AgentChat.vue` 增加状态切换
- 前端：Widget 增加在线状态显示

---

#### P1-2: 智能分配引擎

**现状：** 客服手动点击"接管"，先到先得。

**改造：**
```
分配策略（可配置）：
1. 轮询分配 (Round-Robin) — 依次分配给在线客服
2. 最少繁忙 (Least-Busy) — 分配给当前会话数最少的客服
3. 技能组匹配 (Skill-Based) — 按项目/分类匹配指定客服组
4. 手动分配 (Manual) — 保持现有模式，客服自行抢单

优先实现：轮询分配 + 手动抢单（同时支持）
```

**涉及文件：**
- 后端：`agent_service.py` 新增分配逻辑
- 后端：`conversations.py` 接管端点改为自动分配
- 前端：`AgentChat.vue` 支持自动分配场景

---

#### P1-3: 正在输入提示

**现状：** 双方都不知道对方是否在打字。

**改造：**
- WebSocket 事件 `message.typing`（含 conversation_id + user_id）
- 客服端：用户输入时发送，500ms 防抖
- Widget：客服输入时显示"客服正在输入..."
- 超时自动清除（3s 内无新事件则消失）

**涉及文件：**
- 后端：`ws_manager.py` 广播 typing 事件
- 前端：`AgentChat.vue` 发送/接收 typing 事件
- 前端：Widget 接收 typing 事件

---

#### P1-4: 排队等待机制

**现状：** 无排队，所有客服忙碌时转接请求静默堆积。

**改造：**
- 排队队列：Redis 有序集合（`handoff:queue:{project_id}`），score = 时间戳 + 优先级
- 用户端显示：
  - 排队位置（"您前面还有 2 位"）
  - 预计等待时间（基于历史平均处理时间）
  - 等待中 FAQ 推荐（自动推送相关文章）
  - 可取消排队
- 超时升级：排队超过 N 分钟 → 通知所有客服 / 发邮件提醒

**涉及文件：**
- 后端：`agent_service.py` 排队队列管理
- 后端：新增 `POST /api/chat/handoff/cancel`
- 前端：Widget 排队页面组件
- 前端：FAQ 自动推荐

---

#### P1-5: 话术库（Canned Responses）

**现状：** 客服逐字输入，效率低。

**改造：**
- 话术分两级：**项目级**（管理员预设） + **个人级**（客服自己保存）
- 话术分类：问候/常见问题/结束语/退换货/物流等
- 支持快捷插入（`/` 触发搜索，点击插入）
- 话术支持变量：`{customer_name}` / `{order_id}` 等

**涉及文件：**
- 后端：新增 `src/domain/canned_response.py`
- 后端：新增 `src/services/canned_response_service.py`
- 后端：新增 API `GET/POST/PUT/DELETE /api/canned-responses`
- 前端：`AgentChat.vue` 增加话术面板

---

#### P1-6: 客服间转接

**现状：** 客服无法将会话转给其他同事。

**改造：**
- `POST /api/conversations/{id}/transfer` → `{target_agent_id, reason}`
- 转接后原客服的会话列表移除该会话
- 目标客服收到通知（WebSocket 事件）
- 会话历史完整保留
- 可附转接备注（内部可见）

**涉及文件：**
- 后端：`conversations.py` 新增 transfer 端点
- 后端：`ws_manager.py` 转接通知
- 前端：`AgentChat.vue` 转接操作 + 确认弹窗

---

### 🟢 P2 — 体验优化（提升专业度）

#### P2-1: 富文本消息支持

**现状：** 客服消息仅纯文本，无图片、链接、卡片。

**改造：**
- 消息支持 Markdown 子集（粗体、链接、列表）
- 图片上传（直接传图或粘贴截图）
- 商品卡片（结构化数据，显示商品名、价格、链接、图片）
- 订单链接（可点击跳转至订单详情）

**涉及文件：**
- 前端：`AgentChat.vue` 富文本编辑器
- 前端：Widget 富文本渲染
- 后端：`schemas.py` 消息模型扩展

---

#### P2-2: CSAT 满意度评价

**现状：** 对话结束后无评价闭环。

**改造：**
- 客服结束对话（closed）后，自动推送 CSAT 邀请
- 评分：1-5 星 + 评价标签（"解决了我问题" / "回复太慢" / "态度不好"等）
- 可选文字反馈
- 评价数据：记录到 `analytics` 库，可生成报表

**涉及文件：**
- 后端：`analytics_service.py` 新增 CSAT 表
- 后端：新增 `POST /api/feedback/csat`
- 前端：Widget 新增 CSAT 组件
- 前端：Dashboard 新增 CSAT 数据卡片

---

#### P2-3: 会话标签

**现状：** 无法对会话分类管理。

**改造：**
- 预置标签：咨询/投诉/退换货/物流/其他
- 自定义标签：客服可添加/移除
- 按标签筛选会话列表
- 标签分析报表

**涉及文件：**
- 后端：`conversation_service.py` 扩展标签字段
- 后端：API 扩展标签 CRUD
- 前端：`AgentChat.vue` 标签管理

---

#### P2-4: 自动回复与离开消息

**现状：** 客服下班后无自动响应。

**改造：**
- 客服离线/离开时，自动回复："当前暂无在线客服，请留下您的问题，我们将在 XX 小时内回复"
- 客服忙碌时，自动回复："当前咨询量较大，您前面还有 X 位，请稍候"
- 可配置项目级自动回复模板

**涉及文件：**
- 后端：`agent_service.py` 自动回复触发器
- 后端：配置模板存储

---

#### P2-5: 客服工作台统计

**现状：** 无客服工作量数据。

**改造：**
```
客服维度统计：
- 今日接单数
- 今日消息数
- 平均响应时间（首次 + 整体）
- 平均处理时长
- 满意度评分
- 在线时长
```

**涉及文件：**
- 后端：`analytics_service.py` 客服统计
- 后端：API `GET /api/analytics/agents`
- 前端：Dashboard 新增客服看板

---

## 5. 数据模型设计

### 5.1 扩展表结构

#### conversations 表扩展

```sql
-- 现有字段保持不变，新增：
ALTER TABLE conversations ADD COLUMN priority INTEGER NOT NULL DEFAULT 0;
  -- 优先级：0=普通, 1=高, 2=紧急
ALTER TABLE conversations ADD COLUMN tags TEXT DEFAULT '[]';
  -- 标签 JSON 数组：["咨询","退换货"]
ALTER TABLE conversations ADD COLUMN closed_at INTEGER DEFAULT 0;
  -- 关闭时间戳
ALTER TABLE conversations ADD COLUMN assigned_agent_id TEXT DEFAULT '';
  -- 自动分配的客服 ID（区别于当前的 agent_id，agent_id 是实际接管人）
```

#### handoff_requests 表扩展

```sql
ALTER TABLE handoff_requests ADD COLUMN reason TEXT DEFAULT '';
  -- 转接原因：user_initiated / system_suggested / auto_escalation
ALTER TABLE handoff_requests ADD COLUMN priority INTEGER DEFAULT 0;
ALTER TABLE handoff_requests ADD COLUMN queue_position INTEGER DEFAULT 0;
ALTER TABLE handoff_requests ADD COLUMN estimated_wait_seconds INTEGER DEFAULT 0;
```

#### 新增表：agent_status

```sql
CREATE TABLE IF NOT EXISTS agent_status (
    user_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'offline'
        CHECK(status IN ('online', 'busy', 'away', 'offline')),
    current_load INTEGER DEFAULT 0,
    max_load INTEGER DEFAULT 5,
    last_heartbeat INTEGER DEFAULT 0,
    skills TEXT DEFAULT '[]',
    auto_accept BOOLEAN DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_status_project ON agent_status(project_id);
CREATE INDEX IF NOT EXISTS idx_agent_status_status ON agent_status(status);
```

#### 新增表：canned_responses

```sql
CREATE TABLE IF NOT EXISTS canned_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    category TEXT DEFAULT '',
    shortcut TEXT DEFAULT '',
    is_global BOOLEAN DEFAULT 0,  -- 0=个人, 1=项目级
    sort_order INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_canned_project ON canned_responses(project_id);
CREATE INDEX IF NOT EXISTS idx_canned_user ON canned_responses(user_id);
```

#### 新增表：csat_ratings

```sql
CREATE TABLE IF NOT EXISTS csat_ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    agent_id TEXT DEFAULT '',
    rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
    tags TEXT DEFAULT '[]',
    feedback TEXT DEFAULT '',
    created_at INTEGER NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);
CREATE INDEX IF NOT EXISTS idx_csat_project ON csat_ratings(project_id);
CREATE INDEX IF NOT EXISTS idx_csat_agent ON csat_ratings(agent_id);
```

#### 新增表：conversation_tags

```sql
CREATE TABLE IF NOT EXISTS conversation_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    tag TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);
CREATE INDEX IF NOT EXISTS idx_conv_tags_conv ON conversation_tags(conversation_id);
CREATE INDEX IF NOT EXISTS idx_conv_tags_tag ON conversation_tags(tag);
```

### 5.2 消息模型扩展

```python
class Message:
    """扩展字段"""
    message_type: str  # 'text' | 'image' | 'card' | 'system'
    payload: dict = {}  # 扩展负载（图片URL、卡片数据等）
    client_msg_id: str  # 客户端消息ID（用于去重）
    edited_at: int = 0  # 编辑时间
```

---

## 6. API 设计

### 6.1 新增端点

#### 用户主动转接

```http
POST /api/chat/handoff
X-API-Key: <api_key>
Content-Type: application/json

{
    "conversation_id": "conv_abc123",
    "reason": "user_initiated",        // user_initiated | system_suggested
    "message": "我需要人工帮助",          // 可选，转接原因
    "contact_email": "user@example.com", // 可选
    "contact_phone": "13800138000"       // 可选
}

Response 200:
{
    "success": true,
    "queue_position": 3,
    "estimated_wait_seconds": 120,
    "status": "queuing"
}
```

#### 取消排队

```http
POST /api/chat/handoff/cancel
X-API-Key: <api_key>

{
    "conversation_id": "conv_abc123"
}

Response 200:
{
    "success": true,
    "status": "active"
}
```

#### 客服在线状态

```http
// 更新状态
PUT /api/agent/status
Authorization: Bearer <token>

{
    "status": "online"  // online | busy | away | offline
}

// 获取项目客服状态
GET /api/projects/{project_id}/agent/status?page=1&page_size=20

Response:
{
    "items": [
        {
            "user_id": "u_xxx",
            "name": "客服小明",
            "status": "online",
            "current_load": 2,
            "max_load": 5,
            "last_heartbeat": 1690000000
        }
    ],
    "total_online": 3,
    "total": 5
}
```

#### 转接会话

```http
POST /api/projects/{project_id}/conversations/{conversation_id}/transfer
Authorization: Bearer <token>

{
    "target_agent_id": "u_yyy",
    "reason": "专业技能组转接",
    "note": "用户询问物流问题，我这边不熟悉"
}

Response 200:
{
    "success": true,
    "status": "agent",
    "agent_id": "u_yyy"
}
```

#### 话术库

```http
// 列表
GET /api/projects/{project_id}/canned-responses?category=greeting&page=1&page_size=50

// 创建
POST /api/projects/{project_id}/canned-responses
{
    "title": "欢迎语",
    "content": "您好，欢迎咨询！请问有什么可以帮您？",
    "category": "greeting",
    "shortcut": "/hi",
    "is_global": true
}

// 更新
PUT /api/projects/{project_id}/canned-responses/{id}

// 删除
DELETE /api/projects/{project_id}/canned-responses/{id}
```

#### CSAT 评价

```http
POST /api/feedback/csat
X-API-Key: <api_key>

{
    "conversation_id": "conv_abc123",
    "rating": 5,
    "tags": ["解决了我问题", "回复很快"],
    "feedback": "客服非常专业，点赞"
}

Response 200:
{
    "success": true,
    "message": "感谢您的评价！"
}
```

#### 会话标签

```http
// 添加标签
POST /api/projects/{project_id}/conversations/{cid}/tags
{
    "tag": "投诉"
}

// 移除标签
DELETE /api/projects/{project_id}/conversations/{cid}/tags?tag=投诉

// 获取标签列表
GET /api/projects/{project_id}/tags
```

### 6.2 WebSocket 端点

```
ws://host/ws?token=<jwt_token>

消息格式（JSON）：
{
    "type": "message.new",
    "data": {
        "conversation_id": "conv_abc123",
        "message": {
            "id": 123,
            "role": "agent",
            "content": "您好，有什么可以帮您？",
            "created_at": 1690000000
        }
    }
}
```

**事件类型：**

| 方向 | type | 说明 | 发送方 |
|------|------|------|--------|
| → | `subscribe.conversation` | 订阅会话消息 | 客户端 |
| → | `subscribe.project` | 订阅项目事件（新转接等） | 客户端 |
| → | `message.send` | 发送消息 | 客户端 |
| → | `message.typing` | 输入中 | 客户端 |
| → | `message.read` | 已读回执 | 客户端 |
| → | `heartbeat` | 心跳 | 客户端 |
| ← | `message.new` | 新消息 | 服务端 |
| ← | `message.typing` | 对方输入中 | 服务端 |
| ← | `message.read` | 对方已读 | 服务端 |
| ← | `conversation.status` | 会话状态变更 | 服务端 |
| ← | `handoff.new` | 新转接请求 | 服务端 |
| ← | `agent.status` | 客服状态变更 | 服务端 |
| ← | `error` | 错误信息 | 服务端 |

---

## 7. 前端组件设计

### 7.1 Widget 扩展

```
widget/
├── components/
│   ├── ChatWidget.vue          # 主容器（重构，支持多模式）
│   ├── ChatModeAI.vue          # AI 对话模式（现有）
│   ├── ChatModeHandoff.vue     # 主动转接入口（新增）
│   ├── ChatModeQueue.vue       # 排队等待界面（新增）
│   │   ├── QueuePosition.vue   # 排队位置
│   │   ├── EstimatedWait.vue   # 预计等待
│   │   ├── FaqSuggestions.vue  # 等待中FAQ推荐
│   │   └── CancelQueue.vue     # 取消排队
│   ├── ChatModeAgent.vue       # 人工对话模式（重构，WebSocket替代轮询）
│   │   ├── TypingIndicator.vue # 正在输入
│   │   └── RichMessage.vue     # 富文本消息
│   └── CsatDialog.vue          # 满意度评价（新增）
```

### 7.2 AgentChat 重构

```
AgentChat.vue （重构，新增模块）
├── Sidebar（左侧面板）
│   ├── PendingQueue.vue        # 待接单（WebSocket 实时更新）
│   ├── ActiveConversations.vue # 进行中
│   ├── ResolvedList.vue        # 已处理历史
│   └── AgentStatusBar.vue      # 客服状态切换（新增）
├── ChatPanel（主面板）
│   ├── Header.vue
│   │   ├── CustomerInfo.vue    # 客户信息侧栏（新增）
│   │   ├── TransferButton.vue  # 转接按钮（新增）
│   │   └── TagsManager.vue     # 标签管理（新增）
│   ├── MessageList.vue
│   │   └── RichMessage.vue     # 富文本渲染
│   └── InputArea.vue
│       ├── RichTextEditor.vue  # 富文本编辑器（新增）
│       └── CannedResponsePanel.vue # 话术面板（新增）
└── InternalNotes.vue           # 内部笔记侧栏（新增）
```

### 7.3 新增页面

```
views/
├── agent/
│   └── AgentChat.vue           # 重构
├── analytics/
│   └── AgentPerformance.vue    # 客服绩效统计（新增）
└── settings/
    └── CannedResponses.vue     # 话术库管理（新增）
```

---

## 8. WebSocket 实时通信方案

### 8.1 架构设计

```
┌──────────┐     ┌──────────────┐     ┌──────────┐
│  Client   │◄───►│  WS Manager  │◄───►│  Redis    │  ← 多实例广播
│  (Widget) │     │  (FastAPI)   │     │  Pub/Sub  │
└──────────┘     └──────┬───────┘     └──────────┘
                        │
┌──────────┐     ┌──────┴───────┐     ┌──────────┐
│  Client   │◄───►│  WS Manager  │◄───►│  Redis    │
│  (Admin)  │     │  (FastAPI)   │     │  Pub/Sub  │
└──────────┘     └──────────────┘     └──────────┘
```

### 8.2 连接管理（Python）

```python
# src/services/ws_manager.py

class ConnectionManager:
    """WebSocket 连接管理器。"""

    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self._connections: Dict[str, Set[WebSocket]] = defaultdict(set)
        # key: user_id, value: set of WS connections
        self._user_channels: Dict[str, Set[str]] = defaultdict(set)
        # key: user_id, value: set of subscribed channel IDs
        self._redis = None  # aioredis client for multi-instance

    async def connect(self, ws: WebSocket, user_id: str):
        await ws.accept()
        self._connections[user_id].add(ws)

    async def disconnect(self, ws: WebSocket, user_id: str):
        self._connections[user_id].discard(ws)
        if not self._connections[user_id]:
            del self._connections[user_id]

    async def send_to_user(self, user_id: str, event: dict):
        """向用户的所有连接发送消息。"""
        for ws in self._connections.get(user_id, set()):
            try:
                await ws.send_json(event)
            except WebSocketDisconnect:
                await self.disconnect(ws, user_id)

    async def broadcast_to_project(self, project_id: str, event: dict, exclude_user: str = ""):
        """向项目的所有在线客服广播。"""
        agent_ids = self._get_project_agents(project_id)
        for uid in agent_ids:
            if uid != exclude_user:
                await self.send_to_user(uid, event)
```

### 8.3 心跳与重连机制

```typescript
// Client 端 useWebSocket.ts

const HEARTBEAT_INTERVAL = 30_000    // 30s 心跳
const RECONNECT_BASE_DELAY = 1_000   // 初始重连延迟 1s
const RECONNECT_MAX_DELAY = 30_000   // 最大重连延迟 30s

function useWebSocket(url: string, token: string) {
    let ws: WebSocket | null = null
    let reconnectDelay = RECONNECT_BASE_DELAY
    let heartbeatTimer: number | null = null

    function connect() {
        ws = new WebSocket(`${url}?token=${token}`)

        ws.onopen = () => {
            reconnectDelay = RECONNECT_BASE_DELAY  // 重置重连延迟
            startHeartbeat()
        }

        ws.onclose = () => {
            stopHeartbeat()
            // 指数退避重连
            setTimeout(connect, reconnectDelay)
            reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_DELAY)
        }

        ws.onmessage = (event) => {
            const msg = JSON.parse(event.data)
            handleEvent(msg)
        }
    }

    function startHeartbeat() {
        heartbeatTimer = setInterval(() => {
            ws?.send(JSON.stringify({ type: 'heartbeat' }))
        }, HEARTBEAT_INTERVAL)
    }

    return { connect, send: (data) => ws?.send(JSON.stringify(data)), close }
}
```

---

## 9. 迁移路径

### 阶段一：基础改造（优先级 P0，预计 1-2 周）

| 步骤 | 内容 | 依赖 |
|------|------|------|
| 1.1 | Widget 增加"转人工"按钮 + 用户主动发起转接 | 无 |
| 1.2 | 后端新增 `POST /api/chat/handoff` + `cancel` 端点 | 1.1 |
| 1.3 | 实现多维 Handoff 判定引擎（替换阈值逻辑） | 无 |
| 1.4 | 实现 WebSocket 基础架构（ConnectionManager + 路由） | 无 |
| 1.5 | AgentChat 改为 WebSocket 接收消息（保留轮询为降级方案） | 1.4 |
| 1.6 | Widget 改为 WebSocket 通信（降级到轮询） | 1.4 |

### 阶段二：客服工作台增强（优先级 P1，预计 1-2 周）

| 步骤 | 内容 | 依赖 |
|------|------|------|
| 2.1 | 客服在线状态（status + heartbeat） | 1.4 |
| 2.2 | 排队等待机制（队列 + 预计等待时间 + FAQ推荐） | 1.2 |
| 2.3 | 智能分配引擎（轮询分配 + 最少繁忙） | 2.1 |
| 2.4 | 正在输入提示（typing indicator） | 1.4 |
| 2.5 | 话术库 CRUD 及快捷插入 | 无 |
| 2.6 | 客服间转接功能 | 1.4 |

### 阶段三：体验优化（优先级 P2，预计 1 周）

| 步骤 | 内容 | 依赖 |
|------|------|------|
| 3.1 | 富文本消息（Markdown + 图片 + 商品卡片） | 1.4 |
| 3.2 | CSAT 满意度评价 | 无 |
| 3.3 | 会话标签管理 | 无 |
| 3.4 | 自动回复/离开消息 | 2.1 |
| 3.5 | 客服工作台统计 | 2.1 |

---

## 10. 附录：行业标准对照表

| 功能 | OpenAsk 当前 | 行业标准 | 差距 | 建议优先级 |
|------|-------------|---------|------|-----------|
| 用户主动转接 | ❌ 无 | Intercom/LiveChat 均有"转人工"按钮 | 严重缺失 | **P0** |
| 实时通信 | HTTP 轮询 2s/5s | WebSocket 或 SSE | 严重不足 | **P0** |
| 转接判定 | 检索分数阈值 | 多维度综合判定 | 简单 | **P0** |
| 客服在线状态 | ❌ 无 | 在线/忙碌/离开/离线 | 缺失 | P1 |
| 智能分配 | ❌ 手动抢单 | 轮询/技能组/最少繁忙 | 缺失 | P1 |
| 正在输入 | ❌ 无 | 双方可见 | 缺失 | P1 |
| 排队等待 | ❌ 无 | 排队位置+预计时间+FAQ推荐 | 缺失 | P1 |
| 话术库 | ❌ 无 | 预置+自定义+快捷插入 | 缺失 | P1 |
| 客服间转接 | ❌ 无 | 支持附带备注 | 缺失 | P1 |
| 富文本消息 | ❌ 纯文本 | Markdown/图片/卡片/链接 | 缺失 | P2 |
| 满意度评价 | ❌ 无 | 1-5星+标签+反馈 | 缺失 | P2 |
| 会话标签 | ❌ 无 | 预置+自定义+筛选 | 缺失 | P2 |
| 自动回复 | ❌ 无 | 离线/忙碌自动回复 | 缺失 | P2 |
| 客服绩效统计 | ❌ 无 | 接单量/响应时间/满意度 | 缺失 | P2 |
| 内部笔记 | ❌ 无 | 客服可见的私人笔记 | 缺失 | 后续 |
| 会话存档 | ✅ 有 | 完整历史可追溯 | 满足 | - |
| 角色分离 | ✅ user/assistant/agent | 更细粒度 | 基础满足 | - |
| 鉴权体系 | ✅ JWT + API Key | 标准方案 | 满足 | - |

---

## 快速参考：关键文件变更清单

```
openAsk/
├── src/
│   ├── api/
│   │   ├── routes.py              # 新增 handoff 端点
│   │   ├── conversations.py       # 扩展 transfer 端点
│   │   ├── ws.py                  # [新增] WebSocket 路由
│   │   └── schemas.py             # 扩展消息模型
│   ├── services/
│   │   ├── ws_manager.py          # [新增] WebSocket 连接管理
│   │   ├── handoff_judge.py       # [新增] 多维转接判定引擎
│   │   ├── agent_service.py       # [新增] 客服状态/分配/排队
│   │   ├── canned_response_service.py  # [新增] 话术库
│   │   ├── conversation_service.py     # 扩展标签/优先级
│   │   └── analytics_service.py   # 扩展 CSAT/客服统计
│   ├── core/
│   │   └── retriever.py           # 替换 _check_handoff_needed
│   └── domain/
│       ├── conversation.py        # 扩展字段
│       └── canned_response.py     # [新增]

admin-panel/
├── src/
│   ├── views/
│   │   └── agent/
│   │       └── AgentChat.vue       # 重构（WebSocket + 话术+转接+标签）
│   ├── components/
│   │   └── agent/                  # [新增] 拆分组件
│   ├── composables/
│   │   └── useWebSocket.ts        # [新增] WebSocket Hook
│   ├── api/
│   │   └── chat.ts                # 扩展 API
│   └── styles/
│       └── agent-chat.scss        # 重构样式

widget/ (假设独立于 admin-panel 的嵌入脚本)
├── ChatWidget.vue                 # 重构（多模式 + WebSocket + CSAT）
```

---

> **文档维护者：** Claude Code (Fable 5)
> **最后更新：** 2026-08-10
> **基于代码分析：** `openAsk` v2.1.1 + `admin-panel` v2.1.1