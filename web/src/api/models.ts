/** v1.4.0 多模型 LLM API（V1.7.0 M-2 会话级支持）
 *
 * V1.8.0 final-audit（P1）：统一走共享 httpClient——401 自动 refresh 重放 +
 * Bearer 自动注入；生产 access TTL 过期后模型列表/切换不再中断。
 */
import httpClient from './httpClient'
import type { ModelsResponse, ModelSwitchResponse } from '../types'

/**
 * GET /models — 列出所有可用模型
 *
 * V1.7.0：可选 ``threadId`` —— 携带时返回该会话生效模型
 * （``threads.model_id ?? 全局``），响应含 ``thread_id`` 字段（US-2.1/2.2）；
 * 不传时行为与 v1.6 完全一致（全局 current，US-2.3）。
 */
export async function fetchModels(threadId?: string | null): Promise<ModelsResponse> {
  const { data } = await httpClient.get<ModelsResponse>('/models', {
    params: threadId ? { thread_id: threadId } : undefined,
  })
  return data
}

/**
 * POST /models/switch — 切换模型（全局 或 会话级）
 *
 * - ``switchModel(modelId)``：无会话上下文 → 进程级全局（US-2.3，v1.6 兼容）；
 * - ``switchModel(modelId, threadId)``：仅该会话生效（US-2.1/2.2）。
 *
 * R2 回归修复：后端 /models/switch 使用 verify_jwt_if_prod（与 interrupt/session
 * 写端点同口径），前端必须带上 JWT，否则生产/匿名请求会被 401 误杀，活跃 UI 路径
 * （ModelSwitcher）被打死。httpClient 请求拦截器自动注入 Bearer（与 chat.ts 一致）。
 */
export async function switchModel(
  modelId: string,
  threadId?: string | null,
): Promise<ModelSwitchResponse> {
  const { data } = await httpClient.post<ModelSwitchResponse>(
    '/models/switch',
    threadId ? { model_id: modelId, thread_id: threadId } : { model_id: modelId },
  )
  return data
}
