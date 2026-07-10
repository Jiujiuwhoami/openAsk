# 部署与维护手册

## 一、本地 Windows 开发部署

### 1. 系统要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows 10/11 |
| Python 版本 | 3.11+（系统安装） |
| 内存 | 至少 4GB |
| 网络 | 能访问 SenseNova API |

### 2. Python 安装

#### Python 3.11

```powershell
python --version
# 输出: Python 3.11.x
```

### 3. 项目配置

#### 克隆项目

```powershell
cd d:\AgentTest
git clone <repository-url> Zvec
cd Zvec
```

#### 安装依赖

直接使用系统 Python 安装依赖：

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 4. 环境变量配置

#### 创建 .env 文件

在项目根目录创建 `.env` 文件：

```env
# SenseNova API 配置
SENSE_NOVA_API_KEY=your_api_key_here
SENSE_NOVA_API_BASE=https://api.sensenova.cn/v1

# Zvec 配置
ZVEC_DATA_PATH=data/zvec
ZVEC_DIMENSION=384

# FastAPI 配置
API_HOST=127.0.0.1
API_PORT=8000

# 日志配置
LOG_LEVEL=DEBUG
LOG_FILE=app.log

# 限流配置
RATE_LIMIT_PER_USER=60/minute
RATE_LIMIT_GLOBAL=1000/minute
RATE_LIMIT_STRATEGY=sliding_window
RATE_LIMIT_STORAGE_URI=memory://
```

#### 环境变量说明

| 变量名 | 说明 | 开发默认值 |
|--------|------|------------|
| SENSE_NOVA_API_KEY | SenseNova API 密钥 | - |
| SENSE_NOVA_API_BASE | SenseNova API 基础 URL | https://api.sensenova.cn/v1 |
| ZVEC_DATA_PATH | Zvec 数据存储路径 | data/zvec |
| ZVEC_DIMENSION | 向量维度 | 384 |
| API_HOST | API 服务绑定地址 | 127.0.0.1 |
| API_PORT | API 服务端口 | 8000 |
| LOG_LEVEL | 日志级别 | DEBUG |
| RATE_LIMIT_PER_USER | 单用户每分钟请求限制 | 60/minute |
| RATE_LIMIT_GLOBAL | 全局每分钟请求限制 | 1000/minute |
| RATE_LIMIT_STRATEGY | 限流策略（fixed_window/sliding_window）| sliding_window |
| RATE_LIMIT_STORAGE_URI | 限流存储后端 | memory:// |
| LLM_CACHE_ENABLED | 是否启用 LLM 响应缓存 | true |
| LLM_CACHE_MAXSIZE | 缓存最大条目数 | 1000 |
| LLM_CACHE_TTL | 缓存 TTL（秒） | 86400 |
| LLM_CACHE_SIMILARITY_THRESHOLD | 缓存相似度阈值 | 0.95 |
| METRICS_ENABLED | 是否启用 Prometheus 监控 | true |

### 5. 启动开发服务器

#### 使用 uvicorn 启动

```powershell
uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --reload
```

参数说明：
- `--host 127.0.0.1`：仅本机访问
- `--port 8000`：端口 8000
- `--reload`：代码修改后自动重启（开发模式）

#### 验证服务

```powershell
# 使用 curl（PowerShell 别名）测试
Invoke-WebRequest -Uri http://127.0.0.1:8000/api/health -UseBasicParsing

# 或在浏览器访问
# http://127.0.0.1:8000/api/health
```

预期响应：

```json
{
    "status": "healthy",
    "zvec_status": "connected",
    "timestamp": "2024-01-01T12:00:00Z"
}
```

### 6. 停止服务

在终端按 `Ctrl + C` 停止服务。

### 7. 数据管理

#### 初始化知识库

首次启动前，需要将 `data/documents/faq/` 下的 FAQ 文档导入 Zvec 向量数据库：

```powershell
python -c "from src.services.knowledge_service import KnowledgeService; svc = KnowledgeService(); svc.load_faq_documents(); print(f'导入完成，共 {svc.count()} 条文档')"
```

#### 查看文档数量

```powershell
python -c "from src.infrastructure.zvec_store import ZvecStore; store = ZvecStore(); print(f'文档总数: {store.count()}')"
```

#### 手动备份

