"""QA 独立验证脚本 —— 生产模式下对话链路鉴权实证（P2-1 复核）。

放在 tests/scripts/ 下，pytest.ini 已 --ignore=tests/scripts，不污染主测试套件。

运行：
    APP_ENV=production JWT_SECRET=... ADMIN_TOKEN=... python tests/scripts/qa_verify_chat_auth.py

目的：不靠读代码，而是用 FastAPI TestClient 真实发请求，验证
    1. GET  /devices              （对照组，已知已鉴权）  期望 401
    2. POST /chat                 （工程师称未鉴权）      实测 ?
    3. GET  /chat/stream/{tid}    （工程师称未鉴权）      实测 ?
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 脚本位于 tests/scripts/，需把项目根加入 sys.path 才能 import api.*
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# 必须在 import api.* 之前设置，否则 settings 已用 dev 默认值实例化
os.environ["APP_ENV"] = "production"
os.environ.setdefault("JWT_SECRET", "qa-verify-secret-not-default-0123456789")
os.environ.setdefault("ADMIN_TOKEN", "qa-verify-admin-token-not-default-0123")
os.environ.setdefault("DEMO_MODE", "true")

from fastapi.testclient import TestClient  # noqa: E402

import api.main as main  # noqa: E402
from api.config import settings  # noqa: E402


def main_check() -> int:
    print(f"settings.is_production = {settings.is_production}")
    if not settings.is_production:
        print("!! 无法进入生产模式，验证不成立")
        return 2

    client = TestClient(main.app, raise_server_exceptions=False)

    results: list[tuple[str, str, int, str]] = []

    # 1) 对照组：已知挂了 Depends(verify_jwt_if_prod) 的数据端点
    r = client.get("/devices")
    results.append(("GET", "/devices", r.status_code, "对照组-应401"))

    # 2) POST /chat —— 工程师声称未鉴权
    r = client.post("/chat", json={"message": "hi", "thread_id": "t-qa", "stream": False})
    results.append(("POST", "/chat", r.status_code, "工程师称未鉴权"))

    # 3) GET /chat/stream/{thread_id} —— 工程师声称未鉴权
    r = client.get("/chat/stream/t-qa", params={"message": "hi"})
    results.append(("GET", "/chat/stream/{tid}", r.status_code, "工程师称未鉴权"))

    print("\n=== 匿名请求（不带 Authorization）实测结果 ===")
    for method, path, code, note in results:
        verdict = "已鉴权(401)" if code == 401 else f"未拦截({code})"
        print(f"  {method:5s} {path:26s} -> {code:3d}  [{verdict}]  {note}")

    print("\n=== 结论 ===")
    devices_code = results[0][2]
    chat_code = results[1][2]
    stream_code = results[2][2]

    if devices_code != 401:
        print("  !! 对照组未返回 401，测试环境本身不可信")
        return 2

    if chat_code == 401:
        print("  POST /chat            : 已强制鉴权 —— 工程师 P2-1 描述与实际不符")
    else:
        print(f"  POST /chat            : 匿名可达({chat_code}) —— 工程师描述属实")

    if stream_code == 401:
        print("  GET  /chat/stream/{id}: 已强制鉴权 —— 工程师 P2-1 描述与实际不符")
    else:
        print(f"  GET  /chat/stream/{{id}}: 匿名可达({stream_code}) —— 真实鉴权缺口")

    return 0


if __name__ == "__main__":
    raise SystemExit(main_check())
