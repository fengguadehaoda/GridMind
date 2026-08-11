/** v1.4.0 HITL 审计 API
 *
 * V1.8.0 final-audit（P1）：统一走共享 httpClient——401 自动 refresh 重放 +
 * Bearer 自动注入；生产 access TTL 过期后审计页不再中断。
 */
import httpClient from './httpClient'
import type { AuditEntry, AuditResponse, HitlAuditDecision } from '../types'

/** GET /audit/hitl?decision=&limit= */
export async function fetchAuditLog(
  decision?: HitlAuditDecision,
  limit = 50,
): Promise<AuditResponse> {
  const { data } = await httpClient.get<AuditResponse>('/audit/hitl', {
    params: { decision, limit },
  })
  return data
}

/** GET /audit/hitl/{thread_id} */
export async function fetchAuditByThread(threadId: string): Promise<AuditResponse> {
  const { data } = await httpClient.get<AuditResponse>(`/audit/hitl/${threadId}`)
  return data
}

/** GET /metrics/summary */
export async function fetchMetricsSummary(): Promise<{
  enabled: boolean
  metrics: Record<string, unknown>
}> {
  const { data } = await httpClient.get('/metrics/summary')
  return data
}

/** GET /health/critical */
export async function fetchCriticalHealth(): Promise<{ devices: unknown[]; count: number }> {
  const { data } = await httpClient.get('/health/critical')
  return data
}