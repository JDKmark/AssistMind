# AssistMind 后端镜像（线上演示用）
# 构建上下文 = 仓库根（见 docker-compose.app.yml：context: . / dockerfile: backend/Dockerfile）。
# 目录布局与源码仓库一致（backend/app、backend/scripts、knowledge），
# 保证 seed 脚本里 os.path.join(__file__, "..", "..", "knowledge") 在容器内仍指向正确位置。

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /workspace/backend

# 构建依赖（sentence-transformers / torch 在部分平台需要编译扩展）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 先拷贝依赖清单与包源码，充分利用 Docker 层缓存
COPY backend/pyproject.toml /workspace/backend/pyproject.toml
COPY backend/app /workspace/backend/app
COPY backend/scripts /workspace/backend/scripts

# 安装后端包（生产依赖，不含 dev 测试工具）
RUN pip install --no-cache-dir .

# 知识库材料（容器内首次启动 auto-seed 用）
COPY knowledge /workspace/knowledge

COPY backend/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 8001
ENTRYPOINT ["/docker-entrypoint.sh"]
