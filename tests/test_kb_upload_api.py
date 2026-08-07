"""KB Upload · T05 API 集成测试（架构 kb-upload-architecture-2026-08-06 §5 T05）。

覆盖（三端点 + 鉴权 + 错误文案）：
1. **POST /api/knowledge/upload**：md/txt/pdf 成功、损坏 pdf 400、不支持扩展名 400、
   >5MB 413、非法编码 422、空文件 422、可选 title、同名重传幂等覆盖
2. **GET /api/knowledge/uploads**：列表 + chunk 数
3. **DELETE /api/knowledge/uploads/{doc_id}**：删除成功 / 再删 404 /
   非 user-upload 命名空间守卫 404
4. **鉴权**：生产模式匿名 401（三端点）/ 带合法 JWT 200；dev 放行

**隔离策略**：SQLite（``knowledge_chunks`` + ``kb_meta``）+ Chroma 全部落到
``tmp_path``，并重置 ``core.vector_store`` 进程级单例 —— 绝不污染真实知识库。
生产模式通过 reload ``api.config`` + ``api.services.auth`` 切换（test_security_patch 同款）。

**运行**::

    cd /path/to/GridMind
    python -m pytest tests/test_kb_upload_api.py -v
"""

from __future__ import annotations

import importlib
import os
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

# ── 测试环境预配置（在导入 api 之前）──
os.environ.setdefault("MOCK_ENABLED", "true")
os.environ["JWT_SECRET"] = "test-jwt-secret-api-32bytes-required-pad!"
os.environ["ADMIN_TOKEN"] = "test-admin-token-api"
os.environ["JWT_ALGORITHM"] = "HS256"
os.environ["JWT_ISSUER"] = "gridmind"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # 保证 pdf_fixture 可导入

import pytest
from fastapi.testclient import TestClient

from core.kb_upload import MSG_DOC_NOT_FOUND, MSG_ENCODING_UNSUPPORTED, MSG_EMPTY_DOC
from core.kb_upload import MSG_FILE_TOO_LARGE, MSG_INVALID_EXT, MSG_PDF_PARSE_FAILED
from pdf_fixture import build_min_pdf


# ═══════════════════════════════════════════════════════
# 公共工具：DB / Chroma 隔离
# ═══════════════════════════════════════════════════════


def _apply_db_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """把 SQLite + Chroma 重定向到 tmp_path，并重置 VectorStore 单例。

    Returns:
        测试用 SQLite 文件路径。
    """
    db_path = tmp_path / "kb_api.db"
    chroma_dir = tmp_path / "chroma"

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
    monkeypatch.setattr(
        vs_mod, "settings",
        SimpleNamespace(
            chroma_persist_dir=str(chroma_dir),
            dashscope_api_key="sk-placeholder",
        ),
    )
    # 重置进程级单例：下次 get_vector_store() 用隔离环境新建
    monkeypatch.setattr(vs_mod, "_store_singleton", None)

    db_mod.init_db()  # 建表（走 patched _conn）
    return str(db_path)


def _reload_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """重载 api.config + api.services.auth，让最新 env（dev/prod）生效。

    说明：路由的 ``Depends(verify_jwt_if_prod)`` 持有旧函数对象，但其
    ``__globals__`` 指向 auth 模块 dict —— reload 原地更新 dict 后旧函数
    同样读到新 settings（无需 reload 路由模块）。
    """
    import api.config as config_mod
    importlib.reload(config_mod)
    import api.services.auth as auth_mod
    importlib.reload(auth_mod)


def _make_client() -> TestClient:
    """返回 TestClient(app)（**不**用 with → 跳过 lifespan，避免真实 MCP 连接）。"""
    import api.main as main_mod
    return TestClient(main_mod.app)


