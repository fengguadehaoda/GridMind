# GridMind（灵枢电网）M-5 增量 PRD · 对话体验（会话管理 + 导出 + 前端角色感知 UI）

**版本**：v1.7.0 第二批（M-5）　**作者**：许清楚（产品经理）　**日期**：2026-08-10
**上游**：路线图 `gridmind-next-step-roadmap-2026-08-08.md` §二 M-5（3-5 人天）；多用户 PRD `multiuser-prd-2026-08-10.md` §六 6.1；多用户架构 `multiuser-architecture-2026-08-10.md` §八 待明确事项 2/3
**基线**：M-4 后 pytest 717 passed / 18 skipped；RBAC 5 角色已在后端强制；threads 表 + owner 校验 + per-session 模型（M-1/M-2）已落地

---

## 一、项目信息

- **Language**：中文
- **Programming Language**：现有技术栈（FastAPI + Vue3 + Pinia + Element Plus），不引入新框架
- **Project Name**：gridmind_session_mgmt
- **原始需求复述**：在已有「会话归属表 + owner 校验 + per-session 模型」地基上，补齐对话体验三件事：① 会话可命名/归档/删除、侧栏只列本人会话、多会话并行切换；② 当前会话可导出 Markdown/JSON（含消息 + M-3 来源引用 + M-4 图谱，供复盘/交接）；③ 前端角色感知 UI（Header 用户+角色徽标、导航按角色过滤、KB 上传/删除按钮按角色显隐）。**后端强校验已存在，前端角色感知 UI 仅承担 UX 展示、不承担安全职责。**

---

## 二、产品定义

### 2.1 产品目标（一句话）

让调度员能在**本人会话**间命名、归档、删除与并行切换，一键导出**可溯源**（含来源引用与图谱）的复盘/交接文档，并让每个角色只看到自己该看的功能入口——全程不破坏现有单会话 /chat 流程。

### 2.2 用户故事（含验收标准）

#### US-1 会话管理：可命名/归档/删除，侧栏只列本人会话（多用户隔离）

- **场景**：调度员张三连续处理「#1 主变异常」「母线过载」两个任务，需要把不同任务存成不同会话、命名区分、不再用的归档、误建的删除。
- **验收标准**：
  - AC1-1：`GET /sessions` 只返回**当前用户**的会话列表（管理员/admin token 可跨用户视角），按 `updated_at` 倒序；不含他人会话。
  - AC1-2：会话可重命名（默认「新会话」），重命名成功后列表即时刷新；对他人会话重命名返回 403（生产模式）。
  - AC1-3：会话可归档——归档后从默认列表消失（进入归档视图）；可删除——删除后列表移除，再次访问 `GET /thread/{id}` 返回 404（或软删除语义见 §七 Q2）。
  - AC1-4：生产模式下越权操作（他人会话的归档/删除/重命名）一律 403，不泄漏「会话是否存在」。

#### US-2 对话导出：当前会话导出为 Markdown/JSON（复盘/交接用）

- **场景**：交接班时，调度员把「#1 主变异常处置」整个会话连同知识库引用、图谱推理路径导出，交给下一班/审计复盘。
- **验收标准**：
  - AC2-1：提供「导出 Markdown」「导出 JSON」两个入口（仅当前激活会话可导出）。
  - AC2-2：Markdown 含：会话标题/thread_id/导出时间/导出人/模型 + 按时间顺序的完整消息 + 每条 assistant 消息的来源引用（M-3 `sources`：文档名/章节/摘要/分数）+ 图谱（M-4 `graph_answer`：节点/边/路径清单）。
  - AC2-3：JSON 为结构化机读格式（schema 见 §四 4.2），含全部消息及 `knowledge_answer`/`sources`/`graph_answer` 原样保留，可被外部程序解析。
  - AC2-4：导出为**新增能力**，不修改任何现有 /chat /thread 端点行为；空会话导出给出明确提示不生成文件。

#### US-3 角色感知 UI：Header 徽标 + 导航过滤 + KB 按钮显隐

