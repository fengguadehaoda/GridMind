// ─── Onboarding Composable ──────────────────
// 组件内 `const { hasOnboarded, next, complete } = useOnboarding()` 即可
//
// 模式：与 useDisplay() 一致 —— reactive state 用 storeToRefs 解包，方法直接返回
// 额外：setupOnboardingGuard(router) 注册全局首屏守卫（T04 启用）

import type { Router } from 'vue-router'
import { storeToRefs } from 'pinia'
import { useOnboardingStore } from '@/stores/onboarding'
import type { OnboardingScenarioId } from '@/types/theme'

/** 白名单 tour key（与 OnboardingTour.vue 中枚举同步） */
const ALLOWED_TOURS: ReadonlyArray<string> = [
  'chat',
  'monitor',
  'grayscale',
  'audit',
  'system',
]

/** 路由 meta 标记为 public=true 的页面（如 /onboarding）不受守卫拦截 */
function isPublicRoute(path: string): boolean {
  return path === '/onboarding'
}

/** tour query 是否合法（任意值，存在即可作为"白名单"放行） */
function hasTourQuery(query: Record<string, unknown>): boolean {
  if (!query) return false
  const tour = query.tour
  if (typeof tour !== 'string' || tour.length === 0) return false
  return ALLOWED_TOURS.includes(tour)
}

export function useOnboarding() {
  const store = useOnboardingStore()
  const {
    hasOnboarded,
    currentStep,
    scenarioId,
    startedAt,
    completedAt,
    tourStates,
  } = storeToRefs(store)

  return {
    // reactive state (refs)
    hasOnboarded,
    currentStep,
    scenarioId,
    startedAt,
    completedAt,
    tourStates,
    // actions
    start: store.start,
    next: store.next,
    prev: store.prev,
    complete: store.complete,
    reset: store.reset,
    selectScenario: (id: OnboardingScenarioId) => store.selectScenario(id),
    hydrate: store.hydrate,
  }
}

/**
 * 注册 onboarding 首屏守卫（T04 启用）
 *
 * 守卫逻辑（架构 §5 T04 实现要点 #4）：
 *   - 未完成 && 不是 /onboarding && 没有 ?tour=xxx → 跳转 /onboarding
 *   - ?force=1 query 表示用户主动"重看"，放行
 *   - 防止首次访问 / 与 /onboarding 来回死循环
 *
 * 调用方：main.ts（app.mount 之前；store.hydrate 已先调用过）
 */
export function setupOnboardingGuard(router: Router): void {
  router.beforeEach((to, _from, next) => {
    const store = useOnboardingStore()
    // SSR 兜底
    if (typeof window === 'undefined') {
      next()
      return
    }

    // 已完成 或 force=1 重看入口：直接放行
    if (store.hasOnboarded || isPublicRoute(to.path) || to.query.force === '1') {
      next()
      return
    }

    // 有合法的 ?tour=xxx → 直接进入单页 tour（不强制先 wizard）
    if (hasTourQuery(to.query as Record<string, unknown>)) {
      next()
      return
    }

    // 第一次访问：redirect /onboarding
    next({ path: '/onboarding' })
  })
}
