# GridMind（灵枢电网）M-3 · 知识库来源引用链 + 多文档对话 —— 系统架构设计 + 任务分解

**架构师**：高见远（Bob）　**日期**：2026-08-10　**基线**：v1.7.0 第一批（654 pytest passed）
**上游**：`docs/kb-citation-prd-2026-08-10.md`（主理人已拍板全部决策，见 §8 已确认项）
**范围**：仅 M-3；**明确不做** M-4（图谱问答 UI）、M-5（Agent 记忆）
**落盘**：`docs/kb-citation-architecture-2026-08-10.md`（本文档）+ `docs/kb-citation-class-diagram.mermaid` + `docs/kb-citation-sequence-diagram.mermaid`

---

## 一、实现方案 + 框架选型

### 1.1 核心难点与对策

| # | 难点 | 对策 |
|---|---|---|
| 1 | **来源信息在下游被丢弃**：`retrieve()` 返回纯文本 `vector_chunks`，chunk 元数据（doc_id/source/score）在 RAG 层被丢弃 | 不新增存储，在 RAG 层**透传 + 补齐**：`VectorStore.search()` 返回值已含 `metadata`，只需在 `RetrievalResult`/`KnowledgeAnswer` 增加 `sources` 字段并逐层透传 |
| 2 | **Chroma 路径 score 恒为 0.0** | `search()` 的 Chroma 分支读取 `results["distances"]`，按余弦距离换算相似度 `score = clamp(1 - distance, 0, 1)`；keyword fallback 已有 0-1 分数保留 |
| 3 | **`AgentState.knowledge_answer` 从未赋值、`attachContext` 从未被调用、SSE done 不携带 knowledge_answer**——整条链路是断的 | ① knowledge_agent 节点在工具结果 / mock 分支构建 `KnowledgeAnswer` 注入状态；② `/chat/stream` done 事件追加 `knowledge_answer` 字段（增量、向后兼容）；③ chatStore 在 done 事件捕获并 `attachContext({knowledgeAnswer})` |
| 4 | **mock 分支无结构化来源** | 在 agent_factory 增加 `_build_mock_knowledge_answer()`，油温/过载/停机检修/兜底各硬编码与正文「📄 引用来源」一致的 `sources`；feature-intro mock 通道复用 `search_feature_intro` 的 chunk 构建 `sources`（主理人拍板：纳入，成本低） |
| 5 | **score 量纲不一致**（Chroma 距离 vs keyword 命中率 vs feature-intro rerank） | 统一归一化到 **0-1**：Chroma `1-distance`；keyword 命中率天然 0-1；feature-intro rerank 得分**封顶 1.0**（`min(1.0, score)`）。`citation_min_score=0.25` 与拒答阈值对齐 |

### 1.2 框架选型

- **无新增第三方依赖**（§六）。全部为既有栈增量改造：Pydantic v2（schema）、Chroma（向量）、LangGraph（Agent 状态）、FastAPI SSE（链路）、Vue 3 + Element Plus + Pinia（前端）。
- **架构模式**：保持现有分层不变 —— `mcp_tools（工具层）→ core/rag_engine（RAG 引擎）→ api/agents（LangGraph 节点）→ api/main.py（SSE 出口）→ web（chatStore → RagPanel）`。M-3 只在这些层之间打通结构化数据通道，**不引入新模块、不重构既有调用链**。
- **对齐参考**：`search_feature_intro` 已返回结构化 chunk（doc_id/section/title/kind/content/score），`SourceRef` 字段命名以其为准（kind 不需要对外，title/section 复用）。

### 1.3 关键设计决策（主理人已拍板，架构照此执行）

| 决策 | 结论 |
|---|---|
| 原文取回 | **后端直接下发 `content_excerpt`**（≥200 字），不做按需端点（PRD P1-3 降为不做） |
| score 语义 | 统一 0-1 归一化；`citation_min_score=0.25` 与拒答阈值对齐；`citation_top_n=5` 可配置 |
| mock 剧本 | 补 2-3 条知识类 sources（油温/过载/停机检修），与正文「📄 引用来源」文本一致 |
| feature-intro | 纳入 `sources`（真实路径 + mock 路径均纳入） |
| 测试基线 | 以实跑为准（654 passed，v1.7.0 后） |
| 后端兼容 | `citations: string[]` 原字段保留，新增 `sources` 结构化字段；现有消费方（ReasoningChainPanel 等）零回归 |

