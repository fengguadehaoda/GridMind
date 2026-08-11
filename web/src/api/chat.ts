import type {
  ChatResponse,
  SseEvent,
  InterruptDecisionRequest,
  InterruptDecisionResponse,
  DiagnosisFusionResult,
  AuditEntry,
  AuditResponse,
} from '../types'
import type {
  AbortSessionRequest,
  AbortSessionResponse,
  HitlAuditDecisionPayload,
  PauseSessionResponse,
  PendingHitlCountResponse,
  ResumeSessionResponse,
  RewindSessionRequest,
  RewindSessionResponse,
} from '../types'
import { getAuthHeaders, getJwtToken } from '../composables/useJwtAuth'
import { useDisplayStore } from '../stores/display'
// V1.8.0 认证（T04）：复用共享 httpClient（401 自动 refresh 重放 + Bearer 注入）。
// resolveBaseUrl 由 httpClient 提供并在此转发，保持既有 importers 零改动。
import httpClient, { resolveBaseUrl } from './httpClient'

export { resolveBaseUrl }

/** 读取当前显示模式（standard | presentation）作为 X-Display-Mode header 值。
 *
 * Bug1 修复：演示/标准切换此前只改背景动效，未传给后端；这里在每次请求时
 * 从 display store 取当前模式。无 Pinia 环境（单测 / Node）回退 localStorage
 * （store 每次切换都会持久化），再兜底 'standard'。
 */
export function getDisplayModeHeader(): string {
  try {
    const store = useDisplayStore()
    return store.displayMode
  } catch {
    try {
      const m = localStorage.getItem('gridmind.displayMode')
      return m === 'presentation' ? 'presentation' : 'standard'
    } catch {
      return 'standard'
    }
  }
}

const BASE = resolveBaseUrl()

/** POST /chat — 发送消息（阻塞模式） */
export async function sendMessage(message: string, threadId?: string): Promise<ChatResponse> {
  const { data } = await httpClient.post<ChatResponse>(
    '/chat',
    {
      message,
      thread_id: threadId || null,
      stream: false,
    },
    // Bug1 修复：带上显示模式 header，后端据此决定 mock/真实 LLM 路径
    { headers: { 'X-Display-Mode': getDisplayModeHeader() } },
  )
  return data
}

/** GET /chat/stream/{thread_id}?message=... — SSE 流式对话
 *  返回一个 AbortController，外部可调用 abort() 取消 */
export function streamChat(
  threadId: string,
  message: string,
  onEvent: (event: SseEvent) => void,
  onError: (err: string) => void,
  onDone: () => void,
): AbortController {
  const controller = new AbortController()

  const url = `${BASE}/chat/stream/${encodeURIComponent(threadId)}?message=${encodeURIComponent(message)}`

  fetch(url, {
    signal: controller.signal,
    // Bug1 修复：带上显示模式 header，后端据此决定 mock/真实 LLM 路径
    // 生产模式鉴权修复：携带 JWT，避免匿名请求被 401 拦截（与后端 verify_jwt_if_prod 对齐）
    headers: {
      'X-Display-Mode': getDisplayModeHeader(),
      ...getAuthHeaders(),
    },
  })
    .then(async (response) => {
      if (!response.ok) {
        onError(`HTTP ${response.status}: ${response.statusText}`)
        return
      }

      const reader = response.body?.getReader()
      if (!reader) {
        onError('Response body is not readable')
        return
      }

      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || '' // keep incomplete line

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const payload = line.slice(6).trim()
            if (payload === '[DONE]') {
              onDone()
              return
            }
            try {
              const event = JSON.parse(payload) as SseEvent
              onEvent(event)
              if (event.type === 'done') {
                onDone()
                return
              }
            } catch {
              // skip malformed JSON
            }
          }
        }
      }
      onDone()
    })
    .catch((err) => {
      if (err.name !== 'AbortError') {
        onError(String(err))
      }
    })

  return controller
}

/** POST /interrupt/{thread_id}/approve — 批准 HITL（老端点，向后兼容 1 季度） */
export async function approveInterrupt(threadId: string, reason = ''): Promise<ChatResponse> {
  const { data } = await httpClient.post<ChatResponse>(
    `/interrupt/${encodeURIComponent(threadId)}/approve`,
    { reason },
    { headers: getAuthHeaders() },
  )
  return data
}

