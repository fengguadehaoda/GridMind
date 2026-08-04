// ─── 主题便捷访问 Composable ─────────────────
// 组件内 `const { isDark, theme, toggle } = useTheme()` 即可

import { storeToRefs } from 'pinia'
import { useThemeStore } from '@/stores/theme'
import type { Theme } from '@/types/theme'

export function useTheme() {
  const store = useThemeStore()
  const { theme, systemTheme, isDark, isLight, hasUserChoice } = storeToRefs(store)

  return {
    theme,
    systemTheme,
    isDark,
    isLight,
    hasUserChoice,
    toggle: store.toggle,
    setTheme: (t: Theme) => store.setTheme(t),
    followSystem: store.followSystem,
    init: store.init,
  }
}
