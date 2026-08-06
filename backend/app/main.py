"""AssistMind FastAPI 应用入口。

负责创建 FastAPI 实例 + lifespan、注册路由、配置 CORS。
本文件仅做装配，业务逻辑在各 api/ 和 core/ 模块中实现。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, chat, feedback, health, knowledge, ops, ticket
from app.config import get_settings
from app.core.infra.alertmanager import get_alertmanager
from app.core.infra.circuit_breaker import init_breakers
from app.core.infra.elasticsearch import get_elasticsearch
from app.core.infra.langfuse import get_langfuse, is_langfuse_enabled
from app.core.infra.prometheus import get_prometheus
from app.core.infra.qdrant import get_qdrant
from app.core.infra.redis import get_redis
from app.core.mcp.server import get_mcp_app, get_mcp_session_manager

logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化，关闭时清理。"""
    settings.validate_security()
    logger.info("[%s] 启动中，DEBUG=%s", settings.APP_NAME, settings.DEBUG)

    # 初始化 Redis（断路器状态共享依赖 Redis）
    redis = get_redis()
    await redis.connect()
    if redis.is_connected:
        logger.info("[Main] Redis 连接成功，初始化断路器（Redis 共享状态）")
        init_breakers(redis=redis.client)
    else:
        logger.warning("[Main] Redis 连接失败，断路器降级为内存存储（仅单 worker 可用）")
        init_breakers(redis=None)

    # 初始化 Qdrant（RAG 向量召回）并从全量数据构建 BM25 内存索引
    try:
        qdrant = get_qdrant()
        await qdrant.connect()
        if qdrant.is_connected:
            from app.core.rag.bm25 import get_bm25

            all_docs = await qdrant.scroll_all()
            get_bm25().build(all_docs)
            logger.info("[Main] Qdrant 连接成功，BM25 索引构建 %d 文档", len(all_docs))
        else:
            logger.warning("[Main] Qdrant 连接失败，检索降级为仅 BM25（BM25 索引可能为空）")
    except Exception as e:
        logger.warning("[Main] Qdrant/BM25 初始化失败: %s", e)

    # 初始化运维数据源客户端（Prometheus / Elasticsearch / Alertmanager）
    # 客户端创建无 I/O 不阻塞；真实数据源健康探测在 auto 模式首次访问时惰性执行
    for getter in (get_prometheus, get_elasticsearch, get_alertmanager):
        try:
            await getter().connect()
        except Exception as e:
            logger.warning("[Main] 运维数据源客户端初始化失败: %s", e)

    # 初始化 Langfuse 客户端（LLM 可观测性；未配置 key 时跳过，不阻塞启动）
    try:
        if is_langfuse_enabled():
            get_langfuse()
            logger.info("[Main] Langfuse 已启用（host=%s）", settings.LANGFUSE_HOST)
        else:
            logger.info(
                "[Main] Langfuse 未启用（未配置 LANGFUSE_PUBLIC_KEY/SECRET_KEY），跳过初始化，不进行埋点"
            )
    except Exception as e:
        logger.warning("[Main] Langfuse 初始化失败（降级为不埋点）: %s", e)

    # 启动 MCP session manager（挂载为子 app 时 lifespan 不自动执行，需手动启动）
    mcp_cm = None
    if settings.MCP_SERVER_ENABLED:
        try:
            get_mcp_app()  # 确保 app 和 session_manager 已创建
            mcp_sm = get_mcp_session_manager()
            # session_manager.run() 是 async context manager，需在整个 app 生命周期内保持
            mcp_cm = mcp_sm.run()
            await mcp_cm.__aenter__()
            logger.info("[Main] MCP session manager 已启动")
        except Exception as e:
            logger.warning("[Main] MCP session manager 启动失败: %s", e)
            mcp_cm = None

    # TODO: Phase 2 后续：Qdrant / PostgreSQL 连接池
    yield

    logger.info("[%s] 关闭中", settings.APP_NAME)

    # 关闭 MCP session manager
    if mcp_cm is not None:
        try:
            await mcp_cm.__aexit__(None, None, None)
            logger.info("[Main] MCP session manager 已关闭")
        except Exception as e:
            logger.warning("[Main] MCP session manager 关闭失败: %s", e)

    await redis.close()

    # 关闭运维数据源客户端
    for getter in (get_prometheus, get_elasticsearch, get_alertmanager):
        try:
            await getter().close()
        except Exception as e:
            logger.warning("[Main] 运维数据源客户端关闭失败: %s", e)


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例。"""
    app = FastAPI(
        title=settings.APP_NAME,
        description="SaaS 产品文档智能问答客服系统",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router, prefix="/api/v1", tags=["health"])
    app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
    app.include_router(chat.router, prefix="/api/v1/chat", tags=["chat"])
    app.include_router(knowledge.router, prefix="/api/v1/knowledge", tags=["knowledge"])
    app.include_router(ticket.router, prefix="/api/v1/ticket", tags=["ticket"])
    app.include_router(feedback.router, prefix="/api/v1/feedback", tags=["feedback"])
    app.include_router(ops.router, prefix="/api/v1/ops", tags=["ops"])

    # 挂载 MCP Server（streamable_http 传输）
    if settings.MCP_SERVER_ENABLED:
        app.mount("/mcp", get_mcp_app())
        logger.info("[Main] MCP Server 已挂载到 /mcp")

    return app


app = create_app()
