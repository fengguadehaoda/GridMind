// ─── 主题 Store（Pinia）───────────────────────
// 详见架构文档 §3.2 完整实现
// 关键路径：localStorage → prefers-color-scheme → 反 FOUC（index.html 内联脚本）

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { Theme } from '@/types/theme'
import { THEME_STORAGE_KEY, THEME_DEFAULT } from '@/types/theme'

export const useThemeStore = defineStore('theme', () => {
  // ── State ──
  const theme = ref<Theme>(THEME_DEFAULT)
  const systemTheme = ref<Theme>(getInitialSystemTheme())
  const hasUserChoice = ref(false)

  // ── Getters ──
  const isDark = computed(() => theme.value === 'dark')
  const isLight = computed(() => theme.value === 'light')

  // ── Actions ──
  function apply(t: Theme) {
    theme.value = t
    if (typeof document !== 'undefined') {
      document.documentElement.setAttribute('data-theme', t)
    }
  }

  function persist(t: Theme) {
    try {
      localStorage.setItem(THEME_STORAGE_KEY, t)
    } catch {
      /* private mode / 配额超限：静默失败 */
    }
  }

  function init() {
    if (typeof window === 'undefined') return
    try {
      const saved = localStorage.getItem(THEME_STORAGE_KEY)
      if (saved === 'light' || saved === 'dark') {
        hasUserChoice.value = true
        // 与内联脚本已注入的值保持一致（避免二次切换）
        const current = document.documentElement.getAttribute('data-theme') as Theme | null
        if (current === saved) {
          theme.value = saved
        } else {
          apply(saved)
        }
        return
      }
    } catch {
      /* ignore */
    }
    // 没有持久化：跟随系统
    apply(systemTheme.value)
    watchSystem()
  }

  function setTheme(t: Theme) {
    hasUserChoice.value = true
    apply(t)
    persist(t)
  }

  function toggle() {
    setTheme(theme.value === 'dark' ? 'light' : 'dark')
  }

  function followSystem() {
    hasUserChoice.value = false
    try {
      localStorage.removeItem(THEME_STORAGE_KEY)
    } catch {
      /* ignore */
    }
    apply(systemTheme.value)
    watchSystem()
  }

  function watchSystem() {
    if (typeof window === 'undefined') return
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const handler = (e: MediaQueryListEvent) => {
      if (hasUserChoice.value) return
      systemTheme.value = e.matches ? 'dark' : 'light'
      apply(systemTheme.value)
    }
    try {
      mq.addEventListener('change', handler)
    } catch {
      // Safari < 14
      mq.addListener(handler)
    }
  }

  return {
    theme,
    systemTheme,
    hasUserChoice,
    isDark,
    isLight,
    init,
    setTheme,
    toggle,
    followSystem,
    watchSystem,
  }
})

/**
 * 同步读取系统主题（用于 SSR-safe 初始化）
 * 浏览器侧：prefers-color-scheme: light → light，否则 dark
 */
function getInitialSystemTheme(): Theme {
  if (typeof window === 'undefined') return THEME_DEFAULT
  try {
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
  } catch {
    return THEME_DEFAULT
  }
}
