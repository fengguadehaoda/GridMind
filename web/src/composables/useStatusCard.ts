/**
 * composables/useStatusCard.ts · 状态卡片全局单例（T02）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构决策（header-redesign-architecture-2026-08-06 §1.3 + §3.4 + §7.4 + §7.6）：
 *   - 模块级单例（仿 useCommands 模式）：visible / collapsed / data / history 跨组件共享
 *   - App.vue 原有 CPU/MEM/AGT/CLK 模拟逻辑与时钟迁入本文件（M1 阶段模拟，
 *     后续可无缝替换为 metricsStore / 后端接口，StatusCardData 形状不变）
 *   - ⌘K 命令 action_status_card_toggle 懒注册：start() 被 App.vue onMounted 调用时注册，
 *     避免模块顶层在 pinia 未就绪时调用 useCommands() 崩溃
 *   - localStorage 持久化：gridmind.statusCard.visible / gridmind.statusCard.collapsed /
 *     gridmind.statusCard.position（拖动位置 { right, bottom }）
 */
import { computed, ref } from 'vue'
import type { ComputedRef, Ref } from 'vue'
import type { StatusCardData, StatusMetricSample } from '@/types/header'
import { useCommands } from '@/composables/useCommands'

/** 持久化键（架构 §7.7 命名约定：gridmind.{域}.{项}） */
const VISIBLE_KEY = 'gridmind.statusCard.visible'
const COLLAPSED_KEY = 'gridmind.statusCard.collapsed'
const POSITION_KEY = 'gridmind.statusCard.position'

/** 指标模拟刷新间隔（5s，与 App.vue 原逻辑一致） */
const METRICS_INTERVAL_MS = 5_000
/** 时钟刷新间隔（1s） */
const CLOCK_INTERVAL_MS = 1_000
/**
 * 趋势采样间隔：架构 §7.4 描述为「每 5min 采样一次，保留 12 点（近 1h）」。
 * M1 模拟阶段缩短为 30s，便于首屏即可看到趋势曲线；接入真实 metrics 后
 * 按 5min/12 点回填即可，StatusCardData / StatusMetricSample 形状不变。
 */
const SAMPLE_INTERVAL_MS = 30_000
/** 历史环形缓冲上限（近 1h 12 点） */
const HISTORY_LIMIT = 12

/** 读取持久化布尔值（隐私模式 / SSR 场景静默降级） */
function readBoolean(key: string, fallback: boolean): boolean {
  if (typeof window === 'undefined') return fallback
  try {
    const raw = window.localStorage.getItem(key)
    return raw === null ? fallback : raw === 'true'
  } catch {
    return fallback
  }
}

/** 写入持久化布尔值（静默失败） */
function writeBoolean(key: string, value: boolean): void {
  try {
    window.localStorage.setItem(key, String(value))
  } catch {
    /* 隐私模式等场景静默失败 */
  }
}

/** 读取持久化 JSON（隐私模式 / SSR 场景静默降级） */
function readJSON<T>(key: string, fallback: T): T {
  if (typeof window === 'undefined') return fallback
  try {
    const raw = window.localStorage.getItem(key)
    return raw === null ? fallback : (JSON.parse(raw) as T)
  } catch {
    return fallback
  }
}

/** 写入持久化 JSON（静默失败） */
function writeJSON(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value))
  } catch {
    /* 隐私模式等场景静默失败 */
  }
}

/** 卡片位置（right/bottom 相对视口右下角，单位 px） */
export interface StatusCardPosition {
  right: number
  bottom: number
}

/** 归一化位置：非有限数 / 负数兜底为 0，避免损坏的持久化数据破坏布局 */
function normalizePosition(p: StatusCardPosition): StatusCardPosition {
  const right = Number.isFinite(p.right) && p.right >= 0 ? p.right : 0
  const bottom = Number.isFinite(p.bottom) && p.bottom >= 0 ? p.bottom : 0
  return { right, bottom }
}