/** POST /interrupt/{thread_id}/reject — 拒绝 HITL（老端点，向后兼容 1 季度） */
export async function rejectInterrupt(threadId: string, reason = ''): Promise<ChatResponse> {
  const { data } = await httpClient.post<ChatResponse>(
    `/interrupt/${encodeURIComponent(threadId)}/reject`,
    { reason },
    { headers: getAuthHeaders() },
  )
  return data
}

/** POST /interrupt/{thread_id}/decision — 统一 HITL 决策端点（P0：Edit & Continue）
 *
 * 支持三种决策：
 * - approve       仅批准（edited_args 必为 null）
 * - reject        拒绝（终止执行）
 * - edit_approve  修改后批准（edited_args 必填；走 safety 重检 + 审计 + resume）
 *
 * 服务端 safety 重检失败时：返回 200 + `{rejected_by_safety: true}`，由前端展示红色横幅。
 */
export async function decideInterrupt(
  threadId: string,
  payload: InterruptDecisionRequest,
): Promise<InterruptDecisionResponse> {
  const { data } = await httpClient.post<InterruptDecisionResponse>(
    `/interrupt/${encodeURIComponent(threadId)}/decision`,
    payload,
    { headers: getAuthHeaders() },
  )
  return data
}

/** GET /thread/{thread_id} — 查询线程历史 */
export async function getThread(threadId: string) {
  const { data } = await httpClient.get(`/thread/${encodeURIComponent(threadId)}`)
  return data
}

/** GET / — 健康检查 */
export async function healthCheck() {
  const { data } = await httpClient.get('/')
  return data
}

/** GET /audit/hitl/{thread_id} — 查询 HITL 审计日志 */
export async function getHitlAudit(threadId: string) {
  const { data } = await httpClient.get(`/audit/hitl/${encodeURIComponent(threadId)}`)
  return data as { thread_id: string; count: number; entries: Array<Record<string, unknown>> }
}

/** GET /diagnosis/{thread_id}/reasoning — 拉取诊断完整推理链（P0 可解释性 AI） */
export async function getDiagnosisReasoning(threadId: string): Promise<DiagnosisFusionResult> {
  const { data } = await httpClient.get<DiagnosisFusionResult>(`/diagnosis/${encodeURIComponent(threadId)}/reasoning`)
  return data
}

/* ═══════════════════════════════════════════════════════════════
 * v1.5.1 T01 基础设施 · 7 个新方法（F1/F2/F3）
 *
 * 主理人决策 7.1（A 方案）：JWT 通过 VITE_DEV_JWT_TOKEN 环境变量注入；
 * 每条 REST 请求都通过 getAuthHeaders() 自动带上 `Authorization: Bearer <jwt>`。
 *
 * 顺序：
 *   - F1 暂停/恢复/中止：pauseSession / resumeSession / abortSession
 *   - F2 重跑：rewindSession + getSessionCheckpoints
 *   - F3 待审计数：fetchPendingHitlCount
 *   - F4 HITL 三按钮：hitlApprove / hitlReject / hitlApproveWithEdit
 *   - SSE：subscribeSessionEvents（fetch + ReadableStream，JWT header 注入）
 * ═══════════════════════════════════════════════════════════════ */

/**
 * F1 · POST /sessions/{id}/pause —— 暂停推理（注入 __pause__ 软信号）
 *
 * @param threadId - LangGraph thread_id（与 SSE event.session_id 对齐）
 * @param reason - 暂停原因（user_manual / system_overload / hitl_required /
 *                 checkpoint_full；默认 'user_manual'）
 * @returns 暂停响应（含 pausedAt / pausedStep / pausedNode）
 */
export async function pauseSession(
  threadId: string,
  reason: string = 'user_manual',
): Promise<PauseSessionResponse> {
  const { data } = await httpClient.post<PauseSessionResponse>(
    `/sessions/${encodeURIComponent(threadId)}/pause`,
    { reason },
    { headers: getAuthHeaders() },
  )
  return data
}

/**
 * F1 · POST /sessions/{id}/resume —— 恢复推理（清除 __pause__ 软信号）
 *
 * body.action = 'continue_from_pause' 让后端语义化为"从暂停点续跑"，
 * 区别于首次 start（action = 'start'）。
 */
export async function resumeSession(threadId: string): Promise<ResumeSessionResponse> {
  const { data } = await httpClient.post<ResumeSessionResponse>(
    `/sessions/${encodeURIComponent(threadId)}/resume`,
    { action: 'continue_from_pause' },
    { headers: getAuthHeaders() },
  )
  return data
}