---

## 二、文件列表（新增/修改）

### 后端（7 个）

| 文件 | 改动 |
|---|---|
| `api/schemas/__init__.py` | **改**：新增 `SourceRef` Pydantic 模型；`RetrievalResult` 增加 `sources: list[SourceRef] = []`；`KnowledgeAnswer` 增加 `sources: list[SourceRef] = []`（既有字段不动）；`ChatResponse` 增加可选 `knowledge_answer: KnowledgeAnswer | None = None`；`__all__` 导出 SourceRef |
| `api/config.py` | **改**：新增 `citation_min_score: float = 0.25`、`citation_top_n: int = 5`（env `CITATION_MIN_SCORE` / `CITATION_TOP_N`） |
| `core/vector_store.py` | **改**：`search()` Chroma 分支读取 distances 换算真实 score；`_index_chunks` 的 Chroma metadata 追加 `chunk_id`/`filename`/`chunk_index`/`total_chunks`；新增静态方法 `_distance_to_score(distance)`（可单测）；新增 `_enrich_search_result()` 用 `self._chunks`（按 chunk_id / doc_id+content 兜底）补齐 metadata（filename/chunk_index/total_chunks/section）；`_keyword_fallback` 返回 metadata 同步补齐 chunk_id/meta 字段 |
| `core/rag_engine.py` | **改**：`retrieve()` 从 `vec_results` 构建 `sources: list[SourceRef]`（snippet≤120 字 / content_excerpt≥200 字 / score / section / chunk 序号）；feature-intro 分支（`search_feature_intro` 命中）用 `fi_chunks` 构建 `sources`；`answer()` 按 `citation_min_score` 过滤 + `citation_top_n` 截断后写入 `KnowledgeAnswer.sources`（`citations` 保留 vector_chunks 副本）；新增内部 helper `_make_source_ref()` / `_strip_title_prefix()` / `_make_excerpt()` / `_sort_sources()` |
| `mcp_tools/tools/knowledge_tools.py` | **改**：`search_feature_intro` rerank 得分封顶 `min(1.0, score)`（保持 0-1 量纲）；`query_knowledge_base` 无需改（`answer.model_dump()` 自动携带 sources）；`search_knowledge_chunks` 可选（P2）补 metadata |
| `api/agents/agent_factory.py` | **改**：新增 `_extract_knowledge_answer_from_results()`（从工具结果字符串 JSON 反解 KnowledgeAnswer）、`async _build_mock_knowledge_answer()`（油温/过载/停机检修/兜底/feature-intro 硬编码 sources）、`_attach_knowledge_answer()`（统一注入 state.knowledge_answer）；在 knowledge_agent 节点各返回路径（mock / presentation / 降级 mock / 工具执行 / 真实回复）挂接注入 |
| `api/main.py` | **改**：`chat_stream` 的 done 事件追加 `knowledge_answer`（`result.get("knowledge_answer")` model_dump，非空才携带）；`chat` 阻塞路径回填 `ChatResponse.knowledge_answer`（可选，低成本补齐） |

### 前端（6 个，其中 2 个新增）

| 文件 | 改动 |
|---|---|
| `web/src/types/index.ts` | **改**：新增 `SourceRef` 接口；`KnowledgeAnswer` 增加 `sources?: SourceRef[]`；`SseEvent` 增加 `knowledge_answer?: KnowledgeAnswer | null`；`ChatResponse` 增加 `knowledge_answer?: KnowledgeAnswer | null` |
| `web/src/stores/chatStore.ts` | **改**：`sendMessage` 在 done 事件捕获 `event.knowledge_answer`，流式收尾后调用 `attachContext({ knowledgeAnswer })`（修复从未被调用的缺口）；`sendMessageBlocking` 同样在 push assistantMsg 后 attach |
| `web/src/composables/useKbSources.ts` | **新增**：来源聚合/筛选/折叠记忆纯逻辑 —— `groupSourcesByDoc()`、`filterSourcesByDoc()`、`useSourcesCollapse(key)`（localStorage）、`formatScore()`、`sourceLabel()`（filename/title/`(未知文档)` 降级） |
| `web/src/components/RagPanel.vue` | **改**：引用区升级为卡片式（P0-3）；默认折叠 + localStorage 记忆（P2-2）；≥2 文档时渲染筛选 chips（P1-1）；无来源/无 doc_id/无 score 降级文案（P1-2）；**移除 `.answer-text` 重复渲染**（见共享知识 K-8）；`sources` 为空时回退渲染旧 `citations` 纯文本（向后兼容） |
| `web/src/components/kb/CitationCard.vue` | **新增**：单条引用卡片 —— 文件名/标题 + 匹配度（有 score 时）+ section + snippet（≤120 字）+ doc_id 复制按钮 + 「点开查看原文」内联展开 `content_excerpt`（≥200 字） |
| `web/src/components/kb/DocFilterChips.vue` | **新增**：文档筛选 chips（「全部」+ 各文档名 + 命中数），纯展示组件，`v-model` 受控 |

