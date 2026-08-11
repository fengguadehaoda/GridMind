# GridMind（灵枢电网）M-3 · 知识库来源引用链 + 多文档对话 —— 增量 PRD

**产品经理**：许清楚（Alice）　**日期**：2026-08-10　**基线**：v1.7.0 第一批（多用户 + RBAC + per-session 模型 + 大屏接口，654 pytest passed）
**范围**：仅本批变更（M-3）；**明确不做** M-4（图谱问答 UI）、M-5（Agent 会话记忆）
**落盘**：`docs/kb-citation-prd-2026-08-10.md`

---

## 〇、现状核实结论（先于设计的事实）

| # | 核查项 | 结论 |
|---|---|---|
| 1 | `core/rag_engine.py` 返回结构 | `retrieve()` 返回 `RetrievalResult(vector_chunks: list[str], graph_entities, graph_paths, confidence)`——**vector_chunks 为纯文本 list，无 chunk_id / doc_id / source / score 元数据**；`answer()` 的 `citations` 也仅是纯文本 list。来源信息**在下游被丢弃** |
| 2 | `api/agents/agent_factory.py` | knowledge_agent 工具含 `query_knowledge_base` / `search_knowledge_chunks` 等；真实路径工具结果经 `_invoke_tool → json.dumps` 转为字符串后由 `_synthesize_via_llm` 拼装——**结构化来源信息只在工具结果字符串里存在，未结构化回传前端** |
| 3 | `web/src/components/RagPanel.vue` | **已有引用展示雏形**：`answer.citations`（纯文本）渲染在「📄 引用来源」collapse 中，另有图谱路径。但无文档名 / 分数 / 可点开原文 |
| 4 | `web/src/types/index.ts` | `KnowledgeAnswer { answer, citations: string[], graph_paths, confidence, refuse, refuse_reason }`——citations 为 `string[]` |
| 5 | `core/kb_upload.py` | doc_id = `user-upload:{slug}-{sha1前8位}`；chunk 存 SQLite `knowledge_chunks`（doc_id/title/content/source/meta），meta 含 filename/chunk_index/total_chunks 等；`list_docs()` 已能按 doc_id 分组返回文档级信息（含 chunk_count）——**原文可按 doc_id 取回，但当前 API 无「按 doc_id 取 chunk 原文」端点** |
| 6 | 现成「引用/来源/出处」字段 | 仅 `mcp_tools/tools/knowledge_tools.py` 的 `search_feature_intro` 返回结构化 chunk（doc_id/section/title/kind/content/score）——**是最接近的可复用模式**；`VectorStore.search` 的 keyword fallback 返回含 doc_id/title/source 的 metadata，但 Chroma 路径 score 恒为 0.0（需修正） |
| 7 | **前端数据链路（关键缺口）** | `chatStore.attachContext` 已定义但**从未被调用**；SSE `done` 事件不携带 knowledgeAnswer；`AgentState.knowledge_answer` 字段存在但**从未赋值**。→ **RagPanel 目前实际收不到真实数据**，M-3 必须打通「后端结构化来源 → SSE → chatStore → RagPanel」整条链路 |
| 8 | RBAC | KB 读 = `verify_jwt_if_prod`（全员可读）；写 = `require_role(KB_ADMIN, ADMIN)`。引用展示仅读 → **RBAC 无影响**（见 §6） |

---

## 一、产品目标（一句话）

**让调度员在 AI 回答下方看到「可点开、可溯源、可筛选」的来源引用卡片，把 RAG 回答从黑盒变为可验证。**

---

## 二、用户故事（含验收标准）

### US-1 回答下方展示来源引用列表，可点开查看原文

> As a 调度员, I want AI 回答下方展示「来源引用」列表（文档名 + 章节/chunk 摘要），可点开查看原文片段，so that 我能快速验证 AI 依据是否可靠。

**验收标准（AC-1）**
- 当本轮为 knowledge_agent 且检索到 ≥1 个来源时，回答气泡下方出现「来源引用」卡片区（在现有 RagPanel 区域增强，非新开页面）
- 每条引用展示：文档名（filename/title）+ 章节或 chunk 摘要（≤120 字）
- 点击引用卡片可展开/弹窗查看**原文片段**（≥200 字上下文，前后各补足约 100 字）
- 无来源时（refuse 或 0 来源）不渲染卡片区，不破坏现有回答展示

### US-2 引用可溯源：doc_id / 文件名 / 匹配度 / 原文摘录

> As a 调度员, I want 每条引用展示 doc_id、文件名、匹配度（score）、原文摘录，so that 我能做技术溯源与留档。

**验收标准（AC-2）**
- 每条引用卡片展示：文件名、doc_id（可复制）、score（0-1，保留 2 位小数）、原文摘录（≤200 字）
- score 为真实检索分数（修正 Chroma 路径恒 0 的问题），而非置信度启发式
- 结构化来源数据从后端 `KnowledgeAnswer.sources` 字段下发（新字段，向后兼容，见 §4）
- doc_id 缺失/为空时展示 `(未知文档)` 降级文案，不报错

