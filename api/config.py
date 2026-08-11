"""GridMind 全局配置。

从 .env 和环境变量加载，提供统一的配置访问入口。
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

# 项目根目录
ROOT_DIR = Path(__file__).resolve().parent.parent

# 加载 .env（如有）
load_dotenv(ROOT_DIR / ".env")

# ── 应用版本（唯一版本常量，A1 遗留修复）────────────────────
# 前后端统一从这里读取；当前代码基线 v1.8.0（最终交付）。
# 升级版本时同步更新：web/package.json / web/package-lock.json / RELEASE-NOTES.md。
APP_VERSION: str = "1.8.0"


class Settings(BaseSettings):
    # ── 服务 ────────────────────────────────────────────
    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("API_PORT", "9900"))
    mcp_host: str = os.getenv("MCP_HOST", "0.0.0.0")
    mcp_port: int = int(os.getenv("MCP_PORT", "9901"))

    # ── DashScope ────────────────────────────────────────
    dashscope_api_key: str = os.getenv("DASHSCOPE_API_KEY", "sk-placeholder")

    # ── DeepSeek ─────────────────────────────────────────
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")

    # ── LLM 默认 / 当前模型 ────────────────────────────────
    default_model: str = os.getenv("DEFAULT_MODEL", "qwen-plus")

    # ── 数据库 ──────────────────────────────────────────
    database_path: str = str(
        ROOT_DIR / os.getenv("DATABASE_PATH", "data/gridmind.db")
    )

    # ── Chroma ──────────────────────────────────────────
    chroma_persist_dir: str | None = os.getenv("CHROMA_PERSIST_DIR")
    if chroma_persist_dir:
        chroma_persist_dir = str(ROOT_DIR / chroma_persist_dir)

    # ── 图谱 ────────────────────────────────────────────
    graph_db_path: str | None = os.getenv("GRAPH_DB_PATH")
    if graph_db_path:
        graph_db_path = str(ROOT_DIR / graph_db_path)

    # ── Neo4j（M0 知识图谱升级 · 新增）─────────────────────
    # Q4=A：M0 默认不切主链路（neo4j_enabled=False 时使用 NetworkXBackend 兼容垫片）
    neo4j_enabled: bool = os.getenv("NEO4J_ENABLED", "false").lower() == "true"
    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "gridmind-dev")
    neo4j_database: str = os.getenv("NEO4J_DATABASE", "neo4j")

    # ── 运行时 ──────────────────────────────────────────
    mock_enabled: bool = os.getenv("MOCK_ENABLED", "false").lower() == "true"
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    # ── 可解释性 AI 三层架构（P0）─────────────────────
    explainability_enabled: bool = os.getenv("EXPLAINABILITY_ENABLED", "true").lower() == "true"
    # P1-2: 架构要求 "5 分钟内热加载生效"，默认 300s（之前为 60s，过于激进）
    rules_hot_reload_interval_s: int = int(os.getenv("RULES_HOT_RELOAD_INTERVAL_S", "300"))
    explainability_checker_enabled: dict[str, bool] = {
        "overload": True,
        "short_circuit": True,
        "power_flow": True,
        "voltage": True,
        "temperature": True,
    }

    # ── M2 灰度切流 + 双向同步配置（新增）─────────────────────
    # 灰度比例（0 / 10 / 50 / 100 四态机）；M2 D+0 默认 0%
    grayscale_ratio: int = int(os.getenv("GRAYSCALE_RATIO", "0"))
    # 同步服务定时周期（秒）
    sync_interval_s: int = int(os.getenv("SYNC_INTERVAL_S", "300"))
    # 同步事件队列上限（内存 asyncio.Queue maxsize）
    sync_event_queue_size: int = int(os.getenv("SYNC_EVENT_QUEUE_SIZE", "1000"))
    # 自动回滚硬阈值：5min 窗口错误率 > 1% 触发
    auto_rollback_error_rate: float = float(os.getenv("AUTO_ROLLBACK_ERROR_RATE", "0.01"))
    # 自动回滚硬阈值：5min 窗口 P95 延迟 > 200ms 触发
    auto_rollback_p95_ms: float = float(os.getenv("AUTO_ROLLBACK_P95_MS", "200"))
    # 自动回滚硬阈值：Neo4j 连续失败次数 ≥ N 触发
    auto_rollback_neo4j_fails: int = int(os.getenv("AUTO_ROLLBACK_NEO4J_FAILS", "3"))
    # 自动回滚监控窗口（秒）
    auto_rollback_window_s: int = int(os.getenv("AUTO_ROLLBACK_WINDOW_S", "300"))
    # 自动回滚样本下限（< 此值不触发，避免冷启动误判）
    auto_rollback_min_samples: int = int(os.getenv("AUTO_ROLLBACK_MIN_SAMPLES", "50"))
    # 灰度管理 admin token（环境变量配置）
    admin_token: str = os.getenv("ADMIN_TOKEN", "gridmind-admin-token")

    # ── V1.5.1 T06 安全补丁（SSE 鉴权 + 限流）──────────
    # JWT 签名密钥（**生产必须**通过 JWT_SECRET 环境变量覆盖默认值）
    jwt_secret: str = os.getenv("JWT_SECRET", "gridmind-dev-secret-change-in-prod")
    # JWT 签名算法（默认 HS256；如需 RS256 配 JWKS 则改 RSA 密钥对）
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    # JWT 签发方（issuer claim），验证 token 必需匹配
    jwt_issuer: str = os.getenv("JWT_ISSUER", "gridmind")
    # admin 端点 IP 维度限流（次/分钟），slowapi 用
    rate_limit_per_minute: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))

    # ── V1.8.0 认证（T01）：JWT TTL / 初始管理员 / 锁定与密码策略 ──
    # access token 有效期（秒，默认 900s=15min）—— 前端 access 仅存内存，
    # 到期后由 401 拦截器用 refresh 自动续期（架构 §3.3 + 主理人拍板 #7）。
    jwt_access_ttl_seconds: int = int(os.getenv("JWT_ACCESS_TTL_SECONDS", "900"))
    # refresh token 有效期（秒，默认 604800s=7d）；DB 只存 SHA-256 hash，
    # 每次刷新轮换（旧行 revoked_at + replaced_by 成链）。
    jwt_refresh_ttl_seconds: int = int(os.getenv("JWT_REFRESH_TTL_SECONDS", "604800"))
    # 初始管理员密码（生产必配；dev 缺省用占位密码并告警）。
    # 生产 fail-closed：APP_ENV=production 且 users 表无 admin 且本值未配 → 启动拒绝。
    admin_initial_password: str = os.getenv("ADMIN_INITIAL_PASSWORD", "")
    # /auth/login 每 IP 限流（次/分钟），slowapi 用（per-IP 第二层防线）
    login_rate_limit_per_minute: int = int(os.getenv("LOGIN_RATE_LIMIT_PER_MINUTE", "10"))
    # /auth/register 每 IP 限流（次/分钟），slowapi 用（开放注册防滥用第一层；
    # 比 login 10/min 更严，默认 5/min；REGISTER_RATE_LIMIT_PER_MINUTE 可配）
    register_rate_limit_per_minute: int = int(os.getenv("REGISTER_RATE_LIMIT_PER_MINUTE", "5"))
    # per-account 锁定：连续失败 ≥ threshold 次 → 锁定 lock_minutes 分钟
    account_lock_threshold: int = int(os.getenv("ACCOUNT_LOCKOUT_THRESHOLD", "5"))
    account_lock_minutes: int = int(os.getenv("ACCOUNT_LOCKOUT_MINUTES", "15"))
    # 密码策略（主理人拍板 #2）：最短长度 ≥ 8 位 + 至少一个数字 + 至少一个字母
    password_min_length: int = int(os.getenv("PASSWORD_MIN_LENGTH", "8"))
    # 密码有效期（天，默认 90 天过期提醒；/auth/me 返回 password_expiring）
    password_expiry_days: int = int(os.getenv("PASSWORD_EXPIRY_DAYS", "90"))

    # ── B5：生产环境安全开关 ──────────────────────────
    # APP_ENV=production（或 PRODUCTION=1）时启用生产安全策略：
    # - JWT_SECRET / ADMIN_TOKEN 仍为公开默认值 → 启动拒绝（见模块底部门禁）
    # - 数据读取端点强制 JWT 鉴权（api/services/auth.verify_jwt_if_prod）
    # 未设置 APP_ENV（默认 dev）时行为保持原样，本地开发零配置。
    APP_ENV: str = os.getenv("APP_ENV", "dev").strip().lower()

    @property
    def is_production(self) -> bool:
        """生产模式判定：``APP_ENV=production`` 或 ``PRODUCTION=1``。"""
        if (self.APP_ENV or "dev").strip().lower() == "production":
            return True
        return os.getenv("PRODUCTION", "0").strip().lower() in ("1", "true", "yes")

    # ── V1.7.0 多用户地基（M-1 / M-2）──────────────
    # 未知 thread 严格拒绝开关（PRD Q2 默认：backfill + 懒登记，默认 False）：
    # - False（默认）：threads 表无记录但 checkpoint 存在 → 首个已认证访问者
    #   懒登记接管（保证 v1.6 存量数据可访问、不丢历史）；
    # - True：未知 thread 一律 404（生产部署如要求「未知 thread 一律拒绝」再开启）。
    threads_strict_mode: bool = os.getenv(
        "THREADS_STRICT_MODE", "false"
    ).lower() in ("1", "true", "yes")

    # ── M3a 推理能力增强配置（新增）─────────────────────
    # 1. Cypher 模板注册中心开关（默认 True —— M3a 启动即生效）
    template_registry_enabled: bool = os.getenv(
        "TEMPLATE_REGISTRY_ENABLED", "true"
    ).lower() == "true"
    # 2. 推理规则引擎开关（**默认 False**，需灰度验证：10% → 50% → 100%）
    inference_engine_enabled: bool = os.getenv(
        "INFERENCE_ENGINE_ENABLED", "false"
    ).lower() == "true"
    # 3. 路径优化器开关（Q3=A 默认 True —— M3a 即生效）
    path_optimizer_enabled: bool = os.getenv(
        "PATH_OPTIMIZER_ENABLED", "true"
    ).lower() == "true"
    # 4. 路径优化器 LRU 缓存大小（默认 256，Q3=A 已拍板）
    path_optimizer_cache_size: int = int(
        os.getenv("PATH_OPTIMIZER_CACHE_SIZE", "256")
    )

    # ── M3c 可观测性配置（新增）─────────────────────
    # 1. Prometheus 指标采集开关（默认 True —— §5.6 验收要求）
    metrics_enabled: bool = os.getenv("METRICS_ENABLED", "true").lower() == "true"
    # 2. 钉钉告警开关（默认 False —— Q4=A 已拍板，webhook 就绪后才开）
    dingtalk_enabled: bool = os.getenv("DINGTALK_ENABLED", "false").lower() == "true"
    # 3. 钉钉 webhook URL（沙箱留空字符串 → 发送时直接 log，不真发）
    dingtalk_webhook_url: str = os.getenv("DINGTALK_WEBHOOK_URL", "")
    # 4. 钉钉签名密钥（可选，留接口；当前简单实现不验签）
    dingtalk_secret: str | None = os.getenv("DINGTALK_SECRET") or None
    # 5. 钉钉告警冷却期（默认 300s = 5min —— §5.2 验收要求）
    dingtalk_cooldown_s: int = int(os.getenv("DINGTALK_COOLDOWN_S", "300"))
    # 6. 灰度面板前端开关（默认 True —— M3c 启用即可见）
    grayscale_panel_enabled: bool = os.getenv("GRAYSCALE_PANEL_ENABLED", "true").lower() == "true"

    # ── M-3 知识库来源引用链配置（新增）──────────────────
    # score 统一归一化 0-1（K-2）；citation_min_score 与拒答阈值（0.25）对齐，
    # 低于该值的来源不下发；citation_top_n 限制单轮最多下发的结构化来源条数。
    citation_min_score: float = float(os.getenv("CITATION_MIN_SCORE", "0.25"))
    citation_top_n: int = int(os.getenv("CITATION_TOP_N", "5"))

    model_config = {"frozen": True}  # 不可变配置


settings = Settings()

# ═══════════════════════════════════════════════════════
# B5：生产模式安全门禁——公开默认密钥必须被 .env 覆盖，否则拒绝启动
# ═══════════════════════════════════════════════════════
if settings.is_production:
    _DEFAULT_JWT_SECRET = "gridmind-dev-secret-change-in-prod"
    _DEFAULT_ADMIN_TOKEN = "gridmind-admin-token"
    if not settings.jwt_secret or settings.jwt_secret == _DEFAULT_JWT_SECRET:
        raise SystemExit(
            "[FATAL] APP_ENV=production 但 JWT_SECRET 仍为公开默认值或未配置。\n"
            "        请在 .env 中设置强随机 JWT_SECRET（如 `openssl rand -hex 32`）后重试。"
        )
    if not settings.admin_token or settings.admin_token == _DEFAULT_ADMIN_TOKEN:
        raise SystemExit(
            "[FATAL] APP_ENV=production 但 ADMIN_TOKEN 仍为公开默认值或未配置。\n"
            "        请在 .env 中设置强随机 ADMIN_TOKEN 后重试。"
        )
    if settings.neo4j_enabled and settings.neo4j_password == "gridmind-dev":
        print(
            "[WARN] APP_ENV=production 且 NEO4J_ENABLED=true，但 NEO4J_PASSWORD "
            "仍为公开默认值，建议在 .env 中覆盖。"
        )
