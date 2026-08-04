// ─── GridMind 主题相关类型 ──────────────────
// 详见架构文档 §3.1

// ─── 主题 ──────────────────────────────────
export type Theme = 'dark' | 'light'
export const THEME_STORAGE_KEY = 'gridmind.theme' as const
export const THEME_DEFAULT: Theme = 'dark'

// ─── Logo ──────────────────────────────────
export type LogoVariant = 'horizontal' | 'vertical' | 'mark' | 'mono'
export type LogoTheme = 'auto' | 'dark' | 'light'

export interface LogoProps {
  variant?: LogoVariant
  theme?: LogoTheme
  size?: number | string
  showWordmark?: boolean
  alt?: string
}

// ─── ThemeToggle ───────────────────────────
export type ThemeToggleSize = 'sm' | 'md' | 'lg'
export type ThemeTogglePosition = 'inline' | 'fixed'

export interface ThemeToggleProps {
  size?: ThemeToggleSize
  showLabel?: boolean
  position?: ThemeTogglePosition
}

export interface ThemeToggleEmits {
  (e: 'change', theme: Theme): void
}

// ─── 背景组件 ──────────────────────────────
export type BackgroundIntensity = 'low' | 'mid' | 'high'

export interface TechBackgroundProps {
  intensity?: BackgroundIntensity
  showGrid?: boolean
  showGlow?: boolean
}

export interface ScanlineOverlayProps {
  opacity?: number
  speed?: number
  forceOff?: boolean
}

export interface DataStreamBadgeProps {
  label: string
  value: string | number
  unit?: string
  tone?: 'info' | 'success' | 'warning' | 'danger' | 'accent'
  pulse?: boolean
}

export interface PulseDotProps {
  tone?: 'success' | 'danger' | 'warning' | 'info' | 'accent'
  size?: number
  speed?: number
}

export interface HexGridProps {
  cols?: number
  rows?: number
  interactive?: boolean
}

// ─── StatHexagon ───────────────────────────
export interface StatHexagonProps {
  label: string
  value: string | number
  unit?: string
  delta?: number
  tone?: 'info' | 'success' | 'warning' | 'danger' | 'accent'
  icon?: string
  loading?: boolean
}

// ─── CommandPalette（M2 占位）──────────────
export type CommandScope = 'global' | 'chat' | 'monitor' | 'rag'

export interface CommandItem {
  id: string
  scope: CommandScope
  title: string
  subtitle?: string
  shortcut?: string[]
  icon?: string
  keywords?: string[]
  action: () => void | Promise<void>
  disabled?: boolean
}

export interface CommandPaletteProps {
  open: boolean
  scope?: CommandScope
}

// ─── Chat ──────────────────────────────────
export type AgentRole = 'user' | 'assistant' | 'system' | 'tool'

export interface AgentBadgeProps {
  agent: 'monitor' | 'diagnosis' | 'rag' | 'planner' | 'orchestrator' | 'user' | 'system'
  size?: 'sm' | 'md'
  showLabel?: boolean
}

export interface ThinkingIndicatorProps {
  label?: string
  speed?: number
}
