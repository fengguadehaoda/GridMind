"""QA 独立验证脚本 —— 生产模式下"写端点"匿名可达性实证。

背景：路由内省发现除 /chat/stream 外，还有一批 POST 写端点未挂鉴权依赖，
其中 /interrupt/{tid}/approve 是 HITL 高危工具审批闸门。本脚本用真实
HTTP 请求核实：生产模式下匿名调用是否被拦截（401）。

判定口径：
    401              -> 已拦截（安全）
    其它任何状态码    -> 未拦截（请求已进入业务处理，鉴权缺失）

运行：
    python tests/scripts/qa_verify_write_endpoints_auth.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ["APP_ENV"] = "production"
os.environ.setdefault("JWT_SECRET", "qa-verify-secret-not-default-0123456789")
os.environ.setdefault("ADMIN_TOKEN", "qa-verify-admin-token-not-default-0123")
os.environ.setdefault("DEMO_MODE", "true")

from fastapi.testclient import TestClient  # noqa: E402

import api.main as main  # noqa: E402
from api.config import settings  # noqa: E402

TID = "t-qa-probe"

# (method, path, json_body, 说明)
CASES: list[tuple[str, str, dict | None, str]] = [
    ("GET", "/devices", None, "对照组：已鉴权数据端点"),
    ("GET", f"/sessions/{TID}/events", None, "对照组：SSE 读端点(verify_thread_ownership)"),
    ("GET", f"/chat/stream/{TID}?message=hi", None, "SSE 流式对话（R2 修复目标）"),
    ("POST", f"/interrupt/{TID}/approve", {}, "HITL 高危工具审批闸门"),
    ("POST", f"/interrupt/{TID}/reject", {}, "HITL 拒绝"),
    ("POST", f"/interrupt/{TID}/decision", {"decision": "approve"}, "HITL 决策"),
    ("POST", f"/sessions/{TID}/abort", {}, "会话中止"),
    ("POST", f"/sessions/{TID}/pause", {}, "会话暂停"),
    ("POST", f"/sessions/{TID}/resume", {}, "会话恢复"),
    ("POST", f"/sessions/{TID}/rewind", {"to_step": 0}, "会话回溯"),
    ("POST", "/models/switch", {"model": "mock"}, "切换 LLM 模型"),
    ("POST", "/grayscale/set", {"percent": 100}, "灰度比例设置"),
    ("POST", "/grayscale/manual_rollback", {}, "灰度手动回滚"),
    ("POST", "/debug/sync_force", None, "调试强制同步"),
    ("GET", "/metrics", None, "指标暴露"),
    ("GET", "/debug/sync_lag", None, "调试同步延迟"),
]


def run() -> int:
    print(f"settings.is_production = {settings.is_production}\n")
    if not settings.is_production:
        print("!! 未进入生产模式，验证不成立")
        return 2

    client = TestClient(main.app, raise_server_exceptions=False)

    unprotected: list[tuple[str, str, int, str]] = []
    protected: list[tuple[str, str, int, str]] = []

    for method, path, body, note in CASES:
        try:
            if method == "GET":
                r = client.get(path)
            else:
                r = client.post(path, json=body if body is not None else {})
            code = r.status_code
        except Exception as e:  # noqa: BLE001
            print(f"  [异常] {method} {path}: {type(e).__name__}: {e}")
            continue

        if code == 401:
            protected.append((method, path, code, note))
        else:
            unprotected.append((method, path, code, note))

    print("=== 匿名请求被拦截(401) —— 安全 ===")
    for method, path, code, note in protected:
        print(f"  {method:5s} {path:34s} -> {code}  {note}")

    print("\n=== 匿名请求未被拦截 —— 鉴权缺口 ===")
    for method, path, code, note in unprotected:
        print(f"  {method:5s} {path:34s} -> {code}  {note}")

    print(f"\n小结：{len(protected)} 个已拦截 / {len(unprotected)} 个未拦截")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
