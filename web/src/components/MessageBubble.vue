<template>
  <div
    class="message-bubble"
    :class="[msg.role, { loading: msg.loading }]"
  >
    <!-- 头像 / 角色徽章 -->
    <div class="avatar">
      <div class="avatar-hex">
        <span class="avatar-icon">{{ avatarIcon }}</span>
      </div>
      <div class="avatar-role">{{ roleLabel }}</div>
    </div>

    <!-- 内容 -->
    <div class="bubble-content">
      <div class="message-header">
        <AgentBadge :agent="agentType" size="sm" :show-label="false" />
        <span class="role-name">{{ roleLabel }}</span>
        <span class="timestamp">{{ timeStr }}</span>
      </div>

      <div class="message-body" v-if="msg.content || msg.loading">
        <div class="message-text message-content" v-if="msg.content">
          <div v-for="(seg, i) in renderedContent" :key="i">
            <el-tag v-if="seg.type === 'tag'" :type="seg.tagType" size="small" style="margin: 2px 4px 2px 0">
              {{ seg.text }}
            </el-tag>
            <span v-else-if="seg.type === 'text'" v-html="seg.text" />
          </div>
        </div>
        <ThinkingIndicator v-if="msg.loading" label="思考中" :speed="1.2" />
      </div>

      <!-- 健康评分卡片 -->
      <div v-if="msg.healthScores?.length" class="context-block">
        <HealthCard :scores="msg.healthScores" />
      </div>

      <!-- 知识答案 -->
      <div v-if="msg.knowledgeAnswer" class="context-block">
        <RagPanel :answer="msg.knowledgeAnswer" />
      </div>

      <!-- 可解释性 AI 推理链（P0） -->
      <div v-if="shouldShowReasoning" class="context-block">
        <ReasoningChainPanel
          v-if="reasoningResult"
          :result="reasoningResult"
          :initially-collapsed="false"
        />
        <div v-else class="reasoning-loading">
          <el-button size="small" @click="loadReasoning" :loading="loadingReasoning">
            🔍 加载推理链
          </el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { ChatMessage, DiagnosisFusionResult } from '../types'
import HealthCard from './HealthCard.vue'
import RagPanel from './RagPanel.vue'
import ReasoningChainPanel from './ReasoningChainPanel.vue'
import AgentBadge from './chat/AgentBadge.vue'
import ThinkingIndicator from './chat/ThinkingIndicator.vue'
import { getDiagnosisReasoning } from '../api/chat'

const props = defineProps<{ msg: ChatMessage }>()

const reasoningResult = ref<DiagnosisFusionResult | null>(null)
const loadingReasoning = ref(false)

/** 是否应该展示推理链（仅 diagnosis_agent 消息 + 含 metadata.has_reasoning_chain） */
const shouldShowReasoning = computed(() => {
  return (
    props.msg.metadata?.has_reasoning_chain === true &&
    props.msg.metadata?.agent_name === 'diagnosis_agent'
  )
})

/** 自动加载推理链（消息挂载时拉取） */
async function loadReasoning() {
  if (!props.msg.metadata?.thread_id || loadingReasoning.value) return
  loadingReasoning.value = true
  try {
    reasoningResult.value = await getDiagnosisReasoning(props.msg.metadata.thread_id)
  } catch (err) {
    console.warn('Failed to load reasoning chain:', err)
  } finally {
    loadingReasoning.value = false
  }
}

onMounted(() => {
  if (shouldShowReasoning.value) {
    void loadReasoning()
  }
})

const avatarIcon = computed(() => {
  switch (props.msg.role) {
    case 'user': return '我'
    case 'assistant': return 'AI'
    case 'system': return 'SYS'
    case 'tool': return '⚙'
    default: return '?'
  }
})

const agentType = computed(() => {
  if (props.msg.role === 'user') return 'user' as const
  if (props.msg.role === 'system') return 'system' as const
  // assistant 和 tool 在缺少 agent_name 时归到 orchestrator
  return 'orchestrator' as const
})

const roleLabel = computed(() => {
  switch (props.msg.role) {
    case 'user': return '我'
    case 'assistant': return 'AI 助手'
    case 'system': return '系统'
    case 'tool': return '工具调用'
    default: return props.msg.role
  }
})

const timeStr = computed(() => {
  try {
    const d = new Date(props.msg.timestamp)
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  } catch { return '' }
})

