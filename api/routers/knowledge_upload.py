"""用户上传知识库路由（V1.7 · KB Upload · 架构 kb-upload-architecture-2026-08-06）。

**端点**：

===========================================  ======  ============================
路径                                          方法    鉴权
===========================================  ======  ============================
``/api/knowledge/upload``                    POST    Depends(verify_jwt_if_prod)
``/api/knowledge/uploads``                   GET     Depends(verify_jwt_if_prod)
``/api/knowledge/uploads/{doc_id}``          DELETE  Depends(verify_jwt_if_prod)
===========================================  ======  ============================

**鉴权**（架构 §1.1 难点 6 + 共享知识 §7.7）：写操作（上传 / 删除）与列表读
操作均 ``Depends(verify_jwt_if_prod)`` —— 生产强制 JWT（401 fail-closed），
dev 放行（与既有 ``/devices`` 等数据端点一致）。

**错误文案映射**（架构 §3.1 ``UploadError`` + 共享知识 §7.10 三分类）：
- 400 ``INVALID_EXT``       —— 「仅支持 txt / md / pdf 文件」
- 400 ``PDF_PARSER_MISSING`` —— 「PDF 解析库未安装，无法解析 PDF」
- 400 ``PDF_PARSE_FAILED``  —— 「PDF 解析失败，请确认文件未损坏或未加密」
- 413 ``FILE_TOO_LARGE``    —— 「文件大小不能超过 5MB」
- 422 ``ENCODING_UNSUPPORTED`` / ``EMPTY_DOC`` —— 解析失败可读文案
- 404 ``DOC_NOT_FOUND``     —— 删除不存在的 / 非 user-upload 文档
- 500 —— 服务异常（完整 traceback 入日志，响应仅通用 message）

作者：寇豆码（工程师）
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel, Field

from api.services.auth import verify_jwt_if_prod
from core.kb_upload import KbUploadService, MSG_INTERNAL, UploadError

# ═══════════════════════════════════════════════════════
# 响应模型（对齐架构 §3.1 classDiagram）
# ═══════════════════════════════════════════════════════


class UploadResponse(BaseModel):
    """``POST /api/knowledge/upload`` 响应体。"""

    doc_id: str = Field(default="", description="库内唯一 id，如 user-upload:t1-ops-a1b2c3d4")
    title: str = Field(default="", description="文档标题（缺省为文件名）")
    filename: str = Field(default="", description="原始文件名（含扩展名）")
    size_bytes: int = Field(default=0, description="文件字节大小")
    chunk_count: int = Field(default=0, description="入库知识片段数")
    status: str = Field(default="ok", description="固定为 ok；失败以 HTTP 错误返回")


class KbUploadItem(BaseModel):
    """单条用户上传文档（列表项）。"""

    doc_id: str = Field(default="", description="库内唯一 id")
    filename: str = Field(default="", description="原始文件名（含扩展名）")
    title: str = Field(default="", description="文档标题")
    size_bytes: int = Field(default=0, description="文件字节大小")
    uploaded_at: str = Field(default="", description="最近一次上传时间（本地时间串）")
    chunk_count: int = Field(default=0, description="该文档的知识片段数")
    status: str = Field(default="ok", description="固定为 ok（同步上传无持久处理中/失败态）")


class KbUploadListResponse(BaseModel):
    """``GET /api/knowledge/uploads`` 响应体。"""

    items: list[KbUploadItem] = Field(default_factory=list, description="文档列表（时间倒序）")
    total: int = Field(default=0, description="文档总数")


class DeleteResponse(BaseModel):
    """``DELETE /api/knowledge/uploads/{doc_id}`` 响应体。"""

    status: str = Field(default="ok", description="固定为 ok")
    doc_id: str = Field(default="", description="被删除的文档 id")
    deleted_chunks: int = Field(default=0, description="实际删除的分片数")


# ═══════════════════════════════════════════════════════
# 路由
# ═══════════════════════════════════════════════════════

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

#: 进程级 service（无状态，可安全复用）
_service = KbUploadService()


def _map_upload_error(exc: UploadError) -> HTTPException:
    """把业务 :class:`UploadError` 映射为带可读文案的 HTTPException。"""
    return HTTPException(status_code=exc.http_status, detail=exc.message)


@router.post("/upload", response_model=UploadResponse)
async def upload_knowledge(
    file: Annotated[UploadFile, File(description="知识文档（.txt / .md / .pdf，≤5MB）")],
    title: Annotated[str | None, Form(description="可选标题，缺省取文件名")] = None,
    identity: Annotated[dict[str, Any] | None, Depends(verify_jwt_if_prod)] = None,  # type: ignore[assignment]
) -> UploadResponse:
    """上传知识文档：解析 → 切分 → 入库（同步，成功即「已入库」）。

    执行链路（架构 §4.1 时序图）：
    ``router → KbUploadService.ingest → VectorStore.upsert_chunks``
    （SQLite 覆盖式 + Chroma + bump ``kb_revision`` → MCP ``ensure_fresh`` 热更新）。
    V1.7.1 P1：``.pdf`` 走 pypdf 逐页提取（lazy import），``.txt/.md`` 走编码检测。

    Args:
        file: multipart 文件（必填，.txt / .md / .pdf，≤5MB）。
        title: 可选标题。
        identity: 鉴权主体（dev 放行 / 生产 JWT）。

    Returns:
        :class:`UploadResponse` —— ``{doc_id, title, filename, size_bytes, chunk_count, status}``。

    Raises:
        HTTPException 400/413/422/500: 见模块 docstring 错误映射。
    """
    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="文件名缺失")

    data = await file.read()
    try:
        result = _service.ingest(filename, data, title)
    except UploadError as exc:
        raise _map_upload_error(exc) from exc
    except Exception as exc:  # noqa: BLE001 — 完整 traceback 入日志，响应仅通用 message
        logger.error("KB upload failed (filename={}): {}", filename, exc)
        raise HTTPException(status_code=500, detail=MSG_INTERNAL) from exc

    logger.info(
        "KB upload API OK: doc_id={} filename={} chunks={} (principal={})",
        result.doc_id, result.filename, result.chunk_count,
        _principal(identity),
    )
    return UploadResponse(**asdict(result))


@router.get("/uploads", response_model=KbUploadListResponse)
async def list_uploads(
    identity: Annotated[dict[str, Any] | None, Depends(verify_jwt_if_prod)] = None,  # type: ignore[assignment]
) -> KbUploadListResponse:
    """列出全部用户上传文档（含 chunk 数，时间倒序）。

    Args:
        identity: 鉴权主体（dev 放行 / 生产 JWT）。

    Returns:
        :class:`KbUploadListResponse` —— ``{items, total}``。

    Raises:
        HTTPException 500: 列表查询失败（通用 message）。
    """
    try:
        items = _service.list_docs()
    except Exception as exc:  # noqa: BLE001
        logger.error("KB upload list failed: {}", exc)
        raise HTTPException(status_code=500, detail=MSG_INTERNAL) from exc

    logger.debug("KB upload list OK: {} docs (principal={})", len(items), _principal(identity))
    return KbUploadListResponse(
        items=[KbUploadItem(**item) for item in items],
        total=len(items),
    )


@router.delete("/uploads/{doc_id}", response_model=DeleteResponse)
async def delete_upload(
    doc_id: str,
    identity: Annotated[dict[str, Any] | None, Depends(verify_jwt_if_prod)] = None,  # type: ignore[assignment]
) -> DeleteResponse:
    """删除用户上传文档（物理删除 SQLite + Chroma 分片，bump revision）。

    命名空间守卫（架构 §1.3）：doc_id 必须以 ``user-upload:`` 开头，否则 404；
    绝不触碰 ``feature-intro`` / 老 seed。

    Args:
        doc_id: 目标文档 id（URL path，含冒号无需额外编码）。
        identity: 鉴权主体（dev 放行 / 生产 JWT）。

    Returns:
        :class:`DeleteResponse` —— ``{status, doc_id, deleted_chunks}``。

    Raises:
        HTTPException 404: 文档不存在 / 非 user-upload 命名空间。
        HTTPException 500: 删除失败（通用 message）。
    """
    try:
        deleted = _service.delete(doc_id)
    except UploadError as exc:
        raise _map_upload_error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("KB upload delete failed (doc_id={}): {}", doc_id, exc)
        raise HTTPException(status_code=500, detail=MSG_INTERNAL) from exc

    logger.info(
        "KB upload delete API OK: doc_id={} deleted_chunks={} (principal={})",
        doc_id, deleted, _principal(identity),
    )
    return DeleteResponse(status="ok", doc_id=doc_id, deleted_chunks=deleted)


# ═══════════════════════════════════════════════════════
# 内部工具
# ═══════════════════════════════════════════════════════


def _principal(identity: dict[str, Any] | None) -> str:
    """从鉴权主体提取可读 principal（日志审计用，不泄漏敏感值）。"""
    if not isinstance(identity, dict):
        return "anonymous"
    return str(identity.get("sub") or identity.get("user_id") or "anonymous")