/**
 * F2 · POST /sessions/{id}/rewind —— 从某 step 重跑（含 edited_content）
 *
 * @param body.step_index - 重跑起点 step 序号（从 0 开始）
 * @param body.edited_content - 用户编辑后的 prompt 片段（null = 用原内容）
 */
export async function rewindSession(
  threadId: string,
  body: RewindSessionRequest,
): Promise<RewindSessionResponse> {
  const { data } = await httpClient.post<RewindSessionResponse>(
    `/sessions/${encodeURIComponent(threadId)}/rewind`,
    body,
    { headers: getAuthHeaders() },
  )
  return data
}

/**
 * F1 · POST /sessions/{id}/abort —— 强制中止（不可恢复）
 *
 * @param body.reason - 中止原因
 */
export async function abortSession(
  threadId: string,
  body: AbortSessionRequest = {},
): Promise<AbortSessionResponse> {
  const { data } = await httpClient.post<AbortSessionResponse>(
    `/sessions/${encodeURIComponent(threadId)}/abort`,
    body,
    { headers: getAuthHeaders() },
  )
  return data
}

/**
 * GET /sessions/{id}/checkpoints —— 列出所有 step checkpoint（F2 编辑前读取）
 *
 * 注意：
 * - v1.5.0 已有同名端点（dialog hitlSchemas），本方法是 v1.5.1 新增的
 *   session 级路径。两者并存。
 * - QA R1 P1-3：后端当前**未实现**该 session 级端点（PRD 声称就绪但不存在），
 *   前端 sessionStats.fetchCheckpoints 已改为本地派生（reasoning.steps），
 *   本方法**保留**供向后兼容 / 未来后端补齐后启用（T3.1 测试亦依赖其存在）。
 */
export async function getSessionCheckpoints(
  threadId: string,
): Promise<{
  steps: Array<{
    step_index: number
    step_id: string
    name: string
    description: string
    prompt_fragment: string
    is_editable: boolean
    checkpoint_id: string
    created_at: string
  }>
}> {
  const { data } = await httpClient.get(`/sessions/${encodeURIComponent(threadId)}/checkpoints`, {
    headers: getAuthHeaders(),
  })
  return data
}

/**
 * F3 · GET /audit/pending-count —— 当前待审 HITL 任务数
 *
 * 与 audit store 内部 fetchPendingHitlCount（直接用 fetch）功能重复；
 * 暴露 axios 版本便于其他模块直接调用（独立于 audit store）。
 */
export async function fetchPendingHitlCount(): Promise<PendingHitlCountResponse> {
  const { data } = await httpClient.get<PendingHitlCountResponse>(`/audit/pending-count`, {
    headers: getAuthHeaders(),
  })
  return data
}

/**
 * F3 · GET /audit/hitl?decision=&limit=&risk_level= —— 审计历史分页
 *
 * @param decision - 'approved' | 'rejected' | 'edited'；不传 = 全部
 * @param limit - 默认 50；上限 200（服务端会 clamp）
 * @param riskLevel - 'low' | 'normal' | 'high' | 'critical'（v1.5.1 新增可选过滤）
 */
export async function fetchAuditDecisions(
  decision?: 'approved' | 'rejected' | 'edited',
  limit: number = 50,
  riskLevel?: 'low' | 'normal' | 'high' | 'critical',
): Promise<AuditResponse> {
  const params: Record<string, string | number> = { limit }
  if (decision) params.decision = decision
  if (riskLevel) params.risk_level = riskLevel
  const { data } = await httpClient.get<AuditResponse>(`/audit/hitl`, {
    params,
    headers: getAuthHeaders(),
  })
  return data
}

/**
 * F4 · POST /hitl/{taskId}/approve —— 仅批准 HITL（v1.5.0 兼容端点）
 *
 * AuditResponse 中不一定需要 body，这里传空 {} 保持 REST 约定。
 */
export async function hitlApprove(taskId: string): Promise<AuditResponse> {
  const { data } = await httpClient.post<AuditResponse>(
    `/hitl/${encodeURIComponent(taskId)}/approve`,
    {},
    { headers: getAuthHeaders() },
  )
  return data
}

/**
 * F4 · POST /hitl/{taskId}/reject —— 拒绝 HITL
 *
 * @param payload.reason - 拒绝原因（可选）
 */
