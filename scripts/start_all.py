#!/usr/bin/env python3
"""GridMind 一键启动脚本（FR-5）。

启动顺序：
1. 初始化数据库 + 写入种子数据
2. 启动 MCP 工具服务（端口 9901）
3. 启动 FastAPI API 服务（端口 9900）

用法：
    python -m scripts.start_all         # 直接启动
    python -m scripts.start_all --mock   # 以 Mock 模式启动（无需 DashScope Key）
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

# 将项目根目录加入 sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))


def check_key() -> bool:
    """检查 DashScope API Key 是否已配置。"""
    key = os.getenv("DASHSCOPE_API_KEY", "")
    # 默认 .env.example 中的占位 key
    if not key or key == "sk-placeholder":
        return False
    return True


def init_database() -> None:
    """初始化数据库与种子数据。"""
    print("\n═══ [1/3] 初始化数据库 ═══")
    from mcp_tools.db.database import init_db
    from mcp_tools.db.seed_data import seed_all

    init_db()
    seed_all()
    print("✅ 数据库初始化完成\n")


def start_mcp_server() -> subprocess.Popen:
    """启动 MCP 工具服务（子进程）。"""
    print("\n═══ [2/4] 启动 MCP 工具服务（端口 9901） ═══")
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "from mcp_tools.server import start; start()"],
        cwd=str(ROOT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    print(f"  MCP Server PID: {proc.pid}")
    return proc


def start_api_server() -> subprocess.Popen:
    """启动 FastAPI API 服务（子进程）。"""
    print("\n═══ [3/4] 启动 API 服务（端口 9900） ═══")
    from api.config import settings

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "api.main:app",
            "--host", "0.0.0.0",
            "--port", str(settings.api_port),
            "--log-level", "info",
        ],
        cwd=str(ROOT_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    print(f"  API Server PID: {proc.pid}")
    return proc


def start_frontend() -> subprocess.Popen | None:
    """启动 Vue 3 前端开发服务器。"""
    frontend_dir = ROOT_DIR / "web"
    if not (frontend_dir / "package.json").exists():
        print("\n⏭️  前端模块未就绪（web/package.json 不存在），跳过前端启动")
        return None
    print("\n═══ [4/4] 启动前端服务（端口 5173） ═══")
    npm_exec = shutil.which("npm") or "npm"
    proc = subprocess.Popen(
        [npm_exec, "run", "dev"],
        cwd=str(frontend_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    print(f"  Frontend PID: {proc.pid}")
    return proc


def main() -> None:
    parser = argparse.ArgumentParser(description="GridMind 一键启动")
    parser.add_argument("--mock", action="store_true", help="Mock 模式（无需 DashScope Key）")
    parser.add_argument("--no-db-init", action="store_true", help="跳过数据库初始化")
    args = parser.parse_args()

    print("╔══════════════════════════════════════════╗")
    print("║     GridMind — 灵枢电网                   ║")
    print("║     v1.3 — 一键启动                      ║")
    print("╚══════════════════════════════════════════╝")

    # 检查 Key
    has_key = check_key()
    if not has_key and not args.mock:
        print("\n⚠️  未配置 DASHSCOPE_API_KEY")
        print("   启动将使用占位 Key，LLM 相关功能不可用")
        print("   如需完整功能，请设置环境变量或在 .env 中配置")
        print("   或使用 --mock 模式跳过检查\n")
    elif args.mock:
        print("\n🔧 Mock 模式 — LLM 调用将被模拟\n")

    # 数据库初始化
    if not args.no_db_init:
        try:
            init_database()
        except Exception as e:
            print(f"❌ 数据库初始化失败: {e}")
            sys.exit(1)
    else:
        print("⏭️  跳过数据库初始化\n")

    # 启动 MCP 服务
    try:
        mcp_proc = start_mcp_server()
    except Exception as e:
        print(f"❌ MCP 服务启动失败: {e}")
        sys.exit(1)

    # 等待 MCP 就绪
    print("\n⏳ 等待 MCP 服务就绪...")
    time.sleep(3)

    # 启动 API 服务
    try:
        api_proc = start_api_server()
    except Exception as e:
        print(f"❌ API 服务启动失败: {e}")
        mcp_proc.terminate()
        sys.exit(1)

    # 启动前端
    frontend_proc = start_frontend()

    print("\n" + "=" * 50)
    print("✅  GridMind 已启动！")
    print("   API 服务:  http://localhost:9900")
    print("   MCP 服务:  http://localhost:9901")
    print("   前端界面:  http://localhost:5173")
    print("   健康检查:  http://localhost:9900/")
    print("   对话接口:  POST http://localhost:9900/chat")
    print("=" * 50)
    print("\n按 Ctrl+C 停止所有服务...\n")

    # 等待子进程
    try:
        mcp_proc.wait()
        api_proc.wait()
        if frontend_proc:
            frontend_proc.wait()
    except KeyboardInterrupt:
        print("\n\n⏹️  正在停止服务...")
        mcp_proc.terminate()
        api_proc.terminate()
        if frontend_proc:
            frontend_proc.terminate()
        print("✅ 服务已停止")


if __name__ == "__main__":
    main()
