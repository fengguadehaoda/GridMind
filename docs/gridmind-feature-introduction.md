---
doc: gridmind-feature-introduction
title: GridMind 功能介绍
version: 1.0.0
lang: zh-CN
source: docs/gridmind-feature-introduction.md
updated: 2026-08-05
tags: [feature-intro]
---

# GridMind · 灵枢电网 功能介绍

> 本文档是 GridMind 新手引导（场景卡片 / 页面 tour / 引导 wizard）与对话中「功能介绍类问答」的
> **single source of truth**。所有前端写死文案均以本文档为准。
>
> 维护约定：
> 1. 每个 `##` 小节会被切分为知识库中的一个独立分片（chunk），`doc_id = feature-intro:<id>`。
> 2. 每个 `##` 小节标题之后紧跟一段 `---` 包裹的 YAML front-matter，声明 `id` / `title` / `icon` /
>    `tags` / `starterMessage` 等结构化元信息；正文用于 RAG 语义检索。
> 3. 修改本文档后执行 `python -m scripts.seed_feature_intro --reload`，
>    或调用 `POST /knowledge/feature-intro/reload`（需 `X-Admin-Token`）即可热更新入仓。
> 4. `tags` 前缀约定：
>    - `feature-intro` —— 全量标记，所有分片都带（前端一次拉全用）
>    - `chapter:N`     —— 章节号
>    - `view:<route>`  —— 第 2 章核心视图
>    - `scenario:<id>` —— 第 3 章引导场景（对应 `ONBOARDING_SCENARIOS`）
>    - `tour:<page>`   —— 第 4 章页面 tour（对应 `TOUR_STEPS`）
>    - `wizard:step3` / `wizard-step3` / `tour-flow` —— 第 5 章引导流程（对应 `Step3Monitor.vue`）

---

## 1.1 什么是 GridMind（电力 AI 调度中枢）

---
id: overview-what-is
title: 什么是 GridMind（电力 AI 调度中枢）
icon: Cpu
tags: [feature-intro, chapter:1, overview]
---

GridMind（灵枢电网）是一套面向电力调度场景的 **AI 调度中枢**。它把「设备实时监控」「故障诊断」
「安规知识检索」「模型灰度上线」「操作审计」五件事收敛到同一个多智能体系统里，用一条对话流驱动。

技术形态：前端 Vue 3 + Vite + Element Plus + Pinia；后端 FastAPI + LangGraph 多智能体编排 +
MCP 工具服务；知识层 Chroma 向量库 + Neo4j 知识图谱 + SQLite 业务库；模型层支持 DashScope /
DeepSeek 多模型热切换。

GridMind 解决的核心痛点：调度员面对的是「分散在多个系统里的设备数据 + 写在纸质规程里的安规知识 +
必须留痕的高危操作」。GridMind 把它们统一为「问一句话 → Agent 编排工具 → 三层推理给结论 →
高危动作走人工审批 → 全过程留档」的闭环。

## 1.2 五大核心视图一览（chat / monitor / grayscale / audit / system）

---
id: overview-five-views
title: 五大核心视图一览
icon: Grid
tags: [feature-intro, chapter:1, overview, views-index]
---

GridMind 一共有 5 个核心视图（路由），分别是：

1. **对话视图 chat**（`/chat`）—— 与多智能体系统对话的主入口，承载完整推理过程与 HITL 审批。
2. **监控视图 monitor**（`/monitor`）—— 设备实时列表、健康评分、遥测趋势、异常清单。
3. **灰度切换 grayscale**（`/grayscale`）—— 新模型/新链路分批放量、监控窗口与自动回滚。
4. **审计日志 audit**（`/audit`）—— 所有 HITL 决策的留痕与检索，3 年留存。
5. **系统总览 system**（`/system`）—— 灰度状态、LLM 模型信息与 Prometheus 指标聚合看板。

这 5 个视图对应 5 套单页 tour（见第 4 章），可通过 URL query `?tour=chat` /
`?tour=monitor` / `?tour=grayscale` / `?tour=audit` / `?tour=system` 随时重新触发。

## 1.3 三层推理 + HITL 审批闭环理念

---
id: overview-three-layer
title: 三层推理 + HITL 审批闭环
icon: Connection
tags: [feature-intro, chapter:1, overview, hitl]
---

