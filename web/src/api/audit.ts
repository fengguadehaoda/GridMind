/** v1.4.0 HITL 审计 API */
import axios from 'axios'
import type { AuditEntry, AuditResponse, HitlAuditDecision } from '../types'

const http = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

/** GET /audit/hitl?decision=&limit= */
export async function fetchAuditLog(
  decision?: HitlAuditDecision,
  limit = 50,
): Promise<AuditResponse> {
  const { data } = await http.get<AuditResponse>('/audit/hitl', {
    params: { decision, limit },
  })
  return data
}

/** GET /audit/hitl/{thread_id} */
export async function fetchAuditByThread(threadId: string): Promise<AuditResponse> {
  const { data } = await http.get<AuditResponse>(`/audit/hitl/${threadId}`)
  return data
}

/** GET /metrics/summary */
export async function fetchMetricsSummary(): Promise<{
  enabled: boolean
  metrics: Record<string, unknown>
}> {
  const { data } = await http.get('/metrics/summary')
  return data
}

/** GET /health/critical */
export async function fetchCriticalHealth(): Promise<{ devices: unknown[]; count: number }> {
  const { data } = await http.get('/health/critical')
  return data
}