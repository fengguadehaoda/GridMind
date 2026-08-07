# PRD · GridMind 功能介绍知识库化（Feature Intro KB）

> 文档版本：v1.1 · 2026-08-05（根据主理人补充上下文修订：纳入引导 wizard 第 3 步写死文案、对话功能介绍问答 grounding）
> 产品负责人：许清楚（GridMind 产品经理）
> 关联需求：新手教程的功能介绍对接「功能介绍文档」并加入知识库，功能介绍以该文档为准；对话中的功能介绍类问答亦以该文档为主
> 关联前端资产（写死文案来源）：
> - `web/src/types/theme.ts` → `ONBOARDING_SCENARIOS`（4 场景标题/描述/种子问题）
> - `web/src/components/onboarding/OnboardingTour.vue` → `TOUR_STEPS`（5 页面 tour 标题/说明）
> - `web/src/components/onboarding/Step2Dialogue.vue` → 从 `ONBOARDING_SCENARIOS` 读取快捷指令
> - `web/src/components/onboarding/Step3Monitor.vue` → 引导 wizard 第 3 步硬编码说明
> 关联后端资产：`core/rag_engine.py`（retrieve/answer）、`core/vector_store.py`（SQLite→Chroma 索引）、`mcp_tools/db/seed_data.py`（seed KNOWLEDGE_CHUNKS）、知识库表 `knowledge_chunks`（chunk_id, doc_id, title, content, source）

---

## 1. 产品目标

**一句话目标**：将当前写死在前端代码中的功能介绍文案抽离为一份独立、可维护的「功能介绍」文档，并接入 GridMind 知识库（Chroma），使新手引导（场景卡片 + 页面 tour + wizard 第 3 步）与对话中的功能介绍类问答都以知识库文档为准、可由运营/产品自助更新，同时在 API 不可用时回退到本地内置文案以保证可用性。

**可衡量的验收标准**：

1. 交付一份覆盖「产品概述 / 核心视图 / 引导场景 / 各页面 tour / 引导 wizard 第 3 步」的 Markdown 文档，其对现有 **4 个引导场景 + 5 个页面 tour + 引导 wizard 第 3 步（Step3Monitor.vue）** 写死文案的信息点覆盖率为 **100%**（见 §4 内容大纲）。
2. 知识库中存在可检索的「功能介绍」主题分片（至少按 **4 场景 + 5 页面 + wizard 第 3 步** 切片），并支持通过 API/工具查询返回结构化片段；分片元信息（doc_id / title / content / source 及场景·页面标签）对齐现有 `knowledge_chunks` 模型，可经既有 seed/同步流程入仓。
3. 前端新手引导（场景卡 `ONBOARDING_SCENARIOS` + tour `TOUR_STEPS` + wizard 第 3 步 `Step3Monitor.vue`）在 API 可用时从知识库读取、文案与文档一致；**API 不可用时回退命中率 100%**，页面可正常加载、不白屏。
4. 对话中的功能介绍类问答（如"请给我介绍一下 GridMind 的 5 个核心视图"）**优先从功能介绍文档检索作答并支持引用追溯**，答案与文档一致、可溯源到具体片段，而非依赖泛化生成或过时写死话术。
5. 运营/产品人员可在**不重启服务、不改动前端代码**的前提下更新文档并使其重新入仓生效（热更新），且更新对对话问答与引导展示同时生效。
6. 文档更新后，前端在可控时间内（建议 ≤ 1 次会话或支持手动刷新）拿到新文案，且不影响进行中用户的当前引导进度（无闪烁、无强制中断）。

---

## 2. 用户故事

- **作为新用户（首次打开 GridMind）**：我希望在引导中看到准确、与产品当前功能一致的功能介绍，以便快速理解 5 个核心视图与 4 个引导场景，不会因文案滞后而误导我点击或操作。
- **作为运营 / 产品人员（维护功能介绍）**：我希望把功能介绍集中维护在一份文档里并通过后台重新入仓，以便新功能上线或话术调整时及时更新引导文案，无需提前端开发排期。
- **作为前端负责人（保障可用性）**：我希望功能介绍在知识库不可达时仍能展示一套兜底文案，以便在检索服务抖动或离线环境下新手引导不中断、不报错。
- **作为新用户在智能对话中提问功能介绍**：我希望系统基于最新的「功能介绍」文档作答并给出引用，以便我的问题（如"介绍 5 个核心视图"）得到与产品现状一致、可溯源的回答，而不是过时的写死话术或泛化内容。

