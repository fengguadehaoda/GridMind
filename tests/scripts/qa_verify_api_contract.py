"""QA 独立验证脚本 —— 前端调用端点的后端契约存在性实证。

注意：FastAPI 0.139 的 include_router 采用惰性 _IncludedRouter，
被包含的子路由不会出现在 app.routes 中，静态枚举会漏判。
因此本脚本一律用**真实请求**判定端点是否存在（404 = 后端无此路由）。

判定口径：
    404  -> 后端不存在该端点（前后端契约不一致）
    其它 -> 端点存在（401/403/422/503 等均说明已进入路由匹配）

运行：
    python tests/scripts/qa_verify_api_contract.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.setdefault("DEMO_MODE", "true")

from fastapi.testclient import TestClient  # noqa: E402

import api.main as main  # noqa: E402

TID = "t-qa-probe"
DOC = "doc-qa-probe"

# 前端源码中实际发起的请求（method, path, 前端调用点）
CASES: list[tuple[str, str, str]] = [
    ("POST", "/chat", "api/chat.ts:75 sendMessage"),
    ("GET", f"/chat/stream/{TID}", "api/chat.ts:99 streamChat"),
    ("GET", f"/sessions/{TID}/events", "composables/useSseStream.ts"),
    ("POST", f"/hitl/{TID}/approve", "api/chat.ts:374 hitlApprove"),
    ("POST", f"/hitl/{TID}/reject", "api/chat.ts:391 hitlReject"),
    ("GET", "/audit/pending-count", "api/chat.ts:339 fetchPendingHitlCount"),
    ("GET", f"/sessions/{TID}/checkpoints", "api/chat.ts:326 fetchCheckpoints"),
    ("GET", "/audit/hitl", "api/chat.ts:360 fetchAuditDecisions"),
    ("POST", f"/interrupt/{TID}/approve", "后端 HITL 审批"),
    ("GET", "/api/knowledge/feature-intro", "composables/useFeatureIntro.ts"),
    ("POST", "/api/knowledge/upload", "api/knowledgeUpload.ts:51"),
    ("GET", "/api/knowledge/uploads", "api/knowledgeUpload.ts:68"),
    ("DELETE", f"/api/knowledge/uploads/{DOC}", "api/knowledgeUpload.ts:82"),
    ("GET", "/devices", "设备列表"),
]


def run() -> int:
    client = TestClient(main.app, raise_server_exceptions=False)

    missing: list[tuple[str, str, str]] = []
    present: list[tuple[str, str, int, str]] = []

    for method, path, caller in CASES:
        try:
            r = client.request(method, path, json={} if method in ("POST", "PUT") else None)
            code = r.status_code
        except Exception as e:  # noqa: BLE001
            print(f"  [异常] {method} {path}: {type(e).__name__}: {e}")
            continue

        if code == 404:
            missing.append((method, path, caller))
        else:
            present.append((method, path, code, caller))

    print("=== 端点存在（非 404）===")
    for method, path, code, caller in present:
        print(f"  {method:6s} {path:38s} -> {code}   <- {caller}")

    print("\n=== 端点缺失（404，前端调用但后端无此路由）===")
    if not missing:
        print("  （无）")
    for method, path, caller in missing:
        print(f"  {method:6s} {path:38s} -> 404 <- {caller}")

    print(f"\n小结：{len(present)} 存在 / {len(missing)} 缺失")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