/** 将内容按【标签】分割，高亮对话中关键标签 */
const renderedContent = computed(() => {
  if (!props.msg.content) return [{ type: 'text', text: '' }]
  const segments: { type: string; text?: string; tagType?: string }[] = []
  const parts = props.msg.content.split(/(【[^】]+】)/g)
  for (const part of parts) {
    if (/^【[^】]+】$/.test(part)) {
      const tagText = part.slice(1, -1)
      let tagType = ''
      if (tagText.includes('异常') || tagText.includes('严重')) tagType = 'danger'
      else if (tagText.includes('预警') || tagText.includes('警告')) tagType = 'warning'
      else if (tagText.includes('批准') || tagText.includes('成功')) tagType = 'success'
      else if (tagText.includes('知识') || tagText.includes('查询')) tagType = 'primary'
      else tagType = 'info'
      segments.push({ type: 'tag', text: part, tagType })
    } else if (part) {
      // 转义 HTML，保留换行
      const escaped = part
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/\n/g, '<br>')
      segments.push({ type: 'text', text: escaped })
    }
  }
  return segments
})
</script>

<style scoped>
.message-bubble {
  display: flex;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-6);
  transition: background var(--dur-fast) var(--ease-out-quint);
}

.message-bubble:hover {
  background: var(--brand-primary-fade);
}

.message-bubble.user {
  flex-direction: row-reverse;
}

/* ── 头像区 ───────────────────────── */
.avatar {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-1);
  padding-top: var(--space-1);
}

.avatar-hex {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-card-solid);
  border: 1px solid var(--border-default);
  clip-path: var(--clip-hex);
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  font-weight: var(--fw-bold);
  color: var(--brand-primary);
  transition: var(--theme-transition);
}

.avatar-icon {
  display: block;
}

.message-bubble.user .avatar-hex {
  border-color: var(--brand-accent);
  color: var(--brand-accent);
}

.message-bubble.system .avatar-hex {
  border-color: var(--status-danger);
  color: var(--status-danger);
}

.message-bubble.tool .avatar-hex {
  border-color: var(--status-warning);
  color: var(--status-warning);
}

.avatar-role {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.1em;
}

/* ── 内容区 ───────────────────────── */
.bubble-content {
  max-width: 75%;
  min-width: 0;
  flex: 1;
}

.user .bubble-content {
  text-align: right;
}

.message-header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
}

.user .message-header {
  justify-content: flex-end;
}

.role-name {
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
}

.timestamp {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text-muted);
  margin-left: auto;
}

.user .timestamp {
  margin-left: 0;
  margin-right: auto;
}

/* ── 消息体（4 角色切角 + 发光）── */
.message-body {
  background: var(--role-assistant-bg);
  border: 1px solid var(--role-assistant-border);
  border-radius: var(--radius-md);
  padding: var(--space-4) var(--space-5);
  text-align: left;
  position: relative;
  box-shadow: 0 0 0 1px transparent;
  transition: var(--theme-transition);
}

.message-body::before {
  content: '';
  position: absolute;
  left: 0;
  top: var(--space-4);
  bottom: var(--space-4);
  width: 3px;
  background: var(--brand-primary);
  box-shadow: 0 0 8px var(--brand-primary);
  border-radius: 2px;
}

/* user 角色：右侧琥珀发光 */
.message-bubble.user .message-body {
  background: var(--role-user-bg);
  border-color: var(--role-user-border);
  clip-path: var(--clip-corner-sm);
}

.message-bubble.user .message-body::before {
  left: auto;
  right: 0;
  background: var(--brand-accent);
  box-shadow: 0 0 8px var(--brand-accent);
}

/* assistant 角色：左侧青色发光 + 切角 */
.message-bubble.assistant .message-body {
  background: var(--role-assistant-bg);
  border-color: var(--role-assistant-border);
  clip-path: var(--clip-corner-sm);
}

/* tool 角色：直角，无发光 */
.message-bubble.tool .message-body {
  background: var(--role-tool-bg);
  border-color: var(--role-tool-border);
  border-radius: var(--radius-sm);
  border-left: 3px solid var(--status-warning);
}

.message-bubble.tool .message-body::before {
  display: none;
}

/* system 角色：透明背景 + 居中 */
.message-bubble.system {
  justify-content: center;
}

.message-bubble.system .avatar {
  display: none;
}

.message-bubble.system .bubble-content {
  max-width: 80%;
  text-align: center;
}

.message-bubble.system .message-body {
  background: var(--role-system-bg);
  border: 1px dashed var(--role-system-border);
  color: var(--text-muted);
  font-size: var(--fs-sm);
  text-align: center;
  font-family: var(--font-mono);
  padding: var(--space-2) var(--space-4);
}

.message-bubble.system .message-body::before {
  display: none;
}

.message-text {
  font-family: var(--font-body);
  font-size: var(--fs-md);
  line-height: var(--lh-loose);
  word-break: break-word;
  color: var(--text-primary);
}

.context-block {
  margin-top: var(--space-3);
}

.reasoning-loading {
  margin-top: var(--space-2);
  display: flex;
  justify-content: center;
}

/* 打字机光标效果 */
.message-bubble:last-child .message-body:has(.loading) .message-text {
  border-right: 2px solid var(--brand-primary);
  animation: gm-blink 0.8s step-end infinite;
  padding-right: var(--space-1);
}
</style>
