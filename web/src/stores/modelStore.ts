/**
 * v1.4.0 多模型 Store（Pinia）
 *
 * V1.7.0 M-2 改造（架构 §1.4 + PRD §6.2）：
 * - ``current`` 单值 → ``sessionModels: Record<threadId, modelId>`` 会话级映射
 *   + ``globalCurrent``（无会话上下文回退）+ ``activeThreadId``（当前激活会话）；
 * - ``getEffectiveModel()`` = ``sessionModels[active] ?? globalCurrent ?? defaultModel``；
 * - ``switchTo(modelId)`` 自动携带当前 ``activeThreadId``（无激活会话走全局，US-2.3）；
 * - ``setActiveThread(threadId)`` 切换会话时同步拉取/回退该会话模型。
 *
 * 约定（架构 §7.6）：modelStore 是会话模型唯一状态源；``sessionModels`` 键 =
 * LangGraph ``thread_id``；激活会话由 ``chatStore.activeThreadId`` 提供，
 * ``ChatView`` 负责调 ``setActiveThread`` 同步。
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { ModelInfo } from '../types'
import { fetchModels as apiFetchModels, switchModel as apiSwitchModel } from '../api/models'

export const useModelStore = defineStore('model', () => {
  // ── State ──
  const available = ref<ModelInfo[]>([])
  /** V1.7.0：会话级模型映射（键 = LangGraph thread_id） */
  const sessionModels = ref<Record<string, string>>({})
  /** V1.7.0：全局当前模型（无会话上下文时生效；与 v1.6 语义一致） */
  const globalCurrent = ref<string>('')
  /** V1.7.0：当前激活会话（由 chatStore.activeThreadId 同步；null=无激活会话） */
  const activeThreadId = ref<string | null>(null)
  const defaultModel = ref<string>('')
  const loaded = ref(false)
  const switching = ref(false)
  const error = ref<string | null>(null)

  // ── Getters ──
  /** 兼容旧调用：返回「当前生效模型」（v1.7 后为 getEffectiveModel 的别名） */
  const current = computed<string>(() => getEffectiveModel())

  /** 当前生效模型 = sessionModels[active] ?? globalCurrent ?? defaultModel */
  function getEffectiveModel(): string {
    const active = activeThreadId.value
    if (active && sessionModels.value[active]) {
      return sessionModels.value[active]
    }
    if (globalCurrent.value) return globalCurrent.value
    return defaultModel.value
  }

  /** 当前激活会话的会话级模型（无激活会话 / 未设置 → null） */
  function activeSessionModel(): string | null {
    const active = activeThreadId.value
    if (!active) return null
    return sessionModels.value[active] ?? null
  }

  const currentInfo = computed<ModelInfo | undefined>(() =>
    available.value.find(m => m.id === getEffectiveModel())
  )

  // ── Actions ──

  /** 初始化：拉全局模型清单（不带 thread_id，v1.6 兼容） */
  async function init() {
    if (loaded.value) return
    try {
      const data = await apiFetchModels()
      available.value = data.available
      globalCurrent.value = data.current
      defaultModel.value = data.default
      loaded.value = true
    } catch (e) {
      error.value = (e as Error).message
      console.error('[modelStore] init failed:', e)
    }
  }

  /**
   * 切换激活会话：同步会话级模型（US-2.1/2.2）。
   * - ``threadId`` 非空 → 拉取该会话生效模型（``?thread_id=``）并缓存到
   *   ``sessionModels[threadId]``；
   * - ``threadId`` 为 null → 清空激活会话（后续走全局，US-2.3）。
   */
  async function setActiveThread(threadId: string | null) {
    activeThreadId.value = threadId
    if (!threadId) return
    // 已缓存则直接回退，避免重复请求
    if (sessionModels.value[threadId]) return
    try {
      const data = await apiFetchModels(threadId)
      if (data.thread_id) {
        sessionModels.value[data.thread_id] = data.current
      }
    } catch (e) {
      // 拉取失败不阻断：getEffectiveModel 回退 globalCurrent/defaultModel
      console.warn('[modelStore] fetch session model failed:', e)
    }
  }

  /**
   * 切换模型：自动携带当前激活会话（无激活会话走全局，US-2.3）。
   * 成功后更新 ``sessionModels[active]``（或 ``globalCurrent``）。
   */
  async function switchTo(modelId: string) {
    const target = getEffectiveModel()
    if (modelId === target) return
    switching.value = true
    error.value = null
    const active = activeThreadId.value
    try {
      const resp = await apiSwitchModel(modelId, active)
      if (resp.thread_id) {
        sessionModels.value[resp.thread_id] = resp.current
      } else {
        globalCurrent.value = resp.current
      }
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
    sessionModels,
    globalCurrent,
    activeThreadId,
    defaultModel,
    loaded,
    switching,
    error,
    // getters
    current,
    currentInfo,
    getEffectiveModel,
    activeSessionModel,
    // actions
    init,
    setActiveThread,
    switchTo,
  }
})
