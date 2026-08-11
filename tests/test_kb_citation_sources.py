"""M-3 知识库来源引用链集成测试（架构 kb-citation-architecture-2026-08-10 §5 T02）。

覆盖（T02 验收标准 + 全局一致性 K-1/K-2/K-3/K-4/K-6）：
1. mock 油温问题 → 节点 update 含 knowledge_answer.sources（2 条，
   与正文《变压器运行规程》第 4.2 节 /《电力设备故障诊断手册》一致，K-4）
2. mock 过载 / 停机检修 / 兜底 / 功能介绍 各返回匹配 sources；剧本外无 sources
3. ``_extract_knowledge_answer_from_results`` 从工具结果字符串 JSON 反解
4. ``_attach_knowledge_answer`` 仅 knowledge_agent 注入（其他 Agent no-op）
5. ``/chat/stream`` done 事件携带 knowledge_answer.sources（K-6 端到端，
   TestClient + 假 graph_builder）；其他 Agent 轮次不出现该键
6. Chroma ``_distance_to_score(0.13) == 0.87``；keyword fallback score 非 0
7. ``retrieve()`` sources 长度与 vector_chunks 一致；``answer()`` sources
   按 score 降序、过滤 <0.25、截断 ≤top_n；``citations`` 仍为全部副本（K-3）

运行：
    python -m pytest tests/test_kb_citation_sources.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("MOCK_ENABLED", "true")
os.environ["JWT_SECRET"] = "test-jwt-secret-kb-cite-32bytes-required-pad!"
os.environ["ADMIN_TOKEN"] = "test-admin-token-kb-cite"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from api.schemas import KnowledgeAnswer, SourceRef  # noqa: E402
from core.kb_upload import KbUploadService  # noqa: E402


def _run(coro):  # 兼容无 pytest-asyncio 的同步 runner
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════
# fixture：隔离 SQLite + Chroma（对齐 test_kb_upload_rag.py 的 rag_env）
# ═══════════════════════════════════════════════════════


@pytest.fixture
def kb_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """隔离 SQLite + Chroma，重置 VectorStore 单例（feature-intro mock 用）。"""
    db_path = tmp_path / "kb_cite.db"
    chroma_dir = tmp_path / "chroma_kb_cite"

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


# ═══════════════════════════════════════════════════════
# 1. mock 剧本 sources（K-4 与正文「📄 引用来源」一致）
# ═══════════════════════════════════════════════════════


class TestMockKnowledgeSources:
    """mock 油温 / 过载 / 停机检修 / 兜底 / 功能介绍 sources 断言。"""

    def test_mock_oil_temperature_sources(self) -> None:
        from api.agents.agent_factory import _build_mock_knowledge_answer

        ka = _run(_build_mock_knowledge_answer("变压器油温异常怎么办", "mock正文"))
        assert ka is not None
        assert len(ka.sources) == 2, "油温剧本应返回 2 条结构化来源"
        titles = [(s.title, s.section) for s in ka.sources]
        assert titles[0] == ("变压器运行规程", "4.2"), "第一条应来自《变压器运行规程》第 4.2 节"
        assert titles[1][0] == "电力设备故障诊断手册", "第二条应来自《电力设备故障诊断手册》"
        # 契约约束：score 0-1、snippet ≤120、content_excerpt ≥200
        for s in ka.sources:
            assert 0 <= s.score <= 1
            assert s.snippet and len(s.snippet) <= 121
            assert s.content_excerpt and len(s.content_excerpt) >= 200
            assert s.doc_id and s.doc_id.startswith("user-upload:mock-"), "K-4 mock doc_id 用 user-upload:mock-*"
        # 正文一致性（K-4）：answer 与 mock 正文同源
        assert ka.answer == "mock正文"
        assert ka.refuse is False

    def test_mock_overload_sources(self) -> None:
        from api.agents.agent_factory import _build_mock_knowledge_answer

        ka = _run(_build_mock_knowledge_answer("变压器过载如何处置"))
        assert ka is not None
        assert len(ka.sources) == 2
        assert ka.sources[0].title == "变压器运行规程"
        assert ka.sources[0].section == "6.1", "过载剧本第一条应来自第 6.1 节"
        assert all(s.score is None or 0 <= s.score <= 1 for s in ka.sources)

    def test_mock_shutdown_sources(self) -> None:
        # C-1：停机检修 knowledge 分支防御性实现（mock Supervisor 实际路由到
        # diagnosis_agent，本分支保留 + sources，不改路由）
        from api.agents.agent_factory import _build_mock_knowledge_answer

        ka = _run(_build_mock_knowledge_answer("变压器停机检修流程"))
        assert ka is not None
        assert len(ka.sources) == 2
        assert ka.sources[0].title == "变压器运行规程"
        assert ka.sources[0].section == "6.2", "停机检修剧本第一条应来自第 6.2 节"

    def test_mock_fallback_sources(self) -> None:
        from api.agents.agent_factory import _build_mock_knowledge_answer

        ka = _run(_build_mock_knowledge_answer("随便问一个知识问题"))
        assert ka is not None
        assert len(ka.sources) == 1
        assert ka.sources[0].title == "电力设备运行规程", "兜底剧本应来自《电力设备运行规程》通用章节"

    def test_mock_out_of_script_empty_msg(self) -> None:
        from api.agents.agent_factory import _build_mock_knowledge_answer

        assert _run(_build_mock_knowledge_answer("")) is None, "空消息不应注入 knowledge_answer"

    def test_mock_feature_intro_sources(self, kb_env: dict[str, object]) -> None:
        """feature-intro 通道 mock 复用 search_feature_intro 的 chunk 构建 sources。"""
        from core.vector_store import get_vector_store
        from api.agents.agent_factory import _build_mock_knowledge_answer

        store = get_vector_store()
        store.upsert_chunks([
            {
                "doc_id": "feature-intro:view-chat",
                "title": "对话视图 chat",
                "content": (
                    "对话视图是 GridMind 的核心页面之一，用于与多 Agent 系统进行"
                    "自然语言交互。用户可以通过对话视图发起设备监控、故障诊断、"
                    "知识库检索等任务，并查看 Agent 的实时回答与来源引用。"
                    "对话视图支持 SSE 流式输出、HITL 人工审批弹窗和可解释性"
                    "推理链展示，是调度员日常值班的核心工作界面。"
                ),
                "source": "docs/gridmind-feature-introduction.md",
                "tags": ["feature-intro", "kind:view"],
                "meta": {"section": "2.1", "kind": "view"},
            }
        ])
        ka = _run(_build_mock_knowledge_answer("介绍一下功能介绍", "mock"))
        assert ka is not None
        assert ka.sources, "feature-intro 命中时应构建结构化 sources"
        assert ka.sources[0].doc_id.startswith("feature-intro:"), "feature-intro 来源 doc_id 应为 feature-intro:*"
        assert ka.sources[0].section == "2.1"
        assert all(s.score is None or 0 <= s.score <= 1 for s in ka.sources)


# ═══════════════════════════════════════════════════════
# 2. 工具结果反解 + AgentState 注入
# ═══════════════════════════════════════════════════════


class TestExtractAndAttach:
    """``_extract_knowledge_answer_from_results`` / ``_attach_knowledge_answer``。"""

    def test_extract_from_tool_results(self) -> None:
        from api.agents.agent_factory import _extract_knowledge_answer_from_results

        ka_dict = {
            "answer": "测试回答",
            "citations": ["纯文本1"],
            "graph_paths": [["变压器", "包含", "油温监控"]],
            "confidence": 0.9,
            "refuse": False,
            "refuse_reason": None,
            "sources": [{"doc_id": "user-upload:d1", "title": "T1", "score": 0.8}],
        }
        results = [f"【query_knowledge_base】结果：{json.dumps(ka_dict, ensure_ascii=False)}"]
        ka = _extract_knowledge_answer_from_results(results)
        assert ka is not None
        assert ka.answer == "测试回答"
        assert len(ka.sources) == 1
        assert ka.sources[0].doc_id == "user-upload:d1"
        assert ka.sources[0].score == 0.8

    def test_extract_negative(self) -> None:
        from api.agents.agent_factory import _extract_knowledge_answer_from_results

        assert _extract_knowledge_answer_from_results([]) is None
        assert _extract_knowledge_answer_from_results(["【tool】结果：{'x': 1}"]) is None
        assert _extract_knowledge_answer_from_results(["无 JSON 的结果字符串"]) is None
        # 含 answer 键但结构非法 → None
        assert _extract_knowledge_answer_from_results(['{"answer": "a"'] ) is None

    def test_attach_noop_for_other_agents(self) -> None:
        from api.agents.agent_factory import _attach_knowledge_answer

        update = {"messages": []}
        out = _run(_attach_knowledge_answer("monitor_agent", update, last_user="变压器油温"))
        assert "knowledge_answer" not in out

    def test_attach_mock_injection(self) -> None:
        from api.agents.agent_factory import _attach_knowledge_answer

        update = {"messages": []}
        out = _run(_attach_knowledge_answer(
            "knowledge_agent", update, last_user="变压器油温异常", answer_text="mock",
        ))
        assert "knowledge_answer" in out
        assert isinstance(out["knowledge_answer"], KnowledgeAnswer)
        assert len(out["knowledge_answer"].sources) == 2

    def test_attach_preserves_existing(self) -> None:
        from api.agents.agent_factory import _attach_knowledge_answer

        existing = KnowledgeAnswer(answer="x", citations=[], graph_paths=[], confidence=0.5)
        update = {"messages": [], "knowledge_answer": existing}
        out = _run(_attach_knowledge_answer(
            "knowledge_agent", update, last_user="变压器油温",
        ))
        assert out["knowledge_answer"] is existing, "已显式设置时不得覆盖"


# ═══════════════════════════════════════════════════════
# 3. score 修正（K-2）与 RAG 数据契约（K-1/K-3）
# ═══════════════════════════════════════════════════════


class TestScoreAndContract:
    """Chroma distance→score、keyword fallback 元数据、sources 长度与过滤。"""

    def test_distance_to_score(self) -> None:
        from core.vector_store import VectorStore

        assert VectorStore._distance_to_score(0.13) == 0.87, "distance=0.13 → score=0.87"
        assert VectorStore._distance_to_score(-0.5) == 1.0, "负距离 clamp 到 1.0"
        assert VectorStore._distance_to_score(1.5) == 0.0, "超过 1 的相似度 clamp 到 0.0"
        assert VectorStore._distance_to_score("bad") == 0.0, "非法输入返回 0.0"

    def test_chroma_id_unique_per_chunk(self) -> None:
        """P2-F（C-2）：同文档多 chunk 的 Chroma id 必须唯一（不再互相覆盖）。"""
        from core.vector_store import VectorStore

        # 多 chunk 文档（带 SQLite 自增 chunk_id，真实入库路径）
        chunks = [
            {"doc_id": "user-upload:d-multi", "chunk_id": 101, "content": "第一段", "meta": {"chunk_index": 0, "total_chunks": 3}},
            {"doc_id": "user-upload:d-multi", "chunk_id": 102, "content": "第二段", "meta": {"chunk_index": 1, "total_chunks": 3}},
            {"doc_id": "user-upload:d-multi", "chunk_id": 103, "content": "第三段", "meta": {"chunk_index": 2, "total_chunks": 3}},
        ]
        ids = [VectorStore._chroma_id(c) for c in chunks]
        assert len(set(ids)) == 3, "同文档多 chunk id 必须唯一"
        assert ids[0] == "doc::user-upload:d-multi::c101", "id 应含 chunk_id 后缀"
        # 无 chunk_id 时回退 chunk_index（罕见路径，保证仍唯一）
        ids_fb = [
            VectorStore._chroma_id({"doc_id": "d2", "meta": {"chunk_index": 0}}),
            VectorStore._chroma_id({"doc_id": "d2", "meta": {"chunk_index": 1}}),
        ]
        assert len(set(ids_fb)) == 2
        # 老 seed 单 chunk 文档（无 chunk_id/chunk_index）保持裸 doc::{doc_id} 兼容
        assert VectorStore._chroma_id({"doc_id": "doc-001", "content": "x"}) == "doc::doc-001"

    def test_keyword_fallback_metadata(self, kb_env: dict[str, object]) -> None:
        """keyword fallback 返回 metadata 补齐 chunk_id/doc_id/title/source/filename/chunk_index/total_chunks。"""
        from core.vector_store import get_vector_store

        svc = KbUploadService()
        svc.ingest("引用测试规程.md", ("变压器油温异常时应立即检查冷却系统并安排油色谱分析。" * 12).encode("utf-8"))
        store = get_vector_store()
        store.ensure_fresh()
        hits = store.search("变压器油温异常", top_k=3, exclude_tags=["feature-intro"])
        assert hits, "keyword fallback 应命中上传分片"
        for h in hits:
            meta = h["metadata"]
            assert "chunk_id" in meta and meta["chunk_id"] is not None
            assert meta.get("doc_id"), "metadata 应含 doc_id"
            assert meta.get("title"), "metadata 应含 title"
            assert meta.get("filename"), "metadata 应含 filename（meta.filename 反解）"
            assert meta.get("chunk_index") is not None, "metadata 应含 chunk_index"
            assert meta.get("total_chunks") is not None, "metadata 应含 total_chunks"
            assert 0 <= h["score"] <= 1, "score 应归一化 0-1"
            assert h["score"] > 0, "命中分片 score 不应为 0（M-3 修复 Chroma 恒 0 问题）"

    def test_retrieve_sources_len_matches_vector_chunks(self, kb_env: dict[str, object]) -> None:
        """retrieve() sources 长度与 vector_chunks 一致；score 0-1；snippet ≤120。"""
        from core.vector_store import get_vector_store
        from core.rag_engine import RagEngine

        svc = KbUploadService()
        svc.ingest("引用测试规程.md", ("变压器油温异常时应立即检查冷却系统并安排油色谱分析。" * 12).encode("utf-8"))
        store = get_vector_store()
        store.ensure_fresh()
        engine = RagEngine(vector_store=store)
        result = engine.retrieve("变压器油温异常", top_k=3)
        assert len(result.sources) == len(result.vector_chunks), "K-3 并行构建：sources 与 vector_chunks 一一对应"
        for s in result.sources:
            assert s.doc_id, "业务路径 source 应含 doc_id"
            assert s.score is None or 0 <= s.score <= 1
            assert s.snippet is None or len(s.snippet) <= 121
            assert isinstance(s, SourceRef)

    def test_answer_filters_sorts_truncates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """answer() sources 按 score 降序、过滤 <0.25、截断 ≤top_n；citations 保留全部副本。"""
        from core.rag_engine import RagEngine

        class FakeVectorStore:
            def search(self, query, top_k=3, exclude_tags=None):
                return [
                    {"content": "A" * 300, "metadata": {"doc_id": "d1", "title": "T1", "chunk_id": 1}, "score": 0.9},
                    {"content": "B" * 300, "metadata": {"doc_id": "d2", "title": "T2", "chunk_id": 2}, "score": 0.1},
                    {"content": "C" * 300, "metadata": {"doc_id": "d3", "title": "T3", "chunk_id": 3}, "score": 0.5},
                    {"content": "D" * 300, "metadata": {"doc_id": "d4", "title": "T4", "chunk_id": 4}, "score": 0.7},
                ]

        class FakeGraph:
            def expand_entities(self, seed_ids, hops=2):
                return [], []

            def search_entities(self, keyword):
                return []

        engine = RagEngine(vector_store=FakeVectorStore(), knowledge_graph=FakeGraph())
        monkeypatch.setattr(engine, "_generate", lambda query, context: "测试回答")
        ka = engine.answer("测试", top_k=4)
        assert [s.score for s in ka.sources] == [0.9, 0.7, 0.5], "0.1 被过滤，其余按 score 降序"
        assert len(ka.citations) == 4, "citations 仍为全部 vector_chunks 副本（K-3）"
        assert all(0 <= s.score <= 1 for s in ka.sources)


# ═══════════════════════════════════════════════════════
# 4. SSE done 端到端（TestClient + 假 graph_builder，K-6）
# ═══════════════════════════════════════════════════════


class TestSseDonePayload:
    """``/chat/stream`` done 事件增量携带 knowledge_answer（K-6）。"""

    @staticmethod
    def _done_payload(client, path: str, params: dict) -> dict:
        resp = client.get(path, params=params)
        assert resp.status_code == 200, f"status={resp.status_code} text={resp.text[:300]}"
        for line in resp.text.splitlines():
            if not line.startswith("data: "):
                continue
            payload = line[len("data: "):].strip()
            if payload == "[DONE]":
                continue
            evt = json.loads(payload)
            if evt.get("type") == "done":
                return evt
        raise AssertionError("未找到 done 事件")

    def test_done_carries_knowledge_answer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fastapi.testclient import TestClient
        import api.main as main_mod

        class FakeBuilder:
            async def run(self, thread_id, message, display_mode=None, model_id=None):
                return {
                    "messages": [
                        {"role": "user", "content": message},
                        {"role": "assistant", "content": "【知识库 Agent】测试回答",
                         "metadata": {"agent_name": "knowledge_agent"}},
                    ],
                    "knowledge_answer": KnowledgeAnswer(
                        answer="测试回答",
                        citations=["纯文本1"],
                        graph_paths=[],
                        confidence=0.85,
                        refuse=False,
                        sources=[
                            SourceRef(doc_id="user-upload:mock-transformer-rules",
                                      filename="变压器运行规程.md", title="变压器运行规程",
                                      section="4.2", score=0.87),
                            SourceRef(doc_id="user-upload:mock-diagnosis-handbook",
                                      filename="电力设备故障诊断手册.md", title="电力设备故障诊断手册",
                                      score=0.72),
                        ],
                    ),
                }

        monkeypatch.setattr(main_mod, "graph_builder", FakeBuilder())
        monkeypatch.setattr(main_mod, "resolve_model", lambda thread_id: None)
        client = TestClient(main_mod.app)  # 不用 with：跳过 lifespan（既有测试模式）
        evt = self._done_payload(client, "/chat/stream/thread-kb-cite-1", {"message": "变压器油温异常"})
        assert "knowledge_answer" in evt, "knowledge_agent 轮次 done 事件应携带 knowledge_answer"
        ka = evt["knowledge_answer"]
        assert len(ka["sources"]) == 2
        assert ka["sources"][0]["title"] == "变压器运行规程"
        assert ka["sources"][0]["section"] == "4.2"
        # 既有字段保持（K-6 向后兼容）
        assert evt["type"] == "done"
        assert evt["interrupt_required"] is False

    def test_done_omits_key_for_other_agents(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fastapi.testclient import TestClient
        import api.main as main_mod

        class FakeBuilder:
            async def run(self, thread_id, message, display_mode=None, model_id=None):
                return {
                    "messages": [
                        {"role": "assistant", "content": "【监控 Agent】运行正常",
                         "metadata": {"agent_name": "monitor_agent"}},
                    ],
                }

        monkeypatch.setattr(main_mod, "graph_builder", FakeBuilder())
        monkeypatch.setattr(main_mod, "resolve_model", lambda thread_id: None)
        client = TestClient(main_mod.app)
        evt = self._done_payload(client, "/chat/stream/thread-kb-cite-2", {"message": "设备状态"})
        assert "knowledge_answer" not in evt, "其他 Agent 轮次不得出现 knowledge_answer 键（K-6）"

    def test_chat_blocking_backfills_knowledge_answer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """C-4：阻塞 /chat 路径防御性补齐 ChatResponse.knowledge_answer。"""
        from fastapi.testclient import TestClient
        import api.main as main_mod

        class FakeBuilder:
            async def run(self, thread_id, message, display_mode=None, model_id=None):
                return {
                    "messages": [
                        {"role": "assistant", "content": "测试回答",
                         "metadata": {"agent_name": "knowledge_agent"}},
                    ],
                    "knowledge_answer": KnowledgeAnswer(
                        answer="测试回答", citations=[], graph_paths=[], confidence=0.8,
                        refuse=False,
                        sources=[SourceRef(doc_id="user-upload:mock-transformer-rules",
                                           title="变压器运行规程", section="4.2", score=0.87)],
                    ),
                }

        monkeypatch.setattr(main_mod, "graph_builder", FakeBuilder())
        monkeypatch.setattr(main_mod, "resolve_model", lambda thread_id: None)
        client = TestClient(main_mod.app)
        resp = client.post("/chat", json={
            "thread_id": "thread-kb-cite-3", "message": "变压器油温",
        })
        assert resp.status_code == 200, f"status={resp.status_code} text={resp.text[:300]}"
        body = resp.json()
        assert body["knowledge_answer"] is not None
        assert body["knowledge_answer"]["sources"][0]["title"] == "变压器运行规程"
