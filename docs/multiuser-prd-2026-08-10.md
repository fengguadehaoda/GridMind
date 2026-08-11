# GridMind（灵枢电网）v1.7.0 第一批迭代 · 增量 PRD

**主题**：多用户地基 + 大屏接口
**作者**：许清楚（产品经理）　**日期**：2026-08-10　**基线**：git `4752ab3`（P0 4/4 + P1 6/6 全落地）
**类型**：增量 PRD —— 仅描述本批变更，不重写全量 PRD
**语言**：中文　**技术栈**：Python FastAPI（后端）+ Vite + Vue3 + Pinia（前端，维持现状）

---

## 〇、背景与边界

### 原始需求复述
GridMind 将升级为**多用户部署**的内网系统（≥2 人并发、各自会话）。本批打地基：① 会话数据按用户隔离（M-1）；② 模型选择按会话隔离（M-2）；③ 知识库全局共享 + 角色写权限；④ 大屏模式仅预留扩展点/接口占位（F-1 降级）。

### 主理人已拍板的 3 个决策（必须遵守）
| # | 决策 | 结论 |
|---|---|---|
| D1 | 是否多用户部署 | **做**：M-1（多用户数据隔离）+ M-2（per-session 模型隔离）都做 |
| D2 | 大屏模式 | **立项但只留接口**：F-1 仅设计扩展点/接口占位（路由占位、布局模式枚举、样式 hook），不实现完整大屏 UI |
| D3 | KB 数据隔离 | **全局共享 + 角色写权限**（推荐方案），不做按用户隔离 |

### 本批不做（边界声明）
- M-3（知识库引用链）、M-4（图谱问答 UI）、M-5（上下文管理/导出）、M-6（可观测性补强）→ **后续批次**
- 完整大屏 UI（F-1 仅接口占位）
- KB 按用户隔离（D3 拍板不做）

### 兼容性约束（贯穿本批）
1. 现有端点**签名尽量不变**：只允许新增可选参数/表/header，不允许删改既有必填字段或返回结构。
2. 鉴权沿用 `verify_jwt_if_prod` 语义：**生产强制、dev 放行**，本地开发行为不破坏。
3. 既有 admin token（`X-Admin-Token`）机制**保留**，RBAC 管理员角色与其等效，二选一通过。
4. 存量数据（v1.6 已存在的 checkpoint/thread）不得因迁移丢失或不可访问（见 P0-1 迁移策略）。

---

## 一、产品目标（本批一句话价值）

> 把 GridMind 从「单机演示」升级为「可多人并用的内网系统」：会话与模型按用户隔离（数据地基）、知识库按角色治理（内容安全）、大屏模式预留扩展点（接口占位），为 v2.0 大屏/多 Agent 铺路，且不破坏现有单用户体验。

---

## 二、用户故事（含验收标准）

### 2.1 M-1 多用户数据隔离

**US-1.1** 作为调度员张三，我想只能打开/继续**我自己**的会话，这样别人无法查看或操作我的调度记录。
- 验收：生产模式下，李四的 token 访问张三的 thread（`/chat/stream/{id}`、`/thread/{id}`、`/sessions/{id}/*`、`/interrupt/{id}/*`、`/diagnosis/{id}/reasoning`、`/audit/hitl/{id}`）一律 403/404；张三访问自己的全部正常。

**US-1.2** 作为管理员，我想能查看**所有用户**的会话与审计（监管视角），以便故障排查与合规检查。
- 验收：管理员角色（或 admin token）通过全部会话/线程端点的 owner 校验；无越权误报。

**US-1.3** 作为开发者（dev 模式），我希望本地开发不需要 token 和归属校验，保持既有行为。
- 验收：`APP_ENV` 非 production 时，本批所有新增校验不生效（等同 `verify_jwt_if_prod` 放行口径）；既有 dev 测试/演示流程零改动跑通。

### 2.2 M-2 per-session 模型隔离

**US-2.1** 作为调度员张三，我在会话 A 切到 DeepSeek，会话 B 仍是 Qwen，互不影响。
- 验收：并发两个会话时，A 的推理走 `deepseek-chat`、B 走 `qwen-plus`；后端 `get_model_for_thread(A)` / `get_model_for_thread(B)` 返回各自值。

**US-2.2** 作为调度员，我新建会话时使用全局默认模型，切换只影响当前激活会话。
- 验收：新会话（threads 表无 model_id 记录）默认走 `get_default_model()`；切换模型后仅当前 sessionId 生效，刷新/重进会话后偏好仍在。

