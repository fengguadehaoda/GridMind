/**
 * api/knowledgeUpload.ts · 用户上传知识库 API（V1.7 · KB Upload）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 复用 web/src/api/chat.ts 的 resolveBaseUrl()（默认 /api，Vite proxy 到 9900）；
 * 鉴权统一 getAuthHeaders()（Authorization: Bearer <jwt>，dev 默认 token）。
 *
 * 上传用 FormData + axios onUploadProgress 进度回调；
 * **不**手动设置 Content-Type（交给浏览器带 multipart boundary）。
 *
 * 作者：寇豆码（工程师）
 */

import axios from 'axios'
import { getAuthHeaders } from '../composables/useJwtAuth'
import type {
  DeleteResponse,
  KbUploadListResponse,
  UploadResponse,
} from '../types/knowledgeUpload'
import { resolveBaseUrl } from './chat'

const BASE = resolveBaseUrl()

// 上传可能包含解析/切分/入库，超时放宽到 120s（默认 60s）
const http = axios.create({
  baseURL: BASE,
  timeout: 120000,
})

/**
 * POST /api/knowledge/upload — 上传知识文档（multipart：file + 可选 title）
 *
 * @param file - 待上传文件（.txt / .md / .pdf，≤5MB；浏览器侧校验后才会调用）
 * @param title - 可选标题，缺省后端取文件名
 * @param onProgress - 传输进度回调（0-100 百分比）
 * @returns 上传成功响应（doc_id / chunk_count / ...）
 * @throws axios 错误（调用方用 store 层统一提取可读文案）
 */
export async function uploadKnowledge(
  file: File,
  title: string | undefined,
  onProgress?: (percent: number) => void,
): Promise<UploadResponse> {
  const form = new FormData()
  form.append('file', file)
  if (title && title.trim()) {
    form.append('title', title.trim())
  }
  const { data } = await http.post<UploadResponse>('/knowledge/upload', form, {
    // 不手动设 Content-Type —— 浏览器自动带 multipart boundary
    headers: { ...getAuthHeaders() },
    onUploadProgress: (e) => {
      if (onProgress && e.total) {
        onProgress(Math.round((e.loaded / e.total) * 100))
      }
    },
  })
  return data
}

/**
 * GET /api/knowledge/uploads — 列出全部用户上传文档（含 chunk 数）
 *
 * @returns 文档列表响应（时间倒序）
 */
export async function fetchUploads(): Promise<KbUploadListResponse> {
  const { data } = await http.get<KbUploadListResponse>('/knowledge/uploads', {
    headers: getAuthHeaders(),
  })
  return data
}

/**
 * DELETE /api/knowledge/uploads/{doc_id} — 删除用户上传文档
 *
 * @param docId - 文档 id（形如 user-upload:slug-hash；encodeURIComponent 防特殊字符）
 * @returns 删除响应（deleted_chunks）
 */
export async function deleteUpload(docId: string): Promise<DeleteResponse> {
  const { data } = await http.delete<DeleteResponse>(
    `/knowledge/uploads/${encodeURIComponent(docId)}`,
    { headers: getAuthHeaders() },
  )
  return data
}

/** 从 axios 错误中提取可读后端文案（detail）或兜底文案 */
export function extractUploadErrorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const detail = (err.response?.data as { detail?: unknown } | undefined)?.detail
    if (typeof detail === 'string' && detail.trim()) {
      return detail
    }
    if (err.code === 'ECONNABORTED') {
      return '请求超时，请稍后重试'
    }
    if (err.response?.status) {
      return `请求失败（HTTP ${err.response.status}），请稍后重试`
    }
    return '网络异常，请检查后端服务是否可用'
  }
  return err instanceof Error ? err.message : '未知错误，请稍后重试'
}
