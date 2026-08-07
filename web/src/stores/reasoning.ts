/**
 * v1.5.1 T01 基础设施 · reasoning store（Pinia）
 *
 * 状态机：8 状态（详见 ./types/reasoning.ts ReasoningStatus）
 *   idle / running / paused / editing / resuming / completed / error / aborted
 *
 * Actions（架构 §3.5 共 17 个；本实现含 lifecycle 与 SSE handler 合计 18 个）：
 *   lifecycle 6：
 *     start / hydrate / appendStep / updateStep / completeStep / failStep
 *   terminal 4：
 *     markCompleted / markError / abort / reset
 *   F1 2：
 *     pause / resume
 *   F2 4：
 *     beginEdit / updateDraft / cancelEdit / rerunFromStep
 *   SSE handlers 3：
 *     onSsePaused / onSseResumed / onSseStepReplaced
 *
 * 关键约束（架构 + 主理人决策）：
 *   - 7.3 仅 running 状态显示"暂停"按钮（entering paused 自动 hideRunning）
 *   - 7.4 步骤编辑限定 isEditable（user content 可编辑；system/tool 不可）
 *   - 7.5 弹窗优先级：toast 1000 > 弹窗 100（与本 store 无关，留 audit / dialog 处理）
 *   - "pause 后所有 running step → pending" 的不变量由 onSsePaused 维护
 *   - editing ↔ running 互斥（架构 §1.1.1 第 2 条）
 *
 * Pinia setup-style store（Composition API）：与 v1.5.0 所有 store 风格一致。
 *
 * 作者：寇豆码（T01 工程师）
 * 参考：frontend-v151-architecture-2026-08-04.md §3.5 + §1.1/§1.2
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import {
  type AbortSessionRequest,
  type AbortSessionResponse,
  type PauseSessionResponse,
  type PendingHitlCountResponse,
  type ReasoningStatus,
  type ReasoningStep,
  type ResumeSessionResponse,
  type RewindSessionRequest,
  type RewindSessionResponse,
  type StepStatus,
} from '@/types'
import {
  abortSession as abortSessionApi,
  fetchPendingHitlCount as fetchPendingHitlCountApi,
  pauseSession as pauseSessionApi,
  resumeSession as resumeSessionApi,
  rewindSession as rewindSessionApi,
} from '@/api/chat'

/* ────────────────────────────────────────────────────────────
 * sessionId 持久化 key（架构 §1.5.3 边界条件："暂停后刷新页面"）
 * 仅在 status 进入 paused / error / editing 时存；resume 后清理
 * ──────────────────────────────────────────────────────────── */
const REATTACH_THREAD_ID_KEY = 'gridmind.reattach_thread_id'

/* ────────────────────────────────────────────────────────────
 * 私有工具函数（不导出）
 * ──────────────────────────────────────────────────────────── */

/**
 * 生成稳定 id（前端持久化用；后端无对应字段）。
 *
 * 优先 crypto.randomUUID()，降级到 Date.now+random（极老浏览器）。
 */
function makeStepId(): string {
  if (typeof globalThis.crypto !== 'undefined' && typeof globalThis.crypto.randomUUID === 'function') {
    return globalThis.crypto.randomUUID()
  }
  return `step_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`
}

/** ISO-8601 当前时间（兼容 SSR/Node 环境） */
function nowIso(): string {
  return new Date().toISOString()
}

/* ────────────────────────────────────────────────────────────
 * Store 实现
 * ──────────────────────────────────────────────────────────── */

