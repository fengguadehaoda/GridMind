# GridMind HITL Edit & Continue 模式 PRD

> **文档版本**：v1.0 · 2026  
> **作者**：许清楚（产品经理）  
> **状态**：待评审  
> **关联**：竞品分析 `deliverables/competitive-analysis.md` 第 406-414 行 P0-3

---

## 1. 产品目标

**一句话目标**：在 Agent 高危操作拦截点引入「修改后继续」模式，让运维人员可以就地编辑 Agent 生成的工单/方案字段，一次性完成「修 → 验 → 批 → 执」全流程，将人工介入效率提升 50%，减少 Agent 反复迭代。

**核心指标（3 个可量化指标）**：

| 指标 | 当前基线 | 30 天目标 | 测量方式 |
|---|---|---|---|
| **HITL 一次通过率** | 约 40%（拒绝率较高，因 Agent 工单字段常需人工从头改） | ≥ 70% | `interrupt_action` 审计日志 `approved_with_edit` / `pending` |
| **单次高危操作平均耗时** | 约 90 秒（拒绝后让 Agent 重做 → 再中断） | ≤ 45 秒 | `pending → resume` 时间戳差 |
| **Agent 迭代次数（高危场景）** | 2.4 次/任务 | ≤ 1.4 次/任务 | 同 thread_id 中 `pending` 事件出现次数 |

---

## 2. 用户故事

### US-1：调度员就地修改派单（核心场景）
- **角色**：值班调度员 王工
- **场景**：Agent 基于诊断报告生成了对 `TR-002` 设备的派单建议，但实际值班表里王工知道今晚 TR-002 区域有保电任务，需调整 `priority` 从 `high` 改为 `medium`，并补充"避开 22:00-24:00 保电时段"备注
- **想做什么**：在 HITL 弹窗里直接修改 `priority` 下拉框 + 备注文本框，点"修改后批准"按钮
- **为什么**：从 0 让 Agent 重新规划需要 60+ 秒且可能丢失现场上下文；就地修改 10 秒搞定
- **验收标准**：
  - 弹窗中 `priority` 字段为可编辑下拉框，默认值=Agent 建议值
  - 备注文本框 ≤ 500 字，超出有红色提示
  - 点"修改后批准"后 5 秒内工单下发到工单系统
  - 修改记录在审计日志中可见（who/when/before-after）

### US-2：运维人员纠正设备 ID
- **角色**：现场运维 张师傅
- **场景**：Agent 诊断报告把 `BB-006 35kV 母线`误关联为 `TR-001`，实际应该是 `BB-007 10kV 母线`
- **想做什么**：在弹窗里改正 `device_id`，系统自动校验设备是否存在
- **为什么**：派错设备是严重安全事故，必须能纠错
- **验收标准**：
  - `device_id` 字段是带搜索补全的下拉框（不允许自由输入）
  - 输入不存在的设备 ID → 字段红框 + 错误提示"设备不存在"
  - 修改后必须重跑 `safety_agent.check_safety_compliance`，通过后才能"批准"

### US-3：安全审核员直接拒绝
- **角色**：安全审核 李主任
- **场景**：Agent 建议对带电设备做"停机"操作，但当时电网负荷极高不能停机
- **想做什么**：点"拒绝"按钮，填写拒绝原因
- **为什么**：当前已有功能，需保证不被新功能影响
- **验收标准**：
  - 拒绝按钮始终可用，与 Edit 模式并存
  - 拒绝原因必填，≤ 200 字
  - 拒绝后图继续执行（用降级方案或返回用户）

### US-4：安全审核员多人会签（Escalation）
- **角色**：值班调度 + 部门主管 + 安全员
- **场景**：220kV 主变停机检修需要三人会签（电力规程要求）
- **想做什么**：调度员审批后，弹窗"转交主管"；主管批准后转安全员；任一环节拒绝则终止
- **为什么**：现行《电力安全工作规程》第 4.3.2 条要求关键操作多人审核
- **验收标准**（**P1 范围**）：
  - 审批窗口支持"转交下一人"按钮
  - 列表显示所有审核人进度（待审/已批/拒绝）
  - 全部通过后工具才真正执行

### US-5：审计员事后追溯
- **角色**：审计员
- **场景**：事故发生后需追溯"谁批准了什么"
- **想做什么**：按 `thread_id` 或时间范围查询 HITL 审计记录
- **验收标准**：
  - 审计页面按 `thread_id` 列出所有 HITL 事件
  - 每条记录包含：操作工具、原参数、编辑后参数、编辑人、时间戳、最终决策、安全重检结果