- **场景**：5 种角色登录后看到与自己权限匹配的界面——运维看到灰度面板、审计看到 HITL 审计、知识管理员看到上传按钮、调度员只看到对话/监控/知识检索。
- **验收标准**：
  - AC3-1：Header 显示「用户名 + 角色徽标」（如 `张三 · 调度员`）；数据源为 JWT `role` claim，解析失败/缺失默认「调度员」（与后端 `get_role` 默认一致）。
  - AC3-2：导航按角色过滤：**灰度面板 = 运维/管理员**；**HITL 审计 = 审计/运维/管理员**（列表按角色可见范围）；**系统总览 = 管理员**（沿用 PRD §6.1 矩阵，与后端 `require_role` 对齐）；智能对话/实时监控/帮助中心全员。
  - AC3-3：知识库页的「上传文档」「删除」按钮**仅 kb_admin/admin 显示**；列表/检索读入口全员保留。
  - AC3-4：模型切换入口全员保留（per-session 模型 M-2 不受角色限制）。
  - AC3-5：dev 模式（无 token / dev token 不可解析）前端按「调度员」默认可见性渲染，不与后端 dev 放行行为冲突。

#### US-4 新会话创建/切换：多会话并行切换不丢状态

- **场景**：调度员开了 3 个会话（#1 主变、母线过载、规程查询），来回切换继续对话，各自上下文与模型偏好互不串扰。
- **验收标准**：
  - AC4-1：侧栏「＋ 新建会话」= 本地新建（沿用现有懒登记语义：首次发送消息后才在后端 `threads` 表登记 thread_id）。
  - AC4-2：切换会话后：消息列表、HITL 审批态、推理链（reasoning store）、模型选择器全部切到目标会话；切回原会话上下文不丢失（内存态）。
  - AC4-3：切换会话时若当前会话正在流式输出/推理中，给出轻量确认或自动终止当前流（不静默丢失用户输入）。
  - AC4-4：per-session 模型偏好随会话走：切换会话后模型下拉显示该会话生效模型（复用 `modelStore.setActiveThread` + `GET /models?thread_id=`，已有能力）。

#### US-5 与现有能力协同：导出含 M-3 sources / M-4 graph_answer；per-session 模型随会话走

- **场景**：导出文档不是「纯聊天文本」，而是可直接用于复盘的可溯源报告。
- **验收标准**：
  - AC5-1：导出内容覆盖 M-3 来源引用（`KnowledgeAnswer.sources`）与 M-4 图谱问答（`KnowledgeAnswer.graph_answer`），缺省字段（非 knowledge_agent 轮次）自动跳过不报错。
  - AC5-2：会话列表项展示该会话生效模型（`threads.model_id ?? 全局`），与模型选择器一致。
  - AC5-3：回归：现有 `/chat`（阻塞 + SSE 流式）、HITL 三按钮、审计、灰度、KB 上传流程零改动跑通（M-4 后 pytest 基线不回归）。

---

## 三、技术规范

### 3.1 需求池

#### P0（Must have —— 会话管理闭环）

| # | 需求 | 说明 / 验收要点 |
|---|---|---|
| P0-1 | `GET /sessions`（本人会话列表） | 复用 `ThreadStore.list_by_owner`；响应含 thread_id/title/model_id/created_at/updated_at/archived；生产 owner 过滤（管理员可选全量）；dev 放行。多用户架构待明确事项 3 正式落地 |
| P0-2 | 会话重命名 | `PATCH /sessions/{thread_id}` 或 `POST /sessions/{id}/rename`，body `{title}`；`ensure_thread_owned` 越权校验；更新 `threads.title` + `updated_at` |
| P0-3 | 会话归档/删除 | 归档：`threads.archived=1`（软删除，见 Q1/Q2 决策）；删除：归档→可恢复 vs 物理删除（Q2）；两者都走 owner 校验 |
| P0-4 | 前端会话侧栏 | 会话列表（本人）+ 激活态高亮 + 新建 + 重命名/归档/删除操作 + 空态引导；放 ChatView 左侧（布局改造见 §五） |
| P0-5 | 新会话创建/切换状态管理 | chatStore 扩展：`sessions` 列表 + `activateThread(tid)` + `newSession()`；切换时同步 reasoning/model/HITL 态；沿用懒登记语义不破坏现有 /chat |

#### P1（Should have —— 导出 + 角色感知 UI）

