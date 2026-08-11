# GridMind（灵枢电网）增量 PRD：开放注册 + 管理员角色×端点权限矩阵可视化

**作者**：许清楚（PM）　**日期**：2026-08-11　**基线**：V1.8.0 认证已上线（auth + users CRUD + RBAC）
**上游输入**：`docs/auth-prd-2026-08-11.md`、`docs/auth-architecture-2026-08-11.md`、`docs/multiuser-architecture-2026-08-10.md`
**主理人已拍板**（不可违背）：① 开放注册，任何人可注册（默认角色 dispatcher），注册即登录（返回 access+refresh），管理员在用户管理页调整角色；② 在现有 UsersView 基础上增强——按角色显示/配置可访问的端点类别（会话/灰度/KB/审计/系统/模型），可视化角色×权限矩阵。
**技术栈**：Python FastAPI（后端）+ Vite + Vue3 + Element Plus + Pinia（前端，维持现状）
**落盘**：本文档

---

## 项目信息

- **Language**：中文
- **Programming Language**：Python FastAPI（后端）+ Vite + Vue3 + Element Plus + Tailwind（前端，维持现状）
- **Project Name**：gridmind_register_rbac
- **原始需求**：① 登录页增加「注册」入口 → 注册表单（username/password/确认密码/可选 email）→ 注册即登录（自动签发 access+refresh 跳主页），默认角色 dispatcher；② UsersView 增强为「5 角色 × 端点类别」权限矩阵可视化，每格 ✓/✗ 且数据源与后端 `require_role` 映射同源（前端只读展示，不承担安全）。

---

## 〇、现状核实结论（设计前提）

| 现状点 | 核实结果 | 对本 PRD 的影响 |
|---|---|---|
| `api/routers/auth.py` | 6 端点：`POST /auth/login`（slowapi 10/min/IP）、`POST /auth/refresh`、`POST /auth/logout`、`GET /auth/me`、`POST /auth/change-password`（严格 `verify_jwt_token`）、`POST /auth/dev-login`（仅非生产） | 新增 `POST /auth/register` 落位本 router；复用共享 limiter 与 `_request_meta`；**不改动任何现有端点** |
| `api/routers/users.py` | 3 端点：`GET /users`、`POST /users`、`PATCH /users/{id}`，全部 `Depends(require_role(Role.ADMIN))`（dev 放行、X-Admin-Token 等效管理员） | 注册端点**不走** `/users`（公开 vs 管理员）；管理员改角色仍走 `PATCH /users/{id}`，语义不变 |
| `api/services/user_service.py` | `create_user(username, password, role, email=None, actor_id=None, ip_address=None, user_agent=None, must_change_password=True, user_id=None)`；`_validate_password`：≥8 位 + 至少一个数字 + 至少一个字母（`settings.password_min_length`）；bcrypt cost 12 + 72 字节截断；username 小写唯一（`^[a-z0-9_.-]{1,64}$`） | **可完整复用为 register**：传 `role="dispatcher"`、`must_change_password=False`（用户自设密码，区别于管理员创建=1）、`actor_id=新用户 id`；冲突 409 / 策略 422 语义直接继承 |
| `web/src/views/UsersView.vue` | 表格列：用户名/角色 tag/状态/最近登录/创建时间/操作（编辑/禁用）；过滤：搜索（username/email）、角色、状态；对话框：用户名、邮箱（可选）、初始密码、角色下拉、禁用开关；`ROLE_OPTIONS` 硬编码 5 角色 | 在本页增强：新增「权限矩阵」Tab/卡片，复用 `ROLE_OPTIONS` 标签映射；矩阵数据改为后端拉取（不硬编码） |
| `web/src/data/navItems.ts` / `menuDrawerGroups.ts` | 前端已有**页面级**角色显隐常量：`ROLES_GRAYSCALE=['operator','admin']`、`ROLES_AUDIT=['auditor','operator','admin']`、`ROLES_SYSTEM=['admin']`（导航显隐 UX） | 这是**导航可见性**，不是端点级权限矩阵；本 PRD 矩阵数据源为后端权威定义，导航常量保持现状（P2 可考虑同源派生） |
| `api/services/rbac.py` | `Role` 5 枚举 + `ROLE_VALUES` + `get_role` + `role_allows` + `require_role`；**代码中无集中「端点类别→角色」映射表**（权限矩阵目前只存在于 multiuser 架构文档 §3.4 表格，实际强制散落在 `main.py` / `knowledge_upload.py` 的 `Depends(require_role(...))` 装饰器） | **需新增单一权威定义**（矩阵可视化数据源），并用一致性测试防漂移；`require_role` 语义零改动 |
| 前端会话持久化 | `stores/auth.ts` 已有 `applyLoginResponse(resp)`：access 内存 + refresh localStorage + user + status=authenticated；`login/devLogin/refresh` 均复用 | 注册即登录 = 前端调 `register()` → 后端返回与 login 同构的 `LoginResponse` → 复用 `applyLoginResponse`，一行接入 |
| 数据库 | `users` / `refresh_tokens` / `auth_audit_log` 三表已建（幂等迁移）；`auth_audit_log.event_type` 为自由文本列 | **无新表**；注册仅 INSERT users 一行 + 新增审计事件类型 `register_success` / `register_failed` |

