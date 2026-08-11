"""R-1b：``core.llm_client`` 全局模型态 / RLock 并发 / 非法 model_id 单测。

此前 ``_current_model`` 全局态、``set_current_model`` 非法 model_id→400
（底层 ``ValueError``）、以及并发 set/get 均无直接单测。本文件补齐：

1. ``get_current_model`` 未设置时回退 ``get_default_model()``；
2. ``set_current_model`` / ``get_current_model`` 读写一致；
3. ``set_current_model`` 非法 model_id（未知 / 空 / None）→ ``ValueError``；
4. RLock 并发：多线程交错 set/get 不抛错、终态合法；
5. ``thread_store.resolve_model`` 回退逻辑：
   ``thread_id=None`` → 全局当前模型；有会话模型 → 会话模型。

隔离：每个用例通过 ``reset_global_model`` fixture 保存/恢复进程级全局态，
不污染其它测试；resolve_model 用例把 SQLite 切到 ``tmp_path``。

运行：
    python -m pytest tests/test_llm_client.py -q
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest


@pytest.fixture
def reset_global_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """保存/恢复 ``core.llm_client._current_model`` 进程级全局态。"""
    import core.llm_client as llm

    saved = llm._current_model
    llm._current_model = None
    yield
    llm._current_model = saved


def _model_ids() -> list[str]:
    import core.llm_client as llm

    return [m["id"] for m in llm.AVAILABLE_MODELS]


def test_get_current_model_default_when_unset(reset_global_model) -> None:
    """未显式设置时回退默认模型（与 get_default_model 一致）。"""
    import core.llm_client as llm

    assert llm._current_model is None
    assert llm.get_current_model() == llm.get_default_model()


def test_set_and_get_current_model(reset_global_model) -> None:
    """set 后 get 读回一致；多次切换覆盖。"""
    import core.llm_client as llm

    llm.set_current_model("qwen-turbo")
    assert llm.get_current_model() == "qwen-turbo"
    llm.set_current_model("deepseek-chat")
    assert llm.get_current_model() == "deepseek-chat"


def test_set_current_model_invalid_raises_value_error(reset_global_model) -> None:
    """非法 model_id（未知 / 空 / None）→ ValueError（HTTP 400 的底层来源）。"""
    import core.llm_client as llm

    for bad in ("not-a-real-model", "", None):
        with pytest.raises(ValueError):
            llm.set_current_model(bad)  # type: ignore[arg-type]
    # 校验失败不改变当前态
    assert llm.get_current_model() == llm.get_default_model()


def test_concurrent_set_get_rlock(reset_global_model) -> None:
    """RLock 并发：多线程交错 set/get 不抛错、终态合法。"""
    import core.llm_client as llm

    ids = _model_ids()
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def worker(idx: int) -> None:
        try:
            barrier.wait(timeout=5)
            for i in range(80):
                target = ids[(idx + i) % len(ids)]
                llm.set_current_model(target)
                got = llm.get_current_model()
                if got not in ids:
                    errors.append(AssertionError(f"非法模型读回: {got!r}"))
        except Exception as e:  # noqa: BLE001 — 收集线程内异常统一断言
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"并发 set/get 出现 {len(errors)} 个异常: {errors[:5]}"
    assert llm.get_current_model() in ids, "终态必须是合法模型"


def test_resolve_model_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """thread_store.resolve_model：thread_id ? 会话模型 : 全局当前模型。"""
    import mcp_tools.db.database as db_module
    import core.llm_client as llm
    from api.services import thread_store as ts

    tmp_db = tmp_path / "llm_resolve.db"

    def patched_conn() -> sqlite3.Connection:
        tmp_db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    monkeypatch.setattr(db_module, "get_connection", patched_conn)
    db_module.init_db()

    llm._current_model = None
    default_model = llm.get_default_model()

    # thread_id=None / 未登记 thread → 全局当前模型
    assert ts.resolve_model(None) == default_model
    assert ts.resolve_model("t-unknown") == default_model

    # 显式设置会话模型 → 会话模型优先
    ts.set_model_for_thread("t-set", "qwen-turbo")
    assert ts.resolve_model("t-set") == "qwen-turbo"

    # 全局切换后，无会话模型的 thread 跟随全局
    llm.set_current_model("deepseek-chat")
    assert ts.resolve_model(None) == "deepseek-chat"
    assert ts.resolve_model("t-set") == "qwen-turbo", "会话模型不受全局切换影响"
