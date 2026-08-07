/**
 * stores/knowledgeUpload.ts · 用户上传知识库 Pinia store（V1.7 · KB Upload）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 职责（架构 kb-upload-architecture-2026-08-06 §3.2 classDiagram）：
 *   - items       —— 文档列表（KbUploadItem[]）
 *   - loading     —— 列表加载中
 *   - errorMessage —— 最近一次操作的可读错误文案
 *   - uploading   —— 上传中集合（文件名 → UploadProgress）
 *   - fetchUploads / upload / remove / formatSize
 *
 * 状态机：idle → uploading（进度 0-100）→ success | error；
 * 上传为**同步**语义，成功即「已入库」，失败抛 Error（组件即时弹文案并保留错误态可重试）。
 *
 * 作者：寇豆码（工程师）
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  deleteUpload,
  extractUploadErrorMessage,
  fetchUploads,
  uploadKnowledge,
} from '../api/knowledgeUpload'
import type {
  KbUploadItem,
  UploadProgress,
  UploadResponse,
} from '../types/knowledgeUpload'

/** 前端展示的进度实体（文件名 → 进度） */
export interface UploadingEntry extends UploadProgress {
  filename: string
}

export const useKnowledgeUploadStore = defineStore('knowledgeUpload', () => {
  // ── State ──
  const items = ref<KbUploadItem[]>([])
  const loading = ref(false)
  const errorMessage = ref('')
  /** 文件名 → 上传进度（多文件并发互不覆盖） */
  const uploading = ref<Record<string, UploadingEntry>>({})

  // ── Actions ──

  /** 刷新文档列表（幂等：失败保留旧数据 + 可读文案） */
  async function fetchUploadsAction(): Promise<void> {
    if (loading.value) return
    loading.value = true
    errorMessage.value = ''
    try {
      const resp = await fetchUploads()
      items.value = Array.isArray(resp.items) ? resp.items : []
    } catch (err) {
      const msg = extractUploadErrorMessage(err)
      errorMessage.value = msg
      // 列表失败不清空旧数据，避免闪烁（共享知识 #10 风格）
      console.error('[knowledgeUpload.fetchUploads]', err)
    } finally {
      loading.value = false
    }
  }

  /**
   * 上传单个文件：传输进度 → 解析入库 → 成功刷新列表。
   *
   * @param file - 待上传文件（已通过格式/大小校验）
   * @param title - 可选标题
   * @param onProgress - 透传给组件的进度回调（可选）
   * @returns 上传成功响应
   * @throws Error 携带可读文案（组件即时弹错并保留重试能力）
   */
  async function upload(
    file: File,
    title?: string,
    onProgress?: (percent: number) => void,
  ): Promise<UploadResponse> {
    const key = file.name
    uploading.value[key] = { filename: key, percent: 0, status: 'uploading' }
    try {
      const resp = await uploadKnowledge(file, title, (pct) => {
        const cur = uploading.value[key] ?? { filename: key }
        uploading.value[key] = { ...cur, percent: pct, status: 'uploading' }
        onProgress?.(pct)
      })
      // 传输完成 → 解析入库阶段（后端同步返回前保持 100 + uploading）
      const cur = uploading.value[key] ?? { filename: key }
      uploading.value[key] = { ...cur, percent: 100, status: 'uploading' }
      // 入库成功后刷新列表并清理上传态
      await fetchUploadsAction()
      delete uploading.value[key]
      return resp
    } catch (err) {
      const msg = extractUploadErrorMessage(err)
      const cur = uploading.value[key] ?? { filename: key }
      uploading.value[key] = { ...cur, percent: 0, status: 'error', error: msg }
      errorMessage.value = msg
      throw new Error(msg)
    }
  }

  /** 删除文档：成功后刷新列表；失败抛可读文案 */
  async function remove(docId: string): Promise<void> {
    try {
      await deleteUpload(docId)
      items.value = items.value.filter((it) => it.doc_id !== docId)
    } catch (err) {
      const msg = extractUploadErrorMessage(err)
      errorMessage.value = msg
      throw new Error(msg)
    }
  }

  /** 清理某文件名的上传进度（成功提示展示后调用） */
  function clearUploading(filename: string): void {
    delete uploading.value[filename]
  }

  /** 清空全部上传进度（组件卸载 / 切换 Tab 时调用） */
  function clearAllUploading(): void {
    uploading.value = {}
  }

  // ── 工具 ──

  /** 字节数 → 人类可读（B / KB / MB） */
  function formatSize(bytes: number): string {
    if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
    const units = ['B', 'KB', 'MB', 'GB']
    let idx = 0
    let value = bytes
    while (value >= 1024 && idx < units.length - 1) {
      value /= 1024
      idx += 1
    }
    return `${value >= 100 || idx === 0 ? Math.round(value) : value.toFixed(1)} ${units[idx]}`
  }

  return {
    // state
    items,
    loading,
    errorMessage,
    uploading,
    // actions
    fetchUploads: fetchUploadsAction,
    upload,
    remove,
    clearUploading,
    clearAllUploading,
    // utils
    formatSize,
  }
})
