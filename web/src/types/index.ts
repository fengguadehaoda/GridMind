/**
 * GridMind 类型入口 · 统一导出
 * 业务类型在下方，主题/Logo/组件类型在 ./theme
 *
 * v1.5.0 增量：
 *   - DisplayMode / ColorBlindPalette / Status / BackgroundIntensity 'off'
 *     → 已在 ./theme 中定义，由下方 `export * from './theme'` 自动转发
 *   - OnboardingScenario / OnboardingScenarioId / ONBOARDING_SCENARIOS
 *     → 同样在 ./theme 中定义（架构 §3.2），无需在 index.ts 重复声明
 *
 * v1.5.1 T01 增量：
 *   - DiagnosisReasoningStep：原 v1.5.0 `ReasoningStep`（diagnosis 推理链形态）
 *     重命名，避免与新的 LangGraph step 类型 `ReasoningStep` 命名冲突
 *   - HitlTask / RiskLevel / PendingHitlCountResponse / 7 个 API DTO → 全部内联在本文件
 *   - SseEvent.type 字面量联合新增 11 个事件 type（heartbeat / step_* / reasoning_*
 *     / step_replaced / hitl_*）
 *
 * 作者：寇豆码（T01 工程师）
 * 参考：frontend-v151-architecture-2026-08-04.md §3.5/§3.6/§3.7/§3.8
 */
export * from './theme'

/* v1.6.0 P1 增量：CommandGroup / HelpArticleMeta / SearchHit / SessionViewStatus /
 * SessionStepView / SessionCheckpointView / SessionStats / GrayscaleNodeType /
 * GrayscaleMode / GrayscaleGraph / GrayscalePlan 等全部定义在 ./theme，
 * 由上方 `export * from './theme'` 自动转发，无需重复声明（架构 §3.2）。 */

/* ═══════════════════════════════════════════════════════════════
 * v1.5.1 T01 基础设施 · 公共类型（F1-F4 + SSE）
 * 内联自原独立文件 src/types/reasoning.ts（为控制 T01 文件改动数 ≤ 13 合并）
 * ═══════════════════════════════════════════════════════════════ */

/** F1 推理状态机 */
export type ReasoningStatus =
  | 'idle'         // 未开始
  | 'running'      // 正在生成
  | 'paused'       // 调度员主动暂停
  | 'editing'      // 正在编辑某 step
  | 'resuming'     // 暂停后恢复中
  | 'completed'    // 正常完成
  | 'error'        // 系统错误
  | 'aborted'      // 调度员主动中止

/** F2 step 状态 */
export type StepStatus = 'pending' | 'running' | 'completed' | 'failed' | 'edited'

/** F2 step 角色（决定可编辑性） */
export type StepRole = 'user' | 'assistant' | 'system' | 'tool'

/**
 * LangGraph 单个推理步骤（v1.5.1 新增）。
 *
 * snake_case 字段与后端 Pydantic 对齐（API 客户端直接使用）；camelCase
 * 是 Pinia store 内部语义化命名。
 */
export interface ReasoningStep {
  /** 客户端稳定 id（crypto.randomUUID 生成） */
  id: string
  /** 步骤序号（从 0 开始） */
  index: number
  /** 节点标识（如 'safety_check' / 'risk_assess' / 'dispatch_plan'） */
  nodeName: string
  /** 节点对外显示名（中文） */
  name: string
  /** 节点说明 */
  description: string
  /** 用户可编辑的 prompt 片段 */
  promptFragment: string
  /** 编辑中的草稿（仅前端缓存） */
  draftPromptFragment: string | null
  /** 后端 content 的 SHA-256 hash */
  contentHash: string | null
  /** step 状态 */
  status: StepStatus
  /** step 角色 */
  role: StepRole
  /** 后端字段：started_at ISO 字符串 */
  startedAt: string
  /** 后端字段：finished_at ISO 字符串 */
  finishedAt: string | null
  /** 后端字段：本步耗时（ms） */
  durationMs: number | null
  /** 步骤输出 */
  output: Record<string, unknown> | string | null
  /** 业务规则：user content 可编辑 */
  isEditable: boolean
}

/** F3 + F4 HITL 任务 */
export type HitlTaskStatus = 'pending' | 'approved' | 'rejected' | 'approved-with-edit'

export type RiskLevel = 'low' | 'normal' | 'high' | 'critical'

export interface HitlTask {
  id: string
  sessionId: string
  stepId: string | null
  createdAt: string
  promptContext: string
  aiSuggestion: string
  confidence: number
  riskLevel: RiskLevel
  status: HitlTaskStatus
}

/* F1/F2/F3 REST DTO（chat.ts 7 个新方法）*/

