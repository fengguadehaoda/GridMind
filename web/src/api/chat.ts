import axios from 'axios'
import type { ChatResponse, SseEvent, InterruptDecisionRequest, InterruptDecisionResponse, DiagnosisFusionResult } from '../types'

const BASE = '/api'

const http = axios.create({
  baseURL: BASE,
  timeout: 60000,
})

/** POST /chat — 发送消息（阻塞模式） */
export async function sendMessage(message: string, threadId?: string): Promise<ChatResponse> {
  const { data } = await http.post<ChatResponse>('/chat', {
    message,
    thread_id: threadId || null,
    stream: false,
  })
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

  fetch(url, { signal: controller.signal })
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
  const { data } = await http.post<ChatResponse>(`/interrupt/${encodeURIComponent(threadId)}/approve`, { reason })
  return data
}

/** POST /interrupt/{thread_id}/reject — 拒绝 HITL（老端点，向后兼容 1 季度） */
export async function rejectInterrupt(threadId: string, reason = ''): Promise<ChatResponse> {
  const { data } = await http.post<ChatResponse>(`/interrupt/${encodeURIComponent(threadId)}/reject`, { reason })
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
  const { data } = await http.post<InterruptDecisionResponse>(
    `/interrupt/${encodeURIComponent(threadId)}/decision`,
    payload,
  )
  return data
}

/** GET /thread/{thread_id} — 查询线程历史 */
export async function getThread(threadId: string) {
  const { data } = await http.get(`/thread/${encodeURIComponent(threadId)}`)
  return data
}

/** GET / — 健康检查 */
export async function healthCheck() {
  const { data } = await http.get('/')
  return data
}

/** GET /audit/hitl/{thread_id} — 查询 HITL 审计日志 */
export async function getHitlAudit(threadId: string) {
  const { data } = await http.get(`/audit/hitl/${encodeURIComponent(threadId)}`)
  return data as { thread_id: string; count: number; entries: Array<Record<string, unknown>> }
}

/** GET /diagnosis/{thread_id}/reasoning — 拉取诊断完整推理链（P0 可解释性 AI） */
export async function getDiagnosisReasoning(threadId: string): Promise<DiagnosisFusionResult> {
  const { data } = await http.get<DiagnosisFusionResult>(`/diagnosis/${encodeURIComponent(threadId)}/reasoning`)
  return data
}
