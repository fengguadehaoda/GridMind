// GridMind M3c · 前端可观测性 API 客户端
//
// 复用 axios /api prefix（与 monitor.ts 等价），调用：
// - GET  /metrics       → Prometheus text 格式（沙箱调试用，前端一般不直接消费）
// - GET  /metrics/summary → JSON 摘要（前端面板首选）
// - GET  /grayscale/status     → 灰度状态
// - GET  /grayscale/history    → 切换历史
// - POST /grayscale/set        → 管理员手动切流（X-Admin-Token）
// - POST /grayscale/manual_rollback → 手动回滚（X-Admin-Token）

import axios from 'axios'
import type { AxiosRequestConfig } from 'axios'

const http = axios.create({
  baseURL: '/api',
  timeout: 15000,
})

// ── 类型契约（与后端 MetricsCollector.get_summary() 对齐）────────────────

export interface MetricsSummary {
  enabled: boolean
  metrics: {
    started_at: number
    cypher_total: number
    template_renders: number
    switches: number
    rollbacks: number
    grayscale_ratio: number
    grayscale_state: number
    window_samples: number
    window_error_rate: number
  }
}

export interface GrayscaleMonitor {
  samples: number
  error_rate: number
  p95_ms: number
  neo4j_consecutive_failures: number
  window_s: number
  thresholds: {
    error_rate: number
    p95_ms: number
    neo4j_failures: number
  }
}

export interface GrayscaleHistoryEntry {
  ts: number
  actor: string
  from_ratio: number
  to_ratio: number
  from_state: string
  to_state: string
  reason: string
}

export interface GrayscaleStatus {
  state: string
  ratio: number
  started_at: number | null
  rollback_reason: string | null
  rollback_count: number
  neo4j_enabled: boolean
  monitor: GrayscaleMonitor
  history: GrayscaleHistoryEntry[]
}

export interface GrayscaleSetPayload {
  ratio: number
  actor?: string
}

export interface GrayscaleRollbackPayload {
  reason?: string
  actor?: string
}

export interface GrayscaleHistoryResponse {
  count: number
  entries: GrayscaleHistoryEntry[]
}

// ── Metrics 端点 ─────────────────────────────────────────────

/** GET /metrics/summary — JSON 摘要（前端面板首选数据源）。 */
export async function getMetricsSummary(
  config?: AxiosRequestConfig,
): Promise<MetricsSummary> {
  const { data } = await http.get<MetricsSummary>('/metrics/summary', config)
  return data
}

/** GET /metrics — Prometheus exposition format 原始文本（调试用）。 */
export async function getMetricsPromText(
  config?: AxiosRequestConfig,
): Promise<string> {
  const { data } = await http.get<string>('/metrics', {
    ...config,
    responseType: 'text',
    transformResponse: [(d) => d],
  })
  return data
}

// ── Grayscale 端点（与 M2 兼容）──────────────────────────────

/** GET /grayscale/status — 灰度状态快照（无需 admin token）。 */
export async function getGrayscaleStatus(
  config?: AxiosRequestConfig,
): Promise<GrayscaleStatus> {
  const { data } = await http.get<GrayscaleStatus>('/grayscale/status', config)
  return data
}

/** GET /grayscale/history?limit=20 — 切换历史。 */
export async function getGrayscaleHistory(
  limit = 20,
  config?: AxiosRequestConfig,
): Promise<GrayscaleHistoryResponse> {
  const { data } = await http.get<GrayscaleHistoryResponse>(
    '/grayscale/history',
    { ...config, params: { limit } },
  )
  return data
}

/** POST /grayscale/set — 管理员切流（带 X-Admin-Token）。 */
export async function grayscaleSet(
  payload: GrayscaleSetPayload,
  adminToken: string,
  config?: AxiosRequestConfig,
): Promise<unknown> {
  const { data } = await http.post('/grayscale/set', payload, {
    ...config,
    headers: {
      ...(config?.headers ?? {}),
      'X-Admin-Token': adminToken,
    },
  })
  return data
}

/** POST /grayscale/manual_rollback — 手动回滚（带 X-Admin-Token）。 */
export async function grayscaleManualRollback(
  payload: GrayscaleRollbackPayload,
  adminToken: string,
  config?: AxiosRequestConfig,
): Promise<unknown> {
  const { data } = await http.post('/grayscale/manual_rollback', payload, {
    ...config,
    headers: {
      ...(config?.headers ?? {}),
      'X-Admin-Token': adminToken,
    },
  })
  return data
}

/* ═══════════════════════════════════════════════════════════════
 * v1.6.0 P1-4 · 灰度拓扑图端点（可选探测）
 * 后端未排期时返回 404，grayscaleGraph store 回落前端模拟数据。
 * ═══════════════════════════════════════════════════════════════ */

export interface GrayscaleGraphNodeDto {
  id: string
  name: string
  type: string
  load: number
  error_rate?: number
  status?: string
  meta?: Record<string, unknown>
}

export interface GrayscaleGraphEdgeDto {
  source: string
  target: string
  label?: string
  weight?: number
}

export interface GrayscaleGraphResponse {
  nodes: GrayscaleGraphNodeDto[]
  edges: GrayscaleGraphEdgeDto[]
}

/** GET /grayscale/graph — 灰度拓扑图（节点 ≤200；404 时前端回落模拟） */
export async function getGrayscaleGraph(
  config?: AxiosRequestConfig,
): Promise<GrayscaleGraphResponse> {
  const { data } = await http.get<GrayscaleGraphResponse>('/grayscale/graph', config)
  return data
}
