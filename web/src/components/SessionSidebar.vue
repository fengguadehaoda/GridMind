<!--
  web/src/components/SessionSidebar.vue · M-5 会话侧栏（T03 + T04 导出入口）
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  架构 session-mgmt-architecture §1.4 + PRD §五 UI 设计稿：
    - 「＋ 新建会话」顶部按钮（newSession，懒登记语义）
    - 活跃会话列表（激活高亮 = chatStore.threadId）
    - 每项「⋯」菜单：重命名（内联输入）/ 归档 / 恢复（归档项）/ 删除（二次确认）
    - 「🗂 已归档」折叠分组（archivedSessions；恢复按钮本批纳入，主理人决策）
    - 空态 / 加载态 / 错误态（重试）
    - 顶部「导出 ▾」工具栏（仅当前激活会话可导出；空会话提示不生成文件，AC2-4）

  交互要点（PRD §五）：
    - 点击会话项 = activateThread（Q7 流式确认 + AbortController 中断）
    - 删除对齐 KB 删除交互（ElMessageBox.confirm 二次确认）
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ChatDotRound,
  Collection,
  Download,
  EditPen,
  FolderOpened,
  Plus,
  Refresh,
  MoreFilled,
  Delete,
  Fold,
  Expand,
} from '@element-plus/icons-vue'
import { useChatStore } from '../stores/chatStore'
import { useModelStore } from '../stores/modelStore'
import { getJwtUserId, getJwtRole } from '../composables/useJwtAuth'
import {
  buildExportFilename,
  buildJson,
  buildMarkdown,
  downloadFile,
  type ExportFormat,
} from './export/sessionExport'
import type { SessionSummary } from '../types'

const store = useChatStore()
const modelStore = useModelStore()

/** 已归档折叠分组 open 态（本地 UI 状态） */
const archivedOpen = ref(false)

/** 内联重命名：正在重命名的 thread_id（空 = 非重命名态） */
const renamingId = ref<string>('')
const renameDraft = ref('')
const renameInputRef = ref<HTMLInputElement | null>(null)

/** 激活态判定 */
function isActive(tid: string): boolean {
  return store.threadId === tid
}

/** 会话项展示的生效模型（threads.model_id ?? 全局，AC5-2） */
function modelLabel(s: SessionSummary): string {
  if (s.model_id) return s.model_id
  return modelStore.getEffectiveModel() || ''
}

/** 空态判定：无活跃会话且无归档会话且未在加载/出错 */
const isEmpty = computed(
  () =>
    !store.sessionsLoading &&
    !store.sessionError &&
    store.sessions.length === 0 &&
    store.archivedSessions.length === 0,
)

/* ────────────────────────────────────────────────────────────
 * 新建 / 切换
 * ──────────────────────────────────────────────────────────── */

function onCreate(): void {
  void store.newSession()
}

function onSelect(s: SessionSummary): void {
  if (isActive(s.thread_id)) return
  void store.activateThread(s.thread_id)
}

/* ────────────────────────────────────────────────────────────
 * 内联重命名
 * ──────────────────────────────────────────────────────────── */

function beginRename(s: SessionSummary): void {
  renamingId.value = s.thread_id
  renameDraft.value = s.title || '新会话'
  // 等 DOM 渲染后聚焦
  requestAnimationFrame(() => {
    renameInputRef.value?.focus()
    renameInputRef.value?.select()
  })
}

async function confirmRename(): Promise<void> {
  const tid = renamingId.value
  const title = renameDraft.value.trim()
  renamingId.value = ''
  if (!tid) return
  if (!title) {
    ElMessage.warning('标题不能为空')
    return
  }
  await store.renameSession(tid, title)
}

function cancelRename(): void {
  renamingId.value = ''
}

/* ────────────────────────────────────────────────────────────
 * 归档 / 恢复 / 删除
 * ──────────────────────────────────────────────────────────── */

function onArchive(s: SessionSummary): void {
  void store.archiveSession(s.thread_id)
}

function onRestore(s: SessionSummary): void {
  void store.restoreSession(s.thread_id)
}

/** 删除二次确认（对齐 KB 删除交互，PRD §五 交互要点 2） */
function onDelete(s: SessionSummary): void {
  void ElMessageBox.confirm(
    `删除后该会话将从列表移除（数据保留供审计追溯），确定删除「${s.title || '新会话'}」？`,
    '确认删除',
    {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
      confirmButtonClass: 'el-button--danger',
    },
  )
    .then(async () => {
      await store.deleteSession(s.thread_id)
    })
    .catch(() => {
      /* 用户取消，静默 */
    })
}

/* ────────────────────────────────────────────────────────────
 * 导出（T04 · 仅当前激活会话）
 * ──────────────────────────────────────────────────────────── */

/** 是否可导出：当前激活会话存在非空 user/assistant 消息（AC2-4） */
const canExport = computed(() =>
  store.exportableMessages.some((m) => m.role === 'user' || m.role === 'assistant'),
)

