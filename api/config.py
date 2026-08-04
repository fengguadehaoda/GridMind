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


class Settings(BaseSettings):
    # ── 服务 ────────────────────────────────────────────
    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("API_PORT", "9900"))
    mcp_host: str = os.getenv("MCP_HOST", "0.0.0.0")
    mcp_port: int = int(os.getenv("MCP_PORT", "9901"))

    # ── DashScope ────────────────────────────────────────
    dashscope_api_key: str = os.getenv("DASHSCOPE_API_KEY", "sk-placeholder")

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

    model_config = {"frozen": True}  # 不可变配置


settings = Settings()