| # | 需求 | 说明 / 验收要点 |
|---|---|---|
| P1-1 | 对话导出 Markdown | 前端组装下载（Blob + `URL.createObjectURL`），含消息 + sources + graph_answer（§四 4.2 格式）；数据源优先当前激活会话内存消息（含已 attach 的 knowledgeAnswer），历史会话导出见 Q3 |
| P1-2 | 对话导出 JSON | 结构化导出（schema §四 4.2），字段与后端 Pydantic snake_case 对齐 |
| P1-3 | Header 用户 + 角色徽标 | 新增轻量 `useAuth`/user store：base64 解码 JWT `role` claim（方案 A，见 Q4）；解析失败默认 dispatcher |
| P1-4 | 导航按角色过滤 | App.vue 5 个导航项 + `menuDrawerGroups.ts` 快捷区增加 `roles?: Role[]` 元数据，渲染时过滤 |
| P1-5 | KB 上传/删除按钮显隐 | KnowledgeUpload.vue 上传区 + 删除列按 `canManageKb = role ∈ {kb_admin, admin}` 显隐；读列表全员保留 |

#### P2（Nice to have）

| # | 需求 |
|---|---|
| P2-1 | 归档会话恢复（归档视图 + 一键恢复） |
| P2-2 | 会话搜索（按标题/消息内容过滤侧栏） |
| P2-3 | 批量操作（多选归档/删除） |
| P2-4 | 会话列表分页/虚拟滚动（会话量大时） |

### 3.2 与 RBAC 的关系（明确边界）

- **职责边界**：M-1 已把 RBAC + owner 校验在后端**强制落地**（`require_role`、`ensure_thread_owned`、`verify_thread_ownership_if_prod`）。本批**前端角色感知 UI 只是展示层 UX 增强**：
  - 前端隐藏按钮/导航 = 减少噪音 + 防误操作，**不构成安全边界**；
  - 即使前端显示、甚至绕过前端直接调 API，后端仍会 403/404（fail-closed）；
  - 验收不依赖「前端是否显示」，而依赖「后端是否拒绝」（现有 P1-2/P1-3 单测继续有效）。
- **角色清单**（沿用）：dispatcher / operator / kb_admin / auditor / admin。
- **dev 模式**：后端放行（身份 None → dispatcher），前端同样按 dispatcher 默认渲染，行为一致。

### 3.3 现状核实（M-5 开工前确认，供架构师参考）

- 后端 `api/services/thread_store.py`：`list_by_owner` 已就绪，返回 `[{thread_id, owner_id, title, model_id, created_at, updated_at}]`（updated_at DESC）；**无** rename/archive/delete 方法，threads 表**无** archived 列（需 ALTER TABLE 或迁移）。
- 后端 `api/main.py`：**无** `GET /sessions` 列表端点（架构待明确事项 3 明确留给 M-5）；`/chat` 已返回 `thread_id`；`/thread/{id}` 返回历史；`/models`、`/models/switch` 已支持 per-session；灰度/审计/KB 写端点均已 RBAC 收口。
- 前端 `web/src/stores/chatStore.ts`：单会话状态（messages/threadId/resetChat），threadId 初值 `thread_{Date.now()}`，SSE done 事件更新为真实 thread_id；**无**会话列表/切换/命名/归档。
- 前端 `web/src/components/ChatView.vue`：**无**会话侧栏；布局为消息列表 + 输入区（flex column）。
- 前端 `web/src/App.vue`：Header 5 个**硬编码**导航项（对话/监控/灰度/审计/系统）+ 菜单抽屉；**无**角色感知。
- 前端 `web/src/data/menuDrawerGroups.ts`：4 分组 + 快捷区（新对话/知识库管理/消息引导）；**无**角色过滤元数据。
- 前端 `web/src/components/controls/KnowledgeUpload.vue`：上传拖拽区 + 删除列**对所有用户显示**，无角色判断。
- 前端角色来源：`web/src/composables/useJwtAuth.ts` 仅提供 token + Authorization header，**不解析 JWT claim、无用户 store**。
- 导出能力：**全代码库无**（无 Blob/createObjectURL/下载逻辑），M-5 为全新能力。

---

## 四、数据结构草案

### 4.1 会话列表响应 schema（`GET /sessions`）

```json
{
  "sessions": [
    {
      "thread_id": "thread-7f3a...",
      "title": "#1 主变异常处置",
      "model_id": "qwen-plus",
      "archived": false,
      "created_at": "2026-08-10T09:12:00",
      "updated_at": "2026-08-10T10:30:00"
    }
  ],
  "total": 1
}
```

