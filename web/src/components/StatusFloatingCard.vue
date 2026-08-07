<script setup lang="ts">
/**
 * StatusFloatingCard.vue · 右下角浮动系统状态卡片（T02 / T04）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构决策（header-redesign-architecture-2026-08-06 §1.2 + §3.4 + §7.1 + §7.2 + §7.4）：
 *   - fixed 定位，right/bottom 由 useStatusCard().position 动态绑定（默认右下 16px，可拖动）
 *   - 折叠态一行：CPU xx% · 内存 xx% · AIT n · CLK HH:mm:ss；点击展开
 *   - 展开态：指标详情 + 原生 SVG 趋势折线（12 采样，不引图表库）+ 服务连接 + 最近活动
 *   - 显隐 / 折叠 / 位置 / 历史采样全部消费 useStatusCard() 单例（与 ⌘K 命令共享）
 *   - 「隐藏」→ useStatusCard.hide() → localStorage 持久化（gridmind.statusCard.visible）
 *   - 拖动：左上角 grip 手柄 mousedown 拖动（clamp 视口内），mouseup setPosition 持久化
 *     （gridmind.statusCard.position）；方向键微调；拖动手势与点击折叠区域分离
 *   - T04：<768px 极窄屏整卡隐藏（CSS media query 纯布局；JS 断点不混写）
 *   - prefers-reduced-motion 降级：关闭 transition/animation
 *
 * 作者：寇豆码（T02 工程师）
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useStatusCard } from '@/composables/useStatusCard'
import StatusIcon from '@/components/controls/StatusIcon.vue'

const props = withDefaults(defineProps<{ connected?: boolean }>(), {
  connected: false,
})

const {
  visible,
  collapsed,
  data,
  history,
  position,
  setPosition,
  toggleCollapsed,
  hide,
} = useStatusCard()

/** 卡片根节点（拖动 clamp 需要实时尺寸） */
const cardRef = ref<HTMLElement | null>(null)

/** 拖动中标记：驱动 .status-card--dragging 视觉 + 抑制拖完的 click */
const dragging = ref(false)

/** 拖动起始快照（鼠标坐标 + 卡片起始 right/bottom + 尺寸） */
interface DragSnapshot {
  mouseX: number
  mouseY: number
  startRight: number
  startBottom: number
  cardWidth: number
  cardHeight: number
}
const dragStart = ref<DragSnapshot | null>(null)

/** 拖完同一轮 click 抑制标记（防止 grip 拖动后误触发折叠/展开） */
const suppressClick = ref(false)

/** 数值夹取到 [min, max] */
function clamp(v: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, v))
}

/** 视口内最大 right/bottom（保证整卡不越界；极小视口兜底 0） */
function maxOffset(): { right: number; bottom: number } {
  const el = cardRef.value
  if (!el) return { right: 0, bottom: 0 }
  return {
    right: Math.max(0, window.innerWidth - el.offsetWidth),
    bottom: Math.max(0, window.innerHeight - el.offsetHeight),
  }
}

/** 拖动手柄 mousedown（仅左键）：记录起点 + 挂全局 move/up 监听 */
function startDrag(e: MouseEvent): void {
  if (e.button !== 0) return
  const el = cardRef.value
  if (!el) return
  dragging.value = true
  suppressClick.value = true
  dragStart.value = {
    mouseX: e.clientX,
    mouseY: e.clientY,
    startRight: position.value.right,
    startBottom: position.value.bottom,
    cardWidth: el.offsetWidth,
    cardHeight: el.offsetHeight,
  }
  // 拖动期间：全局 grabbing 光标 + 禁止选中（约束：不影响其他交互）
  document.body.style.cursor = 'grabbing'
  document.body.style.userSelect = 'none'
  window.addEventListener('mousemove', onDragMove)
  window.addEventListener('mouseup', onDragEnd)
  e.preventDefault()
}

