# OpenAsk 项目修复清单

> 生成日期：2026-08-07
> 评估范围：openAsk（后端）+ admin-panel（前端）
> 目标：从「功能完整」达到「可安全上线」

---

## 📊 当前状态快照

| 指标 | 状态 | 目标 |
|------|------|------|
| 前端 TypeScript 构建 | ✅ 通过（0 错误） | 通过 |
| 后端测试总数 | ✅ 454 个通过 + 3 跳过 | — |
| 后端测试覆盖率 | ✅ 72% | ≥80% |
| SaaS 功能清单完成度 | ✅ 136/156（87%） | — |
| **代码提交状态** | ⚠️ 69 个文件未提交 | 全部提交 |
| **API 层集成测试** | ⚠️ 5 个路由模块 0% 覆盖 | ≥60% |
| **数据迁移脚本** | ❌ 不存在 | 实现 |
| **CI/CD 流水线** | ❌ 不存在 | 搭建 |

---

## 🔴 P0 — 上线阻塞项（必须修复）

### 1.1 代码提交与版本控制

**问题**：openAsk 和 admin-panel 两个仓库共有 69 个文件改动未提交（含 SaaS 改造核心代码），开发成果有丢失风险。

**行动项**：
- [ ] 审查 openAsk 工作区改动（27 修改 + 20 新增 + 6 删除）
- [ ] 审查 admin-panel 工作区改动（15 修改 + 10 新增）
- [ ] 删除敏感文件：`.env`、`data/` 下的真实数据库、`app.log`
- [ ] 确认 `.gitignore` 覆盖 `.env`、`data/`、`*.log`、`venv/`、`__pycache__/`
- [ ] 提交后端：`git add -A && git commit -m "feat: SaaS 用户/项目体系改造完成"`
- [ ] 提交前端：`git add -A && git commit -m "feat: SaaS 前端改造完成"`
- [ ] 打版本标签：`git tag v2.0.0`
- [ ] 推送到远程仓库

---

### 1.2 API 层集成测试（5 个模块 0% 覆盖）

**问题**：billing、analytics、conversations、ecommerce、main 的 API 路由完全没有测试覆盖，权限隔离、参数校验、错误响应均未验证。

**行动项**：

| 模块 | 端点数 | 需验证的关键场景 |
|------|--------|-----------------|
| `src/api/billing.py` | 5 | Stripe 未配置降级、Webhook 签名验证、套餐切换取消旧订阅 |
| `src/api/analytics.py` | 10 | 项目所有权校验、日志导出格式、转接请求流 |
| `src/api/conversations.py` | 4 | 会话所有权校验、跨项目访问 404 |
| `src/api/ecommerce.py` | 4 | 文件格式校验、导入去重、模板不存在 |
| `src/api/main.py` | — | 应用启动、CORS、异常处理器、优雅关闭 |

**新增测试文件**：
- [ ] `tests/test_billing_api.py` — mock `stripe` 模块，覆盖 checkout/portal/webhook/invoices
- [ ] `tests/test_analytics_api.py` — 用 TestClient 测日志/趋势/反馈/转人工端点
- [ ] `tests/test_conversations_api.py` — 会话 CRUD + 所有权隔离
- [ ] `tests/test_ecommerce_api.py` — 商品导入 + FAQ 模板
- [ ] `tests/test_main.py` — 应用启动、CORS 头、异常处理

**通过标准**：API 层覆盖率 ≥60%，总覆盖率 ≥80%

---

### 1.3 数据迁移脚本（migrate_v1_to_v2.py）

**问题**：SAAS_MIGRATION_PLAN.md 规划的一次性迁移脚本未实现，存量租户用户无法升级到新 User/Project 体系。

**行动项**：
- [ ] 创建 `scripts/migrate_v1_to_v2.py`
- [ ] 读取旧 `tenants.db` 中所有租户记录
- [ ] 每个租户创建对应 User（email: `migrated-{tenant_id}@local`）
- [ ] 每个 User 创建 Project，复用原租户 API Key
- [ ] 旧知识库文档重新关联到新 Project
- [ ] 支持 `--dry-run` 预览模式
- [ ] 迁移前自动备份数据库
- [ ] 编写迁移验证脚本（迁移后文档数一致）

---

## 🟡 P1 — 高优先级（建议发布前完成）

