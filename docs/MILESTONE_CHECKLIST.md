# OpenAsk 多租户上线里程碑清单

> 创建日期：2026-07-31
> 最后更新：2026-08-01
> 项目状态：多租户改造已完成，后端运行中（29 租户），前端已部署至 http://34.21.191.70:5173

---

## P0 — 上线前必须完成（安全 / 功能）

> 🔴 未完成前不得上线

### ☑️ 1. 轮换 Admin Key ✅

- [x] 生成新的 Admin API Key（`sk_` 前缀 + 64 位随机 hex）
- [x] 更新后端 `.env` 中 `API_KEY` 为新生成的值
- [x] 更新 `DEFAULT_TENANT_API_KEY`（默认租户 key 已同步）
- [x] 重启 openAsk 容器使配置生效
- [x] 验证：用新 key 调 `/api/admin/tenants` 返回 200
- [x] 验证：用旧 key 调 `/api/admin/tenants` 返回 401
- [x] **删除本地所有包含旧 key 的文档/笔记/日志**
- [x] `.env` 权限加固为 600

### ☑️ 2. 提交后端 Dirty 文件 ✅

- [x] 检查 12 个修改文件的改动是否正确：
  - `src/api/main.py` — 生命周期 + 多租户初始化
  - `src/api/routes.py` — resolve_tenant + admin_router
  - `src/api/schemas.py` — 租户响应模型
  - `src/core/retriever.py` — 租户级检索
  - `src/domain/exceptions.py` — 租户异常
  - `src/domain/models.py` — Tenant 模型
  - `src/infrastructure/llm_response_cache.py` — 租户缓存
  - `src/infrastructure/zvec_store.py` — 租户 filter
  - `src/services/knowledge_service.py` — 租户 CRUD
  - `src/services/sensenova_client.py` — 租户 LLM
  - `src/utils/config.py` — 租户配置
  - `src/utils/limiter.py` — 租户限流
- [x] 恢复或删除被误删的 `tests/test_knowledge_service.py`
- [x] 提交 commit 并推送到远端

### ☑️ 3. 补多租户端到端测试 ✅

- [x] 编写 `tests/test_tenant_e2e.py` 覆盖以下场景：
  - [x] 创建租户 → 验证返回 TenantResponse
  - [x] 用租户 key 调 `/api/chat` → 返回该租户知识库答案
  - [x] 用租户 A 的 key 访问租户 B 的文档 → 返回空结果（隔离验证）
  - [x] 租户 key 过期/禁用 → 返回 401
  - [x] 用 admin key 创建/更新/删除租户
  - [x] 轮换 key 后旧 key 立即失效
  - [x] 软删除租户后不返回在活跃列表中
- [x] 全部测试通过（28 passed + 17/17 手工 E2E）

### ☑️ 4. Admin API 加 Rate Limit ✅

- [x] 为 `/api/admin/tenants/*` 端点加限流（`5/minute`）
- [x] 验证：连续请求超限返回 429

---

## P1 — 功能完善（上线后 1 周内）

> 🟡 不影响现有功能但必须尽快补

### ☑️ 5. 租户统计数据接入真实数据 ✅

- [x] 改造 `TenantService.get_document_count()` — 接入 Zvec 查询
- [x] 改造 `TenantStats.total_calls/prompt_tokens/completion_tokens/cache_hit_rate` — 接入 `TenantStatsRegistry` 实际调用计数
- [x] 前端统计弹窗验证数据正确
- [x] `tests/test_tenant_e2e.py::test_stats_endpoint_returns_real_data` 通过

### ☑️ 6. 租户删除语义明确 ✅

- [x] **采用方案二**：保留软删除（`status='deleted'`），list 接口默认不加 deleted 租户，加 `?include_deleted=true` 参数可选
- [x] 删除租户时不删除知识库数据，仅标记 status
- [x] 前端更新：删除操作加二次确认弹窗（显示将被删除的文档数）

### ☑️ 7. 前端租户切换加确认弹窗 ✅

- [x] 点击下拉切换租户时弹出 `ElMessageBox.confirm` 确认框
- [x] 显示即将切换到的租户名称和当前操作的后果提示
- [x] 确认后才切换

### ☑️ 8. 前端 Admin Key 保存后验证 ✅

- [x] `SettingsView` 中保存 Admin Key 后，自动调 `/api/admin/tenants` 验证 key 有效性
- [x] 验证失败弹 Toast 提示 "Admin Key 无效，请检查"
- [x] 验证成功自动刷新租户列表

---

## P2 — 生产就绪（上线前建议完成）

> 🟢 提升稳定性和可运维性

### □ 9. 部署 HTTPS

- [ ] 接入 Cloudflare Tunnel 或 Caddy 自动 HTTPS
- [ ] 域名绑定（建议 `admin.openask.example.com`）
- [ ] 配置 HSTS Header

### ☑️ 10. Docker Compose 统一编排 ✅

- [x] `docker-compose.yml` 编排 openAsk + admin-panel
- [x] 统一网络、端口、环境变量管理
- [x] 支持 `docker-compose up -d` 一键启动
- [x] 健康检查：`/api/health` 30s 间隔探测

### □ 11. 错误提示区分场景

- [ ] 租户 key 失效 → "当前租户 API Key 已过期，请刷新或重新选择租户"
- [ ] Admin key 失效 → "Admin API Key 无效，请在系统配置中重新设置"
- [ ] 租户未找到 → "该租户不存在或已被删除"

### □ 12. 审计日志

- [ ] 记录租户 CRUD 操作（操作人、时间、变更内容）
- [ ] 记录 key 轮换事件
- [ ] 提供日志查看 API（可选）

### □ 13. 数据备份策略

- [ ] 编写 `data/tenants.db` 备份脚本
- [ ] 编写 Zvec 向量库备份说明
- [ ] 文档化数据迁移流程

### □ 14. CORS 生产配置

- [ ] 更新 `.env` 中 `CORS_ORIGINS` 为实际域名
- [ ] 移除 `*` 通配（当前为 `http://localhost:3000,http://localhost:8000`）

### □ 15. 前端 Admin Key 安全增强

- [ ] 评估是否迁移到 `sessionStorage`（页面关闭即过期）
- [ ] 或改为后端 token 模式（登录获取 token）

### □ 16. 前端 E2E 测试

- [ ] 编写 Playwright/Cypress 用例：
  - [ ] 租户选择页加载 + 切换
  - [ ] 租户创建流程
  - [ ] 知识库上传 + 问答
  - [ ] 租户管理增删改查

### □ 17. 健康检查增强

- [ ] `/api/health` 增加租户维度（可选参数 `?tenant_id=xxx`）
- [ ] 容器编排中增加 admin-panel 健康检查

### □ 18. 监控与告警

- [ ] 接入 Prometheus / Grafana（LLM 调用量、延迟、错误率）
- [ ] 配置异常告警（LLM 调用失败率 > 5%）

---

## 里程碑汇总

| 里程碑 | 阶段 | 预计工作量 | 状态 |
|---|---|---|---|
| M0: 安全加固 | P0 | 2-3h | ✅ 已完成 |
| M1: 代码落地 | P0 | 1-2h | ✅ 已完成 |
| M2: 测试覆盖 | P0 | 3-5h | ✅ 已完成 |
| M3: 功能完善 | P1 | 4-8h | ✅ 已完成 |
| M4: 生产就绪 | P2 | 8-16h | ⬜ 进行中（Docker 编排已完成，其余待办） |
| **上线** | — | — | 🟢 P0/P1 已全部通过，可上线；P2 建议后续持续完善 |

---

*文档版本：v2.0 · 2026-08-01*