---

## 3. HITL 三大模式定位（CRITICAL）

| 模式 | 适用场景 | 用户操作 | 风险等级 | UI 表现 | 优先级 |
|---|---|---|---|---|---|
| **Approval**（保留） | 标准审批，Agent 输出完全可接受 | 批准 / 拒绝 | 高 | 当前弹窗（仅按钮） | P0 |
| **Edit & Continue**（新增） | 工单局部修改后再执行 | 编辑字段 → 重新校验 → 批准 | 高 | 弹窗 + 内嵌表单编辑器 | **P0** |
| **Escalation**（新增） | 多人会签 / 转交上级 | 选人 → 转交 → 等待多人审批 | 极高 | 弹窗 + 选人组件 + 进度条 | P1 |

**Edit & Continue 模式是该 PRD 核心**，下文 3.1-3.3 详述。

### 3.1 可编辑字段范围

| 工具 | 可编辑字段 | 不可编辑字段（系统锁定） |
|---|---|---|
| `dispatch_work_order` | `description`（描述）、`priority`（优先级） | `device_id`（影响安全）、`work_order_id`（系统生成） |
| `suggest_shutdown` | `reason`（停运原因） | `device_id`、`shutdown_id` |
| （未来扩展）诊断结论 | `root_cause`（根因）、`recommendation`（修复建议） | `device_id`、`conclusion_id` |
| （未来扩展）调度指令 | `steps`（操作步骤）、`remark`（备注） | `instruction_id`、时间戳 |

**锁定原则**：
- 设备 ID 不可编辑（必须通过 US-2 的纠错流程另发起）
- 系统生成的 ID、时间戳不可编辑
- Agent 用于推理的输入字段（故障参数、检测 z-score）不可编辑

### 3.2 编辑校验规则

| 校验项 | 规则 | 失败处理 |
|---|---|---|
| 必填字段 | `description` / `reason` 不能为空 | 字段红框 + 提交按钮禁用 |
| 优先级枚举 | `priority` ∈ {`high`, `medium`, `low`} | 提示"无效优先级" |
| 描述长度 | `description` ≤ 500 字 | 字数计数器红色 + 提交按钮禁用 |
| 设备 ID 校验 | 修改后必须存在于 `device_list`（US-2 流程） | 设备 ID 字段红框 + 错误提示 |
| **安全重检** | 编辑后必须调用 `safety_agent.check_safety_compliance(operation, device_type)` | 顶部黄色横幅"安全重检中..."，重检失败则禁止批准 |
| **冲突检测** | 编辑内容不能与已派发的同设备同优先级工单冲突（查 `work_order` 表） | 红色横幅"⚠️ 该设备已有 priority=high 的在途工单，请先合并或降低优先级" |
| **停机时间校验** | 若 `reason` 含"保电"、"迎峰度夏"等关键词，强制 `priority` ≤ `medium` | 弹窗提示 + 字段联动 |

### 3.3 编辑记录（审计）

每条 HITL 事件（无论哪种决策）必须在 `hitl_audit_log` 表中留痕：

| 字段 | 类型 | 说明 |
|---|---|---|
| `audit_id` | UUID | 主键 |
| `thread_id` | string | 关联对话 |
| `tool_name` | string | 触发 HITL 的工具名 |
| `original_args` | JSON | Agent 生成的原始参数 |
| `edited_args` | JSON \| null | 人工编辑后参数（仅 Edit 模式有值） |
| `decision` | enum | `approved` / `approved_with_edit` / `rejected` / `escalated` |
| `editor_id` | string | 操作人 ID（从 JWT token 取） |
| `editor_name` | string | 操作人姓名（前端传入便于审计展示） |
| `editor_role` | enum | `dispatcher` / `operator` / `safety_officer` / `supervisor` |
| `edit_reason` | string | 修改原因（编辑时必填） |
| `safety_recheck_result` | JSON | 安全重检结果（通过/失败 + 详情） |
| `conflict_check_result` | JSON | 冲突检测结果 |
| `timestamp` | datetime | 决策时间 |
| `client_ip` | string | 来源 IP（合规要求） |

> **保留期**：≥ 3 年（电力行业合规要求），归档至冷存储。

---

## 4. UI 设计草图

### 4.1 当前 HITL 弹窗改造点

