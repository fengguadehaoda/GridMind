<script setup lang="ts">
/**
 * DataStreamBadge · 数据流徽章
 * 用于顶栏状态条（CPU 23% / MEM 41% / 时钟 / 智能体数等）
 */
import { computed } from 'vue'
import type { DataStreamBadgeProps } from '@/types/theme'

const props = withDefaults(defineProps<DataStreamBadgeProps>(), {
  tone: 'info',
  unit: '',
  pulse: false,
})

const toneColor = computed(() => {
  switch (props.tone) {
    case 'success': return 'var(--status-success)'
    case 'danger': return 'var(--status-danger)'
    case 'warning': return 'var(--status-warning)'
    case 'accent': return 'var(--brand-accent)'
    case 'info':
    default: return 'var(--brand-primary)'
  }
})
</script>

<template>
  <div class="gm-data-badge" :style="{ '--badge-color': toneColor }">
    <span v-if="pulse" class="gm-data-badge__dot" />
    <span class="gm-data-badge__label">{{ label }}</span>
    <span class="gm-data-badge__value">{{ value }}</span>
    <span v-if="unit" class="gm-data-badge__unit">{{ unit }}</span>
  </div>
</template>

<style scoped>
.gm-data-badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: 4px var(--space-3);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  font-variant-numeric: tabular-nums;
  color: var(--text-primary);
  letter-spacing: 0.05em;
  white-space: nowrap;
  transition: var(--theme-transition);
}

.gm-data-badge:hover {
  border-color: var(--badge-color);
  box-shadow: 0 0 8px var(--brand-primary-soft);
}

.gm-data-badge__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--badge-color);
  box-shadow: 0 0 6px var(--badge-color);
  animation: gm-pulse 1.8s var(--ease-in-out-cubic) infinite;
  flex-shrink: 0;
}

.gm-data-badge__label {
  color: var(--text-secondary);
  text-transform: uppercase;
}

.gm-data-badge__value {
  color: var(--badge-color);
  font-weight: var(--fw-semibold);
  margin-left: 2px;
}

.gm-data-badge__unit {
  color: var(--text-muted);
  margin-left: 1px;
}

@keyframes gm-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
</style>
