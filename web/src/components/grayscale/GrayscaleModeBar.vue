<script setup lang="ts">
/**
 * GrayscaleModeBar.vue · 探索 / 规划双模式切换条（v1.6.0 P1-4）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构决策（p1-iteration-architecture §1 P1-4）：
 *   - 探索模式：AI 推荐方案，只读
 *   - 规划模式：人工勾选节点 → 生成方案，可编辑
 *   - 对标 Coze 双模式
 */
import { computed } from 'vue'
import { useGrayscaleGraphStore } from '@/stores/grayscaleGraph'

const graphStore = useGrayscaleGraphStore()

const mode = computed(() => graphStore.mode)

const MODES = [
  { value: 'explore', label: '探索模式', hint: '系统推荐方案 · 只读' },
  { value: 'plan', label: '规划模式', hint: '勾选节点生成方案 · 可编辑' },
] as const
</script>

<template>
  <div class="gm-grayscale-mode-bar" data-test="grayscale-mode-bar">
    <div class="gm-grayscale-mode-bar__seg" role="tablist" aria-label="灰度模式切换">
      <button
        v-for="m in MODES"
        :key="m.value"
        type="button"
        role="tab"
        class="gm-grayscale-mode-bar__btn"
        :class="{ 'is-active': mode === m.value }"
        :aria-selected="mode === m.value"
        :data-test="`grayscale-mode-${m.value}`"
        @click="graphStore.setMode(m.value)"
      >
        {{ m.label }}
      </button>
    </div>
    <span class="gm-grayscale-mode-bar__hint">
      {{ MODES.find((m) => m.value === mode)?.hint }}
    </span>
  </div>
</template>

<style scoped lang="scss">
.gm-grayscale-mode-bar {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  flex-wrap: wrap;
}

.gm-grayscale-mode-bar__seg {
  display: inline-flex;
  padding: 3px;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-pill);
  background: var(--bg-input);
  gap: 2px;
}

.gm-grayscale-mode-bar__btn {
  padding: 6px var(--space-4);
  border: none;
  border-radius: var(--radius-pill);
  background: transparent;
  color: var(--text-secondary);
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  cursor: pointer;
  transition: all var(--dur-fast) var(--ease-out-quint);
}

.gm-grayscale-mode-bar__btn.is-active {
  background: var(--brand-primary);
  color: var(--text-inverse);
  font-weight: var(--fw-semibold);
  box-shadow: var(--glow-primary-soft);
}

.gm-grayscale-mode-bar__hint {
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}
</style>
