<script setup lang="ts">
/**
 * StatHexagon · 六边形统计卡
 * 大屏仪表盘使用，与切角风格统一
 */
import { computed } from 'vue'
import type { StatHexagonProps } from '@/types/theme'

const props = withDefaults(defineProps<StatHexagonProps>(), {
  unit: '',
  tone: 'info',
  loading: false,
})

const toneColor = computed(() => {
  switch (props.tone) {
    case 'success': return 'var(--status-success)'
    case 'warning': return 'var(--status-warning)'
    case 'danger': return 'var(--status-danger)'
    case 'accent': return 'var(--brand-accent)'
    case 'info':
    default: return 'var(--brand-primary)'
  }
})

const deltaSign = computed(() => {
  if (props.delta === undefined || props.delta === 0) return ''
  return props.delta > 0 ? '↑' : '↓'
})

const deltaTone = computed(() => {
  if (props.delta === undefined || props.delta === 0) return 'neutral'
  if (props.delta > 0) {
    // 不同业务语境下 delta 含义不同，这里假设「上升 = 好」（success）
    return props.tone === 'danger' ? 'success' : 'success'
  }
  return props.tone === 'success' ? 'danger' : 'danger'
})
</script>

<template>
  <div
    class="gm-stat-hex"
    :class="[`gm-stat-hex--${tone}`, { 'gm-stat-hex--loading': loading }]"
    :style="{ '--hex-tone': toneColor }"
  >
    <div v-if="loading" class="gm-stat-hex__skeleton" />
    <template v-else>
      <div class="gm-stat-hex__label">{{ label }}</div>
      <div class="gm-stat-hex__value-row">
        <span class="gm-stat-hex__value">{{ value }}</span>
        <span v-if="unit" class="gm-stat-hex__unit">{{ unit }}</span>
      </div>
      <div
        v-if="delta !== undefined"
        class="gm-stat-hex__delta"
        :class="`gm-stat-hex__delta--${deltaTone}`"
      >
        <span class="gm-stat-hex__delta-sign">{{ deltaSign }}</span>
        <span>{{ Math.abs(delta).toFixed(1) }}%</span>
      </div>
    </template>
  </div>
</template>

<style scoped>
.gm-stat-hex {
  position: relative;
  width: 100%;
  aspect-ratio: 1.15 / 1;
  background: var(--bg-card);
  border: 1px solid var(--border-default);
  clip-path: polygon(25% 0%, 75% 0%, 100% 50%, 75% 100%, 25% 100%, 0% 50%);
  padding: var(--space-5) var(--space-4);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  gap: var(--space-2);
  transition: all var(--dur-base) var(--ease-out-quint);
}

.gm-stat-hex:hover {
  background: var(--bg-card-solid);
  border-color: var(--hex-tone);
  box-shadow: 0 0 16px var(--brand-primary-soft);
}

.gm-stat-hex__label {
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  font-weight: var(--fw-medium);
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.15em;
}

.gm-stat-hex__value-row {
  display: flex;
  align-items: baseline;
  gap: 2px;
}

.gm-stat-hex__value {
  font-family: var(--font-display);
  font-size: var(--fs-2xl);
  font-weight: var(--fw-bold);
  color: var(--hex-tone);
  font-variant-numeric: tabular-nums;
  line-height: 1;
}

.gm-stat-hex__unit {
  font-family: var(--font-mono);
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  margin-left: 2px;
}

.gm-stat-hex__delta {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  font-weight: var(--fw-medium);
}

.gm-stat-hex__delta--success { color: var(--status-success); }
.gm-stat-hex__delta--danger { color: var(--status-danger); }
.gm-stat-hex__delta--neutral { color: var(--text-muted); }

.gm-stat-hex__delta-sign {
  font-size: var(--fs-sm);
}

.gm-stat-hex--loading {
  background: var(--bg-card);
}

.gm-stat-hex__skeleton {
  width: 60%;
  height: 60%;
  background: linear-gradient(
    90deg,
    var(--bg-card-solid) 0%,
    var(--border-muted) 50%,
    var(--bg-card-solid) 100%
  );
  background-size: 200% 100%;
  animation: gm-shimmer 1.5s linear infinite;
  border-radius: var(--radius-sm);
}
</style>
