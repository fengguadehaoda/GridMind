<script setup lang="ts">
/**
 * MenuDrawer.vue · 右侧菜单抽屉（T01 / T04）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构决策（header-redesign-architecture-2026-08-06 §1.2 + §1.3 + §3.4 + §7.8）：
 *   - el-drawer（direction="rtl"）；桌面 360px；<768px 移动端 ≥80% 视口宽（T04）
 *   - 分组数据 = menuDrawerGroups.ts 注册表（视图/主题/系统/调试 + 底部快捷区）
 *   - component 型条目用 <component :is> 直接嵌入复用控件（零改动）
 *   - route 型跳转 / action 型执行回调，执行后自动关闭抽屉
 *   - P1-1 搜索：内存 filter（label + keywords），数据量 <40 条不引 fuzzy 库
 *   - P1-2 快捷键：⌘\ / Ctrl+\ 快捷开关抽屉（priority 40，低于命令面板 50）
 *   - a11y：aria-label / 焦点由 el-drawer 管理 / Esc 关闭（EP 内置）
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import type { Component } from 'vue'
import { useRouter } from 'vue-router'
import { Search } from '@element-plus/icons-vue'
import { menuDrawerGroups, menuDrawerQuickEntries } from '@/data/menuDrawerGroups'
import type { MenuDrawerEntry, MenuDrawerGroup } from '@/types/header'
import { useViewport } from '@/composables/useViewport'
import { registerHotkey } from '@/utils/hotkeys'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ (e: 'update:modelValue', value: boolean): void }>()

const router = useRouter()
const { isMobile } = useViewport()

/** v-model 协议（与 NavDrawer 同款） */
const open = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

/** 抽屉宽度：<768px ≥80% 视口（架构 §7.3 + T04）；桌面固定 360px */
const drawerSize = computed(() => (isMobile.value ? '86%' : '360px'))

/** 搜索关键词（P1-1） */
const keyword = ref('')

/** 关键词匹配：label + keywords[] 子串命中（小写归一化） */
function matches(entry: MenuDrawerEntry, kw: string): boolean {
  const q = kw.trim().toLowerCase()
  if (!q) return true
  if (entry.label.toLowerCase().includes(q)) return true
  if (entry.keywords?.some((k) => k.toLowerCase().includes(q))) return true
  return false
}

/** 分组过滤：关键词命中任一条目才保留该分组 */
const filteredGroups = computed<MenuDrawerGroup[]>(() => {
  const kw = keyword.value.trim()
  if (!kw) return menuDrawerGroups
  return menuDrawerGroups
    .map((g) => ({ ...g, entries: g.entries.filter((e) => matches(e, kw)) }))
    .filter((g) => g.entries.length > 0)
})

/** 底部快捷区过滤 */
const filteredQuick = computed(() =>
  menuDrawerQuickEntries.filter((e) => matches(e, keyword.value)),
)

/** 关闭抽屉（复位搜索词，避免下次打开残留） */
function close(): void {
  open.value = false
  keyword.value = ''
}

/** 条目执行：route 跳转 / action 执行（component 型直接渲染，无需处理） */
function handleEntry(entry: MenuDrawerEntry): void {
  if (entry.type === 'route') {
    close()
    void router.push(entry.route)
  } else if (entry.type === 'action') {
    close()
    entry.action()
  }
}

/** 类型守卫：route/action 型条目的可选图标（模板窄化兜底，避免 vue-tsc 联合类型报错） */
function hasIcon(entry: MenuDrawerEntry): boolean {
  return entry.type !== 'component' && Boolean(entry.icon)
}
function iconOf(entry: MenuDrawerEntry): Component | undefined {
  return entry.type === 'component' ? undefined : entry.icon
}

// P1-2：⌘\ / Ctrl+\ 快捷开关抽屉（与命令面板 ⌘K priority 50 不冲突）
let unregisterToggle: (() => void) | null = null
onMounted(() => {
  unregisterToggle = registerHotkey({
    id: 'menu-drawer-toggle',
    match: (e) =>
      (e.metaKey || e.ctrlKey) &&
      e.key.toLowerCase() === '\\' &&
      !e.shiftKey &&
      !e.altKey,
    priority: 40,
    preventDefault: true,
    handler: () => {
      open.value = !open.value
    },
  })
})
onUnmounted(() => {
  unregisterToggle?.()
})
</script>

