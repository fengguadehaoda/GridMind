<script setup lang="ts">
/**
 * CommandPalette · ⌘K 全局命令面板（M2 任务，本期占位）
 * 仅实现基础结构与快捷键注册，命令注册中心 useCommands 待 M2 阶段
 */
import { onMounted, onUnmounted, ref, watch } from 'vue'
import type { CommandPaletteProps, CommandItem } from '@/types/theme'

const props = withDefaults(defineProps<CommandPaletteProps>(), {
  scope: 'global',
})

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void
  (e: 'select', item: CommandItem): void
}>()

const search = ref('')
const commands: CommandItem[] = [] // M2 阶段从 useCommands() 注入

function onKeydown(e: KeyboardEvent) {
  // ⌘K / Ctrl+K
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
    e.preventDefault()
    emit('update:open', !props.open)
  }
  // ESC
  if (e.key === 'Escape' && props.open) {
    emit('update:open', false)
  }
}

onMounted(() => {
  document.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  document.removeEventListener('keydown', onKeydown)
})

watch(() => props.open, (v) => {
  if (v) {
    search.value = ''
  }
})
</script>

<template>
  <!-- M2 占位：基础结构已就位，命令注册/搜索/执行待 M2 阶段实现 -->
  <el-dialog
    v-if="open"
    :model-value="open"
    width="560px"
    align-center
    :show-close="false"
    class="gm-command-palette"
    @update:model-value="emit('update:open', $event)"
  >
    <div class="gm-command-palette__search">
      <el-input
        v-model="search"
        placeholder="输入命令（开发中 · M2 上线）"
        size="large"
        :autofocus="true"
      />
    </div>
    <div class="gm-command-palette__hint">
      <span class="gm-command-palette__hint-text">M2 阶段实现：清空对话、切换主题、跳到监控、知识检索、设置等</span>
    </div>
  </el-dialog>
</template>

<style scoped>
.gm-command-palette__search {
  margin-bottom: var(--space-4);
}
.gm-command-palette__hint {
  padding: var(--space-3);
  background: var(--bg-card);
  border: 1px dashed var(--border-default);
  border-radius: var(--radius-sm);
  text-align: center;
}
.gm-command-palette__hint-text {
  font-size: var(--fs-sm);
  color: var(--text-muted);
}
</style>