### US-3 多文档对话：按相关度聚合 + 文档级筛选

> As a 调度员, I want 问题命中多个文档时按相关度聚合展示，并可筛选「只看某文档」，so that 跨规程检索时不迷失在来源堆里。

**验收标准（AC-3）**
- 一次回答命中 N 个文档时，来源卡片按 score 降序聚合展示，同文档多条 chunk 归组（文档名 + 命中数）
- 卡片区提供「全部 / 按文档」筛选器（chips 或下拉），选中某文档后只显示该文档的引用
- 前端筛选为纯前端行为（不重新请求后端），后端只需保证 `sources` 按 score 排序返回
- 命中 1 个文档时不显示筛选器（避免无意义 UI）

### US-4 演示/降级模式也要有来源展示

> As a 产品演示人员, I want 演示模式（presentation）与 mock 降级路径下也能看到来源，so that 无真实 Key 时演示效果不打折。

**验收标准（AC-4）**
- 演示模式 mock 剧本（如「变压器油温」）**补充结构化 sources**（与正文中「📄 引用来源」文本一致，如《变压器运行规程》第 4.2 节），前端渲染同真实路径
- mock 剧本外的演示问题（`is_demo_out_of_scope`）保持现状（无来源卡片），不额外渲染
- 真实路径无 Key 自动降级 mock 时同样带结构化来源
- 后端 mock 分支的 `KnowledgeAnswer.sources` 字段存在即可，前端不做 mock/真实分支区分

---

## 三、需求池

### P0（必须做）
- **P0-1 后端来源数据贯通**：`RagEngine.retrieve()` 返回值携带每 chunk 的结构化来源（chunk_id / doc_id / filename / source / score / section / snippet）；`KnowledgeAnswer` 新增 `sources` 字段（保留 `citations: string[]` 原字段不动）
- **P0-2 修正真实 score**：Chroma 路径 `VectorStore.search` 的 score 由硬编码 `0.0` 改为真实距离换算；keyword fallback 已有 score 保留
- **P0-3 前端引用卡片可点开原文**：RagPanel 引用区升级为卡片式（文档名 + 摘要 + score），点击展开/弹窗看原文片段
- **P0-4 打通 SSE 数据链路**：`/chat/stream` 的 `done` 事件携带 `knowledge_answer`（含 sources）；`AgentState.knowledge_answer` 在 knowledge_agent 节点赋值；chatStore 接收后 attach 到消息（修复现 `attachContext` 从未被调用的缺口）

### P1（应该做）
- **P1-1 多文档聚合 + 文档级筛选**：卡片按文档归组 + 前端筛选器（纯前端）
- **P1-2 来源缺失降级文案**：无来源 / doc_id 缺失 / score 异常时的统一降级展示（含「(未知文档)」「未提供匹配度」等）
- **P1-3 原文取回端点**（如需点开看完整原文）：新增 `GET /api/knowledge/uploads/{doc_id}/chunks`（读操作，`verify_jwt_if_prod`）返回该文档全部分片原文；或 P0-3 若直接下发 `content_excerpt` 足够则降为 P2

### P2（最好做）
- **P2-1 引用排序/相似度阈值设置**：后端可配置 `citation_min_score`（默认 0.25，与拒答阈值对齐）与 top-N 上限（默认 5）
- **P2-2 引用数折叠/展开记忆**：引用卡片区默认折叠，用户展开后会话内保持（localStorage）

---

## 四、数据结构草案（JSON Schema）

```jsonc
// KnowledgeAnswer 增量字段（新增，不动既有字段，向后兼容）
{
  // 既有字段（保持不变）
  "answer": "……",
  "citations": ["纯文本1", "纯文本2"],          // 兼容旧前端
  "graph_paths": [["变压器", "包含", "油温监控"]],
  "confidence": 0.82,
  "refuse": false,
  "refuse_reason": null,

  // M-3 新增
  "sources": [                                   // 结构化来源（按 score 降序）
    {
      "chunk_id": 42,                            // SQLite knowledge_chunks 自增 id
      "doc_id": "user-upload:main-transformer-ops-a1b2c3d4",
      "filename": "主变运行规程.md",              // 原始文件名（meta.filename 或 source 反解）
      "title": "主变运行规程",                    // 文档标题
      "source": "user-upload/主变运行规程.md",    // source 字段
      "section": "4.2",                          // 可选：meta.section / md 章节
      "score": 0.87,                             // 真实匹配度 0-1
      "snippet": "变压器油温异常分级：……（≤120字）",  // 摘要
      "content_excerpt": "……（≥200字原文摘录，前后补足）", // 点开看原文用
      "chunk_index": 3,                          // 该 chunk 在文档内序号
      "total_chunks": 12                         // 该文档总 chunk 数
    }
  ]
}
```

