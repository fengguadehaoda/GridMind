#!/usr/bin/env python3
"""启动 MCP 工具服务（端口 9901），供手动进程管理使用。"""
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from mcp_tools.server import start

if __name__ == "__main__":
    start()