```powershell
# 创建备份目录
New-Item -ItemType Directory -Force -Path "backups"

# 备份 Zvec 数据目录
Compress-Archive -Path "data/zvec" -DestinationPath "backups\zvec_backup_$(Get-Date -Format 'yyyyMMdd').zip"
```

#### 数据恢复

```powershell
# 恢复 Zvec 数据
Expand-Archive -Path "backups\zvec_backup_20240101.zip" -DestinationPath "data" -Force
```

---

## 二、Linux Debian 生产部署

### 1. 系统要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Debian 11/12 |
| Python 版本 | 3.11+ |
| 内存 | 至少 2GB |
| 硬盘空间 | 至少 10GB |
| 网络 | 能访问 SenseNova API |

### 2. Python 环境配置

#### 安装 Python 3.11

```bash
# Debian 12
sudo apt update
sudo apt install python3 python3-pip python3-venv

# Debian 11（需要添加源）
sudo apt update
sudo apt install software-properties-common
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt install python3.11 python3.11-venv python3.11-dev
```

#### 创建虚拟环境

```bash
cd /opt/Zvec
python3.11 -m venv venv
source venv/bin/activate
```

#### 安装依赖

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. 环境变量配置

#### 创建 .env 文件

```bash
cp .env.example .env
nano .env
```

填入以下内容：

```env
# SenseNova API 配置
SENSE_NOVA_API_KEY=your_api_key_here
SENSE_NOVA_API_BASE=https://api.sensenova.cn/v1

# Zvec 配置
ZVEC_DATA_PATH=/opt/Zvec/data/zvec
ZVEC_DIMENSION=384

# FastAPI 配置
API_HOST=0.0.0.0
API_PORT=8000

# 日志配置
LOG_LEVEL=INFO
LOG_FILE=/opt/Zvec/logs/app.log

# 限流配置
RATE_LIMIT_PER_USER=60/minute
RATE_LIMIT_GLOBAL=1000/minute
RATE_LIMIT_STRATEGY=sliding_window
RATE_LIMIT_STORAGE_URI=redis://localhost:6379/0

# LLM 响应缓存（生产环境用 Redis）
LLM_CACHE_ENABLED=true
LLM_CACHE_MAXSIZE=10000
LLM_CACHE_TTL=86400
LLM_CACHE_SIMILARITY_THRESHOLD=0.95
LLM_CACHE_STORAGE_URI=redis://localhost:6379/1
```

### 4. Docker 部署

#### Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 创建日志目录
RUN mkdir -p /app/logs

EXPOSE 8000

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

#### docker-compose.yml

```yaml
version: '3.8'

services:
  zvec-service:
    build: .
    ports:
      - "8000:8000"
    environment:
      - SENSE_NOVA_API_KEY=${SENSE_NOVA_API_KEY}
      - ZVEC_DATA_PATH=/app/data/zvec
      - LOG_FILE=/app/logs/app.log
    volumes:
      - ./data/zvec:/app/data/zvec
      - ./logs:/app/logs
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

#### 部署命令

```bash
# 构建镜像
docker build -t zvec-service:latest .

# 启动服务
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down
```

### 5. 手动部署（systemd）

#### 创建服务文件

创建 `/etc/systemd/system/zvec-service.service`：

```ini
[Unit]
Description=Zvec Customer Service API
After=network.target

[Service]
User=zvec
Group=zvec
WorkingDirectory=/opt/Zvec
Environment="PATH=/opt/Zvec/venv/bin"
ExecStart=/opt/Zvec/venv/bin/gunicorn src.api.main:app \
    --workers 4 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --timeout 120
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

#### 安装 Gunicorn

```bash
pip install gunicorn
```

#### 启动服务

```bash
sudo systemctl daemon-reload
sudo systemctl enable zvec-service
sudo systemctl start zvec-service
```

#### 查看状态

```bash
sudo systemctl status zvec-service
```

### 6. Zvec 数据管理

#### 数据库初始化

首次部署后，需要导入 FAQ 文档到 Zvec：

```bash
source venv/bin/activate
python -c "from src.services.knowledge_service import KnowledgeService; svc = KnowledgeService(); svc.load_faq_documents(); print(f'导入完成，共 {svc.count()} 条文档')"
```

#### 查看文档数量

```bash
source venv/bin/activate
python -c "from src.infrastructure.zvec_store import ZvecStore; store = ZvecStore(); print(f'文档总数: {store.count()}')"
```