**US-2.3** 作为单用户/旧客户端使用者，我不传 session 上下文时仍可全局切换模型（向后兼容）。
- 验收：`POST /models/switch {model_id}`（无 thread_id）行为与 v1.6 完全一致（进程级全局）；`GET /models` 不传 thread_id 返回全局 current。

### 2.3 KB 全局共享 + 角色写权限

**US-3.1** 作为知识管理员，我可以上传/删除规程文档；调度员/运维只能检索阅读。
- 验收：知识管理员/admin 上传、删除成功；调度员/运维/审计调用 `POST /api/knowledge/upload`、`DELETE /api/knowledge/uploads/{id}` 返回 403（生产模式），UI 不显示写入口。

**US-3.2** 作为调度员，我可以检索**所有**共享知识库内容（全局可读，含他人上传的文档）。
- 验收：任意登录用户 `GET /api/knowledge/uploads` 与知识检索端点均可读全量文档列表与内容（D3 决策：不做按用户隔离）。

**US-3.3** 作为审计角色，我只有只读权限，任何 KB 写操作与系统配置操作都被拒绝。
- 验收：审计角色调用上传/删除/灰度写/系统配置端点均 403；审计读端点正常。

### 2.4 F-1 大屏模式接口预留（仅接口，不实现 UI）

**US-4.1** 作为前端工程师，我可以在 `DisplayMode` 中识别 `'bigscreen'`，并在 App.vue 读取 `isBigScreen`，为后续大屏 UI 预留扩展点。
- 验收：`types/theme.ts` 的 `DisplayMode` 联合类型含 `'bigscreen'`；`display.ts` 的 `isDisplayMode` 守卫与 `isBigScreen` getter 就绪；App.vue 暴露 `isBigScreen` 计算属性（不接入任何大屏布局逻辑）。

**US-4.2** 作为访问者，我访问 `/bigscreen` 得到明确的「开发中」占位页，不出现 404 或白屏。
- 验收：路由已注册；占位页含标题与返回入口；不影响既有路由。

**US-4.3** 作为样式维护者，我可以在 tokens 中读到大屏断点 token，未来实现大屏 UI 时无需改 tokens 入口。
- 验收：`tokens.scss` 预留大屏断点变量（如 `--bp-bigscreen` / `data-display-mode="bigscreen"` 占位块），当前不产生可见样式变化。

---

## 三、需求池（P0 / P1 / P2）

> 优先级口径：P0 = 必须（本批核心，不做则多用户不可用/不安全）；P1 = 应该（本批应做，可分批合入）；P2 = 可以（纯占位，不阻断发版）。

### P0（必须）

| ID | 需求 | 验收标准（可测） |
|---|---|---|
| P0-1 | **threads 归属表**：新增 `threads` 表（thread_id, owner_id, created_at, title），提供创建/查询/迁移能力；存量 checkpoint 数据可迁移或懒登记，不丢失、不阻塞访问 | ① 建表/迁移幂等可重复执行；② 新会话创建即写入 owner；③ 存量 thread 首次访问可被正常接管/登记（见 P0-2）；④ 全量 pytest 通过 |
| P0-2 | **owner 校验全端点覆盖**：DB 级归属校验扩展到 `/chat`、`/chat/stream/{id}`、`/thread/{id}`、`/diagnosis/{id}/reasoning`、`/interrupt/{id}/*`、`/sessions/{id}/*`、`/sessions/{id}/events`、`/audit/hitl/{id}`；生产强制、dev 放行；管理员角色放行 | ① 生产模式下跨用户访问全部返回 403/404；② dev 模式全部放行；③ `verify_thread_ownership` 升级为 DB 查询（保留 token claim 快速路径）；④ 越权攻击用例（tests/）覆盖每个端点 |
| P0-3 | **per-session 模型隔离**：后端模型读取支持 thread 维度（threads.model_id 或 checkpoint 状态，二选一由架构定，须提供统一 `get_model_for_thread(thread_id)`）；`POST /models/switch` 新增**可选** `thread_id` 参数（缺省=全局，兼容旧客户端）；`GET /models` 新增可选 `thread_id`；前端 modelStore 与 sessionId 绑定 | ① 双会话并发各自模型正确；② 无 thread_id 时全局行为与 v1.6 一致；③ 新会话回退默认模型；④ 模型切换不越权（只能切自己的会话模型） |