---

## 3. 需求池（P0 / P1 / P2）

### P0（Must have · 本期必须交付）

- **P0-1 创建完整功能介绍 Markdown 文档**：作为新手引导与对话问答的 single source of truth，目录结构见 §4，信息点 100% 覆盖现有写死文案（含 `ONBOARDING_SCENARIOS` 4 场景、`TOUR_STEPS` 5 页面 tour、`Step3Monitor.vue` wizard 第 3 步）。
- **P0-2 提供后端入仓能力（API / 工具）**：将文档切分后加入 Chroma 知识库，并打上「功能介绍」主题标签，且为每个分片附带「场景 / 页面 / wizard 步骤」元信息（如 `monitor-overview`、`chat`、`wizard-step3`）；分片字段对齐现有 `knowledge_chunks` 模型（chunk_id / doc_id / title / content / source），可经 `seed_data.py` 或同步流程入仓。
- **P0-3 前端改为从知识库 / API 读取**：场景卡（`ONBOARDING_SCENARIOS`）、页面 tour（`TOUR_STEPS`）、引导 wizard 第 3 步（`Step3Monitor.vue`）的数据源由代码内置改为服务端返回；**必须保留一份本地内置文案作为回退（fallback）**，API 失败时直接使用，保证页面可用。
- **P0-4 回退策略落地**：定义明确的降级逻辑（加载顺序、失败判定、超时阈值），并在文档/接口契约中说明回退文案的来源与版本。
- **P0-5 对话功能介绍问答以文档为主（RAG grounding）**：功能介绍文档入仓后，作为对话中功能介绍类问答的优先检索来源；用户提问（如 `monitor-overview` 场景的 `starterMessage`"请给我介绍一下 GridMind 的 5 个核心视图"）优先命中文档片段并支持引用追溯，答案与文档一致。

### P1（Should have · 强烈建议本期交付）

- **P1-1 按标签过滤返回片段**：支持按「场景 / 页面」标签过滤，返回指定功能介绍片段（如仅返回 `monitor` 页面 tour 步骤），降低前端拉取与渲染成本。
- **P1-2 文档热更新 / 重新入仓**：支持文档增删改后重新同步 Chroma（重新切分 + 去重 + 覆盖旧分片），不中断在线用户、不要求重启。

### P2（Nice to have · 后续迭代）

- **P2-1 建立功能实体图谱关系**：在知识库 / Neo4j 中建立「功能实体—功能」的关系（如「实时监控」关联「设备表」「健康评分」），支撑知识图谱浏览与跨功能引用（呼应 `knowledge-rag` 场景的「知识图谱浏览」卖点）。

---

## 4. UI / 文案设计稿

### 4.1 功能介绍文档建议目录结构

```
GridMind 功能介绍（INDEX）
├── 第 1 章 产品概述
│   ├── 1.1 什么是 GridMind（电力 AI 调度中枢）
│   ├── 1.2 五大核心视图一览（chat / monitor / grayscale / audit / system）
│   └── 1.3 三层推理 + HITL 审批闭环理念
├── 第 2 章 核心视图（5 个路由）
│   ├── 2.1 对话视图 chat
│   ├── 2.2 监控视图 monitor
│   ├── 2.3 灰度切换 grayscale
│   ├── 2.4 审计日志 audit
│   └── 2.5 系统总览 system
├── 第 3 章 引导场景（4 个场景）
│   ├── 3.1 实时监控全览 monitor-overview
│   ├── 3.2 故障诊断演练 fault-diagnosis
│   ├── 3.3 知识库检索 knowledge-rag
│   └── 3.4 灰度切换 grayscale-rollout
├── 第 4 章 各页面功能 tour（5 个页面）
│   ├── 4.1 chat tour（4 步）
│   ├── 4.2 monitor tour（5 步）
│   ├── 4.3 grayscale tour（4 步）
│   ├── 4.4 audit tour（3 步）
│   └── 4.5 system tour（3 步）
└── 第 5 章 引导流程（wizard 步骤）
    └── 5.1 第三步 · 切换到实时监控视图（Step3Monitor.vue）
```

