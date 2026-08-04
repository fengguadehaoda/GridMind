<script setup lang="ts">
/**
 * TechBackground · 科技感背景
 * - SVG 网格底纹
 * - 顶部径向光晕（暗主题显眼，亮主题降级）
 * - 双主题自动适配
 */
import { computed } from 'vue'
import { useTheme } from '@/composables/useTheme'
import type { TechBackgroundProps } from '@/types/theme'
import { useReducedMotion } from '@/composables/useReducedMotion'

const props = withDefaults(defineProps<TechBackgroundProps>(), {
  intensity: 'mid',
  showGrid: true,
  showGlow: true,
})

const { isDark } = useTheme()
const prefersReducedMotion = useReducedMotion()

const gridOpacity = computed(() => {
  if (!props.showGrid) return 0
  const base = isDark.value ? 0.6 : 0.4
  const factor = props.intensity === 'low' ? 0.5 : props.intensity === 'high' ? 1 : 0.75
  return base * factor
})

const glowOpacity = computed(() => {
  if (!props.showGlow) return 0
  if (!isDark.value) return 0.15 // 亮主题大幅降级
  return props.intensity === 'low' ? 0.3 : props.intensity === 'high' ? 0.7 : 0.5
})

const gridColor = computed(() => 'var(--grid-color)')
</script>

<template>
  <div class="gm-tech-bg" aria-hidden="true">
    <svg
      v-if="showGrid"
      class="gm-tech-bg__grid"
      xmlns="http://www.w3.org/2000/svg"
      :style="{ opacity: gridOpacity }"
    >
      <defs>
        <pattern id="gm-grid-pattern" width="32" height="32" patternUnits="userSpaceOnUse">
          <path
            d="M 32 0 L 0 0 0 32"
            fill="none"
            :stroke="gridColor"
            stroke-width="1"
          />
        </pattern>
        <pattern id="gm-grid-pattern-major" width="128" height="128" patternUnits="userSpaceOnUse">
          <path
            d="M 128 0 L 0 0 0 128"
            fill="none"
            :stroke="gridColor"
            stroke-width="1.5"
            stroke-opacity="0.6"
          />
        </pattern>
      </defs>
      <rect width="100%" height="100%" fill="url(#gm-grid-pattern)" />
      <rect width="100%" height="100%" fill="url(#gm-grid-pattern-major)" />
    </svg>

    <div
      v-if="showGlow"
      class="gm-tech-bg__glow"
      :class="{ 'gm-tech-bg__glow--animated': !prefersReducedMotion }"
      :style="{ opacity: glowOpacity }"
    />
  </div>
</template>

<style scoped>
.gm-tech-bg {
  position: absolute;
  inset: 0;
  z-index: var(--z-base);
  pointer-events: none;
  overflow: hidden;
}

.gm-tech-bg__grid {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}

.gm-tech-bg__glow {
  position: absolute;
  inset: 0;
  background: radial-gradient(
    ellipse 80% 50% at 50% 0%,
    var(--brand-primary) 0%,
    transparent 60%
  );
  transition: var(--theme-transition);
}

.gm-tech-bg__glow--animated {
  animation: gm-glow-pulse 8s var(--ease-in-out-cubic) infinite;
}

@keyframes gm-glow-pulse {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.5;
  }
}
</style>
