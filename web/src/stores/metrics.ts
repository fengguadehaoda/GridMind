// GridMind M3c · Pinia 可观测性 store
//
// 设计目标：
// - 单一入口管理灰度面板的拉取 + 自动刷新 + 缓存
// - 与 monitorStore 风格保持一致（ref + actions + 自动 startPolling/stopPolling）
// - 错误保留旧数据 + 标记 fetchFailed（不静默失败）

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import * as api from '../api/metrics'
import type {
  GrayscaleHistoryEntry,
  GrayscaleMonitor,
  GrayscaleStatus,
  MetricsSummary,
} from '../api/metrics'

export const useMetricsStore = defineStore('metrics', () => {
  // ── State ──────────────────────────────────
  const status = ref<GrayscaleStatus | null>(null)
  const metricsSummary = ref<MetricsSummary | null>(null)
  const history = ref<GrayscaleHistoryEntry[]>([])
  const loading = ref(false)
  const lastUpdated = ref('')
  const fetchFailed = ref(false)
  const pollingEnabled = ref(true)
  // 上一次切换操作的状态（用于 UI 反馈）
  const operationMsg = ref<string>('')
  const operationOk = ref<boolean | null>(null)

  // ── 轮询（与 monitor 一致：5s 间隔，5s 内已能感知一次切换）──────
  const POLLING_INTERVAL = 5000
  let timer: ReturnType<typeof setInterval> | null = null

  // ── Computed ──────────────────────────────────
  const monitor = computed<GrayscaleMonitor | null>(() => status.value?.monitor ?? null)
  const ratio = computed(() => status.value?.ratio ?? 0)
  const state = computed(() => status.value?.state ?? 'off')
  const rollbackCount = computed(() => status.value?.rollback_count ?? 0)
  const recentSwitchCount = computed(() => history.value.length)

  // ── Actions ───────────────────────────────

  /** 拉取灰度状态 + 历史 + metrics 摘要（一次刷新三连）。 */
  async function fetchStatus() {
    if (loading.value) return
    loading.value = true
    try {
      const [st, hist, ms] = await Promise.allSettled([
        api.getGrayscaleStatus(),
        api.getGrayscaleHistory(20),
        api.getMetricsSummary(),
      ])
      if (st.status === 'fulfilled') status.value = st.value
      if (hist.status === 'fulfilled') history.value = hist.value.entries ?? []
      if (ms.status === 'fulfilled') metricsSummary.value = ms.value
      fetchFailed.value =
        st.status === 'rejected' && hist.status === 'rejected'
    } catch {
      fetchFailed.value = true
    } finally {
      lastUpdated.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
      loading.value = false
    }
  }

  /** 启动轮询（幂等）。 */
  function startPolling() {
    if (timer) return
    // 立即拉一次，避免 5s 空窗
    fetchStatus()
    timer = setInterval(() => {
      if (pollingEnabled.value) fetchStatus()
    }, POLLING_INTERVAL)
  }

  /** 停止轮询（页面 unload / 用户主动停）。 */
  function stopPolling() {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  // ── 写入操作（admin 切流 / 回滚）─────────────────

  async function setRatio(targetRatio: number, actor: string, adminToken: string) {
    operationMsg.value = ''
    try {
      await api.grayscaleSet({ ratio: targetRatio, actor }, adminToken)
      operationMsg.value = `已切流至 ${targetRatio}%`
      operationOk.value = true
      await fetchStatus() // 立即刷新
    } catch (err) {
      operationMsg.value = `切流失败：${(err as Error)?.message ?? err}`
      operationOk.value = false
      throw err
    }
  }

  async function manualRollback(reason: string, actor: string, adminToken: string) {
    operationMsg.value = ''
    try {
      await api.grayscaleManualRollback({ reason, actor }, adminToken)
      operationMsg.value = `已触发回滚（${reason}）`
      operationOk.value = true
      await fetchStatus()
    } catch (err) {
      operationMsg.value = `回滚失败：${(err as Error)?.message ?? err}`
      operationOk.value = false
      throw err
    }
  }

  return {
    // state
    status,
    metricsSummary,
    history,
    loading,
    lastUpdated,
    fetchFailed,
    pollingEnabled,
    operationMsg,
    operationOk,
    // computed
    monitor,
    ratio,
    state,
    rollbackCount,
    recentSwitchCount,
    // actions
    POLLING_INTERVAL,
    fetchStatus,
    startPolling,
    stopPolling,
    setRatio,
    manualRollback,
  }
})
