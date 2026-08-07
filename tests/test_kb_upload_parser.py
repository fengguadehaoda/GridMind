"""KB Upload · T01 解析器单测（架构 kb-upload-architecture-2026-08-06 §5 T01）。

覆盖（不触 DB，纯函数级验证）：
1. ``build_doc_id`` 幂等性 + 格式 + 命名空间前缀
2. ``_detect_encoding``：UTF-8 / GBK / 双失败 → ``UploadError(ENCODING_UNSUPPORTED)``
3. ``_split_text``：txt 段落聚合（~500 字符 + 80 字符重叠）/ md ``##`` 章节 / 空内容
4. ``_build_chunks``：tags 结构（``user-upload`` + ``source:{文件名}``）、
   content 标题前缀、meta 元信息（chunk_index / total_chunks / size_bytes）
5. ``_parse_pdf``（V1.7.1 P1）：pypdf 逐页提取 / 空页跳过 / 空白行清洗 /
   lazy import 缺失 400 / 损坏 400 / 加密 400 / ``.pdf`` 切分回落段落聚合

**运行**::

    cd /path/to/GridMind
    python -m pytest tests/test_kb_upload_parser.py -v
"""

from __future__ import annotations

import builtins
import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # 保证 pdf_fixture 可导入

import pytest

from core.kb_upload import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    DOC_ID_PREFIX,
    ROOT_TAG,
    KbUploadService,
    UploadError,
    _clean_pdf_text,
)
from pdf_fixture import build_min_pdf


# ═══════════════════════════════════════════════════════
# 1. build_doc_id
# ═══════════════════════════════════════════════════════


class TestBuildDocId:
    """doc_id 幂等性 + 命名空间 + 格式。"""

    def test_prefix_and_format(self) -> None:
        """doc_id 以 ``user-upload:`` 开头，形如 ``{slug}-{8位hex}``。"""
        doc_id = KbUploadService.build_doc_id("主变操作票.md")
        assert doc_id.startswith(f"{DOC_ID_PREFIX}:"), doc_id
        suffix = doc_id.split(":", 1)[-1]
        # 形如 slug-8位hex
        parts = suffix.rsplit("-", 1)
        assert len(parts) == 2, doc_id
        assert len(parts[1]) == 8, doc_id
        int(parts[1], 16)  # hex 合法则通过

    def test_same_filename_idempotent(self) -> None:
        """同名文件 → doc_id 稳定（幂等覆盖的基础）。"""
        a = KbUploadService.build_doc_id("#T1 主变操作票.md")
        b = KbUploadService.build_doc_id("#T1 主变操作票.md")
        assert a == b

    def test_extension_ignored_in_slug(self) -> None:
        """slug 取文件名去扩展名；hash 含扩展名 → 不同扩展名 doc_id 不同。"""
        md = KbUploadService.build_doc_id("regulations.md")
        txt = KbUploadService.build_doc_id("regulations.txt")
        assert md != txt, "不同扩展名（hash 不同）→ doc_id 应不同"

    def test_ascii_slug_lowercased(self) -> None:
        """非字母数字 → ``-``，小写。"""
        doc_id = KbUploadService.build_doc_id("T1_主变_Operation Manual.md")
        slug = doc_id.split(":", 1)[-1].rsplit("-", 1)[0]
        assert slug == slug.lower(), "slug 必须小写"
        assert "t1" in slug and "operation-manual" in slug, slug
        assert "_" not in slug and " " not in slug, "非字母数字必须替换为 '-'"

    def test_empty_or_symbol_only_falls_back_to_doc(self) -> None:
        """全符号文件名 → slug 回落 ``doc``（不产生空 slug）。"""
        doc_id = KbUploadService.build_doc_id("！！！.md")
        assert doc_id.startswith(f"{DOC_ID_PREFIX}:doc-"), doc_id


# ═══════════════════════════════════════════════════════
# 2. 编码检测
# ═══════════════════════════════════════════════════════


class TestDetectEncoding:
    """UTF-8 优先 / GBK 兜底 / 双失败抛错。"""

    def test_utf8(self) -> None:
        data = "紧急停机步骤".encode("utf-8")
        assert KbUploadService._detect_encoding(data) == "utf-8"

    def test_gbk_fallback(self) -> None:
        data = "紧急停机步骤".encode("gbk")
        assert KbUploadService._detect_encoding(data) == "gbk"

    def test_both_fail_raises(self) -> None:
        # 0xFF 0xFE 0x00 0x00 对 utf-8 / gbk 均非法
        data = bytes([0xFF, 0xFE, 0x00, 0x00, 0x01, 0x02])
        with pytest.raises(UploadError) as exc:
            KbUploadService._detect_encoding(data)
        assert exc.value.code == "ENCODING_UNSUPPORTED"
        assert exc.value.http_status == 422


