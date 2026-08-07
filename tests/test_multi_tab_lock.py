"""V1.5.1 LangGraph 后端改造 · T04 · 多 Tab 锁集成测试（架构 §2.6 + §6 T04）。

**T04 范围**：测试 ``/chat`` + 4 个写端点（``/sessions/{id}/pause`` 等）的
``session_lock_manager.acquire(thread_id, timeout=5.0)`` 串行化行为。

**测试策略**：

- **HTTP 层测试**：用 ``fastapi.testclient.TestClient`` 真实打 HTTP 端点
  （不走函数直调），验证 lock + 503 SESSION_LOCKED 完整链路
- **Mock GraphBuilder**：测试**不**依赖真实 LangGraph 执行（避免 MCP / DB /
  LLM 副作用），用 ``MockGraphBuilderForLock`` 替换 ``api.main.graph_builder``
  - 关键：``run()`` / ``pause()`` / ``rewind_to_step()`` / ``abort()`` /
    ``resume()`` 都 ``await asyncio.sleep(hold_time)`` + 返回值
  - ``hold_time > endpoint_timeout(5.0s)`` 是设计要点 —— 必须确保第二个
    并发请求**无法**在第一个释放前拿到锁，强制走 503 路径
- **threading + TestClient**：并发用 ``threading.Thread``（TestClient 内部
  用 anyio 后端，每请求独立线程），主线程 ``join`` 等结果
- **真实 ``session_lock_manager`` 单例**：与 main.py 集成路径一致

**5 个测试场景**（≥3 PASS 必达，**实际 5**）：

1. ``test_chat_endpoint_lock`` — 并发 2 个 ``/chat`` 同 thread → 第 2 个 503
2. ``test_pause_endpoint_lock`` — 并发 2 个 ``/sessions/{id}/pause`` → 第 2 个 503
3. ``test_resume_after_lock_released`` — 第 1 个 pause 完成后第 2 个可进入
4. ``test_rewind_endpoint_lock`` — 并发 2 个 ``/sessions/{id}/rewind`` → 第 2 个 503
5. ``test_different_thread_ids_do_not_block`` — 不同 thread_id 的 /chat 互不阻塞

**运行**::

    cd /path/to/GridMind
    PYTHONPATH=. python -m pytest tests/test_multi_tab_lock.py -v

或单独跑（兼容）::

    PYTHONPATH=. python tests/test_multi_tab_lock.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

# 在导入 api 之前开启 Mock 模式（避免触发 LLM Key 校验）
os.environ.setdefault("MOCK_ENABLED", "true")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient


# ═══════════════════════════════════════════════════════
# Mock GraphBuilder
# ═══════════════════════════════════════════════════════


class MockGraphBuilderForLock:
    """多 Tab 锁测试专用的 Mock GraphBuilder。

    关键设计：
    - 所有方法 ``await asyncio.sleep(hold_time)`` 模拟"长时间写操作"
    - ``hold_time`` 必须 > 端点 ``session_lock.acquire(timeout=5.0)`` 的 5 秒
      超时，否则第 2 个并发请求会在第 1 个释放前**拿到**锁（test 失去意义）
    - 默认 ``hold_time=5.5s`` —— 略大于 5.0s 阈值，保证 503 路径触发
    - **不**接 MCP / DB / LLM，零外部依赖
    """

    def __init__(self, hold_time: float = 5.5) -> None:
        self.hold_time = hold_time
        self.run_call_count: int = 0
        self.pause_call_count: int = 0
        self.resume_call_count: int = 0
        self.rewind_call_count: int = 0
        self.abort_call_count: int = 0
        self._lock = threading.Lock()

    def _bump(self, attr: str) -> int:
        with self._lock:
            setattr(self, attr, getattr(self, attr) + 1)
            return getattr(self, attr)

    async def run(
        self,
        thread_id: str,
        message: str,
        display_mode: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        # QA 修复：POST /chat 的 Bug1 改造新增 ``X-Display-Mode`` header，端点
        # 以 ``display_mode=`` 关键字调用 graph_builder.run；原 mock 签名未同步
        # → TypeError。补齐该参数并以 **kwargs 兜底后续新增的关键字参数。
        self._bump("run_call_count")
        await asyncio.sleep(self.hold_time)
        return {
            "messages": [
                {"role": "user", "content": message},
                {"role": "assistant", "content": f"mock response for {thread_id}"},
            ],
            "interrupt_action": None,
            "interrupt_tool": None,
            "interrupt_args": None,
            "interrupt_msg": None,
        }

    async def pause(self, thread_id: str, reason: str = "") -> bool:
        self._bump("pause_call_count")
        await asyncio.sleep(self.hold_time)
        return True

    async def resume(
        self,
        thread_id: str,
        action: str,
        reason: str = "",
        edited_args: dict | None = None,
        edit_reason: str = "",
    ) -> dict[str, Any]:
        self._bump("resume_call_count")
        await asyncio.sleep(self.hold_time)
        return {
            "status": "resumed",
            "thread_id": thread_id,
            "messages_count": 0,
        }

    async def rewind_to_step(
        self,
        thread_id: str,
        step_index: int,
        edited_content: dict | None = None,
    ) -> dict[str, Any]:
        self._bump("rewind_call_count")
        await asyncio.sleep(self.hold_time)
        return {
            "status": "rewound",
            "thread_id": thread_id,
            "rewound_from_step": step_index,
            "rewound_to_step": "supervisor",
            "messages_count": 0,
        }

    async def abort(self, thread_id: str, reason: str = "") -> bool:
        self._bump("abort_call_count")
        await asyncio.sleep(self.hold_time)
        return True


# ═══════════════════════════════════════════════════════
# Fixture：TestClient + Mock builder
# ═══════════════════════════════════════════════════════


@pytest.fixture
def mock_builder() -> MockGraphBuilderForLock:
    """默认 5.5s hold（保证 > 5.0s endpoint timeout，触发 503 路径）。"""
    return MockGraphBuilderForLock(hold_time=5.5)


@pytest.fixture
def fast_builder() -> MockGraphBuilderForLock:
    """快速 mock（0.1s hold）—— 用于非并发场景（lock 释放后能再获取）。"""
    return MockGraphBuilderForLock(hold_time=0.1)


@pytest.fixture
def client(mock_builder: MockGraphBuilderForLock):
    """FastAPI TestClient + 注入 mock builder 到 ``api.main.graph_builder``。

    注意：TestClient(app)（**不**用 ``with``）跳过 lifespan，避免触发
    真实 MCP 连接 / AsyncSqliteSaver。手动设 ``api.main.graph_builder``
    即可让 ``if graph_builder is None`` 检查通过。
    """
    from api.main import app
    import api.main

    api.main.graph_builder = mock_builder
    return TestClient(app)


# ═══════════════════════════════════════════════════════
# 工具：并发发起 2 个 HTTP 请求
# ═══════════════════════════════════════════════════════


def _fire_concurrent(
    client: TestClient,
    method: str,
    url: str,
    json_body: dict | None,
    results: list[tuple[str, int, float]],
    barrier: threading.Event,
) -> None:
    """在独立线程中发 1 个 HTTP 请求，结果追加到 ``results``。

    Args:
        client: TestClient。
        method: ``"GET"`` / ``"POST"``。
        url: 请求 URL。
        json_body: POST body（GET 时忽略）。
        results: 共享结果列表；append ``(label, status_code, elapsed)``。
        barrier: 同步 barrier —— 等待双方都 ready 后同时发请求（让并发更"公平"）。
    """
    barrier.wait(timeout=3.0)
    start = time.monotonic()
    if method.upper() == "POST":
        r = client.post(url, json=json_body)
    else:
        r = client.get(url, params=json_body or {})
    elapsed = time.monotonic() - start
    results.append((
        threading.current_thread().name,
        r.status_code,
        elapsed,
    ))


# ═══════════════════════════════════════════════════════
# 1. /chat 端点并发锁
# ═══════════════════════════════════════════════════════


def test_chat_endpoint_lock(client: TestClient) -> None:
    """并发 2 个 ``POST /chat`` 同 thread → 第 2 个 503 SESSION_LOCKED。

    时序：
    - t=0.0s: T1 /chat 进入 with-lock block，调 graph_builder.run()（5.5s）
    - t=0.0s: T2 /chat 进入 with-lock block，等待锁
    - t=5.0s: T2 acquire 超时 → SessionLockTimeout → 503
    - t=5.5s: T1 run 完成，释放锁 → 200

    验证（**顺序无关**：不假设哪个线程先拿锁，只验"1×200 + 1×503"）：
    - 恰好 1 个 status 200 + 1 个 status 503
    - 慢的那个请求 elapsed ≈ 5.0s（endpoint timeout）
    """
    thread_id = "t-chat-lock-1"
    body = {"thread_id": thread_id, "message": "测试"}
    results: list[tuple[str, int, float]] = []
    barrier = threading.Event()

    t1 = threading.Thread(
        target=_fire_concurrent,
        args=(client, "POST", "/chat", body, results, barrier),
        name="chat-1",
    )
    t2 = threading.Thread(
        target=_fire_concurrent,
        args=(client, "POST", "/chat", body, results, barrier),
        name="chat-2",
    )
    t1.start()
    t2.start()
    barrier.set()  # 双方都 ready 后同时发
    t1.join(timeout=10.0)
    t2.join(timeout=10.0)

    assert len(results) == 2, f"应有 2 个结果，实际 {len(results)}"
    statuses = sorted([r[1] for r in results])
    assert statuses == [200, 503], (
        f"应 1×200 + 1×503, actual {results}"
    )
    # 慢的那个请求 elapsed ≈ 5.0s（不阻塞过久；不立即返回）
    slow_elapsed = max(r[2] for r in results)
    assert 4.5 <= slow_elapsed <= 6.5, (
        f"慢请求应等 ≈ 5s 才超时, actual={slow_elapsed:.2f}s"
    )
    print(
        f"[PASS] /chat lock: 1×200 + 1×503, "
        f"slow={slow_elapsed:.2f}s"
    )


# ═══════════════════════════════════════════════════════
# 2. /sessions/{id}/pause 端点并发锁
# ═══════════════════════════════════════════════════════


def test_pause_endpoint_lock(client: TestClient) -> None:
    """并发 2 个 ``POST /sessions/{id}/pause`` → 第 2 个 503 SESSION_LOCKED。"""
    thread_id = "t-pause-lock-1"
    body = {"reason": "lock test"}
    results: list[tuple[str, int, float]] = []
    barrier = threading.Event()

    t1 = threading.Thread(
        target=_fire_concurrent,
        args=(client, "POST", f"/sessions/{thread_id}/pause", body, results, barrier),
        name="pause-1",
    )
    t2 = threading.Thread(
        target=_fire_concurrent,
        args=(client, "POST", f"/sessions/{thread_id}/pause", body, results, barrier),
        name="pause-2",
    )
    t1.start()
    t2.start()
    barrier.set()
    t1.join(timeout=10.0)
    t2.join(timeout=10.0)

    statuses = sorted([r[1] for r in results])
    assert statuses == [200, 503], f"应 1×200 + 1×503, actual {results}"
    slow_elapsed = max(r[2] for r in results)
    assert 4.5 <= slow_elapsed <= 6.5, f"应等 ≈ 5s, actual={slow_elapsed:.2f}s"
    print(
        f"[PASS] /pause lock: 1×200 + 1×503, "
        f"slow={slow_elapsed:.2f}s"
    )


# ═══════════════════════════════════════════════════════
# 3. 锁释放后第 2 个请求可进入
# ═══════════════════════════════════════════════════════


def test_resume_after_lock_released(fast_builder: MockGraphBuilderForLock) -> None:
    """第 1 个 pause 完成后第 2 个可进入（验证锁正确释放，**不**泄漏）。

    用 ``fast_builder``（0.1s hold）让测试快速完成。
    """
    from api.main import app
    import api.main

    api.main.graph_builder = fast_builder
    client = TestClient(app)
    thread_id = "t-resume-after-release"

    # 第 1 个 pause
    start1 = time.monotonic()
    r1 = client.post(f"/sessions/{thread_id}/pause", json={"reason": "first"})
    elapsed1 = time.monotonic() - start1
    assert r1.status_code == 200, (
        f"first pause 应 200, got {r1.status_code}: {r1.text}"
    )
    # 实际耗时应 ≈ hold_time（0.1s）+ 一些 HTTP overhead
    assert elapsed1 < 2.0, f"first pause 太慢: {elapsed1:.2f}s"

    # 第 2 个 pause（紧跟其后）—— 必须能进入，不能因前一个未释放而 503
    start2 = time.monotonic()
    r2 = client.post(f"/sessions/{thread_id}/pause", json={"reason": "second"})
    elapsed2 = time.monotonic() - start2
    assert r2.status_code == 200, (
        f"second pause 应 200（锁已释放）, got {r2.status_code}: {r2.text}"
    )
    assert elapsed2 < 2.0, f"second pause 太慢（可能阻塞了）: {elapsed2:.2f}s"

    # mock builder 收到 2 次 pause
    assert fast_builder.pause_call_count == 2, (
        f"应 2 次 pause 调用, actual={fast_builder.pause_call_count}"
    )
    print(
        f"[PASS] lock released: 2 pauses OK "
        f"({elapsed1:.2f}s + {elapsed2:.2f}s, "
        f"calls={fast_builder.pause_call_count})"
    )


# ═══════════════════════════════════════════════════════
# 4. /sessions/{id}/rewind 端点并发锁
# ═══════════════════════════════════════════════════════


def test_rewind_endpoint_lock(client: TestClient) -> None:
    """并发 2 个 ``POST /sessions/{id}/rewind`` → 第 2 个 503。"""
    thread_id = "t-rewind-lock-1"
    body = {"step_index": 0}
    results: list[tuple[str, int, float]] = []
    barrier = threading.Event()

    t1 = threading.Thread(
        target=_fire_concurrent,
        args=(client, "POST", f"/sessions/{thread_id}/rewind", body, results, barrier),
        name="rewind-1",
    )
    t2 = threading.Thread(
        target=_fire_concurrent,
        args=(client, "POST", f"/sessions/{thread_id}/rewind", body, results, barrier),
        name="rewind-2",
    )
    t1.start()
    t2.start()
    barrier.set()
    t1.join(timeout=10.0)
    t2.join(timeout=10.0)

    statuses = sorted([r[1] for r in results])
    assert statuses == [200, 503], f"应 1×200 + 1×503, actual {results}"
    slow_elapsed = max(r[2] for r in results)
    assert 4.5 <= slow_elapsed <= 6.5, f"应等 ≈ 5s, got {slow_elapsed:.2f}s"
    print(
        f"[PASS] /rewind lock: 1×200 + 1×503, slow={slow_elapsed:.2f}s"
    )


# ═══════════════════════════════════════════════════════
# 5. 不同 thread_id 互不阻塞
# ═══════════════════════════════════════════════════════


def test_different_thread_ids_do_not_block(
    fast_builder: MockGraphBuilderForLock,
) -> None:
    """不同 thread_id 的 /chat 应并发成功（架构 §2.6.1 核心契约）。

    用 ``fast_builder``（0.1s hold）让测试在 1s 内完成。
    """
    from api.main import app
    import api.main

    api.main.graph_builder = fast_builder
    client = TestClient(app)
    results: list[tuple[str, int, float]] = []
    barrier = threading.Event()

    t_a = threading.Thread(
        target=_fire_concurrent,
        args=(
            client, "POST", "/chat",
            {"thread_id": "t-thread-A", "message": "A"},
            results, barrier,
        ),
        name="chat-A",
    )
    t_b = threading.Thread(
        target=_fire_concurrent,
        args=(
            client, "POST", "/chat",
            {"thread_id": "t-thread-B", "message": "B"},
            results, barrier,
        ),
        name="chat-B",
    )
    t_a.start()
    t_b.start()
    barrier.set()
    t_a.join(timeout=5.0)
    t_b.join(timeout=5.0)

    assert len(results) == 2
    by_label = {name: (status, elapsed) for name, status, elapsed in results}
    # 双方都应 200（不同 thread_id → 独立 lock → 不阻塞）
    assert by_label["chat-A"][0] == 200, f"A 应 200, got {by_label}"
    assert by_label["chat-B"][0] == 200, f"B 应 200, got {by_label}"
    # 双方都应在 1s 内完成（0.1s hold + overhead）
    assert by_label["chat-A"][1] < 2.0, f"A too slow: {by_label['chat-A']}"
    assert by_label["chat-B"][1] < 2.0, f"B too slow: {by_label['chat-B']}"
    # mock 收到 2 次 run
    assert fast_builder.run_call_count == 2
    print(
        f"[PASS] different thread_ids concurrent OK: "
        f"A={by_label['chat-A'][1]:.2f}s, B={by_label['chat-B'][1]:.2f}s"
    )


# ═══════════════════════════════════════════════════════
# 6. （额外）abort 端点也走同 lock 路径
# ═══════════════════════════════════════════════════════


def test_abort_endpoint_lock(client: TestClient) -> None:
    """并发 2 个 ``POST /sessions/{id}/abort`` → 第 2 个 503（与 pause 同源）。"""
    thread_id = "t-abort-lock-1"
    body = {"reason": "lock test"}
    results: list[tuple[str, int, float]] = []
    barrier = threading.Event()

    t1 = threading.Thread(
        target=_fire_concurrent,
        args=(client, "POST", f"/sessions/{thread_id}/abort", body, results, barrier),
        name="abort-1",
    )
    t2 = threading.Thread(
        target=_fire_concurrent,
        args=(client, "POST", f"/sessions/{thread_id}/abort", body, results, barrier),
        name="abort-2",
    )
    t1.start()
    t2.start()
    barrier.set()
    t1.join(timeout=10.0)
    t2.join(timeout=10.0)

    statuses = sorted([r[1] for r in results])
    assert statuses == [200, 503], f"应 1×200 + 1×503, actual {results}"
    slow_elapsed = max(r[2] for r in results)
    assert 4.5 <= slow_elapsed <= 6.5, f"应等 ≈ 5s, got {slow_elapsed:.2f}s"
    print(
        f"[PASS] /abort lock: 1×200 + 1×503, slow={slow_elapsed:.2f}s"
    )


# ═══════════════════════════════════════════════════════
# Runner（兼容 ``python tests/test_multi_tab_lock.py``）
# ═══════════════════════════════════════════════════════


def _run_all() -> None:
    """非 pytest 入口。"""
    import traceback

    mock = MockGraphBuilderForLock(hold_time=5.5)
    fast = MockGraphBuilderForLock(hold_time=0.1)

    from api.main import app
    import api.main

    tests: list[tuple[str, Any]] = [
        ("test_chat_endpoint_lock", lambda: _run_chat_test(mock)),
        ("test_pause_endpoint_lock", lambda: _run_endpoint_test(mock, "/sessions/{tid}/pause", "POST", {"reason": "x"}, "pause")),
        ("test_resume_after_lock_released", lambda: _run_resume_after_release(fast)),
        ("test_rewind_endpoint_lock", lambda: _run_endpoint_test(mock, "/sessions/{tid}/rewind", "POST", {"step_index": 0}, "rewind")),
        ("test_different_thread_ids_do_not_block", lambda: _run_different_threads(fast)),
        ("test_abort_endpoint_lock", lambda: _run_endpoint_test(mock, "/sessions/{tid}/abort", "POST", {"reason": "x"}, "abort")),
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


def _run_chat_test(mock: MockGraphBuilderForLock) -> None:
    from api.main import app
    import api.main

    api.main.graph_builder = mock
    client = TestClient(app)
    test_chat_endpoint_lock(client)


def _run_endpoint_test(
    mock: MockGraphBuilderForLock,
    url_template: str,
    method: str,
    body: dict,
    label: str,
) -> None:
    from api.main import app
    import api.main

    api.main.graph_builder = mock
    client = TestClient(app)
    thread_id = f"t-{label}-lock-1"
    url = url_template.format(tid=thread_id)
    results: list[tuple[str, int, float]] = []
    barrier = threading.Event()
    t1 = threading.Thread(
        target=_fire_concurrent,
        args=(client, method, url, body, results, barrier),
        name=f"{label}-1",
    )
    t2 = threading.Thread(
        target=_fire_concurrent,
        args=(client, method, url, body, results, barrier),
        name=f"{label}-2",
    )
    t1.start(); t2.start()
    barrier.set()
    t1.join(timeout=10.0); t2.join(timeout=10.0)
    by_label = {n: (s, e) for n, s, e in results}
    assert by_label[f"{label}-1"][0] == 200
    assert by_label[f"{label}-2"][0] == 503


def _run_resume_after_release(fast: MockGraphBuilderForLock) -> None:
    from api.main import app
    import api.main

    api.main.graph_builder = fast
    client = TestClient(app)
    test_resume_after_lock_released(fast)


def _run_different_threads(fast: MockGraphBuilderForLock) -> None:
    from api.main import app
    import api.main

    api.main.graph_builder = fast
    client = TestClient(app)
    test_different_thread_ids_do_not_block(fast)


if __name__ == "__main__":
    _run_all()