```
当前 HitlDialog.vue (480px)                 改造后 EditDialog.vue (720px)
┌────────────────────────────┐               ┌──────────────────────────────────┐
│ ⚠️ 高危操作确认            │               │ ⚠️ 高危操作 · 待审核               │
├────────────────────────────┤               ├──────────────────────────────────┤
│ [告警条]                    │               │ [告警条] 风险等级: 高            │
│ 工具: dispatch_work_order   │               │                                  │
│ 说明: 需要人工确认后才能执行 │               │ 工具: dispatch_work_order        │
│ 会话: thread-123            │               │ 目标设备: TR-002 #2主变         │
│ [审批备注 textarea]         │               │                                  │
│ [拒绝] [批准]               │               │ ┌─ 内嵌编辑器 ─────────────────┐ │
└────────────────────────────┘               │ │ 描述: [textarea, 6行]         │ │
                                             │ │       字数 234/500            │ │
                                             │ │ 优先级: [high ▼]              │ │
                                             │ │ 备注: [textarea, 2行]         │ │
                                             │ └────────────────────────────────┘ │
                                             │                                  │
                                             │ [安全重检状态] ✅ 通过 (12:34:56) │
                                             │ [冲突检测状态] ⚠️ 同设备已有工单  │
                                             │                                  │
                                             │ 修改原因: [必填 textarea]        │
                                             │                                  │
                                             │  [拒绝]  [仅批准]  [修改后批准]   │
                                             └──────────────────────────────────┘
```

**关键改造点**：
1. **弹窗宽度** 480px → 720px（容纳编辑器）
2. **新增内嵌表单编辑器**：根据 `tool_name` 动态渲染字段
3. **三个按钮**（替换原两按钮）：拒绝 / 仅批准 / 修改后批准
4. **底部状态条**：实时显示"安全重检"、"冲突检测"两个异步校验状态
5. **修改原因必填**（Edit 模式专属）
6. **设备信息卡片**：在编辑器上方显示 `device_id` 对应的设备名/位置/当前状态（只读）

### 4.2 编辑器组件选型

| 候选方案 | 优点 | 缺点 | 选型 |
|---|---|---|---|
| Element Plus `<el-form>` + `<el-input>` | 轻量（~30KB）、已有依赖、易校验 | 无富文本、无 diff | ✅ **采用** |
| 引入 Monaco Editor | 支持 diff、语法高亮 | 过重（~2MB）、本场景用不到 | ❌ 不采用 |
| `<el-input type="textarea">` + `<el-select>` | 满足需求、校验便捷 | 需自己写字段联动 | ✅ 字段类型用此 |

**字段类型映射**：

| 工具参数 | UI 控件 | 校验方式 |
|---|---|---|
| `description` (string) | `<el-input type="textarea" :rows="6" :maxlength="500" show-word-limit>` | 前端 `maxlength` + 后端 Pydantic 校验 |
| `priority` (enum) | `<el-select>` | 前端枚举 + 后端 `Literal["high","medium","low"]` |
| `reason` (string) | `<el-input type="textarea" :rows="3" :maxlength="200" show-word-limit>` | 同上 |
| （未来）`steps` (list) | `<el-input>` 列表 + "+"按钮 | JSON Schema 校验 |

### 4.3 三按钮状态联动

| 用户操作 | 前端状态 | 提交给后端的 payload |
|---|---|---|
| 点"拒绝" | 不校验编辑内容 | `{decision: "rejected", reason: "..."}` |
| 点"仅批准" | 不校验编辑内容（编辑器只读或忽略） | `{decision: "approved", edited_args: null}` |
| 点"修改后批准" | 校验：必填、长度、枚举 → 安全重检 → 冲突检测 | `{decision: "approved_with_edit", edited_args: {...}, edit_reason: "..."}` |

**按钮可用性**：
- 任一异步校验（安全重检/冲突检测）失败时 → "修改后批准"按钮禁用 + tooltip 提示原因
- 编辑器有未保存修改但点"仅批准" → 弹窗确认"放弃修改？"

### 4.4 暗/亮双主题适配

- 复用 `ui-redesign-proposal.md` 中已定义的 CSS 变量（`--brand-primary`、`--status-danger`、`--glow-primary` 等）
- 新增变量：`--edit-field-bg`（编辑器背景）、`--edit-field-border-focus`（聚焦边框）
- 暗主题：编辑字段背景 `rgba(255,255,255,0.04)`；亮主题：`#fafafa`
- 校验失败边框：亮主题 `--status-danger`；暗主题 `#FF6B7A`（更高对比度）

---

## 5. 需求池（按优先级）

### P0：核心 Edit 模式（必须 30 天内交付）

