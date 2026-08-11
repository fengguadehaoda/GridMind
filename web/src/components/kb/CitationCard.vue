<template>
  <div class="citation-card">
    <div class="card-header">
      <span class="card-index">[{{ index + 1 }}]</span>
      <span class="card-title" :title="label">{{ label }}</span>
      <span v-if="scoreLabel" class="card-score">匹配度 {{ scoreLabel }}</span>
    </div>

    <div class="card-meta">
      <span v-if="sectionLabel" class="card-section">{{ sectionLabel }}</span>
      <button type="button" class="doc-id-copy" :class="{ copied }" @click="copyDocId">
        <span class="doc-id-text">doc_id: {{ docIdLabel }}</span>
        <el-icon class="doc-id-icon"><CopyDocument /></el-icon>
        <span v-if="copied" class="copied-tip">已复制</span>
      </button>
    </div>

    <div class="card-snippet">{{ snippetLabel }}</div>

    <div class="card-actions">
      <button
        type="button"
        class="toggle-excerpt"
        :disabled="!canExpand"
        @click="toggleExpand"
      >
        {{ expanded ? '收起原文' : '▶ 点开查看原文' }}
      </button>
      <span v-if="!canExpand" class="excerpt-unavailable">原文暂不可用</span>
      <span v-if="canExpand && chunkLabel" class="chunk-label">{{ chunkLabel }}</span>
    </div>

    <div v-if="expanded && canExpand" class="card-excerpt">
      <pre>{{ source.content_excerpt }}</pre>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { CopyDocument } from '@element-plus/icons-vue'
import type { SourceRef } from '../../types'
import { formatScore, sourceLabel } from '../../composables/useKbSources'

const props = defineProps<{ source: SourceRef; index: number }>()

const expanded = ref(false)
const copied = ref(false)

/** 文档显示名（K-5：filename/title/(未知文档)） */
const label = computed(() => sourceLabel(props.source))
/** doc_id 降级展示（K-5） */
const docIdLabel = computed(() => props.source.doc_id || '(未知文档)')
/** 匹配度标签（K-5：score 缺失 → 不渲染） */
const scoreLabel = computed(() => formatScore(props.source.score))
/** 章节标签（第 N 节） */
const sectionLabel = computed(() => (props.source.section ? `第 ${props.source.section} 节` : ''))
/** 摘要降级（K-5） */
const snippetLabel = computed(() => props.source.snippet?.trim() || '（该片段暂无摘要）')
/** 原文可用性（K-5：content_excerpt 空 → 按钮置灰） */
const canExpand = computed(() => !!props.source.content_excerpt?.trim())
/** 分片位置（chunk_index+1 / total_chunks，0-based 对齐 meta.chunk_index） */
const chunkLabel = computed(() => {
  if (props.source.chunk_index === null || props.source.chunk_index === undefined) return ''
  const total = props.source.total_chunks ?? '?'
  return `片段 ${props.source.chunk_index + 1}/${total}`
})

function toggleExpand(): void {
  if (canExpand.value) expanded.value = !expanded.value
}

/** 复制 doc_id（原生 navigator.clipboard，架构 §六：不做 clipboard 库） */
async function copyDocId(): Promise<void> {
  const text = props.source.doc_id || ''
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text)
    } else {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
    copied.value = true
    window.setTimeout(() => {
      copied.value = false
    }, 1500)
  } catch {
    /* 复制失败静默（剪贴板权限受限等场景） */
  }
}
</script>

<style scoped>
.citation-card {
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  padding: var(--space-3) var(--space-4);
  margin-bottom: var(--space-2);
  transition: var(--theme-transition);
}

.citation-card:hover {
  border-color: var(--brand-primary);
}

.card-header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.card-index {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}

.card-title {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
  flex: 1;
}

.card-score {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--brand-primary);
  flex-shrink: 0;
}

.card-meta {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-top: var(--space-1);
  flex-wrap: wrap;
}

.card-section {
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  color: var(--text-secondary);
}

.doc-id-copy {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: none;
  background: transparent;
  padding: 2px 4px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text-muted);
  transition: var(--theme-transition);
}

.doc-id-copy:hover {
  color: var(--brand-primary);
  background: var(--brand-primary-fade);
}

.doc-id-copy.copied {
  color: var(--status-success, #67c23a);
}

.doc-id-text {
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.copied-tip {
  font-family: var(--font-cn);
}

.card-snippet {
  margin-top: var(--space-1);
  font-size: var(--fs-sm);
  line-height: var(--lh-normal);
  color: var(--text-secondary);
}

.card-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-2);
  flex-wrap: wrap;
}

.toggle-excerpt {
  border: 1px solid var(--border-default);
  background: transparent;
  color: var(--brand-primary);
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  padding: 2px 10px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: var(--theme-transition);
}

.toggle-excerpt:hover:not(:disabled) {
  border-color: var(--brand-primary);
  background: var(--brand-primary-fade);
}

.toggle-excerpt:disabled {
  color: var(--text-muted);
  cursor: not-allowed;
  opacity: 0.6;
}

.excerpt-unavailable {
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}

.chunk-label {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}

.card-excerpt {
  margin-top: var(--space-2);
  border-top: 1px dashed var(--border-default);
  padding-top: var(--space-2);
}

.card-excerpt pre {
  font-family: var(--font-body);
  font-size: var(--fs-sm);
  line-height: var(--lh-loose);
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
  max-height: 320px;
  overflow-y: auto;
}
</style>
