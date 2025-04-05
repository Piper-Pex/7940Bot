# Dockerfile 多阶段构建
FROM python:3.9-slim as builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

FROM python:3.9-slim
WORKDIR /app
COPY --from=builder /root/.local /root/.local
COPY . .

# 通过环境变量注入密钥
ENV PATH=/root/.local/bin:$PATH
ENV CONFIG_PATH=config/secrets/.prod.env

CMD ["python", "app/main.py"]
