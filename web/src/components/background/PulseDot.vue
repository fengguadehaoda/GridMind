<script setup lang="ts">
/**
 * PulseDot · 脉冲状态点
 * 5 种 tone：success / danger / warning / info / accent
 */
import { computed } from 'vue'
import type { PulseDotProps } from '@/types/theme'
import { useReducedMotion } from '@/composables/useReducedMotion'

const props = withDefaults(defineProps<PulseDotProps>(), {
  tone: 'info',
  size: 8,
  speed: 2,
})

const toneVar = computed(() => {
  switch (props.tone) {
    case 'success': return 'var(--status-success)'
    case 'danger': return 'var(--status-danger)'
    case 'warning': return 'var(--status-warning)'
    case 'accent': return 'var(--brand-accent)'
    case 'info':
    default: return 'var(--brand-primary)'
  }
})

const prefersReducedMotion = useReducedMotion()
</script>

<template>
  <span
    class="gm-pulse-dot"
    :class="{ 'gm-pulse-dot--animated': !prefersReducedMotion }"
    :style="{
      width: `${size}px`,
      height: `${size}px`,
      '--pulse-color': toneVar,
      '--pulse-speed': `${speed}s`,
    }"
    role="status"
    aria-label="状态指示"
  />
</template>

<style scoped>
.gm-pulse-dot {
  display: inline-block;
  border-radius: 50%;
  background: var(--pulse-color);
  flex-shrink: 0;
  position: relative;
  box-shadow: 0 0 6px var(--pulse-color);
}

.gm-pulse-dot--animated::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 50%;
  background: var(--pulse-color);
  opacity: 0.6;
  animation: gm-pulse-dot var(--pulse-speed) var(--ease-in-out-cubic) infinite;
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
