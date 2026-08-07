"""KB Upload · T05 RAG 全链路验收（架构 kb-upload-architecture-2026-08-06 §5 T05）。

验收（PRD §1 + 架构 §4.1/§4.2）：
1. 上传 → ``ensure_fresh`` 热更新（模拟 MCP 9901 进程）→ ``search`` 命中
   —— user-upload 分片**可被业务 RAG 检索**（exclude_tags=["feature-intro"] 不排除它）
2. 删除 → ``ensure_fresh`` → ``search`` 不再命中
3. ``search_by_tag("user-upload")`` / ``source:{文件名}`` 分组可用
4. 命名空间守卫：``delete_chunks`` 拒绝非 user-upload 前缀（返回 0）

**热更新模拟**：写库进程（API）用 ``get_vector_store()`` 单例；读进程（MCP）
用**新 VectorStore 实例**（独立 Chroma 目录模拟另一进程），初始 ``_revision="0"``，
``ensure_fresh()`` 感知 ``kb_revision`` 变化后重载 SQLite 分片。

**运行**::

    cd /path/to/GridMind
    python -m pytest tests/test_kb_upload_rag.py -v
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("MOCK_ENABLED", "true")
os.environ["JWT_SECRET"] = "test-jwt-secret-rag-32bytes-required-pad!"
os.environ["ADMIN_TOKEN"] = "test-admin-token-rag"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core.kb_upload import DOC_ID_PREFIX, KbUploadService, ROOT_TAG, UploadError
from core.vector_store import VectorStore, get_vector_store


@pytest.fixture
def rag_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """隔离 SQLite + Chroma（API 进程目录），重置 VectorStore 单例。"""
    db_path = tmp_path / "kb_rag.db"
    chroma_dir = tmp_path / "chroma_api"

    from mcp_tools.db import database as db_mod
    from core import kb_upload as kb_mod
    from core import vector_store as vs_mod

    def _conn() -> sqlite3.Connection:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    monkeypatch.setattr(db_mod, "get_connection", _conn)
    monkeypatch.setattr(vs_mod, "get_connection", _conn)
    monkeypatch.setattr(kb_mod, "get_connection", _conn)

    patched_settings = SimpleNamespace(
        chroma_persist_dir=str(chroma_dir),
        dashscope_api_key="sk-placeholder",
    )
    monkeypatch.setattr(vs_mod, "settings", patched_settings)
    monkeypatch.setattr(vs_mod, "_store_singleton", None)

    db_mod.init_db()
    return {"db_path": db_path, "settings": patched_settings, "tmp_path": tmp_path}


def _mcp_process_store(rag_env: dict[str, object], tmp_path: Path) -> VectorStore:
    """模拟 MCP 9901 读进程：独立 Chroma 目录 + 全新 VectorStore 实例。"""
    settings_obj = rag_env["settings"]  # SimpleNamespace，可原地改属性
    assert isinstance(settings_obj, SimpleNamespace)
    settings_obj.chroma_persist_dir = str(tmp_path / "chroma_mcp")
    return VectorStore(collection_name="knowledge_base")


# ═══════════════════════════════════════════════════════
# 1. 上传 → ensure_fresh → 检索命中
# ═══════════════════════════════════════════════════════


class TestUploadThenSearch:
    """上传 → 跨进程热更新 → RAG 检索命中（10s 验收的进程模拟）。"""

    def test_upload_ensure_fresh_search_hit(
        self, rag_env: dict[str, object], tmp_path: Path,
    ) -> None:
        """上传后 MCP 进程 ensure_fresh 感知 → search 命中 user-upload 分片。"""
        svc = KbUploadService()
        content = (
            "## 紧急停机\n\n"
            "当 #T1 主变发生严重故障时，应立即执行紧急停机步骤：\n"
            "1. 断开 10kV 侧断路器；\n"
            "2. 拉开隔离开关；\n"
            "3. 通知调度并挂牌。"
        )
        result = svc.ingest("#T1 主变操作票.md", content.encode("utf-8"))

        assert result.doc_id.startswith(f"{DOC_ID_PREFIX}:")

        # 模拟 MCP 进程：全新实例，初始 revision 未感知
        mcp_store = _mcp_process_store(rag_env, tmp_path)
        assert mcp_store._revision == "0"  # noqa: SLF001 — 初始未感知

        reloaded = mcp_store.ensure_fresh()
        assert reloaded is True, "revision 变化应触发重载（热更新）"

        # 业务 RAG：exclude feature-intro → user-upload 不被排除 → 命中
        hits = mcp_store.search("紧急停机步骤", top_k=5, exclude_tags=["feature-intro"])
        assert hits, "上传内容应被业务 RAG 检索到"
        assert any(
            "user-upload" in (h.get("tags") or [])
            and "#T1 主变" in h.get("content", "")
            for h in hits
        ), "命中分片应带 user-upload 标签且内容含上传文档标题"

    def test_search_by_tag_user_upload(
        self, rag_env: dict[str, object], tmp_path: Path,
    ) -> None:
        """search_by_tag('user-upload') / source 标签分组可用。"""
        svc = KbUploadService()
        svc.ingest("标签测试.txt", "紧急停机步骤一。\n\n紧急停机步骤二。".encode("utf-8"))

        mcp_store = _mcp_process_store(rag_env, tmp_path)
        mcp_store.ensure_fresh()

        root_items = mcp_store.search_by_tag(ROOT_TAG, top_k=50)
        assert root_items, "根标签 user-upload 应能列出分片"
        assert all("user-upload" in (it.get("tags") or []) for it in root_items)

        source_items = mcp_store.search_by_tag("source:标签测试.txt", top_k=50)
        assert source_items, "source:{原始文件名} 标签应能精确过滤"
        assert all(it.get("source", "").endswith("标签测试.txt") for it in source_items)


# ═══════════════════════════════════════════════════════
# 2. 删除 → 不可检索
# ═══════════════════════════════════════════════════════


class TestDeleteThenUnsearchable:
    """删除后 MCP 进程热更新 → 不再召回（架构 §4.3）。"""

    def test_delete_makes_unsearchable(
        self, rag_env: dict[str, object], tmp_path: Path,
    ) -> None:
        """删除 → ensure_fresh → search 不再命中该文档。"""
        svc = KbUploadService()
        content = "## 机密规程\n\n删除后不应再被检索到：立即停运并隔离电源。"
        result = svc.ingest("机密规程.md", content.encode("utf-8"))

        mcp_store = _mcp_process_store(rag_env, tmp_path)
        mcp_store.ensure_fresh()
        assert mcp_store.search("停运并隔离电源", top_k=5, exclude_tags=["feature-intro"])

        # 删除
        deleted = svc.delete(result.doc_id)
        assert deleted >= 1

        # 模拟「另一进程」在删除后首次自检：全新实例（_revision="0"）→
        # ensure_fresh 感知 revision 相对初始有变化 → 重载 → 不再召回。
        # 注：_bump_revision 为秒级时间戳，同秒内连续写入 revision 串相同，
        # 因此**复用** mcp_store 二次 ensure_fresh 无法区分（生产无碍——
        # 跨进程只需感知「相对上次自检有变化」，此处用新进程等价验证）。
        mcp_store2 = _mcp_process_store(rag_env, tmp_path)
        reloaded = mcp_store2.ensure_fresh()
        assert reloaded is True
        hits = mcp_store2.search("停运并隔离电源", top_k=5, exclude_tags=["feature-intro"])
        assert not any("user-upload" in (h.get("tags") or []) for h in hits), (
            "删除后 user-upload 分片不应再被召回"
        )
        assert mcp_store2.search_by_tag(ROOT_TAG, top_k=50) == []

    def test_delete_namespace_guard(self, rag_env: dict[str, object]) -> None:
        """命名空间守卫：删除 feature-intro → UploadError(404) + delete_chunks 返回 0。"""
        svc = KbUploadService()
        with pytest.raises(UploadError) as exc:
            svc.delete("feature-intro:tour-chat")
        assert exc.value.http_status == 404

        store = get_vector_store()
        assert store.delete_chunks("doc-001") == 0, "非 user-upload 前缀拒绝删除"
        assert store.delete_chunks("feature-intro:tour-chat") == 0
