# Dockerfile 多阶段构建
FROM python:3.10-slim AS builder

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

CMD ["python", "main.py"]