### 测试 / 文档

| 文件 | 改动 |
|---|---|
| `tests/test_kb_citation_sources.py` | **新增**：M-3 集成测试（见 §5 验收标准） |
| `docs/kb-citation-architecture-2026-08-10.md` | **新增**：本文档 |
| `docs/kb-citation-class-diagram.mermaid` | **新增**：类图（随本文档交付） |
| `docs/kb-citation-sequence-diagram.mermaid` | **新增**：时序图（真实 + mock，随本文档交付） |

---

## 三、数据结构和接口

### 3.1 SourceRef JSON Schema（全量字段）

```jsonc
{
  "chunk_id": 42,                    // SQLite knowledge_chunks 自增 id；feature-intro 分片无独立 id → null
  "doc_id": "user-upload:main-transformer-ops-a1b2c3d4",  // 必填语义；空 → 前端 (未知文档)
  "filename": "主变运行规程.md",      // meta.filename 或 source 反解（user-upload/<原名>）
  "title": "主变运行规程",            // 文档标题
  "source": "user-upload/主变运行规程.md",  // 原始 source 字段
  "section": "4.2",                  // 可选：meta.section / md 章节；feature-intro 有，用户上传暂无 → null
  "score": 0.87,                     // 0-1 真实检索分数；null → 前端不显示匹配度
  "snippet": "变压器油温异常分级：……",  // ≤120 字摘要（去《标题》前缀后截断）
  "content_excerpt": "……",           // ≥200 字原文摘录（chunk 全文去前缀；不足取全文，不强行补）
  "chunk_index": 3,                  // meta.chunk_index；缺失 → null
  "total_chunks": 12                 // meta.total_chunks；缺失 → null
}
```

### 3.2 KnowledgeAnswer 扩展（向后兼容）

```jsonc
{
  // 既有字段（不动）
  "answer": "……",
  "citations": ["纯文本1", "纯文本2"],          // 保留
  "graph_paths": [["变压器", "包含", "油温监控"]], // 保留
  "confidence": 0.82,                            // 保留（启发式置信度，与 citation score 不同语义）
  "refuse": false, "refuse_reason": null,        // 保留
  // M-3 新增
  "sources": [ /* SourceRef[]，按 score 降序，已过滤 citation_min_score + 截断 top_n */ ]
}
```

### 3.3 接口签名变化（均向后兼容，签名不变、字段增补）

```python
# core/vector_store.py
def search(self, query: str, top_k: int = 3, exclude_tags: list[str] | None = None
           ) -> list[dict[str, Any]]:
    # 返回项新增 metadata 字段：chunk_id / doc_id / title / source / filename /
    #   chunk_index / total_chunks / section（尽量补齐；缺失为 None/空）
    # score 语义：Chroma = clamp(1 - distance, 0, 1)；keyword = 命中率 0-1；均 round(3)

# core/rag_engine.py
def retrieve(self, query: str, top_k: int = 3, thread_id: str = "default") -> RetrievalResult:
    # RetrievalResult 新增 sources: list[SourceRef]（vector_chunks/graph_* 保留）
def answer(self, query: str, top_k: int = 3, thread_id: str = "default") -> KnowledgeAnswer:
    # KnowledgeAnswer 新增 sources（过滤 + 截断 + 排序）；citations 保留全部 vector_chunks

# mcp_tools/tools/knowledge_tools.py
async def query_knowledge_base(query: str) -> dict[str, Any]:
    # 不变；answer.model_dump() 自动携带 sources
async def search_feature_intro(query: str, top_k: int = 5, tag: str | None = None) -> dict[str, Any]:
    # chunks[].score 封顶 1.0（0-1 量纲对齐）

# api/agents/agent_factory.py（节点内部）
async def _build_mock_knowledge_answer(last_msg: str) -> KnowledgeAnswer | None
def _extract_knowledge_answer_from_results(results: list[str]) -> KnowledgeAnswer | None
def _attach_knowledge_answer(agent_name: str, update: dict, *, results=None, last_user=None) -> dict
```

