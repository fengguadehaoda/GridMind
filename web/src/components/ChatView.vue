<!--
  web/src/components/ChatView.vue
  GridMind 主对话视图

  变更历史：
    v1.5.0 T05 — 建立 v1.5.0 聊天 + 演示 + 监控
    v1.5.1 T02 — F1/F4（reasoning + HITL）集成
    v1.5.1 T05 — HitlEditDialog 前置（F4）
    v1.5.1 T07 — R-X5 + R-X6 修复
      · R-X5：异常信息不再暴露给用户；仅写 dev console.error
      · R-X6：SSE 订阅从 subscribeSessionEvents 改为 useSseStream composable
              （带自动重连退避 1s/5s/15s/30s + 30s 心跳超时，架构 §6.3）
-->
<template>
  <div class="chat-view">
    <!-- 科技感背景层（最底）—— v1.5.0 T02：intensity 由 display store 注入 -->
    <TechBackground :intensity="bgIntensity" :show-glow="true" />

    <!-- CRT 扫描线（仅暗主题可见）—— v1.5.0 T02：标准模式 forceOff，演示模式恢复 -->
    <ScanlineOverlay :opacity="0.4" :speed="8" :force-off="isStandard" />

    <!-- F1 推理控制栏：仅在有活跃 session 时显示（T02 集成） -->
    <ReasoningControlBar v-if="reasoning.isActive" />

    <!-- T05 · F4 HITL 弹窗前置（架构 §1.4 + §3.4）：
         - 在控制栏下、消息列表前的对话流顶部
         - sticky top: 80px（Header 高度 60px + 间距 20px）+ z-index 100（toast 1000 > 弹窗 100）
         - 自定义 div 容器（替换原 el-dialog）+ focus trap（4 按钮循环）+ backdrop blur
         - 三按钮（拒绝/仅批准/修改后批准）+ 二次确认（×/Esc/点遮罩）-->
    <HitlEditDialog
      v-model="showHitl"
      :interrupt-node="store.interruptNode"
      :interrupt-msg="store.interruptMsg"
      :thread-id="store.pendingThreadId"
      :interrupt-args="store.interruptArgs"
      :busy="store.hitlBusy"
      :safety-reject="store.hitlSafetyReject"
      @approve="onApprove"
      @reject="onReject"
      @edit-approve="onEditApprove"
    />

    <!-- 消息列表 -->
    <div ref="scrollRef" class="message-list" data-tour="chat-history">
      <!-- 空白引导 -->
      <div v-if="!messages.length" class="welcome">
        <div class="welcome-illustration">
          <svg width="120" height="120" viewBox="0 0 120 120" fill="none" aria-hidden="true">
            <polygon
              points="60,8 104,32 104,72 60,96 16,72 16,32"
              fill="none"
              stroke="var(--brand-primary)"
              stroke-width="1.5"
              stroke-opacity="0.5"
            />
            <polygon
              points="60,20 92,38 92,68 60,86 28,68 28,38"
              fill="var(--brand-primary-soft)"
              stroke="var(--brand-primary)"
              stroke-width="1"
              stroke-opacity="0.4"
            />
            <path
              d="M60 32 L78 64 L60 76 L42 64 Z"
              fill="var(--brand-primary)"
            />
            <circle cx="60" cy="56" r="4" fill="var(--brand-accent)" />
            <circle cx="60" cy="8" r="2.5" fill="var(--brand-primary)" />
            <circle cx="104" cy="32" r="2.5" fill="var(--brand-primary)" fill-opacity="0.6" />
            <circle cx="104" cy="72" r="2.5" fill="var(--brand-accent)" />
            <circle cx="60" cy="96" r="2.5" fill="var(--brand-primary)" fill-opacity="0.6" />
            <circle cx="16" cy="72" r="2.5" fill="var(--brand-primary)" fill-opacity="0.6" />
            <circle cx="16" cy="32" r="2.5" fill="var(--brand-primary)" />
          </svg>
        </div>
        <h2 class="welcome-title">灵枢电网</h2>
        <p class="welcome-sub">GridMind · 在下方输入问题或点击快捷指令开始演示</p>

        <div class="welcome-toolbar" data-tour="chat-model-switcher">
          <ModelSwitcher />
        </div>

        <div data-tour="chat-demo-shortcuts">
          <DemoShortcuts
            :shortcuts="store.demoShortcuts"
            :loading="store.loading"
            @send="onShortcutSend"
          />
        </div>
      </div>

      <TransitionGroup name="slide-up">
        <MessageBubble
          v-for="msg in messages"
          :key="msg.id"
          :msg="msg"
        />
      </TransitionGroup>

      <div ref="bottomRef" />
    </div>

    <!-- 输入区 -->
    <div class="input-area" data-tour="chat-input">
      <div class="input-bar">
        <el-input
          v-model="inputText"
          type="textarea"
          :rows="1"
          :autosize="{ minRows: 1, maxRows: 4 }"
          placeholder="输入电力运维相关的问题…"
          :disabled="store.loading"
          @keydown.enter.prevent="onSend"
          resize="none"
          class="chat-input"
        />
        <el-button
          type="primary"
          :icon="Promotion"
          :loading="store.loading"
          :disabled="!inputText.trim()"
          @click="onSend"
          class="send-btn"
        >
          发送
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, nextTick, watch, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Promotion } from '@element-plus/icons-vue'
import { useChatStore } from '../stores/chatStore'
import { useDisplay } from '../composables/useDisplay'
import { useReasoningStore } from '../stores/reasoning'
// F2 修复：audit store SSE handler 接线（hitl_interrupt / hitl_resolved 实时推送）
import { useAuditStore } from '../stores/audit'
import { useSessionStatsStore } from '../stores/sessionStats'
import { useSseStream } from '../composables/useSseStream'
import type { SseStreamHandle } from '../composables/useSseStream'
import type { SseEvent, ReasoningStep, StepRole, HitlTaskStatus } from '../types'
// F4 修复：统一走 api/chat.ts resolveBaseUrl（VITE_API_BASE → '/api' 兜底）
import { resolveBaseUrl } from '../api/chat'
import MessageBubble from './MessageBubble.vue'
import DemoShortcuts from './DemoShortcuts.vue'
import ModelSwitcher from './ModelSwitcher.vue'
import TechBackground from './background/TechBackground.vue'
import ScanlineOverlay from './background/ScanlineOverlay.vue'
import ReasoningControlBar from './reasoning/ReasoningControlBar.vue'
// T05 · F4 HITL 弹窗前置
import HitlEditDialog from './HitlEditDialog.vue'