| 需求 ID | 描述 | 验收要点 | 估算 |
|---|---|---|---|
| REQ-P0-1 | 后端扩展 `InterruptRequest` 接受 `edited_args` + `decision: "approved_with_edit"` | Pydantic 校验、API 文档更新 | 1 人日 |
| REQ-P0-2 | 后端 `Command(resume=...)` 接收编辑后 args 替换原 plan | 单元测试：编辑 args 生效 | 1 人日 |
| REQ-P0-3 | 前端 `HitlDialog.vue` 重构为 `EditDialog.vue` 动态渲染字段 | 支持 dispatch_work_order + suggest_shutdown 两种工具 | 2 人日 |
| REQ-P0-4 | 前端"修改后批准"按钮 + 字段校验（必填/长度/枚举） | Element Plus 校验规则 | 0.5 人日 |
| REQ-P0-5 | 前端 `chatStore.ts` 扩展 `approveWithEdit(threadId, editedArgs, editReason)` action | 与 SSE 流式流程兼容 | 0.5 人日 |
| REQ-P0-6 | 后端新增"安全重检"钩子（编辑后自动调用 `check_safety_compliance`） | 重检失败 → 拒绝继续 | 1 人日 |
| REQ-P0-7 | `hitl_audit_log` 表 schema + 写入逻辑 | 包含 §3.3 全部字段 | 1 人日 |
| REQ-P0-8 | 端到端测试：完整 Edit → 重检 → 批准 → 工具执行 | Playwright + 单元测试 | 1 人日 |

**P0 合计**：8 人日 ≈ 30 天（1-2 人并行）

### P1：完整校验规则 + 审计（30-60 天）

| 需求 ID | 描述 | 估算 |
|---|---|---|
| REQ-P1-1 | 冲突检测：同设备同优先级工单查重 | 2 人日 |
| REQ-P1-2 | 停机时间关键词联动（保电 → 强制降级） | 1 人日 |
| REQ-P1-3 | 审计页面：按 thread_id / 时间范围查询 | 3 人日 |
| REQ-P1-4 | Escalation 模式：选人组件 + 多级审批流 | 5 人日 |
| REQ-P1-5 | 审计数据保留 3 年 + 冷归档脚本 | 1 人日 |

### P2：增强（60+ 天）

| 需求 ID | 描述 |
|---|---|
| REQ-P2-1 | Edit 前后 diff 可视化（绿色新增、红色删除） |
| REQ-P2-2 | Edit 模板保存（常用修改保存为模板一键应用） |
| REQ-P2-3 | 移动端适配（响应式弹窗） |
| REQ-P2-4 | 多人协同 Edit（同一 HITL 多名调度员同时编辑冲突合并） |

---

## 6. 验收标准（10 条 Given/When/Then）

### AC-1：编辑字段生效
- **Given** Agent 建议派发 `TR-001` 设备的 high 优先级工单
- **When** 调度员将 `priority` 改为 `medium`、`description` 末尾追加"避开 22:00-24:00"
- **Then** 系统以编辑后参数执行 `dispatch_work_order`，且最终回复中显示"已按编辑后内容派单"

### AC-2：安全重检失败
- **Given** Agent 建议停运 `TR-001` 设备
- **When** 调度员将 `reason` 改为"测试性临时停运 5 分钟"
- **Then** 系统调用 `check_safety_compliance` 返回"安规 4.3.2 不允许带电设备短时频繁停运"
- **And** "修改后批准"按钮置灰，tooltip 提示"安全重检未通过"

### AC-3：必填校验
- **Given** 弹窗打开，`description` 默认有 Agent 建议内容
- **When** 调度员清空 `description`
- **Then** 字段红框 + 错误提示"故障描述不能为空"
- **And** "修改后批准"按钮禁用

### AC-4：长度超限
- **Given** 弹窗打开
- **When** 调度员在 `description` 粘贴 600 字文本
- **Then** 文本框只接收 500 字，字数计数器显示"500/500"红色
- **And** "修改后批准"按钮禁用

### AC-5：仅批准保留原行为
- **Given** Agent 建议派发 `TR-001` high 优先级工单
- **When** 调度员直接点"仅批准"按钮（不修改任何字段）
- **Then** 系统以 Agent 原参数执行
- **And** 审计日志 `decision="approved"`，`edited_args=null`

### AC-6：拒绝功能不变
- **Given** 弹窗打开
- **When** 调度员点"拒绝"按钮 + 填写原因"当前保电时段不允许操作"
- **Then** 系统不执行 `dispatch_work_order`
- **And** 图继续执行（用降级方案或返回用户最终回复）
- **And** 审计日志 `decision="rejected"`

