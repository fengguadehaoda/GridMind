"""V1.7.0 多用户地基 · T3 per-session 模型隔离测试（M-2）。

**范围**（架构 multiuser-architecture Task 3 验收 + PRD US-2.1/2.2/2.3）：
1. 双会话并发：``get_model_for_thread(A)=deepseek-chat``、
   ``get_model_for_thread(B)=全局默认``，互不影响；
2. ``POST /models/switch {model_id}``（无 thread_id）行为与 v1.6 完全一致
   （进程级全局）；``GET /models`` 不传 thread_id 返回全局 current；
3. 新会话（threads 无 model_id）走 ``get_default_model()``；切换后偏好持久
   （threads 表，重启/重进仍在）；
4. 生产模式切换他人会话模型 → 403；切换自己会话模型 → 200；
5. ``AgentState.model_id`` 贯通：``graph.run(..., model_id=...)`` 写入状态，
   ``_synthesize_via_llm`` / supervisor 用 ``state.model_id``（None 回退全局）。

**隔离**：主库切 tmp + reload 鉴权栈（生产用例）。

作者：寇豆码（工程师）
"""

from __future__ import annotations

import importlib
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

TEST_JWT_SECRET = "session-models-secret-0123456789abcdef"
TEST_ADMIN_TOKEN = "session-models-admin-token"


def _connect(tmp_db: Path):
    def patched() -> sqlite3.Connection:
        tmp_db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    return patched


def _reload_stack() -> None:
    import api.config as config_mod

    importlib.reload(config_mod)
    import api.services.rbac as rbac_mod

    importlib.reload(rbac_mod)
    import api.services.thread_store as ts_mod

    importlib.reload(ts_mod)
    import api.services.auth as auth_mod

    importlib.reload(auth_mod)
    from api.services import grayscale_admin_service as gas_mod

    importlib.reload(gas_mod)
    import api.services.hitl_audit_service as has_mod

    importlib.reload(has_mod)
    import api.main as main_mod

    importlib.reload(main_mod)


@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """主库切 tmp + init_db，返回 DB 路径。"""
    tmp_db = tmp_path / "session_models.db"
    import mcp_tools.db.database as db_mod
    import api.services.hitl_audit_service as has_mod

    patched = _connect(tmp_db)
    monkeypatch.setattr(db_mod, "get_connection", patched)
    monkeypatch.setattr(has_mod, "get_connection", patched)
    db_mod.init_db()
    return tmp_db


@pytest.fixture
def prod_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """生产模式客户端。"""
    tmp_db = tmp_path / "session_models_prod.db"
    import mcp_tools.db.database as db_mod
    import api.services.hitl_audit_service as has_mod

    patched = _connect(tmp_db)
    monkeypatch.setattr(db_mod, "get_connection", patched)
    monkeypatch.setattr(has_mod, "get_connection", patched)
    db_mod.init_db()

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("ADMIN_TOKEN", TEST_ADMIN_TOKEN)
    _reload_stack()

    import api.main as main_mod

    yield TestClient(main_mod.app, raise_server_exceptions=False)

    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("PRODUCTION", raising=False)
    _reload_stack()


def _token(user_id: str, role: str = "dispatcher") -> str:
    from api.services.auth import issue_test_token

    return issue_test_token(user_id=user_id, extra_claims={"role": role})


def _headers(user_id: str, role: str = "dispatcher") -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(user_id, role)}"}


# ═══════════════════════════════════════════════════════
# 1. 双会话并发模型隔离（DB 层）
# ═══════════════════════════════════════════════════════


