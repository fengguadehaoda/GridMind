// ─── GridMind 主题相关类型 ──────────────────
// 详见架构文档 §3.1
// v1.5.0 新增：Status / ColorBlindPalette / DisplayMode / BackgroundIntensity 'off'
//            / OnboardingScenario / STATUS_PRESENTATION

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
/**
 * 背景动效强度（v1.5.0 新增 'off' 用于标准模式完全关闭 background 系列）
 * - 'off'  : 完全关闭（标准模式 + 长时间盯盘）
 * - 'low'  : 极弱（保留 PulseDot 微动效）
 * - 'mid'  : 中等
 * - 'high' : 完整（演示模式 / 汇报录屏）
 */
export type BackgroundIntensity = 'off' | 'low' | 'mid' | 'high'

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
  /** 状态语义：v1.5.0 新增 'normal'/'critical'/'accent'，旧值 'success'/'danger' 内部自动映射 */
  tone?:
    | 'normal'
    | 'warning'
    | 'critical'
    | 'info'
    | 'accent'
    | 'success'   // legacy alias → normal
    | 'danger'    // legacy alias → critical
  size?: number
  speed?: number
  /** v1.5.0 P0-2：形状（外轮廓），不传默认 'circle'，旧调用方零改动 */
  shape?: 'circle' | 'triangle' | 'square' | 'diamond' | 'hexagon'
  /** v1.5.0 P0-2：内字符（glyph），不传默认 'dot'，旧调用方零改动 */
  glyph?: 'check' | 'bang' | 'cross' | 'info' | 'dot'
  /** 自定义 aria-label（不传则自动拼装） */
  ariaLabel?: string
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

// ─── CommandPalette（v1.6.0 P1-1 增强）──────
export type CommandScope = 'global' | 'chat' | 'monitor' | 'rag'

/** 命令分组（v1.6.0 P1-1 新增：面板内按组渲染） */
export type CommandGroup = 'routes' | 'actions' | 'context'

export interface CommandItem {
  id: string
  /** 分组（v1.6.0 P1-1 新增）：路由跳转 / 常用操作 / 上下文命令 */
  group: CommandGroup
  scope: CommandScope
  title: string
  subtitle?: string
  shortcut?: string[]
  icon?: string
  /** 搜索关键词：中文 / 拼音首字母 / 英文，如 ['监控','jk','monitor'] */
  keywords?: string[]
  action: () => void | Promise<void>
  disabled?: boolean
}

export interface CommandPaletteProps {
  open: boolean
  scope?: CommandScope
}

// ═══════════════════════════════════════════════════════
// v1.6.0 P1-2 帮助中心类型
// ═══════════════════════════════════════════════════════

/** 帮助文章元信息（web/public/help/manifest.json 白名单驱动） */
export interface HelpArticleMeta {
  id: string
  title: string
  /** 运行时 fetch 路径，如 '/help/architecture.md' */
  path: string
  summary: string
  keywords: string[]
  order: number
}

/** 全文搜索命中项 */
export interface SearchHit {
  articleId: string
  type: 'title' | 'heading' | 'body'
  /** 命中标题 / 章节标题 / 正文片段 */
  text: string
  /** 命中片段（含 <mark> 高亮标记） */
  snippet: string
  score: number
}

/** Markdown 渲染器抽取的标题（目录用） */
export interface MarkdownHeading {
  level: number
  text: string
  id: string
}

// ═══════════════════════════════════════════════════════
// v1.6.0 P1-3 Session 可观测类型
// ═══════════════════════════════════════════════════════

/** 徽标 4 态（由 reasoning 8 态聚合：idle+completed+aborted→idle；running+resuming→running；paused+editing→paused；error→error） */
export type SessionViewStatus = 'idle' | 'running' | 'paused' | 'error'

/** 步骤时间线视图（由 reasoning.steps 派生） */
export interface SessionStepView {
  /** 客户端稳定 id（对应 reasoning.step.id） */
  id: string
  index: number
  name: string
  nodeName: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'edited'
  durationMs: number | null
  /** 该步 token（后端提供才非空；当前降级为 null） */
  tokens: number | null
  startedAt: string
}

/** 可回滚 checkpoint 视图（GET /sessions/{id}/checkpoints 映射） */
export interface SessionCheckpointView {
  stepIndex: number
  stepId: string
  name: string
  checkpointId: string
  createdAt: string
  isEditable: boolean
}

/** Session 详情聚合视图（sessionStats store 组装） */
export interface SessionStats {
  viewStatus: SessionViewStatus
  sessionId: string
  elapsedMs: number
  totalSteps: number
  completedSteps: number
  /** null = 待接入（降级展示为"步骤数 + 耗时"） */
  totalTokens: number | null
  checkpoints: SessionCheckpointView[] | null
  errorMessage: string
}

