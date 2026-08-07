"""用户上传知识库 service（V1.7.1 · KB Upload P1: PDF · 架构 kb-upload-architecture-2026-08-06）。

**职责**：把用户上传的 txt / md / pdf 文档解析、切分、构建 chunk，并复用
:meth:`core.vector_store.VectorStore.upsert_chunks` 覆盖式写入
SQLite ``knowledge_chunks`` + Chroma ``knowledge_base``（跨进程热更新）。

**命名空间隔离**（共享知识 §7.1-§7.3）：
- ``doc_id = user-upload:{slug}-{sha1前8位}``，同名文件 doc_id 稳定 → 幂等覆盖
- 根标签 ``user-upload`` + ``source:{原始文件名}``；**不**加 ``feature-intro``，
  保证业务 RAG（``exclude_tags=["feature-intro"]``）正常召回用户上传分片。

**解析策略**（架构 §1.1 难点 3 + §3.1；V1.7.1 增补 P1 PDF）：
- 按扩展名分发（架构 §1 解析器）：``.pdf`` 走 :meth:`_parse_pdf`（pypdf 逐页提取，
  lazy import），``.txt/.md`` 走现有文本解码
- 编码检测：UTF-8 优先 → ``UnicodeDecodeError`` 时 GBK 兜底 → 均失败抛 ``ENCODING_UNSUPPORTED``
- txt：按空行分段聚合至 ~500 字符，段间 80 字符重叠
- md：优先按 ``##`` 二级标题切分章节（复用 ``scripts/seed_feature_intro`` 思路简化），
  长章节再按段落二次聚合；无章节则回落 txt 段落聚合
- pdf：pypdf 提取文本先做基本清洗（合并空白行）再回落 txt 段落聚合

作者：寇豆码（工程师）
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from loguru import logger

from core.vector_store import get_vector_store
from mcp_tools.db.database import get_connection


# ═══════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════

#: 命名空间根：doc_id 一律以 ``user-upload:`` 开头（与 feature-intro / doc-* 隔离）
DOC_ID_PREFIX: str = "user-upload"

#: 分片根标签（用于 ``search_by_tag("user-upload")`` 与列表分组）
ROOT_TAG: str = "user-upload"

#: 单文件大小上限（5MB）
MAX_FILE_BYTES: int = 5 * 1024 * 1024

#: 允许的扩展名白名单（V1.7.1 P1：已启用 pypdf 解析 ``.pdf``）
ALLOWED_EXT: frozenset[str] = frozenset({".txt", ".md", ".pdf"})

#: txt 段落聚合目标长度（字符）
CHUNK_SIZE: int = 500

#: 段落间重叠长度（字符）
CHUNK_OVERLAP: int = 80

#: md 章节超过该长度时按段落二次聚合，避免单 chunk 过长
MD_SECTION_MAX: int = 1000

#: 用户可读错误文案（PRD §4.2 / 架构共享知识 §7.5）
MSG_INVALID_EXT: str = "仅支持 txt / md / pdf 文件"
MSG_FILE_TOO_LARGE: str = "文件大小不能超过 5MB"
MSG_ENCODING_UNSUPPORTED: str = "编码不支持，请转换为 UTF-8 或 GBK"
MSG_EMPTY_DOC: str = "文档内容为空，无法入库"
MSG_PDF_PARSER_MISSING: str = "PDF 解析库未安装，无法解析 PDF"
MSG_PDF_PARSE_FAILED: str = "PDF 解析失败，请确认文件未损坏或未加密"
MSG_DOC_NOT_FOUND: str = "文档不存在或不属于用户上传知识库"
MSG_INTERNAL: str = "服务异常，请稍后重试"


# ═══════════════════════════════════════════════════════
# 错误与结果类型
# ═══════════════════════════════════════════════════════


class UploadError(Exception):
    """用户上传知识库业务错误（code / message / http_status 三要素）。

    Attributes:
        code: 机器可读错误码（如 ``INVALID_EXT`` / ``FILE_TOO_LARGE``）。
        message: 用户可读中文文案。
        http_status: 建议映射的 HTTP 状态码（400 / 413 / 422 / 404 / 500）。
    """

    def __init__(self, code: str, message: str, http_status: int = 400) -> None:
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(message)


@dataclass
class UploadResult:
    """``ingest`` 成功后的结果（对应 API ``UploadResponse``）。"""

    doc_id: str
    title: str
    filename: str
    size_bytes: int
    chunk_count: int
    status: str = "ok"


# ═══════════════════════════════════════════════════════
# Service
# ═══════════════════════════════════════════════════════


class KbUploadService:
    """用户上传知识库 service：解析、切分、入库、列表、删除编排。

    全部写操作最终委托 :func:`core.vector_store.get_vector_store` 的
    ``upsert_chunks`` / ``delete_chunks``（覆盖式 + bump ``kb_revision``），
    与功能介绍（feature-intro）链路完全对齐，无重复实现。
    """

    # ── doc_id ──────────────────────────────────────────

    @staticmethod
    def build_doc_id(filename: str) -> str:
        """按文件名生成稳定 doc_id：``user-upload:{slug}-{sha1前8位}``。

        规则（架构 §3.1 + 共享知识 §7.1）：
        - ``slug`` = 文件名去扩展名，非字母数字 → ``-``，小写；全为符号时回落 ``doc``
        - ``hash`` = ``sha1(原始文件名含扩展名).hexdigest()[:8]``
        - 同名文件 → 相同 doc_id → ``upsert_chunks`` 幂等覆盖（P2-1 提前满足）

        Args:
            filename: 原始文件名（含扩展名）。

        Returns:
            形如 ``user-upload:main-transformer-ops-a1b2c3d4`` 的 doc_id。
        """
        name = (filename or "").strip()
        stem = Path(name).stem
        slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
        if not slug:
            slug = "doc"
        digest = hashlib.sha1(name.encode("utf-8", errors="replace")).hexdigest()[:8]
        return f"{DOC_ID_PREFIX}:{slug}-{digest}"

    # ── 编码检测 ────────────────────────────────────────

    @staticmethod
    def _detect_encoding(data: bytes) -> str:
        """检测文本编码：UTF-8 优先，GBK 兜底。

        Args:
            data: 文件原始字节。

        Returns:
            检测到的编码名（``utf-8`` / ``gbk``）。

        Raises:
            UploadError: 两种编码均解码失败（code=``ENCODING_UNSUPPORTED``，422）。
        """
        try:
            data.decode("utf-8")
            return "utf-8"
        except UnicodeDecodeError:
            pass
        try:
            data.decode("gbk")
            return "gbk"
        except UnicodeDecodeError:
            raise UploadError(
                "ENCODING_UNSUPPORTED", MSG_ENCODING_UNSUPPORTED, http_status=422,
            ) from None

    # ── PDF 解析（P1 · lazy import pypdf）────────────────

    @staticmethod
    def _parse_pdf(data: bytes) -> str:
        """用 pypdf 逐页提取 PDF 文本（lazy import，未安装时返回明确 400 文案）。

        架构 §1 解析器按扩展名分发：``.pdf`` 走本方法；pypdf 提取的文本
        常含大量换行 / 空白，先经 :func:`_clean_pdf_text` 合并空白行，
        再回落 :meth:`_split_text` 的段落聚合（见 :meth:`ingest`）。

        Args:
            data: PDF 文件原始字节。

        Returns:
            拼接后的纯文本（空页跳过；连续空行已清洗；无文本时为空串）。

        Raises:
            UploadError: pypdf 未安装（code=``PDF_PARSER_MISSING``，400）；
                PDF 结构损坏 / 加密（code=``PDF_PARSE_FAILED``，400）。
        """
        # lazy import：未装 pypdf 时 .pdf 上传返回明确 400 而非 ImportError 崩溃
        try:
            from pypdf import PdfReader
        except ImportError:
            raise UploadError(
                "PDF_PARSER_MISSING", MSG_PDF_PARSER_MISSING, http_status=400,
            ) from None

        try:
            reader = PdfReader(BytesIO(data))
        except Exception as exc:  # noqa: BLE001 — 结构损坏统一映射可读 400
            logger.warning("PDF parse failed (reader init): {}", exc)
            raise UploadError(
                "PDF_PARSE_FAILED", MSG_PDF_PARSE_FAILED, http_status=400,
            ) from exc

        # 加密 PDF：无密码无法提取文本，直接给出可读文案而非空结果
        try:
            if reader.is_encrypted:
                logger.warning("PDF parse failed: file is encrypted")
                raise UploadError(
                    "PDF_PARSE_FAILED", MSG_PDF_PARSE_FAILED, http_status=400,
                )
        except UploadError:
            raise
        except Exception as exc:  # noqa: BLE001 — is_encrypted 探测失败按不可解析处理
            logger.warning("PDF encrypted check failed: {}", exc)
            raise UploadError(
                "PDF_PARSE_FAILED", MSG_PDF_PARSE_FAILED, http_status=400,
            ) from exc

        pages: list[str] = []
        for page in reader.pages:
            try:
                text = page.extract_text() or ""
            except Exception as exc:  # noqa: BLE001 — 单页提取失败跳过，不整体失败
                logger.warning("PDF page extract failed: {}", exc)
                continue
            text = text.strip()
            if text:
                pages.append(text)
        return _clean_pdf_text("\n\n".join(pages))

    # ── 文本切分 ────────────────────────────────────────

    @classmethod
    def _split_text(cls, text: str, ext: str) -> list[str]:
        """按扩展名分派切分：md 章节优先，txt/pdf/无章节回落段落聚合。

        Args:
            text: 已解码的纯文本（pdf 为 pypdf 提取并清洗后的文本）。
            ext: 小写扩展名（``.txt`` / ``.md`` / ``.pdf``）。

        Returns:
            切分后的文本段列表（非空）。

        Raises:
            UploadError: 文本为空（code=``EMPTY_DOC``，422）。
        """
        text = (text or "").strip()
        if not text:
            raise UploadError("EMPTY_DOC", MSG_EMPTY_DOC, http_status=422)

        if ext == ".md":
            sections = cls._split_markdown_sections(text)
            if sections:
                return sections
        return cls._aggregate_paragraphs(text)

    @staticmethod
    def _split_markdown_sections(text: str) -> list[str]:
        """按 ``##`` 二级标题切分 Markdown（复用 seed_feature_intro 思路简化）。

        标题行保留在段落内容内（提升 keyword fallback 召回）；无任何 ``##``
        标题或仅有一段时返回空列表（由调用方回落段落聚合）。

        Args:
            text: Markdown 原文。

        Returns:
            章节文本列表（每个元素含标题行 + 正文）。
        """
        parts = re.split(r"(?m)^(##\s+\S.*)$", text)
        if len(parts) < 3:
            # 无 ``##`` 标题 → 单段，交回段落聚合
            return []

        sections: list[str] = []
        preamble = parts[0].strip()
        if preamble:
            sections.append(preamble)
        for i in range(1, len(parts), 2):
            heading = parts[i].strip()
            body = parts[i + 1].strip() if i + 1 < len(parts) else ""
            merged = f"{heading}\n{body}".strip()
            if merged:
                sections.append(merged)
        return sections

    @staticmethod
    def _aggregate_paragraphs(text: str) -> list[str]:
        """按空行分段聚合到 ~500 字符，段间保留 80 字符重叠。

        Args:
            text: 纯文本。

        Returns:
            聚合后的文本段列表（非空）。

        Raises:
            UploadError: 无任何非空段落（code=``EMPTY_DOC``，422）。
        """
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text)]
        paragraphs = [p for p in paragraphs if p]
        if not paragraphs:
            raise UploadError("EMPTY_DOC", MSG_EMPTY_DOC, http_status=422)

        chunks: list[str] = []
        buffer = ""
        for para in paragraphs:
            if not buffer:
                buffer = para
                continue
            if len(buffer) + 1 + len(para) <= CHUNK_SIZE:
                buffer += "\n\n" + para
            else:
                chunks.append(buffer)
                # 下一段以本段尾部重叠开头（≤80 字符），保证跨段语义不断裂
                tail = buffer[-CHUNK_OVERLAP:] if len(buffer) > CHUNK_OVERLAP else buffer
                buffer = tail + "\n\n" + para
        if buffer.strip():
            chunks.append(buffer)
        return [c.strip() for c in chunks if c.strip()]

    # ── chunk 构建 ──────────────────────────────────────

    @classmethod
    def _build_chunks(
        cls,
        filename: str,
        title: str,
        text: str,
        size_bytes: int | None = None,
    ) -> list[dict[str, Any]]:
        """把切分后的文本构建为 ``VectorStore.upsert_chunks`` 入参 chunk 列表。

        对齐架构 §3.1 chunk 结构：content 带「《标题》」前缀提升 keyword fallback
        召回；tags = ``["user-upload", "source:{原始文件名}"]``；meta 携带文件名 /
        大小 / 上传时间 / 分片序号等元信息（供列表展示与审计）。

        Args:
            filename: 原始文件名（含扩展名）。
            title: 文档标题（默认取文件名）。
            text: 已解码纯文本。
            size_bytes: 文件字节大小（缺省按 UTF-8 文本长度估算）。

        Returns:
            chunk dict 列表（doc_id 全部相同，分片序号递增）。
        """
        ext = Path(filename).suffix.lower()
        doc_id = cls.build_doc_id(filename)
        segments = cls._split_text(text, ext)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        byte_size = int(size_bytes) if size_bytes is not None else len(text.encode("utf-8"))

        chunks: list[dict[str, Any]] = []
        for i, seg in enumerate(segments):
            chunks.append({
                "doc_id": doc_id,
                "title": title,
                "content": f"《{title}》\n\n{seg}",
                "source": f"{DOC_ID_PREFIX}/{filename}",
                "tags": [ROOT_TAG, f"source:{filename}"],
                "icon": None,
                "starter_message": None,
                "meta": {
                    "filename": filename,
                    "size_bytes": byte_size,
                    "uploaded_at": now,
                    "chunk_index": i,
                    "total_chunks": len(segments),
                    "lang": "zh-CN",
                },
            })
        return chunks

    # ── 入库编排 ────────────────────────────────────────

    def ingest(self, filename: str, data: bytes, title: str | None = None) -> UploadResult:
        """上传知识库主入口：校验 → 解析 → 切分 → 入库 → 返回结果。

        同步语义（架构 §1.3）：解析切分入库在一次请求内完成；成功即「已入库」。
        V1.7.1 P1：``.pdf`` 走 :meth:`_parse_pdf`（pypdf lazy import），
        ``.txt/.md`` 走编码检测（UTF-8→GBK）+ 解码。

        Args:
            filename: 原始文件名（含扩展名）。
            data: 文件原始字节。
            title: 可选标题；缺省取文件名（含扩展名）。

        Returns:
            :class:`UploadResult`（doc_id / title / filename / size_bytes / chunk_count）。

        Raises:
            UploadError: 格式不符（400）/ 大小超限（413）/ PDF 解析库缺失或
                解析失败（400）/ 编码不支持（422）/ 内容为空（422）；底层写库
                异常会向上抛（由 API 层映射 500）。
        """
        filename = (filename or "").strip() or "untitled.txt"
        ext = Path(filename).suffix.lower()

        # 1. 格式校验（P1-4 提前纳入 P0）
        if ext not in ALLOWED_EXT:
            raise UploadError("INVALID_EXT", MSG_INVALID_EXT, http_status=400)

        # 2. 大小校验
        if len(data) > MAX_FILE_BYTES:
            raise UploadError("FILE_TOO_LARGE", MSG_FILE_TOO_LARGE, http_status=413)

        # 3. 解析（按扩展名分发：pdf → pypdf 逐页提取；txt/md → 编码检测 + 解码）
        if ext == ".pdf":
            text = self._parse_pdf(data)
        else:
            encoding = self._detect_encoding(data)
            text = data.decode(encoding)

        # 4. 切分 + 构建 chunk（含标题前缀 + 元信息）
        resolved_title = (title or "").strip() or Path(filename).name
        chunks = self._build_chunks(filename, resolved_title, text, size_bytes=len(data))

        # 5. 入库（覆盖式 + bump kb_revision → MCP ensure_fresh 热更新）
        store = get_vector_store()
        chunk_count = store.upsert_chunks(chunks)

        doc_id = self.build_doc_id(filename)
        logger.info(
            "KB upload ingest OK: doc_id={} filename={} title={} chunks={} bytes={}",
            doc_id, filename, resolved_title, chunk_count, len(data),
        )
        return UploadResult(
            doc_id=doc_id,
            title=resolved_title,
            filename=filename,
            size_bytes=len(data),
            chunk_count=chunk_count,
            status="ok",
        )

    # ── 列表 / 删除 ─────────────────────────────────────

    def list_docs(self) -> list[dict[str, Any]]:
        """列出全部用户上传文档（按 doc_id 分组，含 chunk 数）。

        Returns:
            ``KbUploadItem`` 结构 dict 列表（doc_id / filename / title /
            size_bytes / uploaded_at / chunk_count / status），按上传时间倒序。
        """
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT doc_id, title, source, meta, updated_at "
                "FROM knowledge_chunks "
                "WHERE doc_id LIKE ? "
                "ORDER BY updated_at DESC",
                (f"{DOC_ID_PREFIX}:%",),
            ).fetchall()
        finally:
            conn.close()

        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            doc_id = row["doc_id"]
            meta = _loads_meta(row["meta"])
            item = grouped.setdefault(doc_id, {
                "doc_id": doc_id,
                "filename": (
                    str(meta.get("filename") or "").strip()
                    or _filename_from_source(row["source"])
                    or doc_id
                ),
                "title": str(row["title"] or "").strip(),
                "size_bytes": int(meta.get("size_bytes") or 0),
                "uploaded_at": str(row["updated_at"] or "").strip(),
                "chunk_count": 0,
                "status": "ok",
            })
            item["chunk_count"] += 1
            if not item["uploaded_at"] and row["updated_at"]:
                item["uploaded_at"] = str(row["updated_at"]).strip()

        items = sorted(
            grouped.values(),
            key=lambda it: it["uploaded_at"],
            reverse=True,
        )
        return items

    def delete(self, doc_id: str) -> int:
        """删除用户上传文档（物理删除 SQLite + Chroma 分片，bump revision）。

        命名空间守卫（架构 §1.3 + 共享知识 §7.8）：doc_id 必须以
        ``user-upload:`` 开头，否则抛 ``DOC_NOT_FOUND``（404），绝不触碰
        ``feature-intro:*`` / 老 seed ``doc-*``。

        Args:
            doc_id: 目标文档 id。

        Returns:
            实际删除的分片数。

        Raises:
            UploadError: 非 user-upload 命名空间或不存在（404）。
        """
        doc_id = (doc_id or "").strip()
        if not doc_id.startswith(f"{DOC_ID_PREFIX}:"):
            raise UploadError("DOC_NOT_FOUND", MSG_DOC_NOT_FOUND, http_status=404)

        store = get_vector_store()
        deleted = store.delete_chunks(doc_id)
        if deleted == 0:
            raise UploadError("DOC_NOT_FOUND", MSG_DOC_NOT_FOUND, http_status=404)

        logger.info("KB upload delete OK: doc_id={} deleted_chunks={}", doc_id, deleted)
        return deleted


# ═══════════════════════════════════════════════════════
# 内部工具
# ═══════════════════════════════════════════════════════


def _loads_meta(raw: Any) -> dict[str, Any]:
    """安全解析 ``meta`` JSON 列；非法 / 空值一律返回空 dict。"""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw))
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _filename_from_source(source: Any) -> str:
    """从 ``source``（形如 ``user-upload/<原始文件名>``）反解原始文件名。"""
    raw = str(source or "").strip()
    if "/" in raw:
        return raw.split("/", 1)[-1]
    return raw


def _clean_pdf_text(text: str) -> str:
    """清洗 pypdf 提取的 PDF 文本：去行首尾空白、合并连续空白行。

    pypdf 提取的文本常含大量换行 / 空白（列布局、行尾换行符残留），直接切分
    会产生大量碎片空行。这里把连续空白行压缩为单个段落分隔（保留段落语义，
    供 :meth:`KbUploadService._aggregate_paragraphs` 按空行分段），不处理行内
    空白——避免误伤中文 PDF 中字符间的空格（如「变 压 器」）。

    Args:
        text: pypdf 提取的原始文本（可能为空串）。

    Returns:
        清洗后的纯文本（可能为空串）。
    """
    lines = [ln.strip() for ln in (text or "").splitlines()]
    cleaned: list[str] = []
    prev_blank = False
    for ln in lines:
        if not ln:
            if prev_blank:
                continue  # 连续空行 → 只保留一个段落分隔
            prev_blank = True
        else:
            prev_blank = False
        cleaned.append(ln)
    return "\n".join(cleaned).strip()
