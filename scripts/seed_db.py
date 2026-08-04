#!/usr/bin/env python3
"""数据库初始化与种子数据写入（独立脚本）。

用法：
    python -m scripts.seed_db
"""

from __future__ import annotations

from loguru import logger

from api.config import ROOT_DIR
from mcp_tools.db.database import init_db
from mcp_tools.db.seed_data import seed_all


def main() -> None:
    logger.info("GridMind - 数据库初始化")
    logger.info("数据目录: {}", ROOT_DIR / "data")

    init_db()
    seed_all()

    logger.info("数据库初始化完成")
    logger.info("数据库文件: {}", ROOT_DIR / "data" / "gridmind.db")


if __name__ == "__main__":
    main()
