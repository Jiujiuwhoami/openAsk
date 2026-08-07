# OpenAsk API 示例

> 位置：`examples/` — 包含 curl、Python、JavaScript 三种语言的代码示例

## 目录

```
examples/
├── README.md                # 本文件
├── curl/
│   └── README.md           # curl 命令示例（完整 API 参考）
├── python/
│   ├── chat.py             # 问答示例（普通 + 流式）
│   └── knowledge.py        # 知识库管理示例
├── javascript/
│   └── chat.js             # JavaScript 问答示例
└── embed/
    └── demo.html           # 嵌入脚本演示页面
```

## 快速开始

### 1. 获取 API Key

在项目设置页获取你的 API Key，或通过注册 API 获取。

### 2. 替换变量

所有示例中的 `YOUR_API_KEY` 替换为实际的 API Key。

### 3. 运行示例

```bash
# curl（直接复制粘贴）
curl -X POST http://localhost:8000/api/chat \
  -H "X-API-Key: sk_your_key" \
  -H "Content-Type: application/json" \
  -d '{"query": "退货政策是什么？"}'

# Python
cd examples/python
pip install requests
python chat.py

# JavaScript
cd examples/javascript
node chat.js
```

## API 文档

完整的 API 文档（含 Swagger UI）访问：
- 自托管: http://localhost:8000/docs
- 云托管: https://api.openask.dev/docs

## 认证方式

| 场景 | 认证方式 | 适用 API |
|------|----------|----------|
| 管理面板 | `Authorization: Bearer <JWT>` | projects, logs, analytics, billing |
| 嵌入脚本 | `X-API-Key: <api_key>` | chat, knowledge, search |
| 注册登录 | 无（或表单） | auth/register, auth/token |