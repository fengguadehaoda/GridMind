#!/usr/bin/env python3
"""GridMind 一键启动脚本（FR-5）。

启动顺序：
1. 初始化数据库 + 写入种子数据（--no-db-init 跳过）
2. 启动 MCP 工具服务（端口 9901）
3. 启动 FastAPI API 服务（端口 9900）
4. 启动 Vue 3 前端开发服务（端口 5173）

用法：
    python -m scripts.start_all         # 直接启动
    python -m scripts.start_all --mock   # 以 Mock 模式启动（无需 DashScope Key）
    python -m scripts.start_all --no-db-init  # 跳过数据库初始化

启动可靠性（v1.7 起，修复"假成功 / 错误被吞"）：
- Key 读取：顶部显式 load_dotenv()，.env 中的 DASHSCOPE_API_KEY 不再误报缺失
- 端口预检：9900/9901/5173 被占用时明确报错 / 提示，不再默默失败
- 就绪轮询：MCP/API/前端启动后 HTTP 轮询确认就绪，超时打印日志并退出(1)
- 日志落盘：子进程 stdout/stderr 写入 logs/mcp.log、logs/api.log、logs/frontend.log
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# 将项目根目录加入 sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

# ── 显式加载 .env ──────────────────────────────────────────────────────
# 必须在 check_key() / 读取端口之前执行，否则 main() 启动早期读不到 .env 里
# 配置的 DASHSCOPE_API_KEY 等变量，用户明明配了 key 却会被误报"未配置"。
# 与 api/config.py 的 load_dotenv(ROOT_DIR / ".env") 保持一致。
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT_DIR / ".env")

# 当前版本号（⚠️ 每次发版更新此处；同时保持 RELEASE-NOTES.md 同步）
# 说明：仓库 RELEASE-NOTES.md 当前仍停留在 v1.4.0（滞后于代码基线），
# 因此 get_version() 仅在文件版本不低于此值时采用文件值。
CURRENT_VERSION = "1.7.0"

# 前端固定端口（Vite 默认）；API/MCP 端口以 api.config.settings 为准，.env 可覆盖
FRONTEND_PORT_DEFAULT = 5173

LOG_DIR = ROOT_DIR / "logs"
MCP_LOG = LOG_DIR / "mcp.log"
API_LOG = LOG_DIR / "api.log"
FRONTEND_LOG = LOG_DIR / "frontend.log"

# 就绪轮询参数
READY_RETRIES = 10
READY_INTERVAL_S = 1.0
FRONTEND_RETRIES = 15  # Vite 冷启动通常更慢


def get_version() -> str:
    """返回 GridMind 版本号（banner 展示用）。

    优先从 RELEASE-NOTES.md 解析最新版本；仅当解析结果不低于代码基线
    CURRENT_VERSION 时采用（当前仓库 RELEASE-NOTES.md 仍停留在 v1.4.0，
    滞后于代码基线，直接展示会误导用户），否则回退硬编码 CURRENT_VERSION。
    """
    try:
        text = (ROOT_DIR / "RELEASE-NOTES.md").read_text(encoding="utf-8")
        match = re.search(r"^##\s+v(\d+\.\d+(?:\.\d+)?)", text, re.MULTILINE)
        if match:
            parsed = tuple(int(x) for x in match.group(1).split("."))
            current = tuple(int(x) for x in CURRENT_VERSION.split("."))
            if parsed >= current:
                return match.group(1)
    except OSError:
        pass
    return CURRENT_VERSION


def check_key() -> bool:
    """检查 DashScope API Key 是否已配置（.env / 环境变量）。"""
    key = os.getenv("DASHSCOPE_API_KEY", "")
    if not key or key == "sk-placeholder":
        # 兜底：从 api.config 读取（其内部同样 load_dotenv，双保险）
        try:
            from api.config import settings
            key = settings.dashscope_api_key or ""
        except Exception:
            key = ""
    return bool(key) and key != "sk-placeholder"


def init_database() -> None:
    """初始化数据库与种子数据。"""
    print("\n═══ [1/4] 初始化数据库 ═══")
    from mcp_tools.db.database import init_db
    from mcp_tools.db.seed_data import seed_all

    init_db()
    seed_all()
    print("✅ 数据库初始化完成\n")


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """检查本机端口是否已被占用（TCP 连接测试）。

    host 传 "localhost" 时 create_connection 会同时尝试 IPv4/IPv6，
    覆盖 Vite 只监听 [::1] 的场景，避免误判端口空闲。
    """
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def http_ready(url: str, timeout: float = 1.0) -> bool:
    """HTTP 层就绪探测：收到任意 HTTP 响应（含 4xx/5xx）即视为就绪。

    - 连接被拒绝（服务尚未监听）→ False
    - 返回 404/405 等（如 MCP 根路径无路由）→ 说明 HTTP 服务已在监听 → True
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            resp.read()
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def tail_log(log_path: Path, lines: int = 30) -> None:
    """打印日志文件末尾 N 行，便于定位启动失败原因。"""
    if not log_path.exists():
        print(f"  （无日志文件: {log_path}）")
        return
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"  （读取日志失败: {e}）")
        return
    tail = text.splitlines()[-lines:]
    print(f"  ── {log_path} 最近 {len(tail)} 行 ──")
    for line in tail:
        print(f"    | {line}")