**实现注记（给架构师）**：
- `RetrievalResult` 增加 `sources: list[SourceRef]`（新增字段，`vector_chunks` 保留）；`answer()` 组装时并行构建 `sources` 与旧 `citations`
- 数据来源：`VectorStore.search()` 已返回 `metadata`（doc_id/title/source）；`kb_upload._build_chunks` 的 meta 含 filename/chunk_index/total_chunks——只需在 RAG 层**透传 + 补齐**，不做新存储
- `search_feature_intro` 的返回结构（doc_id/section/title/kind/content/score）作为字段命名对齐参考
- mock 分支：knowledge_agent mock 剧本（油温/过载/兜底）返回带 `sources` 的 `KnowledgeAnswer`（硬编码来源，与正文文本一致）

---

## 五、UI 设计稿（ASCII：回答气泡下方的引用卡片区）

```
┌──────────────────────────────────────────────────────────────┐
│  🤖 知识库 Agent                                    [置信度 82%] │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ 根据《主变运行规程》第 4.2 节，变压器油温异常应……      │  │  ← 回答正文
│  └────────────────────────────────────────────────────────┘  │
│                                                                │
│  📄 来源引用（3 条 · 2 个文档）         [全部 ▾] [主变运行规程] [诊断手册] │  ← 文档筛选 chips（≥2 文档时显示）
│  ┌────────────────────────────────────────────────────────┐  │
│  │ [1] 主变运行规程.md          匹配度 87%                │  │
│  │     4.2 变压器油温异常分级：……                        │  │  ← 摘要 ≤120 字
│  │     ▶ 点开查看原文          doc_id: user-upload:…⧉     │  │
│  ├────────────────────────────────────────────────────────┤  │
│  │ [2] 主变运行规程.md          匹配度 72%                │  │
│  │     6.1 过载运行限制：……                              │  │
│  │     ▶ 点开查看原文          doc_id: user-upload:…⧉     │  │
│  ├────────────────────────────────────────────────────────┤  │
│  │ [3] 电力设备故障诊断手册.md   匹配度 65%               │  │
│  │     变压器油温异常原因分析：……                        │  │
│  │     ▶ 点开查看原文          doc_id: user-upload:…⧉     │  │
│  └────────────────────────────────────────────────────────┘  │
│  （点击「点开查看原文」→ 卡片展开 或 右侧抽屉，显示 content_excerpt ≥200 字）│
└──────────────────────────────────────────────────────────────┘

交互要点：
- 引用区默认**折叠为一行摘要**（「📄 来源引用（3 条）」），点击展开卡片列表——减少视觉噪音
- 点开原文：展开卡片内联展示 `content_excerpt`（优先）；P1-3 端点就绪后可「查看完整文档」
- 筛选 chips：选中某文档后高亮，列表仅显示该文档引用；「全部」恢复
- 降级：`sources` 为空 → 整区不渲染；doc_id 空 → 「(未知文档)」；score 缺失 → 不显示匹配度标签
```

---

## 六、与 RBAC 的关系

**结论：无影响。**

- 引用链只读 KB 数据（doc_id / filename / chunk 原文），而 KB 读操作当前为 `verify_jwt_if_prod`（**全员可读**，D3 决策「全局共享」），引用展示不新增任何读权限要求
- KB 写（上传/删除）仍为 `kb_admin/admin`，M-3 **不触碰写路径**
- 前端不感知角色差异：任何登录用户看到相同的引用卡片
- 唯一注意点：若未来实施「按文档可见性」策略（超出本批范围），引用卡片需随 KB 读权限联动——**在架构里预留 `sources` 过滤钩子即可，本期不实现**

---

## 七、待确认问题

1. **原文取回方式**：点开看原文用「后端直接下发 `content_excerpt`（≥200 字）」还是「新增 `GET /api/knowledge/uploads/{doc_id}/chunks` 端点按需取回」？前者实现快、体验顺（推荐 P0 用前者）；后者省流量、可看完整文档（P1）。
2. **score 语义**：Chroma 距离换算的 score 与 keyword fallback 的 score 量纲不一致——是否统一归一化到 0-1？默认 `citation_min_score` 阈值是否与拒答阈值（0.25）对齐？
3. **mock 剧本范围**：为「油温/过载」等 knowledge 剧本补结构化 sources 即可，还是需要覆盖演示快捷键全部知识类条目（「变压器过载如何处置」等）？建议至少覆盖演示快捷入口 2-3 条。
4. **测试基线口径**：主理人给定「654 pytest passed」，本机 `pytest_out.log` 为 441 passed（可能为旧日志）——CI 前请确认以哪个为准，M-3 回归按最终基线执行。
5. **feature-intro 通道**：功能介绍类问题（走 `search_feature_intro`）当前已返回结构化 doc_id/title/section/score——是否也纳入 `sources` 展示？（建议纳入，成本低且演示价值高）

---

**分析完毕，待主理人审阅。** 本 PRD 已核实代码现状，P0 聚焦「打通后端→SSE→前端整条引用链路」，全部为增量字段、向后兼容，工程师可直接排期。