GridMind 的诊断结论不是「LLM 说了算」，而是三层融合：

1. **LLM 推理层**：大模型基于设备遥测、巡检记录、历史工单给出初判与置信度。
2. **机理校验层**：过载 / 短路 / 潮流 / 电压 / 温度 5 类物理机理校验，用设备铭牌参数
   （额定电流、短路阻抗、额定电压）做定量复核。
3. **规则护栏层**：安规规则库做最终护栏，冲突时以规则为准，并强制标记「需人工复核」。

三层输出融合为最终严重度（info / warning / critical）、是否冲突、是否需要人工复核、
以及强制动作（none / dispatch / shutdown），并保留完整 4 步推理链供事后追溯。

**HITL（Human-in-the-loop）审批闭环**：当 Agent 准备调用高危工具（如停机、切负荷）时，
图会 interrupt 挂起，把工具名与参数推给前端；调度员可以「批准 / 拒绝 / 修改后批准」三选一。
修改后批准会重新走一遍安规重检，不通过则 fail-closed 拒绝执行。所有决策写入
`hitl_audit_log`，保留 3 年。

## 2.1 对话视图 chat

---
id: view-chat
title: 对话视图 chat
icon: ChatDotRound
route: /chat
tags: [feature-intro, chapter:2, view:chat]
---

**对话视图**是 GridMind 的主入口，路由 `/chat`。

能做什么：
- 用自然语言向多智能体系统提问，例如「查一下 TR-001 的最新遥测」「诊断 #T1 主变压器的温度异常」。
- 完整看到「用户提问 → LLM 推理 → 工具返回 → 最终回答」的全过程，而不是只看到一个结论。
- 每个消息气泡支持快捷审批（HITL）：高危工具调用会就地弹出批准 / 拒绝 / 修改后批准。
- 4 个演示快捷指令（设备查询 / 异常检测 / 知识检索 / 高危操作），点卡片即真实发送消息。
- 在线切换 LLM 后端（多模型热切换），无需重启服务。
- 输入区：回车发送、Shift+Enter 换行，发送后走真实 SSE 流式回复。

对应 tour：`?tour=chat`（4 步，见 4.1）。

## 2.2 监控视图 monitor

---
id: view-monitor
title: 监控视图 monitor
icon: Monitor
route: /monitor
tags: [feature-intro, chapter:2, view:monitor]
---

**监控视图**是设备实时态势中心，路由 `/monitor`。

能做什么：
- 顶部 4 个 StatHexagon 统计卡：设备总数 / 正常运行 / 预警 / 严重，采用颜色 + 形状 + 图标 +
  文字码四重区分（不依赖颜色即可识别状态，满足 WCAG 2.2 §1.4.1）。
- 手动刷新 / 自动刷新开关（每 15 秒轮询）。
- 设备总览表按健康分升序排列（最差在前），点击「详情」打开抽屉。
- 设备详情抽屉内含健康评分卡（LLM + 机理校验 + 规则护栏三层输出健康分与异常清单）
  与遥测趋势图（6h / 24h / 48h 时间窗切换，异常点用三角形标记）。
- 异常清单：z-score 异常检测，自动标注严重程度，可一键跳到 HITL 审批页。

对应 tour：`?tour=monitor`（5 步，见 4.2）。

## 2.3 灰度切换 grayscale

---
id: view-grayscale
title: 灰度切换 grayscale
icon: Switch
route: /grayscale
tags: [feature-intro, chapter:2, view:grayscale]
---

**灰度切换**用于把新模型 / 新链路分批上线、逐步放量，路由 `/grayscale`。

能做什么：
- 查看当前切流比例、灰度状态机、错误率、累计回滚次数。
- 手动切流：比例只支持 0 / 10 / 50 / 100 四档，需要管理员 token（环境变量 `ADMIN_TOKEN`，
  请求头 `X-Admin-Token`）。
- 监控窗口：5 分钟滚动窗口内的样本数、错误率、P95 延迟、Neo4j 连续失败次数；
  任意指标超阈值自动触发回滚（默认错误率 > 1%、P95 > 200ms、Neo4j 连续失败 ≥ 3 次）。