#### 集合优化（Collection Optimize）

Zvec 集合在批量更新后需要定期优化以保持检索性能：

```bash
# 批量更新知识库后优化
python -c "
from src.infrastructure.zvec_store import ZvecStore
store = ZvecStore()
store.optimize()
print('Collection optimized successfully')
"
```

**优化时机**：
- 批量插入/更新/删除文档后
- 定期维护窗口（每周一次）
- 检索性能下降时

**优化作用**：
- 清理删除标记
- 优化索引结构
- 提升检索速度

#### Schema 演进

Zvec 支持动态 Schema 演进，无需重建集合：

```bash
# 添加新字段
python -c "
from src.infrastructure.zvec_store import ZvecStore
from zvec import FieldSchema, InvertIndexParam, DataType
store = ZvecStore()
store.add_field(FieldSchema(
    name='category',
    data_type=DataType.STRING,
    index_param=InvertIndexParam(),
))
print('Field added successfully')
"
```

#### 手动备份

```bash
# 创建备份目录
mkdir -p /opt/Zvec/backups

# 备份 Zvec 数据
tar -czvf /opt/Zvec/backups/zvec_backup_$(date +%Y%m%d).tar.gz /opt/Zvec/data/zvec/
```

#### 定时备份（cron）

```bash
crontab -e
```

添加以下内容：

```
# 每天凌晨 2 点备份
0 2 * * * cd /opt/Zvec && tar -czvf backups/zvec_backup_$(date +\%Y\%m\%d).tar.gz data/zvec/

# 每周日凌晨 3 点优化集合
0 3 * * 0 cd /opt/Zvec && python -c "from src.infrastructure.zvec_store import ZvecStore; ZvecStore().optimize()"
```

#### 备份策略

| 项目 | 频率 | 保留期限 |
|------|------|----------|
| 每日备份 | 每天凌晨 2 点 | 7 天 |
| 每周备份 | 每周日凌晨 2 点 | 4 周 |
| 每月备份 | 每月 1 号凌晨 2 点 | 12 个月 |
| 集合优化 | 每周日凌晨 3 点 | - |

#### 数据恢复

```bash
# 恢复 Zvec 数据
tar -xzvf backups/zvec_backup_20240101.tar.gz -C /opt/Zvec
```

### 7. 监控与日志

#### 日志轮转配置

创建 `/etc/logrotate.d/zvec-service`：

```
/opt/Zvec/logs/app.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 644 zvec zvec
}
```

#### 日志分析

```bash
# 查看最近日志
tail -n 100 /opt/Zvec/logs/app.log

# 查看错误日志
grep -i "error" /opt/Zvec/logs/app.log

# 实时监控日志
tail -f /opt/Zvec/logs/app.log
```

#### Prometheus 监控（可选）

添加到 FastAPI：

```python
from prometheus_fastapi_instrumentator import Instrumentator, metrics

instrumentator = Instrumentator()
instrumentator.add(metrics.request_size(
    should_include_handler=True,
    should_include_method=True,
    should_include_status=True,
))
instrumentator.instrument(app).expose(app, endpoint="/metrics")
```

访问 `http://localhost:8000/metrics` 查看指标。

#### 自定义指标

| 指标名 | 类型 | 说明 |
|--------|------|------|
| zvec_search_duration_seconds | Histogram | Zvec 检索延迟 |
| zvec_document_count | Gauge | 知识库文档总数 |
| sensenova_api_duration_seconds | Histogram | SenseNova API 调用延迟 |
| sensenova_token_usage_total | Counter | Token 消耗（按操作类型分标签） |
| cache_hit_total / cache_miss_total | Counter | LLM 响应缓存命中/未命中 |
| knowledge_documents_loaded | Counter | 文档加载次数（按格式分标签） |

#### 健康检查

```bash
curl http://localhost:8000/api/health
```

---

## 三、故障排查

### Windows 开发环境

#### 问题：端口被占用

```powershell
# 查找占用端口的进程
netstat -ano | findstr :8000

# 结束进程（替换 PID）
taskkill /PID <PID> /F
```

#### 问题：依赖安装失败

```powershell
# 清理缓存
python -m pip cache purge

# 重新安装
python -m pip install --no-cache-dir -r requirements.txt
```

