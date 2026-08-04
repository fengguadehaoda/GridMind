"""启动 GridMind Neo4j 容器（Docker Compose）。

流程：
    1. 检查 Docker 可用性（不可用则友好降级——仅打印说明，不抛错）
    2. 拉起 `docker compose -f docker/neo4j/docker-compose.yml up -d`
    3. 探活 HTTP 7474 + Bolt 7687 端口
    4. 打印 Browser URL + Bolt URI

约束（M0）：
    - 仅供本机开发 / 集成测试使用；不在 CI 中调用。
    - 不修改系统服务（M0 阶段无 systemd 集成）。
    - Docker 不可用时不抛错，仅 WARN（环境就位后再跑）。

用法：
    python scripts/start_neo4j.py
    python scripts/start_neo4j.py --timeout 90
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from loguru import logger

# 项目根目录
ROOT_DIR = Path(__file__).resolve().parent.parent
COMPOSE_FILE = ROOT_DIR / "docker" / "neo4j" / "docker-compose.yml"
DEFAULT_HTTP_PORT = 7474
DEFAULT_BOLT_PORT = 7687
DEFAULT_TIMEOUT = 60  # 秒


def _check_docker_available() -> tuple[bool, str]:
    """检查 Docker CLI 是否可用。

    Returns:
        (available, version_or_reason)
    """
    if not shutil.which("docker"):
        return False, "docker CLI 不在 PATH"
    try:
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return True, result.stdout.strip() or "unknown"
        return False, f"docker version 失败: {result.stderr.strip()}"
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return False, f"docker 调用异常: {exc}"


def _is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """TCP 端口连通性检查。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def _run_compose_up() -> tuple[bool, str]:
    """调用 `docker compose up -d` 启动容器。

    Returns:
        (success, message)
    """
    if not COMPOSE_FILE.exists():
        return False, f"找不到 compose 文件: {COMPOSE_FILE}"

    cmd = [
        "docker",
        "compose",
        "-f",
        str(COMPOSE_FILE),
        "up",
        "-d",
    ]
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


def _wait_for_ports(
    http_port: int, bolt_port: int, timeout: int
) -> tuple[bool, list[str]]:
    """等待 HTTP + Bolt 端口同时可用。

    Returns:
        (ready, log_messages)
    """
    log: list[str] = []
    deadline = time.monotonic() + timeout
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        http_ok = _is_port_open("127.0.0.1", http_port)
        bolt_ok = _is_port_open("127.0.0.1", bolt_port)
        if http_ok and bolt_ok:
            log.append(f"第 {attempt} 次探活：HTTP={http_port} ✓  Bolt={bolt_port} ✓")
            return True, log
        if attempt % 6 == 1:  # 每 6 次打印一次
            log.append(
                f"第 {attempt} 次探活：HTTP={http_port} {'✓' if http_ok else '✗'}  "
                f"Bolt={bolt_port} {'✓' if bolt_ok else '✗'}"
            )
        time.sleep(2.0)
    return False, log


def _probe_neo4j_bolt(uri: str, user: str, password: str) -> bool:
    """通过 Python neo4j 驱动 ping Bolt（仅当端口通时调用）。"""
    try:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(uri, auth=(user, password))
        try:
            driver.verify_connectivity(timeout=3)
            return True
        finally:
            driver.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Bolt 探活失败: {}", exc)
        return False


def main() -> int:
    """脚本入口。

    Returns:
        进程退出码（0=成功；1=启动失败；2=探活超时）
    """
    parser = argparse.ArgumentParser(description="启动 GridMind Neo4j 容器")
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"启动超时（秒），默认 {DEFAULT_TIMEOUT}",
    )
    parser.add_argument(
        "--password",
        type=str,
        default=os.getenv("NEO4J_PASSWORD", "gridmind-dev"),
        help="Neo4j 密码（默认 gridmind-dev）",
    )
    parser.add_argument(
        "--user",
        type=str,
        default=os.getenv("NEO4J_USER", "neo4j"),
        help="Neo4j 用户名（默认 neo4j）",
    )
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("GridMind Neo4j 启动器")
    logger.info("  Compose: {}", COMPOSE_FILE.relative_to(ROOT_DIR))
    logger.info("  HTTP:    http://localhost:{}", DEFAULT_HTTP_PORT)
    logger.info("  Bolt:    bolt://localhost:{}", DEFAULT_BOLT_PORT)
    logger.info("  超时:    {} 秒", args.timeout)
    logger.info("=" * 60)

    # Step 1: Docker 可用性
    docker_ok, docker_msg = _check_docker_available()
    if not docker_ok:
        logger.warning("Docker 不可用：{}", docker_msg)
        logger.warning("M0 标记：'待 Docker 环境就位'。compose 文件已就绪。")
        logger.warning("请安装 Docker Desktop 后重试：")
        logger.warning("  https://www.docker.com/products/docker-desktop/")
        return 1

    logger.info("Docker 可用：{}", docker_msg)

    # Step 2: 容器启动
    ok, msg = _run_compose_up()
    if not ok:
        logger.error("容器启动失败: {}", msg)
        return 1
    logger.info("容器已提交（detach），等待端口探活...")

    # Step 3: 端口探活
    ready, log = _wait_for_ports(
        DEFAULT_HTTP_PORT, DEFAULT_BOLT_PORT, args.timeout
    )
    for line in log:
        logger.info(line)
    if not ready:
        logger.error(
            "探活超时（{} 秒内 {} / {} 端口未就位）",
            args.timeout, DEFAULT_HTTP_PORT, DEFAULT_BOLT_PORT,
        )
        logger.error("可执行 `docker logs gridmind-neo4j` 查看原因")
        return 2

    # Step 4: Bolt 探活（验证鉴权）
    bolt_uri = f"bolt://localhost:{DEFAULT_BOLT_PORT}"
    if _probe_neo4j_bolt(bolt_uri, args.user, args.password):
        logger.success("✓ Bolt 探活成功（鉴权通过）")
    else:
        logger.warning("Bolt 探活失败，但端口已通——可能是密码不一致")

    logger.success("=" * 60)
    logger.success("GridMind Neo4j 启动完成")
    logger.success("  Browser:  http://localhost:{}", DEFAULT_HTTP_PORT)
    logger.success("  Bolt:     {}", bolt_uri)
    logger.success("  凭据:     {} / {}", args.user, args.password)
    logger.success("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
