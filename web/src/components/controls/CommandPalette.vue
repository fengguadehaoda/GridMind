<script setup lang="ts">
/**
 * CommandPalette.vue · ⌘K 全局命令面板（v1.6.0 P1-1 增强）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构决策（p1-iteration-architecture §1 P1-1 + §4.1 时序图）：
 *   - 命令注册中心 useCommands 注入（禁止组件内写死命令）
 *   - 自研 fuzzy search（utils/fuzzy.ts）：中文 / 拼音首字母 / 英文
 *   - 分组渲染：路由跳转 / 常用操作 / 上下文命令
 *   - 键盘导航：↑↓ 选择 + Enter 执行 + ESC 关闭
 *   - 快捷键走 hotkey 注册中心（⌘K priority 50 / ESC priority 100）
 *   - 空态引导：未找到命令，试试：切到监控 / 新建对话
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  ChatDotRound,
  Monitor,
  Histogram,
  Document,
  DataBoard,
  Plus,
  Delete,
  Moon,
  VideoPlay,
  VideoPause,
  Brush,
  Bell,
  RefreshLeft,
  Search,
  Right,
} from '@element-plus/icons-vue'
import type { CommandItem, CommandPaletteProps, CommandGroup } from '@/types/theme'
import { useCommands } from '@/composables/useCommands'
import { useReasoningStore } from '@/stores/reasoning'
import { filter, highlightMatch, normalizeQuery } from '@/utils/fuzzy'
import { registerHotkey, ESC_PRIORITY } from '@/utils/hotkeys'

const props = withDefaults(defineProps<CommandPaletteProps>(), {
  scope: 'global',
})

const emit = defineEmits<{
  (e: 'update:open', value: boolean): void
  (e: 'select', item: CommandItem): void
}>()

const { commands, execute } = useCommands()
const reasoning = useReasoningStore()

const search = ref('')
const activeIndex = ref(0)
const listRef = ref<HTMLElement | null>(null)

let unregisterToggle: (() => void) | null = null
let unregisterEsc: (() => void) | null = null

/** 图标名 → Element Plus 图标组件映射 */
const ICON_MAP: Record<string, unknown> = {
  ChatDotRound,
  Monitor,
  Histogram,
  Document,
  DataBoard,
  Plus,
  Delete,
  Moon,
  VideoPlay,
  VideoPause,
  Brush,
  Bell,
  RefreshLeft,
}

const GROUP_LABEL: Record<CommandGroup, string> = {
  routes: '路由跳转',
  actions: '常用操作',
  context: '上下文命令',
}

const GROUP_ORDER: CommandGroup[] = ['routes', 'actions', 'context']

/** 动态 disabled（reasoning 状态实时） */
function isDisabled(item: CommandItem): boolean {
  if (item.disabled) return true
  if (item.id === 'action_reason_pause') return !reasoning.isRunning
  if (item.id === 'action_reason_resume') return !reasoning.isPaused
  return false
}

/** 过滤结果（fuzzy），记录分数 */
const filtered = computed(() => {
  const results = filter(commands.value, search.value)
  return results.map(({ item, score }) => ({ item, score }))
})

/** 扁平化结果（键盘导航用） */
const flatResults = computed(() => filtered.value)

/** 按分组组织（渲染用） */
const groupedResults = computed(() => {
  const groups = new Map<CommandGroup, Array<{ item: CommandItem; score: number }>>()
  for (const group of GROUP_ORDER) groups.set(group, [])
  for (const entry of filtered.value) {
    const list = groups.get(entry.item.group)
    if (list) list.push(entry)
  }
  return GROUP_ORDER.filter((g) => (groups.get(g)?.length ?? 0) > 0).map((g) => ({
    group: g,
    items: groups.get(g)!,
  }))
})

const hasResults = computed(() => filtered.value.length > 0)

/** 高亮标题（受信自有数据） */
function highlightTitle(item: CommandItem): string {
  return highlightMatch(item.title, search.value)
}

function iconOf(item: CommandItem): unknown {
  return item.icon ? (ICON_MAP[item.icon] ?? Right) : Right
}

/** 打开面板：清空 query + 聚焦输入框 */
watch(
  () => props.open,
  async (v) => {
    if (v) {
      search.value = ''
      activeIndex.value = 0
      await nextTick()
      inputRef.value?.focus()
    }
  },
)

const inputRef = ref<HTMLInputElement | null>(null)

function closePalette(): void {
  emit('update:open', false)
}