### AC-7：冲突检测
- **Given** `TR-001` 已有 priority=high 的在途工单 `WO-20240115-001`
- **When** 调度员新建议 priority=high 派单
- **Then** 弹窗顶部红色横幅"⚠️ 该设备已有 priority=high 在途工单 WO-20240115-001"
- **And** "修改后批准"按钮禁用，建议"请合并或降低优先级"

### AC-8：审计记录完整
- **Given** 任何 HITL 事件（任意一种 decision）
- **When** 事件完成
- **Then** `hitl_audit_log` 表新增一条记录，包含 §3.3 全部字段
- **And** 审计页面可按 `thread_id` 或时间范围查询到

### AC-9：编辑原因必填
- **Given** 调度员修改了至少一个字段
- **When** 点"修改后批准"但"修改原因"为空
- **Then** 字段红框 + 错误提示"请填写修改原因"
- **And** 不提交

### AC-10：暗/亮主题适配
- **Given** 前端运行在暗主题模式
- **When** 打开 HITL Edit 弹窗
- **Then** 编辑器背景为深色、字段聚焦边框高对比度、按钮配色符合 `--glow-primary` 规范
- **And** 切换到亮主题后颜色自动切换，无残留

---

## 7. 待确认问题

| # | 问题 | 假设默认 | 需确认方 |
|---|---|---|---|
| Q1 | 编辑后的 `description` 是否需要走 `safety_agent` 重检？哪些操作必须重检？ | **假设**：所有 Edit 操作都自动重检一次 `check_safety_compliance` | @安全负责人（李主任） |
| Q2 | "修改后批准"是否需要二次确认弹窗（避免误操作）？ | **假设**：不需要，校验已足够 | @产品 + @UX |
| Q3 | 审计日志保留期是 3 年还是更长？电力行业规程是否要求更久？ | **假设**：3 年 | @法务/合规 |
| Q4 | Edit 模式是否支持"撤销修改"（回到 Agent 原始值）？ | **假设**：支持，"仅批准"按钮即视为撤销 | @UX |
| Q5 | Escalation 模式的审批人列表从哪里来？是否需要对接现有 OA 系统？ | **假设**：MVP 阶段前端硬编码列表，P1 后期对接 OA | @架构师 |
| Q6 | 当用户网络中断导致"修改后批准"请求超时，已编辑内容是否本地暂存？ | **假设**：暂存到 `localStorage`，恢复后弹出"恢复未提交编辑"提示 | @前端 + @产品 |

---

## 8. 非目标（Out of Scope）

为避免范围蔓延，本期明确**不做**的事项：

- ❌ AI 智能建议"应该如何修改"（属于 LLM 能力增强，不是 HITL 范畴）
- ❌ 富文本/图片/附件编辑（电力工单为纯文本结构化字段）
- ❌ 多人协同实时编辑（Google Docs 式 OT 算法，P2 再说）
- ❌ 移动端原生 App（响应式 Web 足够）
- ❌ 离线模式（电网调度室网络通常稳定）
- ❌ 与第三方工单系统（SAP PM、Maximo）双向同步（属集成工作）

---

## 9. 上线计划

| 阶段 | 时间 | 内容 |
|---|---|---|
| **内测** | D+15 | P0 全部需求 + 单测完成，内部 mock 数据验证 |
| **灰度** | D+25 | 接入真实 MCP 工具，邀请 3-5 名真实调度员试用 |
| **全量** | D+30 | 全量上线 + 审计日志告警（异常决策实时通知值班主管） |
| **复盘** | D+45 | 收集核心指标数据，决定 P1 排期 |

---

## 10. 附录：与现有架构的契合点

| 现有能力 | 复用方式 |
|---|---|
| LangGraph `MemorySaver` checkpointer | 已在 `graph.py` 第 99 行使用，Edit 模式无需改动 |
| `Command(resume=...)` 注入机制 | `graph.py` 第 347 行 `resume()` 方法扩展为接收 `edited_args` |
| `pending_tool_plan` 持久化 | `agent_factory.py` 第 178-190 行已实现，Edit 模式复用 |
| `safety_agent.check_safety_compliance` MCP 工具 | `mcp_tools/server.py` 第 92-96 行，Edit 后重检直接调用 |
| Element Plus + Pinia + TypeScript 前端栈 | 与 `web/src/components/HitlDialog.vue` 一致 |

**改造面**：仅前端 `HitlDialog.vue` → `EditDialog.vue`、后端 `InterruptRequest` schema 与 `resume()` 方法、审计日志表。LangGraph 图本身无需重写。