# ═══════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _dev_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个 test 前强制 dev 模式 + 已知 JWT 配置（防生产态跨测试污染）。

    QA 修复（测试隔离缺陷）：
        ``_reload_settings`` 内部走 ``importlib.reload``，这是**不可被
        monkeypatch 回滚**的全局副作用 —— ``prod_client`` fixture 先
        ``setenv("APP_ENV","production")`` 再 reload，会把
        ``api.config.settings`` / ``api.services.auth.settings`` 永久替换成
        生产态实例。测试结束后 monkeypatch 只还原**环境变量**，不会重新
        reload 模块，于是生产态泄漏到本文件之后的所有测试模块
        （字母序：test_kb_upload_api → test_kg_* → test_multi_tab_lock），
        导致 ``POST /chat`` 等端点匿名请求被 ``verify_jwt_if_prod`` 拦成 401。

        因此在 teardown 阶段显式清掉 APP_ENV/PRODUCTION 并再 reload 一次，
        把模块状态复位为 dev，保证跨模块隔离。
    """
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("PRODUCTION", raising=False)
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-api-32bytes-required-pad!")
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token-api")
    _reload_settings(monkeypatch)
    yield
    # ── teardown：复位为 dev 态，防 importlib.reload 副作用外泄 ──
    os.environ.pop("APP_ENV", None)
    os.environ.pop("PRODUCTION", None)
    _reload_settings(monkeypatch)


@pytest.fixture
def kb_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """dev 模式客户端：隔离 DB + 三端点放行（verify_jwt_if_prod 返回 None）。"""
    _apply_db_isolation(tmp_path, monkeypatch)
    return _make_client()


@pytest.fixture
def prod_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """生产模式客户端：强制 JWT（三端点匿名 → 401）。"""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", "prod-secret-not-default-32bytes!!")
    monkeypatch.setenv("ADMIN_TOKEN", "prod-admin-token-32bytes!!!")
    _reload_settings(monkeypatch)
    _apply_db_isolation(tmp_path, monkeypatch)
    return _make_client()


# ═══════════════════════════════════════════════════════
# 1. POST /api/knowledge/upload
# ═══════════════════════════════════════════════════════


class TestUploadEndpoint:
    """上传端点：成功 / 错误文案 / 幂等覆盖。"""

    def test_upload_md_ok(self, kb_client: TestClient) -> None:
        """md 上传成功：doc_id 前缀 + chunk_count + 默认 title=文件名。"""
        resp = kb_client.post(
            "/api/knowledge/upload",
            files={"file": ("规程.md", "## 第一章\n\n紧急停机步骤。\n\n## 第二章\n\n复电流程。", "text/markdown")},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert body["doc_id"].startswith("user-upload:"), body["doc_id"]
        assert body["filename"] == "规程.md"
        assert body["title"] == "规程.md"  # 缺省取文件名
        assert body["chunk_count"] >= 1
        assert body["size_bytes"] > 0

    def test_upload_txt_ok(self, kb_client: TestClient) -> None:
        """txt 上传成功。"""
        resp = kb_client.post(
            "/api/knowledge/upload",
            files={"file": ("运维手册.txt", "第一段内容。\n\n第二段内容。", "text/plain")},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["chunk_count"] >= 1

    def test_upload_with_title(self, kb_client: TestClient) -> None:
        """可选 title 生效。"""
        resp = kb_client.post(
            "/api/knowledge/upload",
            data={"title": "我的自定义标题"},
            files={"file": ("a.txt", "内容", "text/plain")},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["title"] == "我的自定义标题"

    def test_upload_pdf_ok(self, kb_client: TestClient) -> None:
        """pdf（V1.7.1 P1）上传成功：200 + doc_id 前缀 + chunk 数 + 可检索。"""
        pdf_bytes = build_min_pdf("Transformer temperature range: 40-70 C")
        resp = kb_client.post(
            "/api/knowledge/upload",
            files={"file": ("手册.pdf", pdf_bytes, "application/pdf")},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert body["doc_id"].startswith("user-upload:"), body["doc_id"]
        assert body["filename"] == "手册.pdf"
        assert body["chunk_count"] >= 1
        assert body["size_bytes"] == len(pdf_bytes)

        # 可检索：user-upload 标签下能召回该 PDF 提取文本
        from core.vector_store import get_vector_store

        store = get_vector_store()
        hits = store.search_by_tag("user-upload")
        assert any("Transformer temperature range" in h["content"] for h in hits), hits

    def test_upload_unsupported_ext_400(self, kb_client: TestClient) -> None:
        """不支持扩展名（.docx）→ 400「仅支持 txt / md / pdf 文件」。"""
        resp = kb_client.post(
            "/api/knowledge/upload",
            files={"file": ("手册.docx", b"content", "application/octet-stream")},
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"] == MSG_INVALID_EXT

    def test_upload_malformed_pdf_400(self, kb_client: TestClient) -> None:
        """损坏的 PDF → 400「PDF 解析失败，请确认文件未损坏或未加密」。"""
        resp = kb_client.post(
            "/api/knowledge/upload",
            files={"file": ("手册.pdf", b"%PDF-1.4 this is not a valid pdf at all", "application/pdf")},
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"] == MSG_PDF_PARSE_FAILED

    def test_upload_too_large_413(self, kb_client: TestClient) -> None:
        """>5MB → 413「文件大小不能超过 5MB」。"""
        big = b"x" * (5 * 1024 * 1024 + 1)
        resp = kb_client.post(
            "/api/knowledge/upload",
            files={"file": ("big.txt", big, "text/plain")},
        )
        assert resp.status_code == 413, resp.text
        assert resp.json()["detail"] == MSG_FILE_TOO_LARGE

    def test_upload_bad_encoding_422(self, kb_client: TestClient) -> None:
        """UTF-8 / GBK 均失败 → 422「编码不支持」。"""
        bad = bytes([0xFF, 0xFE, 0xFD, 0xFC])
        resp = kb_client.post(
            "/api/knowledge/upload",
            files={"file": ("bad.txt", bad, "text/plain")},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"] == MSG_ENCODING_UNSUPPORTED

    def test_upload_empty_file_422(self, kb_client: TestClient) -> None:
        """空文件 → 422「文档内容为空」。"""
        resp = kb_client.post(
            "/api/knowledge/upload",
            files={"file": ("empty.txt", b"", "text/plain")},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"] == MSG_EMPTY_DOC

    def test_upload_same_name_idempotent(self, kb_client: TestClient) -> None:
        """同名重传 → doc_id 稳定 + 列表仅 1 条（幂等覆盖）。"""
        content = ("\n\n".join([f"## 第{i}节\n\n" + "内" * 200 for i in range(3)])).encode("utf-8")
        r1 = kb_client.post(
            "/api/knowledge/upload",
            files={"file": ("同名.md", content, "text/markdown")},
        )
        assert r1.status_code == 200, r1.text
        r2 = kb_client.post(
            "/api/knowledge/upload",
            files={"file": ("同名.md", content, "text/markdown")},
        )
        assert r2.status_code == 200, r2.text
        assert r1.json()["doc_id"] == r2.json()["doc_id"], "同名文件 doc_id 应稳定"

        lst = kb_client.get("/api/knowledge/uploads").json()
        matched = [it for it in lst["items"] if it["doc_id"] == r1.json()["doc_id"]]
        assert len(matched) == 1, f"幂等覆盖后列表应仅 1 条: {len(matched)}"
        assert matched[0]["chunk_count"] == r2.json()["chunk_count"]


# ═══════════════════════════════════════════════════════
# 2. GET /api/knowledge/uploads
# ═══════════════════════════════════════════════════════


class TestListEndpoint:
    """列表端点：文档 + chunk 数。"""

    def test_list_after_upload(self, kb_client: TestClient) -> None:
        """上传后列表包含该文档，chunk_count 与上传响应一致。"""
        up = kb_client.post(
            "/api/knowledge/upload",
            files={"file": ("列表.md", "## 一\n\n甲。\n\n## 二\n\n乙。", "text/markdown")},
        ).json()
        resp = kb_client.get("/api/knowledge/uploads")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] >= 1
        item = next(it for it in body["items"] if it["doc_id"] == up["doc_id"])
        assert item["filename"] == "列表.md"
        assert item["chunk_count"] == up["chunk_count"]
        assert item["status"] == "ok"
        assert item["uploaded_at"]  # 非空

    def test_list_empty(self, kb_client: TestClient) -> None:
        """无上传时返回空列表。"""
        resp = kb_client.get("/api/knowledge/uploads")
        assert resp.status_code == 200
        assert resp.json()["items"] == []
        assert resp.json()["total"] == 0


# ═══════════════════════════════════════════════════════
# 3. DELETE /api/knowledge/uploads/{doc_id}
# ═══════════════════════════════════════════════════════


class TestDeleteEndpoint:
    """删除端点：成功 / 404 / 命名空间守卫。"""

    def _upload(self, kb_client: TestClient) -> dict[str, Any]:
        return kb_client.post(
            "/api/knowledge/upload",
            files={"file": ("待删.md", "## 删除测试\n\n内容", "text/markdown")},
        ).json()

    def test_delete_ok(self, kb_client: TestClient) -> None:
        """删除成功：deleted_chunks 与上传一致，列表清空。"""
        up = self._upload(kb_client)
        resp = kb_client.delete(f"/api/knowledge/uploads/{up['doc_id']}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert body["doc_id"] == up["doc_id"]
        assert body["deleted_chunks"] == up["chunk_count"]
        lst = kb_client.get("/api/knowledge/uploads").json()
        assert all(it["doc_id"] != up["doc_id"] for it in lst["items"])

    def test_delete_again_404(self, kb_client: TestClient) -> None:
        """重复删除 → 404。"""
        up = self._upload(kb_client)
        assert kb_client.delete(f"/api/knowledge/uploads/{up['doc_id']}").status_code == 200
        resp = kb_client.delete(f"/api/knowledge/uploads/{up['doc_id']}")
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == MSG_DOC_NOT_FOUND

    def test_delete_feature_intro_guard_404(self, kb_client: TestClient) -> None:
        """命名空间守卫：删除 feature-intro 文档 → 404，绝不触碰内置知识。"""
        resp = kb_client.delete("/api/knowledge/uploads/feature-intro:tour-chat")
        assert resp.status_code == 404, resp.text
        assert resp.json()["detail"] == MSG_DOC_NOT_FOUND


# ═══════════════════════════════════════════════════════
# 4. 鉴权（生产 401 / 带 JWT 200；dev 放行）
# ═══════════════════════════════════════════════════════


class TestAuth:
    """鉴权：生产强制 JWT，dev 放行（架构 §1.1 难点 6 + 共享知识 §7.7）。"""

    def test_dev_no_auth_required(self, kb_client: TestClient) -> None:
        """dev 模式：三端点匿名可访问（verify_jwt_if_prod 放行）。"""
        assert kb_client.get("/api/knowledge/uploads").status_code == 200

    def test_prod_anonymous_401_upload(self, prod_client: TestClient) -> None:
        """生产：匿名上传 → 401。"""
        resp = prod_client.post(
            "/api/knowledge/upload",
            files={"file": ("x.md", "内容", "text/markdown")},
        )
        assert resp.status_code == 401, resp.text
        assert "Bearer" in resp.headers.get("WWW-Authenticate", "")

    def test_prod_anonymous_401_list(self, prod_client: TestClient) -> None:
        """生产：匿名列表 → 401。"""
        resp = prod_client.get("/api/knowledge/uploads")
        assert resp.status_code == 401, resp.text

    def test_prod_anonymous_401_delete(self, prod_client: TestClient) -> None:
        """生产：匿名删除 → 401。"""
        resp = prod_client.delete("/api/knowledge/uploads/user-upload:any-00000000")
        assert resp.status_code == 401, resp.text

    def test_prod_with_valid_jwt_ok(self, prod_client: TestClient) -> None:
        """生产：合法 JWT → 三端点放行（上传 → 列表 → 删除）。"""
        from api.services.auth import issue_test_token

        token = issue_test_token(user_id="u-1")
        headers = {"Authorization": f"Bearer {token}"}

        up = prod_client.post(
            "/api/knowledge/upload",
            files={"file": ("鉴权.md", "## 鉴权\n\n内容", "text/markdown")},
            headers=headers,
        )
        assert up.status_code == 200, up.text
        doc_id = up.json()["doc_id"]

        lst = prod_client.get("/api/knowledge/uploads", headers=headers)
        assert lst.status_code == 200, lst.text
        assert any(it["doc_id"] == doc_id for it in lst.json()["items"])

        dele = prod_client.delete(f"/api/knowledge/uploads/{doc_id}", headers=headers)
        assert dele.status_code == 200, dele.text
        assert dele.json()["deleted_chunks"] >= 1

    def test_prod_invalid_jwt_401(self, prod_client: TestClient) -> None:
        """生产：无效 token → 401。"""
        headers = {"Authorization": "Bearer not-a-real-jwt"}
        resp = prod_client.get("/api/knowledge/uploads", headers=headers)
        assert resp.status_code == 401, resp.text