function move(delta: number): void {
  if (!flatResults.value.length) return
  activeIndex.value = (activeIndex.value + delta + flatResults.value.length) % flatResults.value.length
  scrollActiveIntoView()
}

function scrollActiveIntoView(): void {
  nextTick(() => {
    const el = listRef.value?.querySelector<HTMLElement>('[data-active="true"]')
    el?.scrollIntoView({ block: 'nearest' })
  })
}

function setActive(index: number): void {
  activeIndex.value = index
}

/** 执行当前激活命令 */
async function runCommand(index: number): Promise<void> {
  const entry = flatResults.value[index]
  if (!entry || isDisabled(entry.item)) return
  emit('select', entry.item)
  closePalette()
  try {
    await execute(entry.item.id)
  } catch (err) {
    console.error('[CommandPalette.execute]', err)
  }
}

/** 输入框键盘导航 */
function onInputKeydown(e: KeyboardEvent): void {
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    move(1)
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    move(-1)
  } else if (e.key === 'Enter') {
    e.preventDefault()
    void runCommand(activeIndex.value)
  }
}

onMounted(() => {
  // ⌘K / Ctrl+K 全局唤起（注册中心仲裁）
  unregisterToggle = registerHotkey({
    id: 'command-palette-toggle',
    match: (e) =>
      (e.metaKey || e.ctrlKey) &&
      e.key.toLowerCase() === 'k' &&
      !e.shiftKey &&
      !e.altKey,
    priority: 50,
    preventDefault: true,
    enabled: () => !props.open,
    handler: () => emit('update:open', true),
  })
  // ESC 关闭（优先级 100：面板 > 浮层 > drawer）
  unregisterEsc = registerHotkey({
    id: 'command-palette-esc',
    key: 'Escape',
    priority: ESC_PRIORITY.commandPalette,
    preventDefault: true,
    enabled: () => props.open,
    handler: closePalette,
  })
})

onUnmounted(() => {
  unregisterToggle?.()
  unregisterEsc?.()
})
</script>

<template>
  <el-dialog
    :model-value="open"
    width="560px"
    align-center
    :show-close="false"
    :close-on-press-escape="false"
    :close-on-click-modal="true"
    class="gm-command-palette"
    append-to-body
    @update:model-value="emit('update:open', $event)"
  >
    <div class="gm-command-palette__search">
      <el-icon class="gm-command-palette__search-icon"><Search /></el-icon>
      <input
        ref="inputRef"
        v-model="search"
        class="gm-command-palette__input"
        type="text"
        placeholder="输入命令，例如 监控 / jk / monitor"
        autocomplete="off"
        spellcheck="false"
        data-test="command-palette-input"
        @keydown="onInputKeydown"
      />
      <kbd class="gm-command-palette__esc-hint">ESC</kbd>
    </div>

    <div
      ref="listRef"
      class="gm-command-palette__list"
      data-test="command-palette-list"
      @mousedown.prevent
    >
      <template v-if="hasResults">
        <section
          v-for="group in groupedResults"
          :key="group.group"
          class="gm-command-palette__group"
        >
          <h4 class="gm-command-palette__group-label">{{ GROUP_LABEL[group.group] }}</h4>
          <div
            v-for="(entry, gi) in group.items"
            :key="entry.item.id"
            class="gm-command-palette__item"
            :class="{
              'is-active': flatResults[activeIndex]?.item.id === entry.item.id,
              'is-disabled': isDisabled(entry.item),
            }"
            :data-active="flatResults[activeIndex]?.item.id === entry.item.id"
            :data-test="`command-item-${entry.item.id}`"
            @mouseenter="setActive(flatResults.findIndex((r) => r.item.id === entry.item.id))"
            @click="runCommand(flatResults.findIndex((r) => r.item.id === entry.item.id))"
          >
            <span class="gm-command-palette__item-icon">
              <el-icon :size="15"><component :is="iconOf(entry.item)" /></el-icon>
            </span>
            <span class="gm-command-palette__item-main">
              <span class="gm-command-palette__item-title" v-html="highlightTitle(entry.item)"></span>
              <span v-if="entry.item.subtitle" class="gm-command-palette__item-subtitle">
                {{ entry.item.subtitle }}
              </span>
            </span>
            <span class="gm-command-palette__item-hint">
              <template v-if="entry.item.shortcut">
                <kbd v-for="(k, ki) in entry.item.shortcut" :key="ki">{{ k }}</kbd>
              </template>
              <kbd v-else>⏎</kbd>
            </span>
          </div>
        </section>
      </template>

      <div v-else class="gm-command-palette__empty" data-test="command-palette-empty">
        <el-icon class="gm-command-palette__empty-icon"><Search /></el-icon>
        <p class="gm-command-palette__empty-title">未找到命令，试试：切到监控 / 新建对话</p>
        <p class="gm-command-palette__empty-hint">支持中文、拼音首字母（如 jk）与英文（如 monitor）</p>
      </div>
    </div>

    <div class="gm-command-palette__footer">
      <span class="gm-command-palette__footer-item"><kbd>↑</kbd><kbd>↓</kbd> 选择</span>
      <span class="gm-command-palette__footer-item"><kbd>⏎</kbd> 执行</span>
      <span class="gm-command-palette__footer-item"><kbd>ESC</kbd> 关闭</span>
      <span class="gm-command-palette__footer-count">{{ flatResults.length }} 条命令</span>
    </div>
  </el-dialog>