export interface PauseSessionResponse {
  pausedAt: string
  pausedStep: number
  pausedNode: string
}

export interface ResumeSessionResponse {
  resumedAt: string
  currentNode: string
}

export interface RewindSessionRequest {
  step_index: number
  edited_content: { prompt_fragment: string } | null
}

export interface RewindSessionResponse {
  rewoundTo: { step_index: number; checkpoint_id: string; timestamp: string }
  new_steps: ReasoningStep[]
}

export interface AbortSessionRequest {
  reason?: string
}

export interface AbortSessionResponse {
  abortedAt: string
}

export interface PendingHitlCountResponse {
  count: number
}

export interface AuditDecisionsResponse {
  count: number
  entries: AuditEntry[]
  retention_years?: number
  thread_id?: string
}

export interface HitlAuditDecisionPayload {
  decision: 'approve' | 'reject' | 'edit_approve'
  reason?: string
  edited_args?: Record<string, unknown>
  edit_reason?: string
}

/* SSE Event type 字面量联合（v1.5.1 扩展） */

export type SseEventType =
  | 'token'
  | 'done'
  | 'error'
  | 'heartbeat'
  | 'step_started'
  | 'step_completed'
  | 'step_failed'
  | 'reasoning_paused'
  | 'reasoning_resumed'
  | 'reasoning_completed'
  | 'reasoning_error'
  | 'step_replaced'
  | 'hitl_interrupt'
  | 'hitl_resolved'

export type PauseReason = 'user_manual' | 'system_overload' | 'hitl_required' | 'checkpoint_full'
export type AbortReason = 'user_manual' | 'safety_violation' | 'timeout' | 'checkpoint_expired'

/* ═══════════════════════════════════════════════════════════════
 * 业务类型主体（v1.5.0 + v1.5.1 共存）
 * ═══════════════════════════════════════════════════════════════ */

/** 消息角色 */
export type MessageRole = 'user' | 'assistant' | 'system' | 'tool'

/** 健康等级 */
export type HealthLevel = 'normal' | 'warning' | 'critical'

/** 异常严重程度 */
export type AnomalySeverity = 'low' | 'medium' | 'high'

/** LLM 模型（v1.4.0 多模型） */
export interface ModelInfo {
  id: string
  provider: string
  label: string
  description: string
}

/** /models 端点响应 */
export interface ModelsResponse {
  available: ModelInfo[]
  current: string
  default: string
}

/** /models/switch 端点响应 */
export interface ModelSwitchResponse {
  ok: boolean
  current: string
}

/** HITL 审计决策类型 */
export type HitlAuditDecision = 'approved' | 'rejected' | 'edited'

/** HITL 审计条目 */
export interface AuditEntry {
  id?: number
  thread_id: string
  decision: HitlAuditDecision | string
  actor?: string
  reason?: string
  edited_content?: string
  created_at?: string
  timestamp?: number
  risk_level?: string
  tool_name?: string
}

/** /audit/hitl 端点响应 */
export interface AuditResponse {
  count: number
  entries: AuditEntry[]
  retention_years?: number
  thread_id?: string
}

/** 中断操作 */
export type InterruptAction = 'pending' | 'approved' | 'rejected' | 'edit_approved'

/** HITL Edit & Continue 决策枚举（HTTP 请求体专用） */
export type EditDecision = 'approve' | 'reject' | 'edit_approve'

/** 可编辑字段定义（前端镜像：与 api/services/hitl_editable_schemas.py 同步） */
export type EditableFieldType = 'text' | 'textarea' | 'select' | 'number'

export interface EditableField {
  key: string
  type: EditableFieldType
  label: string
  required?: boolean
  max_length?: number | null
  options?: string[] | null
  placeholder?: string
  help_text?: string
}

/** 中断决策请求体（POST /interrupt/{tid}/decision） */
export interface InterruptDecisionRequest {
  decision: EditDecision
  reason?: string
  edited_args?: Record<string, unknown>
  edit_reason?: string
}

/** 聊天消息 */
export interface Message {
  role: MessageRole
  content: string
  name?: string | null
  tool_calls?: Record<string, unknown>[] | null
  tool_call_id?: string | null
  timestamp: string
}

/** 聊天请求 */
export interface ChatRequest {
  message: string
  thread_id?: string | null
  stream?: boolean
}

/** 聊天响应 */
export interface ChatResponse {
  thread_id: string
  response: string
  agent_name?: string | null
  interrupt_required: boolean
  interrupt_node?: string | null
  interrupt_msg?: string | null
  // Bug2 修复：演示模式剧本外响应标记（前端据此清审批态 + 展示提示）
  is_demo_out_of_scope?: boolean
}

