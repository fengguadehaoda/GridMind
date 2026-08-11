<template>
  <div class="doc-filter-chips" role="group" aria-label="按文档筛选来源">
    <button
      type="button"
      class="chip"
      :class="{ active: isAllActive }"
      @click="$emit('update:modelValue', null)"
    >
      全部
    </button>
    <button
      v-for="group in groups"
      :key="group.doc_id || group.label"
      type="button"
      class="chip"
      :class="{ active: modelValue === group.doc_id }"
      @click="$emit('update:modelValue', group.doc_id)"
    >
      {{ group.label }}（{{ group.count }}）
    </button>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { SourceGroup } from '../../composables/useKbSources'

const props = defineProps<{ groups: SourceGroup[]; modelValue: string | null }>()

defineEmits<{ (e: 'update:modelValue', value: string | null): void }>()

/** 「全部」激活条件：modelValue 为 null / 空串 */
const isAllActive = computed(() => props.modelValue === null || props.modelValue === '')
</script>

<style scoped>
.doc-filter-chips {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-1);
  margin-top: var(--space-2);
}

.chip {
  border: 1px solid var(--border-default);
  background: var(--bg-card);
  color: var(--text-secondary);
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  padding: 2px 10px;
  border-radius: 999px;
  cursor: pointer;
  transition: var(--theme-transition);
}

.chip:hover {
  border-color: var(--brand-primary);
  color: var(--brand-primary);
}

.chip.active {
  border-color: var(--brand-primary);
  background: var(--brand-primary);
  color: var(--bg-card);
}
</style>