- 切换历史：最近 10 条切换记录，`auto_` 前缀代表系统自动回滚。

对应 tour：`?tour=grayscale`（4 步，见 4.3）。

## 2.4 审计日志 audit

---
id: view-audit
title: 审计日志 audit
icon: Document
route: /audit
tags: [feature-intro, chapter:2, view:audit]
---

**审计日志**沉淀所有 HITL 人工决策，路由 `/audit`。

能做什么：
- 审计统计：总记录数 + 按决策类型（批准 / 拒绝 / 编辑）的分布。
- 筛选栏：按 `thread_id` 子串过滤、按决策类型过滤，回车或 change 即应用。
- 审计条目：每条记录包含 `thread_id` / actor / tool / risk / reason / 编辑内容，
  所有 HITL 操作 **3 年留存**，落库表 `hitl_audit_log`。
- 支持按风险等级（low / normal / high / critical）过滤，便于复盘高危操作。

对应 tour：`?tour=audit`（3 步，见 4.4）。

## 2.5 系统总览 system

---
id: view-system
title: 系统总览 system
icon: DataAnalysis
route: /system
tags: [feature-intro, chapter:2, view:system]
---

**系统总览**是运行态聚合看板，路由 `/system`。

能做什么：
- 灰度总览：聚合显示灰度状态机、Neo4j 路由占比、累计回滚次数、最近一次切换。
- LLM 模型：当前使用的模型、可选模型数、默认模型。
- Prometheus 指标：Counter / Gauge / Histogram 三类视图，5 秒自动刷新，最新值带脉冲微动效。
- 可观测性链路还包括钉钉告警（默认关闭，配置 webhook 后开启，5 分钟冷却期）。

对应 tour：`?tour=system`（3 步，见 4.5）。

## 3.1 实时监控全览 monitor-overview

---
id: monitor-overview
title: 实时监控全览
icon: Monitor
description: 了解 5 个核心路由分别做什么。
starterMessage: 请给我介绍一下 GridMind 的 5 个核心视图
tags: [feature-intro, chapter:3, scenario:monitor-overview]
---

**适合谁**：第一次打开 GridMind、想先建立整体认知的调度员或评审人。

**这个场景带你做什么**：从对话开始，让 Agent 用一段话讲清 chat / monitor / grayscale /
audit / system 这 5 个核心路由分别负责什么、什么时候该去哪个页面，再跳到监控页看真实数据。

**种子问题**：请给我介绍一下 GridMind 的 5 个核心视图

**预期收获**：知道 5 个路由的职责边界，知道从「发现异常」到「诊断」到「审批」到「留痕」
这条主线各在哪个页面完成。

## 3.2 故障诊断演练 fault-diagnosis

---
id: fault-diagnosis
title: 故障诊断演练
icon: FirstAidKit
description: 体验三层推理 + HITL 审批闭环。
starterMessage: 请诊断 #T1 主变压器的温度异常
tags: [feature-intro, chapter:3, scenario:fault-diagnosis]
---

**适合谁**：想验证「AI 结论是否可信」的技术评审与一线调度员。

**这个场景带你做什么**：让 Agent 对一台温度异常的主变压器做完整诊断，观察 LLM 推理层、
机理校验层（过载 / 短路 / 潮流 / 电压 / 温度）、规则护栏层三层如何融合出最终严重度；
当 Agent 尝试调用高危工具时，亲手完成一次 HITL 审批（批准 / 拒绝 / 修改后批准）。

**种子问题**：请诊断 #T1 主变压器的温度异常

**预期收获**：看到完整 4 步推理链（LLM → 机理校验 → 规则护栏 → 融合），
理解「冲突检测」「需人工复核」「强制动作」这三个字段的含义，并完成一次可追溯的审批。

## 3.3 知识库检索 knowledge-rag

---
id: knowledge-rag
title: 知识库检索
icon: Reading
description: 试试 Q&A + 引用追溯 + 知识图谱浏览。
starterMessage: 解释一下《电力安全事故应急条例》中关于紧急停机的条款
tags: [feature-intro, chapter:3, scenario:knowledge-rag]
---

**适合谁**：需要把纸面规程变成可即时查询能力的安全管理与运维人员。

