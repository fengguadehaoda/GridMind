<script setup lang="ts">
/**
 * ScanlineOverlay · CRT 扫描线效果
 * 亮主题自动降级（CSS 变量 --scanline-opacity = 0 时不可见）
 */
import { computed } from 'vue'
import { useTheme } from '@/composables/useTheme'
import type { ScanlineOverlayProps } from '@/types/theme'
import { useReducedMotion } from '@/composables/useReducedMotion'

const props = withDefaults(defineProps<ScanlineOverlayProps>(), {
  opacity: 0,
  speed: 8,
  forceOff: false,
})

const { isDark } = useTheme()
const prefersReducedMotion = useReducedMotion()

const finalOpacity = computed(() => {
  if (props.forceOff) return 0
  if (!isDark.value) return 0
  return props.opacity || 0.6
})

const isVisible = computed(() => finalOpacity.value > 0 && !prefersReducedMotion.value)
</script>

<template>
  <div
    v-if="isVisible"
    class="gm-scanline"
    aria-hidden="true"
    :style="{
      '--scanline-final-opacity': finalOpacity,
      '--scanline-final-speed': `${speed}s`,
    }"
  />
</template>

<style scoped>
.gm-scanline {
  position: absolute;
  inset: 0;
  z-index: var(--z-base);
  pointer-events: none;
  background: repeating-linear-gradient(
    to bottom,
    transparent 0,
    transparent 2px,
    var(--scanline-color) 2px,
    var(--scanline-color) 3px
  );
  opacity: var(--scanline-final-opacity);
  mix-blend-mode: overlay;
}

.gm-scanline::before {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(
    180deg,
    transparent 0%,
    var(--brand-primary) 50%,
    transparent 100%
  );
  opacity: 0.15;
  animation: gm-scanline-sweep var(--scanline-final-speed) linear infinite;
}

@keyframes gm-scanline-sweep {
  0% {
    transform: translateY(-100%);
  }
  100% {
    transform: translateY(100%);
  }
}
</style>
