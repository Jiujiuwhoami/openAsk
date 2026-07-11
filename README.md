<!-- openAsk-readme:start -->
# OpenAsk - 基于知识库的智能问答系统

OpenAsk 是一个基于 **Zvec（阿里巴巴开源嵌入式向量数据库）** 和 **SenseNova API** 构建的开源智能问答系统。上传文档建立知识库，即可通过自然语言提问，获得基于知识库的精准回答。

> 📄 **技术论文**：[OpenAsk：基于向量检索增强生成（RAG）的开源智能问答系统](https://fromzero.trade) — 详细阐述系统架构、核心算法与性能评估

## 功能特性

- **语义检索**：支持自然语言提问，精准匹配知识库
- **智能回答**：基于检索结果生成高质量回复
- **低延迟**：毫秒级响应，提升用户体验
- **可扩展**：支持多语言、多场景的快速扩展

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 语言 | Python | 3.11+ |
| 框架 | FastAPI | API 接口框架 |
| 向量数据库 | Zvec | 阿里巴巴开源嵌入式向量数据库 |
| 大语言模型 | SenseNova API | 语义理解与回答生成 |
| 向量模型 | Sentence-BERT | 文本向量化 |

## 快速开始

### 环境要求

- Python 3.11+
- 能访问 SenseNova API
- Docker（可选，使用容器部署）

### 一键启动（推荐） 🐳

最快的方式，无需安装 Python 和依赖：

```bash
# 1. 克隆项目
git clone https://github.com/Jiujiuwhoami/openAsk.git
cd openAsk

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 SENSE_NOVA_API_KEY

# 3. 一键启动
docker compose up -d

# 首次启动约 1-2 分钟（需下载模型），完成后：
curl http://localhost:8000/api/health
```

首次启动会自动下载两个模型：
- **Sentence-BERT**（`all-MiniLM-L6-v2`）— 文本向量化
- **BGE-Reranker**（`BAAI/bge-reranker-v2-m3`）— 重排序精排

下载完成后存入 HuggingFace 缓存，后续启动秒开。

### 手动部署（Windows 开发环境）

> 本项目 Windows 开发环境约定直接使用系统 Python，不创建虚拟环境。

#### 1. 克隆项目

```powershell
git clone https://github.com/Jiujiuwhoami/openAsk.git
cd OpenAsk
```

#### 2. 安装依赖

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

#### 3. 配置环境变量

```powershell
copy .env.example .env
# 编辑 .env，填入 SENSE_NOVA_API_KEY 等配置
```

#### 4. 初始化知识库（导入 FAQ 文档）

```powershell
python -c "from src.services.knowledge_service import KnowledgeService; svc = KnowledgeService(); svc.load_faq_documents(); print(f'导入完成，共 {svc.count()} 条文档')"
```

#### 5. 启动服务

```powershell
uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --reload
```

#### 6. 验证服务

**健康检查：**

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/health
```

**测试问答：**

```powershell
$body = @{ query = "退货政策是什么？"; top_k = 3 } | ConvertTo-Json
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/chat -Method Post -Body $body -ContentType "application/json"
```

### Linux / macOS 开发环境（虚拟环境）

#### 1. 克隆项目

```bash
git clone https://github.com/Jiujiuwhoami/openAsk.git
cd OpenAsk
```

#### 2. 创建虚拟环境

```bash
python3.11 -m venv venv
source venv/bin/activate  # Linux/Mac
```

#### 3. 安装依赖

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

#### 4. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入 SENSE_NOVA_API_KEY 等配置
```

#### 5. 初始化知识库

```bash
python -c "from src.services.knowledge_service import KnowledgeService; svc = KnowledgeService(); svc.load_faq_documents(); print(f'导入完成，共 {svc.count()} 条文档')"
```

#### 6. 启动服务

```bash
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```

#### 7. 验证服务

```bash
# 健康检查
curl http://localhost:8000/api/health

# 测试问答
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "退货政策是什么？", "top_k": 3}'
```

## 项目结构

```
OpenAsk/
├── docs/                    # 文档目录
│   ├── claude.md           # AI 行为约束
│   ├── project.md          # 架构概览
│   ├── development.md      # 代码规范
│   └── deploy.md           # 运维备份
├── data/                   # 数据目录
│   ├── knowledge_base.md   # 知识点索引
│   ├── documents/          # 原始文档存储
│   └── zvec/               # Zvec 数据库文件
├── src/                    # 源代码目录
│   ├── domain/             # 领域模型（Document, SearchResult）
│   ├── infrastructure/     # 基础设施层（VectorStore, MetadataStore, EmbeddingService）
│   ├── services/           # 服务层（KnowledgeService, DocumentLoader, Splitter）
│   ├── core/               # 核心编排层（Retriever）
│   ├── api/                # API 接口
│   └── utils/              # 工具函数
├── tests/                  # 测试目录
├── .env                    # 环境变量
├── .gitignore              # Git 忽略规则
├── requirements.txt        # 依赖清单
└── README.md               # 项目说明
```

## API 接口

> 完整的请求/响应 Schema 见 [src/api/schemas.py](src/api/schemas.py)。若 `.env` 中配置了 `API_KEY`，除健康检查外所有接口需在请求头携带 `X-API-Key`。

### 问答接口

```
POST /api/chat
请求体: { "query": "用户问题", "top_k": 5 }
响应:   { "answer": "...", "sources": [...], "cache_hit": false, "llm_used": true }
```

### 流式问答接口（SSE）

```
POST /api/chat/stream
请求体: { "query": "用户问题", "top_k": 5 }
响应:   text/event-stream，逐事件返回：
        - {"event": "sources",     "data": [...]}      # 来源文档
        - {"event": "cache_hit",   "data": true}        # 是否命中缓存
        - {"event": "answer_delta","data": "文本增量"}  # 回答增量
        - {"event": "done",        "data": {"reranked": true}}
        - {"event": "error",       "data": "错误信息"}
```

### 检索接口

```
POST /api/search         # 单次检索
请求体: { "query": "用户问题", "top_k": 5 }
响应:   [{ "doc_id": "...", "title": "...", "content": "...", "score": 0.95 }]

POST /api/search/batch   # 批量检索（一次提交多个 query）
请求体: { "queries": ["问题1", "问题2"], "top_k": 5 }
响应:   [{ "query_index": 0, "query": "问题1", "results": [...] }, ...]
```

### 知识库管理 (RESTful)

```
GET    /api/knowledge?page=1&page_size=20   # 知识点列表（分页）
POST   /api/knowledge                        # 添加知识点（JSON）
POST   /api/knowledge/upload                 # 上传文档（multipart，支持 .md/.txt/.pdf/.docx/.html）
GET    /api/knowledge/{doc_id}               # 获取单个知识点
PUT    /api/knowledge/{doc_id}               # 更新知识点
DELETE /api/knowledge/{doc_id}               # 删除知识点
```

### 健康检查

```
GET /api/health
响应: { "status": "healthy", "version": "1.0.0", "timestamp": "...",
        "zvec_status": "healthy", "embedding_status": "healthy",
        "llm_status": "healthy", "cache_status": "healthy",
        "document_count": 0 }
```

## 文档

详细文档请查看 `docs/` 目录：

- [claude.md](docs/claude.md) - AI 行为约束和系统指令
- [project.md](docs/project.md) - 项目架构和技术栈
- [development.md](docs/development.md) - 开发规范
- [deploy.md](docs/deploy.md) - 部署与维护手册

## 贡献

欢迎提交 Issue 和 Pull Request！

## 许可证

MIT License