> 每个章节建议带结构化 front-matter 元信息（建议字段）：`id`（与前端枚举一致）、`title`、`icon`、`tags`（如 `scenario:monitor-overview` / `tour:chat`）、`starterMessage`（仅场景卡需要）。这些元信息供 P0-2 入仓切片与 P1-1 过滤使用。

### 4.2 内容大纲（覆盖现有 4 场景 + 5 页面写死文案的信息点）

#### 第 3 章 · 4 个引导场景（对应 `ONBOARDING_SCENARIOS`）

| 场景 id | 标题 | 描述要点 | 种子问题（starterMessage） |
|---|---|---|---|
| `monitor-overview` | 实时监控全览 | 了解 5 个核心路由分别做什么 | 「请给我介绍一下 GridMind 的 5 个核心视图」 |
| `fault-diagnosis` | 故障诊断演练 | 体验三层推理 + HITL 审批闭环 | 「请诊断 #T1 主变压器的温度异常」 |
| `knowledge-rag` | 知识库检索 | 试试 Q&A + 引用追溯 + 知识图谱浏览 | 「解释一下《电力安全事故应急条例》中关于紧急停机的条款」 |
| `grayscale-rollout` | 灰度切换 | 把一个新模型分批上线，逐步放量 | 「我要把 v2 模型灰度切换到 50%」 |

> 文档需保留每个场景的 `icon`（Monitor / FirstAidKit / Reading / Switch）与种子问题，作为场景卡渲染与 Step 2 种子消息的数据来源。

#### 第 4 章 · 5 个页面 tour（对应 `TOUR_STEPS`）

**4.1 chat tour（4 步）**
- 对话流（chat-history）：完整对话历史——用户提问 → LLM 推理 → 工具返回 → 最终回答；每个气泡支持快捷审批（HITL）。
- 演示快捷指令（chat-demo-shortcuts）：4 个种子快捷方式（设备查询 / 异常检测 / 知识检索 / 高危操作），点击卡片即真实发送消息。
- 模型切换（chat-model-switcher）：支持在线切换 LLM 后端（v2.0 / 多模型热切换）。
- 输入区（chat-input）：回车发送 · Shift+Enter 换行 · 发送后启动 700ms 思考延迟 + 真实 SSE 流式回复。

**4.2 monitor tour（5 步）**
- 顶部统计（monitor-stats）：4 个 StatHexagon（设备总数 / 正常运行 / 预警 / 严重），颜色 + 形状 + 图标 + 文字码四重区分。
- 刷新控制（monitor-toolbar）：手动刷新 / 自动刷新开关（每 15 秒轮询）。
- 设备总览表（monitor-table）：按健康分升序（最差在前），点击「详情」打开抽屉看遥测趋势 + 巡检记录。
- 健康评分卡（monitor-health-card）：设备详情中，综合 LLM + 机理校验 + 规则护栏三层输出健康分与异常清单。
- 遥测趋势图（monitor-telemetry）：6h / 24h / 48h 时间窗切换；异常数据点用三角形标记（状态四重区分之一）。

**4.3 grayscale tour（4 步）**
- 灰度统计（grayscale-stats）：当前切流比例 / 状态机 / 错误率 / 累计回滚次数；语义不依赖颜色（图标 + 文字码）。
- 手动切流（grayscale-toggle）：需管理员 token（环境变量 ADMIN_TOKEN）；比例仅支持 0 / 10 / 50 / 100 四档。
- 监控窗口（grayscale-metrics）：样本数 / 错误率 / P95 / Neo4j 连续失败；任意指标超阈值自动触发回滚。
- 切换历史（grayscale-history）：最近 10 条切换记录；`auto_` 前缀代表系统自动回滚。