/** SSE 事件数据
 *
 * v1.5.0 原有 3 个 type：'token' | 'done' | 'error'
 * v1.5.1 T01 扩展 11 个 type（详见 SseEventType 字面量联合）：
 *   - 'heartbeat' / 'step_started' / 'step_completed' / 'step_failed'（F1 步骤进度）
 *   - 'reasoning_paused' / 'reasoning_resumed' / 'reasoning_completed' /
 *     'reasoning_error' / 'step_replaced'（F1 状态机 + F2 rewind）
 *   - 'hitl_interrupt' / 'hitl_resolved'（F3/F4 HITL 队列推送）
 *
 * 字段语义：
 *   - thread_id：旧 v1.5.0 命名；保留兼容
 *   - session_id：v1.5.1 LangGraph session 主键
 *   - step_id / step_index / new_steps：F1/F2 步骤标识
 *   - task_id / ai_suggestion / confidence / risk_level：F3/F4 HITL 字段
 *   - paused_at / resumed_at / error / decision / resolved_at：F1-F4 状态
 */
export interface SseEvent {
  type: SseEventType
  content?: string
  thread_id?: string
  interrupt_required?: boolean
  interrupt_node?: string | null
  interrupt_msg?: string | null
  // Bug2 修复：演示模式剧本外响应标记（done 事件携带，前端据此清审批态）
  is_demo_out_of_scope?: boolean
  // v1.5.1 F1-F4 新增字段
  session_id?: string
  step_id?: string
  step_index?: number
  // F1 修复（QA F1 P1）：step_started 事件字段（后端 /sessions/{id}/events 序列化时
  // 将 payload 与 type/thread_id/timestamp 平铺；此处声明可选字段供 ChatView appendStep 使用）
  step_name?: string
  step_description?: string
  step_role?: string
  prompt_fragment?: string
  is_editable?: boolean
  started_at?: string
  // F2 修复（QA F2 P1）：hitl_interrupt / hitl_resolved 事件字段
  // （后端 sse_event_emitter.emit_hitl_interrupt → {tool, args}；emit_hitl_resolved → {decision, resolved_at}）
  tool?: string | null
  args?: Record<string, unknown> | null
  task_id?: string
  ai_suggestion?: string
  confidence?: number
  risk_level?: RiskLevel
  error?: string
  paused_at?: string
  resumed_at?: string
  new_steps?: ReasoningStep[]
  decision?: 'approved' | 'rejected' | 'edit_approved'
  resolved_at?: string
}

/** 设备信息 */
export interface DeviceInfo {
  device_id: string
  device_name: string
  device_type: string
  location: string
  status: string
}

/** 异常项 */
export interface AnomalyItem {
  device_id: string
  metric: string
  value: number
  z_score: number
  severity: AnomalySeverity
  description: string
  detected_at: string
}

/** 健康评分结果 */
export interface HealthScoreResult {
  device_id: string
  device_name: string
  health_score: number
  health_level: HealthLevel
  anomalies: AnomalyItem[]
  summary: string
}

/** 遥测读数 */
export interface TelemetryReading {
  timestamp: string
  temperature?: number
  voltage?: number
  current_load?: number
  humidity?: number
  pressure?: number
}

/** 设备健康概要（/devices 列表中的 health 字段，不含异常明细） */
export interface HealthSummary {
  device_id: string
  device_name: string
  health_score: number
  health_level: HealthLevel
  anomaly_count: number
  summary: string
}

/** 设备总览（/devices 列表项） */
export interface DeviceOverview extends DeviceInfo {
  latest_telemetry: TelemetryReading
  health: HealthSummary
}

/** 设备详情（/devices/{id}） */
export interface DeviceDetail extends DeviceInfo {
  install_date?: string
}

/** 巡检记录 */
export interface InspectionRecord {
  inspection_id: string
  inspector: string
  inspect_time: string
  result: string
  notes?: string
}

/** 监控响应类型 */
export interface DevicesResponse {
  devices: DeviceOverview[]
}

export interface DeviceDetailResponse {
  device: DeviceDetail
  health: HealthSummary
  anomalies: AnomalyItem[]
  latest_telemetry: TelemetryReading
  inspections: InspectionRecord[]
}

export interface TelemetryResponse {
  device_id: string
  telemetry: TelemetryReading[]
}

export interface HealthScoresResponse {
  scores: HealthScoreResult[]
}

export interface HealthCriticalResponse {
  critical: HealthSummary[]
}

/** 图谱实体 */
export interface GraphEntity {
  id: string
  name: string
  type: string
  properties: Record<string, unknown>
}

/** 知识库回答 */
export interface KnowledgeAnswer {
  answer: string
  citations: string[]
  graph_paths: string[][]
  confidence: number
  refuse: boolean
  refuse_reason?: string | null
}