/** mousemove：delta → newRight/newBottom（clamp 视口内），实时更新 position */
function onDragMove(e: MouseEvent): void {
  const snap = dragStart.value
  if (!dragging.value || !snap) return
  const deltaX = e.clientX - snap.mouseX
  const deltaY = e.clientY - snap.mouseY
  const { right: maxRight, bottom: maxBottom } = maxOffset()
  const newRight = clamp(snap.startRight - deltaX, 0, maxRight)
  const newBottom = clamp(snap.startBottom - deltaY, 0, maxBottom)
  position.value = { right: newRight, bottom: newBottom }
}

/** mouseup：卸载全局监听 + 恢复 body 样式 + setPosition 持久化 */
function onDragEnd(): void {
  if (!dragging.value) return
  dragging.value = false
  const snap = dragStart.value
  dragStart.value = null
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
  window.removeEventListener('mousemove', onDragMove)
  window.removeEventListener('mouseup', onDragEnd)
  if (snap) {
    setPosition({ right: position.value.right, bottom: position.value.bottom })
  }
  // 下一宏任务再放行 click：抑制 mouseup 后紧随的 click 误触发折叠/展开
  window.setTimeout(() => {
    suppressClick.value = false
  }, 0)
}

/** 根节点 capture 阶段吞掉拖完同一轮的 click（grip 拖动手势与点击折叠互不干扰） */
function onCardClickCapture(e: MouseEvent): void {
  if (suppressClick.value) {
    e.preventDefault()
    e.stopPropagation()
  }
}

/** 键盘可达（可选增强）：方向键微调位置，Shift 加速到 10px */
function onGripKeydown(e: KeyboardEvent): void {
  const step = e.shiftKey ? 10 : 1
  let { right, bottom } = position.value
  switch (e.key) {
    case 'ArrowLeft':
      right += step
      break
    case 'ArrowRight':
      right -= step
      break
    case 'ArrowUp':
      bottom += step
      break
    case 'ArrowDown':
      bottom -= step
      break
    default:
      return
  }
  e.preventDefault()
  const { right: maxRight, bottom: maxBottom } = maxOffset()
  setPosition({ right: clamp(right, 0, maxRight), bottom: clamp(bottom, 0, maxBottom) })
}

/** 挂载时兜底：持久化位置可能超出当前视口（窗口缩放后），拉回视口内并回写 */
onMounted(() => {
  const cur = position.value
  const { right: maxRight, bottom: maxBottom } = maxOffset()
  const clampedRight = clamp(cur.right, 0, maxRight)
  const clampedBottom = clamp(cur.bottom, 0, maxBottom)
  if (clampedRight !== cur.right || clampedBottom !== cur.bottom) {
    setPosition({ right: clampedRight, bottom: clampedBottom })
  }
})

/** 组件卸载兜底清理（拖到一半切路由时恢复 body 样式并移除监听） */
onUnmounted(() => {
  if (dragging.value) {
    dragging.value = false
    dragStart.value = null
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
    window.removeEventListener('mousemove', onDragMove)
    window.removeEventListener('mouseup', onDragEnd)
  }
})

/** 指标色调（≥85 danger / ≥60 warning / info；架构 §7.4 阈值） */
function toneOf(v: number): 'info' | 'warning' | 'danger' {
  if (v >= 85) return 'danger'
  if (v >= 60) return 'warning'
  return 'info'
}
const cpuTone = computed(() => toneOf(data.value.cpu))
const memTone = computed(() => toneOf(data.value.mem))

/** 展开态服务连接状态：App.vue healthCheck 结果经 prop 传入（架构 §3.4） */
const serviceConnected = computed(() => props.connected)

/** 最近活动时间：最近一次采样点时间，兜底当前时钟 */
const lastActivity = computed(() => {
  const h = history.value
  if (!h.length) return data.value.clk
  return new Date(h[h.length - 1]!.t).toLocaleTimeString('zh-CN', { hour12: false })
})

