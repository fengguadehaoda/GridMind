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

    <!-- 回答内容 -->
    <div class="answer-text">
      <div v-html="renderedAnswer" />
    </div>

    <el-collapse style="margin-top: 8px">
      <!-- 引用来源 -->
      <el-collapse-item v-if="answer.citations?.length" title="📄 引用来源" name="citations">
        <div v-for="(cite, i) in answer.citations" :key="i" class="citation-item">
          <PulseDot tone="info" :size="6" />
          <el-tag size="small" type="info" style="margin-right: 6px">[{{ i + 1 }}]</el-tag>
          <span class="citation-text">{{ cite }}</span>
        </div>
      </el-collapse-item>

      <!-- 图谱路径 -->
      <el-collapse-item v-if="answer.graph_paths?.length" title="🔗 图谱检索路径" name="graph">
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
import { computed } from 'vue'
import { Reading, ArrowRight } from '@element-plus/icons-vue'
import type { KnowledgeAnswer } from '../types'
import PulseDot from './background/PulseDot.vue'
import { useReducedMotion } from '../composables/useReducedMotion'

const props = defineProps<{ answer: KnowledgeAnswer }>()
const prefersReducedMotion = useReducedMotion()

const confidenceTag = computed(() => {
  const c = props.answer.confidence
  if (c >= 0.8) return 'success'
  if (c >= 0.5) return 'warning'
  return 'danger'
})

const renderedAnswer = computed(() => {
  if (!props.answer.answer) return ''
  return props.answer.answer
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\n/g, '<br>')
})
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

.answer-text {
  font-family: var(--font-body);
  font-size: var(--fs-md);
  line-height: var(--lh-loose);
  color: var(--text-primary);
  padding: var(--space-2) 0;
}

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