// ═══════════════════════════════════════════════════════
// v1.6.0 P1-4 KG 灰度可视化类型
// ═══════════════════════════════════════════════════════

export type GrayscaleNodeType = 'backend' | 'candidate' | 'alarm' | 'metric' | 'checkpoint'
export type GrayscaleMode = 'explore' | 'plan'

export interface GrayscaleGraphNode {
  id: string
  name: string
  type: GrayscaleNodeType
  /** 0-100 → 节点大小 */
  load: number
  /** 0-1 → 节点颜色（status 色阶） */
  errorRate: number
  status: 'active' | 'candidate' | 'excluded'
  meta?: Record<string, unknown>
}

export interface GrayscaleGraphEdge {
  source: string
  target: string
  label?: string
  weight?: number
}

export interface GrayscaleGraph {
  nodes: GrayscaleGraphNode[]
  edges: GrayscaleGraphEdge[]
}

export type GrayscalePlanDimension = 'switchCount' | 'loadRate' | 'protectionFit'

export interface GrayscalePlanScore {
  dimension: GrayscalePlanDimension
  /** 操作开关数量 / 负载率 / 保护适配性 */
  label: string
  /** 0-100 打分 */
  value: number
  /** 原始值：'3 个' / '62%' / '优' */
  raw: string
}

