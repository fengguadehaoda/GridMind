"""功能介绍文档入仓脚本（P0-2 / P1-2）。

解析 ``docs/gridmind-feature-introduction.md``，按 ``##`` 二级标题切分章节，
提取每节的 YAML front-matter 元信息，生成知识库分片并调用
:meth:`core.vector_store.VectorStore.upsert_chunks` 覆盖式写入
SQLite ``knowledge_chunks`` 表 + Chroma ``knowledge_base`` collection。

文档格式约定（与 ``docs/gridmind-feature-introduction.md`` 一一对应）：

.. code-block:: markdown

    ---
    doc: gridmind-feature-introduction     # 文档级 front-matter（可选）
    source: docs/gridmind-feature-introduction.md
    ---

    # 文档标题

    ## 3.1 实时监控全览 monitor-overview

    ---
    id: monitor-overview
    title: 实时监控全览
    icon: Monitor
    tags: [feature-intro, chapter:3, scenario:monitor-overview]
    starterMessage: 请给我介绍一下 GridMind 的 5 个核心视图
    ---

    正文……

也兼容 ```` ```yaml ```` 围栏形式的 front-matter。

用法::

    python -m scripts.seed_feature_intro              # 解析并入仓
    python -m scripts.seed_feature_intro --reload     # 同上（语义显式：覆盖重载）
    python -m scripts.seed_feature_intro --dry-run    # 只解析不写库，打印分片摘要
    python -m scripts.seed_feature_intro --doc path/to/other.md

作者：寇豆码
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# 允许 `python scripts/seed_feature_intro.py` 直接运行（补 sys.path）
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import yaml  # noqa: E402  （pyyaml 已在环境中，见 requirements）
from loguru import logger  # noqa: E402

from core.vector_store import VectorStore, get_vector_store  # noqa: E402
from mcp_tools.db.database import init_db  # noqa: E402

# ═══════════════════════════════════════════════════════
# 常量约定（跨文件共享，务必与前端 / API 保持一致）
# ═══════════════════════════════════════════════════════

#: 默认文档路径（相对项目根）
DEFAULT_DOC_PATH: str = "docs/gridmind-feature-introduction.md"

#: doc_id 前缀 —— 保证功能介绍分片与老 seed 分片（doc-001…）命名空间隔离
DOC_ID_PREFIX: str = "feature-intro"

#: 全量标签 —— 所有功能介绍分片都会自动补上，前端一次拉全用
ROOT_TAG: str = "feature-intro"

#: ``##`` 二级标题匹配（行首，恰好两个 #）
_HEADING_RE = re.compile(r"^##(?!#)\s*(.+?)\s*$", re.MULTILINE)

#: ``---`` 包裹的 front-matter 块（紧跟标题之后）
_DASH_FM_RE = re.compile(r"^\s*---\s*\n(.*?)\n\s*---\s*(?:\n|$)", re.DOTALL)

#: ```` ```yaml ```` 围栏形式的 front-matter 块
_FENCE_FM_RE = re.compile(r"^\s*```ya?ml\s*\n(.*?)\n\s*```\s*(?:\n|$)", re.DOTALL)


# ═══════════════════════════════════════════════════════
# 解析
# ═══════════════════════════════════════════════════════


def _parse_doc_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """提取文档级 front-matter（文件最开头的 ``---`` 块）。

    Args:
        text: 完整 Markdown 文本。

    Returns:
        ``(front_matter_dict, 剩余正文)``；无 front-matter 时返回 ``({}, text)``。
    """
    if not text.lstrip().startswith("---"):
        return {}, text
    stripped = text.lstrip()
    offset = len(text) - len(stripped)
    m = _DASH_FM_RE.match(stripped)
    if m is None:
        return {}, text
    data = _safe_yaml(m.group(1))
    return data, text[offset + m.end():]


def _safe_yaml(raw: str) -> dict[str, Any]:
    """安全解析 YAML 片段；解析失败返回空 dict 并告警。

    Args:
        raw: YAML 文本。

    Returns:
        解析后的 dict（非 dict 结果一律视为空）。
    """
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        logger.warning("front-matter YAML 解析失败，已忽略该块：{}", e)
        return {}
    return data if isinstance(data, dict) else {}


def _split_sections(body: str) -> list[tuple[str, str]]:
    """按 ``##`` 二级标题切分正文。

    Args:
        body: 去掉文档级 front-matter 后的 Markdown 正文。

    Returns:
        ``[(heading, section_body), ...]``，按文档顺序排列。
    """
    matches = list(_HEADING_RE.finditer(body))
    sections: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections.append((m.group(1).strip(), body[start:end]))
    return sections


def _extract_section_front_matter(section_body: str) -> tuple[dict[str, Any], str]:
    """提取小节 front-matter（``---`` 块优先，其次 ```` ```yaml ```` 围栏）。

    Args:
        section_body: ``##`` 标题之后、下一个 ``##`` 之前的原始文本。

    Returns:
        ``(front_matter_dict, 正文内容)``。
    """
    stripped = section_body.lstrip("\n")
    for pattern in (_DASH_FM_RE, _FENCE_FM_RE):
        m = pattern.match(stripped)
        if m is not None:
            return _safe_yaml(m.group(1)), stripped[m.end():].strip()
    return {}, stripped.strip()


def _normalize_tags(raw: Any) -> list[str]:
    """把 front-matter 的 ``tags`` 规范为字符串列表，并补上 :data:`ROOT_TAG`。

    Args:
        raw: YAML 中的 tags 值（list / 逗号串 / None）。

    Returns:
        去重保序的标签列表，首位固定为 ``feature-intro``。
    """
    items: list[str]
    if raw is None:
        items = []
    elif isinstance(raw, (list, tuple)):
        items = [str(t).strip() for t in raw]
    else:
        items = [t.strip() for t in str(raw).split(",")]

    result: list[str] = [ROOT_TAG]
    seen: set[str] = {ROOT_TAG}
    for t in items:
        if t and t not in seen:
            seen.add(t)
            result.append(t)
    return result


def _build_meta(fm: dict[str, Any], heading: str) -> dict[str, Any]:
    """把 front-matter 中的结构化字段收敛为 ``meta`` JSON。

    保留除去已单列字段（title / icon / tags / starterMessage / …）之外的所有键，
    并额外写入 ``id`` / ``heading`` 便于前端定位。

    Args:
        fm: 小节 front-matter dict。
        heading: ``##`` 标题原文。

    Returns:
        meta dict（可直接 JSON 序列化）。
    """
    reserved = {"title", "icon", "tags", "startermessage", "starter_message", "source"}
    meta: dict[str, Any] = {"heading": heading}
    for k, v in fm.items():
        if str(k).lower() in reserved:
            continue
        meta[str(k)] = v
    return meta


def parse_document(doc_path: Path) -> list[dict[str, Any]]:
    """解析功能介绍 Markdown，生成待入仓的分片列表。

    Args:
        doc_path: Markdown 文件绝对路径。

    Returns:
        分片列表，每项含 ``doc_id`` / ``title`` / ``content`` / ``source`` /
        ``tags`` / ``icon`` / ``starter_message`` / ``meta``。

    Raises:
        FileNotFoundError: 文档不存在。
        ValueError: 文档中没有任何 ``##`` 小节。
    """
    if not doc_path.is_file():
        raise FileNotFoundError(f"功能介绍文档不存在：{doc_path}")

    text = doc_path.read_text(encoding="utf-8")
    doc_fm, body = _parse_doc_front_matter(text)

    default_source = str(doc_fm.get("source") or DEFAULT_DOC_PATH)
    doc_version = str(doc_fm.get("version") or "")
    doc_lang = str(doc_fm.get("lang") or "zh-CN")

    sections = _split_sections(body)
    if not sections:
        raise ValueError(f"文档 {doc_path} 中未找到任何 '##' 小节，无法切分")

    chunks: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for heading, section_body in sections:
        fm, content = _extract_section_front_matter(section_body)

        # id：front-matter 优先，缺省时用标题 slug 兜底
        section_id = str(fm.get("id") or "").strip() or _slugify(heading)
        if section_id in seen_ids:
            logger.warning("重复的小节 id '{}'（标题：{}），后者将覆盖前者", section_id, heading)
        seen_ids.add(section_id)

        title = str(fm.get("title") or heading).strip()
        tags = _normalize_tags(fm.get("tags"))

        starter = fm.get("starterMessage")
        if starter is None:
            starter = fm.get("starter_message")

        meta = _build_meta(fm, heading)
        meta["id"] = section_id
        meta["version"] = doc_version
        meta["lang"] = doc_lang

        # 正文为空时（纯结构化小节）用标题 + 描述兜底，保证 RAG 可检索
        body_text = content.strip()
        if not body_text:
            body_text = str(fm.get("description") or title)

        # content 前缀带标题，提升 keyword fallback 召回率
        full_content = f"{title}\n\n{body_text}".strip()

        chunks.append({
            "doc_id": f"{DOC_ID_PREFIX}:{section_id}",
            "title": title,
            "content": full_content,
            "source": default_source,
            "tags": tags,
            "icon": (str(fm["icon"]).strip() if fm.get("icon") else None),
            "starter_message": (str(starter).strip() if starter else None),
            "meta": meta,
        })

    return chunks


def _slugify(heading: str) -> str:
    """把中文/混合标题转为可用的 slug（兜底 id 生成）。

    Args:
        heading: ``##`` 标题原文，如 ``3.1 实时监控全览 monitor-overview``。

    Returns:
        小写 slug 字符串；无 ASCII 可用字符时回落为 ``section-<hash>``。
    """
    ascii_part = re.sub(r"[^a-zA-Z0-9]+", "-", heading).strip("-").lower()
    if ascii_part:
        return ascii_part
    return f"section-{abs(hash(heading)) % 10_000_000:07d}"


# ═══════════════════════════════════════════════════════
# 入仓
# ═══════════════════════════════════════════════════════


def seed_feature_intro(
    doc_path: str | Path | None = None,
    dry_run: bool = False,
    store: VectorStore | None = None,
) -> dict[str, Any]:
    """解析文档并覆盖式入仓。

    Args:
        doc_path: 文档路径（相对项目根或绝对路径）。``None`` 用
            :data:`DEFAULT_DOC_PATH`。
        dry_run: 为 ``True`` 时只解析不写库。
        store: 复用外部 :class:`VectorStore` 实例（API 热更新时传入单例）；
            ``None`` 时使用进程级单例。

    Returns:
        ``{"status": "ok", "count": N, "doc": "...", "chunks": [...]}``。

    Raises:
        FileNotFoundError / ValueError: 见 :func:`parse_document`。
    """
    path = Path(doc_path) if doc_path else Path(DEFAULT_DOC_PATH)
    if not path.is_absolute():
        path = _ROOT / path

    chunks = parse_document(path)
    logger.info("解析完成：{} 个分片（来源 {}）", len(chunks), path.name)

    if dry_run:
        for c in chunks:
            logger.info(
                "  [dry-run] {} | tags={} | icon={} | content={} 字",
                c["doc_id"], ",".join(c["tags"]), c["icon"] or "-", len(c["content"]),
            )
        return {
            "status": "dry-run",
            "count": len(chunks),
            "doc": str(path),
            "chunks": chunks,
        }

    # 确保 knowledge_chunks 元信息列已迁移（幂等）
    init_db()

    vs = store if store is not None else get_vector_store()
    written = vs.upsert_chunks(chunks)
    logger.info("入仓完成：{} 个分片写入 SQLite + Chroma", written)

    return {
        "status": "ok",
        "count": written,
        "doc": str(path),
        "chunks": chunks,
    }


# ═══════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════


def _build_arg_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="python -m scripts.seed_feature_intro",
        description="解析功能介绍 Markdown 并覆盖式写入知识库（SQLite + Chroma）",
    )
    parser.add_argument(
        "--doc",
        default=DEFAULT_DOC_PATH,
        help=f"文档路径（默认 {DEFAULT_DOC_PATH}）",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="重新解析并覆盖写入（语义显式；默认行为已是覆盖式，本参数仅作可读性标记）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只解析不写库，打印分片摘要",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 输出结果摘要（便于脚本消费）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。

    Args:
        argv: 参数列表（``None`` 时取 ``sys.argv[1:]``）。

    Returns:
        进程退出码（0 成功，1 失败）。
    """
    args = _build_arg_parser().parse_args(argv)

    try:
        result = seed_feature_intro(doc_path=args.doc, dry_run=args.dry_run)
    except Exception as e:  # noqa: BLE001 —— CLI 顶层需要兜住所有异常
        logger.error("功能介绍入仓失败：{}", e)
        if args.json:
            print(json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False))
        return 1

    if args.json:
        print(json.dumps(
            {
                "status": result["status"],
                "count": result["count"],
                "doc": result["doc"],
                "doc_ids": [c["doc_id"] for c in result["chunks"]],
            },
            ensure_ascii=False,
        ))
    else:
        print(f"[{result['status']}] {result['count']} 个功能介绍分片已处理（{result['doc']}）")
        for c in result["chunks"]:
            print(f"  - {c['doc_id']:<42} tags={','.join(c['tags'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
