// ─── Onboarding 进度 Store（Pinia）───────────
// v1.5.0 P0-4 新手引导 wizard
// 详见架构文档 §3.3
//
// 设计要点：
// 1. hasOnboarded / scenarioId / completedAt 持久化到 localStorage
// 2. currentStep / startedAt 是**会话级**状态（不持久化）
// 3. hydrate() 必须在 app.mount('#app') 之前调用（main.ts 已接入）
// 4. T01 仅占位 —— 路由守卫跳转 / 场景数据消费由 T04 完成

import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { OnboardingScenarioId } from '@/types/theme'

/** localStorage 键（架构 §7.1 命名约定：gridmind.{域}.{项}） */
const ONBOARDED_KEY = 'gridmind.onboarded' as const
const ONBOARDED_AT_KEY = 'gridmind.onboardedAt' as const
const SCENARIO_KEY = 'gridmind.onboarding.scenarioId' as const

/** wizard 步骤范围 */
type OnboardingStep = 1 | 2 | 3

export const useOnboardingStore = defineStore('onboarding', () => {
  // ── State ──
  const hasOnboarded = ref(false)
  const currentStep = ref<OnboardingStep>(1)
  const scenarioId = ref<OnboardingScenarioId | null>(null)
  const startedAt = ref<string | null>(null)
  const completedAt = ref<string | null>(null)
  /** 单页 tour 完成标记：key = tour 名（如 'chat' / 'monitor'），由 T04 写入 */
  const tourStates = ref<Record<string, boolean>>({})

  // ── Actions ──

  /**
   * 从 localStorage 读初始值。
   * - SSR 安全（typeof window 守卫）
   * - 静默失败
   *
   * 调用方：main.ts 中 app.mount('#app') 之前
   */
  function hydrate(): void {
    if (typeof window === 'undefined') return
    try {
      hasOnboarded.value = localStorage.getItem(ONBOARDED_KEY) === 'true'
      const at = localStorage.getItem(ONBOARDED_AT_KEY)
      completedAt.value = at
      const scenario = localStorage.getItem(SCENARIO_KEY)
      if (scenario) scenarioId.value = scenario as OnboardingScenarioId
    } catch {
      /* 静默失败 */
    }
  }

  /** 开始 wizard：重置 step + 清空 startedAt（hasOnboarded 不动） */
  function start(): void {
    currentStep.value = 1
    scenarioId.value = null
    startedAt.value = new Date().toISOString()
  }

  /** 选择引导场景（Step 1 → Step 2 触发） */
  function selectScenario(id: OnboardingScenarioId): void {
    scenarioId.value = id
    try {
      localStorage.setItem(SCENARIO_KEY, id)
    } catch {
      /* 静默失败 */
    }
  }

  /** wizard 下一步（1 → 2 → 3；3 是末步） */
  function next(): void {
    if (currentStep.value < 3) {
      currentStep.value = (currentStep.value + 1) as OnboardingStep
    }
  }

  /** wizard 上一步（3 → 2 → 1；1 是首步） */
  function prev(): void {
    if (currentStep.value > 1) {
      currentStep.value = (currentStep.value - 1) as OnboardingStep
    }
  }

  /** 完成 wizard：写 hasOnboarded + completedAt，触发 localStorage 持久化 */
  function complete(): void {
    hasOnboarded.value = true
    const now = new Date().toISOString()
    completedAt.value = now
    try {
      localStorage.setItem(ONBOARDED_KEY, 'true')
      localStorage.setItem(ONBOARDED_AT_KEY, now)
    } catch {
      /* 静默失败 */
    }
  }

  /** 重置所有状态（用于"重看引导"或测试；清空 localStorage） */
  function reset(): void {
    hasOnboarded.value = false
    completedAt.value = null
    scenarioId.value = null
    startedAt.value = null
    currentStep.value = 1
    tourStates.value = {}
    try {
      localStorage.removeItem(ONBOARDED_KEY)
      localStorage.removeItem(ONBOARDED_AT_KEY)
      localStorage.removeItem(SCENARIO_KEY)
    } catch {
      /* 静默失败 */
    }
  }

  return {
    // state
    hasOnboarded,
    currentStep,
    scenarioId,
    startedAt,
    completedAt,
    tourStates,
    // actions
    hydrate,
    start,
    selectScenario,
    next,
    prev,
    complete,
    reset,
  }
})