export interface GrayscalePlan {
  id: string
  /** 方案 A / B / C */
  name: string
  mode: GrayscaleMode
  scores: GrayscalePlanScore[]
  total: number
  /** 应用后切流比例 */
  targetRatio: number
  selectedNodeIds: string[]
  recommended?: boolean
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

// ═══════════════════════════════════════════════════════
// v1.5.0 · P0-1 + P0-2 + P0-4 · 显示 / 状态 / 色盲 / 引导
// ═══════════════════════════════════════════════════════

// ─── DisplayMode（P0-1 背景演示/标准开关）────
/** 显示模式：'standard' = 长时间盯盘（背景降噪）/ 'presentation' = 汇报演示（背景全开） */
export type DisplayMode = 'standard' | 'presentation'

/** displayMode 持久化键（架构 §7.1 命名约定：gridmind.{域}.{项}） */
export const DISPLAY_MODE_STORAGE_KEY = 'gridmind.displayMode' as const

/** displayMode 默认值 */
export const DISPLAY_MODE_DEFAULT: DisplayMode = 'standard'

// ─── BackgroundIntensity 持久化键（T02 使用）──
/** bgIntensity 持久化键（T01 仅声明命名空间；T02 才实际写入） */
export const BG_INTENSITY_STORAGE_KEY = 'gridmind.bgIntensity' as const

/** bgIntensity 默认值 */
export const BG_INTENSITY_DEFAULT: BackgroundIntensity = 'off'

// ─── ColorBlindPalette（P0-2 色盲模式）──────
/** 4 套色盲 palette 标识符（kebab-case 风格细分） */
export type ColorBlindPalette =
  | 'default'
  | 'ibm-cb-safe'
  | 'okabe-ito'
  | 'colorbrewer-rdylbu'

/** colorBlindPalette 持久化键 */
export const COLORBLIND_STORAGE_KEY = 'gridmind.colorBlindPalette' as const

/** colorBlindPalette 默认值（主理人决策 #7：默认不启用色盲模式） */
export const COLORBLIND_DEFAULT: ColorBlindPalette = 'default'

/** 色盲 palette 中文标签（用于 ColorBlindModeToggle 展示） */
export const PALETTE_LABEL: Record<ColorBlindPalette, string> = {
  'default': '默认（红绿黄蓝）',
  'ibm-cb-safe': 'IBM 色盲安全',
  'okabe-ito': 'Okabe-Ito（去红绿）',
  'colorbrewer-rdylbu': 'ColorBrewer（高对比）',
}

// ─── Status 四元组（P0-2 状态四重区分）────
/** 状态语义：颜色 + 形状 + 图标 + 文字码四元组的"颜色"维度 */
export type Status = 'normal' | 'warning' | 'critical' | 'info' | 'accent'

/** 形状（StatusIcon / PulseDot 共用） */
export type StatusShape = 'circle' | 'triangle' | 'square' | 'diamond' | 'hexagon'

/** 内嵌字符（StatusIcon / PulseDot glyph） */
export type StatusGlyph = 'check' | 'bang' | 'cross' | 'info' | 'dot'

/** 状态图标语义名（StatusIcon 内部 SVG 渲染查找用） */
export type StatusIconName =
  | 'check-circle'
  | 'exclamation-triangle'
  | 'octagon-x'
  | 'info-square'

/** 文字码（aria-label 拼接 + 屏幕阅读器朗读） */
export type StatusTextCode = 'OK' | '!' | 'X' | 'i' | '*'

/** Status 四元组（语义不依赖颜色，WCAG 2.2 §1.4.1） */
export interface StatusPresentation {
  tone: Status
  shape: StatusShape
  glyph: StatusGlyph
  iconName: StatusIconName
  textCode: StatusTextCode
  palette: ColorBlindPalette
}

/** Status → 四元组映射（不含 tone / palette，运行时由调用方补齐） */
export const STATUS_PRESENTATION: Record<
  Status,
  Omit<StatusPresentation, 'tone' | 'palette'>
> = {
  normal: {
    shape: 'circle',
    glyph: 'check',
    iconName: 'check-circle',
    textCode: 'OK',
  },
  info: {
    shape: 'square',
    glyph: 'info',
    iconName: 'info-square',
    textCode: 'i',
  },
  warning: {
    shape: 'triangle',
    glyph: 'bang',
    iconName: 'exclamation-triangle',
    textCode: '!',
  },
  critical: {
    shape: 'diamond',
    glyph: 'cross',
    iconName: 'octagon-x',
    textCode: 'X',
  },
  accent: {
    shape: 'hexagon',
    glyph: 'dot',
    iconName: 'check-circle',
    textCode: '*',
  },
}

// ═══════════════════════════════════════════════════════
// v1.5.0 · P0-4 · Onboarding 类型（T04 完整使用）
// ═══════════════════════════════════════════════════════

/** 引导场景 ID（4 个固定场景，主理人决策 #1 采纳） */
export type OnboardingScenarioId =
  | 'monitor-overview'
  | 'fault-diagnosis'
  | 'knowledge-rag'
  | 'grayscale-rollout'

/** 引导场景配置（wizard Step 1 渲染 + Step 2 种子消息） */
export interface OnboardingScenario {
  id: OnboardingScenarioId
  title: string
  description: string
  icon: string
  starterMessage: string
}

/**
 * 4 个引导场景的**本地兜底**文案（V1.6 功能介绍知识库化）
 *
 * ⚠️ 从 V1.6 起，场景文案的唯一事实来源是
 * `docs/gridmind-feature-introduction.md` 第 3 章，经知识库由
 * `GET /api/knowledge/feature-intro` 下发；本常量**仅在 API 不可用时兜底**。
 *
 * 改文案请改 Markdown 文档，不要改这里（改这里只影响离线降级态）。
 *
 * 读取入口：`useFeatureIntro().scenarios`
 */
export const ONBOARDING_SCENARIOS_FALLBACK: OnboardingScenario[] = [
  {
    id: 'monitor-overview',
    title: '实时监控全览',
    description: '了解 5 个核心路由分别做什么。',
    icon: 'Monitor',
    starterMessage: '请给我介绍一下 GridMind 的 5 个核心视图',
  },
  {
    id: 'fault-diagnosis',
    title: '故障诊断演练',
    description: '体验三层推理 + HITL 审批闭环。',
    icon: 'FirstAidKit',
    starterMessage: '请诊断 #T1 主变压器的温度异常',
  },
  {
      id: 'knowledge-rag',
      title: '知识库检索',
      description: '试试 Q&A + 引用追溯 + 知识图谱浏览。',
      icon: 'Reading',
      starterMessage: '变压器过载如何处置',
  },
  {
    id: 'grayscale-rollout',
    title: '灰度切换',
    description: '把一个新模型分批上线，逐步放量。',
    icon: 'Switch',
    starterMessage: '我要把 v2 模型灰度切换到 50%',
  },
]

/**
 * @deprecated V1.6 起改名为 {@link ONBOARDING_SCENARIOS_FALLBACK}。
 * 保留别名仅为兼容外部引用，新代码请用 `useFeatureIntro().scenarios`。
 */
export const ONBOARDING_SCENARIOS: OnboardingScenario[] = ONBOARDING_SCENARIOS_FALLBACK

/** onboarding store 状态契约（store 实现见 stores/onboarding.ts） */
export interface OnboardingState {
  hasOnboarded: boolean
  currentStep: 1 | 2 | 3
  scenarioId: OnboardingScenarioId | null
  startedAt: string | null
  completedAt: string | null
  /** 单页 tour 完成标记：key = tour 名（如 'chat' / 'monitor'） */
  tourStates: Record<string, boolean>
}
