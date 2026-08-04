import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as api from '../api/monitor'
import type { DeviceOverview } from '../types'

export const useMonitorStore = defineStore('monitor', () => {
  // ── State ──────────────────────────────────
  const devices = ref<DeviceOverview[]>([])
  const loading = ref(false)
  const lastUpdated = ref<string>('')
  const fetchFailed = ref(false)
  const pollingEnabled = ref(true)

  // ── 轮询 ──────────────────────────────────
  const POLLING_INTERVAL = 15000 // 与 App.vue 健康检查一致
  let timer: ReturnType<typeof setInterval> | null = null

  // ── Actions ───────────────────────────────
  /** 拉取设备总览 */
  async function fetchDevices() {
    loading.value = true
    try {
      const resp = await api.getDevices()
      devices.value = resp.devices || []
      fetchFailed.value = false
    } catch {
      fetchFailed.value = true // 保留旧数据，仅标记失败
    } finally {
      lastUpdated.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
      loading.value = false
    }
  }

  /** 启动轮询（幂等） */
  function startPolling() {
    if (timer) return
    timer = setInterval(() => {
      if (pollingEnabled.value) fetchDevices()
    }, POLLING_INTERVAL)
  }

  /** 停止轮询 */
  function stopPolling() {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  return {
    devices,
    loading,
    lastUpdated,
    fetchFailed,
    pollingEnabled,
    POLLING_INTERVAL,
    fetchDevices,
    startPolling,
    stopPolling,
  }
})
