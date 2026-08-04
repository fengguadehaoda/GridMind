<script setup lang="ts">
/**
 * AgentBadge · 智能体徽章
 * 区分不同智能体的视觉标识：monitor / diagnosis / rag / planner / orchestrator / user / system
 */
import { computed } from 'vue'
import type { AgentBadgeProps } from '@/types/theme'

const props = withDefaults(defineProps<AgentBadgeProps>(), {
  size: 'md',
  showLabel: true,
})

interface AgentMeta {
  label: string
  icon: string
  color: string
}

const AGENT_META: Record<string, AgentMeta> = {
  monitor: { label: '监控', icon: 'M', color: 'var(--brand-primary)' },
  diagnosis: { label: '诊断', icon: 'D', color: 'var(--brand-accent)' },
  rag: { label: '知识', icon: 'K', color: 'var(--brand-violet)' },
  planner: { label: '规划', icon: 'P', color: 'var(--status-info)' },
  orchestrator: { label: '调度', icon: 'O', color: 'var(--status-success)' },
  user: { label: '我', icon: 'U', color: 'var(--text-primary)' },
  system: { label: '系统', icon: 'S', color: 'var(--text-muted)' },
}

const meta = computed(() => AGENT_META[props.agent] || AGENT_META.system)
</script>

<template>
  <div
    class="gm-agent-badge"
    :class="[`gm-agent-badge--${size}`, `gm-agent-badge--${agent}`]"
    :style="{ '--agent-color': meta.color }"
  >
    <span class="gm-agent-badge__icon">{{ meta.icon }}</span>
    <span v-if="showLabel" class="gm-agent-badge__label">{{ meta.label }}</span>
  </div>
</template>

<style scoped>
.gm-agent-badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: 2px var(--space-2);
  border: 1px solid var(--agent-color);
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-weight: var(--fw-semibold);
  color: var(--agent-color);
  background: color-mix(in srgb, var(--agent-color) 8%, transparent);
  text-transform: uppercase;
  letter-spacing: 0.1em;
}

.gm-agent-badge--sm {
  font-size: 10px;
  padding: 1px 6px;
}
.gm-agent-badge--md {
  font-size: var(--fs-xs);
}

.gm-agent-badge__icon {
  display: inline-block;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: var(--agent-color);
  color: var(--text-inverse);
  font-size: 9px;
  line-height: 14px;
  text-align: center;
  font-weight: var(--fw-bold);
}

.gm-agent-badge--sm .gm-agent-badge__icon {
  width: 12px;
  height: 12px;
  line-height: 12px;
  font-size: 8px;
}
</style>
