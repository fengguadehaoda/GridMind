"""QA R2 回归探针 —— 鉴权修复是否误伤 dev 模式合法链路。

动机：
    R2 修复给 /models/switch、/debug/sync_force、/grayscale/set 挂了
    ``Depends(verify_admin_token)``。但 ``verify_admin_token``（main.py:65）
    与 ``verify_jwt_if_prod``（auth.py:180）语义**不同**：
      - verify_jwt_if_prod : dev 模式直接 return None（放行）
      - verify_admin_token : **无 dev 分支**，任何环境都强制 X-Admin-Token
    因此这三个端点在 dev 模式下也会 401。若前端未带该 header，功能直接坏。

判定口径（dev 模式，不带任何鉴权头）：
    200/422/503 -> 放行（dev 可用）
    401/403     -> 被拦截（dev 亦不可用 -> 若前端未带 header 即为功能回归）

输出全 ASCII，避免 Windows 控制台 GBK 乱码。

运行：
    python tests/scripts/qa_verify_r2_dev_regression.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# 显式 dev 模式（不设 APP_ENV）
os.environ.pop("APP_ENV", None)
os.environ.pop("PRODUCTION", None)
os.environ.setdefault("DEMO_MODE", "true")

from fastapi.testclient import TestClient  # noqa: E402

import api.main as main  # noqa: E402
from api.config import settings  # noqa: E402

TID = "t-qa-dev"

# (method, path, body, 前端调用点, 前端是否已带对应鉴权头)
CASES: list[tuple[str, str, dict | None, str, bool]] = [
    ("POST", "/chat", {"message": "hi", "thread_id": TID, "stream": False},
     "api/chat.ts:75 sendMessage", False),
    ("GET", f"/chat/stream/{TID}?message=hi", None,
     "api/chat.ts:101 streamChat", True),
    ("POST", f"/interrupt/{TID}/approve", {"reason": ""},
     "api/chat.ts:169 approveInterrupt", True),
    ("POST", f"/sessions/{TID}/pause", {},
     "api/chat.ts:258 pauseSession", True),
    ("POST", "/models/switch", {"model_id": "mock"},
     "api/models.ts:18 switchModel", False),
    ("POST", "/grayscale/set", {"ratio": 100},
     "api/metrics.ts:135 (带 X-Admin-Token)", True),
    ("POST", "/debug/sync_force", {},
     "无前端调用方（运维用）", False),
]


def run() -> int:
    print("settings.is_production =", settings.is_production)
    if settings.is_production:
        print("!! 期望 dev 模式，实际为 production，验证不成立")
        return 2
    print()

    client = TestClient(main.app, raise_server_exceptions=False)

    blocked: list[tuple[str, str, int, str, bool]] = []
    allowed: list[tuple[str, str, int, str, bool]] = []

    for method, path, body, caller, fe_has_header in CASES:
        try:
            if method == "GET":
                r = client.get(path)
            else:
                r = client.post(path, json=body if body is not None else {})
            code = r.status_code
        except Exception as e:  # noqa: BLE001
            print(f"  [EXC] {method} {path}: {type(e).__name__}: {e}")
            continue

        row = (method, path, code, caller, fe_has_header)
        if code in (401, 403):
            blocked.append(row)
        else:
            allowed.append(row)

    print("=== dev mode: ALLOWED (dev still usable) ===")
    for method, path, code, caller, _ in allowed:
        print(f"  {method:5s} {path:34s} -> {code:3d}   {caller}")

    print("\n=== dev mode: BLOCKED 401/403 ===")
    if not blocked:
        print("  (none)")
    for method, path, code, caller, fe in blocked:
        flag = "OK (frontend sends header)" if fe else ">>> REGRESSION: frontend sends NO header"
        print(f"  {method:5s} {path:34s} -> {code:3d}   {caller}")
        print(f"        {flag}")

    regressions = [r for r in blocked if not r[4]]
    print(f"\nSummary: {len(allowed)} allowed / {len(blocked)} blocked / "
          f"{len(regressions)} REGRESSION")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
