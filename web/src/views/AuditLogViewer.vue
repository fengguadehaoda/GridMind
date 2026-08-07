<script setup lang="ts">
/**
 * AuditLogViewer · HITL 审计日志查看器（v1.4.0 新增）
 *
 * - 列出全部 HITL 决策（approve / reject / edit）
 * - 支持按 thread_id + decision 类型筛选
 * - 3 年保留期提示（合规追溯）
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { fetchAuditLog, fetchAuditByThread } from '@/api/audit'
import type { AuditEntry, HitlAuditDecision } from '@/types'

const route = useRoute()

const allEntries = ref<AuditEntry[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

// 筛选
// F7 修复（QA F7 P1）：命令面板 / HitlBadge 会 push '/audit?filter=pending'，
// 但本组件此前从不读 route.query → 筛选失效。这里拓宽决策筛选类型以支持
// 'pending'（待审批），挂载时从 route.query.filter 初始化。
type DecisionFilter = HitlAuditDecision | 'pending' | ''
const filterThread = ref('')
const filterDecision = ref<DecisionFilter>('')
const retentionYears = ref(3)

const filtered = computed<AuditEntry[]>(() => {
  let list = allEntries.value
  if (filterThread.value.trim()) {
    const q = filterThread.value.trim().toLowerCase()
    list = list.filter(e => (e.thread_id || '').toLowerCase().includes(q))
  }
  if (filterDecision.value) {
    list = list.filter(e => e.decision === filterDecision.value)
  }
  return list
})

const stats = computed(() => {
  const byDecision: Record<string, number> = {}
  for (const e of allEntries.value) {
    const d = e.decision || 'unknown'
    byDecision[d] = (byDecision[d] || 0) + 1
  }
  return byDecision
})

async function load() {
  loading.value = true
  error.value = null
  try {
    // 用 thread 过滤优先（如果填了）
    if (filterThread.value.trim()) {
      const r = await fetchAuditByThread(filterThread.value.trim())
      allEntries.value = r.entries
      retentionYears.value = r.retention_years || 3
    } else {
      const r = await fetchAuditLog(undefined, 200)
      allEntries.value = r.entries
      retentionYears.value = r.retention_years || 3
    }
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

async function applyFilter() {
  await load()
}

async function clearFilter() {
  filterThread.value = ''
  filterDecision.value = ''
  await load()
}

onMounted(() => {
  // F7 修复：读取 route.query.filter 初始化决策筛选（命令面板 / HitlBadge 均 push filter=pending）
  const q = route.query.filter
  if (typeof q === 'string' && (q === 'approved' || q === 'rejected' || q === 'edited' || q === 'pending')) {
    filterDecision.value = q
  }
  void load()
})

function formatTime(ts: number | string | undefined): string {
  if (!ts) return '-'
  if (typeof ts === 'string') return ts
  try {
    return new Date(ts * 1000).toLocaleString('zh-CN')
  } catch {
    return String(ts)
  }
}

function decisionColor(d: string): string {
  if (d === 'approved') return 'var(--gm-success, #10b981)'
  if (d === 'rejected') return 'var(--gm-danger, #ef4444)'
  if (d === 'edited') return 'var(--gm-warning, #f59e0b)'
  if (d === 'pending') return 'var(--gm-accent, #615ced)'
  return 'var(--gm-text-secondary, #6b7280)'
}

function decisionLabel(d: string): string {
  if (d === 'approved') return '✓ 批准'
  if (d === 'rejected') return '✕ 拒绝'
  if (d === 'edited') return '✎ 编辑'
  if (d === 'pending') return '⏳ 待审批'
  return d
}
</script>

<template>
  <div class="audit-viewer">
    <header class="audit-viewer__header">
      <div>
        <h1 class="audit-viewer__title">HITL 审计日志</h1>
        <p class="audit-viewer__subtitle">
          高危操作的合规追溯 · 保留期 <strong>{{ retentionYears }}</strong> 年
        </p>
      </div>
      <button class="audit-viewer__refresh" :disabled="loading" @click="load">
        {{ loading ? '刷新中…' : '🔄 刷新' }}
      </button>
    </header>

    <!-- 筛选栏 -->
    <div class="audit-viewer__filters" data-tour="audit-filter">
      <input
        v-model="filterThread"
        class="audit-viewer__input"
        placeholder="按 thread_id 筛选（支持子串）"
        @keyup.enter="applyFilter"
      />
      <select v-model="filterDecision" class="audit-viewer__select" @change="applyFilter">
        <option value="">全部决策</option>
        <option value="pending">⏳ 待审批</option>
        <option value="approved">✓ 批准</option>
        <option value="rejected">✕ 拒绝</option>
        <option value="edited">✎ 编辑</option>
      </select>
      <button class="audit-viewer__btn" @click="applyFilter">应用</button>
      <button class="audit-viewer__btn audit-viewer__btn--ghost" @click="clearFilter">清空</button>
    </div>

    <!-- 统计 -->
    <div class="audit-viewer__stats" data-tour="audit-stats">
      <div class="audit-viewer__stat">
        <div class="audit-viewer__stat-value">{{ allEntries.length }}</div>
        <div class="audit-viewer__stat-label">总记录</div>
      </div>
      <div class="audit-viewer__stat" v-for="(count, d) in stats" :key="d">
        <div class="audit-viewer__stat-value" :style="{ color: decisionColor(d) }">{{ count }}</div>
        <div class="audit-viewer__stat-label">{{ decisionLabel(d) }}</div>
      </div>
    </div>

    <!-- 错误 -->
    <div v-if="error" class="audit-viewer__error">⚠️ {{ error }}</div>

    <!-- 列表 -->
    <div v-if="filtered.length === 0 && !loading" class="audit-viewer__empty">
      暂无审计记录
    </div>

    <div v-else class="audit-viewer__list" data-tour="audit-list">
      <article
        v-for="entry in filtered"
        :key="entry.id || `${entry.thread_id}-${entry.timestamp}`"
        class="audit-viewer__entry"
      >
        <div class="audit-viewer__entry-head">
          <span class="audit-viewer__entry-decision" :style="{ color: decisionColor(entry.decision) }">
            {{ decisionLabel(entry.decision) }}
          </span>
          <span class="audit-viewer__entry-time">{{ formatTime(entry.timestamp || entry.created_at) }}</span>
        </div>
        <div class="audit-viewer__entry-body">
          <div class="audit-viewer__entry-row">
            <span class="audit-viewer__entry-key">thread_id</span>
            <code class="audit-viewer__entry-val">{{ entry.thread_id }}</code>
          </div>
          <div v-if="entry.actor" class="audit-viewer__entry-row">
            <span class="audit-viewer__entry-key">actor</span>
            <span class="audit-viewer__entry-val">{{ entry.actor }}</span>
          </div>
          <div v-if="entry.tool_name" class="audit-viewer__entry-row">
            <span class="audit-viewer__entry-key">tool</span>
            <span class="audit-viewer__entry-val">{{ entry.tool_name }}</span>
          </div>
          <div v-if="entry.risk_level" class="audit-viewer__entry-row">
            <span class="audit-viewer__entry-key">risk</span>
            <span class="audit-viewer__entry-val">{{ entry.risk_level }}</span>
          </div>
          <div v-if="entry.reason" class="audit-viewer__entry-row">
            <span class="audit-viewer__entry-key">reason</span>
            <span class="audit-viewer__entry-val">{{ entry.reason }}</span>
          </div>
          <details v-if="entry.edited_content" class="audit-viewer__entry-details">
            <summary>编辑内容</summary>
            <pre>{{ entry.edited_content }}</pre>
          </details>
        </div>
      </article>
    </div>
  </div>
</template>

<style scoped lang="scss">
.audit-viewer {
  max-width: 1080px;
  margin: 0 auto;
  padding: 32px 24px 80px;
  color: var(--gm-text-primary, #e5e7eb);
}

.audit-viewer__header {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  margin-bottom: 24px;
}

.audit-viewer__title {
  font-size: 28px;
  font-weight: 700;
  margin: 0;
  background: linear-gradient(90deg, #615ced, #1c64f2);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}

.audit-viewer__subtitle {
  font-size: 14px;
  color: var(--gm-text-secondary, #9ca3af);
  margin: 6px 0 0;
}

.audit-viewer__subtitle strong {
  color: var(--gm-accent, #615ced);
}

.audit-viewer__refresh {
  padding: 8px 16px;
  border-radius: 8px;
  border: 1px solid var(--gm-border, rgba(255, 255, 255, 0.12));
  background: var(--gm-bg-elev, rgba(255, 255, 255, 0.05));
  color: inherit;
  cursor: pointer;
  font-size: 13px;
  transition: all 0.15s ease;
}

.audit-viewer__refresh:hover:not(:disabled) {
  background: var(--gm-bg-hover, rgba(255, 255, 255, 0.08));
  border-color: var(--gm-accent, #615ced);
}

.audit-viewer__refresh:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.audit-viewer__filters {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.audit-viewer__input, .audit-viewer__select {
  padding: 8px 12px;
  border-radius: 8px;
  border: 1px solid var(--gm-border, rgba(255, 255, 255, 0.12));
  background: var(--gm-bg-elev, rgba(255, 255, 255, 0.05));
  color: inherit;
  font-size: 13px;
  flex: 1;
  min-width: 200px;
}

.audit-viewer__input:focus, .audit-viewer__select:focus {
  outline: none;
  border-color: var(--gm-accent, #615ced);
}

.audit-viewer__btn {
  padding: 8px 16px;
  border-radius: 8px;
  border: none;
  background: var(--gm-accent, #615ced);
  color: white;
  cursor: pointer;
  font-size: 13px;
}

.audit-viewer__btn:hover:not(:disabled) {
  filter: brightness(1.1);
}

.audit-viewer__btn--ghost {
  background: transparent;
  color: var(--gm-text-secondary, #9ca3af);
  border: 1px solid var(--gm-border, rgba(255, 255, 255, 0.12));
}

.audit-viewer__stats {
  display: flex;
  gap: 12px;
  margin-bottom: 24px;
  flex-wrap: wrap;
}

.audit-viewer__stat {
  flex: 1;
  min-width: 100px;
  padding: 16px;
  border-radius: 12px;
  background: var(--gm-bg-elev, rgba(255, 255, 255, 0.05));
  border: 1px solid var(--gm-border, rgba(255, 255, 255, 0.08));
  text-align: center;
}

.audit-viewer__stat-value {
  font-size: 24px;
  font-weight: 700;
}

.audit-viewer__stat-label {
  font-size: 11px;
  color: var(--gm-text-secondary, #9ca3af);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-top: 4px;
}

.audit-viewer__error {
  padding: 12px 16px;
  background: rgba(239, 68, 68, 0.1);
  border: 1px solid rgba(239, 68, 68, 0.3);
  border-radius: 8px;
  color: #fca5a5;
  font-size: 13px;
  margin-bottom: 16px;
}

.audit-viewer__empty {
  padding: 60px 20px;
  text-align: center;
  color: var(--gm-text-secondary, #9ca3af);
  font-size: 14px;
}

.audit-viewer__list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.audit-viewer__entry {
  padding: 14px 16px;
  border-radius: 10px;
  background: var(--gm-bg-elev, rgba(255, 255, 255, 0.04));
  border: 1px solid var(--gm-border, rgba(255, 255, 255, 0.08));
  border-left: 3px solid var(--gm-accent, #615ced);
  transition: background 0.15s ease;
}

.audit-viewer__entry:hover {
  background: var(--gm-bg-hover, rgba(255, 255, 255, 0.07));
}

.audit-viewer__entry-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.audit-viewer__entry-decision {
  font-size: 14px;
  font-weight: 600;
}

.audit-viewer__entry-time {
  font-size: 12px;
  color: var(--gm-text-secondary, #9ca3af);
  font-family: var(--font-mono, monospace);
}

.audit-viewer__entry-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.audit-viewer__entry-row {
  display: flex;
  gap: 12px;
  font-size: 13px;
  align-items: baseline;
}

.audit-viewer__entry-key {
  font-size: 11px;
  color: var(--gm-text-secondary, #9ca3af);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  min-width: 64px;
}

.audit-viewer__entry-val {
  flex: 1;
  color: var(--gm-text-primary, #e5e7eb);
}

.audit-viewer__entry-val code {
  font-family: var(--font-mono, monospace);
  background: rgba(255, 255, 255, 0.06);
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 12px;
}

.audit-viewer__entry-details {
  margin-top: 6px;
}

.audit-viewer__entry-details summary {
  cursor: pointer;
  font-size: 12px;
  color: var(--gm-accent, #615ced);
}

.audit-viewer__entry-details pre {
  margin-top: 6px;
  padding: 8px;
  background: rgba(0, 0, 0, 0.3);
  border-radius: 6px;
  font-size: 12px;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>