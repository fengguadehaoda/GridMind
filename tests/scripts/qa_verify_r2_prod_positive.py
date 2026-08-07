"""QA R2 正向链路探针 —— 生产模式下"带合法 JWT"的请求不被 401 误杀。

R1/R2 的匿名探针只证明了 fail-closed（该拦的拦住了），不能证明 fail-open 侧
正常（该放的放行）。本脚本补齐正向用例：用 auth.issue_test_token 签发合法
JWT，按前端 getAuthHeaders() 的**实际结构** ``Authorization: Bearer <jwt>``
发请求，确认新挂的 Depends 不会误杀合法流量。

判定口径：
    401 -> 合法请求被误杀（BUG）
    其它 -> 通过鉴权（业务码 503/422 属正常，说明已进入处理函数）

输出全 ASCII。

运行：
    python tests/scripts/qa_verify_r2_prod_positive.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ["APP_ENV"] = "production"
os.environ["JWT_SECRET"] = "qa-r2-secret-not-default-0123456789abc"
os.environ["ADMIN_TOKEN"] = "qa-r2-admin-token-not-default-0123"
os.environ.setdefault("DEMO_MODE", "true")

from fastapi.testclient import TestClient  # noqa: E402

import api.main as main  # noqa: E402
from api.config import settings  # noqa: E402
from api.services.auth import issue_test_token  # noqa: E402

TID = "t-qa-pos"


def run() -> int:
    print("settings.is_production =", settings.is_production)
    if not settings.is_production:
        print("!! not production, invalid run")
        return 2

    # 模拟前端 getAuthHeaders(): { Authorization: 'Bearer <jwt>' }
    token = issue_test_token(user_id="qa-user")
    auth_headers = {"Authorization": f"Bearer {token}"}
    admin_headers = {"X-Admin-Token": os.environ["ADMIN_TOKEN"]}

    print("frontend-shaped header:", list(auth_headers.keys()))
    print()

    client = TestClient(main.app, raise_server_exceptions=False)

    # (method, path, body, headers, label)
    cases = [
        ("GET", "/devices", None, auth_headers, "JWT read (control)"),
        ("GET", f"/chat/stream/{TID}?message=hi", None, auth_headers, "JWT SSE stream"),
        ("POST", "/chat", {"message": "hi", "thread_id": TID, "stream": False},
         auth_headers, "JWT chat"),
        ("POST", f"/interrupt/{TID}/approve", {"reason": ""}, auth_headers, "JWT HITL approve"),
        ("POST", f"/interrupt/{TID}/reject", {"reason": ""}, auth_headers, "JWT HITL reject"),
        ("POST", f"/interrupt/{TID}/decision", {"decision": "approve"},
         auth_headers, "JWT HITL decision"),
        ("POST", f"/sessions/{TID}/pause", {}, auth_headers, "JWT pause"),
        ("POST", f"/sessions/{TID}/resume", {}, auth_headers, "JWT resume"),
        ("POST", f"/sessions/{TID}/abort", {}, auth_headers, "JWT abort"),
        ("POST", f"/sessions/{TID}/rewind", {"to_step": 0}, auth_headers, "JWT rewind"),
        ("POST", "/models/switch", {"model_id": "mock"}, admin_headers, "ADMIN model switch"),
        ("POST", "/grayscale/set", {"ratio": 100}, admin_headers, "ADMIN grayscale set"),
        ("POST", "/debug/sync_force", {}, admin_headers, "ADMIN sync force"),
    ]

    killed: list[tuple[str, str, str]] = []
    passed: list[tuple[str, str, int, str]] = []

    for method, path, body, headers, label in cases:
        try:
            if method == "GET":
                r = client.get(path, headers=headers)
            else:
                r = client.post(path, json=body if body is not None else {}, headers=headers)
            code = r.status_code
        except Exception as e:  # noqa: BLE001
            print(f"  [EXC] {method} {path}: {type(e).__name__}: {e}")
            continue

        if code == 401:
            killed.append((method, path, label))
        else:
            passed.append((method, path, code, label))

    print("=== authorized request PASSED auth (not 401) ===")
    for method, path, code, label in passed:
        print(f"  {method:5s} {path:34s} -> {code:3d}   {label}")

    print("\n=== authorized request WRONGLY 401 (BUG) ===")
    if not killed:
        print("  (none)")
    for method, path, label in killed:
        print(f"  {method:5s} {path:34s} -> 401   {label}")

    print(f"\nSummary: {len(passed)} passed-auth / {len(killed)} wrongly-401")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