### 3.4 SSE done 事件 payload 变化（增量）

```jsonc
{
  "type": "done",
  "thread_id": "thread-…",
  "interrupt_required": false,
  "interrupt_node": null,
  "interrupt_msg": null,
  "is_demo_out_of_scope": false,
  // M-3 新增（仅 knowledge_agent 且 knowledge_answer 非空时携带；其他 Agent 不出现该键）
  "knowledge_answer": { "answer": "…", "citations": [], "sources": [ /* SourceRef[] */ ], "graph_paths": [], "confidence": 0.82, "refuse": false, "refuse_reason": null }
}
```

### 3.5 前端类型定义变化

```ts
export interface SourceRef {
  chunk_id?: number | null
  doc_id?: string
  filename?: string
  title?: string
  source?: string
  section?: string | null
  score?: number | null
  snippet?: string
  content_excerpt?: string
  chunk_index?: number | null
  total_chunks?: number | null
}
export interface KnowledgeAnswer {
  answer: string
  citations: string[]
  sources?: SourceRef[]            // M-3 新增，可选（旧后端兼容）
  graph_paths: string[][]
  confidence: number
  refuse: boolean
  refuse_reason?: string | null
}
export interface SseEvent { /* …既有… */ knowledge_answer?: KnowledgeAnswer | null }   // 新增
export interface ChatResponse { /* …既有… */ knowledge_answer?: KnowledgeAnswer | null } // 新增
```

---

## 四、程序调用流程（时序图）

见 `docs/kb-citation-sequence-diagram.mermaid`（真实 + mock 两条链路）。要点：

1. **真实路径**：`chatStore.sendMessage` → SSE `GET /chat/stream/{tid}` → LangGraph supervisor → knowledge_agent 节点 → LLM 解析 `TOOL_CALL: query_knowledge_base` → `_invoke_tool` → `RagEngine.answer()`（内部 `retrieve()` 构建 `sources`）→ 工具结果字符串含完整 KnowledgeAnswer → 节点 `_extract_knowledge_answer_from_results()` 反解并注入 `AgentState.knowledge_answer` → 图返回 state → `chat_stream` done 事件携带 `knowledge_answer` → chatStore `attachContext({knowledgeAnswer})` → RagPanel 渲染引用卡片，点开内联展开 `content_excerpt`。
2. **mock 路径**：同链路，knowledge_agent 走 `mock_mode` → `_get_mock_response` 返回剧本文本 + `_build_mock_knowledge_answer` 返回硬编码 sources → 节点注入 `knowledge_answer` → SSE done → **前端同一组件渲染，零 mock/真实分支**。

---

## 五、任务列表（有序、含依赖、按实现顺序）

> 规则约束：≤5 任务、每任务 ≥3 文件、T01 为基础设施/契约层。T02 与 T03 仅依赖 T01，可并行。

### T01（P0）后端数据契约层 + score 修正
- **文件**：`api/schemas/__init__.py`、`api/config.py`、`core/vector_store.py`、`core/rag_engine.py`、`mcp_tools/tools/knowledge_tools.py`
- **依赖**：无
- **内容**：
  1. `SourceRef` 模型 + `RetrievalResult.sources` + `KnowledgeAnswer.sources` + `ChatResponse.knowledge_answer`（`api/schemas/__init__.py`）
  2. `citation_min_score` / `citation_top_n` 配置（`api/config.py`）
  3. `VectorStore.search()` Chroma 分支 distances→score；`_distance_to_score()` 静态方法；`_index_chunks` metadata 补 chunk_id/filename/chunk_index/total_chunks；`_enrich_search_result()` 补齐 metadata；`_keyword_fallback` 同步补齐（`core/vector_store.py`）
  4. `RagEngine.retrieve()` 构建 sources（业务路径 + feature-intro 路径）；`answer()` 过滤 + 截断 + 排序写入 sources；helper `_make_source_ref/_strip_title_prefix/_make_excerpt/_sort_sources`（`core/rag_engine.py`）
  5. `search_feature_intro` score 封顶 1.0（`mcp_tools/tools/knowledge_tools.py`）
