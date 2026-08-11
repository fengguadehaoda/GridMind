"""Chroma 向量库管理——知识库文档片段的 embedding 入库与检索。

使用 DashScope embedding（dashscope SDK 直连），
内存/本地持久化模式，无需启动外部向量数据库。

**功能介绍知识库化增量**（2026-08-05）：
- :meth:`VectorStore.upsert_chunks` —— 按 ``doc_id`` 覆盖式写入分片（SQLite + Chroma 同步）
- :meth:`VectorStore.search_by_tag` —— 按 tag 精确过滤（从内存 ``_chunks`` 过滤，无检索噪声）
- :meth:`VectorStore.reload`        —— 重新从 SQLite 加载内存分片
- :func:`get_vector_store`          —— 进程级单例，避免每次请求重建 Chroma client
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from loguru import logger

from api.config import settings
from mcp_tools.db.database import get_connection

# 尝试导入 embedding 模型；无 Key 时 fallback 到简单关键词匹配
try:
    from dashscope import TextEmbedding as DashTextEmbedding
    _dashscope_available = True
except Exception:
    _dashscope_available = False

try:
    import chromadb
    from chromadb.config import Settings as ChromaSettings
except ImportError:
    chromadb = None  # type: ignore[assignment]


#: ``knowledge_chunks`` 表中「功能介绍」相关的元信息列（由 database._ensure_knowledge_chunks_columns 建）
#: 老库（未迁移）读不到这些列时，本模块会自动降级为空值，保持向后兼容。
_META_COLUMNS: tuple[str, ...] = ("tags", "icon", "starter_message", "meta", "updated_at")


def _split_tags(raw: Any) -> list[str]:
    """把 DB 中的逗号分隔标签串解析为去空的标签列表。

    Args:
        raw: DB 原始值，可能为 ``None`` / ``str`` / 已是 ``list``。

    Returns:
        标签列表（顺序保留、去空白、去重且保序）。
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        items = [str(t).strip() for t in raw]
    else:
        items = [t.strip() for t in str(raw).split(",")]
    seen: set[str] = set()
    result: list[str] = []
    for t in items:
        if t and t not in seen:
            seen.add(t)
            result.append(t)
    return result


def _join_tags(tags: Any) -> str:
    """把标签列表序列化为逗号分隔串（写库用）。"""
    return ",".join(_split_tags(tags))


def _loads_meta(raw: Any) -> dict[str, Any]:
    """安全解析 ``meta`` JSON 列；非法 JSON 一律返回空 dict。"""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw))
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _get_embedding(texts: list[str], api_key: str) -> list[list[float]] | None:
    """使用 DashScope TextEmbedding API 获取向量。"""
    if not _dashscope_available:
        return None
    try:
        resp = DashTextEmbedding.call(
            model="text-embedding-v2",
            input=texts if len(texts) == 1 else texts,
            api_key=api_key,
        )
        if resp.status_code == 200 and resp.output and resp.output.embeddings:
            return [e["embedding"] for e in resp.output.embeddings]
        logger.warning("DashScope embedding failed: {}", resp.message)
    except Exception as e:
        logger.warning("DashScope embedding error: {}", e)
    return None


