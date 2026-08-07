/**
 * useViewport.ts · 三档断点 + 移动端 composable（v1.6.0 P1-5 / header-redesign T04）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构决策（p1-iteration-architecture §1 P1-5 + §7 共享知识 #3；
 *            header-redesign-architecture §7.3）：
 *   - 语义 tier 三档：large(≥1920) / standard(1280-1920) / compact(≤1279.98)
 *   - 组件内逻辑分支（如 compact 强制背景降级 / isMobile 抽屉宽度）用本 composable；
 *     纯 CSS 布局用 media query，二者并存不混写同一规则
 *   - matchMedia 查询（large min-width:1920px；compact max-width:1279.98px；
 *     mobile max-width:767.98px 即 below-md <768px）
 *   - T04 新增 isMobile（<768px），用于「更多」折叠点 / 抽屉宽度 / 卡片隐藏等逻辑分支，
 *     向后兼容：既有返回字段不变
 */

import { computed, onMounted, onUnmounted, ref } from 'vue'
import type { ComputedRef, Ref } from 'vue'

export type ViewportTier = 'large' | 'standard' | 'compact'

export interface ViewportState {
  tier: Ref<ViewportTier>
  isLarge: ComputedRef<boolean>
  isStandard: ComputedRef<boolean>
  isCompact: ComputedRef<boolean>
  /** v1.6.0 header-redesign T04：<768px 移动端（below-md） */
  isMobile: ComputedRef<boolean>
  /** 视口宽度（px，供个别组件数值判断） */
  width: Ref<number>
}

function createMediaQuery(query: string): MediaQueryList | null {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return null
  return window.matchMedia(query)
}

export function useViewport(): ViewportState {
  const width = ref<number>(typeof window !== 'undefined' ? window.innerWidth : 1280)

  // 注意：largeQuery / compactQuery / mobileQuery 必须先声明，resolveTier() 函数体访问它们；
  // 若在二者之前调用 resolveTier() 会触发 TDZ（temporal dead zone）→ 白屏（QA R1 P0-1）
  const largeQuery = createMediaQuery('(min-width: 1920px)')
  const compactQuery = createMediaQuery('(max-width: 1279.98px)')
  const mobileQuery = createMediaQuery('(max-width: 767.98px)')

  const tier = ref<ViewportTier>(resolveTier())
  const isMobileRef = ref<boolean>(mobileQuery?.matches ?? false)

  function resolveTier(): ViewportTier {
    if (largeQuery?.matches) return 'large'
    if (compactQuery?.matches) return 'compact'
    return 'standard'
  }

  function updateFromEvent(): void {
    tier.value = resolveTier()
    if (typeof window !== 'undefined') width.value = window.innerWidth
    if (mobileQuery) isMobileRef.value = mobileQuery.matches
  }

  onMounted(() => {
    largeQuery?.addEventListener('change', updateFromEvent)
    compactQuery?.addEventListener('change', updateFromEvent)
    mobileQuery?.addEventListener('change', updateFromEvent)
    if (typeof window !== 'undefined') {
      window.addEventListener('resize', updateFromEvent)
    }
  })

  onUnmounted(() => {
    largeQuery?.removeEventListener('change', updateFromEvent)
    compactQuery?.removeEventListener('change', updateFromEvent)
    mobileQuery?.removeEventListener('change', updateFromEvent)
    if (typeof window !== 'undefined') {
      window.removeEventListener('resize', updateFromEvent)
    }
  })

  const isLarge = computed(() => tier.value === 'large')
  const isStandard = computed(() => tier.value === 'standard')
  const isCompact = computed(() => tier.value === 'compact')
  const isMobile = computed(() => isMobileRef.value)

  return { tier, isLarge, isStandard, isCompact, isMobile, width }
}
