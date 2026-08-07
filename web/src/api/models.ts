/** v1.4.0 多模型 LLM API */
import axios from 'axios'
import type { ModelsResponse, ModelSwitchResponse } from '../types'
import { getAuthHeaders } from '../composables/useJwtAuth'

const http = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

/** GET /models — 列出所有可用模型 */
export async function fetchModels(): Promise<ModelsResponse> {
  const { data } = await http.get<ModelsResponse>('/models')
  return data
}

/** POST /models/switch — 切换当前模型
 *
 * R2 回归修复：后端 /models/switch 使用 verify_jwt_if_prod（与 interrupt/session
 * 写端点同口径），前端必须带上 JWT，否则生产/匿名请求会被 401 误杀，活跃 UI 路径
 * （ModelSwitcher）被打死。复用 getAuthHeaders()（与 chat.ts 一致）。
 */
export async function switchModel(modelId: string): Promise<ModelSwitchResponse> {
  const { data } = await http.post<ModelSwitchResponse>(
    '/models/switch',
    { model_id: modelId },
    { headers: getAuthHeaders() },
  )
  return data
}