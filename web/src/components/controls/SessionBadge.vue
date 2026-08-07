<script setup lang="ts">
/**
 * SessionBadge.vue · Session 状态徽标（v1.6.0 P1-3）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构决策（p1-iteration-architecture §1 P1-3 + §7 共享知识 #5/#6）：
 *   - 4 态：idle / running / paused / error（由 sessionStats.viewStatus 派生）
 *   - 色盲友好：复用 StatusIcon（status + shape + glyph + aria-label 四重区分）
 *   - running 脉冲动效、error 抖动
 *   - 点击 → 打开 SessionDetailDrawer；idle 时引导提示
 *   - 挂载位置：Header 右侧 HitlBadge 之后（App.vue 挂载顺序 #5）
 */
import { computed } from 'vue'
import { ElMessage } from 'element-plus'
import { useSessionStatsStore } from '@/stores/sessionStats'
import StatusIcon from '@/components/controls/StatusIcon.vue'
import type { Status } from '@/types/theme'

const sessionStats = useSessionStatsStore()

/** 4 态 → StatusIcon status 映射 */
const iconStatus = computed<Status>(() => {
  switch (sessionStats.viewStatus) {
    case 'running':
      return 'normal'
    case 'paused':
      return 'warning'
    case 'error':
      return 'critical'
    default:
      return 'info'
  }
})

const label = computed(() => {
  switch (sessionStats.viewStatus) {
    case 'running':
      return '推理中'
    case 'paused':
      return '已暂停'
    case 'error':
      return '运行异常'
    default:
      return '会话空闲'
  }
})

const ariaLabel = computed(() => {
  const base = `AI 会话状态：${label.value}`
  if (sessionStats.viewStatus === 'running') return `${base}，已运行 ${formatElapsed(sessionStats.elapsedMs)}`
  return base
})

const tooltipText = computed(() => {
  switch (sessionStats.viewStatus) {
    case 'running':
      return `推理中 · 已运行 ${formatElapsed(sessionStats.elapsedMs)} · 点击查看详情`
    case 'paused':
      return '推理已暂停 · 点击查看详情与回滚节点'
    case 'error':
      return '推理运行异常 · 点击查看详情'
    default:
      return '当前无进行中的会话'
  }
})

function formatElapsed(ms: number): string {
  const totalSec = Math.floor(ms / 1000)
  const h = Math.floor(totalSec / 3600)
  const m = Math.floor((totalSec % 3600) / 60)
  const s = totalSec % 60
  const pad = (n: number) => String(n).padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`
}

function handleClick(): void {
  if (sessionStats.viewStatus === 'idle') {
    ElMessage.info('当前无进行中的会话')
    return
  }
  sessionStats.openDrawer()
}
</script>

<template>
  <button
    type="button"
    class="gm-session-badge"
    :class="`gm-session-badge--${sessionStats.viewStatus}`"
    data-test="session-badge"
    :data-status="sessionStats.viewStatus"
    :aria-label="ariaLabel"
    :title="tooltipText"
    @click="handleClick"
  >
    <StatusIcon
      :status="iconStatus"
      :size="14"
      :aria-label="`状态：${label}`"
    />
    <span class="gm-session-badge__label" data-test="session-badge-label">
      {{ label }}
    </span>
    <span v-if="sessionStats.viewStatus === 'running'" class="gm-session-badge__elapsed">
      {{ formatElapsed(sessionStats.elapsedMs) }}
    </span>
  </button>
</template>

<style scoped lang="scss">
.gm-session-badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: 4px var(--space-3);
  border-radius: var(--radius-pill);
  border: 1px solid var(--border-default);
  background: var(--bg-card);
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: var(--fs-sm);
  font-weight: var(--fw-medium);
  cursor: pointer;
  user-select: none;
  -webkit-tap-highlight-color: transparent;
  position: relative;
  z-index: 200;
  outline: none;
  transition:
    background var(--dur-fast) var(--ease-out-quint),
    border-color var(--dur-fast) var(--ease-out-quint),
    color var(--dur-fast) var(--ease-out-quint),
    transform var(--dur-fast) var(--ease-out-quint),
    box-shadow var(--dur-fast) var(--ease-out-quint);
}

.gm-session-badge:hover {
  background: var(--brand-primary-soft);
  border-color: var(--brand-primary);
  color: var(--brand-primary);
  transform: translateY(-1px);
  box-shadow: var(--glow-primary-soft);
}

.gm-session-badge:focus-visible {
  outline: 2px solid var(--brand-primary);
  outline-offset: 2px;
}

.gm-session-badge:active {
  transform: translateY(0);
}

.gm-session-badge__label {
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  letter-spacing: 0.05em;
}

.gm-session-badge__elapsed {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}

/* ── 4 态配色（色盲中间层 --cb-status-*）── */
.gm-session-badge--idle {
  background: var(--bg-card);
  border-color: var(--border-default);
  color: var(--text-secondary);
}

.gm-session-badge--running {
  background: var(--cb-status-normal-soft, var(--status-success-soft));
  border-color: var(--cb-status-normal-fg, var(--status-success));
  color: var(--cb-status-normal-fg, var(--status-success));
  animation: gm-session-pulse 2s ease-in-out infinite;
}

.gm-session-badge--paused {
  background: var(--cb-status-warning-soft, var(--status-warning-soft));
  border-color: var(--cb-status-warning-fg, var(--status-warning));
  color: var(--cb-status-warning-fg, var(--status-warning));
}

.gm-session-badge--error {
  background: var(--cb-status-critical-soft, var(--status-danger-soft));
  border-color: var(--cb-status-critical-fg, var(--status-danger));
  color: var(--cb-status-critical-fg, var(--status-danger));
  animation: gm-session-shake 0.5s ease-in-out 3;
}

/* ── running 脉冲 ── */
@keyframes gm-session-pulse {
  0%, 100% {
    box-shadow: 0 0 0 0 var(--cb-status-normal-soft, var(--status-success-soft));
  }
  50% {
    box-shadow: 0 0 8px 2px var(--cb-status-normal-soft, var(--status-success-soft));
  }
}

/* ── error 抖动 ── */
@keyframes gm-session-shake {
  0%, 100% { transform: translateX(0); }
  25% { transform: translateX(-2px); }
  75% { transform: translateX(2px); }
}

/* ── 紧凑断点：只保留图标 + 文字，隐藏耗时 ── */
@media (max-width: 1280px) {
  .gm-session-badge__elapsed {
    display: none;
  }
}

@media (max-width: 768px) {
  .gm-session-badge {
    padding: 4px var(--space-2);
  }
  .gm-session-badge__label {
    display: none;
  }
}

/* ── 减少动效 ── */
@media (prefers-reduced-motion: reduce) {
  .gm-session-badge--running,
  .gm-session-badge--error {
    animation: none;
  }
}
</style>
