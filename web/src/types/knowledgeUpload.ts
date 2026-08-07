/**
 * types/knowledgeUpload.ts · 用户上传知识库前端类型（V1.7 · KB Upload）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 与后端 api/routers/knowledge_upload.py 的 Pydantic 响应模型一一对应
 * （架构 kb-upload-architecture-2026-08-06 §3.2 classDiagram）。
 *
 * 作者：寇豆码（工程师）
 */

/** 上传状态机（同步上传：idle → uploading → success | error） */
export type UploadStatus = 'idle' | 'uploading' | 'success' | 'error'

/** 单条用户上传文档（列表项，对应后端 KbUploadItem） */
export interface KbUploadItem {
  doc_id: string
  filename: string
  title: string
  size_bytes: number
  uploaded_at: string
  chunk_count: number
  status: string
}

/** 上传成功响应（对应后端 UploadResponse） */
export interface UploadResponse {
  doc_id: string
  title: string
  filename: string
  size_bytes: number
  chunk_count: number
  status: string
}

/** 列表响应（对应后端 KbUploadListResponse） */
export interface KbUploadListResponse {
  items: KbUploadItem[]
  total: number
}

/** 删除响应（对应后端 DeleteResponse） */
export interface DeleteResponse {
  status: string
  doc_id: string
  deleted_chunks: number
}

/** 单文件上传进度（store.uploading 的值：文件名 → 进度） */
export interface UploadProgress {
  /** 传输进度百分比 0-100；解析入库阶段保持 100 并显示「正在解析入库…」 */
  percent: number
  status: UploadStatus
  /** 失败原因（仅 status === 'error' 时有值） */
  error?: string
}