/** 趋势折线点（历史 <2 点时不渲染）；viewBox 120×44 内归一化 */
function toPoints(key: 'cpu' | 'mem'): string {
  const h = history.value
  if (h.length < 2) return ''
  const W = 120
  const H = 44
  const PAD = 5
  return h
    .map((s, i) => {
      const x = PAD + (i / (h.length - 1)) * (W - PAD * 2)
      const v = Math.min(100, Math.max(0, s[key]))
      const y = H - PAD - (v / 100) * (H - PAD * 2)
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
}
const cpuPoints = computed(() => toPoints('cpu'))
const memPoints = computed(() => toPoints('mem'))
const hasTrend = computed(() => history.value.length >= 2)
</script>

<template>
  <div
    v-if="visible"
    ref="cardRef"
    class="status-card"
    :class="{
      'status-card--expanded': !collapsed,
      'status-card--dragging': dragging,
    }"
    :style="{ right: position.right + 'px', bottom: position.bottom + 'px' }"
    data-test="status-card"
    @click.capture="onCardClickCapture"
  >
    <!-- 折叠态：整行作为可点击按钮（role="button" 避免与展开态真实按钮嵌套） -->
    <div
      v-if="collapsed"
      class="status-card__collapsed"
      data-test="status-card-toggle"
      role="button"
      tabindex="0"
      aria-expanded="false"
      aria-label="系统状态卡片，点击展开详情"
      @click="toggleCollapsed"
      @keydown.enter.prevent="toggleCollapsed"
      @keydown.space.prevent="toggleCollapsed"
    >
      <!-- 拖动手柄：mousedown.stop 与整行点击折叠区域分离；键盘方向键微调 -->
      <span
        class="status-card__grip"
        role="button"
        tabindex="0"
        aria-label="拖动状态卡片"
        title="拖动状态卡片（按住拖动，方向键微调）"
        @mousedown.stop="startDrag"
        @keydown.stop="onGripKeydown"
      >⋮⋮</span>
      <span class="status-card__item" :class="`status-card__item--${cpuTone}`">
        <i class="status-card__dot" aria-hidden="true" />CPU {{ data.cpu.toFixed(0) }}%
      </span>
      <span class="status-card__sep" aria-hidden="true">·</span>
      <span class="status-card__item" :class="`status-card__item--${memTone}`">
        <i class="status-card__dot" aria-hidden="true" />内存 {{ data.mem.toFixed(0) }}%
      </span>
      <span class="status-card__sep" aria-hidden="true">·</span>
      <span class="status-card__item status-card__item--accent">AIT {{ data.ait }}</span>
      <span class="status-card__sep" aria-hidden="true">·</span>
      <span class="status-card__item status-card__item--accent">CLK {{ data.clk }}</span>
    </div>

    <!-- 展开态：详情 + 趋势 + 服务连接 + 最近活动 -->
    <div v-else class="status-card__expanded" data-test="status-card-expanded">
      <div class="status-card__head">
        <!-- 展开态拖动手柄：位于头部左侧，拖完保持展开态位置 -->
        <span
          class="status-card__grip"
          role="button"
          tabindex="0"
          aria-label="拖动状态卡片"
          title="拖动状态卡片（按住拖动，方向键微调）"
          @mousedown.stop="startDrag"
          @keydown.stop="onGripKeydown"
        >⋮⋮</span>
        <span class="status-card__title">系统状态</span>
        <div class="status-card__head-actions">
          <button
            type="button"
            class="status-card__mini-btn"
            data-test="status-card-hide"
            aria-label="隐藏状态卡片"
            title="隐藏状态卡片"
            @click.stop="hide"
          >隐藏</button>
          <button
            type="button"
            class="status-card__mini-btn"
            aria-label="收起状态卡片"
            title="收起状态卡片"
            @click.stop="toggleCollapsed"
          >收起</button>
        </div>
      </div>

      <div class="status-card__metrics">
        <div class="status-card__metric" :class="`status-card__metric--${cpuTone}`">
          <span class="status-card__metric-label">CPU</span>
          <span class="status-card__metric-value">{{ data.cpu.toFixed(0) }}%</span>
        </div>
        <div class="status-card__metric" :class="`status-card__metric--${memTone}`">
          <span class="status-card__metric-label">内存</span>
          <span class="status-card__metric-value">{{ data.mem.toFixed(0) }}%</span>
        </div>
        <div class="status-card__metric status-card__metric--accent">
          <span class="status-card__metric-label">AIT</span>
          <span class="status-card__metric-value">{{ data.ait }}</span>
        </div>
        <div class="status-card__metric status-card__metric--accent">
          <span class="status-card__metric-label">CLK</span>
          <span class="status-card__metric-value">{{ data.clk }}</span>
        </div>
      </div>

      <!-- 原生 SVG 趋势折线（近 1h 12 采样；架构 §1.2 轻量方案） -->
      <div v-if="hasTrend" class="status-card__trend" aria-hidden="true">
        <svg
          class="status-card__trend-svg"
          viewBox="0 0 120 44"
          preserveAspectRatio="none"
          xmlns="http://www.w3.org/2000/svg"
        >
          <polyline
            :points="cpuPoints"
            fill="none"
            stroke="var(--cb-status-info-fg, var(--brand-primary))"
            stroke-width="1.5"
            stroke-linejoin="round"
            stroke-linecap="round"
          />
          <polyline
            :points="memPoints"
            fill="none"
            stroke="var(--cb-status-normal-fg, var(--status-success))"
            stroke-width="1.5"
            stroke-linejoin="round"
            stroke-linecap="round"
          />
        </svg>
        <div class="status-card__trend-legend">
          <span class="status-card__trend-key"><i class="status-card__trend-chip status-card__trend-chip--cpu" />CPU</span>
          <span class="status-card__trend-key"><i class="status-card__trend-chip status-card__trend-chip--mem" />内存</span>
          <span class="status-card__trend-note">近 1h 采样</span>
        </div>
      </div>

      <div class="status-card__foot">
        <StatusIcon
          :status="serviceConnected ? 'normal' : 'critical'"
          :size="12"
          :aria-label="serviceConnected ? '服务已连接' : '服务未连接'"
        />
        <span class="status-card__foot-text" :class="{ 'is-offline': !serviceConnected }">
          {{ serviceConnected ? '服务已连接' : '服务未连接' }}
        </span>
        <span class="status-card__foot-last">最近活动 {{ lastActivity }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
/* ── 主位：fixed，right/bottom 由 useStatusCard().position 动态绑定（默认右下 16px） ── */
.status-card {
  position: fixed;
  z-index: var(--z-sticky);
  max-width: min(520px, calc(100vw - 32px));
  background: var(--bg-elevated);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-md, 0 4px 12px rgba(0, 0, 0, 0.15));
  backdrop-filter: blur(var(--glass-blur, 12px));
  -webkit-backdrop-filter: blur(var(--glass-blur, 12px));
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  letter-spacing: 0.04em;
  user-select: none;
  -webkit-tap-highlight-color: transparent;
  transition: border-color var(--dur-fast) var(--ease-out-quint), box-shadow var(--dur-fast) var(--ease-out-quint);
  outline: none;
}

.status-card:hover {
  border-color: var(--brand-primary);
  box-shadow: var(--glow-primary-soft);
}

.status-card:focus-visible {
  outline: none;
}

/* 折叠态按钮焦点环 */
.status-card__collapsed:focus-visible {
  outline: 2px solid var(--brand-primary);
  outline-offset: 2px;
  border-radius: var(--radius-md);
}

/* ── 拖动手柄（折叠/展开共用；grip 与点击折叠区域分离） ── */
.status-card__grip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  font-size: 10px;
  line-height: 1;
  letter-spacing: -1px;
  cursor: grab;
  user-select: none;
  -webkit-user-select: none;
  transition: color var(--dur-fast) var(--ease-out-quint), background var(--dur-fast) var(--ease-out-quint);
}