def stop_proc(proc: subprocess.Popen | None) -> None:
    """安全终止子进程（Windows 下连带终止子进程树；忽略已退出等异常）。"""
    if proc is None:
        return
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            return
        except Exception:
            pass
    try:
        proc.terminate()
    except Exception:
        pass


def wait_for_ready(
    proc: subprocess.Popen,
    url: str,
    name: str,
    log_path: Path,
    retries: int = READY_RETRIES,
    interval: float = READY_INTERVAL_S,
) -> bool:
    """轮询等待子进程 HTTP 服务就绪。

    子进程提前退出或超过重试次数仍未就绪 → 打印日志片段并返回 False。
    """
    total = retries * interval
    print(f"  ⏳ 等待 {name} 就绪（{url}，最多 {total:.0f}s）...")
    for attempt in range(1, retries + 1):
        if http_ready(url):
            print(f"  ✅ {name} 就绪（第 {attempt} 次探测）")
            return True
        if proc.poll() is not None:
            print(f"  ❌ {name} 子进程已退出（exit code={proc.returncode}）")
            tail_log(log_path)
            return False
        time.sleep(interval)
    print(f"  ❌ {name} 启动超时（{total:.0f}s 内未就绪）")
    tail_log(log_path)
    return False


def start_mcp_server() -> subprocess.Popen:
    """启动 MCP 工具服务（子进程），stdout/stderr 写入 logs/mcp.log。"""
    from api.config import settings

    print(f"\n═══ [2/4] 启动 MCP 工具服务（端口 {settings.mcp_port}） ═══")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = open(MCP_LOG, "a", encoding="utf-8")
    log_file.write(
        f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} start_all 启动 MCP =====\n"
    )
    log_file.flush()
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "from mcp_tools.server import start; start()"],
        cwd=str(ROOT_DIR),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    print(f"  MCP Server PID: {proc.pid}")
    return proc


def start_api_server() -> subprocess.Popen:
    """启动 FastAPI API 服务（子进程），stdout/stderr 写入 logs/api.log。"""
    from api.config import settings

    print(f"\n═══ [3/4] 启动 API 服务（端口 {settings.api_port}） ═══")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = open(API_LOG, "a", encoding="utf-8")
    log_file.write(
        f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} start_all 启动 API =====\n"
    )
    log_file.flush()
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "api.main:app",
            "--host", "0.0.0.0",
            "--port", str(settings.api_port),
            "--log-level", "info",
        ],
        cwd=str(ROOT_DIR),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    print(f"  API Server PID: {proc.pid}")
    return proc


