#!/usr/bin/env python3
"""启动 MCP 工具服务（端口 9901），供手动进程管理使用。

与 scripts/start_all.py 保持一致：
- 启动前做端口预检，端口被占用时明确报错退出，避免"假成功"。
"""
import socket
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

# 显式加载 .env（与 api/config.py 保持一致）
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT_DIR / ".env")

from api.config import settings  # noqa: E402


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """检查本机端口是否已被占用（TCP 连接测试）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        try:
            sock.connect((host, port))
            return True
        except OSError:
            return False


if __name__ == "__main__":
    if port_in_use(int(settings.mcp_port)):
        print(
            f"❌ 端口 {settings.mcp_port}（MCP）已被占用，"
            "请先停止旧进程后重试。"
        )
        sys.exit(1)

    from mcp_tools.server import start

    start()
