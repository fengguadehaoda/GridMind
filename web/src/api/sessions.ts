/**
 * web/src/api/sessions.ts · M-5 会话管理 API（T02）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构 session-mgmt-architecture §3.3 新端点契约：
 *   - GET    /sessions?archived=0|1|2|all   → SessionsResponse
 *   - PATCH  /sessions/{thread_id}          → SessionSummary（重命名）
 *   - POST   /sessions/{thread_id}/archive  → SessionActionResponse
 *   - POST   /sessions/{thread_id}/restore  → SessionActionResponse
 *   - DELETE /sessions/{thread_id}          → SessionActionResponse（软删）
 *
 * V1.8.0 final-audit（P1）：统一复用共享 httpClient（401 自动 refresh 重放 +
 * Bearer 自动注入）——此前自建 axios 实例，生产 15min access TTL 过期后
 * 会话列表/重命名/归档/恢复/删除全部 401 且不自动续期，功能中断。
 * 作者：寇豆码（工程师）
 */
import httpClient from './httpClient'
import type { SessionsResponse, SessionSummary, SessionActionResponse } from '../types'

/** 归档态过滤参数（与后端 ``archived`` query 对齐：0|1|2|all） */
export type SessionArchivedFilter = 0 | 1 | 2 | 'all'

/**
 * GET /sessions — 会话列表（本人；管理员跨用户全量由后端决定）
 *
 * @param archived - 缺省不传 = 后端默认 0（活跃）；1=归档 2=删除 all=全状态
 */
export async function fetchSessions(archived?: SessionArchivedFilter): Promise<SessionsResponse> {
  const { data } = await httpClient.get<SessionsResponse>('/sessions', {
    params: archived !== undefined ? { archived: String(archived) } : undefined,
  })
  return data
}

/**
 * PATCH /sessions/{thread_id} — 重命名会话
 *
 * @param threadId - 会话 ID
 * @param title    - 新标题（后端校验非空 ≤100，非法 → 422）
 */
export async function renameSession(threadId: string, title: string): Promise<SessionSummary> {
  const { data } = await httpClient.patch<SessionSummary>(
    `/sessions/${encodeURIComponent(threadId)}`,
    { title },
  )
  return data
}

/** POST /sessions/{thread_id}/archive — 归档会话（archived=1） */
export async function archiveSession(threadId: string): Promise<SessionActionResponse> {
  const { data } = await httpClient.post<SessionActionResponse>(
    `/sessions/${encodeURIComponent(threadId)}/archive`,
  )
  return data
}

/** POST /sessions/{thread_id}/restore — 恢复归档会话（archived=0） */
export async function restoreSession(threadId: string): Promise<SessionActionResponse> {
  const { data } = await httpClient.post<SessionActionResponse>(
    `/sessions/${encodeURIComponent(threadId)}/restore`,
  )
  return data
}

/** DELETE /sessions/{thread_id} — 软删会话（archived=2 + deleted_at） */
export async function deleteSession(threadId: string): Promise<SessionActionResponse> {
  const { data } = await httpClient.delete<SessionActionResponse>(
    `/sessions/${encodeURIComponent(threadId)}`,
  )
  return data
}
