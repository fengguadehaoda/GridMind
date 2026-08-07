<script setup lang="ts">
/**
 * StatusIcon · 状态图标（v1.5.0 P0-2 状态四重区分）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 设计：
 *   1. 内嵌 SVG，不引图标库（架构 §1.2 + 任务约束 #3）
 *   2. 5 状态 × 5 形状 = 25 SVG 组合（4 形状常用 + 六边形 accent）
 *      默认按 STATUS_PRESENTATION 映射；支持 shape/glyph 覆盖
 *   3. 颜色 token: --cb-status-{tone}-{fg|soft}（由 :root[data-cb-palette] 路由）
 *   4. 自动 aria-label: "状态：{中文}（{形状} + {字符}）"
 *      符合 WCAG 2.2 §1.4.1（不仅靠颜色）+ §4.1.2（语义化标签）
 *   5. role="img"，可被 aria-hidden="true" 包裹（外层有更全描述时）
 */
import { computed } from 'vue'
import type { Status, StatusGlyph, StatusShape } from '@/types/theme'
import { STATUS_PRESENTATION } from '@/types/theme'

/* ── Props（向后兼容，size 默认 18，shape/glyph 均可选覆盖）── */
const props = withDefaults(
  defineProps<{
    /** 状态语义（必传） */
    status: Status
    /** 渲染尺寸（px 或 CSS 长度） */
    size?: number | string
    /** 形状覆盖（不传则按 STATUS_PRESENTATION 映射） */
    shape?: StatusShape
    /** 内部字符覆盖（不传则按 STATUS_PRESENTATION 映射） */
    glyph?: StatusGlyph
    /** 自定义 aria-label（不传则自动拼装） */
    ariaLabel?: string
  }>(),
  {
    size: 18,
    shape: undefined,
    glyph: undefined,
    ariaLabel: undefined,
  },
)

/* ── 默认 shape / glyph（查表） ────────────────── */
const resolvedShape = computed<StatusShape>(
  () => props.shape ?? STATUS_PRESENTATION[props.status].shape,
)
const resolvedGlyph = computed<StatusGlyph>(
  () => props.glyph ?? STATUS_PRESENTATION[props.status].glyph,
)

/* ── 中文映射（aria-label 拼装用） ─────────────── */
const TONE_ZH: Record<Status, string> = {
  normal: '正常',
  warning: '警告',
  critical: '严重',
  info: '信息',
  accent: '重点',
}

const SHAPE_ZH: Record<StatusShape, string> = {
  circle: '圆形',
  triangle: '三角',
  square: '方形',
  diamond: '菱形',
  hexagon: '六边',
}

const GLYPH_CHAR: Record<StatusGlyph, string> = {
  check: '\u2713', // ✓
  bang: '!', // !
  cross: '\u00D7', // ×
  info: 'i', // i
  dot: '\u2022', // •
}

/* ── aria-label 拼装 ──────────────────────────── */
const autoLabel = computed(
  () =>
    `状态：${TONE_ZH[props.status]}（${SHAPE_ZH[resolvedShape.value]} + ${GLYPH_CHAR[resolvedGlyph.value]}）`,
)
const ariaLabel = computed(() => props.ariaLabel ?? autoLabel.value)

/* ── SVG path 库（24×24 viewBox，居中，外轮廓） ──── */
const SHAPE_PATH: Record<StatusShape, string> = {
  // 圆（外接 24×24，r=10，cx/cy=12）
  circle: 'M12 2 a10 10 0 1 0 0.001 0 z',
  // 等边三角（顶点 12,2.5；底边 2.5,20 - 21.5,20）
  triangle: 'M12 2.5 L21.5 20 L2.5 20 Z',
  // 圆角方形（4×4 边距，rx=2 让 HUD 风不死板）
  square: 'M5 4 H19 A1 1 0 0 1 20 5 V19 A1 1 0 0 1 19 20 H5 A1 1 0 0 1 4 19 V5 A1 1 0 0 1 5 4 Z',
  // 菱形（顶点 12,2/22,12/12,22/2,12）
  diamond: 'M12 2 L22 12 L12 22 L2 12 Z',
  // 正六边（pointy-top 风格，参考 tokens.shared $clip-hex 缩放）
  hexagon: 'M7 2.5 H17 L22 12 L17 21.5 H7 L2 12 Z',
}
</script>

