// ─── 响应 prefers-reduced-motion ──────────────
// 用法：const prefersReducedMotion = useReducedMotion()
import { ref, onMounted, onUnmounted } from 'vue'

export function useReducedMotion() {
  const prefersReducedMotion = ref(false)
  let mq: MediaQueryList | null = null
  const handler = (e: MediaQueryListEvent) => {
    prefersReducedMotion.value = e.matches
  }

  onMounted(() => {
    if (typeof window === 'undefined') return
    mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    prefersReducedMotion.value = mq.matches
    try {
      mq.addEventListener('change', handler)
    } catch {
      mq.addListener(handler)
    }
  })

  onUnmounted(() => {
    if (!mq) return
    try {
      mq.removeEventListener('change', handler)
    } catch {
      mq.removeListener(handler)
    }
  })

  return prefersReducedMotion
}