export const useReasoningStore = defineStore('reasoning', () => {
  // ═══ State（11 fields）═══
  const sessionId = ref<string>('')
  const status = ref<ReasoningStatus>('idle')
  const steps = ref<ReasoningStep[]>([])
  /** 编辑草稿缓存（stepId → draft content）；不持久化 */
  const draftSteps = ref<Record<string, string>>({})
  /** 当前编辑中的 stepId；与 status === 'editing' 互斥绑定 */
  const editingStepId = ref<string>('')
  const lastPausedAt = ref<string>('')
  const lastResumedAt = ref<string>('')
  /** 暂停原因（user_manual / system_overload / hitl_required / checkpoint_full） */
  const pauseReason = ref<string>('')
  /** 中止原因（user_manual / safety_violation / timeout / checkpoint_expired） */
  const abortReason = ref<string>('')
  /** 错误消息（status === 'error' 时显示） */
  const errorMessage = ref<string>('')
  /** pending 状态：API 调用进行中标记（UI 禁用按钮 + 防双击） */
  const pendingPause = ref(false)
  const pendingResume = ref(false)
  const pendingEdit = ref(false)
  const pendingAbort = ref(false)
  /** 当前运行中的 step 序号（-1 表示无 running step） */
  const currentStepIndex = ref<number>(-1)

  // ═══ Getters（计算属性）═══

  /** 处于"活跃生成"态：running / paused / editing / resuming 都算 */
  const isActive = computed(() =>
    ['running', 'paused', 'editing', 'resuming'].includes(status.value),
  )
  const isPaused = computed(() => status.value === 'paused')
  const isRunning = computed(() => status.value === 'running')
  const isEditing = computed(() => status.value === 'editing')
  const isAborted = computed(() => status.value === 'aborted')

  /** 已完成步骤列表 */
  const completedSteps = computed(() => steps.value.filter((s) => s.status === 'completed'))

  /** 正在运行的步骤（通常 0 或 1 个） */
  const nextStepToRun = computed(() => steps.value.find((s) => s.status === 'running') ?? null)

  /** 总步骤数 */
  const totalSteps = computed(() => steps.value.length)

  /** 进度（0..1；用于进度条） */
  const progress = computed(() =>
    totalSteps.value === 0 ? 0 : completedSteps.value.length / totalSteps.value,
  )

  /** 判断某 step 是否可编辑（业务规则：user content 可编辑） */
  function isEditable(stepId: string): boolean {
    const step = steps.value.find((s) => s.id === stepId)
    return Boolean(step?.isEditable)
  }

  /** 单步耗时（ms；finished 为 null 时返回"运行中"实时值） */
  function elapsedMs(stepId: string): number {
    const step = steps.value.find((s) => s.id === stepId)
    if (!step || !step.startedAt) return 0
    if (step.finishedAt) return step.durationMs ?? 0
    return Date.now() - new Date(step.startedAt).getTime()
  }

  // ═══ Lifecycle actions（6 个）═══

  /**
   * 启动一个新推理 session。
   *
   * @param sid - LangGraph threadId（与后端 session_id 对齐）
   * @param initialSteps - 已有 checkpoint 列表（恢复场景时传入）
   */
  function start(sid: string, initialSteps: ReasoningStep[] = []): void {
    sessionId.value = sid
    status.value = 'running'
    steps.value = initialSteps.map((s) => ({ ...s }))
    draftSteps.value = {}
    editingStepId.value = ''
    pauseReason.value = ''
    abortReason.value = ''
    errorMessage.value = ''
    pendingPause.value = false
    pendingResume.value = false
    pendingEdit.value = false
    pendingAbort.value = false
    currentStepIndex.value = -1
  }

  /**
   * 水合恢复（架构 §1.5.3 边界）：从 sessionStorage 读取上次的 threadId。
   *
   * QA R1 P1-3：后端无 `GET /sessions/{id}/state` 端点（PRD 声称就绪但不存在），
   * 重连后的 steps 重建由 ChatView 经 SSE `/sessions/{id}/events` 订阅恢复。
   * 本 store 仅负责"取出 threadId"，不主动发请求（避免重复订阅）。
   */
  function hydrate(): void {
    if (typeof window === 'undefined') return
    try {
      const reattach = window.localStorage.getItem(REATTACH_THREAD_ID_KEY)
      if (reattach && sessionId.value === '') {
        sessionId.value = reattach
        status.value = 'paused'
      }
    } catch {
      /* 静默失败（localStorage 在隐私模式可能抛） */
    }
  }

  /** 追加一个新 step（来自 SSE step_started 事件） */
  function appendStep(step: ReasoningStep): void {
    if (steps.value.some((s) => s.id === step.id)) {
      // 同一 step 重复推送 → skip（防 SSE 重连去重）
      return
    }
    steps.value.push(step)
    if (step.status === 'running') {
      currentStepIndex.value = step.index
    }
  }

  /**
   * 部分更新某个 step 的字段（来自 SSE step_started / step_replaced）。
   *
   * 找不到 stepId 时静默忽略（避免脏数据导致 store 异常）。
   */
  function updateStep(stepId: string, partial: Partial<ReasoningStep>): void {
    const idx = steps.value.findIndex((s) => s.id === stepId)
    if (idx < 0) return
    const before = steps.value[idx]!
    const after = { ...before, ...partial }
    steps.value[idx] = after
    if (after.status === 'running') {
      currentStepIndex.value = after.index
    }
  }

  /**
   * 标记某 step 完成（SSE step_completed 处理入口）。
   *
   * 自动计算耗时 + 设置 finishedAt + 流转 status = 'completed'。
   */
  function completeStep(
    stepId: string,
    output: ReasoningStep['output'] = null,
    durationMs?: number,
  ): void {
    const step = steps.value.find((s) => s.id === stepId)
    if (!step) return
    const finishedAt = nowIso()
    const finalDuration =
      typeof durationMs === 'number'
        ? durationMs
        : Date.now() - new Date(step.startedAt).getTime()
    Object.assign(step, {
      status: 'completed' as StepStatus,
      finishedAt,
      durationMs: finalDuration,
      output,
    })
  }

  /**
   * 标记某 step 失败（SSE step_failed 处理入口）。
   *
   * 不自动 transition status（由后端单独发 `reasoning_error` 事件决定
   * 整体是否 error）；仅更新该 step 自身。
   */
  function failStep(stepId: string, errorMsg: string): void {
    const step = steps.value.find((s) => s.id === stepId)
    if (!step) return
    Object.assign(step, {
      status: 'failed' as StepStatus,
      finishedAt: nowIso(),
      output: { error: errorMsg },
    })
  }

  // ═══ Terminal actions（4 个）═══

  /** SSE reasoning_completed → status = 'completed' */
  function markCompleted(): void {
    status.value = 'completed'
    if (typeof window !== 'undefined') {
      try {
        window.localStorage.removeItem(REATTACH_THREAD_ID_KEY)
      } catch {
        /* ignore */
      }
    }
  }

  /** SSE reasoning_error → status = 'error' + 存错误消息 */
  function markError(message: string): void {
    status.value = 'error'
    errorMessage.value = message
  }

  /**
   * 强制中止（需配合 UI 二次确认 + 后端 POST /sessions/{id}/abort）。
   *
   * 注意：本函数仅做本地状态修改；API 调用请用 `abort()` 的 async 包装版本
   * 或直接由 ChatView 调 `abortSessionApi` 后调本函数。
   */
  function abort(reason = 'user_manual'): void {
    status.value = 'aborted'
    abortReason.value = reason
    if (typeof window !== 'undefined') {
      try {
        window.localStorage.removeItem(REATTACH_THREAD_ID_KEY)
      } catch {
        /* ignore */
      }
    }
  }

  /**
   * 完全重置（切路由 / 登出时调用）。
   *
   * 清空所有状态 + 草稿 + 编辑态；保留 localStorage reattach（用于下次 hydrate）。
   */
  function reset(): void {
    sessionId.value = ''
    status.value = 'idle'
    steps.value = []
    draftSteps.value = {}
    editingStepId.value = ''
    pauseReason.value = ''
    abortReason.value = ''
    errorMessage.value = ''
    pendingPause.value = false
    pendingResume.value = false
    pendingEdit.value = false
    pendingAbort.value = false
    currentStepIndex.value = -1
    lastPausedAt.value = ''
    lastResumedAt.value = ''
  }

  // ═══ F1 actions（2 个）═══

  /**
   * 暂停推理（F1 核心入口）。
   *
   * 流：UI 点击 → 调用 → 调 POST /sessions/{id}/pause → 200 OK 后乐观
   * status = 'paused'（不需要等待 SSE reasoning_paused 二次确认——
   * SSE confirmation 由 useSseStream.onEvent → onSsePaused 处理）。
   *
   * 边界：
   *   - 仅当 status === 'running' 时生效（其余态 no-op）
   *   - pendingPause 防抖（防双击）
   *   - 失败时 status 保持 'running'，UI 按钮还原；同时设置 errorMessage
   */
  async function pause(reason = 'user_manual'): Promise<PauseSessionResponse | null> {
    if (status.value !== 'running' || pendingPause.value) return null
    if (!sessionId.value) {
      errorMessage.value = '无法暂停：未在活跃 session 中'
      return null
    }
    pendingPause.value = true
    try {
      const resp = await pauseSessionApi(sessionId.value)
      // 乐观状态更新（防丢包体验更好；SSE 二次确认会在 onSsePaused 中重放）
      status.value = 'paused'
      pauseReason.value = reason
      lastPausedAt.value = nowIso()
      // 持久化 threadId：刷新页面后供 hydrate 恢复（steps 经 SSE 事件重建；
      // QA R1 P1-3：后端无 /sessions/{id}/state 端点）
      if (typeof window !== 'undefined') {
        try {
          window.localStorage.setItem(REATTACH_THREAD_ID_KEY, sessionId.value)
        } catch {
          /* ignore */
        }
      }
      return resp
    } catch (err) {
      // R-X3 patch：catch 内不暴露 err.message；服务侧 console.error，状态层通用文案
      console.error('[reasoning.pause]', err)
      errorMessage.value = '暂停失败，请稍后重试'
      throw err
    } finally {
      pendingPause.value = false
    }
  }

  /**
   * 恢复推理（F1 续作）。
   *
   * 仅 paused 状态可调用；
   * 1. 乐观 transition：status → 'resuming'（不等 API）
   * 2. 调 POST /sessions/{id}/resume → 200 OK → 维持 'resuming' 等 SSE
   * 3. SSE reasoning_resumed → status = 'running'（由 onSseResumed() 触发）
   *
   * 失败时回滚到 'paused'（同 pause 行为）。
   */
  async function resume(): Promise<ResumeSessionResponse | null> {
    if (status.value !== 'paused' || pendingResume.value) return null
    if (!sessionId.value) {
      errorMessage.value = '无法恢复：未在活跃 session 中'
      return null
    }
    pendingResume.value = true
    // 乐观提前 transition（API 调用前），让用户立即看到 'resuming'
    status.value = 'resuming'
    try {
      const resp = await resumeSessionApi(sessionId.value)
      return resp
    } catch (err) {
      // R-X3 patch：catch 内不暴露 err.message；服务侧 console.error，状态层通用文案
      console.error('[reasoning.resume]', err)
      errorMessage.value = '恢复失败，请稍后重试'
      // 失败回滚 paused
      status.value = 'paused'
      throw err
    } finally {
      pendingResume.value = false
    }
  }

  // ═══ F2 actions（4 个）═══

  /**
   * 进入编辑态（F2 入口）。
   *
   * 业务规则：
   *   - 仅当 step.isEditable === true（仅 user content）
   *   - 仅当 status === 'running' / 'paused'（running 也允许；PRD §3.2.3）
   *   - 立即把 step.promptFragment 复制到 draftSteps[id]
   *
   * @throws 'STEP_NOT_EDITABLE' if step 不可编辑
   * @throws 'REASONING_NOT_EDITABLE_STATE' if status 不允许进入编辑
   */
  function beginEdit(stepId: string): void {
    if (!isEditable(stepId)) {
      throw new Error('STEP_NOT_EDITABLE')
    }
    if (!['running', 'paused'].includes(status.value)) {
      throw new Error('REASONING_NOT_EDITABLE_STATE')
    }
    const step = steps.value.find((s) => s.id === stepId)
    if (!step) return
    editingStepId.value = stepId
    status.value = 'editing'
    draftSteps.value[stepId] = step.promptFragment
  }

  /**
   * 实时更新草稿（仅缓存，不提交后端）。
   *
   * 仅当当前正在编辑该 step 时生效（其他 step 改动被忽略）。
   */
  function updateDraft(stepId: string, content: string): void {
    if (editingStepId.value !== stepId) return
    draftSteps.value[stepId] = content
  }

  /**
   * 取消编辑（discard 草稿）。
   *
   * 返回到 paused（若曾在 paused 时进入）或 running（其余情况）。
   */
  function cancelEdit(): void {
    const editingId = editingStepId.value
    if (editingId) {
      delete draftSteps.value[editingId]
    }
    editingStepId.value = ''
    status.value = lastPausedAt.value ? 'paused' : 'running'
  }

  /**
   * 从某 step 重跑（F2 核心入口）。
   *
   * 调 POST /sessions/{id}/rewind 后 →
   *   - 替换后续 steps 为服务端返回的 new_steps
   *   - 当前 step 标记 status = 'edited' + 更新 promptFragment
   *   - editingStepId 清空 + draftSteps 清理
   *   - status → 'running'
   *
   * 错误自动回滚（架构 §1.2.2）：失败时 status 回到 'paused'，draftSteps 保留。
   */
  async function rerunFromStep(
    stepId: string,
    editedContent?: string,
  ): Promise<RewindSessionResponse | null> {
    if (editingStepId.value !== stepId || pendingEdit.value) return null
    if (!sessionId.value) {
      errorMessage.value = '无法重跑：未在活跃 session 中'
      return null
    }
    const content = editedContent ?? draftSteps.value[stepId]
    if (typeof content !== 'string') {
      errorMessage.value = '重跑失败：草稿内容为空'
      throw new Error('NO_DRAFT_CONTENT')
    }
    pendingEdit.value = true
    try {
      const idx = steps.value.findIndex((s) => s.id === stepId)
      if (idx < 0) {
        errorMessage.value = '重跑失败：步骤不存在'
        throw new Error('STEP_NOT_FOUND')
      }
      const body: RewindSessionRequest = {
        step_index: steps.value[idx]!.index,
        edited_content: { prompt_fragment: content },
      }
      const resp = await rewindSessionApi(sessionId.value, body)
      // 替换后续 steps
      if (Array.isArray(resp.new_steps)) {
        steps.value = [...steps.value.slice(0, idx), ...resp.new_steps]
      }
      // 标记该 step 已编辑
      const editedStep = steps.value[idx]
      if (editedStep) {
        editedStep.status = 'edited'
        editedStep.promptFragment = content
      }
      // 清理编辑态
      editingStepId.value = ''
      delete draftSteps.value[stepId]
      status.value = 'running'
      return resp
    } catch (err) {
      // R-X3 patch：catch 内不暴露 err.message；服务侧 console.error，状态层通用文案
      console.error('[reasoning.rerunFromStep]', err)
      errorMessage.value = '重跑失败，请稍后重试'
      // 自动回滚到 paused（架构 §1.2.2 第 4 行）
      status.value = 'paused'
      throw err
    } finally {
      pendingEdit.value = false
    }
  }

  /**
   * 强制中止（async 版本）—— 调后端 + 本地状态。
   *
   * ChatView 中"✕ 中止"按钮二次确认后调用。
   */
  async function abortWithApi(reason = 'user_manual'): Promise<AbortSessionResponse | null> {
    if (pendingAbort.value) return null
    if (!sessionId.value) return null
    pendingAbort.value = true
    try {
      const body: AbortSessionRequest = { reason }
      const resp = await abortSessionApi(sessionId.value, body)
      abort(reason)
      return resp
    } catch (err) {
      // R-X3 patch：catch 内不暴露 err.message；服务侧 console.error，状态层通用文案
      console.error('[reasoning.abortWithApi]', err)
      errorMessage.value = '中止失败，请稍后重试'
      throw err
    } finally {
      pendingAbort.value = false
    }
  }

  // ═══ SSE handlers（4 个）═══

  /** SSE reasoning_paused → status = 'paused' + running → pending 不变量维持 */
  function onSsePaused(): void {
    status.value = 'paused'
    lastPausedAt.value = nowIso()
    // 不变量：paused 时无 running step
    for (const step of steps.value) {
      if (step.status === 'running') {
        step.status = 'pending'
      }
    }
  }

  /** SSE reasoning_resumed → status = 'running' */
  function onSseResumed(): void {
    status.value = 'running'
    lastResumedAt.value = nowIso()
  }

  /** SSE step_replaced → 替换 fromIndex 之后的 steps */
  function onSseStepReplaced(fromIndex: number, newSteps: ReasoningStep[]): void {
    steps.value = [...steps.value.slice(0, fromIndex), ...newSteps]
  }

  /** 主动查询待审 HITL 任务数（用于 auditStore.refreshPendingCount） */
  async function fetchPendingHitlCount(): Promise<number> {
    try {
      const resp: PendingHitlCountResponse = await fetchPendingHitlCountApi()
      return resp.count
    } catch (err) {
      // 失败时返回 0（由 auditStore 显示灰点）
      return 0
    }
  }

  return {
    // state (14)
    sessionId,
    status,
    steps,
    draftSteps,
    editingStepId,
    lastPausedAt,
    lastResumedAt,
    pauseReason,
    abortReason,
    errorMessage,
    pendingPause,
    pendingResume,
    pendingEdit,
    pendingAbort,
    currentStepIndex,
    // getters (8)
    isActive,
    isPaused,
    isRunning,
    isEditing,
    isAborted,
    completedSteps,
    nextStepToRun,
    totalSteps,
    progress,
    isEditable,
    elapsedMs,
    // lifecycle 6
    start,
    hydrate,
    appendStep,
    updateStep,
    completeStep,
    failStep,
    // terminal 4
    markCompleted,
    markError,
    abort,
    reset,
    // F1 2
    pause,
    resume,
    // F2 4
    beginEdit,
    updateDraft,
    cancelEdit,
    rerunFromStep,
    // async terminal 1
    abortWithApi,
    // SSE handlers 3
    onSsePaused,
    onSseResumed,
    onSseStepReplaced,
    // audit 兼容 1
    fetchPendingHitlCount,
  }
})