def test_two_sessions_models_isolated(
    tmp_db: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """会话 A=deepseek-chat、会话 B=NULL（回退全局），互不影响。"""
    import core.llm_client as llm
    from api.services import thread_store as ts

    monkeypatch.setattr(llm, "_current_model", None)
    default_model = llm.get_default_model()

    store = ts.ThreadStore()
    store.create_thread("t-sess-A", "user-a")
    store.create_thread("t-sess-B", "user-a")
    ts.set_model_for_thread("t-sess-A", "deepseek-chat")

    assert ts.get_model_for_thread("t-sess-A") == "deepseek-chat"
    assert ts.get_model_for_thread("t-sess-B") == default_model
    assert ts.get_model_for_thread("t-sess-A") == "deepseek-chat"  # 不受 B 影响
    print("[PASS] 双会话模型互不影响")


# ═══════════════════════════════════════════════════════
# 2. 全局路径向后兼容（US-2.3）
# ═══════════════════════════════════════════════════════


def test_global_switch_no_thread_id_matches_v16(
    tmp_db: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无 thread_id 的 set_model_for_thread→全局 get_current_model 语义不变。"""
    import core.llm_client as llm
    from api.services.thread_store import resolve_model

    monkeypatch.setattr(llm, "_current_model", None)
    llm.set_current_model("qwen-turbo")
    assert resolve_model(None) == "qwen-turbo"
    # 恢复
    monkeypatch.setattr(llm, "_current_model", None)
    print("[PASS] 无 thread_id 全局兼容")


def test_models_endpoint_without_thread_id_returns_global(
    prod_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /models 不传 thread_id → current 为全局（与 v1.6 一致）。"""
    import core.llm_client as llm

    monkeypatch.setattr(llm, "_current_model", None)
    resp = prod_client.get("/models", headers=_headers("u1"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["current"] == llm.get_default_model()
    assert "thread_id" not in body
    print("[PASS] GET /models 无 thread_id → 全局 current")


# ═══════════════════════════════════════════════════════
# 3. API 会话级切换 + 持久化
# ═══════════════════════════════════════════════════════


def test_switch_model_with_thread_id_persists(
    prod_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """切换会话模型 → threads.model_id 持久（重进/刷新仍在）。"""
    from api.services.thread_store import ThreadStore, get_model_for_thread
    import core.llm_client as llm

    monkeypatch.setattr(llm, "_current_model", None)
    ThreadStore().create_thread("t-persist", "zhangsan")

    resp = prod_client.post(
        "/models/switch",
        json={"model_id": "deepseek-chat", "thread_id": "t-persist"},
        headers=_headers("zhangsan"),
    )
    assert resp.status_code == 200, resp.text[:200]
    body = resp.json()
    assert body["ok"] is True
    assert body["current"] == "deepseek-chat"
    assert body["thread_id"] == "t-persist"

    # 持久化：直接查库（模拟重进会话）
    assert ThreadStore().get_model("t-persist") == "deepseek-chat"
    assert get_model_for_thread("t-persist") == "deepseek-chat"
    print("[PASS] 会话模型切换持久化")


def test_new_session_falls_back_to_default(
    prod_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """新会话（threads 无 model_id）走全局默认模型。"""
    import core.llm_client as llm
    from api.services.thread_store import ThreadStore, get_model_for_thread

    monkeypatch.setattr(llm, "_current_model", None)
    ThreadStore().create_thread("t-new", "zhangsan")
    assert get_model_for_thread("t-new") == llm.get_default_model()
    print("[PASS] 新会话回退默认模型")


def test_get_models_with_thread_id_returns_session_model(
    prod_client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /models?thread_id= → current 为该会话生效模型。"""
    import core.llm_client as llm
    from api.services.thread_store import ThreadStore, set_model_for_thread

    monkeypatch.setattr(llm, "_current_model", None)
    ThreadStore().create_thread("t-q", "zhangsan")
    set_model_for_thread("t-q", "qwen-turbo")

    resp = prod_client.get(
        "/models?thread_id=t-q", headers=_headers("zhangsan"),
    )
    assert resp.status_code == 200, resp.text[:200]
    body = resp.json()
    assert body["current"] == "qwen-turbo"
    assert body["thread_id"] == "t-q"
    print("[PASS] GET /models?thread_id → 会话模型")


# ═══════════════════════════════════════════════════════
# 4. 切换越权
# ═══════════════════════════════════════════════════════


def test_switch_other_users_thread_model_forbidden(
    prod_client: TestClient,
) -> None:
    """生产：切换他人会话模型 → 403。"""
    from api.services.thread_store import ThreadStore

    ThreadStore().create_thread("t-zhang", "zhangsan")
    resp = prod_client.post(
        "/models/switch",
        json={"model_id": "deepseek-chat", "thread_id": "t-zhang"},
        headers=_headers("lisi"),
    )
    assert resp.status_code == 403, (
        f"切换他人会话模型应 403，实际 {resp.status_code}: {resp.text[:200]}"
    )
    print("[PASS] 切换他人会话模型 → 403")


def test_switch_unknown_model_value_error(prod_client: TestClient) -> None:
    """未知模型 ID → 400（校验 AVAILABLE_MODELS）。"""
    resp = prod_client.post(
        "/models/switch",
        json={"model_id": "no-such-model"},
        headers=_headers("u1"),
    )
    assert resp.status_code == 400, (
        f"未知模型应 400，实际 {resp.status_code}: {resp.text[:200]}"
    )
    print("[PASS] 未知模型 → 400")


# ═══════════════════════════════════════════════════════
# 5. AgentState.model_id 贯通
# ═══════════════════════════════════════════════════════


def test_agent_state_carries_model_id() -> None:
    """AgentState 新增 model_id 字段（默认 None）。"""
    from api.schemas import AgentState

    state = AgentState(thread_id="t-x")
    assert state.model_id is None
    state2 = AgentState(thread_id="t-x", model_id="deepseek-chat")
    assert state2.model_id == "deepseek-chat"
    print("[PASS] AgentState.model_id 贯通字段")


def test_graph_run_passes_model_id_into_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """graph.run(..., model_id) 写入 AgentState.model_id。

    用内存图桩验证：不真正编译 LangGraph，而是 monkeypatch
    ``GraphBuilder._ensure_compiled`` + ``graph.ainvoke`` 捕获 initial_state。
    """
    import api.graph as graph_mod
    from api.schemas import AgentState

    captured: dict[str, Any] = {}

    class FakeGraph:
        async def ainvoke(self, initial_state: AgentState, config: dict[str, Any]):
            captured["state"] = initial_state
            captured["config"] = config
            return {"messages": [{"role": "assistant", "content": "ok"}]}

    builder = graph_mod.GraphBuilder.__new__(graph_mod.GraphBuilder)
    builder.graph = FakeGraph()
    builder._compiled = True  # 假设已编译

    import asyncio

    async def main() -> None:
        result = await builder.run(
            "t-x", "hello", display_mode=None, model_id="deepseek-chat",
        )
        return result

    asyncio.run(main())
    state = captured["state"]
    assert isinstance(state, AgentState)
    assert state.model_id == "deepseek-chat"
    assert captured["config"]["configurable"]["thread_id"] == "t-x"
    print("[PASS] graph.run 透传 model_id → AgentState")


def test_synthesize_uses_state_model_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_synthesize_via_llm`` 用 state.model_id 调 achat_completion。"""
    import api.agents.agent_factory as af
    from api.schemas import AgentState

    captured: dict[str, Any] = {}

    async def fake_achat(messages=None, model_id=None, temperature=0.1, **kwargs):
        captured["model_id"] = model_id
        captured["temperature"] = temperature
        return True, "synthesized"

    monkeypatch.setattr(
        "core.llm_client.achat_completion", fake_achat,
    )
    # _synthesize_via_llm 内部 ``from core.llm_client import achat_completion``
    # 是函数内 import，monkeypatch core.llm_client.achat_completion 即可生效
    import core.llm_client as llm

    monkeypatch.setattr(llm, "achat_completion", fake_achat)

    state = AgentState(thread_id="t-x", model_id="deepseek-chat")
    result = asyncio_run(
        af._synthesize_via_llm(state, "diagnosis_agent", [], ["tool-result"]),
    )
    assert result == "synthesized"
    assert captured["model_id"] == "deepseek-chat"
    print("[PASS] _synthesize_via_llm 透传 state.model_id")


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)