function handleExport(format: ExportFormat): void {
  if (!canExport.value) {
    ElMessage.warning('当前会话暂无内容可导出')
    return
  }
  const msgs = store.exportableMessages
  const active =
    store.sessions.find((s) => s.thread_id === store.threadId) ?? null
  const title = active?.title || '新会话'
  const modelId = active?.model_id || modelStore.getEffectiveModel() || null
  const meta = { user_id: getJwtUserId() ?? '访客', role: getJwtRole() }

  const filename = buildExportFilename(title, store.threadId, format)
  if (format === 'md') {
    downloadFile(
      filename,
      buildMarkdown(store.threadId, title, modelId, msgs, meta),
      'text/markdown',
    )
  } else {
    downloadFile(
      filename,
      buildJson(store.threadId, title, modelId, msgs, meta),
      'application/json',
    )
  }
  ElMessage.success(`已导出 ${format === 'md' ? 'Markdown' : 'JSON'} 文件`)
}

onMounted(() => {
  // 进入对话页拉一次会话列表（架构时序 4.1）
  if (!store.sessionsLoading && store.sessions.length === 0) {
    void store.fetchSessions()
  }
})
</script>

<template>
  <aside class="session-sidebar" data-test="session-sidebar">
    <!-- 顶部工具栏：新建 + 导出 -->
    <div class="session-sidebar__toolbar">
      <el-button
        type="primary"
        class="session-sidebar__new"
        :icon="Plus"
        data-test="session-new"
        @click="onCreate"
      >
        新建会话
      </el-button>
      <el-dropdown
        trigger="click"
        :disabled="!canExport"
        data-test="session-export"
        @command="(cmd: string) => handleExport(cmd as ExportFormat)"
      >
        <el-button
          class="session-sidebar__export"
          :icon="Download"
          :disabled="!canExport"
        >
          导出
        </el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="md" data-test="session-export-md">
              导出 Markdown
            </el-dropdown-item>
            <el-dropdown-item command="json" data-test="session-export-json">
              导出 JSON
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>

    <!-- 列表主体 -->
    <div class="session-sidebar__body">
      <!-- 加载态 -->
      <div v-if="store.sessionsLoading" class="session-sidebar__state" data-test="session-loading">
        <el-skeleton :rows="4" animated />
      </div>

      <!-- 错误态（重试） -->
      <div v-else-if="store.sessionError" class="session-sidebar__state is-error" data-test="session-error">
        <p class="session-sidebar__error-text">会话列表加载失败</p>
        <el-button size="small" :icon="Refresh" @click="store.fetchSessions()">
          重试
        </el-button>
      </div>

      <!-- 空态 -->
      <div v-else-if="isEmpty" class="session-sidebar__state" data-test="session-empty">
        <el-icon class="session-sidebar__empty-icon"><ChatDotRound /></el-icon>
        <p class="session-sidebar__empty-text">
          暂无会话，点击上方 ＋ 新建会话开始
        </p>
      </div>

      <!-- 列表 -->
      <template v-else>
        <!-- 活跃会话 -->
        <div class="session-sidebar__list">
          <div
            v-for="s in store.sessions"
            :key="s.thread_id"
            class="session-sidebar__item"
            :class="{ 'is-active': isActive(s.thread_id) }"
            :data-test="`session-item-${s.thread_id}`"
            @click="onSelect(s)"
          >
            <div class="session-sidebar__item-main">
              <!-- 内联重命名输入 -->
              <el-input
                v-if="renamingId === s.thread_id"
                ref="renameInputRef"
                v-model="renameDraft"
                size="small"
                class="session-sidebar__rename-input"
                data-test="session-rename-input"
                @keyup.enter="confirmRename"
                @keyup.esc="cancelRename"
                @blur="confirmRename"
                @click.stop
              />
              <template v-else>
                <span class="session-sidebar__item-title" :title="s.title">
                  {{ s.title || '新会话' }}
                </span>
                <el-tag
                  v-if="modelLabel(s)"
                  size="small"
                  effect="plain"
                  class="session-sidebar__item-model"
                >
                  {{ modelLabel(s) }}
                </el-tag>
              </template>
            </div>

            <!-- ⋯ 菜单 -->
            <el-dropdown
              trigger="click"
              class="session-sidebar__item-menu"
              data-test="session-item-menu"
              @click.stop
              @command="(cmd: string) => { if (cmd === 'rename') beginRename(s); else if (cmd === 'archive') onArchive(s); else if (cmd === 'restore') onRestore(s); else if (cmd === 'delete') onDelete(s) }"
            >
              <button
                type="button"
                class="session-sidebar__menu-btn"
                :aria-label="`会话操作：${s.title || '新会话'}`"
              >
                <el-icon :size="14"><MoreFilled /></el-icon>
              </button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="rename" :icon="EditPen">
                    重命名
                  </el-dropdown-item>
                  <el-dropdown-item command="archive" :icon="FolderOpened" divided>
                    归档
                  </el-dropdown-item>
                  <el-dropdown-item command="delete" :icon="Delete">
                    删除
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </div>

        <!-- 已归档折叠分组 -->
        <div
          v-if="store.archivedSessions.length > 0"
          class="session-sidebar__archived"
          data-test="session-archived-group"
        >
          <button
            type="button"
            class="session-sidebar__archived-head"
            :aria-expanded="archivedOpen"
            @click="archivedOpen = !archivedOpen"
          >
            <el-icon :size="14"><Collection /></el-icon>
            <span>已归档（{{ store.archivedSessions.length }}）</span>
            <el-icon class="session-sidebar__archived-caret" :size="14">
              <component :is="archivedOpen ? Fold : Expand" />
            </el-icon>
          </button>

          <div v-if="archivedOpen" class="session-sidebar__list">
            <div
              v-for="s in store.archivedSessions"
              :key="s.thread_id"
              class="session-sidebar__item is-archived"
              :class="{ 'is-active': isActive(s.thread_id) }"
              :data-test="`session-archived-item-${s.thread_id}`"
              @click="onSelect(s)"
            >
              <div class="session-sidebar__item-main">
                <span class="session-sidebar__item-title" :title="s.title">
                  {{ s.title || '新会话' }}
                </span>
              </div>
              <el-dropdown
                trigger="click"
                class="session-sidebar__item-menu"
                @click.stop
                @command="(cmd: string) => { if (cmd === 'rename') beginRename(s); else if (cmd === 'restore') onRestore(s); else if (cmd === 'delete') onDelete(s) }"
              >
                <button
                  type="button"
                  class="session-sidebar__menu-btn"
                  :aria-label="`会话操作：${s.title || '新会话'}`"
                >
                  <el-icon :size="14"><MoreFilled /></el-icon>
                </button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="rename" :icon="EditPen">
                      重命名
                    </el-dropdown-item>
                    <el-dropdown-item command="restore" :icon="Refresh" divided>
                      恢复
                    </el-dropdown-item>
                    <el-dropdown-item command="delete" :icon="Delete">
                      删除
                    </el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </div>
        </div>
      </template>
    </div>
  </aside>
