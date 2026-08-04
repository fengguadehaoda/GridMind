"""Chroma 向量库管理——知识库文档片段的 embedding 入库与检索。

使用 DashScope embedding（dashscope SDK 直连），
内存/本地持久化模式，无需启动外部向量数据库。
"""

from __future__ import annotations

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

    def _load_chunks(self) -> None:
        """从 SQLite 加载知识库片段到内存，并尝试写入 Chroma。"""
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT chunk_id, doc_id, title, content, source FROM knowledge_chunks"
            ).fetchall()
        finally:
            conn.close()

        self._chunks = [dict(r) for r in rows]

        if self._collection is not None and self._collection.count() == 0:
            texts = [c["content"] for c in self._chunks]
            ids = [f"chunk-{c['chunk_id']}" for c in self._chunks]
            metas = [{"doc_id": c["doc_id"], "title": c["title"], "source": c.get("source", "")}
                     for c in self._chunks]
            try:
                # 尝试用 DashScope embedding；无 Key 或无网络时 fallback 到 keyword
                embeddings = _get_embedding(texts, settings.dashscope_api_key)
                if embeddings is not None:
                    self._collection.add(ids=ids, embeddings=embeddings, documents=texts, metadatas=metas)
                    logger.info("VectorStore: indexed {} chunks with DashScope embeddings", len(ids))
                else:
                    # 不给 embeddings → Chroma 会下载默认 ONNX 模型，跳过以避免首次安装超时
                    logger.info("VectorStore: DashScope embedding unavailable, using keyword fallback")
            except Exception as e:
                logger.warning("Chroma add failed ({}), using fallback only", e)

    def search(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        """向量检索，返回 top_k 个候选片段。"""
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
                    return [
                        {"content": docs[i], "metadata": metas[i], "score": 0.0}
                        for i in range(len(docs))
                    ]
            except Exception as e:
                logger.warning("Chroma query failed ({}), fallback to keyword", e)

        # Fallback: 简单关键词打分
        return self._keyword_fallback(query, top_k)

    def _keyword_fallback(self, query: str, top_k: int) -> list[dict[str, Any]]:
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

        scored: list[tuple[float, dict[str, Any]]] = []
        for c in self._chunks:
            text = c["content"].lower()
            score = sum(1 for t in tokens if t in text) / (len(tokens) or 1)
            if score > 0:
                scored.append((score, c))

        scored.sort(key=lambda x: -x[0])
        return [
            {"content": c["content"], "metadata": {
                "doc_id": c["doc_id"],
                "title": c["title"],
                "source": c.get("source", ""),
            }, "score": round(s, 3)}
            for s, c in scored[:top_k]
        ]

    def count(self) -> int:
        return len(self._chunks)