const store = useChatStore()
// v1.5.0 T02：解构出 bgIntensity / isStandard（storeToRefs 保留响应性）
const { bgIntensity, isStandard } = useDisplay()
// v1.5.1 T02：reasoning store（用于 ReasoningControlBar + SSE 事件映射）
const reasoning = useReasoningStore()
// F2 修复：audit store（hitl_interrupt / hitl_resolved SSE → 即时更新 HitlBadge）
const audit = useAuditStore()
// v1.6.0 P1-3：sessionStats store（SSE token 事件 → 估算聚合）
const sessionStats = useSessionStatsStore()
const inputText = ref('')
const scrollRef = ref<HTMLElement | null>(null)
const bottomRef = ref<HTMLElement | null>(null)

const messages = computed(() => store.messages)

/* ────────────────────────────────────────────────────────────
 * v1.5.1 T05 · F4 HITL 弹窗前置
 *
 * HitlEditDialog 从 App.vue 移至 ChatView（架构 §3.4 + §5 T05）：
 *   - 弹窗"前置"显示在对话流顶部（非底部 modal）
 *   - 位置：<ReasoningControlBar> 后、<message-list> 前
 *   - v-model 同步 store.interruptRequired → showHitl
 *
 * 决策三按钮 handler：
 *   - onApprove: store.decideHitl('approve', { rejectReason })
 *   - onReject: store.decideHitl('reject', { rejectReason })
 *   - onEditApprove: store.approveWithEdit(editedArgs, editReason)
 * ──────────────────────────────────────────────────────────── */
const showHitl = ref(false)

async function onApprove(reason: string) {
  await store.decideHitl('approve', { rejectReason: reason })
}
async function onReject(reason: string) {
  await store.decideHitl('reject', { rejectReason: reason })
}
async function onEditApprove(payload: {
  editedArgs: Record<string, unknown>
  editReason: string
}) {
  await store.approveWithEdit(payload.editedArgs, payload.editReason)
}