# ═══════════════════════════════════════════════════════
# 3. 文本切分
# ═══════════════════════════════════════════════════════


class TestSplitText:
    """txt 段落聚合 + md 章节 + 空内容。"""

    def test_txt_aggregation_size_and_overlap(self) -> None:
        """txt：段落聚合到 ~500 字符，段间保留 80 字符重叠。"""
        # 每段 200 字符 → 3 段 = 600 字符 → 至少 2 个 chunk
        paragraphs = []
        for i in range(6):
            paragraphs.append(f"段落{i}：" + "内" * 120)  # ~126 字符/段
        text = "\n\n".join(paragraphs)
        chunks = KbUploadService._split_text(text, ".txt")

        assert len(chunks) >= 2, f"600+ 字符应切成至少 2 段，实际 {len(chunks)}"
        assert all(len(c) <= CHUNK_SIZE + 200 for c in chunks), "chunk 不应远超目标长度"

        # 重叠：相邻 chunk 间后段开头应包含前段尾部（80 字符重叠）
        for i in range(1, len(chunks)):
            prev_tail = chunks[i - 1][-CHUNK_OVERLAP:]
            assert prev_tail[:20] in chunks[i], (
                f"chunk[{i}] 应以 chunk[{i - 1}] 尾部开头（重叠 80 字符）"
            )

    def test_txt_single_small_paragraph(self) -> None:
        """小文本 → 单 chunk。"""
        chunks = KbUploadService._split_text("一行短内容", ".txt")
        assert chunks == ["一行短内容"]

    def test_md_sections(self) -> None:
        """md：按 ``##`` 章节切分，标题行保留在内容内。"""
        md = (
            "# 文档\n\n简介\n\n"
            "## 第一章 紧急停机\n\n停机步骤 A。\n\n"
            "## 第二章 复电流程\n\n复电步骤 B。"
        )
        chunks = KbUploadService._split_text(md, ".md")
        assert len(chunks) >= 2, f"应至少 2 个章节，实际 {len(chunks)}"
        assert any("紧急停机" in c for c in chunks)
        assert any("复电流程" in c for c in chunks)

    def test_md_no_sections_falls_back_to_paragraphs(self) -> None:
        """md 无 ``##`` 标题 → 回落段落聚合（不抛错）。"""
        md = "第一段内容。\n\n第二段内容。"
        chunks = KbUploadService._split_text(md, ".md")
        assert len(chunks) >= 1
        assert "第一段内容" in chunks[0]

    def test_empty_text_raises(self) -> None:
        with pytest.raises(UploadError) as exc:
            KbUploadService._split_text("   \n\n  ", ".txt")
        assert exc.value.code == "EMPTY_DOC"
        assert exc.value.http_status == 422

    def test_pdf_falls_back_to_paragraphs(self) -> None:
        """pdf（V1.7.1 P1）：非 md → 回落段落聚合（不抛错）。"""
        chunks = KbUploadService._split_text("第一段内容。\n\n第二段内容。", ".pdf")
        assert len(chunks) >= 1
        assert "第一段内容" in chunks[0]
        assert "第二段内容" in chunks[0]


# ═══════════════════════════════════════════════════════
# 4. chunk 构建
# ═══════════════════════════════════════════════════════