### 2.1 修复 factory.py close() 异步泄漏 bug

**问题**：`src/core/factory.py:135` 的 `close()` 是同步方法，内部调用异步 `self._cache.close()` 但未 await，协程被静默丢弃。

**修复方案**：
```python
# 改前
def close(self) -> None:
    self._cache.close()  # 协程未 await！
    logger.info("RetrieverFactory 已关闭")

# 改后
async def close(self) -> None:
    await self._cache.close()
    logger.info("RetrieverFactory 已关闭")
```
- [ ] 修改 `factory.py` 的 `close()` 为 async
- [ ] 检查 main.py 中所有调用点是否 `await` 该 close
- [ ] 补充测试覆盖 async close

---

### 2.2 Redis 配置一致性

**问题**：`docker-compose.yml` 配置了 Redis 服务，但应用默认 `memory://` 存储，动态限流和 LLM 缓存未使用 Redis，多 Worker 部署时限流状态不共享。

**行动项**：
- [ ] 确认 `.env` 中 `RATE_LIMIT_STORAGE_URI` 配置为 `redis://redis:6379/0`
- [ ] 确认 `LLM_CACHE_STORAGE_URI` 配置正确
- [ ] 验证 Redis 连接失败时优雅降级到内存（现有测试已覆盖）
- [ ] 编写 Redis 存储的集成测试（可选，需 Docker）

---

### 2.3 邮件发送真实验证

**问题**：console 模式已测试，resend 模式代码存在但未真实端到端验证。邮箱验证、密码重置、用量告警、转接通知 4 条邮件链路依赖真实 SMTP/Resend。

**行动项**：
- [ ] 配置 Resend API Key（`EMAIL_RESEND_API_KEY`）
- [ ] 验证 4 条邮件链路实际送达：
  - 注册 → 邮箱验证邮件
  - 忘记密码 → 密码重置邮件
  - 用量达 80% → 告警邮件
  - 客户提交转接 → 通知邮件
- [ ] 补充 `tests/test_email_service.py` 中对 `build_handoff_email` 各字段组合的覆盖

---

### 2.4 端到端验证 Strip 支付流

**问题**：Stripe 代码完整，但真实支付流程（Checkout → Webhook → 套餐生效 → 超限拦截 → 降级）未验证。

**行动项**：
- [ ] 配置 Stripe 测试密钥 + Webhook 密钥
- [ ] 用 Stripe CLI 本地测试：
  ```
  stripe listen --forward-to localhost:8000/api/billing/stripe/webhook
  ```
- [ ] 验证以下场景：
  - Free → Pro 升级，Webhook 后套餐生效
  - Pro → Enterprise 切换，旧订阅被取消
  - 订阅取消 → 降级为 Free
  - 用量超限 → 返回 402 + 前端提示
- [ ] 验证发票列表接口返回真实数据

---

### 2.5 补充核心模块剩余测试

| 模块 | 当前覆盖 | 目标 | 行动 |
|------|---------|------|------|
| `knowledge_service.py` | 53% | ≥70% | 补 `load_directory`、`load_faq_documents` 错误路径 |
| `retriever.py` | 65% | ≥75% | 补 `retrieve_stream` 完整流式路径 |
| `zvec_store.py` | 63% | ≥70% | 补 `_build_project_filter`、`_migrate_schema` |
| `sensitive_filter.py` | 63% | ≥80% | 补词库加载、自定义词库 |
| `conversation_service.py` | 74% | ≥85% | 补会话统计、消息分页边界 |

---

## 🟢 P2 — 产品功能完善（上线后迭代）

### 3.1 前端口碑完善

- [ ] 全局管理员看板（查看所有用户/项目/用量）
- [ ] 套餐升降级界面引导（后端 proration 已支持）
- [ ] 免费试用期机制（trial plan）
- [ ] 用户头像上传
- [ ] 基础资料页（昵称修改）

### 3.2 会话与安全增强

- [ ] JWT Refresh Token 轮换机制（当前 24h 强制重新登录）
- [ ] 多设备登录管理
- [ ] 登录历史记录（IP/设备/时间）
- [ ] 登录失败次数限制（防暴力破解）

### 3.3 团队协作（SaaS 关键缺失）