---

## 一、产品目标（一句话）

让 GridMind 从「仅管理员建号」升级为**开放自助注册**（默认 dispatcher 最小权限、注册即登录、防滥用限流），并把**后端权威的角色×端点权限矩阵**以可视化形式呈现给管理员（只读、同源、不漂移），同时保持现有 auth/users/RBAC 语义零破坏。

---

## 二、用户故事（每条含验收标准）

### US-1 开放注册：登录页「注册」入口 → 注册表单 → 注册即登录自动跳主页

- **场景**：无账号用户打开登录页，点击「注册」切换注册表单，填写用户名/密码/确认密码/可选邮箱后提交。
- **验收标准**：
  - AC1-1：登录页底部有「没有账号？立即注册」入口（生产与 dev 均可见），点击后同页切换为注册表单（或独立 `/register` 路由，见 §十 待确认 2）。
  - AC1-2：注册表单字段：用户名（必填，小写字母/数字/`_-.`，1-64 位）、密码（必填，≥8 位含数字+字母，与 login 策略一致）、确认密码（必填，前后端双重校验一致）、邮箱（可选）。
  - AC1-3：提交成功 → `POST /auth/register` 返回 access+refresh（**注册即登录**），前端复用登录会话持久化路径（access 内存 + refresh localStorage + user），**自动跳转** redirect ?? 首页；UserBadge 立即显示新用户与 dispatcher 角色徽标。
  - AC1-4：用户名已存在 → 明确错误 `409 用户名已存在`；邮箱已占用 → `409 邮箱已被使用`；用户名非法/密码弱 → `422` 明确文案（与 users CRUD 一致）。
  - AC1-5：确认密码不一致 → 前端本地拦截（不提交），提示「两次输入的密码不一致」。
  - AC1-6：注册成功后新用户 `role=dispatcher`、`disabled=0`、`must_change_password=0`（自设密码，无需首次强制改密——区别于管理员创建=1）。
  - AC1-7：提交中按钮 loading、防重复提交；失败不清空已填用户名（仅错误提示条）。

### US-2 注册安全：注册限流（防滥用）+ 审计 + 默认 dispatcher 最小权限

- **场景**：开放注册引入批量注册/机器人风险，需在安全基线内收敛。
- **验收标准**：
  - AC2-1：`POST /auth/register` 叠加 per-IP 限流（slowapi，建议 `5/minute`，比 login `10/minute` 更严；超限 → 429）。
  - AC2-2：注册事件写入 `auth_audit_log`：成功 `register_success` / 失败 `register_failed`（含 user_id/username/ip/user_agent/detail；不存密码/明文 token）；审计写库失败不阻断注册主流程（复用 AuthAuditService 降级语义）。
  - AC2-3：注册角色**固定** dispatcher——请求体**不含 role 字段**，杜绝「注册即提权」；角色调整仅由管理员在用户管理页 `PATCH /users/{id}` 完成。
  - AC2-4：新用户默认 `disabled=0`（注册即可用）；被管理员禁用后登录/refresh/me 拒绝（既有语义不变）。
  - AC2-5：用户名/邮箱唯一性由 users 表 UNIQUE 约束 + 409 兜底（复用 create_user 逻辑），并发注册不产生重复账号。
  - AC2-6：P2 预留：邮箱验证（`email_verified`）、验证码（captcha）作为防机器人第二层（本批不做，见 §三 P2）。

