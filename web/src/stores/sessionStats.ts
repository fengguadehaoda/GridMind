/**
 * stores/sessionStats.ts · Session 视图派生 store（v1.6.0 P1-3）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构决策（p1-iteration-architecture §1 P1-3 + §7 共享知识 #1/#6/#7）：
 *   - 复用 reasoningStore 为唯一事实来源，本 store 是薄壳做视图派生
 *   - 徽标 4 态由 reasoning 8 态聚合：
 *       idle+completed+aborted→idle；running+resuming→running；
 *       paused+editing→paused；error→error
 *   - token 降级：优先消费 SSE token 事件（content 字符数聚合为估算值；
 *     若事件带数字 token 字段则优先）；后端确认无 token 字段时
 *     totalTokens === null → UI 展示"步骤数 + 耗时"
 *   - drawerOpen 由 SessionBadge / useCommands 共享（命令面板"查看 Session 详情"）
 */

import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import { useReasoningStore } from '@/stores/reasoning'
import type {
  SessionCheckpointView,
  SessionStats,
  SessionStepView,
  SessionViewStatus,
} from '@/types/theme'

export const useSessionStatsStore = defineStore('sessionStats', () => {
  const reasoning = useReasoningStore()

  // ═══ State ═══
  const drawerOpen = ref(false)
  /** null = 待接入（后端无 token 字段时的降级展示） */
  const totalTokens = ref<number | null>(null)
  /** stepId → 估算 token（分配到当前 running step） */
  const tokensByStep = ref<Record<string, number>>({})
  const tokenSource = ref<'chars' | 'field' | 'none'>('none')
  const checkpoints = ref<SessionCheckpointView[] | null>(null)
  const checkpointsLoading = ref(false)
  const stateLoading = ref(false)

  /** 计时器心跳（驱动 elapsedMs 计算重算） */
  const tick = ref(0)
  let tickTimer: ReturnType<typeof setInterval> | null = null

  // ═══ 4 态聚合 ═══
  const viewStatus = computed<SessionViewStatus>(() => {
    switch (reasoning.status) {
      case 'running':
      case 'resuming':
        return 'running'
      case 'paused':
      case 'editing':
        return 'paused'
      case 'error':
        return 'error'
      default:
        return 'idle'
    }
  })

  const sessionId = computed(() => reasoning.sessionId)

  /** 会话切换（新 session / 清空）时重置 token 与累计 */
  watch(
    () => reasoning.sessionId,
    (newId, oldId) => {
      if (newId !== oldId) {
        totalTokens.value = null
        tokensByStep.value = {}
        tokenSource.value = 'none'
        accumulatedMs.value = 0
        runningSince.value = null
        checkpoints.value = null
      }
    },
  )

  /** 步骤时间线视图 */
  const stepsView = computed<SessionStepView[]>(() =>
    reasoning.steps.map((s) => ({
      id: s.id,
      index: s.index,
      name: s.name,
      nodeName: s.nodeName,
      status: s.status,
      durationMs: s.durationMs,
      tokens: tokensByStep.value[s.id] ?? null,
      startedAt: s.startedAt,
    })),
  )

  const totalSteps = computed(() => reasoning.steps.length)
  const completedSteps = computed(
    () => reasoning.steps.filter((s) => s.status === 'completed').length,
  )
  const errorMessage = computed(() => reasoning.errorMessage)

  // ═══ 计时 ═══
  /** 运行累计（进入 running 累加，暂停冻结） */
  const runningSince = ref<number | null>(null)
  const accumulatedMs = ref(0)

  watch(
    () => reasoning.status,
    (st) => {
      if (st === 'running' || st === 'resuming') {
        if (runningSince.value === null) runningSince.value = Date.now()
      } else if (st === 'paused' || st === 'editing') {
        if (runningSince.value !== null) {
          accumulatedMs.value += Date.now() - runningSince.value
          runningSince.value = null
        }
      } else {
        runningSince.value = null
        if (st === 'idle') accumulatedMs.value = 0
      }
    },
  )

  const elapsedMs = computed(() => {
    // 读取 tick 建立响应式依赖（每 1s 心跳重算）
    void tick.value
    if (runningSince.value !== null) {
      return accumulatedMs.value + (Date.now() - runningSince.value)
    }
    return accumulatedMs.value
  })

  /** 聚合视图（供 drawer / 外部消费） */
  const stats = computed<SessionStats>(() => ({
    viewStatus: viewStatus.value,
    sessionId: sessionId.value,
    elapsedMs: elapsedMs.value,
    totalSteps: totalSteps.value,
    completedSteps: completedSteps.value,
    totalTokens: totalTokens.value,
    checkpoints: checkpoints.value,
    errorMessage: errorMessage.value,
  }))

  // ═══ Actions ═══

  function startTicking(): void {
    if (tickTimer) return
    tickTimer = setInterval(() => {
      tick.value += 1
    }, 1000)
  }

  function stopTicking(): void {
    if (tickTimer) {
      clearInterval(tickTimer)
      tickTimer = null
    }
  }

  /**
   * SSE token 事件入口（由 ChatView handleSseEvent 挂钩）。
   * - 事件带数字 token 字段 → 优先使用
   * - 否则按 content 字符数估算
   * - 分配到当前 running step（无 running step 时只累计总量）
   */
  function onSseToken(content: string, tokenField?: number): void {
    const delta =
      typeof tokenField === 'number' && tokenField > 0 ? tokenField : content.length
    totalTokens.value = (totalTokens.value ?? 0) + delta
    tokenSource.value =
      typeof tokenField === 'number' && tokenField > 0 ? 'field' : 'chars'
    const running = reasoning.steps.find((s) => s.status === 'running')
    if (running) {
      tokensByStep.value[running.id] = (tokensByStep.value[running.id] ?? 0) + delta
    }
  }

  /**
   * 拉取可回滚节点（幂等：sessionId 为空直接跳过）
   *
   * QA R1 P1-3：后端无 `GET /sessions/{id}/checkpoints` 端点（PRD 声称就绪但
   * 实际不存在），为避免 404 改为**本地派生**：以 reasoning.steps 中已完成 /
   * 已编辑步骤作为可回滚节点（与 useCommands.rollbackToLastStep 选取逻辑一致）。
   * rewind 所需 step_index 与 steps.index 对齐，语义等价。
   */
  async function fetchCheckpoints(): Promise<void> {
    if (!sessionId.value) {
      checkpoints.value = null
      return
    }
    checkpointsLoading.value = true
    try {
      const rollbackSteps = reasoning.steps.filter(
        (s) => s.status === 'completed' || s.status === 'edited',
      )
      checkpoints.value = rollbackSteps.map((s) => ({
        stepIndex: s.index,
        stepId: s.id,
        name: s.name,
        checkpointId: s.id,
        createdAt: s.startedAt,
        isEditable: s.isEditable,
      }))
    } finally {
      checkpointsLoading.value = false
    }
  }

  /** 打开抽屉：拉取 checkpoints + 启动计时 */
  function openDrawer(): void {
    drawerOpen.value = true
    void fetchCheckpoints()
    startTicking()
  }

  /** 关闭抽屉：停止计时（保留累计值） */
  function closeDrawer(): void {
    drawerOpen.value = false
    stopTicking()
  }

  /** 重置 token（新建会话 / 清空对话时调用） */
  function resetTokens(): void {
    totalTokens.value = null
    tokensByStep.value = {}
    tokenSource.value = 'none'
    accumulatedMs.value = 0
    runningSince.value = null
  }

  return {
    // state
    drawerOpen,
    totalTokens,
    tokensByStep,
    tokenSource,
    checkpoints,
    checkpointsLoading,
    stateLoading,
    // computed
    viewStatus,
    sessionId,
    stepsView,
    totalSteps,
    completedSteps,
    errorMessage,
    elapsedMs,
    stats,
    // actions
    startTicking,
    stopTicking,
    onSseToken,
    fetchCheckpoints,
    openDrawer,
    closeDrawer,
    resetTokens,
  }
})