// 监听 chatStore.interruptRequired → 同步 showHitl
watch(
  () => store.interruptRequired,
  (v) => {
    showHitl.value = v
  },
  { immediate: true },
)

/* ────────────────────────────────────────────────────────────
 * v1.5.1 T07 · R-X6 修复：SSE 订阅改用 useSseStream composable
 *
 * 变更动机（QA R-X6 + 架构 §6.3）：
 *   - 旧实现 subscribeSessionEvents 为手写 fetch，无重连（断线后无重连）
 *   - 改用 T01 实现的 useSseStream composable：
 *     · 退避序列 [1000, 5000, 15000, 30000] ms（主理人决策 7.2）
 *     · 30s 心跳超时（防连接假死）
 *     · 自动 JWT header 注入
 *     · onUnmounted 自动 disconnect
 *
 * 事件类型映射（架构 §3.5 SSE 11 type 联合）：
 *   - step_started / step_completed / step_failed → reasoning store lifecycle
 *   - reasoning_paused / reasoning_resumed / reasoning_completed / reasoning_error
 *     → 顶层状态机转移
 *   - step_replaced → F2 编辑后重跑结果
 *   - hitl_interrupt / hitl_resolved → T05 (F4) auditStore.latestPending 同步
 *   - heartbeat / token / done → 无操作
 *
 * URL 解析：
 *   - VITE_API_BASE 来自 .env / import.meta.env（带 /api 兜底）
 *   - 端口默认 9900（决策文档 §7.6 端到端联调）
 *   - sessionId 空 → 不创建 stream（早期 chat 阶段）
 * ──────────────────────────────────────────────────────────── */

/** 解析 API base URL（统一走 api/chat.ts resolveBaseUrl）
 *
 * F4 修复（QA F4 P1）：生产兜底不再返回 http://localhost:9900
 * （旧实现会让生产构建的 SSE 连用户本机 9900 端口，导致断流）。
 * 与 api/chat.ts:30-40 一致 —— VITE_API_BASE ?? '/api'（Vite proxy）。
 */
function resolveApiBase(): string {
  return resolveBaseUrl()
}

/**
 * R-X3 patch · 安全清洗：去掉异常消息里的内部敏感片段（token / Bearer / IP / 端口 / 路径）。
 * 防止 step.output.error 等结构化字段把内部信息带进 UI。
 */