### US-3 权限矩阵可视化：5 角色 × 7 端点类别矩阵，每格 ✓/✗ 来自后端权威映射

- **场景**：管理员在用户管理页查看/核对各角色可访问的端点类别，快速理解权限边界。
- **验收标准**：
  - AC3-1：UsersView 内新增「权限矩阵」视图（Tab 或卡片），展示 **5 角色（dispatcher/operator/kb_admin/auditor/admin）× 7 端点类别（会话管理/灰度/KB 写/KB 读/审计/系统配置/模型切换）** 矩阵。
  - AC3-2：每格显示 ✓（可访问）/ ✗（不可访问）；「仅本人数据」语义（会话/审计）以 ✓ + 角标/悬浮说明表达（如「✓(本人)」），数据来自后端 `scope` 字段（见 §四）。
  - AC3-3：矩阵数据**只来自** `GET /rbac/matrix`（或等价端点），前端**不硬编码**任何权限布尔值；后端返回 `matrix: { role: { category: bool } }` + 可选 `scope`。
  - AC3-4：矩阵与后端 `require_role` 映射**同源**——后端单一权威定义驱动矩阵端点，一致性测试防漂移（见 §七 3）。
  - AC3-5：页面加载失败（后端 4xx/5xx）→ 显示错误态 + 重试按钮，不渲染伪造矩阵。
  - AC3-6：矩阵为**只读**展示，无任何勾选/编辑能力（「配置可访问的端点类别」由后端映射决定，本批不做 UI 改权限）。

### US-4 管理员可查看角色说明：每角色一句话职责说明 + 所属矩阵高亮

