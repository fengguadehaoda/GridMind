<template>
  <div ref="wrapRef" class="telemetry-chart" @mousemove="onMove" @mouseleave="onLeave">
    <!-- 空数据占位 -->
    <div v-if="!data.length" class="chart-empty">暂无遥测数据</div>

    <template v-else>
      <svg
        class="chart-svg"
        viewBox="0 0 800 300"
        preserveAspectRatio="xMidYMid meet"
      >
        <!-- 网格线 -->
        <line
          v-for="t in yTicks"
          :key="t"
          :x1="PAD_L"
          :x2="W - PAD_R"
          :y1="yTick(t)"
          :y2="yTick(t)"
          class="grid-line"
        />

        <!-- 指标折线 -->
        <polyline
          v-for="m in metrics"
          :key="m.key"
          class="chart-line"
          :points="linePoints(m)"
          fill="none"
          stroke-width="2"
          stroke-linejoin="round"
          stroke-linecap="round"
          :stroke="`var(${m.cssVar})`"
        />

        <!-- 时间刻度 -->
        <text
          v-for="t in xTicks"
          :key="t.label"
          class="axis-label"
          :x="t.x"
          :y="H - 6"
          text-anchor="middle"
        >{{ t.label }}</text>

        <!-- 悬浮十字线与数据点 -->
        <template v-if="hoverIdx >= 0">
          <line
            class="crosshair"
            :x1="hoverX"
            :x2="hoverX"
            :y1="PAD_T"
            :y2="H - PAD_B"
          />
          <circle
            v-for="m in metrics"
            :key="m.key"
            class="hover-dot"
            :cx="hoverX"
            :cy="metricY(m, hoverIdx)"
            :r="3"
            :fill="`var(${m.cssVar})`"
          />
        </template>
      </svg>

      <!-- 悬浮提示 -->
      <div v-if="hoverIdx >= 0" class="chart-tooltip" :style="tooltipStyle">
        <div class="tooltip-time">{{ hoverTime }}</div>
        <div v-for="m in metrics" :key="m.key" class="tooltip-row">
          <span class="tooltip-dot" :style="{ background: `var(${m.cssVar})` }"></span>
          <span class="tooltip-label">{{ m.label }}</span>
          <span class="tooltip-value">{{ metricText(m, hoverIdx) }}</span>
        </div>
      </div>

      <!-- 图例 -->
      <div class="chart-legend">
        <div v-for="m in metrics" :key="m.key" class="legend-item">
          <span class="legend-dot" :style="{ background: `var(${m.cssVar})` }"></span>
          <span class="legend-name">{{ m.label }}</span>
          <span class="legend-range">{{ legendText(m) }}</span>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { TelemetryReading } from '../types'

const props = defineProps<{ telemetry: TelemetryReading[] }>()

/* ── 图表几何参数 ─────────────────── */
const W = 800
const H = 300
const PAD_L = 12
const PAD_R = 12
const PAD_T = 14
const PAD_B = 30
const PLOT_W = W - PAD_L - PAD_R
const PLOT_H = H - PAD_T - PAD_B

/** 指标定义：key 与遥测字段一一对应，颜色走 CSS 变量（双主题自适应）*/
interface Metric {
  key: 'temperature' | 'voltage' | 'current_load' | 'humidity' | 'pressure'
  label: string
  unit: string
  cssVar: string
  digits: number
}

const metrics: Metric[] = [
  { key: 'temperature', label: '温度', unit: '°C', cssVar: '--metric-temperature', digits: 1 },
  { key: 'voltage', label: '电压', unit: 'kV', cssVar: '--metric-voltage', digits: 2 },
  { key: 'current_load', label: '负载', unit: '%', cssVar: '--metric-current', digits: 1 },
  { key: 'humidity', label: '湿度', unit: '%', cssVar: '--metric-humidity', digits: 1 },
  { key: 'pressure', label: '压力', unit: 'MPa', cssVar: '--metric-pressure', digits: 2 },
]

// 按时间升序排列（接口返回 DESC）
const data = computed<TelemetryReading[]>(() =>
  [...props.telemetry].sort((a, b) => (a.timestamp < b.timestamp ? -1 : 1)),
)
const n = computed(() => data.value.length)

/* ── 坐标映射 ─────────────────────── */
const xPos = (i: number): number =>
  n.value <= 1 ? PAD_L + PLOT_W / 2 : PAD_L + (i / (n.value - 1)) * PLOT_W

function metricValues(m: Metric): number[] {
  return data.value
    .map((d) => d[m.key])
    .filter((v): v is number => v !== undefined)
}

function dataRange(m: Metric): { min: number; max: number } | null {
  const nums = metricValues(m)
  if (!nums.length) return null
  return { min: Math.min(...nums), max: Math.max(...nums) }
}

function plotRange(m: Metric): { min: number; max: number } {
  const r = dataRange(m)
  if (!r) return { min: 0, max: 1 }
  if (r.max - r.min < 1e-9) return { min: r.min - 1, max: r.max + 1 }
  const pad = (r.max - r.min) * 0.1
  return { min: r.min - pad, max: r.max + pad }
}

function metricY(m: Metric, i: number): number {
  const v = data.value[i]?.[m.key]
  if (v === undefined) return PAD_T + PLOT_H
  const { min, max } = plotRange(m)
  return PAD_T + (1 - (v - min) / (max - min)) * PLOT_H
}

