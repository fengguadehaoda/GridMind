<!--
  v1.5.1 T03 · F2 主组件 · StepInlineEditor.vue

  角色：步骤内联编辑器（textarea + 3 按钮 + a11y + focus trap）。
  父组件：ReasoningChainPanel.vue 在 v-else 时挂载它（详见该文件 §T03 集成段）。

  关键约束（架构 §1.2 + 主理人决策 7.4/7.5）：
    - 仅 user content 可编辑（store 端已经用 step.isEditable + status gating 拦截，
      本组件是 UI 表层，不再做权限判断；防御式 try/catch 仅用于友好错误提示）
    - 弹窗 z-index 层级：toast 1000 > 弹窗 100（不适用本组件，仅 inline；本组件自身 z-index 0）
    - input textarea 实时同步到 reasoning.draftSteps[stepId]（不提交后端）
    - 重跑按钮调 store.rerunFromStep → POST /sessions/{id}/rewind
    - 保存按钮仅持久化到 store.draftSteps（v1.5.2 才向后端落盘；当前仅"前端草稿"）
    - 取消按钮调 store.cancelEdit（discard 草稿 + 回 running/paused）
    - 键盘：Esc → 取消；Ctrl/Cmd+Enter → 重跑
    - a11y：role=group + aria-label + focus trap 让 Tab 在 4 个可聚焦元素
      （textarea + 3 按钮）间循环
-->
<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { Check, Close, RefreshRight } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useReasoningStore } from '@/stores/reasoning'
import { useFocusTrap } from '@/composables/useFocusTrap'
import type { ReasoningStep } from '@/types'

const props = defineProps<{
  /** 当前编辑的 stepId；reasoning.editingStepId 必等于该值才能进入此组件 */
  stepId: string
}>()

const reasoning = useReasoningStore()

// ═══ refs ═══
const containerRef = ref<HTMLElement | null>(null)
const textareaRef = ref<HTMLTextAreaElement | null>(null)
const isRerunning = ref(false)
/** 字符数上限（PRD §3.2.4 · 编辑内容超过 4000 字符禁用重跑 + 红色字数提示） */
const MAX_CHARS = 4000

// ═══ 焦点 trap（T01 useFocusTrap · 与 F4 弹窗共用一套实现）═══
// autoActivate=true：onMounted 时自动挂 keydown + 下一帧聚焦首个可聚焦元素（textarea）
useFocusTrap({ containerRef })

// ═══ computed getters ═══
/** 当前 step（强类型 ReasoningStep；isEditable + promptFragment 等） */
const step = computed<ReasoningStep | null>(
  () => reasoning.steps.find((s) => s.id === props.stepId) ?? null,
)

/** 草稿文本（writable：v-model 双向绑定到 textarea） */
const draft = computed<string>({
  get: () => {
    const cached = reasoning.draftSteps[props.stepId]
    if (typeof cached === 'string') return cached
    // 兜底：未 beginEdit 直接进编辑器时，从 step 取 promptFragment
    return step.value?.promptFragment ?? ''
  },
  set: (val: string) => {
    reasoning.updateDraft(props.stepId, val)
  },
})

/** 实时字符数（aria-live 提示用） */
const characterCount = computed<number>(() => draft.value.length)
/** 是否超字数 */
const overLimit = computed<boolean>(() => characterCount.value > MAX_CHARS)
/** 重跑按钮可点：未在 rerunning、未超字数、当前正在编辑该 step */
const canRerun = computed<boolean>(
  () => !isRerunning.value && !overLimit.value && reasoning.editingStepId === props.stepId,
)

// ═══ textarea 自动高度（按内容增长）═══
watch(draft, () => {
  if (!textareaRef.value) return
  const ta = textareaRef.value
  ta.style.height = 'auto'
  // +2 px 防止某些浏览器亚像素抖动
  ta.style.height = `${ta.scrollHeight + 2}px`
})

// ═══ mount 后聚焦 textarea + 选中文本（便于调度员直接覆盖）═══
onMounted(async () => {
  await nextTick()
  const ta = textareaRef.value
  if (!ta) return
  ta.focus()
  // 选中文本首段：长度 0 时光标定位到末尾
  const len = ta.value.length
  ta.setSelectionRange(0, Math.min(len, 80))
})

// ═══ 三个按钮的 handler ═══
function handleSave(): void {
  if (overLimit.value) {
    ElMessage.warning(`超过 ${MAX_CHARS} 字上限，无法保存草稿`)
    return
  }
  // draft 本就通过 v-model 持续同步到 store；这里再调一次确保最终态
  reasoning.updateDraft(props.stepId, draft.value)
  ElMessage.success('已保存草稿（可点"重跑"从此步重新执行）')
}

async function handleRerun(): Promise<void> {
  if (!canRerun.value) return
  isRerunning.value = true
  try {
    const result = await reasoning.rerunFromStep(props.stepId, draft.value)
    const status = result && typeof result === 'object' && 'status' in result
      ? String((result as { status?: unknown }).status ?? 'unknown')
      : 'submitted'
    ElMessage.success(`已重跑步骤 #${step.value?.index ?? '?'}（${status}）`)
  } catch (err) {
    // T07 · R-X5 修复：仅 dev 控制台记录完整异常，用户侧通用 message
    // 原始 err.message 可能包含路径 / token / 内部变量名（架构 §6.8）
    console.error('[StepInlineEditor.rerun] 操作失败：', err)
    ElMessage.error('重跑失败，请稍后重试')
  } finally {
    isRerunning.value = false
  }
}

