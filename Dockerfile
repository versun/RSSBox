# Build in local: docker build . --platform linux/arm64 -t versun/rsstranslator:dev versun/rssbox:dev
# Run with docker-compose to test: docker-compose -f docker-compose.test.yml up -d
# Push to dev: docker push versun/rsstranslator:dev versun/rssbox:dev
# Run with docker-compose in dev: docker-compose -f docker-compose.dev.yml up -d
# Multi-arch build:
# docker buildx create --use
# docker buildx build . --platform linux/arm64,linux/amd64 --push -t versun/rsstranslator:latest -t versun/rssbox:latest -t versun/rsstranslator:version -t versun/rssbox:version

# 使用更小的基础镜像作为builder
FROM python:3.13-slim-bookworm AS builder

# 设置工作目录
WORKDIR /app

# 安装构建依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    && rm -rf /var/lib/apt/lists/*

# 创建并激活虚拟环境
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 升级pip并安装uv
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir uv

# 复制其余代码
COPY . .
# 安装项目依赖
RUN uv pip install --no-cache-dir -e .

# ---- 最终阶段 ----
FROM python:3.13-slim-bookworm AS final

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000 \
    DockerHOME=/app \
    PYTHONPATH="/app"


WORKDIR $DockerHOME

# 关键修复：从builder阶段拷贝虚拟环境和应用代码
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app $DockerHOME

# 安装运行时的最小系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends gosu \
    cron \
    gettext \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && find /opt/venv -type d -name "__pycache__" -exec rm -r {} + \
    && find /opt/venv -type d -name "*.py[co]" -delete

# 配置Cron
RUN mkdir -p /var/run/cron && \
    touch /var/run/crond.pid && \
    chmod 644 /var/run/crond.pid 

COPY config/rt_cron /etc/cron.d/rt_cron
RUN chmod 0644 /etc/cron.d/rt_cron && \
    crontab /etc/cron.d/rt_cron && \
    touch /var/log/cron.log

# 设置entrypoint
COPY scripts/entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/entrypoint.sh
    
# 声明端口
EXPOSE ${PORT}

# 启动命令 (以root用户运行ENTRYPOINT)
ENTRYPOINT ["entrypoint.sh"]
CMD ["/opt/venv/bin/gunicorn","config.wsgi:application"]
