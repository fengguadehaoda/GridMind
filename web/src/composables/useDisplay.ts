// ─── 显示策略 Composable ───────────────────
// 组件内 `const { displayMode, setDisplayMode } = useDisplay()` 即可
//
// 模式：与 useTheme() 一致 —— reactive state 用 storeToRefs 解包，方法直接返回

import { storeToRefs } from 'pinia'
import { useDisplayStore } from '@/stores/display'
import type { ColorBlindPalette, DisplayMode } from '@/types/theme'

export function useDisplay() {
  const store = useDisplayStore()
  const {
    displayMode,
    colorBlind,
    bgIntensity,
    isStandard,
    isPresentation,
    isColorBlindActive,
  } = storeToRefs(store)

  return {
    // reactive state (refs)
    displayMode,
    colorBlind,
    bgIntensity,
    isStandard,
    isPresentation,
    isColorBlindActive,
    // actions
    setDisplayMode: (mode: DisplayMode) => store.setDisplayMode(mode),
    setColorBlindPalette: (palette: ColorBlindPalette) =>
      store.setColorBlindPalette(palette),
    hydrate: store.hydrate,
  }
}