- **验收标准**：
  - `KnowledgeAnswer(**old_dict)` 构造不报错（旧字段兼容）
  - keyword fallback 检索结果 metadata 含 chunk_id/doc_id/title/source/filename/chunk_index/total_chunks
  - `_distance_to_score(0.13) == 0.87`（±round）；Chroma 路径不再恒 0.0
  - `retrieve()` 返回 `sources` 长度与 `vector_chunks` 一致（业务路径）；feature-intro 分支 `sources` 含 doc_id/section/title/score
  - `answer()` 的 `sources` 已按 score 降序、过滤 <0.25、截断 ≤5；`citations` 仍为全部 vector_chunks 副本
  - 既有 RAG 相关测试全绿（`test_kb_upload_rag.py` / `test_kg_m2_rag.py` 等）

### T02（P0）后端引用链路：Agent 赋值 + SSE done + mock sources + 集成测试
- **文件**：`api/agents/agent_factory.py`、`api/main.py`、`tests/test_kb_citation_sources.py`
- **依赖**：T01
- **内容**：
  1. `_extract_knowledge_answer_from_results()`：扫描工具结果字符串，JSON 反解含 `answer`+`sources` 的 dict → `KnowledgeAnswer`
  2. `async _build_mock_knowledge_answer()`：油温/过载/停机检修/兜底硬编码 sources（与正文「📄 引用来源」一致）；feature-intro mock 复用 `search_feature_intro` chunks
  3. `_attach_knowledge_answer()` 挂接 knowledge_agent 全部返回路径（mock / presentation 剧本内 / 降级 mock / 工具执行 / 真实回复），`update["knowledge_answer"] = ka`
  4. `api/main.py`：`chat_stream` done 事件追加 `knowledge_answer`；`chat` 阻塞路径回填
  5. `tests/test_kb_citation_sources.py`：mock sources 断言、`_extract_knowledge_answer_from_results` 反解断言、SSE done 携带 knowledge_answer 的端到端断言（TestClient + 假 graph_builder）
- **验收标准**：
  - mock 油温问题 → 节点 update 含 `knowledge_answer.sources`（2 条，与正文《变压器运行规程》第 4.2 节 /《电力设备故障诊断手册》一致）
  - mock 过载 / 停机检修 / 兜底 / 功能介绍 各返回匹配 sources；剧本外无 sources
  - `/chat/stream` done 事件在 knowledge_agent 轮次携带 `knowledge_answer.sources`；其他 Agent 轮次不出现该键
  - 演示模式（presentation）与真实无 Key 自动降级 mock 均带 sources
  - 新增测试全绿，既有 654 基线不回归

### T03（P0）前端数据链路：类型 + store attach + 来源聚合
- **文件**：`web/src/types/index.ts`、`web/src/stores/chatStore.ts`、`web/src/composables/useKbSources.ts`
- **依赖**：T01（契约对齐；可与 T02 并行）
- **内容**：
  1. `SourceRef` / `KnowledgeAnswer.sources` / `SseEvent.knowledge_answer` / `ChatResponse.knowledge_answer`（`types/index.ts`）
  2. `chatStore.sendMessage`：done 事件捕获 `pendingKnowledgeAnswer` → 流式收尾 `attachContext({ knowledgeAnswer })`；`sendMessageBlocking` push 后 attach（`chatStore.ts`）
  3. `useKbSources.ts`：`groupSourcesByDoc` / `filterSourcesByDoc` / `useSourcesCollapse(key)`（localStorage）/ `formatScore` / `sourceLabel`
- **验收标准**：
  - `vue-tsc` 类型检查通过；`KnowledgeAnswer.sources` 可选（旧数据不报错）
  - SSE done 携带 knowledge_answer 时，`lastAssistantMessage.knowledgeAnswer` 被赋值（可单测/手动验证）
  - `useKbSources.groupSourcesByDoc` 对 2 文档 3 条 sources 归组为 2 组；`filterSourcesByDoc` 按 doc_id 过滤正确
  - 折叠状态写入 localStorage 键 `gridmind.kbSourcesCollapsed`