</template>

<style scoped lang="scss">
.session-sidebar {
  display: flex;
  flex-direction: column;
  width: 260px;
  min-width: 260px;
  height: 100%;
  border-right: 1px solid var(--border-default);
  background: var(--bg-elevated);
  transition: var(--theme-transition);
}

/* ── 顶部工具栏 ── */
.session-sidebar__toolbar {
  display: flex;
  gap: var(--space-2);
  padding: var(--space-3);
  border-bottom: 1px solid var(--border-muted);
}

.session-sidebar__new {
  flex: 1;
  clip-path: var(--clip-corner-sm);
}

.session-sidebar__export {
  flex-shrink: 0;
}

/* ── 列表主体 ── */
.session-sidebar__body {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-2);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

/* 空态 / 加载态 / 错误态 */
.session-sidebar__state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  padding: var(--space-8) var(--space-4);
  text-align: center;
}

.session-sidebar__empty-icon {
  font-size: 32px;
  color: var(--text-muted);
}

.session-sidebar__empty-text {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  color: var(--text-muted);
  margin: 0;
}

.session-sidebar__error-text {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  color: var(--status-danger);
  margin: 0;
}

/* ── 会话项 ── */
.session-sidebar__list {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.session-sidebar__item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-2);
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--dur-fast) var(--ease-out-quint);
}

.session-sidebar__item:hover {
  background: var(--brand-primary-soft, rgba(97, 92, 237, 0.08));
}

.session-sidebar__item.is-active {
  border-color: var(--brand-primary);
  background: var(--brand-primary-soft, rgba(97, 92, 237, 0.12));
  box-shadow: var(--glow-primary-soft);
}

.session-sidebar__item.is-archived {
  opacity: 0.72;
}

.session-sidebar__item-main {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.session-sidebar__item-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  color: var(--text-primary);
}

.session-sidebar__item-model {
  flex-shrink: 0;
  max-width: 72px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.session-sidebar__rename-input {
  flex: 1;
}

.session-sidebar__item-menu {
  flex-shrink: 0;
}

.session-sidebar__menu-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  transition: all var(--dur-fast) var(--ease-out-quint);
}

.session-sidebar__menu-btn:hover {
  color: var(--brand-primary);
  background: var(--bg-card);
}

/* ── 已归档折叠分组 ── */
.session-sidebar__archived {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  border-top: 1px solid var(--border-muted);
  padding-top: var(--space-2);
}

.session-sidebar__archived-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2);
  border: none;
  background: transparent;
  color: var(--text-muted);
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
  letter-spacing: 0.08em;
  cursor: pointer;
  transition: color var(--dur-fast) var(--ease-out-quint);
}

.session-sidebar__archived-head:hover {
  color: var(--brand-primary);
}

.session-sidebar__archived-caret {
  margin-left: auto;
}
</style>