### P1（应该）

| ID | 需求 | 验收标准（可测） |
|---|---|---|
| P1-1 | **RBAC 角色模型**：新增角色枚举（调度员/运维/知识管理员/审计/管理员）+ JWT `role` claim 解析 + 权限解析器（`require_role(...)` 依赖）；无 role claim 默认「调度员」；admin token 等效「管理员」 | ① 5 角色可解析；② 缺失 role 的 token 按调度员处理（不 500）；③ 权限矩阵（§四）逐项有单测 |
| P1-2 | **端点权限映射**：灰度管理（写：set/manual_rollback → 运维/管理员；读：status/history/metrics → 运维/管理员，收口匿名读，衔接 R-1c）；系统配置（`/admin/*`、`/debug/*` → 运维/管理员）；审计读（`/audit/hitl` 列表按角色过滤） | ① 匿名访问灰度读/写端点不再 200/成功；② 运维可灰度与系统配置，调度员不可；③ `/audit/hitl` 列表按角色返回可见范围 |
| P1-3 | **KB 角色写权限**：`POST /api/knowledge/upload`、`DELETE /api/knowledge/uploads/{id}` 需「知识管理员」或「管理员」；读（列表/检索）全局共享；dev 放行 | ① 生产模式非 KB 管理员写操作 403；② 读全量不受角色限制；③ dev 模式上传/删除照常 |

### P2（可以，不阻断）

| ID | 需求 | 验收标准（可测） |
|---|---|---|
| P2-1 | **大屏接口预留**：`types/theme.ts` `DisplayMode` 增加 `'bigscreen'`；`display.ts` 守卫 + `isBigScreen` getter；App.vue 暴露 `isBigScreen` 计算属性（扩展点，不接布局）；`tokens.scss` 预留大屏断点 token；注册 `/bigscreen` 占位路由（返回占位页） | ① 类型/守卫/getter 就绪，现有 standard/presentation 行为零回归；② `/bigscreen` 返回占位页；③ 无大屏 UI 代码合入 |

---

## 四、角色 / 权限矩阵

> 角色：**调度员 / 运维 / 知识管理员 / 审计 / 管理员**。✓ = 允许；✗ = 拒绝；「仅本人」= 受 P0-2 owner 校验约束；「全部」= 跨用户可访问（管理员/审计视角）。

| 端点类别（代表端点） | 调度员 | 运维 | 知识管理员 | 审计 | 管理员 |
|---|---|---|---|---|---|
| **会话管理**（`/chat`、`/chat/stream/{id}`、`/thread/{id}`、`/sessions/{id}/pause\|resume\|rewind\|abort\|events`、`/interrupt/{id}/*`、`/diagnosis/{id}/reasoning`） | 仅本人 | 仅本人 | 仅本人 | 仅本人 | **全部** |
| **灰度管理**（`/grayscale/set`、`/grayscale/manual_rollback`；读 `/grayscale/status\|history\|metrics`） | ✗ | ✓ | ✗ | ✗ | ✓ |
| **KB 读**（知识检索、`GET /api/knowledge/uploads`） | ✓ | ✓ | ✓ | ✓ | ✓ |
| **KB 写**（`POST /api/knowledge/upload`、`DELETE /api/knowledge/uploads/{id}`） | ✗ | ✗ | ✓ | ✗ | ✓ |
| **审计读**（`GET /audit/hitl`、`GET /audit/hitl/{id}`） | 仅本人 thread | 全部 | 仅本人 thread | **全部** | 全部 |
| **系统配置**（`/admin/checkpoint-stats`、`/debug/sync_lag\|sync_force`） | ✗ | ✓ | ✗ | ✗ | ✓ |
| **模型切换**（`POST /models/switch`、`GET /models`） | ✓（session 级） | ✓ | ✓ | ✓ | ✓ |

**矩阵说明**
1. **会话管理**是本批 P0 核心：所有角色都只能操作**自己**的会话，仅管理员（含 admin token）可跨用户。
2. **审计读**：审计/管理员/运维可见全部；调度员/知识管理员仅见本人 thread（避免越权窥探他人操作票）。调度员是否应完全不可见本人审计，见 §七 待确认 Q1。
3. **灰度管理读**：现状匿名可读（`/grayscale/status|history|metrics`），本批随 RBAC 收口到 运维/管理员（衔接路线图 R-1c）。
4. **admin token 兼容**：`/grayscale/set`、`/grayscale/manual_rollback`、`/admin/*`、`/debug/sync_force` 继续接受 `X-Admin-Token`；RBAC 管理员角色与 admin token 等效，二选一通过。
5. **dev 模式**：整张矩阵在生产模式生效；dev（非 `APP_ENV=production`）沿用放行口径，矩阵不生效。

