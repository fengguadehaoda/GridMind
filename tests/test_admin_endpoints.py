"""V1.5.1 LangGraph 后端改造 · T05 · Admin 端点鉴权 + 返回完整性测试（架构 §2.3.3 + §10.8）。

**T05 范围**：验证 ``GET /admin/checkpoint-stats`` 的 3 个关键契约：

1. **鉴权**（架构 §2.3.3）：
   - 无 ``X-Admin-Token`` header → **401 Unauthorized**
   - header 值错误 → **403 Forbidden**
   - header 值正确（= ``settings.admin_token``）→ **200 OK**

2. **返回完整性**（架构 §4.1 CheckpointStats schema）：
   - ``total_checkpoints: int``
   - ``total_threads: int``
   - ``expired_cleaned_24h: int``
   - ``active_sessions: int``
   - ``db_size_bytes: int``
   - ``ttl_seconds: int``（默认 1800，主理人决策 #4）
   - 字段名、类型、数量、TTL 默认值 100% 匹配

3. **fail-closed 行为**：
   - graph_builder 未初始化 → 503（与现有 /chat 端点一致）
   - token 大小写敏感（避免误判）

**测试策略**（**5 个场景**，≥ 3 PASS 必达）：

- 用 ``fastapi.testclient.TestClient`` 真实打 HTTP 端点（不走函数直调）
- ``monkeypatch.setenv`` 把 ``GRIDMIND_ADMIN_TOKEN`` 切到测试值；
  通过 ``reload(api.config)`` 让新值即时生效（settings model_config frozen=True，
  所以走"重 settings 实例"路径）
- 不注入 graph_builder（端点不依赖它，依赖只用于鉴权）

**验收（架构 §10.8）**：

- 5xx 错误率 0%
- 401/403/200 三种路径 100% 正确
- 返回字段 100% 覆盖 schema

**运行**::

    cd /path/to/GridMind
    PYTHONPATH=. python -m pytest tests/test_admin_endpoints.py -v
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

# 在导入 api 之前开启 Mock 模式（避免触发 LLM Key 校验）
os.environ.setdefault("MOCK_ENABLED", "true")
# 强制 admin_token 用确定值（避免 .env 干扰）
os.environ["ADMIN_TOKEN"] = "test-admin-token-xyz"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

# 重新加载 settings 让 os.environ 生效
from api.config import settings as _settings_initial  # noqa: F401


# ═══════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _reload_settings_with_test_token(monkeypatch):
    """每个 test 前重载 settings 实例，确保 ``settings.admin_token`` 用测试值。

    注意：当 pytest 把多个测试文件一起收集时，其它文件可能先于本文件
    import ``api.main``，从而触发 ``api.services.grayscale_admin_service``
    的首次加载（其模块顶部有 ``from api.config import settings`` 捕获了
    当时的 settings 实例）。所以本 fixture **必须**同时重载这 3 个模块：
    - ``api.config``：让 ``settings.admin_token`` 读新 env
    - ``api.services.grayscale_admin_service``：让 ``GrayscaleAdminService``
      函数内引用的 settings 跟着刷新
    - ``api.main``：让 ``verify_admin_token`` 闭包 import 最新的依赖

    否则 ``grayscale_admin_service`` 模块仍然持有旧 settings 实例，导致
    ``verify_admin_token`` 在 token 正确时仍返回 False → 403。
    """
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token-xyz")
    # 重新加载顺序很重要：依赖方（main）放最后
    import api.config as config_module
    importlib.reload(config_module)
    from api.services import grayscale_admin_service as gas_module
    importlib.reload(gas_module)
    import api.main as main_module
    importlib.reload(main_module)
    yield


@pytest.fixture
def client(monkeypatch) -> TestClient:
    """返回 ``TestClient(app)``，**不**经过 lifespan（避免触发真实 MCP 连接）。

    通过 ``TestClient(app)``（**不**用 ``with``）跳过 lifespan：
    lifespan 调 MCP server 连接 / ChromaSync，会让测试缓慢且有副作用。
    """
    from api.main import app
    import api.main

    # 不注入 graph_builder：admin 端点本身不需要（其他端点需要）
    # 但 lifespan 不会跑，所以 graph_builder 默认 None 也没事
    return TestClient(app)


# ═══════════════════════════════════════════════════════
# 1. 有效 admin token → 200 + 完整 CheckpointStats
# ═══════════════════════════════════════════════════════


def test_admin_checkpoint_stats_with_valid_token(client: TestClient) -> None:
    """``X-Admin-Token: test-admin-token-xyz`` → 200 + 完整 CheckpointStats 字段。

    验证（架构 §4.1 + §10.8）：
    - status_code == 200
    - 6 个字段全在（total_checkpoints / total_threads / expired_cleaned_24h /
      active_sessions / db_size_bytes / ttl_seconds）
    - ttl_seconds == 1800（主理人决策 #4）
    - count 类型都是 int
    """
    response = client.get(
        "/admin/checkpoint-stats",
        headers={"X-Admin-Token": "test-admin-token-xyz"},
    )
    assert response.status_code == 200, (
        f"有效 admin token 应 200，实际 {response.status_code}: {response.text}"
    )

    body = response.json()
    assert isinstance(body, dict), f"响应应是 dict，实际 {type(body)}"

    # 6 个必填字段（架构 §2.3.3 + §4.1）
    required_fields = {
        "total_checkpoints",
        "total_threads",
        "expired_cleaned_24h",
        "active_sessions",
        "db_size_bytes",
        "ttl_seconds",
    }
    actual_fields = set(body.keys())
    assert required_fields.issubset(actual_fields), (
        f"缺少字段: 期望 {required_fields - actual_fields}, "
        f"实际 {actual_fields}"
    )

    # 字段类型校验
    for f in required_fields:
        assert isinstance(body[f], int), (
            f"字段 {f} 应为 int，实际 {type(body[f]).__name__}: {body[f]!r}"
        )

    # TTL 默认 1800（主理人决策 #4，30 分钟）
    assert body["ttl_seconds"] == 1800, (
        f"ttl_seconds 默认应为 1800（30 分钟），实际 {body['ttl_seconds']}"
    )

    # 数值范围合理
    assert body["total_checkpoints"] >= 0
    assert body["total_threads"] >= 0
    assert body["expired_cleaned_24h"] >= 0
    assert body["active_sessions"] >= 0
    assert body["db_size_bytes"] >= 0

    print(
        f"[PASS] /admin/checkpoint-stats 200 + 完整字段: {body}"
    )


# ═══════════════════════════════════════════════════════
# 2. 无 X-Admin-Token header → 401
# ═══════════════════════════════════════════════════════


def test_admin_checkpoint_stats_without_token_returns_401(client: TestClient) -> None:
    """dev 模式匿名 → 放行（V1.7.0 RBAC 契约变更，架构 §7.3：require_role dev 放行）。

    V1.7.0 变更说明：``/admin/checkpoint-stats`` 鉴权由 ``verify_admin_token``
    （始终要 token）改为 ``require_role(OPERATOR, ADMIN)``（与 verify_jwt_if_prod
    同语义：生产强制、dev 放行）。因此 dev 模式下无 token → 200（矩阵不生效）；
    生产模式匿名 → 401（JWT 缺失）已由 ``test_rbac_matrix`` 覆盖。
    """
    # dev 模式：require_role 放行（矩阵不生效）
    response = client.get("/admin/checkpoint-stats")
    assert response.status_code == 200, (
        f"dev 模式匿名应 200（RBAC dev 放行），实际 {response.status_code}: {response.text}"
    )
    print(f"[PASS] dev 匿名 → 200（V1.7.0 require_role dev 放行）")


# ═══════════════════════════════════════════════════════
# 3. 错误 admin token → 403
# ═══════════════════════════════════════════════════════


def test_admin_checkpoint_stats_with_wrong_token_returns_403(client: TestClient) -> None:
    """``X-Admin-Token: wrong-token`` → 403 Forbidden（T05 与 #2 区分的关键）。

    关键验证：
    - 与"无 token"返回 401 不同（精细化）
    - 错误信息提示"Invalid"或"invalid"
    """
    response = client.get(
        "/admin/checkpoint-stats",
        headers={"X-Admin-Token": "wrong-token-12345"},
    )
    assert response.status_code == 403, (
        f"错误 token 应 403，实际 {response.status_code}: {response.text}"
    )
    body = response.json()
    detail = body.get("detail", "").lower()
    assert "invalid" in detail, (
        f"403 错误信息应提示 'invalid'，实际: {body}"
    )
    # 403 响应**不**应有 WWW-Authenticate（与 401 区分）
    www_auth = response.headers.get("WWW-Authenticate")
    assert www_auth is None, (
        f"403 不应有 WWW-Authenticate 头（与 401 区分），实际: {www_auth!r}"
    )
    print(f"[PASS] 错误 token → 403: {body}")


# ═══════════════════════════════════════════════════════
# 4. （额外）空 header 值视为无 token（401 而非 403）
# ═══════════════════════════════════════════════════════


def test_admin_checkpoint_stats_with_empty_token_returns_401(
    client: TestClient,
) -> None:
    """``X-Admin-Token: ""``（空字符串）视为无凭证（V1.7.0：dev 放行 → 200）。

    V1.7.0 变更：空字符串被 require_role 视为「未提供 admin token」→ dev 放行；
    生产模式匿名（无 JWT）→ 401 由 test_rbac_matrix 覆盖。
    """
    response = client.get(
        "/admin/checkpoint-stats",
        headers={"X-Admin-Token": ""},
    )
    assert response.status_code == 200, (
        f"dev 空 token 应视为未提供 → 200（放行），实际 {response.status_code}"
    )
    print(f"[PASS] dev 空 token → 200（V1.7.0 require_role dev 放行）")


# ═══════════════════════════════════════════════════════
# 5. （额外）token 大小写敏感
# ═══════════════════════════════════════════════════════


def test_admin_token_is_case_sensitive(client: TestClient) -> None:
    """``X-Admin-Token: TEST-ADMIN-TOKEN-XYZ``（大写）应被拒绝 → 403。

    验证：admin token 比较是**精确**等值（不 case-fold）；避免误判。
    """
    response = client.get(
        "/admin/checkpoint-stats",
        headers={"X-Admin-Token": "TEST-ADMIN-TOKEN-XYZ"},  # 大写
    )
    # 大小写不匹配 → 应 403
    assert response.status_code == 403, (
        f"大小写不匹配的 token 应 403，实际 {response.status_code}: {response.text}"
    )
    print("[PASS] admin token 大小写敏感（大写变体返回 403）")


# ═══════════════════════════════════════════════════════
# Runner（兼容 ``python tests/test_admin_endpoints.py``）
# ═══════════════════════════════════════════════════════


def _run_all() -> None:
    """非 pytest 入口。"""
    import traceback

    # 准备环境（不可用 fixture，跑 standalone 需要手动 reload settings）
    os.environ["ADMIN_TOKEN"] = "test-admin-token-xyz"
    import api.config as config_module
    importlib.reload(config_module)
    from api.services import grayscale_admin_service as gas_module
    importlib.reload(gas_module)
    import api.main as main_module
    importlib.reload(main_module)

    from fastapi.testclient import TestClient as _TC
    from api.main import app

    client = _TC(app)

    tests: list[tuple[str, Any]] = [
        (
            "test_admin_checkpoint_stats_with_valid_token",
            lambda: test_admin_checkpoint_stats_with_valid_token(client),
        ),
        (
            "test_admin_checkpoint_stats_without_token_returns_401",
            lambda: test_admin_checkpoint_stats_without_token_returns_401(client),
        ),
        (
            "test_admin_checkpoint_stats_with_wrong_token_returns_403",
            lambda: test_admin_checkpoint_stats_with_wrong_token_returns_403(client),
        ),
        (
            "test_admin_checkpoint_stats_with_empty_token_returns_401",
            lambda: test_admin_checkpoint_stats_with_empty_token_returns_401(client),
        ),
        (
            "test_admin_token_is_case_sensitive",
            lambda: test_admin_token_is_case_sensitive(client),
        ),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"[PASS] {name}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[FAIL] {name}: {e}")
            traceback.print_exc()

    print(f"\n{passed}/{passed + failed} tests passed")
    if failed:
        sys.exit(1)


from typing import Any  # noqa: E402


if __name__ == "__main__":
    _run_all()
