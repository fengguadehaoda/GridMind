<template>
  <div class="demo-shortcuts">
    <div class="shortcuts-title">
      <el-icon><MagicStick /></el-icon>
      <span>演示快捷指令</span>
    </div>

    <div class="shortcut-grid">
      <div
        v-for="sc in shortcuts"
        :key="sc.label"
        class="shortcut-card"
        :class="{ disabled: loading }"
        @click="onClick(sc)"
      >
        <div class="shortcut-info">
          <div class="shortcut-label">{{ sc.label }}</div>
          <div class="shortcut-desc">{{ sc.description }}</div>
        </div>
        <el-icon class="shortcut-arrow"><ArrowRight /></el-icon>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { MagicStick, ArrowRight } from '@element-plus/icons-vue'
import type { DemoShortcut } from '../types'

defineProps<{
  shortcuts: DemoShortcut[]
  loading: boolean
}>()

const emit = defineEmits<{
  (e: 'send', message: string): void
}>()

function onClick(sc: DemoShortcut) {
  emit('send', sc.message)
}
</script>

<style scoped>
.demo-shortcuts {
  margin-bottom: var(--space-4);
  width: 100%;
  max-width: 720px;
}

.shortcuts-title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
  color: var(--text-muted);
  margin-bottom: var(--space-3);
  letter-spacing: 0.15em;
  text-transform: uppercase;
}

.shortcut-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
}

@media (max-width: 640px) {
  .shortcut-grid {
    grid-template-columns: 1fr;
  }
}

.shortcut-card {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  background: var(--bg-card);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--dur-base) var(--ease-out-quint);
  position: relative;
  clip-path: var(--clip-corner-sm);
  overflow: hidden;
}

.shortcut-card::before {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(
    135deg,
    transparent 0%,
    var(--brand-primary-soft) 50%,
    transparent 100%
  );
  opacity: 0;
  transition: opacity var(--dur-base) var(--ease-out-quint);
  pointer-events: none;
}

.shortcut-card:hover {
  border-color: var(--brand-primary);
  background: var(--bg-card-solid);
  box-shadow: var(--glow-primary-soft);
  transform: translateY(-2px);
}

.shortcut-card:hover::before {
  opacity: 1;
}

.shortcut-card.disabled {
  opacity: 0.4;
  cursor: not-allowed;
  pointer-events: none;
}

.shortcut-card:active {
  transform: translateY(0);
}

.shortcut-info {
  flex: 1;
  min-width: 0;
}

.shortcut-label {
  font-family: var(--font-cn);
  font-size: var(--fs-md);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
  letter-spacing: 0.05em;
}

.shortcut-desc {
  font-size: var(--fs-xs);
  color: var(--text-muted);
  margin-top: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-cn);
}

.shortcut-arrow {
  color: var(--text-muted);
  font-size: var(--fs-md);
  opacity: 0;
  transform: translateX(-4px);
  transition: all var(--dur-base) var(--ease-out-quint);
  flex-shrink: 0;
}

.shortcut-card:hover .shortcut-arrow {
  opacity: 1;
  transform: translateX(0);
  color: var(--brand-primary);
}
</style>