<template>
  <svg
    class="gm-status-icon"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    :aria-label="ariaLabel"
    role="img"
    xmlns="http://www.w3.org/2000/svg"
  >
    <!-- 外轮廓（fg 描边 + soft 填充） -->
    <path
      :d="SHAPE_PATH[resolvedShape]"
      :class="`gm-status-icon__shape gm-status-icon__shape--${props.status}`"
      stroke-linejoin="round"
      stroke-linecap="round"
      stroke-width="1.75"
    />
    <!-- 内字符（fg 填充，居中） -->
    <text
      class="gm-status-icon__glyph"
      x="12"
      y="12"
      text-anchor="middle"
      dominant-baseline="central"
    >{{ GLYPH_CHAR[resolvedGlyph] }}</text>
  </svg>
</template>

<style scoped>
/* ── 容器 ───────────────────────────────────── */
.gm-status-icon {
  display: inline-block;
  flex-shrink: 0;
  vertical-align: middle;
  /* 防止 SVG 内部 transition 触发外部 reflow（架构 §7.4 性能要求） */
  transform: translateZ(0);
}

/* ── 外轮廓：soft 填充 + fg 描边（由 palette 路由颜色） ── */
.gm-status-icon__shape {
  fill: var(--cb-status-normal-soft, var(--status-info-soft));
  stroke: var(--cb-status-normal-fg, var(--status-info));
  transition: fill var(--dur-fast) var(--ease-out-quint),
              stroke var(--dur-fast) var(--ease-out-quint);
}

/* ── 各 tone 路由：--cb-status-{tone}-{fg|soft} ── */
.gm-status-icon__shape--normal {
  fill: var(--cb-status-normal-soft);
  stroke: var(--cb-status-normal-fg);
}
.gm-status-icon__shape--warning {
  fill: var(--cb-status-warning-soft);
  stroke: var(--cb-status-warning-fg);
}
.gm-status-icon__shape--critical {
  fill: var(--cb-status-critical-soft);
  stroke: var(--cb-status-critical-fg);
}
.gm-status-icon__shape--info {
  fill: var(--cb-status-info-soft);
  stroke: var(--cb-status-info-fg);
}
.gm-status-icon__shape--accent {
  fill: var(--cb-status-accent-soft);
  stroke: var(--cb-status-accent-fg);
}

/* ── 内字符：fg 填充（与轮廓同色，确保对比） ───── */
.gm-status-icon__glyph {
  fill: var(--cb-status-normal-fg, var(--status-info));
  font-family: var(--font-mono, 'JetBrains Mono', ui-monospace, monospace);
  font-size: 12px;
  font-weight: var(--fw-bold, 700);
  pointer-events: none;
  user-select: none;
  /* 字符无 transition：避免 palette 切换时字符"变色"动画 */
}
.gm-status-icon__shape--normal + .gm-status-icon__glyph { fill: var(--cb-status-normal-fg); }
.gm-status-icon__shape--warning + .gm-status-icon__glyph { fill: var(--cb-status-warning-fg); }
.gm-status-icon__shape--critical + .gm-status-icon__glyph { fill: var(--cb-status-critical-fg); }
.gm-status-icon__shape--info + .gm-status-icon__glyph { fill: var(--cb-status-info-fg); }
.gm-status-icon__shape--accent + .gm-status-icon__glyph { fill: var(--cb-status-accent-fg); }

/* ── 减弱动效偏好：去掉 transition（仅保留瞬时切换）── */
@media (prefers-reduced-motion: reduce) {
  .gm-status-icon__shape {
    transition: none;
  }
}
</style>
