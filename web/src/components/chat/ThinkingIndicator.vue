<script setup lang="ts">
/**
 * ThinkingIndicator · 思考中动画
 * 替代原 MessageBubble 中的 3 个静态 dots
 */
import type { ThinkingIndicatorProps } from '@/types/theme'
import { useReducedMotion } from '@/composables/useReducedMotion'

withDefaults(defineProps<ThinkingIndicatorProps>(), {
  label: '思考中',
  speed: 1.2,
})

const prefersReducedMotion = useReducedMotion()
</script>

<template>
  <div class="gm-thinking" :style="{ '--thinking-speed': `${speed}s` }">
    <span class="gm-thinking__label">{{ label }}</span>
    <span class="gm-thinking__dots">
      <span v-if="!prefersReducedMotion" class="gm-thinking__dot" />
      <span v-if="!prefersReducedMotion" class="gm-thinking__dot" />
      <span v-if="!prefersReducedMotion" class="gm-thinking__dot" />
      <span v-else class="gm-thinking__dot gm-thinking__dot--static">·</span>
    </span>
  </div>
</template>

<style scoped>
.gm-thinking {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--text-muted);
  font-size: var(--fs-sm);
}

.gm-thinking__label {
  font-family: var(--font-cn);
}

.gm-thinking__dots {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}

.gm-thinking__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--brand-primary);
  animation: gm-thinking var(--thinking-speed) var(--ease-in-out-cubic) infinite;
  box-shadow: 0 0 6px var(--brand-primary-soft);
}

.gm-thinking__dot:nth-child(2) {
  animation-delay: 0.15s;
}

.gm-thinking__dot:nth-child(3) {
  animation-delay: 0.3s;
}

.gm-thinking__dot--static {
  background: var(--text-muted);
  animation: none;
  box-shadow: none;
}

@keyframes gm-thinking {
  0%, 80%, 100% {
    transform: scale(0.6);
    opacity: 0.4;
  }
  40% {
    transform: scale(1);
    opacity: 1;
  }
}
</style>