#### 问题：uvicorn 找不到模块

```powershell
# 确保在项目根目录运行
cd d:\AgentTest\Zvec

# 使用模块方式启动
python -m uvicorn src.api.main:app --reload
```

### Linux 生产环境

#### 问题：API 服务无法启动

```bash
# 检查端口占用
sudo netstat -tlnp | grep 8000

# 检查依赖
pip list | grep -E "fastapi|uvicorn"

# 检查环境变量
cat /opt/Zvec/.env

# 查看详细错误
journalctl -u zvec-service -n 50
```

#### 问题：Zvec 连接失败

```bash
# 检查目录权限
ls -la /opt/Zvec/data/zvec/

# 检查数据库状态
source venv/bin/activate
python -c "from src.core.zvec_client import ZvecClient; client = ZvecClient(); print(client.count())"
```

#### 问题：SenseNova API 调用失败

```bash
# 测试网络连接
curl -I https://api.sensenova.cn

# 检查 API Key
grep SENSE_NOVA_API_KEY /opt/Zvec/.env

# 查看错误日志
grep -i "sensenova" /opt/Zvec/logs/app.log
```

### 紧急恢复流程

1. **停止服务**
   - Windows：按 `Ctrl + C`
   - Linux：`sudo systemctl stop zvec-service` 或 `docker-compose down`

2. **检查日志**
   - Windows：查看终端输出或 `app.log`
   - Linux：`journalctl -u zvec-service -n 100`

3. **恢复备份**
   ```bash
   # Linux
   tar -xzvf backups/zvec_backup_latest.tar.gz -C /opt/Zvec
   ```

4. **启动服务**
   - Windows：重新运行 `uvicorn` 命令
   - Linux：`sudo systemctl start zvec-service`

5. **验证服务**
   ```bash
   curl http://localhost:8000/api/health
   ```

---

## 四、更新与维护

### Windows 开发环境

#### 代码更新

```powershell
# 拉取最新代码
git pull origin main

# 更新依赖
python -m pip install -r requirements.txt

# 重启服务（uvicorn --reload 会自动重启）
```

#### 依赖更新

```powershell
# 查看可更新的依赖
python -m pip list --outdated

# 更新指定依赖
python -m pip install --upgrade fastapi uvicorn
```

### Linux 生产环境

#### 代码更新流程

```bash
# 1. 停止服务
docker-compose down
# 或
sudo systemctl stop zvec-service

# 2. 拉取最新代码
git pull origin main

# 3. 更新依赖
source venv/bin/activate
pip install -r requirements.txt

# 4. 重新构建镜像（Docker 方式）
docker-compose build

# 5. 启动服务
docker-compose up -d
# 或
sudo systemctl start zvec-service

# 6. 验证服务
curl http://localhost:8000/api/health
```

#### 依赖更新

```bash
# 查看可更新的依赖
pip list --outdated

# 更新指定依赖
pip install --upgrade fastapi uvicorn

# 注意：不要使用 pip freeze 覆盖 requirements.txt
# requirements.txt 只保留直接依赖，传递依赖由 pip 自动解析
# 新增依赖时手动添加到 requirements.txt
```

### 定期维护清单

| 任务 | 频率 | Windows | Linux |
|------|------|---------|-------|
| 检查日志 | 每日 | 手动检查 | 自动轮转 |
| 更新依赖 | 每周 | 手动更新 | 自动更新 |
| 数据备份验证 | 每月 | 手动备份 | cron 自动 |
| 性能测试 | 每季度 | 手动测试 | 自动测试 |
| 安全检查 | 每季度 | 手动检查 | 自动扫描 |

---

## 五、版本更新记录

### v1.5 (2026-07-01)
- 与 project.md / development.md / claude.md 版本号对齐

### v1.4 (2026-07-01)
- 统一模块路径引用（zvec_store / knowledge_service）
- 统一环境变量限流格式（带单位，如 60/minute）
- 修复健康检查响应 JSON 语法错误
- 修复 pip freeze 误导说明，明确只保留直接依赖
- 新增知识库初始化导入流程说明
- 新增生产环境 LLM 缓存配置示例（Redis）

### v1.2 (2026-07-01)
- 初始版本

---

**文档版本**: v1.5  
**最后更新**: 2026-07-01  
**适用范围**: Zvec + 独立站客服项目