- **场景**：管理员点击/悬浮角色（行或列头）时看到该角色职责说明，直观理解权限设计意图。
- **验收标准**：
  - AC4-1：矩阵页提供每角色一句话说明（数据来自 `GET /rbac/matrix.roles[].description`，后端权威，前端不硬编码）。
  - AC4-2：点击角色名（列头）→ 该列高亮 + 展示职责说明卡（如「调度员 = 日常调度与对话，仅能访问自己的会话」）。
  - AC4-3：点击端点类别（行头）→ 该行高亮 + 展示类别说明与代表端点（`categories[].endpoints`，如「灰度 = /grayscale/*」）。
  - AC4-4：P1：矩阵支持按角色/类别过滤、搜索（缩小到关心的行/列）。

### US-5 与现有 RBAC 一致性：矩阵数据源与后端 require_role 映射同源，前端只读不承担安全

- **场景**：矩阵展示与真实强制不一致会误导管理员，且前端展示绝不能成为安全边界。
- **验收标准**：
  - AC5-1：`GET /rbac/matrix` 由后端生成，数据源 = `api/services/rbac.py`（或新增 rbac_matrix 模块）中的**单一权威定义**（如 `ROLE_CATEGORY_MATRIX`）；该定义与 `require_role` 实际调用点一致。
  - AC5-2：新增/扩展一致性测试（`tests/test_rbac_matrix.py`）：逐类别断言矩阵允许角色 == 各端点 `Depends(require_role(...))` 实参（灰度=OPERATOR,ADMIN；KB 写=KB_ADMIN,ADMIN；系统=OPERATOR,ADMIN；审计=AUDIT_FULL_ACCESS_ROLES；会话/模型=全员+owner 校验），任一漂移 → 测试红。
  - AC5-3：前端矩阵**只读**：即使矩阵显示 ✓，实际访问仍由后端 `require_role` / `verify_*` 判定；前端不拦截、不授权、不改后端映射。
  - AC5-4：`require_role` / `get_role` / `verify_jwt_if_prod` / `verify_thread_ownership*` / `ensure_thread_owned` / X-Admin-Token 等效管理员——**语义零改动**。
  - AC5-5：现有 auth（login/refresh/logout/me/change-password/dev-login）、users CRUD、RBAC 全部不回归（全量 pytest + vue-tsc 双绿）。

---

## 三、需求池

### P0（必须有，本批交付）
| # | 需求 | 说明 |
|---|---|---|
| P0-1 | `POST /auth/register`（公开） | username/password/可选 email → 与 login 同构响应（注册即登录）；复用 `UserService.create_user`（role=dispatcher、must_change_password=0）；409 冲突 / 422 策略 |
| P0-2 | 注册入口 + 注册表单（前端） | LoginView 增加「注册」入口与注册表单（用户名/密码/确认密码/可选 email）；提交成功后复用 authStore 会话持久化自动跳主页 |
| P0-3 | 注册限流 + 审计 | per-IP slowapi 限流（建议 5/min，可配 `REGISTER_RATE_LIMIT_PER_MINUTE`）；`register_success`/`register_failed` 入 auth_audit_log（US-2 验收硬性要求，建议随 P0 端点同批上线，见 §十 待确认 1） |
| P0-4 | `GET /rbac/matrix`（或等价） | 后端单一权威矩阵定义 → 序列化 `{roles, categories, matrix, scope, generated_at}`；`require_role(Role.ADMIN)`（dev 放行） |
| P0-5 | 矩阵可视化组件 | UsersView 新增「权限矩阵」Tab/卡片：5×7 矩阵 ✓/✗、行/列头悬浮说明、加载/错误/重试态；数据仅来自后端，前端零硬编码 |
| P0-6 | 一致性测试 | 扩展 `tests/test_rbac_matrix.py`：矩阵 == require_role 实际调用点（防漂移） |

### P1（应该有，本批或紧接批次）
| # | 需求 | 说明 |
|---|---|---|
| P1-1 | 注册限流增强 | per-IP 每日上限（如 20/day，防慢速批量注册）；可配 |
| P1-2 | 矩阵交互增强 | 按角色/类别过滤、搜索、排序；点击高亮 + 角色职责说明卡（US-4） |
| P1-3 | 角色职责说明数据 | `GET /rbac/matrix.roles[].description` 由后端权威维护（随矩阵同源下发） |
| P1-4 | 邮箱格式校验 | register 若填 email，后端做基础格式校验（422 明确文案）；前端同步轻校验 |

### P2（最好做）
| # | 需求 | 说明 |
|---|---|---|
| P2-1 | 邮箱验证 | `users.email_verified` 字段（本批不加表字段）；注册后发验证邮件，未验证可限制部分能力 |
| P2-2 | 验证码（captcha） | 图形/滑块验证码作为防机器人第二层（与 P1-1 限流叠加） |
| P2-3 | 自定义角色 | 超出 5 固定角色的自定义角色 + 矩阵动态渲染 |
| P2-4 | 按钮级权限 | 前端按后端矩阵/权限点显隐按钮（如 KB 上传按钮、灰度按钮），仍以后端强校验兜底 |
| P2-5 | 导航同源 | `navItems.ts` 角色显隐从 `GET /rbac/matrix` 派生（消除前端导航常量与后端矩阵两处漂移） |

---

## 四、API 契约草案

> 约定：统一错误体 `{"detail": "..."}`；新增端点不改动既有 auth/users 端点契约。

### 4.1 `POST /auth/register`（公开，P0）

请求：
```json
{ "username": "alice", "password": "******", "email": "alice@example.com" }
```
（`email` 可选；**请求体不含 role**——角色固定 dispatcher，防注册即提权）

响应 `200`（**与 `POST /auth/login` 响应完全同构**——注册即登录）：
```json
{
  "access_token": "<jwt>",
  "refresh_token": "<opaque>",
  "token_type": "bearer",
  "expires_in": 900,
  "mfa_required": false,
  "user": { "id": "uuid", "username": "alice", "display_name": "alice", "role": "dispatcher" }
}
```

错误：
- `409` 用户名已存在 / 邮箱已被使用（明确文案；注册场景允许暴露存在性）
- `422` 用户名非法（仅小写字母/数字/`_-.` 1-64 位）/ 密码不满足策略（≥8 位含数字+字母）/ email 格式非法（若 P1-4 启用）
- `429` IP 限流（建议 5/min/IP；`REGISTER_RATE_LIMIT_PER_MINUTE` 可配）

实现要点（供架构师参考）：
- 复用 `UserService.create_user(username, password, role="dispatcher", email=..., actor_id=<新用户id>, must_change_password=False)`；冲突/策略异常语义直接继承。
- 注册即登录：建议 `AuthService.register()` 编排 = create_user + 签发 access/refresh（与 login 同构 claims：`sub`/`user_id`/`role`/`name`/`iss`/`iat`/`exp`，**不含 thread_id**）+ INSERT refresh_tokens + 审计 `register_success`。
- 审计 `register_failed`：用户名冲突/密码弱等失败事件落库（detail 记录失败类别，不存明文）。

### 4.2 `GET /rbac/matrix`（管理员，P0）

- 依赖：`require_role(Role.ADMIN)`（dev 放行、X-Admin-Token 等效管理员——与 UsersView 同权；如需全员可读见 §十 待确认 4）。
- 响应 `200`：
```json
{
  "roles": [
    { "key": "dispatcher", "label": "调度员", "description": "日常调度与对话；会话仅本人可见" },
    { "key": "operator",   "label": "运维",     "description": "会话 + 灰度 + 系统配置（监控与运维）" },
    { "key": "kb_admin",   "label": "知识管理员", "description": "会话 + 知识库读写" },
    { "key": "auditor",    "label": "审计",     "description": "会话（仅本人）+ 审计全量只读" },
    { "key": "admin",      "label": "管理员",   "description": "全部权限 + 用户管理" }
  ],
  "categories": [
    { "key": "session",  "label": "会话管理", "endpoints": ["/chat", "/thread/{id}", "/sessions/{id}/*"] },
    { "key": "grayscale","label": "灰度",     "endpoints": ["/grayscale/*"] },
    { "key": "kb_write", "label": "KB 写",    "endpoints": ["POST /api/knowledge/upload", "DELETE /api/knowledge/uploads/{id}"] },
    { "key": "kb_read",  "label": "KB 读",    "endpoints": ["GET /api/knowledge/uploads"] },
    { "key": "audit",    "label": "审计",     "endpoints": ["GET /audit/hitl", "GET /audit/hitl/{id}"] },
    { "key": "system",   "label": "系统配置", "endpoints": ["/admin/checkpoint-stats", "/debug/sync_lag", "/debug/sync_force"] },
    { "key": "model",    "label": "模型切换", "endpoints": ["GET /models", "POST /models/switch"] }
  ],
  "matrix": {
    "dispatcher": { "session": true,  "grayscale": false, "kb_write": false, "kb_read": true,  "audit": true,  "system": false, "model": true },
    "operator":   { "session": true,  "grayscale": true,  "kb_write": false, "kb_read": true,  "audit": true,  "system": true,  "model": true },
    "kb_admin":   { "session": true,  "grayscale": false, "kb_write": true,  "kb_read": true,  "audit": true,  "system": false, "model": true },
    "auditor":    { "session": true,  "grayscale": false, "kb_write": false, "kb_read": true,  "audit": true,  "system": false, "model": true },
    "admin":      { "session": true,  "grayscale": true,  "kb_write": true,  "kb_read": true,  "audit": true,  "system": true,  "model": true }
  },
  "scope": {
    "session": { "dispatcher": "own", "operator": "own", "kb_admin": "own", "auditor": "own", "admin": "all" },
    "audit":   { "dispatcher": "own", "operator": "all", "kb_admin": "own", "auditor": "all", "admin": "all" }
  },
  "generated_at": "2026-08-11T12:00:00+00:00"
}
```

契约说明：
1. `matrix` 为**核心契约**（与需求一致：`{ role: { category: bool } }`）：bool = 该角色能否访问该端点类别。
2. `scope` 为**扩展字段**（owner 维度语义）：`own`=仅本人数据（会话、审计对 dispatcher/kb_admin）、`all`=全量可见（审计对 auditor/operator/admin、会话对 admin）。前端将 `scope=own` 的 ✓ 渲染为「✓(本人)」角标/悬浮说明；如主理人希望纯 bool 简化，可省略 scope（见 §十 待确认 7）。
3. 数据源：后端新增**单一权威定义**（建议 `api/services/rbac.py` 内新增 `ROLE_CATEGORY_MATRIX: dict[str, dict[str, str | bool]]` + `ROLE_META`（label/description）+ `CATEGORY_META`（label/endpoints）），`GET /rbac/matrix` 直接序列化；一致性测试见 §七 3。
4. 矩阵语义与 multiuser-architecture §3.4 一致（会话=全员+owner、灰度=OPERATOR,ADMIN、KB 读=全员、KB 写=KB_ADMIN,ADMIN、审计=AUDIT_FULL_ACCESS_ROLES+owner、系统=OPERATOR,ADMIN、模型=全员+thread owner 校验）。

---

## 五、数据结构

**无新表、无新字段**（本批）：

- `users`：注册 = INSERT 一行 `(id=UUID4, username, email?, password_hash, role='dispatcher', disabled=0, must_change_password=0, password_history='[]', failed_attempts=0, locked_until=NULL, last_login_at=NULL, created_at, updated_at)`——完全复用现有表结构与 `create_user` 写路径。
- `refresh_tokens`：注册即登录 = 与 login 相同，INSERT 一行（token_hash=SHA-256、expires_at=now+7d、ip/ua）。
- `auth_audit_log`：新增事件类型 `register_success` / `register_failed`（event_type 为自由文本列，**无需迁移**）；detail 记失败类别/actor，不存密码/明文 token。
- P2 预留（本批**不加**）：`users.email_verified`（邮箱验证）、`users.source`（注册来源：register/admin_create，便于审计与运营分析——如需可 P1 低成本加列）。

---

## 六、UI 设计稿（ASCII）

### 6.1 登录页 `/login`（增加注册入口 + 注册表单，同页 Tab 切换）

```
┌─────────────────────────────────────────────┐
│              ⚡ GridMind 灵枢电网             │
│              ────────────────                │
│                                             │
│        [ 登录 ]  [ 注册 ]  ← Tab 切换        │
│                                             │
│   ┌───────────────────────────────────────┐ │
│   │  用户名   [ alice            ]        │ │
│   └───────────────────────────────────────┘ │
│   ┌───────────────────────────────────────┐ │
│   │  密码     [ ••••••••          ]  👁    │ │
│   └───────────────────────────────────────┘ │
│   ┌───────────────────────────────────────┐ │
│   │  确认密码 [ ••••••••          ]  👁    │ │  ← 仅注册模式
│   └───────────────────────────────────────┘ │
│   ┌───────────────────────────────────────┐ │
│   │  邮箱     [ a@b.com          ]  (可选) │ │  ← 仅注册模式
│   └───────────────────────────────────────┘ │
│                                             │
│        [ 错误提示条（红，明确文案）]          │
│                                             │
│            [    注  册    ]  ← 主按钮        │
│                                             │
│   （底部小字：注册即登录；默认角色为调度员，   │
│     密码至少 8 位且包含数字和字母）            │
└─────────────────────────────────────────────┘
```
- 登录/注册 Tab 切换保留各自表单状态；错误提示条复用现有 `el-alert` 样式；提交 loading 防重复。
- 注册成功后复用 authStore 会话路径 → 跳 redirect ?? 首页（与登录成功一致）。

### 6.2 用户管理页 `/admin/users`（增加「权限矩阵」Tab）

```
┌─────────────────────────────────────────────────────────────────────┐
│ 用户管理                                    [ + 新建用户 ]  [🔍 搜索] │
│                                                                     │
│  ┌────────────┬───────────────────────────────────────────────────┐ │
│  │ 用户列表     │ 权限矩阵                                          │ │  ← el-tabs
│  └────────────┴───────────────────────────────────────────────────┘ │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │ 端点类别      │ 调度员   │ 运维   │ 知识管理员 │ 审计   │ 管理员 ││
│  ├──────────────┼─────────┼───────┼──────────┼───────┼─────────┤│
│  │ 会话管理      │ ✓(本人)  │ ✓(本人)│ ✓(本人)  │ ✓(本人)│ ✓(全部) ││
│  │ 灰度          │ ✗       │ ✓     │ ✗        │ ✗     │ ✓       ││
│  │ KB 写         │ ✗       │ ✗     │ ✓        │ ✗     │ ✓       ││
│  │ KB 读         │ ✓       │ ✓     │ ✓        │ ✓     │ ✓       ││
│  │ 审计          │ ✓(本人)  │ ✓(全部)│ ✓(本人)  │ ✓(全部)│ ✓(全部) ││
│  │ 系统配置      │ ✗       │ ✓     │ ✗        │ ✗     │ ✓       ││
│  │ 模型切换      │ ✓       │ ✓     │ ✓        │ ✓     │ ✓       ││
│  └──────────────┴─────────┴───────┴──────────┴───────┴─────────┘│
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │ 角色说明（点击「调度员」列头后高亮）：                             ││
│  │   🎯 调度员 = 日常调度与对话，仅能访问自己的会话                   ││
│  │   数据来源：GET /rbac/matrix（后端权威，只读展示）                ││
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
```
- 每格：✓ 绿 / ✗ 灰；「(本人)/(全部)」角标来自 `scope`；行头悬浮显示类别代表端点（`categories[].endpoints`）。
- 点击行/列头高亮 + 说明卡（US-4）；P1 增加按角色/类别过滤与搜索。
- 加载失败 → 错误态 + 重试；**无任何勾选/编辑交互**（只读）。

---

## 七、与 RBAC 的关系

1. **矩阵只读展示，权威映射不变**：`GET /rbac/matrix` 只做序列化展示，不改变任何 `require_role` / `verify_*` 判定；「配置可访问的端点类别」仍由后端映射（本批不在 UI 改权限）。
2. **注册默认 dispatcher，角色调整仍走管理员**：register 固定 `role='dispatcher'`（最小权限：仅自己会话 + KB 读 + 模型切换 + 审计本人）；管理员在 UsersView 改角色仍走 `PATCH /users/{id}`（`require_role(ADMIN)`），语义零改动。
3. **同源防漂移**：
   - 后端新增单一权威定义（`ROLE_CATEGORY_MATRIX` + `ROLE_META` + `CATEGORY_META`，建议放 `api/services/rbac.py`）；
   - `GET /rbac/matrix` 直接序列化该定义（前端**不硬编码**矩阵）；
   - 一致性测试（扩展 `tests/test_rbac_matrix.py`）：断言矩阵允许角色 == 各端点 `Depends(require_role(...))` 实参与 owner 语义，任一漂移测试红；
   - 前端 `navItems.ts` 页面级导航显隐常量保持现状（导航 UX 与端点能力分离）；P2 再考虑同源派生（§三 P2-5）。
4. **前端只读不承担安全**：矩阵显示 ✓ 不代表绕过后端；前端无授权/拦截职责，安全边界始终在后端。
5. **现有鉴权口径不变**：`/auth/login|refresh|logout|register|dev-login` 公开；`/auth/me`、`/auth/change-password` 不变；`/users*`、`/rbac/matrix` 为 `require_role(ADMIN)`（dev 放行）；其余业务端点鉴权依赖零改动。

---

## 八、安全设计（增量）

1. **注册限流（防滥用第一层）**：`POST /auth/register` 叠加 per-IP slowapi（建议 `5/minute`，配置项 `REGISTER_RATE_LIMIT_PER_MINUTE`，与 login 的 `10/minute` 区分）；P1 增加 per-IP 日上限。
2. **防注册即提权**：请求体**不含 role 字段**，后端固定 `role='dispatcher'`（即使恶意传 role 也忽略/422）。
3. **密码安全**：复用 `UserService._hash_password`（bcrypt cost 12 + 72 字节截断）与 `_validate_password`（≥8 位数字+字母），与 login 策略完全一致。
4. **审计**：`register_success` / `register_failed` 入 `auth_audit_log`（复用 AuthAuditService，写失败仅告警不阻断）。
5. **防枚举权衡**：注册场景允许 409「用户名已存在」暴露存在性（注册产品形态的固有语义，与登录防枚举不冲突——登录仍统一 401 文案）。
6. **Token 语义**：注册签发的 access/refresh 与 login 完全同构（claims 含 `sub`/`user_id`/`role`/`name`/`iss`/`iat`/`exp`，**不含 thread_id**），自动获得既有鉴权体系全部能力（401 自动续期、轮换、登出撤销）。
7. **矩阵端点权限**：`GET /rbac/matrix` 用 `require_role(ADMIN)`（dev 放行、X-Admin-Token 等效），不向匿名/低权限开放（若需全员只读见 §十 待确认 4）。

---

## 九、dev 模式兼容

1. 默认 dev 体验不变：`verify_jwt_if_prod` / `require_role` dev 放行语义零改动；`gridmind-dev-token` 不可解析 → 前端默认「访客/调度员」。
2. 注册在 dev 同样可用（连本地库，建真实 dispatcher 账号）；`/auth/register` 在 dev 也叠加限流（防本地误刷）。
3. `GET /rbac/matrix` dev 放行（返回真实矩阵），便于前端联调矩阵组件。
4. 生产门禁不变：`/auth/dev-login` 生产 404；register 生产照常开放（正是本需求目标）。

---

## 十、待确认问题（主理人拍板）

1. **注册限流阈值与批次**：per-IP `5/minute` 是否接受？是否随 P0 端点同批上线（建议是——开放注册无限流即有滥用窗口）？per-IP 日上限（如 20/day）本批还是 P1？
2. **注册入口形态**：同页 Tab 切换（LoginView 内 `[登录|注册]`，默认，改动最小）还是独立 `/register` 路由（public）？
3. **email 字段**：可选（与 create_user 一致，默认）还是必填？若必填需后端格式校验 + 前端必填校验。
4. **矩阵访问权**：仅 admin（随 UsersView，默认）还是全员可读（独立 `/rbac` 页面，各角色查看自身权限范围）？
5. **注册即登录的 must_change_password**：确认 `0`（用户自设密码无需强制改密，默认）——与管理员创建=1 区分。
6. **矩阵 cell 语义**：接受 `scope`（own/all）扩展字段表达「仅本人」语义（默认），还是简化为纯 bool（会话/审计也显示 ✓，tooltip 说明）？
7. **一致性测试强度**：接受「矩阵 + 一致性测试」方案（require_role 调用点不动，仅新增权威定义与测试守护）？还是要求把 require_role 装饰器**重构为从同一矩阵读取**（更强同源、改动面更大）？
8. **前端导航同源（P2-5）**：是否预留把 `navItems.ts` 角色显隐改为从 `GET /rbac/matrix` 派生的接口形态（本批不做，仅确认方向）？

---

## 约束与不破坏项（Checklist）

- [ ] 不破坏现有 auth：`/auth/login|refresh|logout|me|change-password|dev-login` 契约与行为零改动
- [ ] 不破坏 users CRUD：`GET/POST /users`、`PATCH /users/{id}` 语义与权限（require_role(ADMIN)）不变
- [ ] 不破坏 RBAC：`get_role` / `require_role` / `verify_jwt_if_prod` / `verify_thread_ownership*` / `ensure_thread_owned` / X-Admin-Token 等效管理员——零改动
- [ ] 注册默认 dispatcher；角色调整仅由管理员在用户管理页做（register 请求体不含 role）
- [ ] 矩阵数据源与后端 require_role 映射同源（后端生成，前端不硬编码；一致性测试防漂移）
- [ ] 前端矩阵只读展示，不承担安全
- [ ] 无新表/新字段（本批）；复用 users + auth_audit_log
- [ ] dev 模式兼容：默认 dev 体验不变；register/rbac/matrix 在 dev 可用
- [ ] 全量回归：`pytest` + `vue-tsc` 双绿（含既有 743+ 基线）
