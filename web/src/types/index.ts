/**
 * GridMind 类型入口 · 统一导出
 * 业务类型在下方，主题/Logo/组件类型在 ./theme
 */
export * from './theme'

/** 消息角色 */
export type MessageRole = 'user' | 'assistant' | 'system' | 'tool'

/** 健康等级 */
export type HealthLevel = 'normal' | 'warning' | 'critical'

/** 异常严重程度 */
export type AnomalySeverity = 'low' | 'medium' | 'high'

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
}

/** SSE 事件数据 */
export interface SseEvent {
  type: 'token' | 'done' | 'error'
  content?: string
  thread_id?: string
  interrupt_required?: boolean
  interrupt_node?: string | null
  interrupt_msg?: string | null
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

/** 推理链中单步 */
export interface ReasoningStep {
  layer: 'llm' | 'mechanical' | 'rules' | 'fusion'
  step_name: string
  outcome: string
  evidence: Record<string, unknown> | string
  elapsed_ms: number
}

/** 三层融合结果 */
export interface DiagnosisFusionResult {
  llm_output: DiagnosisOutput
  mechanical_check: MechanicalCheckResult
  rules_guard: RulesGuardResult
  final_severity: 'info' | 'warning' | 'critical'
  final_diagnosis: string
  requires_human_review: boolean
  forced_action: 'none' | 'dispatch' | 'shutdown'
  reasoning_chain: ReasoningStep[]
  conflict_detected: boolean
  thread_id: string | null
}