---

## 五、数据结构（threads 表草案）

```sql
-- P0-1：会话归属表（owner 校验 + M-2 模型偏好存储位）
CREATE TABLE IF NOT EXISTS threads (
    thread_id   TEXT PRIMARY KEY,                     -- 与 LangGraph checkpoint thread_id 一致
    owner_id    TEXT NOT NULL,                        -- 归属用户（JWT sub / user_id；管理员视角可跨用户）
    title       TEXT NOT NULL DEFAULT '新会话',        -- 会话标题（为 M-5 上下文管理预留扩展位）
    model_id    TEXT,                                 -- M-2 per-session 模型偏好（NULL = 用全局默认）
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),  -- UTC ISO 串
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 按 owner 的会话列表查询（前端会话侧栏）
CREATE INDEX IF NOT EXISTS idx_threads_owner_updated
    ON threads(owner_id, updated_at DESC);
```

**设计说明**
1. `thread_id` 直接复用 LangGraph checkpoint 主键，**不加代理主键**，避免两套 ID 映射。
2. `owner_id` 取自 JWT `sub`/`user_id`（`verify_jwt_token` 已保证非空）。
3. `model_id` 为 M-2 的**主选存储位**（可查询、简单）；备选为写入 checkpoint 状态字段。架构师可二选一，但必须提供统一读取接口 `get_model_for_thread(thread_id)` 与 `set_model_for_thread(thread_id, model_id)`，且 `NULL` 语义 = 全局默认（保证 US-2.2 / US-2.3 兼容）。
4. **迁移/懒登记策略（P0-1）**：
   - 新会话：`POST /chat` 无 thread_id → 服务端生成 thread_id 并插入 threads 行（owner=当前用户）。
   - 存量会话：提供幂等 backfill 脚本 `scripts/backfill_threads.py` 将既有 checkpoint thread_id 登记（owner 归 `system`/管理员）；同时支持**懒登记**——owner 校验遇到表中无记录但 checkpoint 存在时，将首个成功访问的已认证用户登记为 owner（保证 v1.6 数据可访问、不丢历史）。
   - 严格模式（未知 thread 一律拒绝）为可选项，见 §七 Q2。

---

## 六、UI 设计稿

### 6.1 角色影响哪些 UI（ASCII 草图）

角色在 UI 上的影响 = **导航可见性 + 操作入口显隐 + 会话可见范围**（不引入新页面，仅在既有 Header/导航/会话侧栏做角色感知）：

```
┌──────────────────────────────────────────────────────────────────────┐
│ Header:  ⚡ GridMind 灵枢电网   [会话] [知识库] [灰度] [审计] [管理]   │
│          用户：张三（调度员）● 角色徽标   会话模型：qwen-plus ▾        │
├──────────────────────────────────────────────────────────────────────┤
│ 会话侧栏（仅本人会话，P0-2 owner 过滤）                               │
│  ├─ 会话 A（模型: deepseek-chat）← 激活                               │
│  ├─ 会话 B（模型: qwen-plus）                                         │
│  └─ ＋ 新建会话（默认模型）                                           │
├──────────────────────────────────────────────────────────────────────┤
│ 知识库页：                                                            │
│  ├─ 列表/检索（全员可见，全局共享）                                    │
│  └─ [上传文档] [删除]  ← 仅 知识管理员/管理员 显示（P1-3）              │
├──────────────────────────────────────────────────────────────────────┤
│ 灰度页：仅 运维/管理员 可见（P1-2）                                    │
│ 审计页：审计/管理员/运维 可见全部；调度员仅本人 thread（P1-2）          │
│ 管理页：仅 管理员（P1-2）                                              │
└──────────────────────────────────────────────────────────────────────┘

角色 → UI 规则（本批落地项）：
  1. Header 显示当前用户 + 角色徽标（数据源：JWT role claim，P1-1）
  2. 导航项按角色过滤：灰度/审计/管理 入口仅对应角色可见
  3. 会话侧栏只列本人会话（owner 过滤，P0-2 前端配合）
  4. KB 上传/删除按钮按角色显隐（P1-3）；但读入口全员保留
  5. 模型选择器展示「当前会话模型」，切换仅影响激活会话（M-2，见 6.2）
```

