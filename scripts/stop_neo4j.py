"""停止 GridMind Neo4j 容器（Docker Compose）。

流程：
    1. 调用 `docker compose -f docker/neo4j/docker-compose.yml down`
    2. 确认端口 7474 / 7687 已释放
    3. 数据卷（./docker-data/neo4j）默认保留（M0 不删除数据）

约束：
    - 不删除数据卷（数据保护）。
    - Docker 不可用时友好降级。

用法：
    python scripts/stop_neo4j.py
    python scripts/stop_neo4j.py --remove-volumes  # 同时删除数据卷（危险）
"""

from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from loguru import logger

ROOT_DIR = Path(__file__).resolve().parent.parent
COMPOSE_FILE = ROOT_DIR / "docker" / "neo4j" / "docker-compose.yml"
DEFAULT_HTTP_PORT = 7474
DEFAULT_BOLT_PORT = 7687


def _check_docker_available() -> bool:
    return shutil.which("docker") is not None


def _is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def _run_compose_down(remove_volumes: bool) -> tuple[bool, str]:
    """调用 `docker compose down` 停止容器。"""
    if not COMPOSE_FILE.exists():
        return False, f"找不到 compose 文件: {COMPOSE_FILE}"

    cmd = [
        "docker",
        "compose",
        "-f",
        str(COMPOSE_FILE),
        "down",
    ]
    if remove_volumes:
        cmd.append("--volumes")
        logger.warning("⚠ 将一并删除命名数据卷")

    logger.info("执行: {}", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            return True, result.stdout.strip() or "ok"
        return False, f"docker compose 退出码={result.returncode}: {result.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return False, "docker compose 调用超时（>120s）"
    except FileNotFoundError as exc:
        return False, f"docker 可执行文件缺失: {exc}"


def _wait_ports_closed(timeout: int = 30) -> bool:
    """等待两个端口都关闭。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _is_port_open("127.0.0.1", DEFAULT_HTTP_PORT) and not _is_port_open(
            "127.0.0.1", DEFAULT_BOLT_PORT
        ):
            return True
        time.sleep(1.0)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="停止 GridMind Neo4j 容器")
    parser.add_argument(
        "--remove-volumes",
        action="store_true",
        help="同时删除命名数据卷（危险，会清空 Neo4j 全部数据）",
    )
    parser.add_argument(
        "--port-timeout",
        type=int,
        default=30,
        help="等待端口关闭的超时（秒），默认 30",
    )
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("GridMind Neo4j 停止器")
    logger.info("  Compose: {}", COMPOSE_FILE.relative_to(ROOT_DIR))
    logger.info("  删除卷:  {}", args.remove_volumes)
    logger.info("=" * 60)

    if not _check_docker_available():
        logger.warning("Docker 不可用——M0 标记'待 Docker 环境就位'")
        return 1

    ok, msg = _run_compose_down(args.remove_volumes)
    if not ok:
        logger.error("容器停止失败: {}", msg)
        return 1
    logger.info("容器已停止，等待端口释放...")

    if _wait_ports_closed(args.port_timeout):
        logger.success("✓ 端口已释放（{} / {} 均关闭）", DEFAULT_HTTP_PORT, DEFAULT_BOLT_PORT)
    else:
        logger.warning("端口释放超时（>{}s）——可能其他进程占用", args.port_timeout)
        return 2

    if not args.remove_volumes:
        data_dir = ROOT_DIR / "docker" / "neo4j" / "docker-data"
        logger.success("=" * 60)
        logger.success("Neo4j 已停止")
        logger.success("  数据保留:  {}", data_dir.relative_to(ROOT_DIR))
        logger.success("  重新启动:  python scripts/start_neo4j.py")
        logger.success("=" * 60)
    else:
        logger.success("=" * 60)
        logger.success("Neo4j 已停止 + 数据卷已删除")
        logger.success("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