**4.4 audit tour（3 步）**
- 审计统计（audit-stats）：总记录 + 按决策类型（批准 / 拒绝 / 编辑）分布。
- 筛选栏（audit-filter）：按 thread_id 子串 / 决策类型过滤，回车 / change 即应用。
- 审计条目（audit-list）：每条含 thread_id / actor / tool / risk / reason / 编辑内容；所有 HITL 操作 3 年留存。

**4.5 system tour（3 步）**
- 灰度总览（system-grayscale）：聚合显示灰度状态机、Neo4j 路由占比、累计回滚、最近切换。
- LLM 模型（system-model）：当前模型 + 可选模型数 + 默认模型。
- Prometheus 指标（system-metrics）：Counter / Gauge / Histogram 三类视图；5 秒自动刷新，最新值带脉冲微动效。

> 文档中 tour 步骤的「锚点（data-tour）」与「标题 / 说明」需与 `TOUR_STEPS` 的 `element` / `popover.title` / `popover.description` 一一对应，保证从知识库读取后前端高亮位置不漂移。

#### 引导 wizard 第 3 步（对应 `Step3Monitor.vue`）

- 标题：第三步 · 切换到实时监控视图
- 引导说明：第 2 步触发的"异常检测 / 设备查询 / 知识检索"会在监控页实时反馈——设备列表、健康评分、遥测曲线；点按钮跳转。
- 三个要点卡片：
  - 设备实时列表：按健康分排序 · 严重设备置顶 · 颜色 + 图标 + 文字码四重区分。
  - 遥测趋势：打开任意设备抽屉 → 切换 6h / 24h / 48h 时间窗 → 查看温度 / 负载 / 电流曲线。
  - 异常清单：z-score 异常检测 · 自动标注严重程度 · 一键跳到 HITL 审批页。
- CTA：按钮"前往实时监控" → 跳转 `/monitor?tour=monitor`；提示"完成后点底部'完成，开始体验'统一结束引导"。

> 该步骤文案当前写死在 `Step3Monitor.vue`，需抽入文档第 3 章（或独立"引导流程"小节），并保留 `wizard-step3` 标签以便前端按标签读取（呼应 P1-1）。

#### 对话功能介绍问答 grounding（对应 `monitor-overview` starterMessage）

- 用户在智能对话中提问"请给我介绍一下 GridMind 的 5 个核心视图"，即 `monitor-overview` 场景的 `starterMessage`，应优先命中文档「第 2 章 核心视图」分片作答。
- 文档需保证「第 2 章 核心视图」与「第 1 章 产品概述」内容可被 RAG 检索（标题/分段清晰、关键词覆盖"5 个核心视图 / chat / monitor / grayscale / audit / system"），并支持答案引用追溯（呼应 `knowledge-rag` 场景的"引用追溯 + 知识图谱浏览"）。

---

## 5. 待确认问题（需主理人 / 用户确认）

1. **前端是否允许完全脱离写死文案？**
   现状是文案写死在 `theme.ts` / `OnboardingTour.vue`。PRD 默认「保留本地内置文案作为回退（P0-3）」。需主理人确认：回退文案是**始终内置**还是**构建期注入**（build-time 从文档生成），以及是否允许在 API 长期不可用时仅展示回退。

2. **是否在本期建设 Neo4j 功能实体图（P2-1）？**
   建立「功能实体—功能」图谱关系投入较大，且与 `kg_client.py` / `kg_chroma_sync.py` 现有流程耦合。需确认 P2 是否纳入本期范围，或明确为后续迭代。

3. **文档更新频率与触发方式？**
   热更新（P1-2）需要明确：手动触发重新入仓 / 监听文件变更自动同步 / 走 CI 流程？是否需审批与版本记录？源文件托管在 Git 还是知识库后台？

4. **（补充）文档版本与多语言？**
   当前仅中文。是否需要文档版本号字段、以及是否预留英文/无障碍（色盲模式）变体，以便未来复用同一套读取链路。

5. **对话功能介绍问答如何优先 grounding 到该文档？**
   现有 `rag_engine.answer()` 面向通用知识库检索。需确认：是否通过「功能介绍」主题标签 / doc_id 提升该功能类问答的检索优先级？是否强制引用展示？与一般电力知识问答的边界如何划分（避免功能介绍问题被通用知识淹没）？