export async function hitlReject(
  taskId: string,
  payload: Pick<HitlAuditDecisionPayload, 'reason'>,
): Promise<AuditResponse> {
  const { data } = await httpClient.post<AuditResponse>(
    `/hitl/${encodeURIComponent(taskId)}/reject`,
    payload,
    { headers: getAuthHeaders() },
  )
  return data
}

/**
 * F4 · POST /hitl/{taskId}/approve-with-edit —— 修改后批准 HITL
 *
 * @param payload.edited_content - 修改后的内容（必填）
 * @param payload.edit_reason - 修改原因（可选）
 */
export async function hitlApproveWithEdit(
  taskId: string,
  payload: Pick<HitlAuditDecisionPayload, 'edited_args'> & {
    edited_content?: string
    edit_reason?: string
  },
): Promise<AuditResponse> {
  // 后端 schema 期望 { edited_args }；同时兼容前端 audit store 传的
  // { edited_content } 形态（向后兼容 v1.5.0 demo）
  const body: Record<string, unknown> = {}
  if (payload.edited_args) {
    body.edited_args = payload.edited_args
  }
  if (payload.edited_content !== undefined) {
    body.edited_content = payload.edited_content
  }
  if (payload.edit_reason !== undefined) {
    body.edit_reason = payload.edit_reason
  }
  const { data } = await httpClient.post<AuditResponse>(
    `/hitl/${encodeURIComponent(taskId)}/approve-with-edit`,
    body,
    { headers: getAuthHeaders() },
  )
  return data
}

/**
 * SSE · GET /sessions/{id}/events —— 订阅 session 级 SSE 事件流（含 JWT）
 *
 * ⚠️ F6 修复（QA F6 P1）：**已废弃（deprecated）** —— 全代码库零调用方；
 * 实际订阅统一走 ChatView + useSseStream composable（带自动重连 + JWT header）。
 * 本函数仅保留为薄壳，供某些场景直接接管；请勿新增调用方。
 *
 * 与 v1.5.0 streamChat 不同：
 *   - 该端点是 v1.5.1 新增的会话级 SSE，包含 step_started/step_completed/
 *     step_failed/reasoning_paused/reasoning_resumed/step_replaced/hitl_* /
 *     heartbeat 11 个事件 type（详见 SseEvent）
 *   - 推送 Authorization: Bearer <jwt>（浏览器 EventSource 不能，故走 fetch）
 *
 * @param threadId - session id
 * @param onEvent - 事件回调（已 JSON.parse 完毕的 payload）
 * @param onError - fetch 错误回调（仅非 AbortError 才触发）
 * @returns AbortController —— 调用 abort() 主动断开
 */
export function subscribeSessionEvents(
  threadId: string,
  onEvent: (event: SseEvent) => void,
  onError: (err: string) => void,
): AbortController {
  const controller = new AbortController()
  // F6 修复：移除 URL query 中的 ?token=（OWASP 反模式：JWT 会泄漏到
  // 代理日志 / 浏览器历史 / 服务端 access log）。鉴权仅走 Authorization header。
  const url = `${BASE}/sessions/${encodeURIComponent(threadId)}/events`

  fetch(url, {
    method: 'GET',
    headers: {
      Accept: 'text/event-stream',
      'Cache-Control': 'no-cache',
      Authorization: `Bearer ${getJwtToken()}`,
    },
    signal: controller.signal,
    cache: 'no-store',
    credentials: 'same-origin',
  })
    .then(async (response) => {
      if (!response.ok) {
        onError(`SSE subscribeSessionEvents HTTP ${response.status}: ${response.statusText}`)
        return
      }
      const reader = response.body?.getReader()
      if (!reader) {
        onError('SSE body is not readable')
        return
      }
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const parts = buffer.split('\n\n')
        buffer = parts.pop() ?? ''
        for (const part of parts) {
          if (!part) continue
          if (part.startsWith(':')) continue // heartbeat comment
          for (const line of part.split('\n')) {
            if (line.startsWith('data:')) {
              const payload = line.slice(5).trimStart()
              if (!payload) continue
              try {
                onEvent(JSON.parse(payload) as SseEvent)
              } catch {
                /* skip malformed */
              }
            }
          }
        }
      }
    })
    .catch((err) => {
      if (err.name !== 'AbortError') {
        onError(String(err))
      }
    })

  return controller
}