- [ ] 项目成员邀请（邮箱 + 链接）
- [ ] 成员角色：Owner / Admin / Editor / Viewer
- [ ] 成员权限控制（知识库管理、设置、删除）
- [ ] 团队用量共享与独立配额

---

## 🟣 P3 — 增长功能（产品路线图）

### 4.1 电商平台集成（获客渠道）

- [ ] Shopify 插件（App Store 上架）
- [ ] 店匠 / Shoplazza 插件
- [ ] WooCommerce 插件
- [ ] 商品目录自动同步（定时任务）

### 4.2 IM 渠道集成（国内客户）

- [ ] 飞书 Bot
- [ ] 钉钉 Bot
- [ ] 企业微信 Bot
- [ ] 客服工具（Intercom / Zendesk）集成

### 4.3 多语言客服能力

- [ ] 问答语言扩展：英语/日语/韩语/法语/西班牙语
- [ ] 多语言知识库（同一文档多语言版本）
- [ ] 自动语言检测与切换

### 4.4 运营数据分析

- [ ] 客服对话仪表盘（今日咨询量、满意度趋势）
- [ ] 流失用户分析
- [ ] 关键词洞察（用户常问主题）

---

## 📋 基础设施与工程化

### 5.1 CI/CD 流水线

- [ ] 配置 GitHub Actions：
  - [ ] 后端：`python -m pytest tests/ --cov=src --cov-fail-under=80`
  - [ ] 前端：`npm run build`（vue-tsc 类型检查）
  - [ ] 覆盖率上报（Codecov 或 PR comment）
- [ ] 自动构建 Docker 镜像并推送到镜像仓库
- [ ] 配置环境变量模板（`.env.example` 补全所有新变量）

### 5.2 部署与运维

- [ ] 验证 `docker compose up -d` 一键启动
- [ ] 验证健康检查 `/api/health` 返回真实状态
- [ ] 配置日志轮转（避免 app.log 无限增长）
- [ ] SQLite 备份策略（定时 `sqlite3 .backup`）
- [ ] 配置邮件发送域名 SPF/DKIM 记录
- [ ] 配置 HTTPS（Nginx + Let's Encrypt）

### 5.3 文档同步

- [ ] 更新 FEATURE_CHECKLIST.md：缺口分析、转人工、账单管理从"未计划"改为"已实现"
- [ ] 更新 README.md：补充 SaaS 架构说明、环境变量清单
- [ ] 补全 PRODUCTION_DEPLOYMENT.md（或创建）
- [ ] 编写 API 变更日志（v1 → v2 破坏性变更说明）

---

## 🐛 已知代码缺陷汇总

| # | 文件 | 问题 | 严重度 | 状态 |
|---|------|------|--------|------|
| 1 | `src/core/factory.py:135` | `close()` 同步调用异步方法未 await | 🔴 中 | 待修复 |
| 2 | `docker-compose.yml` | Redis 服务与应用默认 memory 存储不一致 | 🟡 低 | 待确认 |
| 3 | FEATURE_CHECKLIST.md | 3 项已实现功能标记为"未计划" | 🟢 低 | 待更新 |
| 4 | `src/api/routes.py` | API 层无集成测试 | 🔴 高 | 待补充 |

---

## ✅ 当前已完成事项（无需重复）

- [x] 前端 TypeScript 构建修复（ProductImport.vue null safety）
- [x] 后端测试覆盖率 49% → 72% 提升
- [x] 新增 15 个测试文件、318 个测试
- [x] 核心服务层测试覆盖（email/review/product_import/plan/analytics/sensenova/factory/reranker/multimodal/dynamic_limiter/zvec/retriever/document_loader）
- [x] SaaS 业务代码（auth/projects/billing/analytics/conversations/ecommerce）全部实现

---

## 🎯 执行优先级建议

```
第 1 周（上线前）：
  1.1 提交所有代码 → 保护成果
  1.2 补 API 层集成测试 → 验证核心业务流
  1.3 实现数据迁移脚本 → 支持存量用户

第 2 周（上线）：
  2.1 修复 factory.py close bug
  2.2 Redis 配置对齐
  2.3 邮件真实验证
  2.4 Stripe 端到端验证
  2.5 补剩余核心模块测试

第 3-4 周（迭代）：
  P2 产品完善 + 基础设施（CI/CD、部署）
```

---

*版本：v1.0 · 2026-08-07*