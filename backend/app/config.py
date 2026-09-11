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
    # local（本机 CrossEncoder，CPU 推理慢）| siliconflow（云端 rerank API，免费档同款模型）
    RERANKER_PROVIDER: str = "local"
    SILICONFLOW_API_BASE: str = "https://api.siliconflow.cn/v1"
    SILICONFLOW_API_KEY: str = ""

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
    # DDL/回填/GRANT 专用迁移账号（phase13 最小权限）：仅 init_db 使用，Uvicorn 运行时不用。
    # 空则回退 DATABASE_URL（本地开发单账号）；生产部署必须显式配置独立迁移账号。
    DATABASE_MIGRATION_URL: str = ""

    # ===== RAG 参数 =====
    CHUNK_SIZE: int = 512
    CHUNK_OVERLAP: int = 64
    VECTOR_TOP_K: int = 40
    BM25_TOP_K: int = 40
    RERANK_TOP_K: int = 20
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
    # 流式生成通道的快速失败超时（仅 stream_llm 使用）：
    # 商汤网关间歇故障时，生成阶段会先等满 LLM_TIMEOUT+重试+Ollama 15s ≈ 90s 才出模板兜底。
    # 流式 TTFT 快（思考模型 content 首 token 通常 <10s），超时压到 10s/6s 后，
    # DeepSeek→Ollama 全失败感知 ≈16s，用户更快看到兜底而非干等。
    LLM_STREAM_TIMEOUT: int = 10
    LLM_STREAM_FALLBACK_TIMEOUT: int = 6
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
    # 尾斜杠必须保留：FastAPI app.mount('/mcp') 对无尾斜杠路径返回 307，
    # mcp 库 streamable_http_client 不跟随重定向会导致连接失败（「Unexpected content type」）。
    MCP_SERVER_URL: str = "http://localhost:8001/mcp/"
    MCP_TRANSPORT: str = "streamable_http"

    # ===== Langfuse =====
    LANGFUSE_HOST: str = "http://localhost:3001"
    LANGFUSE_PUBLIC_KEY: str | None = None
    LANGFUSE_SECRET_KEY: str | None = None

    # ===== 限流 =====
    RATE_LIMIT_PER_MINUTE: int = 60
    # 限流兜底（T8）：off（默认，保持现有 fail-open 语义不变）| inproc（Redis 挂掉时
    # 用进程内固定窗口兜底，仅单实例有效）。默认 off，不得改变现有行为。
    RATE_LIMIT_FALLBACK: str = "off"

    # ===== 应用级可观测（T5 / Prometheus exporter）=====
    # /metrics 端点：prometheus_client 单进程模式（多 worker 需 multiprocess，见文档）。
    # 关闭或未安装 prometheus_client 时整体 no-op，应用能力不受影响。
    METRICS_ENABLED: bool = True
    METRICS_PATH: str = "/metrics"

    # ===== 故障注入（T6，压测用；默认全关，env 驱动，运行时可热切换）=====
    # FAULT_LLM=ok|timeout|error|slow：控制 LLM 层行为
    # FAULT_RERANKER=ok|timeout|fail：控制重排层行为
    # FAULT_QDRANT=ok|down：控制向量召回层行为
    # 实现复用既有 provider / 断路器 / 降级抽象，不在业务路径散落 if。
    FAULT_LLM: str = "ok"
    FAULT_RERANKER: str = "ok"
    FAULT_QDRANT: str = "ok"
    # 运行时覆盖文件（可选）：指向 JSON（如 {"FAULT_RERANKER": "fail"}），按 mtime 热加载，
    # 用于压测中不重启进程地开关故障；留空则只用上面的环境变量。
    FAULT_OVERRIDE_FILE: str = ""
    # timeout 模式等待时长（应 > LLM_STREAM_TIMEOUT，确保走降级），slow 模式首 token 延迟
    FAULT_LLM_DELAY_MS: int = 12000
    FAULT_LLM_SLOW_MS: int = 3000

    # ===== Mock LLM 模式（离线压测，spec 3.5）=====
    # LLM_PROVIDER=mock 时按固定 token 数与固定间隔流式返回，隔离上游、测纯服务端能力。
    MOCK_LLM_TOKENS: int = 300
    MOCK_LLM_INTERVAL_MS: int = 20
    MOCK_LLM_ANSWER: str = ""

    # ===== 语音播报（TTS）=====
    # edge-tts 音色：晓晓女声（客服首选）；可切 zh-CN-YunxiNeural（男声）等
    TTS_VOICE: str = "zh-CN-XiaoxiaoNeural"

    # ===== 工单 =====
    TICKET_ID_RANDOM_SUFFIX: int = 7

    # ===== 电商业务数据源（订单/物流/商品/售后）=====
    # mock: 恒用内存演示数据（默认，无需外部依赖）
    # real: 恒用 PostgreSQL 实现（需先跑 scripts/init_db.py + seed_mall_db.py）
    # auto: 配置了 DATABASE_URL 且 PostgreSQL 健康探测通过 → real；否则降级 mock
    MALL_DATA_SOURCE: str = "mock"

    # ===== 电商实体识别 =====
    # 规则层确定性优先（零 LLM 成本）；开启后仅当规则未命中时才走 LLM 兜底
    # （一次结构化抽取；失败/超时/解析失败 → 空实体 + logger.warning，不阻塞主链路）
    ENTITY_LLM_FALLBACK: bool = False

    # ===== 产品消歧（跨品类同型号，phase15）=====
    # 规则管道（别名召回→信号打分→订单交集）零 LLM；开启后仅在落入反问分支前
    # 走一次 LLM 兜底（fast=True 快速失败；失败/非法输出回落反问 + logger.warning）
    DISAMBIG_LLM_FALLBACK: bool = False

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

    @staticmethod
    def db_minimal_privilege_error(runtime_dsn: str, migration_dsn: str) -> str | None:
        """phase13 最小权限账号校验：通过返回 None，否则返回可读失败原因。

        - runtime 缺用户名 → 拒绝（无法识别账号）
        - runtime 是 postgres 超级用户：有迁移账号时拒绝（声称分离仍用超管）；
          无迁移账号时放行（本地单账号开发场景，与 entrypoint/init_db 既有语义一致）
        - runtime 非 postgres 但缺迁移账号 → 拒绝（DDL/授权无独立账号执行）
        - runtime 与迁移账号相同 → 拒绝（未真正分离）

        容器入口（docker-entrypoint.sh）使用本函数；deploy.sh 的部署前置校验为
        独立 bash 实现（生产语义更严：postgres 无条件拒绝），两处修改需同步。
        """
        from sqlalchemy.engine import make_url

        runtime = make_url(runtime_dsn)
        if not runtime.username:
            return "DATABASE_URL 缺少用户名：生产必须配置非 postgres 的独立 runtime 账号"
        if runtime.username == "postgres":
            if migration_dsn:
                return (
                    "后端 runtime 账号不能是 postgres 超级用户（DATABASE_URL 仍为默认账号，"
                    "即使配置了迁移账号也未真正分离）：请改为独立 runtime 账号"
                )
            return None  # 本地单账号开发（无迁移账号）放行
        if not migration_dsn:
            return "runtime 账号非 postgres 时必须配置独立 DATABASE_MIGRATION_URL（DDL/授权走迁移账号）"
        migration = make_url(migration_dsn)
        if not migration.username or migration.username == runtime.username:
            return "DATABASE_MIGRATION_URL 必须与 runtime 账号（DATABASE_URL）分离，不得复用同一账号"
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
