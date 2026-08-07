# OpenAsk API - curl 示例

> 前置条件：替换以下变量为实际值
> - `YOUR_API_KEY`: 项目设置页获取的 API Key
> - `YOUR_TOKEN`: 登录后获取的 JWT Token
> - `BASE_URL`: API 地址（自托管: http://localhost:8000，云托管: https://api.openask.dev）

---

## 认证

### 注册

```bash
curl -X POST ${BASE_URL}/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "password": "your-password",
    "name": "用户名"
  }'
```

返回: `access_token`, `user`, `project`（含 API Key）

### 登录（OAuth2 标准）

```bash
curl -X POST ${BASE_URL}/api/auth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d 'username=user@example.com&password=your-password'
```

返回: `access_token`（JWT Bearer Token）

---

## 项目管理

### 列出项目

```bash
curl ${BASE_URL}/api/projects \
  -H "Authorization: Bearer ${YOUR_TOKEN}"
```

### 创建项目

```bash
curl -X POST ${BASE_URL}/api/projects \
  -H "Authorization: Bearer ${YOUR_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"name": "我的知识库"}'
```

### 获取项目详情

```bash
curl ${BASE_URL}/api/projects/PROJECT_ID \
  -H "Authorization: Bearer ${YOUR_TOKEN}"
```

### 删除项目

```bash
curl -X DELETE ${BASE_URL}/api/projects/PROJECT_ID \
  -H "Authorization: Bearer ${YOUR_TOKEN}"
```

---

## 知识库管理

### 创建文档

```bash
curl -X POST ${BASE_URL}/api/knowledge \
  -H "X-API-Key: ${YOUR_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "退货政策",
    "content": "本店支持7天无理由退货...",
    "tags": ["退货", "政策"]
  }'
```

### 上传文档

```bash
curl -X POST ${BASE_URL}/api/knowledge/upload \
  -H "X-API-Key: ${YOUR_API_KEY}" \
  -F "file=@/path/to/document.pdf"
```

支持的格式: `.md`, `.txt`, `.pdf`, `.docx`, `.html`

### 列出文档

```bash
curl "${BASE_URL}/api/knowledge?page=1&page_size=10" \
  -H "X-API-Key: ${YOUR_API_KEY}"
```

### 获取文档

```bash
curl ${BASE_URL}/api/knowledge/DOC_ID \
  -H "X-API-Key: ${YOUR_API_KEY}"
```

### 更新文档

```bash
curl -X PUT ${BASE_URL}/api/knowledge/DOC_ID \
  -H "X-API-Key: ${YOUR_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"title": "新标题", "content": "新内容"}'
```

### 删除文档

```bash
curl -X DELETE ${BASE_URL}/api/knowledge/DOC_ID \
  -H "X-API-Key: ${YOUR_API_KEY}"
```

---

## 问答

### 普通问答

```bash
curl -X POST ${BASE_URL}/api/chat \
  -H "X-API-Key: ${YOUR_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"query": "退货政策是什么？", "top_k": 5}'
```

### 流式问答（SSE）

```bash
curl -X POST ${BASE_URL}/api/chat/stream \
  -H "X-API-Key: ${YOUR_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"query": "退货政策是什么？", "top_k": 5}'
```

### 搜索（仅检索，不生成回答）

```bash
curl -X POST ${BASE_URL}/api/search \
  -H "X-API-Key: ${YOUR_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"query": "退货政策", "top_k": 5}'
```

---

## 分析

### 问答日志

```bash
curl "${BASE_URL}/api/projects/PROJECT_ID/logs?page=1&page_size=20" \
  -H "Authorization: Bearer ${YOUR_TOKEN}"
```

### 导出日志

```bash
curl "${BASE_URL}/api/projects/PROJECT_ID/logs/export?format=csv" \
  -H "Authorization: Bearer ${YOUR_TOKEN}" \
  -o chat_logs.csv
```

### 问答量趋势

```bash
curl "${BASE_URL}/api/projects/PROJECT_ID/analytics/trends?days=30" \
  -H "Authorization: Bearer ${YOUR_TOKEN}"
```

### 提交反馈

```bash
curl -X POST ${BASE_URL}/api/projects/PROJECT_ID/feedback \
  -H "Authorization: Bearer ${YOUR_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"log_id": 123, "rating": "good"}'
```

---

## 健康检查

```bash
curl ${BASE_URL}/api/health
```