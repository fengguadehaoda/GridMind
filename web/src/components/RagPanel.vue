<template>
  <div class="rag-panel">
    <h4 class="panel-title">
      <el-icon style="margin-right: 6px"><Reading /></el-icon>
      知识库检索结果
      <el-tag
        :type="answer.refuse ? 'danger' : confidenceTag"
        size="small"
        style="margin-left: 8px"
      >
        {{ answer.refuse ? '已拒答' : `置信度: ${(answer.confidence * 100).toFixed(0)}%` }}
      </el-tag>
    </h4>

    <div v-if="answer.refuse" class="refuse-banner">
      <el-alert
        :title="answer.refuse_reason || '知识库未找到相关信息，已拒答'"
        type="warning"
        :closable="false"
        show-icon
      />
    </div>

    <!-- M-4 图谱问答面板（graph_answer 存在时内嵌于 sources 区之前，US-1 不跳页） -->
    <GraphQAPanel
      v-if="answer.graph_answer"
      :graph-answer="answer.graph_answer"
      :fallback-paths="answer.graph_paths"
      :sources="answer.sources || []"
    />

    <!-- M-3 来源引用卡片区（P0-3 卡片 / P1-1 多文档筛选 / P2-2 折叠记忆） -->
    <div v-if="sources.length" class="sources-section">
      <div
        class="sources-header"
        role="button"
        tabindex="0"
        @click="toggle"
        @keydown.enter="toggle"
      >
        <span class="sources-title">
          📄 来源引用（{{ sources.length }} 条 · {{ groups.length }} 个文档）
        </span>
        <el-icon class="collapse-arrow" :class="{ expanded: !collapsed }"><ArrowDown /></el-icon>
      </div>
      <DocFilterChips
        v-if="!collapsed && groups.length >= 2"
        v-model="activeDocId"
        :groups="groups"
        class="sources-filter"
      />
      <div v-if="!collapsed" class="sources-list">
        <CitationCard
          v-for="(source, i) in filteredSources"
          :key="i"
          :source="source"
          :index="i"
        />
      </div>
    </div>

    <!-- 旧 citations 纯文本回退（K-3：sources 空但有 citations） -->
    <el-collapse v-else-if="answer.citations?.length" style="margin-top: 8px">
      <el-collapse-item title="📄 引用来源" name="citations">
        <div v-for="(cite, i) in answer.citations" :key="i" class="citation-item">
          <PulseDot tone="info" :size="6" />
          <el-tag size="small" type="info" style="margin-right: 6px">[{{ i + 1 }}]</el-tag>
          <span class="citation-text">{{ cite }}</span>
        </div>
      </el-collapse-item>
    </el-collapse>

    <!-- 图谱路径 -->
    <el-collapse v-if="answer.graph_paths?.length" style="margin-top: 8px">
      <el-collapse-item title="🔗 图谱检索路径" name="graph">
        <div v-for="(path, i) in answer.graph_paths" :key="i" class="graph-path">
          <div class="path-label">路径 {{ i + 1 }}</div>
          <div class="path-nodes">
            <template v-for="(node, j) in path" :key="j">
              <el-tag size="small" type="primary" class="path-node">{{ node }}</el-tag>
              <el-icon v-if="j < path.length - 1" class="path-arrow"><ArrowRight /></el-icon>
            </template>
          </div>
        </div>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { Reading, ArrowRight, ArrowDown } from '@element-plus/icons-vue'
import type { KnowledgeAnswer, SourceRef } from '../types'
import PulseDot from './background/PulseDot.vue'
import {
  filterSourcesByDoc,
  groupSourcesByDoc,
  useSourcesCollapse,
} from '../composables/useKbSources'
import CitationCard from './kb/CitationCard.vue'
import DocFilterChips from './kb/DocFilterChips.vue'
// M-4：图谱问答面板（answer.graph_answer 存在时渲染，位于 sources 区之前）
import GraphQAPanel from './GraphQAPanel.vue'

const props = defineProps<{ answer: KnowledgeAnswer }>()

const confidenceTag = computed(() => {
  const c = props.answer.confidence
  if (c >= 0.8) return 'success'
  if (c >= 0.5) return 'warning'
  return 'danger'
})

// ── M-3 来源引用（K-3 渲染优先级：sources → citations → 不渲染）──
const sources = computed<SourceRef[]>(() => props.answer.sources || [])
const groups = computed(() => groupSourcesByDoc(sources.value))
// 引用区默认折叠 + localStorage 记忆（P2-2）
const { collapsed, toggle } = useSourcesCollapse()
// 文档筛选（纯前端，P1-1）；组件随消息重挂载，无需跨消息复位
const activeDocId = ref<string | null>(null)
const filteredSources = computed(() => filterSourcesByDoc(sources.value, activeDocId.value))
</script>

<style scoped>
.rag-panel {
  position: relative;
  background: var(--bg-card);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  padding: var(--space-4);
  clip-path: var(--clip-corner-sm);
  overflow: hidden;
  transition: var(--theme-transition);
}

/* 顶部流光（仅暗主题且非 reduced motion） */
.rag-panel::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
  background: linear-gradient(
    90deg,
    transparent 0%,
    var(--brand-primary) 50%,
    transparent 100%
  );
  background-size: 200% 100%;
  animation: gm-shimmer 4s linear infinite;
  opacity: 0.6;
  pointer-events: none;
}

@media (prefers-reduced-motion: reduce) {
  .rag-panel::before {
    animation: none;
    background: var(--brand-primary);
    opacity: 0.3;
  }
}

.panel-title {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
  margin-bottom: var(--space-2);
  display: flex;
  align-items: center;
  text-transform: uppercase;
  letter-spacing: 0.1em;
}

.refuse-banner {
  margin-bottom: var(--space-2);
}

/* ── M-3 来源引用卡片区 ── */
.sources-section {
  margin-top: var(--space-1);
}

.sources-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
  padding: var(--space-2);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  transition: var(--theme-transition);
  user-select: none;
}

.sources-header:hover {
  border-color: var(--brand-primary);
}

.sources-title {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
}

.collapse-arrow {
  color: var(--text-muted);
  transition: transform var(--dur-fast, 0.2s) var(--ease-out-quint, ease-out);
}

.collapse-arrow.expanded {
  transform: rotate(180deg);
}

.sources-filter {
  margin-top: var(--space-1);
}

.sources-list {
  margin-top: var(--space-1);
}

/* ── 旧 citations 纯文本回退 ── */
.citation-item {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  padding: var(--space-1) 0;
  font-size: var(--fs-sm);
  line-height: var(--lh-normal);
}

.citation-text {
  color: var(--text-secondary);
  flex: 1;
}

.graph-path {
  padding: var(--space-2) 0;
}

.path-label {
  font-size: var(--fs-xs);
  color: var(--text-muted);
  margin-bottom: var(--space-1);
  font-family: var(--font-cn);
}

.path-nodes {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-1);
}

.path-node {
  font-size: var(--fs-xs);
}

.path-arrow {
  color: var(--text-muted);
  font-size: var(--fs-xs);
}
</style>