### T04（P1）前端引用卡片 UI：多文档聚合 + 筛选 + 原文展开 + 降级
- **文件**：`web/src/components/RagPanel.vue`、`web/src/components/kb/CitationCard.vue`、`web/src/components/kb/DocFilterChips.vue`
- **依赖**：T03
- **内容**：
  1. `RagPanel.vue`：sources 卡片区（默认折叠、localStorage 记忆）；≥2 文档渲染 DocFilterChips；移除 `.answer-text` 重复渲染（K-8）；`sources` 空时回退旧 `citations` 纯文本；降级文案
  2. `CitationCard.vue`：文件名/标题 + 匹配度（score 非空）+ section + snippet（≤120 字）+ doc_id 复制 + 「点开查看原文」内联展开 content_excerpt（≥200 字）
  3. `DocFilterChips.vue`：全部/按文档 chips + 命中数
- **验收标准**：
  - 命中 ≥1 来源 → 卡片区出现；0 来源 / refuse → 不渲染卡片区（不破坏现有回答）
  - 每条卡片显示文件名、doc_id（可复制）、score（0-1 两位小数）、摘要（≤120 字）
  - 点击「点开查看原文」→ 卡片内联展开 content_excerpt（≥200 字）
  - ≥2 文档 → 显示筛选器；选中某文档只显示该文档引用；1 个文档 → 不显示筛选器
  - doc_id 空 → 「(未知文档)」；score 空 → 不显示匹配度标签；sources 空但有 citations → 回退纯文本列表
  - 引用区默认折叠；展开/折叠状态会话内保持（localStorage）
  - mock 与真实路径渲染一致（同一组件、无分支）

---

## 六、依赖包列表

**无新增第三方依赖。**

- 后端：Pydantic v2（已有）、Chroma（已有）、LangGraph（已有）、FastAPI（已有）、pytest（已有）
- 前端：Vue 3 + Element Plus + Pinia（已有）
- 理由：M-3 全部为既有栈内增量字段与链路打通，无新能力需求；复制按钮用原生 `navigator.clipboard`，不做 clipboard 库。

---

## 七、共享知识（跨文件约定）

**K-1 SourceRef 字段命名规范**：字段名对齐 `search_feature_intro` 既有返回（doc_id/section/title/score），新增字段（chunk_id/filename/source/snippet/content_excerpt/chunk_index/total_chunks）沿用 snake_case；后端 Pydantic 与前端 TS 字段名**完全一致**，不做 camelCase 转换。

**K-2 score 归一化公式与量纲**：
- Chroma（cosine space）：`score = round(clamp(1 - distance, 0, 1), 3)`，由 `VectorStore._distance_to_score` 统一实现；
- keyword fallback：`score = 命中 token 数 / token 总数`（0-1，既有），保留 round(3)；
- feature-intro rerank：`score = round(min(1.0, 原得分), 3)`（封顶 1.0）；
- 全链路 score ∈ [0,1]，`citation_min_score=0.25` 过滤；`confidence`（启发式 0-1）与 citation score **语义不同、互不换算**，两者都保留。

**K-3 citations 纯文本与 sources 结构化共存规则**：`citations: string[]` 是旧字段，继续承载 `vector_chunks` 副本，供旧前端/旧消费方；`sources: SourceRef[]` 是 M-3 新消费方（RagPanel）唯一数据源。二者**并行构建、互不替代**。前端渲染优先级：`sources.length > 0` → 卡片区；否则 `citations.length > 0` → 旧纯文本列表；都空 → 不渲染。

**K-4 mock sources 与正文一致性约定**：mock 知识剧本的 `sources[].title/section` 必须与正文「📄 引用来源」行一致（如油温 = 《变压器运行规程》第 4.2 节 + 《电力设备故障诊断手册》）；数据集中定义在 `_MOCK_KNOWLEDGE_SOURCES` 一处，禁止正文与 sources 各写一份。mock doc_id 为演示用途（`user-upload:mock-*`），不要求真实存在于 DB。

