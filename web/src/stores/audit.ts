/**
 * v1.5.1 T01 基础设施 · audit store（Pinia）
 *
 * F3 HITL 队列徽标 + F4 弹窗前置数据来源。
 *
 * 数据来源双通道（架构 §1.3.1）：
 *   - **轮询兜底**：每 5 秒 GET /audit/pending-count（防 SSE 断连漂移）
 *   - **主动推送**：SSE `hitl_interrupt` / `hitl_resolved` → 即时更新
 *   - **首屏**：`hydrate()` 在 main.ts mount 前调一次
 *
 * 与 v1.5.0 audit store 兼容：
 *   - 旧的 `audit.ts` 不存在（T01 新建），不影响他人
 *   - 旧的 `api/audit.ts` 提供 `fetchAuditLog / fetchAuditByThread` 等历史 API
 *     → F3/F4 仍可复用
 *
 * Actions（架构 §3.6 + 本实现合计 12 个）：
 *   lifecycle 2：hydrate / reset
 *   polling 3：refreshPendingCount / startPolling / stopPolling
 *   SSE handlers 2：onSseHitlInterrupt / onSseHitlResolved
 *   F4 HITL 三按钮 3：approve / reject / approveWithEdit
 *   内部 1：appendHistory（私有，不导出）
 *
 * 作者：寇豆码（T01 工程师）
 * 参考：frontend-v151-architecture-2026-08-04.md §3.6
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import type { HitlTask, HitlTaskStatus } from '@/types'

/* ────────────────────────────────────────────────────────────
 * 常量
 * ──────────────────────────────────────────────────────────── */

/** 默认轮询间隔（5s）*/
const POLL_INTERVAL_MS = 5000
/** 历史缓存上限（F4 弹窗"上一条"提示用） */
const HISTORY_LIMIT = 50

/* ────────────────────────────────────────────────────────────
 * Store 实现
 * ──────────────────────────────────────────────────────────── */