- 字段语义与 `ThreadStore.list_by_owner` 行对齐 + 新增 `archived`（默认 false）。
- `model_id` 允许 NULL（= 全局默认，前端回退显示当前全局模型）。

### 4.2 导出文件格式

**Markdown（.md）**：

```markdown
# 会话复盘：{title}
- 会话 ID：{thread_id}
- 导出时间：{exported_at}
- 导出人：{user_id}
- 模型：{model_id}

## 消息

### 用户（2026-08-10 10:00:00）
{content}

### 助手（2026-08-10 10:02:00）
{content}

#### 来源引用
- 《{filename}》·{section} — 匹配度 {score} — {snippet}

#### 图谱推理路径
- 节点：{node.name}({node.type})
- 边：{source} —[{relation_type}]→ {target}
- 路径：{node1} → {node2} → {node3}（置信度 {confidence}）
```

**JSON（.json）**：

```json
{
  "format_version": 1,
  "exported_at": "2026-08-10T10:35:00Z",
  "thread_id": "thread-7f3a...",
  "title": "#1 主变异常处置",
  "model_id": "qwen-plus",
  "messages": [
    {
      "role": "user",
      "content": "…",
      "timestamp": "…"
    },
    {
      "role": "assistant",
      "content": "…",
      "timestamp": "…",
      "knowledge_answer": {
        "answer": "…",
        "sources": [{ "doc_id": "…", "filename": "…", "section": "…", "score": 0.92, "snippet": "…" }],
        "graph_answer": {
          "nodes": [{"id": "…", "name": "…", "type": "…", "confidence": 1.0}],
          "edges": [{"source": "…", "target": "…", "relation_type": "CAUSES", "confidence": 0.85}],
          "paths": [{"nodes": ["…"], "relations": ["CAUSES"], "hops": 2, "confidence": 0.8}],
          "backend": "neo4j",
          "degraded": false
        }
      }
    }
  ]
}
```

- JSON 字段与前端 `ChatMessage`/`KnowledgeAnswer`/`GraphAnswer` 类型（`web/src/types/index.ts`）对齐，snake_case 与后端 Pydantic 一致。

---

## 五、UI 设计稿（ASCII）

会话侧栏 + 角色徽标 + 导出按钮位置（在现有 ChatView 布局上**新增左侧会话侧栏**，不改变消息区/输入区结构）：

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Header:  ⚡ GridMind 灵枢电网  [智能对话][实时监控][灰度*][审计*][系统*]   │
│          用户：张三（调度员）●         会话模型：qwen-plus ▾   [帮助][菜单] │
│          * 按角色过滤：灰度=运维/管理员，审计=审计/运维/管理员，           │
│            系统=管理员；不可见角色不渲染该导航项                           │
├───────────────┬──────────────────────────────────────────────────────────┤
│ 会话侧栏       │  ChatView（现有布局）                                     │
│ ┌───────────┐ │  ┌─ 推理控制栏（reasoning 激活时）──────────────────────┐ │
│ │ ＋ 新建会话 │ │  └────────────────────────────────────────────────────┘ │
│ ├───────────┤ │  ┌─ 消息列表 ──────────────────────────────────────────┐ │
│ │ 📁 会话 A  │ │  │  …消息气泡…                                        │ │
│ │    模型: q  │ │  └────────────────────────────────────────────────────┘ │
│ │ ⚙ 导出 ▾  │ │  ┌─ 输入区 ───────────────────────────────────────────┐ │
│ ├───────────┤ │  │ [输入电力运维相关的问题…]              [发送]       │ │
│ │ 会话 B     │ │  └────────────────────────────────────────────────────┘ │
│ ├───────────┤ │                                                          │
│ │ 会话 C     │ │  ⚙ 导出按钮：会话侧栏每项「⋯」菜单 or 侧栏顶部工具栏      │
│ ├───────────┤ │    └─ 导出 Markdown / 导出 JSON（仅当前激活会话可点）      │
│ │ 🗂 已归档   │ │                                                          │
│ └───────────┘ │                                                          │
└───────────────┴──────────────────────────────────────────────────────────┘