function sanitize(errMsg: string | undefined | null): string {
  if (!errMsg) return '步骤执行失败'
  const MAX = 200
  let v = String(errMsg).slice(0, MAX)
  v = v
    .replace(/\?token=[^\s&]+/gi, '?token=***')
    .replace(/Bearer\s+[A-Za-z0-9._\-]+/gi, 'Bearer ***')
    .replace(/sk-[A-Za-z0-9._\-]+/g, 'sk-***')
    .replace(/127\.0\.0\.1:\d+/g, 'localhost:***')
    .replace(/localhost:\d+/g, 'localhost:***')
    .replace(/\/api\/[^\s"',)]+/g, '/api/***')
    .replace(/data\/checkpoints\.db/g, 'data/***')
  return v.trim() || '步骤执行失败'
}

/** 把 stream 设计成可热替换（受 watch 驱动） */
let sseStream: SseStreamHandle<SseEvent> | null = null

function disposeSse(): void {
  if (sseStream) {
    sseStream.disconnect()
    sseStream = null
  }
}

function attachSse(sessionId: string): void {
  disposeSse()
  const base = resolveApiBase()
  const url = `${base}/sessions/${encodeURIComponent(sessionId)}/events`
  sseStream = useSseStream<SseEvent>({
    url,
    retryDelaysMs: [1000, 5000, 15000, 30000],
    heartbeatTimeoutMs: 30000,
    onEvent: handleSseEvent,
    onError: (err) => {
      // T07 · R-X5 修复：仅 dev 控制台记录完整异常，用户侧通用 message
      // 原始异常可能包含路径 / token / 内部变量名（架构 §6.8）—— 不对外暴露
      console.warn('[SSE] 连接异常：', err)
      // R-X6: useSseStream 内部会自动 scheduleReconnect，不需手动重连
      ElMessage.warning('实时连接中断，正在自动重连...')
    },
  })
}

function handleSseEvent(event: SseEvent): void {
  // 生产环境保留一行日志便于联调（生产构建可通过 Vite terser 移除）
  // eslint-disable-next-line no-console
  console.debug('[SSE]', event.type, event)
  switch (event.type) {
    case 'step_started':
      // F1 修复：step_started → reasoning.appendStep（构建实时推理链）
      // 旧注释声称"由 T03 StepInlineEditor 处理"，但全代码库无任何组件调用
      // appendStep；会话级 SSE 挂载后必须在此追加 step，否则 live steps 不可达。
      if (event.step_id) {
        const step: ReasoningStep = {
          id: event.step_id,
          index: typeof event.step_index === 'number' ? event.step_index : reasoning.steps.length,
          nodeName: event.step_name ?? '',
          name: event.step_name ?? `步骤 ${reasoning.steps.length + 1}`,
          description: event.step_description ?? '',
          promptFragment: event.prompt_fragment ?? '',
          draftPromptFragment: null,
          contentHash: null,
          status: 'running',
          role: (event.step_role as StepRole | undefined) ?? 'assistant',
          startedAt: event.started_at ?? new Date().toISOString(),
          finishedAt: null,
          durationMs: null,
          output: null,
          isEditable: event.is_editable ?? false,
        }
        reasoning.appendStep(step)
      }
      break
    case 'step_completed':
      if (event.step_id) {
        reasoning.completeStep(event.step_id)
      }
      break
    case 'step_failed':
      if (event.step_id) {
        // R-X3 patch：清洗原始 error，避免 ?token= / Bearer / 路径泄漏到 step.output.error
        const cleaned = sanitize(event.error)
        // 服务侧记录原始 event（含完整 stack），用户侧仅存清洗后版本
        console.error('[ChatView.step_failed]', event)
        reasoning.failStep(event.step_id, cleaned)
      }
      break
    case 'reasoning_paused':
      reasoning.onSsePaused()
      ElMessage.info('推理已暂停')
      break
    case 'reasoning_resumed':
      reasoning.onSseResumed()
      break
    case 'reasoning_completed':
      reasoning.markCompleted()
      break
    case 'reasoning_error':
      // R-X3 patch：用户侧仅通用文案（schema 一致）+ console.error 保留原始 event 服务侧
      console.error('[ChatView.reasoning_error]', event)
      reasoning.markError('推理服务异常，请稍后重试')
      ElMessage.error('推理服务异常，请稍后重试')
      break
    case 'step_replaced':
      if (typeof event.step_index === 'number' && Array.isArray(event.new_steps)) {
        reasoning.onSseStepReplaced(event.step_index, event.new_steps)
      }
      break
    // v1.6.0 P1-3：token 事件 → sessionStats 估算聚合（内容字符数；若后端带数字 token 字段则优先）
    case 'token':
      if (event.content) {
        sessionStats.onSseToken(event.content)
      }
      break
    case 'hitl_interrupt': {
      // F2 修复：hitl_interrupt → audit.onSseHitlInterrupt（即时更新 HitlBadge，不再只靠 5s 轮询）
      // 后端 payload: {tool, args} + thread_id（api/services/sse_event_emitter.emit_hitl_interrupt）
      const tid = event.thread_id || event.session_id || ''
      if (tid) {
        audit.onSseHitlInterrupt({
          id: tid,
          sessionId: tid,
          stepId: null,
          createdAt: new Date().toISOString(),
          promptContext: '',
          aiSuggestion: event.ai_suggestion || '',
          confidence: typeof event.confidence === 'number' ? event.confidence : 0,
          riskLevel: event.risk_level || 'high',
          status: 'pending',
        })
      }
      break
    }
    case 'hitl_resolved': {
      // F2 修复：hitl_resolved → audit.onSseHitlResolved（即时扣减待审数）
      // 后端 payload: {decision, resolved_at} + thread_id（emit_hitl_resolved）
      // taskId 以 thread_id 对齐（onSseHitlInterrupt 中任务的 id 即 thread_id）
      const tid = event.thread_id || event.session_id || ''
      if (tid) {
        const decision: HitlTaskStatus =
          event.decision === 'rejected'
            ? 'rejected'
            : event.decision === 'edit_approved'
              ? 'approved-with-edit'
              : 'approved'
        audit.onSseHitlResolved(tid, decision)
      }
      break
    }
    // heartbeat / done → 无需 store 动作
    default:
      break
  }
}

// 监听 sessionId 变化：开/关 SSE 流
watch(
  () => reasoning.sessionId,
  (newId) => {
    if (newId) attachSse(newId)
    else disposeSse()
  },
  { immediate: true },
)

onUnmounted(() => {
  disposeSse()
})

/* ────────────────────────────────────────────────────────────
 * Existing 滚动 / 发送 / 快捷指令 逻辑（保持 v1.5.0 兼容）
 * ──────────────────────────────────────────────────────────── */

function scrollToBottom() {
  nextTick(() => {
    bottomRef.value?.scrollIntoView({ behavior: 'smooth' })
  })
}

watch(() => store.messages.length, scrollToBottom)
watch(() => store.loading, scrollToBottom)
watch(() => store.streaming, scrollToBottom)

async function onSend() {
  const text = inputText.value.trim()
  if (!text || store.loading) return
  inputText.value = ''
  await store.sendMessage(text)
}

function onShortcutSend(message: string) {
  inputText.value = message
  onSend()
}

onMounted(() => {
  scrollToBottom()
})
</script>

<style scoped>
.chat-view {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

/* ── 消息列表 ───────────────────── */
.message-list {
  position: relative;
  z-index: var(--z-base);
  flex: 1;
  overflow-y: auto;
  padding: var(--space-5) 0;
}

/* ── 欢迎页 ─────────────────────── */
.welcome {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: var(--space-10) var(--space-8) var(--space-8);
  text-align: center;
}

.welcome-illustration {
  width: 120px;
  height: 120px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  margin-bottom: var(--space-6);
  filter: drop-shadow(0 0 20px var(--brand-primary-soft));
}

.welcome-illustration > svg {
  display: block;
  width: 100%;
  height: 100%;
  max-width: 120px;
  max-height: 120px;
}

.welcome-title {
  font-family: var(--font-cn);
  font-size: var(--fs-2xl);
  font-weight: var(--fw-bold);
  color: var(--text-primary);
  margin-bottom: var(--space-2);
  letter-spacing: 0.15em;
  transition: var(--theme-transition);
}

.welcome-sub {
  font-family: var(--font-cn);
  font-size: var(--fs-md);
  color: var(--text-muted);
  margin-bottom: var(--space-8);
  letter-spacing: 0.05em;
  transition: var(--theme-transition);
}

.welcome-toolbar {
  display: flex;
  justify-content: center;
  margin-bottom: var(--space-4);
}

/* ── 输入区 ─────────────────────── */
.input-area {
  position: relative;
  z-index: var(--z-sticky);
  padding: var(--space-3) var(--space-5) var(--space-4);
  border-top: 1px solid var(--border-default);
  background: var(--bg-elevated);
  transition: var(--theme-transition);
}

.input-area::before {
  content: '';
  position: absolute;
  top: 0;
  left: 20%;
  right: 20%;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--brand-primary), transparent);
  opacity: 0.4;
}

.input-bar {
  display: flex;
  gap: var(--space-3);
  align-items: flex-end;
}

.chat-input {
  flex: 1;
}

.chat-input :deep(.el-textarea__inner) {
  border-radius: var(--radius-md);
  min-height: 42px;
  padding: 10px var(--space-4);
  font-size: var(--fs-md);
}

.send-btn {
  height: 42px;
  padding: 0 var(--space-5);
  font-size: var(--fs-md);
  font-weight: var(--fw-semibold);
  border-radius: var(--radius-md);
  clip-path: var(--clip-corner-sm);
}

/* ── 列表过渡动画 ───────────────── */
.slide-up-enter-active {
  transition: all var(--dur-slow) var(--ease-out-quint);
}
.slide-up-leave-active {
  transition: all var(--dur-fast) var(--ease-in-out-cubic);
}
.slide-up-enter-from {
  opacity: 0;
  transform: translateY(12px);
}
.slide-up-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}
</style>