<template>
  <el-drawer
    :model-value="open"
    direction="rtl"
    :size="drawerSize"
    :with-header="true"
    class="gm-menu-drawer"
    data-test="menu-drawer"
    aria-label="菜单"
    @update:model-value="(v: boolean) => (open = v)"
    @closed="keyword = ''"
  >
    <template #header>
      <div class="gm-menu-drawer__header">
        <span class="gm-menu-drawer__title">菜单</span>
        <button
          type="button"
          class="gm-menu-drawer__close"
          data-test="menu-drawer-close"
          aria-label="关闭菜单"
          @click="close"
        >×</button>
      </div>
    </template>

    <div class="gm-menu-drawer__body">
      <!-- P1-1：抽屉搜索 -->
      <div class="gm-menu-drawer__search">
        <el-icon class="gm-menu-drawer__search-icon" :size="14"><Search /></el-icon>
        <input
          v-model="keyword"
          class="gm-menu-drawer__search-input"
          type="text"
          placeholder="搜索功能，如 主题 / 审计 / hitl"
          aria-label="搜索菜单功能"
          autocomplete="off"
          spellcheck="false"
          data-test="menu-drawer-search"
        />
      </div>

      <!-- 分组渲染（视图 / 主题 / 系统 / 调试） -->
      <div class="gm-menu-drawer__groups">
        <section
          v-for="group in filteredGroups"
          :key="group.id"
          class="gm-menu-drawer__group"
          :data-test="`menu-drawer-group-${group.id}`"
        >
          <h4 class="gm-menu-drawer__group-title">{{ group.title }}</h4>
          <div class="gm-menu-drawer__entries">
            <template v-for="entry in group.entries" :key="entry.id">
              <!-- component 型：零改动嵌入复用控件 -->
              <div
                v-if="entry.type === 'component'"
                class="gm-menu-drawer__entry gm-menu-drawer__entry--component"
                :data-test="`menu-drawer-entry-${entry.id}`"
              >
                <span class="gm-menu-drawer__entry-label">{{ entry.label }}</span>
                <component :is="entry.component" />
              </div>
              <!-- route / action 型：按钮点击执行 -->
              <button
                v-else
                type="button"
                class="gm-menu-drawer__entry gm-menu-drawer__entry--button"
                :data-test="`menu-drawer-entry-${entry.id}`"
                @click="handleEntry(entry)"
              >
                <el-icon
                  v-if="hasIcon(entry)"
                  :size="16"
                  class="gm-menu-drawer__entry-icon"
                ><component :is="iconOf(entry)" /></el-icon>
                <span class="gm-menu-drawer__entry-label">{{ entry.label }}</span>
              </button>
            </template>
          </div>
        </section>
      </div>

      <!-- 底部快捷区（新对话 / 知识库管理 / 消息引导） -->
      <section
        v-if="filteredQuick.length"
        class="gm-menu-drawer__quick"
        data-test="menu-drawer-group-quick"
      >
        <h4 class="gm-menu-drawer__group-title">快捷</h4>
        <div class="gm-menu-drawer__entries">
          <button
            v-for="entry in filteredQuick"
            :key="entry.id"
            type="button"
            class="gm-menu-drawer__entry gm-menu-drawer__entry--button"
            :data-test="`menu-drawer-entry-${entry.id}`"
            @click="handleEntry(entry)"
          >
            <el-icon
              v-if="hasIcon(entry)"
              :size="16"
              class="gm-menu-drawer__entry-icon"
            ><component :is="iconOf(entry)" /></el-icon>
            <span class="gm-menu-drawer__entry-label">{{ entry.label }}</span>
          </button>
        </div>
      </section>
    </div>
  </el-drawer>
</template>

<style scoped lang="scss">
/* ── 抽屉面板（360px rtl；移动端 86%） ── */
.gm-menu-drawer :deep(.el-drawer) {
  background: var(--bg-elevated);
  border-left: 1px solid var(--border-default);
  transition: var(--theme-transition);
}

