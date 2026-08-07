// ─── 显示策略 Store（Pinia）──────────────────
// v1.5.0 P0-1 背景演示/标准模式 + P0-2 色盲模式
// 详见架构文档 §3.3
//
// 设计要点：
// 1. displayMode / colorBlind 是用户**主动选择**，持久化到 localStorage
// 2. bgIntensity 是**派生值**（由 displayMode 计算），不持久化
// 3. applyAttrs() 同步 :root[data-display-mode] / :root[data-cb-palette]，
//    CSS 选择器据此瞬时切换 token 引用，零重渲染
// 4. hydrate() 必须在 app.mount('#app') 之前调用（main.ts 已接入）

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type {
  BackgroundIntensity,
  ColorBlindPalette,
  DisplayMode,
} from '@/types/theme'
import {
  COLORBLIND_DEFAULT,
  COLORBLIND_STORAGE_KEY,
  DISPLAY_MODE_DEFAULT,
  DISPLAY_MODE_STORAGE_KEY,
} from '@/types/theme'

/** colorBlindPalette 合法值白名单（hydrate 时校验用） */
const COLORBLIND_PALETTES: readonly ColorBlindPalette[] = [
  'default',
  'ibm-cb-safe',
  'okabe-ito',
  'colorbrewer-rdylbu',
]

/** type guard: 校验 localStorage 读出的字符串是否合法 */
function isColorBlindPalette(value: string | null): value is ColorBlindPalette {
  return value !== null && (COLORBLIND_PALETTES as readonly string[]).includes(value)
}

/** type guard: 校验 localStorage 读出的字符串是否合法 */
function isDisplayMode(value: string | null): value is DisplayMode {
  return value === 'standard' || value === 'presentation'
}

export const useDisplayStore = defineStore('display', () => {
  // ── State ──
  const displayMode = ref<DisplayMode>(DISPLAY_MODE_DEFAULT)
  const colorBlind = ref<ColorBlindPalette>(COLORBLIND_DEFAULT)
  /**
   * v1.6.0 P1-5：紧凑断点强制背景降级覆盖（compact 时 App 设 'off'，其余 null）
   * 优先级高于 displayMode 派生，不修改用户持久化偏好
   */
  const bgOverride = ref<BackgroundIntensity | null>(null)
  /** 派生：标准模式完全关闭 background，演示模式全开（可被 bgOverride 覆盖） */
  const bgIntensity = computed<BackgroundIntensity>(() => {
    if (bgOverride.value !== null) return bgOverride.value
    return displayMode.value === 'presentation' ? 'high' : 'off'
  })

  // ── Getters ──
  const isStandard = computed(() => displayMode.value === 'standard')
  const isPresentation = computed(() => displayMode.value === 'presentation')
  const isColorBlindActive = computed(() => colorBlind.value !== 'default')

  // ── Actions ──

  /** displayMode 切换：更新 state + 持久化 + 同步 :root 属性（bgIntensity 为 computed 自动派生） */
  function setDisplayMode(mode: DisplayMode): void {
    displayMode.value = mode
    persist(displayMode.value, colorBlind.value)
    applyAttrs()
  }

  /**
   * v1.6.0 P1-5：紧凑断点背景降级覆盖。
   * @param intensity 覆盖值（'off' 强制标准背景强度）或 null 取消覆盖
   */
  function setBgOverride(intensity: BackgroundIntensity | null): void {
    bgOverride.value = intensity
  }

  /** 色盲 palette 切换：更新 state + 持久化 + 同步 :root 属性 */
  function setColorBlindPalette(palette: ColorBlindPalette): void {
    colorBlind.value = palette
    persist(displayMode.value, colorBlind.value)
    applyAttrs()
  }

  /**
   * 从 localStorage 读初始值。
   * - SSR 安全（typeof window 守卫）
   * - 静默失败（try/catch）
   * - 失败则保留 default
   *
   * 调用方：main.ts 中 app.mount('#app') 之前
   */
  function hydrate(): void {
    if (typeof window === 'undefined') return
    try {
      const m = localStorage.getItem(DISPLAY_MODE_STORAGE_KEY)
      const p = localStorage.getItem(COLORBLIND_STORAGE_KEY)
      if (isDisplayMode(m)) displayMode.value = m
      if (isColorBlindPalette(p)) colorBlind.value = p
    } catch {
      /* 隐私模式 / 配额超限：静默失败，保留 default */
    }
    applyAttrs()
  }

  // ── 内部辅助 ──

  /** 同步写 :root data-* 属性（CSS 选择器据此切换 token 引用） */
  function applyAttrs(): void {
    if (typeof document === 'undefined') return
    document.documentElement.setAttribute('data-display-mode', displayMode.value)
    document.documentElement.setAttribute('data-cb-palette', colorBlind.value)
  }

  /** 持久化 displayMode + colorBlind（bgIntensity 派生，不单独持久化） */
  function persist(mode: DisplayMode, palette: ColorBlindPalette): void {
    try {
      localStorage.setItem(DISPLAY_MODE_STORAGE_KEY, mode)
      localStorage.setItem(COLORBLIND_STORAGE_KEY, palette)
    } catch {
      /* 静默失败 */
    }
  }

  return {
    // state
    displayMode,
    colorBlind,
    bgOverride,
    bgIntensity,
    // getters
    isStandard,
    isPresentation,
    isColorBlindActive,
    // actions
    setDisplayMode,
    setColorBlindPalette,
    setBgOverride,
    hydrate,
  }
})
