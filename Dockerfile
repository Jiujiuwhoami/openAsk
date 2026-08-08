FROM python:3.11-slim

WORKDIR /app

# 安装系统编译依赖（zvec 和 sentence-transformers 需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    g++ \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# 先安装 PyTorch CPU-only（避免下载 ~2GB 的 CUDA 包）
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# 安装项目依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目代码
COPY . .

# 创建运行时目录（数据、日志）
RUN mkdir -p /app/data /app/logs

# 预下载 Sentence-BERT 和 BGE-Reranker 模型到镜像层（避免运行时无网络无法加载）
RUN python3 -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')" && \
    echo "Sentence-BERT 模型下载完成"
# Reranker 模型（可选，根据环境变量 RERANKER_ENABLED 决定是否启用）
RUN python3 -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-reranker-v2-m3')" && \
    echo "BGE-Reranker 模型下载完成"

# 强制离线模式（模型已预下载到镜像中）
ENV HF_HUB_OFFLINE=1

EXPOSE 8000

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]