**这个场景带你做什么**：向知识库提问安规条款，观察混合检索（Chroma 向量召回 +
关键词兜底 + Neo4j 图谱扩展）如何给出答案，并逐条查看引用来源。

**种子问题**：解释一下《电力安全事故应急条例》中关于紧急停机的条款

**预期收获**：理解 GridMind 的答案不是凭空生成——每条结论都能追溯到具体知识分片
（doc_id + 标题 + 来源标准号），并可顺着知识图谱查看「设备类别—故障类型—处置措施」的关联。

## 3.4 灰度切换 grayscale-rollout

---
id: grayscale-rollout
title: 灰度切换
icon: Switch
description: 把一个新模型分批上线，逐步放量。
starterMessage: 我要把 v2 模型灰度切换到 50%
tags: [feature-intro, chapter:3, scenario:grayscale-rollout]
---

**适合谁**：负责模型上线节奏与稳定性兜底的平台工程师。

**这个场景带你做什么**：把一个新模型按 0% → 10% → 50% → 100% 四档分批放量，
观察 5 分钟滚动窗口的错误率 / P95 / Neo4j 连续失败次数，并体验超阈值自动回滚。

**种子问题**：我要把 v2 模型灰度切换到 50%

**预期收获**：掌握「小流量验证 → 观察窗口 → 放量或回滚」的标准动作，
知道切流需要管理员 token、知道 `auto_` 前缀的历史记录代表系统自动回滚。

## 4.1 chat tour（4 步）

---
id: tour-chat
title: chat 页面 tour
tour: chat
tags: [feature-intro, chapter:4, tour:chat]
steps:
  - element: '[data-tour="chat-history"]'
    popover:
      title: '对话流'
      description: '这里是完整对话历史：用户提问 → LLM 推理 → 工具返回 → 最终回答。每个气泡支持快捷审批（HITL）。'
      side: 'top'
      align: 'center'
  - element: '[data-tour="chat-demo-shortcuts"]'
    popover:
      title: '演示快捷指令'
      description: '4 个种子快捷方式：设备查询 / 异常检测 / 知识检索 / 高危操作。点任意卡片即真实发送消息。'
      side: 'top'
      align: 'center'
  - element: '[data-tour="chat-model-switcher"]'
    popover:
      title: '模型切换'
      description: '支持在线切换 LLM 后端（v2.0 / 多模型热切换）。'
      side: 'top'
      align: 'center'
  - element: '[data-tour="chat-input"]'
    popover:
      title: '输入区'
      description: '回车发送 · Shift+Enter 换行 · 发送后会启动 700ms 思考延迟 + 真实 SSE 流式回复。'
      side: 'top'
      align: 'center'
---

对话视图单页 tour 共 4 步，按顺序高亮以下锚点：

1. **对话流**（`chat-history`）：这里是完整对话历史：用户提问 → LLM 推理 → 工具返回 → 最终回答。每个气泡支持快捷审批（HITL）。
2. **演示快捷指令**（`chat-demo-shortcuts`）：4 个种子快捷方式：设备查询 / 异常检测 / 知识检索 / 高危操作。点任意卡片即真实发送消息。
3. **模型切换**（`chat-model-switcher`）：支持在线切换 LLM 后端（v2.0 / 多模型热切换）。
4. **输入区**（`chat-input`）：回车发送 · Shift+Enter 换行 · 发送后会启动 700ms 思考延迟 + 真实 SSE 流式回复。

## 4.2 monitor tour（5 步）

---
id: tour-monitor
title: monitor 页面 tour
tour: monitor
tags: [feature-intro, chapter:4, tour:monitor]
steps:
  - element: '[data-tour="monitor-stats"]'
    popover:
      title: '顶部统计'
      description: '4 个 StatHexagon：设备总数 / 正常运行 / 预警 / 严重。颜色 + 形状 + 图标 + 文字码四重区分。'
      side: 'bottom'
      align: 'center'
  - element: '[data-tour="monitor-toolbar"]'
    popover:
      title: '刷新控制'
      description: '手动刷新 / 自动刷新开关（每 15 秒轮询）。'
      side: 'bottom'
      align: 'start'
  - element: '[data-tour="monitor-table"]'
    popover:
      title: '设备总览表'
      description: '按健康分升序排列（最差在前）。点击“详情”打开抽屉查看遥测趋势 + 巡检记录。'
      side: 'top'
      align: 'center'
  - element: '[data-tour="monitor-health-card"]'
    popover:
      title: '健康评分卡'
      description: '打开任一设备详情后看到。综合 LLM + 机理校验 + 规则护栏三层输出健康分与异常清单。'
      side: 'top'
      align: 'center'
  - element: '[data-tour="monitor-telemetry"]'
    popover:
      title: '遥测趋势图'
      description: '点击 6h / 24h / 48h 切换时间窗。异常数据点用三角形标记（标准状态四重区分的一部分）。'
      side: 'top'
      align: 'center'
