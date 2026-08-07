#!/usr/bin/env python3
"""GridMind 一键启动脚本 v2（v1.5.1 升级版）。

改进（vs. scripts/start_all.py）：
- ✅ 实时日志流式输出（不丢失）
- ✅ HTTP 就绪检查（不是 sleep）
- ✅ 自动打开浏览器到前端
- ✅ 优雅 Ctrl+C 关闭所有子进程
- ✅ 端口冲突检测
- ✅ 彩色输出（终端支持时）
- ✅ Neo4j Docker 一键启停（可选）
- ✅ 失败时自动清理已启动的进程
- ✅ 自动为前端签发 dev JWT（打通 v1.5.1 SSE 鉴权）

用法：
    python -m scripts.quickstart                # 完整启动（API + MCP + Web + 自动开浏览器）
    python -m scripts.quickstart --mock          # Mock 模式（无需 DashScope Key）
    python -m scripts.quickstart --no-web        # 不启前端
    python -m scripts.quickstart --no-browser    # 不自动开浏览器
    python -m scripts.quickstart --no-db-init    # 跳过数据库初始化
    python -m scripts.quickstart --neo4j          # 同时启动 Neo4j Docker 容器
    python -m scripts.quickstart --stop          # 停止所有相关进程
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Iterable

# ── 路径与配置 ────────────────────────────────────────────
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

# D10：端口读取环境变量（API_PORT / MCP_PORT），默认 9900/9901，避免硬编码
API_PORT = int(os.getenv("API_PORT", "9900"))
MCP_PORT = int(os.getenv("MCP_PORT", "9901"))
WEB_PORT = 5173
API_DOCS = f"http://localhost:{API_PORT}/docs"
API_BASE = f"http://localhost:{API_PORT}"
WEB_URL = f"http://localhost:{WEB_PORT}"

# ANSI 颜色（终端支持时）
class C:
    G = "\033[92m"   # green
    Y = "\033[93m"   # yellow
    R = "\033[91m"   # red
    B = "\033[94m"   # blue
    M = "\033[95m"   # magenta
    CY = "\033[96m"  # cyan
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RST = "\033[0m"

USE_COLOR = sys.stdout.isatty() and os.getenv("NO_COLOR") is None

def cprint(text: str, color: str) -> None:
    """彩色打印（不支持时回退到纯文本）。"""
    if USE_COLOR:
        print(f"{color}{text}{C.RST}")
    else:
        print(text)


# ── 工具函数 ──────────────────────────────────────────────
def check_port(port: int, label: str) -> bool:
    """检查端口是否已被占用。"""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", port)) == 0:
            cprint(f"⚠️  端口 {port} ({label}) 已被占用", C.Y)
            return False
    return True


def http_ready(url: str, timeout_s: int = 30, label: str = "") -> bool:
    """HTTP 探活（轮询直到 2xx 或超时）。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if 200 <= resp.status < 500:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.5)
        except Exception:
            time.sleep(0.5)
    return False


def stream_output(proc: subprocess.Popen, label: str, color: str) -> None:
    """实时打印子进程输出（独立线程）。"""
    if proc.stdout is None:
        return
    for line in iter(proc.stdout.readline, ""):
        if not line:
            break
        line = line.rstrip()
        if USE_COLOR:
            sys.stdout.write(f"{color}[{label}]{C.RST} {line}\n")
        else:
            sys.stdout.write(f"[{label}] {line}\n")
        sys.stdout.flush()


def _resolve_exe(name: str) -> str:
    """Windows 上把 ``npm`` / ``node`` 解析为真实可执行文件路径（含 .cmd/.bat）。

    Windows ``CreateProcessA`` 在 ``shell=False`` 下不会自动应用 ``PATHEXT``，
    直接传 ``["npm", ...]`` 会触发 ``WinError 2: 系统找不到指定的文件``。
    通过 ``shutil.which`` 查到 .cmd / .bat 完整路径再传给 Popen。
    """
    if sys.platform != "win32" or name.lower().endswith((".exe", ".cmd", ".bat", ".ps1")):
        return name
    import shutil
    return shutil.which(name) or name