.gm-menu-drawer :deep(.el-drawer__header) {
  margin-bottom: 0;
  padding: var(--space-4);
  border-bottom: 1px solid var(--border-muted);
  color: var(--text-primary);
}

.gm-menu-drawer :deep(.el-drawer__body) {
  padding: 0;
  overflow-y: auto;
}

/* ── 头部 ── */
.gm-menu-drawer__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}

.gm-menu-drawer__title {
  font-family: var(--font-cn);
  font-size: var(--fs-md);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
  letter-spacing: 0.1em;
}

.gm-menu-drawer__close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  color: var(--text-secondary);
  font-size: var(--fs-lg);
  line-height: 1;
  cursor: pointer;
  transition: all var(--dur-fast) var(--ease-out-quint);
}

.gm-menu-drawer__close:hover {
  color: var(--brand-primary);
  border-color: var(--brand-primary);
  box-shadow: var(--glow-primary-soft);
}

/* ── 搜索框（P1-1） ── */
.gm-menu-drawer__search {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin: var(--space-4);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  background: var(--bg-input);
  transition: border-color var(--dur-fast) var(--ease-out-quint), box-shadow var(--dur-fast) var(--ease-out-quint);
}

.gm-menu-drawer__search:focus-within {
  border-color: var(--brand-primary);
  box-shadow: var(--glow-primary-soft);
}

.gm-menu-drawer__search-icon {
  color: var(--text-muted);
  flex-shrink: 0;
}

.gm-menu-drawer__search-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  color: var(--text-primary);
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
}

.gm-menu-drawer__search-input::placeholder {
  color: var(--text-muted);
}

/* ── 分组 ── */
.gm-menu-drawer__groups {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: 0 var(--space-4) var(--space-4);
}

.gm-menu-drawer__group-title {
  margin: 0 0 var(--space-2);
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.14em;
}

.gm-menu-drawer__entries {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

/* ── 条目（按钮 / 组件容器共用外壳） ── */
.gm-menu-drawer__entry {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  min-height: 40px;
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--border-muted);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  color: var(--text-secondary);
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  cursor: pointer;
  text-align: left;
  transition: all var(--dur-fast) var(--ease-out-quint);
}

.gm-menu-drawer__entry--button:hover {
  border-color: var(--brand-primary);
  color: var(--brand-primary);
  background: var(--brand-primary-soft);
}

.gm-menu-drawer__entry--button:focus-visible {
  outline: 2px solid var(--brand-primary);
  outline-offset: 2px;
}

/* component 型：标签在左、控件在右（控件自带样式，只做包裹适配） */
.gm-menu-drawer__entry--component {
  justify-content: space-between;
  cursor: default;
  flex-wrap: wrap;
  row-gap: var(--space-2);
}

.gm-menu-drawer__entry--component .gm-menu-drawer__entry-label {
  color: var(--text-secondary);
}

/* 组件型内嵌控件在 360px 内的宽度适配（架构 §7.5：外层包裹样式，不改控件源码） */
.gm-menu-drawer__entry--component :deep(.gm-bg-mode-toggle),
.gm-menu-drawer__entry--component :deep(.gm-cb-mode-toggle),
.gm-menu-drawer__entry--component :deep(.gm-theme-toggle),
.gm-menu-drawer__entry--component :deep(.gm-onboarding-trigger),
.gm-menu-drawer__entry--component :deep(.gm-session-badge),
.gm-menu-drawer__entry--component :deep(.hitl-badge) {
  flex-shrink: 0;
}

.gm-menu-drawer__entry-icon {
  color: var(--brand-primary);
  flex-shrink: 0;
  display: inline-flex;
}

.gm-menu-drawer__entry-label {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ── 底部快捷区 ── */
.gm-menu-drawer__quick {
  padding: var(--space-4);
  border-top: 1px solid var(--border-muted);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

/* ── 减少动效偏好 ── */
@media (prefers-reduced-motion: reduce) {
  .gm-menu-drawer__entry {
    transition: none;
  }
}
</style>