交互要点：
1. 侧栏项 = 会话标题（默认「新会话」，双击/⋯可重命名）；激活项高亮；切换 = activateThread。
2. 每项「⋯」菜单：重命名 / 归档 / 删除（删除需二次确认，对齐 KB 删除交互）。
3. 「🗂 已归档」折叠分组（P2-1 恢复入口）。
4. 导出按钮：侧栏顶部工具栏或激活会话操作区；导出前校验非空会话。
5. 角色徽标：Header 右侧用户区；颜色/文案按 5 角色区分（低优先级视觉，P1）。
6. 移动端（<768px）：侧栏收进抽屉（复用 NavDrawer 模式），不新增移动端布局。
```

---

## 六、与 RBAC 的关系（重申）

前端角色感知 UI（徽标/导航过滤/KB 按钮显隐）**仅提升可用性**，安全由后端 RBAC + owner 校验兜底：

- 展示层规则必须与后端权限矩阵**同源对齐**（灰度=operator/admin；审计读=auditor/operator/admin；KB 写=kb_admin/admin），前端只是镜像；
- 若角色元数据缺失/解析失败 → 默认 dispatcher 可见性（fail-closed 到最保守的展示层）；
- 验收时**不把「前端隐藏」当作安全凭证**，安全验收仍以「绕过前端直接调 API 被 403/404」为准（现有 M-1 测试）。

---

## 七、待确认问题

| # | 问题 | 影响 | 建议默认 |
|---|---|---|---|
| Q1 | 归档实现：`threads` 表加 `archived` 列（软删除）还是独立状态表？ | P0-3 数据结构 | **加 `archived INTEGER NOT NULL DEFAULT 0` 列 + 索引**（最小改动，list_by_owner 增加 `archived=0` 过滤） |
| Q2 | 删除语义：物理删除 checkpoint+threads 行，还是仅从列表隐藏（保留数据）？ | 数据保留/合规 | **软删除为主**：`archived=2`（deleted）或独立 `deleted_at`，保留 checkpoint 数据供审计追溯；物理删除能力列为 P2 |
| Q3 | 导出数据源：仅当前激活会话（前端内存消息，含已 attach 的 knowledgeAnswer）还是任意历史会话（需后端补充知识结构化字段）？ | P1-1/P1-2 范围 | **P1 只导出当前激活会话**（前端内存，保真度高、零后端改动）；历史会话导出（需 `/thread/{id}` 扩展携带 knowledge_answer 或新导出端点）列入 P2/后续批次 |
| Q4 | 前端角色来源：A) 前端 base64 解析 JWT `role` claim（零后端改动）；B) 新增 `GET /auth/me` 返回权威身份？ | P1-3 | **A（最小改动）**：dev token 不可解析 → 默认 dispatcher；生产 role claim 由 token 签发方控制（多用户 PRD Q3 已定 JWT role claim 方案） |
| Q5 | 会话列表是否需要 `message_count`/预览？`list_by_owner` 不含消息数 | P0-1 响应 | **P0 不加**（避免每次列表查 checkpoint 成本）；P2-4 再评估 |
| Q6 | 「新会话」按钮语义：本地 resetChat（懒登记）即可，还是要立即创建后端会话？ | P0-5 | **沿用懒登记**（与现有 /chat 行为一致，不产生空会话垃圾行） |
| Q7 | 切换会话时正在流式输出的处理：强制中断、提示确认、还是允许后台继续？ | P0-5 | 轻量确认（「当前会话正在生成，切换将中断，确定？」）；中断复用现有 AbortController |

---

## 八、不做 / 边界

- **不做 M-6**（可观测性补强）、**不做 F-1 大屏 UI**（App.vue `isBigScreen` 仅保留扩展点）。
- 不新建用户表/登录流（沿用 JWT role claim，多用户 PRD Q3 决策）。
- 不改变 KB 数据隔离策略（全局共享 + 角色写权限）。
- 向后兼容：现有 `/chat`（阻塞/SSE）、HITL、审计、灰度、KB 流程零破坏；导出为纯新增能力。
- 安全边界：本批所有会话管理写端点必须复用 `ensure_thread_owned` / `require_role`，不得新增无鉴权路径。

---

**验收口径汇总**：P0（US-1 + US-4 闭环 + GET /sessions）→ P1（US-2 导出 + US-3 角色感知 UI）→ P2（归档恢复/搜索/批量）。回归全量 pytest（M-4 基线 717 passed 不回归）+ 生产模式越权冒烟（他人会话 403 + 非授权角色 KB 写 403）。

**分析完毕，待主理人审阅。**