function handleCancel(): void {
  // discard 草稿 + 退出 editing 态（store.cancelEdit 内已处理 status 回滚）
  reasoning.cancelEdit()
}

// ═══ 键盘快捷键（PRD §3.2.5）═══
function handleKeydown(event: KeyboardEvent): void {
  // Esc → 取消
  if (event.key === 'Escape') {
    event.preventDefault()
    handleCancel()
    return
  }
  // Ctrl/Cmd+Enter → 重跑
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
    event.preventDefault()
    void handleRerun()
  }
}
</script>

<template>
  <div
    ref="containerRef"
    class="step-inline-editor"
    role="group"
    aria-label="步骤内联编辑器"
  >
    <label :for="`step-edit-textarea-${stepId}`" class="visually-hidden">
      编辑步骤 prompt 片段
    </label>
    <textarea
      :id="`step-edit-textarea-${stepId}`"
      ref="textareaRef"
      v-model="draft"
      class="step-textarea"
      :placeholder="`编辑步骤 #${step?.index ?? '?'}：${step?.name ?? ''} ...`"
      :aria-label="`编辑步骤 #${step?.index ?? '?'}：${step?.name ?? ''}`"
      :aria-describedby="`char-count-${stepId}`"
      :aria-invalid="overLimit"
      :maxlength="MAX_CHARS + 100"
      rows="3"
      @keydown="handleKeydown"
    />
    <div class="step-meta">
      <span
        :id="`char-count-${stepId}`"
        :class="['char-count', overLimit && 'char-count--over']"
        aria-live="polite"
      >
        {{ characterCount }} / {{ MAX_CHARS }} 字
        <template v-if="overLimit"> · 超过上限</template>
      </span>
    </div>
    <div class="step-actions">
      <el-button
        :icon="Check"
        size="small"
        type="primary"
        plain
        :disabled="overLimit"
        @click="handleSave"
      >
        💾 保存草稿
      </el-button>
      <el-button
        :icon="RefreshRight"
        size="small"
        type="success"
        :loading="isRerunning"
        :disabled="!canRerun"
        :aria-busy="isRerunning"
        aria-label="从此步重跑"
        @click="handleRerun"
      >
        <template v-if="isRerunning">重跑中…</template>
        <template v-else>🔄 从此步重跑</template>
      </el-button>
      <el-button
        :icon="Close"
        size="small"
        type="danger"
        plain
        aria-label="取消编辑"
        @click="handleCancel"
      >
        ✕ 取消
      </el-button>
    </div>
    <div class="step-help">
      <small>
        提示：<kbd>Ctrl/Cmd + Enter</kbd> 重跑 · <kbd>Esc</kbd> 取消 · 仅 user content 可编辑
      </small>
    </div>
  </div>
</template>

<style scoped>
/* ──────────────────────────────────────────────────────────
 * v1.5.1 T03 F2 编辑器样式
 * 色板使用 tokens.shared.scss 全局变量；与现有 v1.5.0 主题适配
 * ────────────────────────────────────────────────────────── */
.step-inline-editor {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 8px;
  padding: 12px;
  background: var(--bg-card, #1e1e2e);
  border: 1px solid var(--brand-primary, #4cc2ff);
  border-radius: var(--radius-md, 8px);
  /* F2 与 F4 弹窗分层：本组件 inline 嵌在 panel 内，z-index 默认 0 即可 */
}

.step-textarea {
  width: 100%;
  min-height: 72px;
  max-height: 320px;
  padding: 8px 10px;
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: var(--fs-sm, 13px);
  line-height: 1.5;
  border: 1px solid var(--border-default, #444);
  border-radius: var(--radius-sm, 4px);
  resize: vertical;
  background: var(--bg-base, #14141c);
  color: var(--text-primary, #f0f0f0);
  box-sizing: border-box;
}

.step-textarea:focus {
  outline: 2px solid var(--brand-primary, #4cc2ff);
  outline-offset: 2px;
}

.step-textarea[aria-invalid='true'] {
  border-color: var(--status-critical-fg, #ff5e6c);
  background: rgba(255, 94, 108, 0.04);
}

.step-meta {
  display: flex;
  justify-content: flex-end;
}

.char-count {
  font-family: var(--font-mono, ui-monospace);
  font-size: var(--fs-xs, 11px);
  color: var(--text-muted, #888);
  font-variant-numeric: tabular-nums;
}

.char-count--over {
  color: var(--status-critical-fg, #ff5e6c);
  font-weight: var(--fw-semibold, 600);
}

.step-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
}

.step-help {
  font-size: var(--fs-xs, 11px);
  color: var(--text-muted, #888);
  text-align: right;
}

.step-help kbd {
  padding: 1px 5px;
  margin: 0 2px;
  font-family: var(--font-mono, ui-monospace);
  font-size: 10px;
  background: var(--bg-card-solid, #2a2a3a);
  border: 1px solid var(--border-default, #444);
  border-radius: 3px;
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
</style>
