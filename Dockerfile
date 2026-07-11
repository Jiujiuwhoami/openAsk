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

EXPOSE 8000

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]