/** 24h 时钟格式化（与 App.vue 原 formatTime 一致） */
function formatTime(d: Date): string {
  return d.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

// ═══ 模块级单例状态 ═══
const visible = ref<boolean>(readBoolean(VISIBLE_KEY, true))
const collapsed = ref<boolean>(readBoolean(COLLAPSED_KEY, true))
const position = ref<StatusCardPosition>(normalizePosition(readJSON(POSITION_KEY, { right: 16, bottom: 16 })))
const cpu = ref<number>(23)
const mem = ref<number>(41)
const ait = ref<number>(4)
const clk = ref<string>(formatTime(new Date()))
const serviceConnected = ref<boolean>(false)
const history = ref<StatusMetricSample[]>([])

let clockTimer: ReturnType<typeof setInterval> | null = null
let metricsTimer: ReturnType<typeof setInterval> | null = null
let sampleTimer: ReturnType<typeof setInterval> | null = null
let started = false
let commandRegistered = false

/** 模拟真实波动：CPU 18-40, MEM 35-55（架构 §7.4，与 App.vue 原逻辑一致） */
function updateMetrics(): void {
  cpu.value = 18 + Math.random() * 22
  mem.value = 35 + Math.random() * 20
}

/** 采样：环形覆盖，保留最近 12 点（近 1h） */
function sampleHistory(): void {
  history.value.push({ t: Date.now(), cpu: cpu.value, mem: mem.value })
  if (history.value.length > HISTORY_LIMIT) {
    history.value.splice(0, history.value.length - HISTORY_LIMIT)
  }
}

/** 聚合数据（computed，跨组件共享同一引用） */
const data = computed<StatusCardData>(() => ({
  cpu: cpu.value,
  mem: mem.value,
  ait: ait.value,
  clk: clk.value,
  serviceConnected: serviceConnected.value,
}))

/** 显隐切换（⌘K 命令 action_status_card_toggle 调用） */
function toggleVisible(): void {
  visible.value = !visible.value
  writeBoolean(VISIBLE_KEY, visible.value)
}

/** 折叠 / 展开切换 */
function toggleCollapsed(): void {
  collapsed.value = !collapsed.value
  writeBoolean(COLLAPSED_KEY, collapsed.value)
}

/** 展开态「隐藏」→ visible=false + 持久化 */
function hide(): void {
  visible.value = false
  writeBoolean(VISIBLE_KEY, false)
}

/** 更新卡片位置（拖动 / 键盘微调调用；仅更新 position，不触发展开/折叠/显隐）+ 持久化 */
function setPosition(p: StatusCardPosition): void {
  position.value = normalizePosition(p)
  writeJSON(POSITION_KEY, position.value)
}

/** 外部（App.vue healthCheck）写入服务连接状态 */
function setServiceConnected(value: boolean): void {
  serviceConnected.value = value
}

/** ⌘K 命令懒注册（架构 §7.6；同 id 幂等由 useCommands.register 保证） */
function ensureCommandRegistered(): void {
  if (commandRegistered) return
  commandRegistered = true
  const { register } = useCommands()
  register({
    id: 'action_status_card_toggle',
    group: 'actions',
    scope: 'global',
    title: '切换状态卡片显示',
    subtitle: '显示 / 隐藏右下角系统状态卡片',
    icon: 'Monitor',
    keywords: ['状态', 'zt', 'status', '卡片', '浮动', 'kpt'],
    action: toggleVisible,
  })
}

/** 启动模拟（幂等）：时钟 1s + 指标 5s + 采样；注册 ⌘K 命令 */
function start(): void {
  if (started) return
  started = true
  ensureCommandRegistered()
  // 首帧数据 + 首次采样（趋势图立即可见）
  clk.value = formatTime(new Date())
  updateMetrics()
  sampleHistory()
  clockTimer = setInterval(() => {
    clk.value = formatTime(new Date())
  }, CLOCK_INTERVAL_MS)
  metricsTimer = setInterval(updateMetrics, METRICS_INTERVAL_MS)
  sampleTimer = setInterval(sampleHistory, SAMPLE_INTERVAL_MS)
}

/** 停止模拟（App onUnmounted 调用，防泄漏） */
function stop(): void {
  started = false
  if (clockTimer) {
    clearInterval(clockTimer)
    clockTimer = null
  }
  if (metricsTimer) {
    clearInterval(metricsTimer)
    metricsTimer = null
  }
  if (sampleTimer) {
    clearInterval(sampleTimer)
    sampleTimer = null
  }
}

/** 使用状态卡片单例（StatusFloatingCard / App.vue / 命令面板共享同一实例） */
export function useStatusCard(): {
  visible: Ref<boolean>
  collapsed: Ref<boolean>
  position: Ref<StatusCardPosition>
  data: ComputedRef<StatusCardData>
  history: Ref<StatusMetricSample[]>
  toggleVisible: () => void
  toggleCollapsed: () => void
  hide: () => void
  setPosition: (p: StatusCardPosition) => void
  setServiceConnected: (value: boolean) => void
  start: () => void
  stop: () => void
} {
  return {
    visible,
    collapsed,
    position,
    data,
    history,
    toggleVisible,
    toggleCollapsed,
    hide,
    setPosition,
    setServiceConnected,
    start,
    stop,
  }
}