export const useAuditStore = defineStore('audit', () => {
  // ═══ State（6 fields）═══
  /** 当前待审任务数（驱动 HitlBadge 显示） */
  const pendingHitlCount = ref(0)
  /** 最近 50 条 HITL 历史（用于"上一条"提示 + 审计列表 fallback） */
  const hitlHistory = ref<HitlTask[]>([])
  /** F4 弹窗内容来源（最新一条 pending HITL 任务） */
  const latestPending = ref<HitlTask | null>(null)
  /** 上次同步时间（ISO；用于 UI 显示"⏳ 同步中…") */
  const lastSyncAt = ref<string>('')
  /** SSE 连接状态（connected / disconnected / error） */
  const connectionState = ref<'connected' | 'disconnected' | 'error'>('disconnected')
  /** 是否已完成首次 hydrate */
  const isHydrated = ref(false)

  // ═══ 私有变量（不导出）═══
  let pollTimer: ReturnType<typeof setInterval> | null = null

  // ═══ Getters（4 个）═══
  const hasPending = computed(() => pendingHitlCount.value > 0)
  /** 用于徽标显示：> 99 显示"99+" */
  const displayCount = computed(() => {
    if (pendingHitlCount.value === 0) return '0'
    if (pendingHitlCount.value > 99) return '99+'
    return String(pendingHitlCount.value)
  })
  /** 历史 pending 数（SSE drift 校正用） */
  const pendingInHistory = computed(() =>
    hitlHistory.value.filter((t) => t.status === 'pending').length,
  )
  /** 是否处于"等待后端"状态（用于徽标降级显示"·"） */
  const isBackendUnreachable = computed(
    () => connectionState.value === 'error' || connectionState.value === 'disconnected',
  )

  // ═══ Actions：轮询（3 个）═══

  /**
   * 主动拉取 /audit/pending-count 并更新 store。
   *
   * 失败处理（架构 §1.3.2 + 主理人决策 7.2）：
   *   - connectionState = 'error' → UI 显示"·"灰点
   *   - 不 throw（轮询场景下静默失败优于打断）
   *   - 保留 pendingHitlCount 上次值（不清零）
   */
  async function refreshPendingCount(): Promise<void> {
    try {
      const count = await fetchPendingHitlCount()
      pendingHitlCount.value = count
      lastSyncAt.value = new Date().toISOString()
      connectionState.value = 'connected'
    } catch (err) {
      connectionState.value = 'error'
      // eslint-disable-next-line no-console
      console.warn('[auditStore] refreshPendingCount failed:', err)
    }
  }

  /** 启动 5s 轮询；多次调用安全（内部 timer 守卫） */
  function startPolling(intervalMs: number = POLL_INTERVAL_MS): void {
    if (pollTimer !== null) return
    pollTimer = setInterval(() => {
      void refreshPendingCount()
    }, intervalMs)
  }

  /** 停止轮询（路由离开 / 组件 unmount） */
  function stopPolling(): void {
    if (pollTimer !== null) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  // ═══ Actions：SSE handlers（2 个）═══

  /**
   * SSE `hitl_interrupt` 事件 → pendingHitlCount += 1 + 入历史。
   *
   * F4 弹窗数据源同步绑定：latestPending 总是最新一条 pending HITL 任务。
   */
  function onSseHitlInterrupt(task: HitlTask): void {
    pendingHitlCount.value += 1
    hitlHistory.value = [task, ...hitlHistory.value].slice(0, HISTORY_LIMIT)
    latestPending.value = task
  }

  /**
   * SSE `hitl_resolved` 事件 → pendingHitlCount -= 1 + 任务状态流转。
   *
   * 边界：count 最小 0（避免 SSE drift 导致负数）。
   */
  function onSseHitlResolved(taskId: string, decision: HitlTaskStatus): void {
    pendingHitlCount.value = Math.max(0, pendingHitlCount.value - 1)
    const task = hitlHistory.value.find((t) => t.id === taskId)
    if (task) {
      task.status = decision
    }
    if (latestPending.value && latestPending.value.id === taskId) {
      latestPending.value = null
    }
  }

  // ═══ Actions：F4 HITL 三按钮（3 个）═══

  /**
   * F4 "✓ 仅批准" → POST /hitl/{taskId}/approve（无 body）。
   *
   * 注：完整实现需要等他人在 §3.9 hitlService 中实现 approveHitl()；
   * T01 实现 api/chat.ts 中的 hitlApprove/hitlReject/hitlApproveWithEdit
   * 方法（架构 §1.5.4 + 后端架构 §2.5），由本 store 复用。
   */
  async function approve(taskId: string): Promise<void> {
    const { hitlApprove } = await import('@/api/chat')
    await hitlApprove(taskId)
    onSseHitlResolved(taskId, 'approved')
  }

  /** F4 "✕ 拒绝" → POST /hitl/{taskId}/reject（body: { reason }） */
  async function reject(taskId: string, reason?: string): Promise<void> {
    const { hitlReject } = await import('@/api/chat')
    await hitlReject(taskId, { reason: reason ?? '' })
    onSseHitlResolved(taskId, 'rejected')
  }

  /** F4 "✎ 修改后批准" → POST /hitl/{taskId}/approve-with-edit */
  async function approveWithEdit(
    taskId: string,
    editedContent: string,
    editReason?: string,
  ): Promise<void> {
    const { hitlApproveWithEdit } = await import('@/api/chat')
    await hitlApproveWithEdit(taskId, {
      edited_content: editedContent,
      edit_reason: editReason ?? '',
    })
    onSseHitlResolved(taskId, 'approved-with-edit')
  }

  // ═══ Lifecycle（2 个）═══

  /**
   * 首屏水合（main.ts 中 app.mount 前调用）。
   *
   * 顺序：
   *   1. 立即拉一次 pendingHitlCount（不等轮询）
   *   2. 启动 5s 轮询
   *   3. 标记 isHydrated = true
   *
   * 多次调用安全（isHydrated 守卫）。
   */
  function hydrate(): void {
    if (isHydrated.value) return
    void refreshPendingCount().then(() => {
      isHydrated.value = true
    })
    startPolling()
  }

  /** 重置（登出 / 测试 tearDown） */
  function reset(): void {
    stopPolling()
    pendingHitlCount.value = 0
    hitlHistory.value = []
    latestPending.value = null
    lastSyncAt.value = ''
    connectionState.value = 'disconnected'
    isHydrated.value = false
  }

  return {
    // state（6）
    pendingHitlCount,
    hitlHistory,
    latestPending,
    lastSyncAt,
    connectionState,
    isHydrated,
    // getters（4）
    hasPending,
    displayCount,
    pendingInHistory,
    isBackendUnreachable,
    // polling（3）
    refreshPendingCount,
    startPolling,
    stopPolling,
    // SSE handlers（2）
    onSseHitlInterrupt,
    onSseHitlResolved,
    // F4 三按钮（3）
    approve,
    reject,
    approveWithEdit,
    // lifecycle（2）
    hydrate,
    reset,
  }
})

/* ────────────────────────────────────────────────────────────
 * 内部：直接发 GET /audit/pending-count
 *
 * 不依赖 axios 拦截器（保持简单；reasoning store 同样模式）。
 * JWT 通过 useJwtAuth.getAuthHeaders() 注入（后端 R-X2 修复后生效）。
 * ──────────────────────────────────────────────────────────── */
import { getAuthHeaders } from '@/composables/useJwtAuth'

async function fetchPendingHitlCount(): Promise<number> {
  const apiBase = (import.meta as { env?: Record<string, string | undefined> }).env?.VITE_API_BASE ?? ''
  const base = apiBase && apiBase.length > 0 ? apiBase : '/api'
  const response = await fetch(`${base}/audit/pending-count`, {
    method: 'GET',
    headers: {
      Accept: 'application/json',
      ...getAuthHeaders(),
    },
  })
  if (!response.ok) {
    throw new Error(`pending-count HTTP ${response.status}`)
  }
  const data = (await response.json()) as { count: number }
  return typeof data.count === 'number' ? data.count : 0
}