/** 线程信息 */
export interface ThreadInfo {
  thread_id: string
  messages: Message[]
}

/** 中断审批请求 */
export interface InterruptRequest {
  reason: string
}

/** 中断决策响应（统一 /decision 端点） */
export interface InterruptDecisionResponse {
  thread_id: string
  response: string
  interrupt_required?: boolean
  decision?: 'approve' | 'reject' | 'edit_approve'
  rejected_by_safety?: boolean
  safety_summary?: string
}

/** 聊天消息（UI 增强） */
export interface ChatMessage extends Message {
  id: string
  loading?: boolean
  healthScores?: HealthScoreResult[]
  knowledgeAnswer?: KnowledgeAnswer | null
  metadata?: {
    agent_name?: string | null
    thread_id?: string | null
    has_reasoning_chain?: boolean
    // Bug2 修复：演示模式剧本外响应标记（MessageBubble 据此展示提示样式）
    is_demo_out_of_scope?: boolean
  } | null
}

/** 演示快捷指令 */
export interface DemoShortcut {
  label: string
  icon: string
  message: string
  description: string
}

// ═══════════════════════════════════════════════════════
// P0 可解释性 AI · 三层推理链（与 core/schemas/diagnosis.py 同步）
// ═══════════════════════════════════════════════════════

/** LLM 围栏证据引用 */
export interface EvidenceRef {
  type: 'telemetry' | 'rule' | 'history' | 'anomaly' | 'knowledge'
  id: string
  summary: string
}

/** LLM 结构化诊断输出 */
export interface DiagnosisOutput {
  fault_type: string
  fault_location: string
  confidence: number
  evidence_refs: EvidenceRef[]
  reasoning_text: string
  severity: 'info' | 'warning' | 'critical'
  requires_human_review: boolean
  suggested_action: 'dispatch' | 'shutdown' | 'monitor' | 'none'
}

/** 单条机理校验结果 */
export interface MechanicalCheckItem {
  rule_id: string
  rule_name: string
  passed: boolean
  severity: 'info' | 'warning' | 'critical' | null
  observed_value: number | string | null
  threshold: number | string | null
  evidence: Record<string, unknown>
  explanation: string
}

/** 机理校验汇总 */
export interface MechanicalCheckResult {
  device_id: string
  checks: MechanicalCheckItem[]
  overall_pass: boolean
  critical_failures: number
  contradicted_with_llm: boolean
}

/** 单条被触发的规则 */
export interface TriggeredRule {
  rule_id: string
  source: 'safety_regulation' | 'operation_procedure' | 'emergency_threshold'
  code: string | null
  title: string
  matched_keywords: string[]
  action: 'warn' | 'hitl_required' | 'force_shutdown' | 'escalate_supervisor'
  severity: 'info' | 'warning' | 'critical'
  description: string
  trigger_source: 'user_input' | 'llm_output' | 'mechanical_check' | 'fusion'
}

/** 规则护栏汇总 */
export interface RulesGuardResult {
  triggered: TriggeredRule[]
  forced_hitl: boolean
  forced_shutdown: boolean
}

/** 推理链中单步（v1.5.0 diagnosis 推理链形态）
 *
 * v1.5.1 T01 重命名：原 `ReasoningStep` → `DiagnosisReasoningStep`，
 * 原因是 v1.5.1 引入了 LangGraph 步骤类型也叫 `ReasoningStep`，
 * 与本类型语义冲突（LLM/mechanical/rules/fusion 四层 vs LangGraph
 * step）。保留 `@deprecated ReasoningStep` 别名一个季度。
 */
export interface DiagnosisReasoningStep {
  layer: 'llm' | 'mechanical' | 'rules' | 'fusion'
  step_name: string
  outcome: string
  evidence: Record<string, unknown> | string
  elapsed_ms: number
}

/**
 * 三层融合结果
 *
 * v1.5.1 T01 注意：原 `ReasoningStep[]` 字段（v1.5.0 是 diagnosis 推理链形态
 * layer/step_name/outcome/evidence/elapsed_ms 类型）已重命名为
 * `DiagnosisReasoningStep[]`，因为 v1.5.1 新增的 LangGraph step 类型也叫
 * `ReasoningStep`，为避免冲突而将前者重命名。
 */
export interface DiagnosisFusionResult {
  llm_output: DiagnosisOutput
  mechanical_check: MechanicalCheckResult
  rules_guard: RulesGuardResult
  final_severity: 'info' | 'warning' | 'critical'
  final_diagnosis: string
  requires_human_review: boolean
  forced_action: 'none' | 'dispatch' | 'shutdown'
  reasoning_chain: DiagnosisReasoningStep[]
  conflict_detected: boolean
  thread_id: string | null
}