.status-card__grip:hover {
  color: var(--brand-primary);
  background: var(--brand-primary-soft, rgba(97, 92, 237, 0.08));
}

.status-card__grip:active {
  cursor: grabbing;
}

.status-card__grip:focus-visible {
  outline: 2px solid var(--brand-primary);
  outline-offset: 1px;
}

/* 拖动中：整卡（含子元素）grabbing 光标 + 强调边框 */
.status-card--dragging,
.status-card--dragging * {
  cursor: grabbing !important;
}

.status-card.status-card--dragging {
  border-color: var(--brand-primary);
  box-shadow: var(--glow-primary-strong, var(--glow-primary-soft));
}

/* ── 折叠态一行 ── */
.status-card__collapsed {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 8px var(--space-3);
  white-space: nowrap;
  cursor: pointer;
}

.status-card__item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-variant-numeric: tabular-nums;
}

.status-card__item--info {
  color: var(--cb-status-info-fg, var(--brand-primary));
}

.status-card__item--warning {
  color: var(--cb-status-warning-fg, var(--status-warning));
}

.status-card__item--danger {
  color: var(--cb-status-critical-fg, var(--status-danger));
}

.status-card__item--accent {
  color: var(--cb-status-accent-fg, var(--brand-accent));
}

.status-card__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 6px currentColor;
  flex-shrink: 0;
}