class VectorStore:
    """Chroma 向量库封装（内存/本地模式）。"""

    def __init__(self, collection_name: str = "knowledge_base") -> None:
        self.collection_name = collection_name
        self._client: Any = None
        self._collection: Any = None
        self._chunks: list[dict[str, Any]] = []
        # V1.6 P0-5 增补件 §3.2：跨进程热更新 revision 自检
        self._revision: str = "0"
        self._last_revision_check: float = 0.0
        self._init_client()

    def _init_client(self) -> None:
        if chromadb is None:
            logger.warning("chromadb not available, vector search disabled")
            return

        persist = settings.chroma_persist_dir
        try:
            if persist:
                Path(persist).mkdir(parents=True, exist_ok=True)
                self._client = chromadb.PersistentClient(
                    path=persist,
                    settings=ChromaSettings(anonymized_telemetry=False),
                )
            else:
                self._client = chromadb.EphemeralClient(
                    settings=ChromaSettings(anonymized_telemetry=False),
                )
        except Exception as e:
            logger.warning("Chroma init failed ({}), falling back to in-memory-only mode", e)
            self._client = None

        if self._client is not None:
            try:
                self._collection = self._client.get_or_create_collection(
                    name=self.collection_name,
                    metadata={"hnsw:space": "cosine"},
                )
            except Exception as e:
                logger.warning("Chroma collection init failed ({}), falling back", e)
                self._collection = None

        # 从 SQLite 加载知识库片段
        self._load_chunks()

    @staticmethod
    def _select_sql() -> str | None:
        """构造 SELECT 语句——按实际存在的列自动降级（兼容未迁移的老库）。

        B2 修复：DB 缺失 ``knowledge_chunks`` 表（纯 API 部署未先 seed）时，
        返回 ``None``（调用方降级为空分片），不再抛 ``OperationalError``。

        Returns:
            完整的 ``SELECT ... FROM knowledge_chunks`` SQL 字符串；
            表不存在或读取失败时返回 ``None``。
        """
        try:
            conn = get_connection()
            try:
                existing = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(knowledge_chunks)").fetchall()
                }
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001 — 冷启动表缺失必须降级不抛
            logger.warning(
                "VectorStore._select_sql: 读取 knowledge_chunks 表结构失败，"
                "降级为空分片：{}", e,
            )
            return None

        cols = ["chunk_id", "doc_id", "title", "content", "source"]
        cols.extend(c for c in _META_COLUMNS if c in existing)
        return f"SELECT {', '.join(cols)} FROM knowledge_chunks"

    def _load_chunks(self) -> None:
        """从 SQLite 加载知识库片段到内存，并尝试写入 Chroma。

        B2 修复：表缺失 / 查询失败时降级为空分片（log warning 不抛），
        保证 ``VectorStore()`` 在空 DB 上可正常实例化（``count()==0``）。
        """
        sql = self._select_sql()
        if sql is None:
            self._chunks = []
            return
        try:
            conn = get_connection()
            try:
                rows = conn.execute(sql).fetchall()
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001 — 冷启动表缺失必须降级不抛
            logger.warning(
                "VectorStore._load_chunks: 加载知识库分片失败，降级为空分片：{}", e,
            )
            self._chunks = []
            return

        self._chunks = [self._normalize_row(dict(r)) for r in rows]

        if self._collection is not None and self._collection.count() == 0:
            self._index_chunks(self._chunks)

    @staticmethod
    def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
        """把 DB 行标准化为统一内存结构（补齐缺失的元信息字段）。

        Args:
            row: ``sqlite3.Row`` 转成的 dict。

        Returns:
            含 ``tags`` (list) / ``icon`` / ``starter_message`` / ``meta`` (dict) 的标准化 dict。
        """
        row["tags"] = _split_tags(row.get("tags"))
        row["icon"] = row.get("icon") or None
        row["starter_message"] = row.get("starter_message") or None
        row["meta"] = _loads_meta(row.get("meta"))
        row["source"] = row.get("source") or ""
        return row

    def _index_chunks(self, chunks: list[dict[str, Any]]) -> None:
        """把给定分片写入 Chroma collection（best-effort，失败仅告警不抛）。

        Args:
            chunks: 已标准化的分片列表（须含 ``chunk_id`` / ``doc_id`` / ``title`` / ``content``）。
        """
        if self._collection is None or not chunks:
            return

        texts = [c["content"] for c in chunks]
        ids = [self._chroma_id(c) for c in chunks]
        metas: list[dict[str, Any]] = []
        for c in chunks:
            c_meta = c.get("meta") or {}
            entry: dict[str, Any] = {
                "doc_id": c["doc_id"],
                "title": c["title"],
                "source": c.get("source", "") or "",
                # Chroma metadata 只接受标量，tags 存逗号串
                "tags": _join_tags(c.get("tags")),
            }
            # M-3：metadata 追加结构化字段（None 会被 Chroma 拒绝 → 仅写非空标量）
            if c.get("chunk_id") is not None:
                entry["chunk_id"] = c["chunk_id"]
            if c_meta.get("filename"):
                entry["filename"] = str(c_meta["filename"])
            if c_meta.get("chunk_index") is not None:
                entry["chunk_index"] = int(c_meta["chunk_index"])
            if c_meta.get("total_chunks") is not None:
                entry["total_chunks"] = int(c_meta["total_chunks"])
            if c_meta.get("section"):
                entry["section"] = str(c_meta["section"])
            metas.append(entry)

        try:
            # 尝试用 DashScope embedding；无 Key 或无网络时 fallback 到 keyword
            embeddings = _get_embedding(texts, settings.dashscope_api_key)
            if embeddings is not None:
                self._collection.upsert(
                    ids=ids, embeddings=embeddings, documents=texts, metadatas=metas,
                )
                logger.info("VectorStore: indexed {} chunks with DashScope embeddings", len(ids))
            else:
                # 不给 embeddings → Chroma 会下载默认 ONNX 模型，跳过以避免首次安装超时
                logger.info("VectorStore: DashScope embedding unavailable, using keyword fallback")
        except Exception as e:
            logger.warning("Chroma add failed ({}), using fallback only", e)

    @staticmethod
    def _chroma_id(chunk: dict[str, Any]) -> str:
        """生成 Chroma 文档 id —— ``doc_id`` 主键 + chunk 唯一后缀。

        P2-F（C-2）：此前恒为 ``doc::{doc_id}``，同一文档多 chunk 会互相覆盖
        （Chroma ``upsert`` 同 id 覆盖 → 只保留最后一个 chunk）。现在追加 chunk
        唯一后缀：
        - ``chunk_id``（SQLite 自增主键，稳定唯一）→ ``doc::{doc_id}::c{chunk_id}``
        - 罕见无 chunk_id 的分片回退 ``meta.chunk_index`` → ``doc::{doc_id}::i{idx}``
        - 两者皆缺再回退裸 ``doc::{doc_id}``（老 seed 一 doc 一 chunk 天然唯一）。

        兼容性：检索结果补全（:meth:`_enrich_search_result`）与按 ``doc_id``
        元数据删除（:meth:`_remove_from_chroma` where 过滤）都不依赖 id 具体格式，
        新旧 id 可共存；存量 Chroma 集合（旧 ``doc::{doc_id}`` 单条目）不受影响，
        该文档重新上传时会被 where 删除清理后重建（幂等）。

        Args:
            chunk: 分片 dict。

        Returns:
            Chroma id 字符串（同文档不同 chunk 唯一）。
        """
        doc_id = str(chunk.get("doc_id") or "").strip()
        if not doc_id:
            return f"chunk-{chunk.get('chunk_id', 0)}"
        chunk_id = chunk.get("chunk_id")
        if chunk_id is not None:
            return f"doc::{doc_id}::c{chunk_id}"
        meta = chunk.get("meta") or {}
        chunk_index = meta.get("chunk_index")
        if chunk_index is not None:
            return f"doc::{doc_id}::i{chunk_index}"
        return f"doc::{doc_id}"

    @staticmethod
    def _distance_to_score(distance: float) -> float:
        """把 Chroma 余弦距离换算为相似度 score（0-1，K-2）。

        cosine space 下距离 ∈ [0, 2]；``1 - distance`` 对相关片段通常在
        [0, 1] 区间，越界部分 clamp 到 [0, 1] 后再 ``round(3)``。
        M-3 修复：此前 Chroma 分支 score 恒为 0.0（PRD 现状核查 #6）。

        Args:
            distance: Chroma 返回的距离值（float 或可转 float 的标量）。

        Returns:
            归一化相似度（0-1，3 位小数）；非法输入返回 0.0。
        """
        try:
            d = float(distance)
        except (TypeError, ValueError):
            return 0.0
        return round(min(1.0, max(0.0, 1.0 - d)), 3)

    def _enrich_search_result(self, result: dict[str, Any]) -> dict[str, Any]:
        """补齐 search 返回项的 metadata（chunk_id/filename/chunk_index/total_chunks/section）。

        Chroma metadata 在建索引时只写入标量（None 会被 Chroma 拒绝），且老库
        Chroma 集合可能缺这些字段——因此这里以 ``self._chunks``（SQLite 权威
        事实源）兜底补齐。匹配策略：优先 ``chunk_id`` 精确匹配；退化为
        ``doc_id + content`` 双键匹配（老 seed 分片无独立 chunk_id）。

        Args:
            result: ``search()`` 单个返回项（含 ``content`` / ``metadata`` / ``score``）。

        Returns:
            补齐 metadata 后的同一返回项（缺失字段保留 None/空串，绝不抛错）。
        """
        meta = dict(result.get("metadata") or {})
        content = result.get("content", "")
        match: dict[str, Any] | None = None

        chunk_id = meta.get("chunk_id")
        if chunk_id is not None:
            for c in self._chunks:
                if c.get("chunk_id") == chunk_id:
                    match = c
                    break
        if match is None:
            doc_id = str(meta.get("doc_id") or "")
            for c in self._chunks:
                if str(c.get("doc_id") or "") == doc_id and c.get("content") == content:
                    match = c
                    break

        if match is not None:
            m = match.get("meta") or {}
            meta.setdefault("chunk_id", match.get("chunk_id"))
            meta.setdefault("filename", m.get("filename"))
            meta.setdefault("chunk_index", m.get("chunk_index"))
            meta.setdefault("total_chunks", m.get("total_chunks"))
            meta.setdefault("section", m.get("section"))
            # source / title / doc_id 兜底（keyword fallback 可能缺失）
            if not meta.get("source") and match.get("source"):
                meta["source"] = match["source"]
            if not meta.get("title") and match.get("title"):
                meta["title"] = match["title"]
            if not meta.get("doc_id") and match.get("doc_id"):
                meta["doc_id"] = match["doc_id"]

        result["metadata"] = meta
        return result

    def search(
        self,
        query: str,
        top_k: int = 3,
        exclude_tags: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """向量检索（带可选 tag 黑名单过滤，P0-5+ 防 RAG 反向污染）。

        Args:
            query: 检索关键词。
            top_k: 返回上限。
            exclude_tags: 排除命中任一这些 tag 的分片；用于把 feature-intro
                等命名空间标签从业务查询结果中剔除（默认 ``None`` 行为不变）。

        Returns:
            过滤后的分片列表。
        """
        exclude_set = set(exclude_tags or [])

        if self._collection is not None and _dashscope_available:
            try:
                q_emb = _get_embedding([query], settings.dashscope_api_key)
                if q_emb is not None:
                    results = self._collection.query(
                        query_embeddings=[q_emb[0]],
                        n_results=min(top_k, len(self._chunks) or 1),
                    )
                    docs = results.get("documents", [[]])[0]
                    metas = results.get("metadatas", [[]])[0]
                    # M-3 修复（P0-2）：读取真实 distances → score = clamp(1-distance, 0, 1)
                    distances = results.get("distances", [[]])[0]
                    raw: list[dict[str, Any]] = [
                        {
                            "content": docs[i],
                            "metadata": metas[i],
                            "score": self._distance_to_score(
                                distances[i] if i < len(distances) else 0.0
                            ),
                            "tags": _split_tags(metas[i].get("tags", "")),
                        }
                        for i in range(len(docs))
                    ]
                    if exclude_set:
                        raw = [
                            r for r in raw
                            if not (set(r.get("tags") or []) & exclude_set)
                        ]
                    # 用 SQLite 权威分片补齐 metadata（chunk_id/filename/chunk_index/...）
                    return [self._enrich_search_result(r) for r in raw[:top_k]]
            except Exception as e:
                logger.warning("Chroma query failed ({}), fallback to keyword", e)

        # Fallback: 简单关键词打分（内部按 exclude_tags 过滤）
        return self._keyword_fallback(query, top_k, exclude_tags=exclude_tags)

    def _keyword_fallback(
        self,
        query: str,
        top_k: int,
        exclude_tags: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """关键词匹配 fallback——支持中文子串匹配 + 词匹配。"""
        q_lower = query.lower()
        # 对中文，拆成单个汉字 + 英文单词
        import re
        tokens: list[str] = []
        # 英文/数字词
        tokens.extend(re.findall(r'[a-z0-9]+', q_lower))
        # 中文双字及以上 n-gram（优先 2-4 字片段）
        chinese_chars = re.findall(r'[\u4e00-\u9fff]+', q_lower)
        for cc in chinese_chars:
            # 2-gram 及完整短语
            if len(cc) >= 2:
                for i in range(len(cc) - 1):
                    tokens.append(cc[i:i+2])
            tokens.append(cc)  # 完整中文短语

        exclude_set = set(exclude_tags or [])
        scored: list[tuple[float, dict[str, Any]]] = []
        for c in self._chunks:
            # tag 黑名单过滤（任一命中即排除，先于打分避免空转）
            if exclude_set and (set(c.get("tags") or []) & exclude_set):
                continue
            text = c["content"].lower()
            score = sum(1 for t in tokens if t in text) / (len(tokens) or 1)
            if score > 0:
                scored.append((score, c))

        scored.sort(key=lambda x: -x[0])
        results: list[dict[str, Any]] = []
        for s, c in scored[:top_k]:
            m = c.get("meta") or {}
            results.append({
                "content": c["content"],
                "metadata": {
                    "doc_id": c["doc_id"],
                    "title": c["title"],
                    "source": c.get("source", ""),
                    # M-3：keyword fallback 同步补齐结构化元信息（K-2/元数据契约）
                    "chunk_id": c.get("chunk_id"),
                    "filename": m.get("filename"),
                    "chunk_index": m.get("chunk_index"),
                    "total_chunks": m.get("total_chunks"),
                    "section": m.get("section"),
                },
                "score": round(s, 3),
                "tags": list(c.get("tags") or []),
            })
        # 与 Chroma 分支同一补齐通道（保证两份路径 metadata 字段一致）
        return [self._enrich_search_result(r) for r in results]

    # ═══════════════════════════════════════════════════════
    # 功能介绍知识库化（2026-08-05 新增）
    # ═══════════════════════════════════════════════════════

    def upsert_chunks(self, chunks: list[dict[str, Any]]) -> int:
        """覆盖式写入知识库分片（SQLite + Chroma 同步）。

        去重策略：按 ``doc_id`` 先 ``DELETE FROM knowledge_chunks WHERE doc_id=?``
        再 ``INSERT``，保证重复执行不产生冗余行（幂等）。写库成功后重新
        :meth:`reload` 内存分片，并把本批分片 upsert 进 Chroma collection。

        Args:
            chunks: 分片列表，每项支持以下键（``doc_id`` / ``title`` / ``content`` 必填）::

                {
                    "doc_id": "feature-intro:tour-chat",
                    "title": "chat 页面 tour",
                    "content": "……",
                    "source": "docs/gridmind-feature-introduction.md",
                    "tags": ["feature-intro", "tour:chat"],   # list 或逗号串
                    "icon": "Monitor",                          # 可选
                    "starter_message": "……",                    # 可选（亦接受 starterMessage）
                    "meta": {"steps": [...]},                   # 可选，dict 或 JSON 串
                }

        Returns:
            实际写入的分片数量。

        Raises:
            ValueError: 任一分片缺少 ``doc_id`` / ``title`` / ``content``。
        """
        if not chunks:
            logger.info("VectorStore.upsert_chunks: empty payload, nothing to do")
            return 0

        # 1. 参数校验 + 规范化（先全量校验，避免写一半失败）
        normalized: list[dict[str, Any]] = []
        for idx, raw in enumerate(chunks):
            doc_id = str(raw.get("doc_id") or "").strip()
            title = str(raw.get("title") or "").strip()
            content = str(raw.get("content") or "").strip()
            if not doc_id or not title or not content:
                raise ValueError(
                    f"upsert_chunks: chunk[{idx}] 缺少必填字段 "
                    f"(doc_id={doc_id!r}, title={title!r}, content_len={len(content)})"
                )
            starter = raw.get("starter_message")
            if starter is None:
                starter = raw.get("starterMessage")
            meta_val = raw.get("meta")
            meta_json = (
                json.dumps(meta_val, ensure_ascii=False)
                if isinstance(meta_val, (dict, list)) and meta_val
                else (str(meta_val) if isinstance(meta_val, str) and meta_val else None)
            )
            normalized.append({
                "doc_id": doc_id,
                "title": title,
                "content": content,
                "source": str(raw.get("source") or ""),
                "tags": _join_tags(raw.get("tags")),
                "icon": (str(raw["icon"]).strip() or None) if raw.get("icon") else None,
                "starter_message": (str(starter).strip() or None) if starter else None,
                "meta": meta_json,
            })

        # 2. 写 SQLite（单事务：先按 doc_id 删旧，再批量插新）
        conn = get_connection()
        try:
            existing = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(knowledge_chunks)").fetchall()
            }
            has_meta_cols = all(c in existing for c in _META_COLUMNS)

            doc_ids = [(c["doc_id"],) for c in normalized]
            conn.executemany("DELETE FROM knowledge_chunks WHERE doc_id = ?", doc_ids)

            if has_meta_cols:
                conn.executemany(
                    "INSERT INTO knowledge_chunks"
                    "(doc_id, title, content, source, tags, icon, starter_message, meta, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?, datetime('now','localtime'))",
                    [
                        (
                            c["doc_id"], c["title"], c["content"], c["source"],
                            c["tags"], c["icon"], c["starter_message"], c["meta"],
                        )
                        for c in normalized
                    ],
                )
            else:
                # 老库未迁移：降级为 4 列写入（tags 等元信息丢失，但不阻塞主流程）
                logger.warning(
                    "VectorStore.upsert_chunks: knowledge_chunks 缺少元信息列，"
                    "降级为 4 列写入（请执行 init_db() 完成迁移）"
                )
                conn.executemany(
                    "INSERT INTO knowledge_chunks(doc_id, title, content, source) VALUES (?,?,?,?)",
                    [(c["doc_id"], c["title"], c["content"], c["source"]) for c in normalized],
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        # 3. 重载内存分片（拿到自增 chunk_id）+ 同步 Chroma
        self.reload()
        touched = {c["doc_id"] for c in normalized}
        # P2-F（C-2）：重传前先按 doc_id 清掉该文档旧 Chroma 条目（旧 doc::{doc_id}
        # 单条目 + 新 doc::{doc_id}::c{chunk_id} 多条目），避免 SQLite DELETE+INSERT
        # 后 chunk_id 变化导致陈旧向量残留（幂等，重传不产生重复条目）。
        # SQLite 是权威事实源，清理失败仅告警、不阻断主流程。
        if self._collection is not None:
            for doc_id in touched:
                try:
                    self._collection.delete(where={"doc_id": doc_id})
                except Exception as e:  # noqa: BLE001 — 清理失败不阻断主流程
                    logger.warning(
                        "Chroma pre-cleanup failed for {} ({}); SQLite is authoritative",
                        doc_id, e,
                    )
        self._index_chunks([c for c in self._chunks if c["doc_id"] in touched])

        # 4. V1.6 P0-5 增补件 §3.2：与分片写入同一事务后 bump kb_revision，
        #    通知其他进程（MCP 9901）后续 ``ensure_fresh()`` 自检触发重载。
        self._bump_revision()

        logger.info(
            "VectorStore.upsert_chunks: {} chunks upserted (total in store: {})",
            len(normalized), len(self._chunks),
        )
        return len(normalized)

    def reload(self) -> int:
        """重新从 SQLite 加载内存分片（不重建 Chroma collection）。

        B2 修复：表缺失 / 查询失败时降级为空分片（log warning 不抛）。

        Returns:
            重载后的分片总数（表缺失时为 0）。
        """
        sql = self._select_sql()
        if sql is None:
            self._chunks = []
            return 0
        try:
            conn = get_connection()
            try:
                rows = conn.execute(sql).fetchall()
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001 — 冷启动表缺失必须降级不抛
            logger.warning(
                "VectorStore.reload: 加载知识库分片失败，降级为空分片：{}", e,
            )
            self._chunks = []
            return 0
        self._chunks = [self._normalize_row(dict(r)) for r in rows]
        return len(self._chunks)

    def delete_chunks(self, doc_id: str) -> int:
        """按 ``doc_id`` 物理删除全部分片（SQLite + Chroma 同步，幂等）。

        **命名空间守卫**（KB Upload 架构 §1.3 + 共享知识 §7.8）：仅允许
        ``user-upload:`` 前缀（用户上传知识库命名空间）；其它命名空间
        （``feature-intro:*`` / 老 seed ``doc-*``）一律返回 ``0``，绝不触碰，
        保证删除 / 覆盖操作不影响内置知识。

        执行链路（沿用 :meth:`upsert_chunks` 事务模式）：
        1. ``DELETE FROM knowledge_chunks WHERE doc_id = ?``（单事务）
        2. :meth:`reload` 重载内存分片
        3. 按 ``doc_id`` 元数据从 Chroma collection 移除该文档全部条目（best-effort）
        4. :meth:`_bump_revision` 写 ``kb_revision`` → 跨进程 ``ensure_fresh`` 热更新

        Args:
            doc_id: 目标文档 id（须以 ``user-upload:`` 开头）。

        Returns:
            实际删除的分片数量；非 user-upload 命名空间 / 不存在返回 0。
        """
        doc_id = (doc_id or "").strip()
        if not doc_id.startswith("user-upload:"):
            logger.warning(
                "VectorStore.delete_chunks: 拒绝删除非 user-upload 命名空间 doc_id={!r}",
                doc_id,
            )
            return 0

        conn = get_connection()
        try:
            cur = conn.execute("DELETE FROM knowledge_chunks WHERE doc_id = ?", (doc_id,))
            conn.commit()
            deleted = cur.rowcount
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        if deleted > 0:
            self.reload()
            self._remove_from_chroma(doc_id)
            self._bump_revision()
            logger.info(
                "VectorStore.delete_chunks: {} chunks deleted (doc_id={})",
                deleted, doc_id,
            )
        return deleted

    def _remove_from_chroma(self, doc_id: str) -> None:
        """从 Chroma collection 移除单个文档的**全部条目**（best-effort，失败仅告警）。

        P2-F（C-2）：改为按 ``doc_id`` 元数据过滤删除（``where={"doc_id": ...}``），
        兼容旧 id（``doc::{doc_id}`` 单条目）与新 id（``doc::{doc_id}::c{chunk_id}``
        多条目）；SQLite 是权威事实源，删除失败不影响主流程。

        Args:
            doc_id: 目标文档 id。
        """
        if self._collection is None:
            return
        try:
            self._collection.delete(where={"doc_id": doc_id})
            logger.debug("VectorStore: removed doc {} from Chroma", doc_id)
        except Exception as e:  # noqa: BLE001 — Chroma 失败仅告警
            logger.warning(
                "Chroma delete failed ({}); SQLite is authoritative", e,
            )

    def search_by_tag(self, tag: str | None = None, top_k: int = 20) -> list[dict[str, Any]]:
        """按 tag 精确过滤返回分片（从内存 ``_chunks`` 过滤，避免向量检索噪声）。

        每次调用前会惰性触发 :meth:`ensure_fresh`（节流 ≤5s）以感知跨进程
        热更新（API 进程 ``upsert_chunks`` 后 MCP 进程下次搜索即可看到）。
        节流内连续调用开销可忽略（一次 ``monotonic`` 比较）。

        Args:
            tag: 目标标签，如 ``feature-intro`` / ``scenario:monitor-overview`` /
                ``tour:chat``。传 ``None`` 或空串表示返回全部带标签的分片。
            top_k: 最多返回条数（``<= 0`` 表示不限制）。

        Returns:
            结构化分片列表，每项形如::

                {
                    "id": "tour-chat",                # meta.id，缺省回落到 doc_id
                    "doc_id": "feature-intro:tour-chat",
                    "title": "chat 页面 tour",
                    "content": "……",
                    "tags": ["feature-intro", "tour:chat"],
                    "icon": "Monitor" | None,
                    "starterMessage": "……" | None,
                    "source": "docs/gridmind-feature-introduction.md",
                    "meta": {...},
                }
        """
        # V1.6 P0-5 增补件 §3.2：每次 search 入口惰性自检（节流 ≤5s）
        self.ensure_fresh()

        wanted = (tag or "").strip()
        items: list[dict[str, Any]] = []
        for c in self._chunks:
            tags: list[str] = c.get("tags") or []
            if wanted:
                if wanted not in tags:
                    continue
            elif not tags:
                # 未指定 tag 时只返回带标签的分片（排除老 seed 的通用电力知识）
                continue
            items.append(self._to_item(c))
            if top_k > 0 and len(items) >= top_k:
                break
        return items

    @staticmethod
    def _to_item(chunk: dict[str, Any]) -> dict[str, Any]:
        """把内存分片映射为对外 API item 结构。

        Args:
            chunk: 已标准化的内存分片。

        Returns:
            对外结构化 item dict。
        """
        meta: dict[str, Any] = chunk.get("meta") or {}
        doc_id = str(chunk.get("doc_id") or "")
        item_id = str(meta.get("id") or "").strip()
        if not item_id:
            # doc_id 形如 "feature-intro:tour-chat" → 取冒号后半段作为业务 id
            item_id = doc_id.split(":", 1)[-1] if ":" in doc_id else doc_id
        return {
            "id": item_id,
            "doc_id": doc_id,
            "title": chunk.get("title", ""),
            "content": chunk.get("content", ""),
            "tags": list(chunk.get("tags") or []),
            "icon": chunk.get("icon"),
            "starterMessage": chunk.get("starter_message"),
            "source": chunk.get("source", ""),
            "meta": meta,
        }

    def count(self) -> int:
        return len(self._chunks)

    # ═══════════════════════════════════════════════════════
    # V1.6 P0-5 增补件 §3.2：跨进程热更新自检
    # ═══════════════════════════════════════════════════════

    #: ``ensure_fresh()`` 节流：最多每 5 秒查询一次 kb_meta.kb_revision
    _REVISION_CHECK_INTERVAL_S: float = 5.0

    @staticmethod
    def _read_revision() -> str:
        """从 SQLite 的 ``kb_meta`` 表读取当前知识库 revision。

        表不存在 / 行不存在 / 任何 DB 异常 → 返回 ``"0"``，调用方
        :meth:`ensure_fresh` 据此触发「首次加载」语义（reload 内存即可，
        不会重建 Chroma，因为首次构造时已经建过）。

        Returns:
            revision 字符串（约定首次写入为本地时间字符串；老库为 ``"0"``）。
        """
        try:
            conn = get_connection()
            try:
                row = conn.execute(
                    "SELECT value FROM kb_meta WHERE key = 'kb_revision'"
                ).fetchone()
                return str(row["value"]) if row else "0"
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001 — 任何 DB 异常都降级
            logger.warning("VectorStore._read_revision: 读取失败，降级为 '0'（{}）", e)
            return "0"

    def _bump_revision(self) -> None:
        """写入新的 ``kb_revision``（V1.6 P0-5 增补件 §3.2）。

        实现：``INSERT OR REPLACE`` 单行写入，使用 ``datetime('now','localtime')``
        作为新 revision 字符串（与既有 ``knowledge_chunks.updated_at`` 风格一致）。
        失败仅 warning，不抛——保证入仓主流程不被 revision 自检异常打断
        （共享知识 K-A6：自检失败绝不允许中断对话）。
        """
        try:
            conn = get_connection()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO kb_meta(key, value, updated_at) "
                    "VALUES ('kb_revision', datetime('now','localtime'), "
                    "        datetime('now','localtime'))"
                )
                conn.commit()
                # 本进程内立即同步，避免下次自检还要等 5s 节流
                self._revision = self._read_revision()
                self._last_revision_check = time.monotonic()
                logger.debug("VectorStore._bump_revision: kb_revision -> {}", self._revision)
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "VectorStore._bump_revision: 写入失败（kb_revision 落后），功能不受影响：{}",
                e,
            )

    def ensure_fresh(self) -> bool:
        """惰性检测知识库修订号；有变化则重载内存分片。

        **跨进程安全**：以共享 SQLite 的 ``kb_meta.kb_revision`` 为唯一事实源。
        API 进程（9900）执行 ``upsert_chunks`` 后会 bump revision，本进程（MCP 9901）
        下次 ``ensure_fresh()`` 调用即可看到并触发 ``_load_chunks()`` 重载，
        **无需重启服务**（PRD 验收 5）。

        行为：
            1. 节流：距上次检查 <5s 直接返回 ``False``，避免每请求一次 SELECT。
            2. 读取 ``kb_meta.kb_revision``，与本进程 ``self._revision`` 比较。
            3. 不同 → 重载内存分片（``self._chunks``）；Chroma 不重写
               （MCP 进程只读，单写者原则 K-A4）。
            4. 任何异常 → ``except`` 吞掉并返回 ``False``，**绝不中断对话**
               （共享知识 K-A6）。

        Returns:
            True 表示本次发生了重载；False 表示无需重载或异常降级。
        """
        now = time.monotonic()
        if now - self._last_revision_check < self._REVISION_CHECK_INTERVAL_S:
            return False
        self._last_revision_check = now

        try:
            remote = self._read_revision()
        except Exception as e:  # noqa: BLE001 — K-A6 兜底
            logger.warning("VectorStore.ensure_fresh: revision 读取失败，降级：{}", e)
            return False

        if remote == self._revision:
            return False

        # revision 变化 → 重载内存分片（不重建 Chroma 索引，遵循 K-A4）
        try:
            self.reload()
            self._revision = remote
            logger.info(
                "VectorStore.ensure_fresh: 检测到 revision 变化（{}），"
                "已重载 {} 条分片",
                remote, len(self._chunks),
            )
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "VectorStore.ensure_fresh: 重载失败，已降级（陈旧数据继续使用）：{}", e
            )
            return False


# ═══════════════════════════════════════════════════════
# 进程级单例（API 路由复用，避免每请求重建 Chroma client）
# ═══════════════════════════════════════════════════════

_store_singleton: VectorStore | None = None
_store_lock = threading.Lock()


def get_vector_store(collection_name: str = "knowledge_base") -> VectorStore:
    """获取进程级 :class:`VectorStore` 单例（线程安全，double-checked locking）。

    Args:
        collection_name: Chroma collection 名（仅首次调用生效）。

    Returns:
        全局共享的 :class:`VectorStore` 实例。
    """
    global _store_singleton
    if _store_singleton is None:
        with _store_lock:
            if _store_singleton is None:
                _store_singleton = VectorStore(collection_name=collection_name)
    return _store_singleton