</template>

<style scoped lang="scss">
.gm-command-palette {
  --gm-palette-radius: var(--radius-lg);
}

.gm-command-palette :deep(.el-dialog),
:deep(.gm-command-palette .el-dialog) {
  border: 1px solid var(--border-strong);
  border-radius: var(--gm-palette-radius);
  background: var(--bg-elevated);
  box-shadow: var(--shadow-modal);
  overflow: hidden;
  transition: var(--theme-transition);
}

.gm-command-palette :deep(.el-dialog__body),
:deep(.gm-command-palette .el-dialog__body) {
  padding: var(--space-4);
}

/* ── 搜索框 ── */
.gm-command-palette__search {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  background: var(--bg-input);
  transition: border-color var(--dur-fast) var(--ease-out-quint), box-shadow var(--dur-fast) var(--ease-out-quint);
}

.gm-command-palette__search:focus-within {
  border-color: var(--brand-primary);
  box-shadow: var(--glow-primary-soft);
}

.gm-command-palette__search-icon {
  color: var(--text-muted);
  flex-shrink: 0;
}

.gm-command-palette__input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  color: var(--text-primary);
  font-family: var(--font-cn);
  font-size: var(--fs-md);
}

.gm-command-palette__input::placeholder {
  color: var(--text-muted);
}

.gm-command-palette__esc-hint {
  flex-shrink: 0;
}

/* ── 列表 ── */
.gm-command-palette__list {
  margin-top: var(--space-3);
  max-height: 380px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.gm-command-palette__group-label {
  margin: var(--space-2) var(--space-1) var(--space-1);
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.14em;
}

.gm-command-palette__item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background var(--dur-instant) var(--ease-out-quint),
              border-color var(--dur-instant) var(--ease-out-quint);
}

.gm-command-palette__item.is-active {
  background: var(--brand-primary-soft);
  border-color: var(--brand-primary);
}

.gm-command-palette__item.is-disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.gm-command-palette__item-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  flex-shrink: 0;
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  color: var(--brand-primary);
  border: 1px solid var(--border-muted);
}

.gm-command-palette__item-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.gm-command-palette__item-title {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  font-weight: var(--fw-medium);
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.gm-command-palette__item-title :deep(mark) {
  background: var(--brand-primary-soft);
  color: var(--brand-primary);
  border-radius: 2px;
  padding: 0 1px;
}

.gm-command-palette__item-subtitle {
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.gm-command-palette__item-hint {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  opacity: 0.7;
}

/* ── 空态 ── */
.gm-command-palette__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: var(--space-10) var(--space-4);
  border: 1px dashed var(--border-default);
  border-radius: var(--radius-md);
  text-align: center;
}

.gm-command-palette__empty-icon {
  font-size: 28px;
  color: var(--text-muted);
}

.gm-command-palette__empty-title {
  margin: 0;
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  color: var(--text-secondary);
}

.gm-command-palette__empty-hint {
  margin: 0;
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}

/* ── 底部提示 ── */
.gm-command-palette__footer {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  margin-top: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--border-muted);
}

.gm-command-palette__footer-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}

.gm-command-palette__footer-count {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}

kbd {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 22px;
  height: 20px;
  padding: 0 6px;
  border: 1px solid var(--border-strong);
  border-bottom-width: 2px;
  border-radius: var(--radius-sm);
  background: var(--bg-input);
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  line-height: 1;
}
</style>