def start_frontend() -> subprocess.Popen | None:
    """启动 Vue 3 前端开发服务器（node_modules 缺失时给出提示与选择）。"""
    frontend_dir = ROOT_DIR / "web"
    if not (frontend_dir / "package.json").exists():
        print("\n⏭️  前端模块未就绪（web/package.json 不存在），跳过前端启动")
        return None

    # 注意：Vite 默认只监听 [::1]（IPv6 localhost），故用 "localhost"
    # 同时覆盖 IPv4/IPv6，避免误判端口空闲
    if port_in_use(FRONTEND_PORT_DEFAULT, "localhost"):
        print(f"\n⚠️  端口 {FRONTEND_PORT_DEFAULT} 已被占用（可能已有前端在运行）")
        print("   ⏭️  跳过前端启动，可自行访问 http://localhost:5173\n")
        return None

    if not (frontend_dir / "node_modules").exists():
        print("\n⚠️  前端依赖未安装（web/node_modules 不存在）")
        print("   请先执行: cd web && npm install")
        choice = input("   继续尝试启动前端？[y/N] ").strip().lower()
        if choice not in ("y", "yes"):
            print("   ⏭️  已跳过前端启动\n")
            return None
        print("   继续尝试（缺少依赖可能启动失败，详见 logs/frontend.log）...\n")

    print(f"\n═══ [4/4] 启动前端服务（端口 {FRONTEND_PORT_DEFAULT}） ═══")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = open(FRONTEND_LOG, "a", encoding="utf-8")
    log_file.write(
        f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} start_all 启动前端 =====\n"
    )
    log_file.flush()
    npm_exec = shutil.which("npm") or "npm"
    proc = subprocess.Popen(
        [npm_exec, "run", "dev"],
        cwd=str(frontend_dir),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    print(f"  Frontend PID: {proc.pid}")
    return proc


def check_mcp_tools(api_base_url: str) -> None:
    """API 就绪后探测根端点，打印 MCP 工具连接情况（tools=0 时给出提示）。"""
    print("  🔍 探测 API 根端点，确认 MCP 工具连接状态...")
    try:
        with urllib.request.urlopen(f"{api_base_url}/", timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        count = int(data.get("mcp_tools_count", 0))
        connected = bool(data.get("mcp_connected", False))
        if connected and count > 0:
            print(f"  ✅ API 已连接 MCP（工具数: {count}）")
        else:
            print("  ⚠️  API 未连接上 MCP（tools=0），LLM 工具调用将不可用")
            print("     请查看 logs/mcp.log 与 logs/api.log 定位原因")
    except Exception as e:
        print(f"  ⚠️  无法探测 MCP 状态: {e}")


def check_register_endpoint(api_base_url: str) -> None:
    """API 就绪后校验 openapi.json 是否含 /auth/register（旧 uvicorn 检测）。

    背景（可观测性盲区修复）：用户注册报 404 "Not Found" 的根因是打到的
    API 实例不含 /auth/register（旧 uvicorn 未重启 / 端口错配），而非
    前端或源码问题。本函数在启动时就绪轮询通过后**额外**检查一次——
    只附加 stdout 警告，**不**改变启动成功判断（仍以 ``/`` 200 通过为准）。
    """
    print("  🔍 校验 API 是否包含注册端点 /auth/register ...")
    try:
        with urllib.request.urlopen(f"{api_base_url}/openapi.json", timeout=5) as resp:
            spec = json.loads(resp.read().decode("utf-8"))
        paths = spec.get("paths", {})
        if "/auth/register" in paths:
            print("  ✅ API 包含注册端点 /auth/register")
        else:
            print("  ⚠ 当前 API 实例不含 /auth/register（可能是旧 uvicorn 未重启）")
            print("    建议 Ctrl+C 重跑 python -m scripts.start_all")
    except Exception as e:
        print(f"  ⚠️  无法校验注册端点（openapi.json 获取失败: {e}）")


def precheck_ports(api_port: int, mcp_port: int) -> bool:
    """启动前端口预检：API/MCP 端口被占用则明确报错并返回 False。"""
    problems: list[str] = []
    for name, port in (("API", api_port), ("MCP", mcp_port)):
        if port_in_use(port):
            problems.append(f"  端口 {port}（{name} 服务）已被占用")
    if problems:
        print("\n❌ 端口预检失败，以下端口已被占用：")
        for p in problems:
            print(p)
        print("  请先停止占用这些端口的旧进程（任务管理器结束 python/node 进程）后重试")
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="GridMind 一键启动")
    parser.add_argument("--mock", action="store_true", help="Mock 模式（无需 DashScope Key）")
    parser.add_argument("--no-db-init", action="store_true", help="跳过数据库初始化")
    args = parser.parse_args()

    # 服务端口（与 api.config 保持一致，.env 可覆盖）
    from api.config import settings

    api_port = int(settings.api_port)
    mcp_port = int(settings.mcp_port)

    # --mock 模式下向子进程传播 MOCK_ENABLED=true（子进程继承后，
    # 其 load_dotenv override=False 不会覆盖），保证 LLM 调用确实走 Mock 路径
    if args.mock:
        os.environ["MOCK_ENABLED"] = "true"

    # Banner
    version_line = f"v{get_version()} — 一键启动"
    pad = max(0, 40 - 5 - len(version_line))
    print("╔══════════════════════════════════════════╗")
    print("║     GridMind — 灵枢电网                   ║")
    print(f"║     {version_line}{' ' * pad}║")
    print("╚══════════════════════════════════════════╝")

    # 端口预检（9900/9901 被占用 → 明确报错退出，避免"假成功"）
    if not precheck_ports(api_port, mcp_port):
        sys.exit(1)

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

    # ── 启动 MCP 服务并等待就绪 ──────────────────────
    try:
        mcp_proc = start_mcp_server()
    except Exception as e:
        print(f"❌ MCP 服务启动失败: {e}")
        sys.exit(1)

    if not wait_for_ready(
        mcp_proc,
        f"http://127.0.0.1:{mcp_port}/",
        "MCP 服务",
        MCP_LOG,
    ):
        stop_proc(mcp_proc)
        sys.exit(1)

    # ── 启动 API 服务并等待就绪 ──────────────────────
    try:
        api_proc = start_api_server()
    except Exception as e:
        print(f"❌ API 服务启动失败: {e}")
        stop_proc(mcp_proc)
        sys.exit(1)

    if not wait_for_ready(
        api_proc,
        f"http://127.0.0.1:{api_port}/",
        "API 服务",
        API_LOG,
    ):
        stop_proc(mcp_proc)
        stop_proc(api_proc)
        sys.exit(1)

    # API 就绪后探测 MCP 工具数（tools=0 时给出提示，问题 5）
    check_mcp_tools(f"http://127.0.0.1:{api_port}")

    # API 就绪后校验 /auth/register 端点（旧 uvicorn 未重启 → 前端注册 404，
    # 只附加警告，不改变启动成功判断）
    check_register_endpoint(f"http://127.0.0.1:{api_port}")

    # ── 启动前端 ────────────────────────────────────
    frontend_proc = start_frontend()
    frontend_ready = False
    if frontend_proc is not None:
        frontend_ready = wait_for_ready(
            frontend_proc,
            f"http://localhost:{FRONTEND_PORT_DEFAULT}/",
            "前端服务",
            FRONTEND_LOG,
            retries=FRONTEND_RETRIES,
            interval=READY_INTERVAL_S,
        )
        if not frontend_ready:
            # 前端失败不阻断 MCP/API（非致命），仅明确提示
            print("  ⚠️  前端未能就绪，MCP/API 服务不受影响，可稍后手动启动前端")

    print("\n" + "=" * 50)
    if frontend_ready:
        print("✅  GridMind 已启动！")
    else:
        print("✅  GridMind 核心服务已启动（前端未就绪，详见上方提示）")
    print(f"   API 服务:  http://localhost:{api_port}")
    print(f"   MCP 服务:  http://localhost:{mcp_port}")
    if frontend_proc is None:
        print("   前端界面:  （已跳过，见上方提示）")
    elif frontend_ready:
        print(f"   前端界面:  http://localhost:{FRONTEND_PORT_DEFAULT}")
    else:
        print("   前端界面:  （未就绪，见 logs/frontend.log）")
    print(f"   健康检查:  http://localhost:{api_port}/")
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
        stop_proc(mcp_proc)
        stop_proc(api_proc)
        stop_proc(frontend_proc)
        print("✅ 服务已停止")


if __name__ == "__main__":
    main()
