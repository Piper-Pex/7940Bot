# Dockerfile 多阶段构建
FROM python:3.10-slim as builder
# ------------- 安装系统依赖 -------------
# 安装 psycopg2 所需的 PostgreSQL 开发库和编译工具
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt


COPY . .

# 通过环境变量注入密钥
ENV PATH=/root/.local/bin:$PATH
ENV CONFIG_PATH=config/secrets/.prod.env

CMD ["gunicorn", "--bind", "0.0.0.0:${PORT}", "--worker-class", "uvicorn.workers.UvicornWorker", "main:app"]