class TestBuildChunks:
    """tags / content 前缀 / meta 结构。"""

    def test_tags_structure(self) -> None:
        """每个分片 tags = [user-upload, source:{原始文件名}]，**不含** feature-intro。"""
        chunks = KbUploadService._build_chunks(
            "#T1 主变操作票.md", "#T1 主变操作票", "## 紧急停机\n\n步骤一。\n\n步骤二。",
            size_bytes=100,
        )
        assert chunks, "应至少 1 个 chunk"
        for c in chunks:
            tags = c["tags"]
            assert ROOT_TAG in tags, f"缺根标签 user-upload: {tags}"
            assert "source:#T1 主变操作票.md" in tags, f"缺 source 标签: {tags}"
            assert "feature-intro" not in tags, "user-upload 不得带 feature-intro 标签"

    def test_content_title_prefix(self) -> None:
        """content 以《标题》前缀开头（提升 keyword fallback 召回）。"""
        chunks = KbUploadService._build_chunks("规程.txt", "运维规程", "正文内容", size_bytes=10)
        assert chunks[0]["content"].startswith("《运维规程》")

    def test_meta_and_chunk_index(self) -> None:
        """meta 携带 filename / size_bytes / chunk_index / total_chunks / lang。"""
        chunks = KbUploadService._build_chunks(
            "规程.md", "运维规程",
            "\n\n".join([f"## 第{i}节\n\n" + "内" * 200 for i in range(3)]),
            size_bytes=1024,
        )
        assert len(chunks) >= 2, f"3 节应至少 2 个 chunk，实际 {len(chunks)}"
        for i, c in enumerate(chunks):
            meta = c["meta"]
            assert meta["filename"] == "规程.md"
            assert meta["size_bytes"] == 1024
            assert meta["chunk_index"] == i
            assert meta["total_chunks"] == len(chunks)
            assert meta["lang"] == "zh-CN"

    def test_doc_id_stable_across_chunks(self) -> None:
        """同一文件的所有 chunk 共享同一个 doc_id。"""
        chunks = KbUploadService._build_chunks(
            "同名文档.md", "同名文档", "\n\n".join(["内" * 300] * 3), size_bytes=900,
        )
        doc_ids = {c["doc_id"] for c in chunks}
        assert len(doc_ids) == 1, f"同一文档 chunk doc_id 应一致: {doc_ids}"
        assert chunks[0]["doc_id"].startswith(f"{DOC_ID_PREFIX}:")


# ═══════════════════════════════════════════════════════
# 5. PDF 解析（V1.7.1 P1）
# ═══════════════════════════════════════════════════════


class TestParsePdf:
    """``_parse_pdf``：pypdf 逐页提取 / 空页跳过 / 清洗 / 防御性错误。"""

    def test_extracts_text_from_min_pdf(self) -> None:
        """最小合法 PDF → 提取出页内文本。"""
        pdf = build_min_pdf("Transformer temperature range: 40-70 C")
        text = KbUploadService._parse_pdf(pdf)
        assert "Transformer temperature range" in text

    def test_blank_pages_skipped(self) -> None:
        """多页：空白页跳过，仅拼接有文本的页。"""
        pdf = build_min_pdf(["First page content", "", "Third page content"])
        text = KbUploadService._parse_pdf(pdf)
        assert "First page content" in text
        assert "Third page content" in text
        # 空白页不产生残留空行（页间仅一个分隔）
        assert "\n\n\n" not in text

    def test_blank_line_cleaning(self) -> None:
        """清洗函数：行首尾去空白 + 连续空行合并为单个分隔。"""
        raw = "  行一  \n\n\n\n  行二\n   \n\n行三  \n"
        cleaned = _clean_pdf_text(raw)
        assert cleaned == "行一\n\n行二\n\n行三", repr(cleaned)
        assert "\n\n\n" not in cleaned

    def test_missing_library_raises_400(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """pypdf 未安装（lazy import 失败）→ 400「PDF 解析库未安装」。"""
        real_import = builtins.__import__

        def _fake_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "pypdf" or name.startswith("pypdf."):
                raise ImportError("No module named 'pypdf'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        with pytest.raises(UploadError) as exc:
            KbUploadService._parse_pdf(b"%PDF-1.4 fake")
        assert exc.value.code == "PDF_PARSER_MISSING"
        assert exc.value.http_status == 400
        assert "未安装" in exc.value.message

    def test_corrupt_pdf_raises_400(self) -> None:
        """结构损坏的 PDF → 400「PDF 解析失败」。"""
        with pytest.raises(UploadError) as exc:
            KbUploadService._parse_pdf(b"%PDF-1.4 this is not a valid pdf at all")
        assert exc.value.code == "PDF_PARSE_FAILED"
        assert exc.value.http_status == 400

    def test_encrypted_pdf_raises_400(self) -> None:
        """加密 PDF（无密码）→ 400「PDF 解析失败」（明确文案而非空结果）。"""
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.encrypt("gridmind-test-pwd")
        buf = BytesIO()
        writer.write(buf)

        with pytest.raises(UploadError) as exc:
            KbUploadService._parse_pdf(buf.getvalue())
        assert exc.value.code == "PDF_PARSE_FAILED"
        assert exc.value.http_status == 400
