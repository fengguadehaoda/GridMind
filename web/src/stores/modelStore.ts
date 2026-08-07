/** v1.4.0 多模型 Store（Pinia） */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { ModelInfo } from '../types'
import { fetchModels as apiFetchModels, switchModel as apiSwitchModel } from '../api/models'

export const useModelStore = defineStore('model', () => {
  // ── State ──
  const available = ref<ModelInfo[]>([])
  const current = ref<string>('')
  const defaultModel = ref<string>('')
  const loaded = ref(false)
  const switching = ref(false)
  const error = ref<string | null>(null)

  // ── Getters ──
  const currentInfo = computed<ModelInfo | undefined>(() =>
    available.value.find(m => m.id === current.value)
  )

  // ── Actions ──
  async function init() {
    if (loaded.value) return
    try {
      const data = await apiFetchModels()
      available.value = data.available
      current.value = data.current
      defaultModel.value = data.default
      loaded.value = true
    } catch (e) {
      error.value = (e as Error).message
      console.error('[modelStore] init failed:', e)
    }
  }

  async function switchTo(modelId: string) {
    if (modelId === current.value) return
    switching.value = true
    error.value = null
    try {
      await apiSwitchModel(modelId)
      current.value = modelId
    } catch (e) {
      error.value = (e as Error).message
      console.error('[modelStore] switch failed:', e)
      throw e
    } finally {
      switching.value = false
    }
  }

  return {
    available,
    current,
    defaultModel,
    loaded,
    switching,
    error,
    currentInfo,
    init,
    switchTo,
  }
})