.status-card__sep {
  color: var(--text-muted);
}

/* ── 展开态 ── */
.status-card__expanded {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-3);
  cursor: default;
}

.status-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}

.status-card__title {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  letter-spacing: 0.1em;
}

.status-card__head-actions {
  display: inline-flex;
  gap: var(--space-2);
}

.status-card__mini-btn {
  padding: 2px var(--space-2);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  color: var(--text-secondary);
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  cursor: pointer;
  transition: all var(--dur-fast) var(--ease-out-quint);
}

.status-card__mini-btn:hover {
  color: var(--brand-primary);
  border-color: var(--brand-primary);
  box-shadow: var(--glow-primary-soft);
}

/* ── 指标详情（2×2） ── */
.status-card__metrics {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--space-2);
}

.status-card__metric {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-2);
  padding: 6px var(--space-2);
  border: 1px solid var(--border-muted);
  border-radius: var(--radius-sm);
  background: var(--bg-card);
}

.status-card__metric-label {
  color: var(--text-muted);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
}

.status-card__metric-value {
  font-weight: var(--fw-semibold);
  font-variant-numeric: tabular-nums;
}

.status-card__metric--info .status-card__metric-value {
  color: var(--cb-status-info-fg, var(--brand-primary));
}
.status-card__metric--warning .status-card__metric-value {
  color: var(--cb-status-warning-fg, var(--status-warning));
}
.status-card__metric--danger .status-card__metric-value {
  color: var(--cb-status-critical-fg, var(--status-danger));
}
.status-card__metric--accent .status-card__metric-value {
  color: var(--cb-status-accent-fg, var(--brand-accent));
}

/* ── 趋势图（原生 SVG） ── */
.status-card__trend {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.status-card__trend-svg {
  width: 100%;
  height: 44px;
  display: block;
  border: 1px solid var(--border-muted);
  border-radius: var(--radius-sm);
  background: var(--bg-card);
}

.status-card__trend-legend {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: 10px;
  color: var(--text-muted);
}

.status-card__trend-key {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.status-card__trend-chip {
  width: 10px;
  height: 2px;
  border-radius: 1px;
}

.status-card__trend-chip--cpu {
  background: var(--cb-status-info-fg, var(--brand-primary));
}

.status-card__trend-chip--mem {
  background: var(--cb-status-normal-fg, var(--status-success));
}

.status-card__trend-note {
  margin-left: auto;
}

/* ── 底部：服务连接 + 最近活动 ── */
.status-card__foot {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding-top: var(--space-2);
  border-top: 1px solid var(--border-muted);
  font-size: 10px;
}

.status-card__foot-text {
  color: var(--cb-status-normal-fg, var(--status-success));
  font-weight: var(--fw-medium);
}

.status-card__foot-text.is-offline {
  color: var(--cb-status-critical-fg, var(--status-danger));
}

.status-card__foot-last {
  margin-left: auto;
  color: var(--text-muted);
}

/* ── T04：极窄屏（<768px）整卡隐藏（CSS media query 纯布局；JS 断点不混写） ── */
@media (max-width: 767.98px) {
  .status-card {
    display: none;
  }
}

/* ── 减少动效偏好 ── */
@media (prefers-reduced-motion: reduce) {
  .status-card {
    transition: none;
  }
  .status-card__grip {
    transition: none;
  }
  .status-card__mini-btn {
    transition: none;
  }
}
</style>