**K-5 前端降级文案约定**（集中定义，避免散落）：
- 无来源 / refuse → 整区不渲染；
- `doc_id`/`filename`/`title` 均空 → `(未知文档)`；
- `score == null` → 不显示匹配度标签（不显示 `匹配度: NaN%`）；
- `snippet` 空 → 卡片正文降级为 `（该片段暂无摘要）`；
- `content_excerpt` 空 → 「点开查看原文」不可用（按钮置灰 + `原文暂不可用`）。

**K-6 SSE done 增量字段向后兼容**：done 事件仅在 knowledge_agent 且 `knowledge_answer` 非空时新增该键；其他 Agent / 空值不出现该键。既有字段（thread_id/interrupt_*/is_demo_out_of_scope）不变；任何旧前端忽略未知键即可，不得因新增键改变既有事件结构。

**K-7 Chroma 多 chunk 覆盖限制（既有行为，本期不修）**：`_chroma_id` 对同 doc_id 多 chunk 返回同一 id（`doc::{doc_id}`），Chroma 路径可能每文档仅召回 1 条；keyword fallback（dev/无 Key 环境）按 SQLite 全量 `_chunks` 召回多条，多文档/多 chunk 聚合在测试与演示环境完整可用。M-3 不重排 Chroma id（避免 reindex 风险），如 QA 要求真实 Chroma 环境多 chunk 召回，另立任务（将 id 改为 `doc::{doc_id}::c{chunk_id}` + 一次性 reindex）。

**K-8 RagPanel 不重复渲染 answer**：MessageBubble 已渲染 `msg.content`（最终回答文本），RagPanel 内 `.answer-text` 为历史死代码（knowledgeAnswer 从未被赋值，从未渲染过）；M-3 移除该块，避免回答正文重复显示，**不破坏任何现有可见行为**。RagPanel 聚焦「来源引用卡片 + 图谱路径 + 拒答」区。

**K-9 content_excerpt 生成规则**：取 chunk 全文（去 `《标题》` 前缀），≥200 字自然满足（chunk ~500 字）；不足 200 字的短 chunk 取全文不强行补；feature-intro 短分片同样取全文。snippet 取去前缀后前 120 字（截断加 `…`）。

**K-10 测试基线**：以实跑为准（654 passed，v1.7.0 后）；`pytest_out.log` 中 441 为旧日志不作为基线。M-3 回归命令：`python -m pytest tests/test_kb_citation_sources.py tests/test_kb_upload_rag.py tests/test_kg_m2_rag.py -q` + 全量 `python -m pytest -q`。

---

## 八、待明确事项 / 已确认决策

**主理人已拍板（本架构照此执行，不再询问）**：
1. 原文取回 → 后端直接下发 `content_excerpt`（≥200 字），不做按需端点（PRD P1-3 不做）；
2. score → 统一 0-1 归一化，`citation_min_score=0.25` 与拒答阈值对齐，`top-N=5` 可配置；
3. mock → 补 2-3 条知识类 sources（油温/过载/停机检修），与正文一致；
4. feature-intro 通道纳入 sources（cost 低）；
5. 测试基线 → 以实跑为准（654 passed）。

**仍待主理人确认的边界事项（不影响排期，工程师可先按默认执行）**：
1. **C-1「停机检修」mock 可达性**：mock Supervisor 路由把「检修/停机」关键词导向 diagnosis_agent（高危路径），knowledge_agent 的「停机检修」mock 分支实际难以被路由命中。默认：仍实现该分支 + sources（防御性覆盖，与《变压器运行规程》第 6.2 节一致），不修改路由。
2. **C-2 Chroma 多 chunk 覆盖**（K-7）：是否需要在 M-3 一并修复（改 `_chroma_id` + reindex）？默认：**不做**，keyword fallback 环境已满足 AC-3 演示；如需真实 Chroma 多 chunk，建议单独立 P2 任务。
3. **C-3 `search_knowledge_chunks` 工具**是否补 metadata（目前只返回 content+score）？默认：P2 可选，不影响 P0 主链路（主链路走 `query_knowledge_base`）。
4. **C-4 阻塞 `/chat` 路径**：已按低成本补齐 `ChatResponse.knowledge_answer`（HITL 后续轮次基本为 diagnosis，属防御性补全）；如主理人要求阻塞路径零改动可回退（不影响 SSE 主链路）。

---

**架构设计完毕，待主理人审阅并转工程师排期。**