---

监控视图单页 tour 共 5 步，按顺序高亮以下锚点：

1. **顶部统计**（`monitor-stats`）：4 个 StatHexagon：设备总数 / 正常运行 / 预警 / 严重。颜色 + 形状 + 图标 + 文字码四重区分。
2. **刷新控制**（`monitor-toolbar`）：手动刷新 / 自动刷新开关（每 15 秒轮询）。
3. **设备总览表**（`monitor-table`）：按健康分升序排列（最差在前）。点击“详情”打开抽屉查看遥测趋势 + 巡检记录。
4. **健康评分卡**（`monitor-health-card`）：打开任一设备详情后看到。综合 LLM + 机理校验 + 规则护栏三层输出健康分与异常清单。
5. **遥测趋势图**（`monitor-telemetry`）：点击 6h / 24h / 48h 切换时间窗。异常数据点用三角形标记（标准状态四重区分的一部分）。

## 4.3 grayscale tour（4 步）

---
id: tour-grayscale
title: grayscale 页面 tour
tour: grayscale
tags: [feature-intro, chapter:4, tour:grayscale]
steps:
  - element: '[data-tour="grayscale-stats"]'
    popover:
      title: '灰度统计'
      description: '当前切流比例 / 状态机 / 错误率 / 累计回滚次数。语义不依赖颜色（图标 + 文字码）。'
      side: 'bottom'
      align: 'center'
  - element: '[data-tour="grayscale-toggle"]'
    popover:
      title: '手动切流'
      description: '需要管理员 token（环境变量 ADMIN_TOKEN）。比例只支持 0 / 10 / 50 / 100 四档。'
      side: 'bottom'
      align: 'center'
  - element: '[data-tour="grayscale-metrics"]'
    popover:
      title: '监控窗口'
      description: '样本数 / 错误率 / P95 / Neo4j 连续失败。任意指标超阈值自动触发回滚。'
      side: 'top'
      align: 'center'
  - element: '[data-tour="grayscale-history"]'
    popover:
      title: '切换历史'
      description: '最近 10 条切换记录。auto_ 前缀代表系统自动回滚。'
      side: 'top'
      align: 'center'
---

灰度视图单页 tour 共 4 步，按顺序高亮以下锚点：

1. **灰度统计**（`grayscale-stats`）：当前切流比例 / 状态机 / 错误率 / 累计回滚次数。语义不依赖颜色（图标 + 文字码）。
2. **手动切流**（`grayscale-toggle`）：需要管理员 token（环境变量 ADMIN_TOKEN）。比例只支持 0 / 10 / 50 / 100 四档。
3. **监控窗口**（`grayscale-metrics`）：样本数 / 错误率 / P95 / Neo4j 连续失败。任意指标超阈值自动触发回滚。
4. **切换历史**（`grayscale-history`）：最近 10 条切换记录。auto_ 前缀代表系统自动回滚。

## 4.4 audit tour（3 步）

---
id: tour-audit
title: audit 页面 tour
tour: audit
tags: [feature-intro, chapter:4, tour:audit]
steps:
  - element: '[data-tour="audit-stats"]'
    popover:
      title: '审计统计'
      description: '总记录 + 按决策类型（批准 / 拒绝 / 编辑）的分布。'
      side: 'bottom'
      align: 'center'
  - element: '[data-tour="audit-filter"]'
    popover:
      title: '筛选栏'
      description: '按 thread_id 子串过滤 / 按决策类型过滤，回车 / change 即应用。'
      side: 'bottom'
      align: 'center'
  - element: '[data-tour="audit-list"]'
    popover:
      title: '审计条目'
      description: '每条记录包含 thread_id / actor / tool / risk / reason / 编辑内容。所有 HITL 操作 3 年留存。'
      side: 'top'
      align: 'center'
