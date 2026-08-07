<script setup lang="ts">
/**
 * PulseDot · 脉冲状态点（v1.5.0 P0-2 状态四重区分）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * v1.4.0 → v1.5.0 升级（架构 §3.1 + §5 T03 任务约束 #4）：
 *   1. 新增可选 prop：shape（circle/triangle/square/diamond/hexagon）
 *   2. 新增可选 prop：glyph（check/bang/cross/info/dot）
 *   3. **向后兼容**：默认 'circle' + 'dot'，旧调用方零改动
 *   4. tone 从 'success/danger/...' 改为 v1.5.0 标准 'normal/warning/critical/info/accent'
 *      （仍兼容旧 tone 字符串，自动映射；如未来清理旧 tone，可移除 fallback）
 *   5. 颜色路由到 --cb-status-{tone}-{fg|soft}，palette 切换瞬时跟随
 *   6. aria-label 自动拼装：状态：{中文}（{shape} + {glyph}）
 */
import { computed } from 'vue'
import type { PulseDotProps } from '@/types/theme'
import { STATUS_PRESENTATION } from '@/types/theme'
import { useReducedMotion } from '@/composables/useReducedMotion'

/* ── Props（含可选 shape/glyph，默认值 = 旧行为）── */
const props = withDefaults(defineProps<PulseDotProps>(), {
  tone: 'info',
  size: 8,
  speed: 2,
  shape: 'circle',
  glyph: 'dot',
})

/* ── 兼容旧 tone（'success' → 'normal', 'danger' → 'critical'）── */
const TONE_MAP: Record<string, 'normal' | 'warning' | 'critical' | 'info' | 'accent'> = {
  success: 'normal',
  danger: 'critical',
  warning: 'warning',
  info: 'info',
  accent: 'accent',
}
const resolvedTone = computed(() => TONE_MAP[props.tone ?? 'info'] ?? 'info')

/* ── 当前 tone 的四元组（参考，便于 aria 拼装）── */
const presentation = computed(() => STATUS_PRESENTATION[resolvedTone.value])

/* ── 颜色 token（--cb-status-{tone}-{fg|soft}）── */
const colorVar = computed(() => `var(--cb-status-${resolvedTone.value}-fg)`)
const softVar = computed(() => `var(--cb-status-${resolvedTone.value}-soft)`)

/* ── aria-label 拼装（中文：状态 + 形状 + 字符）── */
const TONE_ZH: Record<string, string> = {
  normal: '正常',
  warning: '警告',
  critical: '严重',
  info: '信息',
  accent: '重点',
}
const SHAPE_ZH: Record<string, string> = {
  circle: '圆形',
  triangle: '三角',
  square: '方形',
  diamond: '菱形',
  hexagon: '六边',
}
const GLYPH_CHAR: Record<string, string> = {
  check: '\u2713',
  bang: '!',
  cross: '\u00D7',
  info: 'i',
  dot: '\u2022',
}

const autoLabel = computed(
  () =>
    `状态：${TONE_ZH[resolvedTone.value] ?? '未知'}（${SHAPE_ZH[props.shape] ?? ''} + ${
      GLYPH_CHAR[props.glyph] ?? ''
    }）`,
)
const ariaLabel = computed(() => props.ariaLabel ?? autoLabel.value)

/* ── Shape clip-path 库（4 形状 + 1 六边形）── */
const SHAPE_CLIP: Record<string, string> = {
  // 圆形：无 clip
  circle: 'circle(50% at 50% 50%)',
  // 三角（顶点朝上，等边近似）
  triangle: 'polygon(50% 0%, 100% 100%, 0% 100%)',
  // 方形
  square: 'polygon(0 0, 100% 0, 100% 100%, 0 100%)',
  // 菱形
  diamond: 'polygon(50% 0%, 100% 50%, 50% 100%, 0% 50%)',
  // 六边形（pointy-top，与 StatusIcon 一致）
  hexagon: 'polygon(25% 0%, 75% 0%, 100% 50%, 75% 100%, 25% 100%, 0% 50%)',
}
const clipPath = computed(() => SHAPE_CLIP[props.shape ?? 'circle'] ?? 'circle(50% at 50% 50%)')

/* ── Glyph 显示条件：仅当 size 足够大时显示字符（< 10px 不显示避免溢出）── */
const showGlyph = computed(
  () => (props.glyph ?? 'dot') !== 'dot' && (props.size ?? 8) >= 10,
)
const glyphChar = computed(() => GLYPH_CHAR[props.glyph ?? 'dot'] ?? '')

const prefersReducedMotion = useReducedMotion()
</script>

<template>
  <span
    class="gm-pulse-dot"
    :class="[
      `gm-pulse-dot--${props.shape ?? 'circle'}`,
      { 'gm-pulse-dot--animated': !prefersReducedMotion },
    ]"
    :style="{
      width: `${size}px`,
      height: `${size}px`,
      '--pulse-color': colorVar,
      '--pulse-soft': softVar,
      '--pulse-speed': `${speed}s`,
      clipPath,
    }"
    role="status"
    :aria-label="ariaLabel"
  >
    <!-- 内字符（仅非 dot 且 size ≥ 10）-->
    <span
      v-if="showGlyph"
      class="gm-pulse-dot__glyph"
      aria-hidden="true"
    >{{ glyphChar }}</span>
    <!-- 脉冲圈（::after 不支持 clip-path 继承，用 span 实现） -->
    <span
      v-if="!prefersReducedMotion"
      class="gm-pulse-dot__ring"
      :style="{
        clipPath,
      }"
      aria-hidden="true"
    />
  </span>
</template>

<style scoped>
.gm-pulse-dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  position: relative;
  background: var(--pulse-color);
  flex-shrink: 0;
  /* 字体属性（仅 glyph span 使用） */
  font-family: var(--font-mono, 'JetBrains Mono', ui-monospace, monospace);
  font-size: calc(var(--pulse-size, 8) * 0.7);
  font-weight: var(--fw-bold, 700);
  color: var(--text-inverse, #050b1a);
  /* 主题/palette 切换时，颜色瞬时跟随（无 transition 避免回放） */
  transition: background var(--dur-fast) var(--ease-out-quint);
}

/* ── 脉冲圈（仅 animated 时渲染）── */
.gm-pulse-dot__ring {
  position: absolute;
  inset: 0;
  background: var(--pulse-color);
  opacity: 0.6;
  animation: gm-pulse-dot var(--pulse-speed) var(--ease-in-out-cubic) infinite;
  pointer-events: none;
}

/* ── 内字符（check/bang/cross/info 时显示）── */
.gm-pulse-dot__glyph {
  position: relative;
  z-index: 1;
  line-height: 1;
  user-select: none;
  pointer-events: none;
}

/* ── 形状视觉差异：除 circle 外，加 1.5px 内描边增强区分度 ── */
.gm-pulse-dot--triangle,
.gm-pulse-dot--square,
.gm-pulse-dot--diamond,
.gm-pulse-dot--hexagon {
  outline: 1.5px solid var(--pulse-color);
  outline-offset: -1.5px;
}

/* ── 减弱动效偏好：去掉动画（仅保留静态指示）── */
@media (prefers-reduced-motion: reduce) {
  .gm-pulse-dot__ring {
    animation: none !important;
  }
}

@keyframes gm-pulse-dot {
  0% {
    transform: scale(1);
    opacity: 0.6;
  }
  100% {
    transform: scale(3);
    opacity: 0;
  }
}
</style>