### 6.2 模型切换如何绑定 session（Mermaid 时序图）

```
sequenceDiagram
    participant U as 调度员（用户A）
    participant F as 前端 chatStore+modelStore
    participant B as API /models/switch
    participant T as threads 表(model_id)

    Note over U,F: 会话 X 激活中（chatStore.activeThreadId = X）
    U->>F: 切换模型 → deepseek-chat
    F->>F: 取 activeThreadId = X（无激活会话则不带 thread_id）
    F->>B: POST /models/switch { model_id, thread_id?: X }
    B->>B: 校验 owner(X) = A（P0-2）
    B->>T: UPDATE threads SET model_id='deepseek-chat' WHERE thread_id=X
    B-->>F: { ok, current }
    F->>F: modelStore.setSessionModel(X, 'deepseek-chat')
    Note over B,T: 用户B 的会话 Y 不受影响（各查各的 model_id；NULL 回退全局默认）
```

**前端绑定要点**：`modelStore.current` 由「单值」改为「会话级映射」：`sessionModels: Record<threadId, modelId>` + `globalCurrent`（无 session 上下文回退）；`switchTo(modelId)` 自动携带当前 `activeThreadId`；`getEffectiveModel()` = `sessionModels[active] ?? globalCurrent ?? default`。旧路径（无会话上下文）仍走全局，保证 US-2.3。

### 6.3 大屏占位路由（F-1，ASCII）

```
/bigscreen  →  <BigScreenPlaceholder>
   ┌─────────────────────────────────────┐
   │  大屏模式（开发中）                    │
   │  · 扩展点已预留：DisplayMode='bigscreen'│
   │  · isBigScreen / 断点 token 已就绪     │
   │  [ ← 返回工作台 ]                      │
   └─────────────────────────────────────┘
（仅占位页 + 路由注册；不实现任何大屏 UI）
```

---

## 七、待确认问题

| # | 问题 | 影响 | 建议默认 |
|---|---|---|---|
| Q1 | 调度员是否应可读**本人会话**的 HITL 审计（还是仅审计/管理员/运维可见，调度员完全不可见）？ | 决定 `/audit/hitl/{id}` owner 校验后是否仍对 owner 放行 | 放行「仅本人」；如合规要求「操作者不可见审计」再收紧为 ✗ |
| Q2 | 存量线程归属策略：懒登记（首个访问者接管）vs 管理员 backfill 归 `system`；生产是否需要「未知 thread 一律拒绝」的严格模式？ | P0-1 迁移安全性与越权风险 | 默认：backfill 脚本 + 懒登记；严格模式作为配置项预留 |
| Q3 | RBAC 角色来源：JWT `role` claim（由 token 签发方控制）还是新增后端用户表？当前无用户表 | P1-1 实现方式与部署影响 | 默认 JWT `role` claim（无用户表，最小改动）；用户表列入后续批次 |
| Q4 | `/models/switch` 兼容策略：保留「无 thread_id 写全局」的旧路径，还是 v1.7 起强制 thread_id？ | M-2 与旧客户端兼容 | 默认保留全局回退路径（US-2.3），v1.7 前端始终传 thread_id |
| Q5 | dev 模式下 KB 写权限是否也放行？ | P1-3 本地开发体验 | 默认与 `verify_jwt_if_prod` 一致：dev 放行、生产强制 |

---

## 八、回归与质量（呼应「不允许一丁点 BUG」）

1. 本批触碰**全部会话端点**，为最高风险改动：全量 `pytest` 必须通过，且新增越权攻击用例（跨用户访问每个端点 → 403/404）。
2. 生产模式端到端冒烟（衔接 R-2）：多用户并发会话 → 各自模型 → KB 角色写权限 → 审计读过滤 → 灰度权限，一次通过。
3. 兼容性回归：单用户 dev 流程（无 token 对话/HITL/KB 上传/模型切换）零改动跑通。
4. 工作量预估（供排期）：**M-1 13-21 人天 + M-2 4-7 人天 + F-1 1-2 人天 ≈ 18-30 人天**，建议 1-2 个 sprint。

---

**PRD 完毕，待主理人审阅。**