def spawn(label: str, cmd: list[str], cwd: Path, env: dict | None = None, color: str = C.CY) -> subprocess.Popen:
    """启动子进程并后台线程打印输出。"""
    cprint(f"  → 启动 {label}: {' '.join(cmd[:3])}...", color)
    # Windows 上把命令名解析为完整 .cmd/.exe 路径
    resolved = [_resolve_exe(cmd[0]), *cmd[1:]]
    proc = subprocess.Popen(
        resolved,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env or os.environ.copy(),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    t = threading.Thread(target=stream_output, args=(proc, label, color), daemon=True)
    t.start()
    return proc


# ── 启动步骤 ──────────────────────────────────────────────
def _candidate_python_paths() -> list[Path]:
    """列出常见 Python 3.13+ 可执行文件位置（按优先级）。"""
    candidates: list[Path] = []
    if os.name == "nt":
        candidates += [
            Path("C:/Python313/python.exe"),
            Path("C:/Program Files/Python313/python.exe"),
            Path("C:/Users") / os.getenv("USERNAME", "") / "AppData/Local/Programs/Python/Python313/python.exe",
            Path.home() / ".workbuddy/binaries/python/versions/3.13.12/python.exe",
            Path.home() / ".workbuddy/binaries/python/versions/3.13.14/python.exe",
        ]
    else:
        candidates += [
            Path("/usr/bin/python3.13"),
            Path("/usr/local/bin/python3.13"),
            Path("/opt/homebrew/bin/python3.13"),
        ]
    return [p for p in candidates if p.exists()]


def find_python_313() -> str | None:
    """尝试定位 Python 3.13+ 可执行文件。

    顺序：
    1. 环境变量 ``GRIDMIND_PYTHON`` 或 ``WORKBUDDY_PYTHON_BIN``
    2. 当前解释器已经是 3.13+
    3. 常见安装路径（Windows / POSIX）
    4. Windows 下 ``py -3.13`` launcher
    """
    for env in ("GRIDMIND_PYTHON", "WORKBUDDY_PYTHON_BIN"):
        p = os.getenv(env)
        if p and Path(p).exists():
            return p

    if sys.version_info >= (3, 13):
        return sys.executable

    for c in _candidate_python_paths():
        return str(c)  # 第一个存在的

    if os.name == "nt":
        try:
            out = subprocess.check_output(
                ["py", "-3.13", "-c", "import sys; print(sys.executable)"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
            if out and Path(out).exists():
                return out
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass

    return None


def check_python_version() -> None:
    """检查 Python 版本；不满足则自动 fallback 到 3.13。

    - 当前 >= 3.13  → 直接通过
    - 当前 3.10~3.12 且本机有 3.13 → ``os.execv`` 用 3.13 重新跑自己
    - 当前 < 3.10  → 直接退出
    - 当前 3.10~3.12 且没 3.13 → 友好报错 + 安装指引
    """
    py_ver = sys.version_info

    if py_ver >= (3, 13):
        cprint(f"  ✓ Python {py_ver.major}.{py_ver.minor}", C.G)
        return

    if py_ver < (3, 10):
        cprint(f"❌ Python {py_ver.major}.{py_ver.minor} 太旧（要求 >= 3.10，推荐 3.13）", C.R)
        sys.exit(1)

    target = find_python_313()
    if target:
        cprint(f"⚠️  当前 Python {py_ver.major}.{py_ver.minor}，自动用 {target} 重启脚本", C.Y)
        # 关键：把 ROOT_DIR 注入 PYTHONPATH，否则 execv 后的子进程用
        # `python -m scripts.quickstart` 找不到 scripts 包（"No module named"）
        existing_pp = os.environ.get("PYTHONPATH", "")
        os.environ["PYTHONPATH"] = (
            str(ROOT_DIR) + (os.pathsep + existing_pp if existing_pp else "")
        )
        try:
            # Windows 上 os.execv 不会给含空格的 argv 加引号，改用 list 形式的 subprocess.run（内部 list2cmdline 会正确转义）
            if os.name == "nt":
                proc = subprocess.run([target, *sys.argv])
                sys.exit(proc.returncode)
            else:
                os.execv(target, [target, *sys.argv])
        except OSError as e:
            cprint(f"❌ 无法用 {target} 重启: {e}", C.R)
            sys.exit(1)
        # execv 不会返回；走到这里说明 fallback 失败
        cprint(f"❌ Python fallback 失败，请手动用 3.13 启动", C.R)
        sys.exit(1)

    cprint(f"❌ Python {py_ver.major}.{py_ver.minor} < 3.13（项目推荐 3.13）", C.R)
    cprint("   安装方案：", C.Y)
    cprint("   1)  python.org 下载 Python 3.13", C.Y)
    cprint("   2)  winget install Python.Python.3.13", C.Y)
    cprint("   3)  或设 GRIDMIND_PYTHON=<某个 3.13 解释器路径>", C.Y)
    sys.exit(1)


def check_dependencies() -> None:
    """检查 Python 版本 + Node + npm。"""
    # 先单独处理 Python（可能触发 os.execv 自重启）
    check_python_version()

    # 检查 Node / npm
    try:
        node_ver = subprocess.check_output(["node", "--version"], text=True).strip()
        cprint(f"  ✓ Node {node_ver}", C.G)
    except (FileNotFoundError, subprocess.CalledProcessError):
        cprint("⚠️  Node.js 未找到，前端可能无法启动", C.Y)

    try:
        npm_ver = subprocess.check_output(["npm", "--version"], text=True).strip()
        cprint(f"  ✓ npm {npm_ver}", C.G)
    except (FileNotFoundError, subprocess.CalledProcessError):
        cprint("⚠️  npm 未找到", C.Y)


def _load_dotenv(env_path: Path) -> bool:
    """极简 .env 加载（无外部依赖）。

    只解析 ``KEY=VALUE`` 行，忽略空行和 ``#`` 注释；不覆盖已存在的环境变量。
    返回是否有任何 key 被注入。
    """
    if not env_path.exists():
        return False
    injected = False
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
                injected = True
    except Exception:
        return False
    return injected


def check_key() -> bool:
    """检查 DashScope / DeepSeek API Key。

    优先级：环境变量 > ``.env`` 文件（避免用户实际有 key 但脚本误报）。
    """
    # 先把 .env 注入到 os.environ
    _load_dotenv(ROOT_DIR / ".env")
    dash = os.getenv("DASHSCOPE_API_KEY", "")
    deep = os.getenv("DEEPSEEK_API_KEY", "")
    has_dash = bool(dash) and dash != "sk-placeholder"
    has_deep = bool(deep) and deep.startswith("sk-")
    return has_dash or has_deep


def check_all_keys() -> tuple[bool, bool]:
    """返回 ``(has_dashscope, has_deepseek)``，用于给用户更精准的提示。"""
    _load_dotenv(ROOT_DIR / ".env")
    dash = os.getenv("DASHSCOPE_API_KEY", "")
    deep = os.getenv("DEEPSEEK_API_KEY", "")
    return (
        bool(dash) and dash != "sk-placeholder",
        bool(deep) and deep.startswith("sk-"),
    )


def init_database() -> None:
    """初始化 SQLite + 种子数据。"""
    cprint("\n═══ [1/5] 数据库初始化 ═══", C.B)
    try:
        from mcp_tools.db.database import init_db
        from mcp_tools.db.seed_data import seed_all
        init_db()
        seed_all()
        cprint("  ✓ 数据库初始化完成", C.G)
    except Exception as e:
        cprint(f"❌ 数据库初始化失败: {e}", C.R)
        sys.exit(1)


def start_mcp_server() -> subprocess.Popen:
    """启动 MCP 服务。"""
    cprint("\n═══ [2/5] MCP 服务 ═══", C.B)
    return spawn(
        "MCP",
        [sys.executable, "-c", "from mcp_tools.server import start; start()"],
        ROOT_DIR,
        color=C.M,
    )


def start_api_server() -> subprocess.Popen:
    """启动 FastAPI。"""
    cprint("\n═══ [3/5] FastAPI 服务 ═══", C.B)
    return spawn(
        "API",
        [sys.executable, "-m", "uvicorn", "api.main:app",
         "--host", "0.0.0.0", "--port", str(API_PORT), "--log-level", "info"],
        ROOT_DIR,
        color=C.CY,
    )


# dev JWT 签发脚本：在后端解释器里跑，保证 secret/issuer/algorithm 与后端完全一致
_DEV_JWT_SNIPPET = (
    "from api.config import settings; import jwt, time; "
    "print(jwt.encode("
    "{'sub':'dev','user_id':'dev','iss':settings.jwt_issuer,"
    "'exp':int(time.time())+31536000,'iat':int(time.time())}, "
    "settings.jwt_secret, algorithm=settings.jwt_algorithm))"
)

_DEV_JWT_PLACEHOLDER = "gridmind-dev-token"
_DEV_JWT_KEY = "VITE_DEV_JWT_TOKEN"

_DEV_ENV_HEADER = (
    "# ─────────────────────────────────────────────────\n"
    "# GridMind Web 端开发环境配置\n"
    "# 本文件中的 dev JWT 由 scripts/quickstart.py 自动签发\n"
    "# （与后端 api/config.py 的 secret/issuer/algorithm 保持一致）\n"
    "# 生产部署时务必替换为真实登录流获得的 token\n"
    "# ─────────────────────────────────────────────────\n"
)


def _read_env_value(env_path: Path, key: str) -> str:
    """从 .env 文件读取指定 key 的值（不存在返回空串）。"""
    if not env_path.exists():
        return ""
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def _looks_like_jwt(token: str) -> bool:
    """粗判是否是合法 JWT：非空、非占位、且含 >=2 个 '.'。"""
    return bool(token) and token != _DEV_JWT_PLACEHOLDER and token.count(".") >= 2


def _write_env_value(env_path: Path, key: str, value: str) -> None:
    """写回 .env：仅替换/追加 ``key=value`` 行，保留其它内容。"""
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    else:
        env_path.parent.mkdir(parents=True, exist_ok=True)
        lines = _DEV_ENV_HEADER.splitlines()

    replaced = False
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.partition("=")[0].strip() == key:
            lines[i] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"{key}={value}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ensure_dev_jwt(frontend_dir: Path) -> None:
    """确保 ``web/.env`` 里有一个后端认得的 dev JWT（打通 SSE 鉴权）。

    v1.5.1 T06 给 ``GET /sessions/{thread_id}/events`` 加了
    ``verify_thread_ownership`` 依赖，会对 ``Authorization: Bearer`` 头做严格
    ``jwt.decode``。前端默认占位值 ``gridmind-dev-token`` 不是合法 JWT，
    会导致 SSE 直接 401（暂停/重跑/工单事件全部收不到）。

    策略：
    - 已有合法 JWT（含 >=2 个 '.' 且非占位）→ 不覆盖用户的真实 token
    - 否则用后端解释器 + ``api.config.settings`` 现场签发一个 1 年有效期的 dev JWT
    - 不带 ``thread_id`` claim：后端视其为通用用户 token，可访问该用户全部 thread
    """
    env_path = frontend_dir / ".env"
    current = _read_env_value(env_path, _DEV_JWT_KEY)
    if _looks_like_jwt(current):
        cprint("  ✓ 前端已存在有效 dev JWT，跳过签发", C.DIM)
        return

    try:
        token = subprocess.check_output(
            [sys.executable, "-c", _DEV_JWT_SNIPPET],
            cwd=str(ROOT_DIR),
            env=os.environ.copy(),
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
        if not _looks_like_jwt(token):
            raise RuntimeError(f"签发结果不是合法 JWT: {token[:80]}")
        _write_env_value(env_path, _DEV_JWT_KEY, token)
        cprint("  ✓ 已为前端签发 dev JWT（SSE 鉴权打通）", C.G)
    except Exception as e:
        cprint(f"  ⚠️  无法签发 dev JWT（SSE 可能 401），请确认 PyJWT 已安装: {e}", C.Y)


def start_frontend() -> subprocess.Popen | None:
    """启动 Vite dev server。"""
    frontend_dir = ROOT_DIR / "web"
    if not (frontend_dir / "package.json").exists():
        cprint("  ⏭️  web/package.json 不存在，跳过前端", C.Y)
        return None

    node_modules = frontend_dir / "node_modules"
    if not node_modules.exists():
        cprint("  ⚠️  node_modules 未安装，先执行 npm install...", C.Y)
        cprint("  → npm install（首次会慢）", C.Y)
        try:
            subprocess.run(["npm", "install"], cwd=str(frontend_dir), check=True, timeout=600)
            cprint("  ✓ npm install 完成", C.G)
        except subprocess.TimeoutExpired:
            cprint("❌ npm install 超时", C.R)
            return None
        except subprocess.CalledProcessError as e:
            cprint(f"❌ npm install 失败: {e}", C.R)
            return None

    cprint("\n═══ [4/5] 前端服务 ═══", C.B)
    return spawn(
        "WEB",
        ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", str(WEB_PORT)],
        frontend_dir,
        color=C.G,
    )


def start_neo4j_docker() -> subprocess.Popen | None:
    """启动 Neo4j Docker 容器（如可用）。"""
    if not shutil_which("docker"):
        cprint("  ⏭️  docker 不可用，跳过", C.Y)
        return None
    cprint("  → 启动 Neo4j 容器...", C.B)
    try:
        subprocess.run(
            ["docker", "run", "-d", "--name", "gridmind-neo4j",
             "-p", "7687:7687", "-p", "7474:7474",
             "-e", "NEO4J_AUTH=neo4j/password",
             "neo4j:5.28.4"],
            check=False, capture_output=True,
        )
        cprint("  ✓ Neo4j 容器启动（bolt://localhost:7687）", C.G)
    except Exception as e:
        cprint(f"  ⚠️  Neo4j 启动失败: {e}", C.Y)
    return None


def shutil_which(name: str) -> str | None:
    import shutil
    return shutil.which(name)


# ── 关闭所有进程 ──────────────────────────────────────────
def shutdown_all(procs: Iterable[tuple[str, subprocess.Popen]]) -> None:
    """优雅关闭所有子进程。"""
    cprint("\n\n⏹️  正在关闭服务...", C.Y)
    for label, proc in procs:
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
    time.sleep(2)
    # 强杀未响应进程
    for label, proc in procs:
        if proc and proc.poll() is None:
            try:
                proc.kill()
                cprint(f"  ✓ {label} 已强制关闭", C.DIM)
            except Exception:
                pass
    cprint("✅ 所有服务已停止", C.G)


def stop_all() -> None:
    """--stop 模式：杀掉所有相关进程。"""
    cprint("⏹️  停止所有 GridMind 相关进程...", C.B)
    # Windows 下 netstat -ano 输出 GBK 编码，用 bytes 模式后用系统默认编码解码避免崩溃
    for port in (API_PORT, MCP_PORT, WEB_PORT):
        try:
            out_bytes = subprocess.check_output(
                ["netstat", "-ano"], shell=False, stderr=subprocess.DEVNULL,
            )
            # 自动尝试多种编码（gbk / utf-8 / cp936）
            out = None
            for enc in ("gbk", "utf-8", "cp936", "mbcs", "latin-1"):
                try:
                    out = out_bytes.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            if out is None:
                out = out_bytes.decode("utf-8", errors="replace")
            for line in out.splitlines():
                if f":{port} " in line and "LISTENING" in line:
                    pid = line.strip().split()[-1]
                    cprint(f"  → 杀掉 PID {pid} (port {port})", C.Y)
                    subprocess.run(["taskkill", "/F", "/PID", pid],
                                   capture_output=True, shell=False)
        except (subprocess.CalledProcessError, FileNotFoundError):
            # 兜底用 lsof (Git Bash / Linux)
            try:
                subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True)
            except FileNotFoundError:
                pass
    cprint("✅ 完成", C.G)


# ── 主流程 ────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="GridMind 一键启动 v2")
    parser.add_argument("--mock", action="store_true", help="Mock 模式（无需 DashScope Key）")
    parser.add_argument("--no-db-init", action="store_true", help="跳过数据库初始化")
    parser.add_argument("--no-web", action="store_true", help="不启动前端")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--neo4j", action="store_true", help="同时启动 Neo4j Docker 容器")
    parser.add_argument("--stop", action="store_true", help="停止所有相关服务")
    args = parser.parse_args()

    # Banner
    print()
    cprint("╔══════════════════════════════════════════╗", C.B)
    cprint("║                                          ║", C.B)
    cprint("║    GridMind · 灵枢电网 v1.5.1            ║", C.B + C.BOLD)
    cprint("║    一键启动                              ║", C.B)
    cprint("║                                          ║", C.B)
    cprint("╚══════════════════════════════════════════╝", C.B)
    print()

    if args.stop:
        stop_all()
        return

    # Mock 模式
    if args.mock:
        os.environ["MOCK_ENABLED"] = "true"
        cprint("🔧 Mock 模式启用 — LLM 调用将被模拟\n", C.M)

    # 检查依赖
    check_dependencies()

    # 检查 Key（精确提示 DashScope / DeepSeek 哪个缺）
    if not args.mock:
        has_dash, has_deep = check_all_keys()
        if not has_dash and not has_deep:
            cprint("⚠️  未配置任何 LLM API Key（启动后 LLM 不可用）", C.Y)
            cprint("   配置 .env 中的 DASHSCOPE_API_KEY / DEEPSEEK_API_KEY，或使用 --mock 跳过\n", C.DIM)
        elif not has_dash:
            cprint("⚠️  DASHSCOPE_API_KEY 未配置（qwen 模型不可用，deepseek 可用）", C.Y)
            cprint("   或使用 --mock 跳过\n", C.DIM)
        elif not has_deep:
            cprint("ℹ️  仅启用 DashScope（DeepSeek 未配置不影响核心功能）\n", C.DIM)

    # 端口检查
    cprint("\n═══ 端口检查 ═══", C.B)
    ports_ok = True
    ports_ok &= check_port(API_PORT, "API")
    ports_ok &= check_port(MCP_PORT, "MCP")
    if not args.no_web:
        ports_ok &= check_port(WEB_PORT, "WEB")
    if not ports_ok:
        cprint("\n❌ 端口冲突！请先关闭占用端口的进程，或使用 --stop", C.R)
        sys.exit(1)
    cprint("  ✓ 所有端口空闲\n", C.G)

    # 可选：Neo4j
    if args.neo4j:
        start_neo4j_docker()

    # 数据库初始化
    procs: list[tuple[str, subprocess.Popen]] = []
    try:
        if not args.no_db_init:
            init_database()

        # MCP
        try:
            mcp_proc = start_mcp_server()
            procs.append(("MCP", mcp_proc))
            if not http_ready(f"http://localhost:{MCP_PORT}/sse", timeout_s=15, label="MCP"):
                cprint("❌ MCP 服务启动超时", C.R)
                raise RuntimeError("MCP startup timeout")
            cprint("  ✓ MCP 就绪", C.G)
        except Exception as e:
            cprint(f"❌ MCP 启动失败: {e}", C.R)
            sys.exit(1)

        # API
        try:
            api_proc = start_api_server()
            procs.append(("API", api_proc))
            if not http_ready(f"{API_BASE}/docs", timeout_s=30, label="API"):
                cprint("❌ API 服务启动超时", C.R)
                raise RuntimeError("API startup timeout")
            cprint("  ✓ API 就绪", C.G)
        except Exception as e:
            cprint(f"❌ API 启动失败: {e}", C.R)
            shutdown_all(procs)
            sys.exit(1)

        # 前端（可选）
        if not args.no_web:
            # 必须在 Vite 启动前写好 web/.env（Vite 只在启动时读取 .env）
            ensure_dev_jwt(ROOT_DIR / "web")
            web_proc = start_frontend()
            if web_proc:
                procs.append(("WEB", web_proc))
                # Vite dev server 通常 1-3 秒就绪
                if not http_ready(WEB_URL, timeout_s=20, label="WEB"):
                    cprint("⚠️  前端启动超时，继续等待...", C.Y)
                else:
                    cprint("  ✓ WEB 就绪", C.G)

        # 汇总
        cprint("\n" + "═" * 50, C.G)
        cprint("✅  GridMind 已启动！", C.G + C.BOLD)
        cprint("═" * 50, C.G)
        cprint(f"   🌐 前端界面:  {WEB_URL}", C.CY)
        cprint(f"   📚 API 文档:   {API_DOCS}", C.CY)
        cprint(f"   🔌 MCP 端口:   {MCP_PORT}", C.CY)
        cprint(f"   📊 Prometheus: {API_BASE}/metrics", C.CY)
        cprint(f"   🎛  灰度面板:  {WEB_URL}/grayscale", C.CY)
        cprint("═" * 50, C.G)
        cprint("\n按 Ctrl+C 停止所有服务...\n", C.DIM)

        # 自动打开浏览器
        if not args.no_browser:
            time.sleep(1)
            try:
                webbrowser.open(WEB_URL)
                cprint(f"  → 已打开浏览器: {WEB_URL}\n", C.DIM)
            except Exception as e:
                cprint(f"  ⚠️  打开浏览器失败: {e}（手动访问 {WEB_URL}）\n", C.Y)

        # 等待 Ctrl+C 或子进程退出
        try:
            while True:
                time.sleep(1)
                # 检测是否有进程意外退出
                for label, p in procs:
                    if p.poll() is not None:
                        cprint(f"\n❌ {label} 进程意外退出（code {p.returncode}）", C.R)
                        shutdown_all(procs)
                        sys.exit(1)
        except KeyboardInterrupt:
            cprint("\n\n🛑 收到 Ctrl+C", C.Y)
            shutdown_all(procs)

    except Exception as e:
        cprint(f"\n❌ 启动失败: {e}", C.R)
        shutdown_all(procs)
        sys.exit(1)


if __name__ == "__main__":
    main()