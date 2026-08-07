"""AssistMind 应用配置。

基于 Pydantic Settings，从环境变量 / .env 文件加载。
启动时通过 validate_security() 校验安全配置。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置（分层组织）。"""

    # ===== 应用 =====
    APP_NAME: str = "AssistMind"
    DEBUG: bool = False
    JWT_SECRET: str = "changeme-in-production-please-use-strong-secret"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    # ===== LLM（主 DeepSeek，备 Ollama）=====
    LLM_PROVIDER: str = "deepseek"
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-chat"
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:7b"

    # ===== Embedding & Reranker =====
    EMBEDDING_MODEL: str = "BAAI/bge-base-zh-v1.5"
    EMBEDDING_DIMENSION: int = 768
    EMBEDDING_DEVICE: str = "cpu"
    RERANKER_MODEL: str = "BAAI/bge-reranker-v2-m3"
    RERANKER_ENABLED: bool = True

    # ===== Qdrant =====
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_COLLECTION: str = "assistmind_docs"
    QDRANT_API_KEY: str | None = None

    # ===== Redis =====
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_CACHE_TTL: int = 3600
    SEMANTIC_CACHE_SIMILARITY: float = 0.92
    SEMANTIC_CACHE_MAX_ENTRIES: int = 10000

    # ===== PostgreSQL =====
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/assistmind"

    # ===== RAG 参数 =====
    CHUNK_SIZE: int = 512
    CHUNK_OVERLAP: int = 64
    VECTOR_TOP_K: int = 40
    BM25_TOP_K: int = 40
    RERANK_TOP_K: int = 8
    RRF_K: int = 60
    RRF_VECTOR_WEIGHT: float = 1.0
    RRF_BM25_WEIGHT: float = 1.0
    JACCARD_DEDUP_THRESHOLD: float = 0.8
    CRAG_HIGH_THRESHOLD: float = 0.7
    CRAG_LOW_THRESHOLD: float = 0.3

    # ===== 查询改写 =====
    QUERY_REWRITE_ENABLED: bool = True
    QUERY_REWRITE_STRATEGY: str = "multi_query"
    QUERY_REWRITE_NUM_VARIANTS: int = 3

    # ===== 失败降级（指数退避 + 断路器 + 独立超时）=====
    FALLBACK_ENABLED: bool = True
    LLM_MAX_RETRIES: int = 2
    LLM_RETRY_BASE_DELAY: float = 1.0
    LLM_RETRY_JITTER: float = 0.5
    LLM_FALLBACK_PROVIDER: str = "ollama"

    # 超时（秒）
    LLM_TIMEOUT: int = 30
    LLM_FALLBACK_TIMEOUT: int = 15
    EMBEDDING_TIMEOUT: int = 10
    QDRANT_TIMEOUT: int = 5
    BM25_TIMEOUT: int = 5
    RERANKER_TIMEOUT: int = 15
    REDIS_TIMEOUT: int = 2
    POSTGRES_TIMEOUT: int = 5
    QUERY_REWRITE_TIMEOUT: int = 10
    CRAG_TIMEOUT: int = 10

    # 断路器
    CB_LLM_FAIL_THRESHOLD: int = 5
    CB_LLM_OPEN_SECONDS: int = 60
    CB_LLM_HALF_OPEN_PROBES: int = 3
    CB_EMBEDDING_FAIL_THRESHOLD: int = 3
    CB_EMBEDDING_OPEN_SECONDS: int = 30
    CB_QDRANT_FAIL_THRESHOLD: int = 5
    CB_QDRANT_OPEN_SECONDS: int = 60
    CB_RERANKER_FAIL_THRESHOLD: int = 3
    CB_REDIS_FAIL_THRESHOLD: int = 5
    CB_REDIS_OPEN_SECONDS: int = 30
    CB_POSTGRES_FAIL_THRESHOLD: int = 5

    # ===== 意图路由 =====
    INTENT_SEMANTIC_THRESHOLD: float = 0.85
    INTENT_SEMANTIC_MARGIN: float = 0.1

    # ===== Agent =====
    MAX_ITERATIONS: int = 5  # ReAct Agent 最大迭代次数

    # ===== 对话记忆 =====
    MEMORY_WINDOW: int = 10
    MEMORY_SUMMARY_THRESHOLD: int = 6

    # ===== MCP =====
    MCP_SERVER_ENABLED: bool = True
    MCP_SERVER_URL: str = "http://localhost:8001/mcp"
    MCP_TRANSPORT: str = "streamable_http"

    # ===== Langfuse =====
    LANGFUSE_HOST: str = "http://localhost:3001"
    LANGFUSE_PUBLIC_KEY: str | None = None
    LANGFUSE_SECRET_KEY: str | None = None

    # ===== 限流 =====
    RATE_LIMIT_PER_MINUTE: int = 60

    # ===== 工单 =====
    TICKET_ID_RANDOM_SUFFIX: int = 7

    # ===== 电商业务数据源（订单/物流/商品/售后）=====
    # mock: 恒用内存演示数据（默认，无需外部依赖）
    # real: 恒用 PostgreSQL 实现（需先跑 scripts/init_db.py + seed_mall_db.py）
    # auto: 配置了 DATABASE_URL 且 PostgreSQL 健康探测通过 → real；否则降级 mock
    MALL_DATA_SOURCE: str = "mock"

    # ===== 运维数据源（Prometheus / ELK）=====
    # auto: 配置了 PROMETHEUS_URL 用真实数据源，未配置或整体不可用降级 mock
    # mock: 恒用预置故障场景模拟数据
    # real: 恒用真实数据源（单源失败走各方法降级 + degraded 标记）
    OPS_DATA_SOURCE: str = "auto"
    PROMETHEUS_URL: str = ""
    PROMETHEUS_TIMEOUT: int = 10
    PROMETHEUS_SERVICE_LABEL: str = "service"
    ALERTMANAGER_URL: str = ""
    ELASTICSEARCH_URL: str = ""
    ELASTICSEARCH_USERNAME: str | None = None
    ELASTICSEARCH_PASSWORD: str | None = None
    ELASTICSEARCH_INDEX: str = "logs-*"
    ELASTICSEARCH_CHANGE_INDEX: str = "changes-*"
    ELASTICSEARCH_TIMEOUT: int = 10

    # 运维数据源断路器
    CB_PROMETHEUS_FAIL_THRESHOLD: int = 5
    CB_PROMETHEUS_OPEN_SECONDS: int = 60
    CB_ELASTICSEARCH_FAIL_THRESHOLD: int = 5
    CB_ELASTICSEARCH_OPEN_SECONDS: int = 60
    CB_ALERTMANAGER_FAIL_THRESHOLD: int = 3
    CB_ALERTMANAGER_OPEN_SECONDS: int = 60

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    def validate_security(self) -> None:
        if self.DEBUG:
            return
        if self.JWT_SECRET in (
            "changeme-in-production-please-use-strong-secret",
            "changeme",
            "default",
            "",
        ):
            raise RuntimeError("JWT_SECRET 必须在生产环境设置为强随机字符串")
        if self.LLM_PROVIDER == "deepseek" and not self.DEEPSEEK_API_KEY:
            raise RuntimeError("LLM_PROVIDER=deepseek 时必须设置 DEEPSEEK_API_KEY")


@lru_cache
def get_settings() -> Settings:
    return Settings()
