/**
 * echartsTheme.ts · ECharts 主题色适配（v1.6.0 P1-4）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构决策（p1-iteration-architecture §1 P1-4 + §7 共享知识 #4）：
 *   - canvas 图表无 CSS 继承，所有颜色必须经本模块读取 CSS 变量注入
 *   - 监听 :root[data-theme] 与 :root[data-cb-palette]（含 data-display-mode）
 *     变化 → 回调通知图表 setOption / 重建
 *   - 禁止硬编码色值
 */

import type { EChartsType } from 'echarts/core'

/** 图表可用的 tokens 调色板快照 */
export interface EchartsThemePalette {
  bgBase: string
  bgCard: string
  textPrimary: string
  textSecondary: string
  textMuted: string
  brand: string
  success: string
  warning: string
  danger: string
  info: string
  accent: string
  border: string
}

/** 读取 CSS 变量（getComputedStyle；SSR 安全） */
export function readToken(name: string, fallback = ''): string {
  if (typeof document === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

/** 读取当前主题 + 色盲 palette 下的一组 tokens 快照 */
export function readPalette(): EchartsThemePalette {
  return {
    bgBase: readToken('--bg-base', '#0a1228'),
    bgCard: readToken('--bg-card', 'rgba(255,255,255,0.04)'),
    textPrimary: readToken('--text-primary', '#e6f1ff'),
    textSecondary: readToken('--text-secondary', '#8fa3c7'),
    textMuted: readToken('--text-muted', '#5a6b8c'),
    brand: readToken('--brand-primary', '#00e5ff'),
    success: readToken('--status-success', '#00e676'),
    warning: readToken('--status-warning', '#ffb300'),
    danger: readToken('--status-danger', '#ff4757'),
    info: readToken('--status-info', '#00e5ff'),
    accent: readToken('--brand-accent', '#ffb300'),
    border: readToken('--border-default', 'rgba(0,229,255,0.2)'),
  }
}

/** 主题相关属性集合（MutationObserver 监听这些属性的变化） */
const THEME_ATTRIBUTES = ['data-theme', 'data-cb-palette', 'data-display-mode']

/**
 * 监听主题 / 色盲 palette 变化。
 * @param cb 变化回调（调用方 setOption 或重建图表）
 * @returns 注销函数（组件 onUnmounted 调用）
 */
export function watchThemeChange(cb: () => void): () => void {
  if (typeof document === 'undefined' || typeof MutationObserver === 'undefined') {
    return () => undefined
  }
  const observer = new MutationObserver((mutations) => {
    const relevant = mutations.some(
      (m) =>
        m.type === 'attributes' &&
        m.attributeName !== null &&
        THEME_ATTRIBUTES.includes(m.attributeName),
    )
    if (relevant) cb()
  })
  observer.observe(document.documentElement, { attributes: true, attributeFilter: THEME_ATTRIBUTES })
  return () => observer.disconnect()
}

/**
 * 应用 tokens 调色板到 chart。
 * 用法：在 setOption 前调用 apply(chart, readPalette()) 生成 color 数组，
 * 或直接把 readPalette() 结果交给图表 option 组装函数。
 */
export function apply(chart: EChartsType, palette: EchartsThemePalette = readPalette()): void {
  // 注入 textStyle / 图例 / tooltip 通用样式，与 HUD tokens 对齐
  chart.setOption({
    textStyle: {
      color: palette.textSecondary,
      fontFamily: 'Inter, "PingFang SC", "Microsoft YaHei", sans-serif',
    },
    legend: {
      textStyle: { color: palette.textSecondary },
      inactiveColor: palette.textMuted,
    },
    tooltip: {
      backgroundColor: palette.bgCard,
      borderColor: palette.border,
      textStyle: { color: palette.textPrimary, fontSize: 12 },
    },
  })
}

/** 图表通用 color 数组（按 tokens 顺序生成，供折线/柱状等系列复用） */
export function tokenColors(palette: EchartsThemePalette = readPalette()): string[] {
  return [palette.brand, palette.success, palette.warning, palette.danger, palette.info, palette.accent]
}

/** 依据错误率（0-1）映射 status 色阶（对标 OPEN-3000 拓扑着色 + Grafana 颜色规范化） */
export function errorRateColor(errorRate: number, palette: EchartsThemePalette = readPalette()): string {
  if (errorRate <= 0.01) return palette.success
  if (errorRate <= 0.05) return palette.warning
  return palette.danger
}