---

审计视图单页 tour 共 3 步，按顺序高亮以下锚点：

1. **审计统计**（`audit-stats`）：总记录 + 按决策类型（批准 / 拒绝 / 编辑）的分布。
2. **筛选栏**（`audit-filter`）：按 thread_id 子串过滤 / 按决策类型过滤，回车 / change 即应用。
3. **审计条目**（`audit-list`）：每条记录包含 thread_id / actor / tool / risk / reason / 编辑内容。所有 HITL 操作 3 年留存。

## 4.5 system tour（3 步）

---
id: tour-system
title: system 页面 tour
tour: system
tags: [feature-intro, chapter:4, tour:system]
steps:
  - element: '[data-tour="system-grayscale"]'
    popover:
      title: '灰度总览'
      description: '聚合显示灰度状态机、Neo4j 路由占比、累计回滚、最近切换。'
      side: 'bottom'
      align: 'start'
  - element: '[data-tour="system-model"]'
    popover:
      title: 'LLM 模型'
      description: '当前模型 + 可选模型数 + 默认模型。'
      side: 'bottom'
      align: 'end'
  - element: '[data-tour="system-metrics"]'
    popover:
      title: 'Prometheus 指标'
      description: 'Counter / Gauge / Histogram 三类视图。5 秒自动刷新，最新值带脉冲微动效。'
      side: 'top'
      align: 'center'
---

系统总览单页 tour 共 3 步，按顺序高亮以下锚点：

1. **灰度总览**（`system-grayscale`）：聚合显示灰度状态机、Neo4j 路由占比、累计回滚、最近切换。
2. **LLM 模型**（`system-model`）：当前模型 + 可选模型数 + 默认模型。
3. **Prometheus 指标**（`system-metrics`）：Counter / Gauge / Histogram 三类视图。5 秒自动刷新，最新值带脉冲微动效。

## 5.1 引导第三步 · 切换到实时监控视图

---
id: wizard-step3
title: 第三步 · 切换到实时监控视图
icon: Monitor
tags: [feature-intro, chapter:5, wizard:step3, wizard-step3, tour-flow, step3]
description: '第 2 步触发的“异常检测 / 设备查询 / 知识检索”会在监控页实时反馈：设备列表、健康评分、遥测曲线。点下方按钮跳转。'
highlights: ['设备列表', '健康评分', '遥测曲线']
bullets:
  - icon: 'Monitor'
    title: '设备实时列表'
    description: '按健康分排序 · 严重设备置顶 · 颜色 + 图标 + 文字码四重区分。'
  - icon: 'DataAnalysis'
    title: '遥测趋势'
    description: '打开任意设备抽屉 → 切换 6h / 24h / 48h 时间窗 → 查看温度/负载/电流曲线。'
  - icon: 'WarningFilled'
    title: '异常清单'
    description: 'z-score 异常检测 · 自动标注严重程度 · 一键跳到 HITL 审批页。'
cta:
  label: '前往实时监控'
  path: '/monitor'
  tour: 'monitor'
  hint: '完成后点底部“完成，开始体验”统一结束引导。'
---

新手引导 wizard 第 3 步的完整文案。

**标题**：第三步 · 切换到实时监控视图

**引导说明**：第 2 步触发的“异常检测 / 设备查询 / 知识检索”会在监控页实时反馈：
**设备列表**、**健康评分**、**遥测曲线**。点下方按钮跳转。

**三个要点卡片**：

1. **设备实时列表**：按健康分排序 · 严重设备置顶 · 颜色 + 图标 + 文字码四重区分。
2. **遥测趋势**：打开任意设备抽屉 → 切换 6h / 24h / 48h 时间窗 → 查看温度/负载/电流曲线。
3. **异常清单**：z-score 异常检测 · 自动标注严重程度 · 一键跳到 HITL 审批页。

**CTA**：按钮「前往实时监控」跳转 `/monitor?tour=monitor`，进入监控页后自动开启 monitor 单页 tour；
提示文案「完成后点底部“完成，开始体验”统一结束引导。」