function linePoints(m: Metric): string {
  return data.value
    .map((d, i) => (d[m.key] === undefined ? '' : `${xPos(i).toFixed(1)},${metricY(m, i).toFixed(1)}`))
    .filter(Boolean)
    .join(' ')
}

/* ── 刻度 ─────────────────────────── */
const yTicks = [0, 0.25, 0.5, 0.75, 1]
const yTick = (f: number): number => PAD_T + (1 - f) * PLOT_H

const xTicks = computed(() => {
  const count = Math.min(6, n.value)
  const ticks: { x: number; label: string }[] = []
  for (let i = 0; i < count; i++) {
    const idx = n.value <= 1 ? 0 : Math.round((i / (count - 1)) * (n.value - 1))
    const label = data.value[idx]?.timestamp.slice(11, 16) ?? ''
    ticks.push({ x: xPos(idx), label })
  }
  return ticks
})

/* ── 悬浮交互 ─────────────────────── */
const wrapRef = ref<HTMLElement | null>(null)
const hoverIdx = ref(-1)

function onMove(e: MouseEvent) {
  if (!wrapRef.value || !n.value) return
  const rect = wrapRef.value.getBoundingClientRect()
  const svgX = ((e.clientX - rect.left) / rect.width) * W
  let best = 0
  let bestDist = Infinity
  for (let i = 0; i < n.value; i++) {
    const d = Math.abs(xPos(i) - svgX)
    if (d < bestDist) {
      bestDist = d
      best = i
    }
  }
  hoverIdx.value = best
}

function onLeave() {
  hoverIdx.value = -1
}

const hoverX = computed(() => (hoverIdx.value >= 0 ? xPos(hoverIdx.value) : 0))
const hoverTime = computed(() => data.value[hoverIdx.value]?.timestamp ?? '')

const tooltipStyle = computed(() => {
  const flip = hoverX.value > W * 0.55
  return {
    left: `${(hoverX.value / W) * 100}%`,
    top: `${(PAD_T / H) * 100}%`,
    transform: `translate(${flip ? 'calc(-100% - 12px)' : '12px'}, -12px)`,
  }
})

function metricText(m: Metric, i: number): string {
  const v = data.value[i]?.[m.key]
  return v === undefined ? '—' : `${v.toFixed(m.digits)} ${m.unit}`
}

function legendText(m: Metric): string {
  const last = data.value[data.value.length - 1]?.[m.key]
  const r = dataRange(m)
  if (!r) return '—'
  const range = `${r.min.toFixed(m.digits)}~${r.max.toFixed(m.digits)} ${m.unit}`
  return last === undefined ? range : `最新 ${last.toFixed(m.digits)} · ${range}`
}
</script>

<style scoped>
.telemetry-chart {
  position: relative;
  width: 100%;
  user-select: none;
}

.chart-svg {
  display: block;
  width: 100%;
  height: auto;
}

/* ── 网格与坐标 ───────────────────── */
.grid-line {
  stroke: var(--border-muted);
  stroke-width: 1;
  stroke-dasharray: 4 4;
}

.axis-label {
  fill: var(--text-muted);
  font-size: 11px;
  font-family: var(--font-mono);
}

.chart-line {
  opacity: 0.9;
  transition: stroke var(--dur-base) var(--ease-in-out-cubic);
}

.chart-line:hover {
  opacity: 1;
  filter: drop-shadow(0 0 4px currentColor);
}

.crosshair {
  stroke: var(--text-secondary);
  stroke-width: 1;
  stroke-dasharray: 3 3;
  opacity: 0.5;
}

.hover-dot {
  stroke: var(--bg-elevated);
  stroke-width: 1.5;
}

/* ── 悬浮提示 ─────────────────────── */
.chart-tooltip {
  position: absolute;
  min-width: 160px;
  padding: var(--space-2) var(--space-3);
  background: var(--glass-bg);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow-card);
  backdrop-filter: blur(var(--glass-blur));
  -webkit-backdrop-filter: blur(var(--glass-blur));
  pointer-events: none;
  z-index: var(--z-sticky);
}

.tooltip-time {
  font-size: var(--fs-xs);
  color: var(--text-muted);
  font-family: var(--font-mono);
  margin-bottom: var(--space-2);
  padding-bottom: var(--space-1);
  border-bottom: 1px solid var(--border-muted);
}

.tooltip-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--fs-xs);
  padding: 2px 0;
}

.tooltip-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.tooltip-label {
  color: var(--text-secondary);
}

.tooltip-value {
  margin-left: auto;
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-weight: var(--fw-semibold);
  padding-left: var(--space-3);
}

/* ── 图例 ─────────────────────────── */
.chart-legend {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-4);
  margin-top: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--border-muted);
}

.legend-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--fs-xs);
}

.legend-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.legend-name {
  color: var(--text-primary);
  font-weight: var(--fw-medium);
  font-family: var(--font-cn);
}

.legend-range {
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 10px;
}

/* ── 空态 ─────────────────────────── */
.chart-empty {
  height: 180px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  font-size: var(--fs-sm);
  border: 1px dashed var(--border-default);
  border-radius: var(--radius-sm);
  font-family: var(--font-cn);
}
</style>
