<script setup lang="ts">
/**
 * SessionDetailDrawer.vue · Session 详情抽屉（v1.6.0 P1-3）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构决策（p1-iteration-architecture §1 P1-3 + §4.2 时序图）：
 *   - 数据源：sessionStats store（视图派生）+ reasoning store（rewind）
 *   - 步骤时间线：由 reasoning.steps 直接渲染（agent/工具/耗时/状态）
 *   - token：totalTokens === null → 降级"步骤数 + 耗时"，token 区标注"待接入"
 *   - 可回滚节点：GET /sessions/{id}/checkpoints → 二次确认 → rewindSession
 *   - ESC 关闭走 hotkey 注册中心（priority 80）
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Timer,
  Coin,
  RefreshLeft,
  CircleCheck,
  CircleClose,
  VideoPlay,
  VideoPause,
  Warning,
  Loading,
} from '@element-plus/icons-vue'
import { useSessionStatsStore } from '@/stores/sessionStats'
import { useReasoningStore } from '@/stores/reasoning'
import { rewindSession as rewindSessionApi } from '@/api/chat'
import StatusIcon from '@/components/controls/StatusIcon.vue'
import { registerHotkey, ESC_PRIORITY } from '@/utils/hotkeys'
import type { SessionCheckpointView, SessionStepView, Status } from '@/types/theme'

const sessionStats = useSessionStatsStore()
const reasoning = useReasoningStore()

const rollbackLoadingId = ref<string>('')

let unregisterEsc: (() => void) | null = null

/* ── 状态映射 ── */
const statusText = computed(() => {
  switch (sessionStats.viewStatus) {
    case 'running':
      return '推理中'
    case 'paused':
      return '已暂停'
    case 'error':
      return '运行异常'
    default:
      return '空闲'
  }
})

const iconStatus = computed<Status>(() => {
  switch (sessionStats.viewStatus) {
    case 'running':
      return 'normal'
    case 'paused':
      return 'warning'
    case 'error':
      return 'critical'
    default:
      return 'info'
  }
})

function formatElapsed(ms: number): string {
  const totalSec = Math.floor(ms / 1000)
  const h = Math.floor(totalSec / 3600)
  const m = Math.floor((totalSec % 3600) / 60)
  const s = totalSec % 60
  const pad = (n: number) => String(n).padStart(2, '0')
  return h > 0 ? `${h} 时 ${pad(m)} 分 ${pad(s)} 秒` : `${pad(m)} 分 ${pad(s)} 秒`
}

function formatDuration(ms: number | null): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function formatTokens(n: number | null): string {
  if (n === null) return '—'
  return n.toLocaleString('zh-CN')
}

/** 步骤状态中文文案 */
function stepStatusText(status: SessionStepView['status']): string {
  switch (status) {
    case 'completed':
      return '已完成'
    case 'running':
      return '进行中'
    case 'failed':
      return '失败'
    case 'edited':
      return '已编辑'
    default:
      return '等待中'
  }
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString('zh-CN', { hour12: false })
  } catch {
    return iso
  }
}

/* ── 步骤时间线 ── */
const steps = computed<SessionStepView[]>(() => sessionStats.stepsView)

function stepIconName(step: SessionStepView): string {
  switch (step.status) {
    case 'completed':
      return 'check'
    case 'running':
      return 'run'
    case 'failed':
      return 'fail'
    case 'edited':
      return 'edit'
    default:
      return 'pending'
  }
}

/* ── token 区块 ── */
const tokenTopSteps = computed(() => {
  const entries = Object.entries(sessionStats.tokensByStep)
  return entries
    .map(([stepId, tokens]) => ({ stepId, tokens }))
    .sort((a, b) => b.tokens - a.tokens)
    .slice(0, 5)
})

/* ── 回滚 ── */
async function confirmRewind(checkpoint: SessionCheckpointView): Promise<void> {
  if (!reasoning.sessionId) {
    ElMessage.warning('当前无进行中的会话')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确认回滚到步骤「${checkpoint.name}」？该步骤之后的步骤将被丢弃，且不可撤销。`,
      '回滚确认',
      { type: 'warning', confirmButtonText: '确认回滚', cancelButtonText: '取消' },
    )
  } catch {
    return // 用户取消
  }
  rollbackLoadingId.value = checkpoint.checkpointId
  try {
    const resp = await rewindSessionApi(reasoning.sessionId, {
      step_index: checkpoint.stepIndex,
      edited_content: null,
    })
    reasoning.onSseStepReplaced(checkpoint.stepIndex, resp.new_steps)
    ElMessage.success(`已回滚到「${checkpoint.name}」`)
    await sessionStats.fetchCheckpoints() // 刷新时间线 + 回滚节点
  } catch (err) {
    console.error('[SessionDetailDrawer.confirmRewind]', err)
    ElMessage.error('回滚失败，请稍后重试')
  } finally {
    rollbackLoadingId.value = ''
  }
}

onMounted(() => {
  unregisterEsc = registerHotkey({
    id: 'session-drawer-esc',
    key: 'Escape',
    priority: ESC_PRIORITY.sessionDrawer,
    preventDefault: true,
    enabled: () => sessionStats.drawerOpen,
    handler: () => sessionStats.closeDrawer(),
  })
})

onUnmounted(() => {
  unregisterEsc?.()
})
</script>

<template>
  <el-drawer
    :model-value="sessionStats.drawerOpen"
    size="480px"
    :with-header="false"
    :close-on-press-escape="false"
    class="gm-session-drawer"
    @update:model-value="(v: boolean) => (v ? sessionStats.openDrawer() : sessionStats.closeDrawer())"
  >
    <!-- 头部 -->
    <header class="gm-session-drawer__head">
      <div class="gm-session-drawer__title-wrap">
        <StatusIcon :status="iconStatus" :size="18" />
        <span class="gm-session-drawer__title">Session 详情</span>
      </div>
      <button
        type="button"
        class="gm-session-drawer__close"
        aria-label="关闭"
        @click="sessionStats.closeDrawer()"
      >×</button>
    </header>

    <!-- 概要 -->
    <section class="gm-session-drawer__overview">
      <div class="gm-session-drawer__overview-item">
        <span class="gm-session-drawer__overview-label">状态</span>
        <span class="gm-session-drawer__overview-value" :class="`is-${sessionStats.viewStatus}`">
          {{ statusText }}
        </span>
      </div>
      <div class="gm-session-drawer__overview-item">
        <span class="gm-session-drawer__overview-label">运行时长</span>
        <span class="gm-session-drawer__overview-value">
          <el-icon><Timer /></el-icon>
          {{ formatElapsed(sessionStats.elapsedMs) }}
        </span>
      </div>
      <div class="gm-session-drawer__overview-item">
        <span class="gm-session-drawer__overview-label">会话 ID</span>
        <span class="gm-session-drawer__overview-value gm-session-drawer__session-id">
          {{ sessionStats.sessionId || '—' }}
        </span>
      </div>
    </section>

    <!-- 空态：无会话 -->
    <div v-if="sessionStats.viewStatus === 'idle'" class="gm-session-drawer__empty">
      <el-icon><Warning /></el-icon>
      <p>当前无进行中的会话</p>
      <p class="gm-session-drawer__empty-hint">在智能对话页发起诊断后，可在此查看步骤与回滚节点</p>
    </div>

    <template v-else>
      <!-- Token 区块 -->
      <section class="gm-session-drawer__section">
        <h3 class="gm-session-drawer__section-title">
          <el-icon><Coin /></el-icon>
          Token 消耗
        </h3>
        <template v-if="sessionStats.totalTokens !== null">
          <div class="gm-session-drawer__token-total">
            <span class="gm-session-drawer__token-value">{{ formatTokens(sessionStats.totalTokens) }}</span>
            <span class="gm-session-drawer__token-unit">累计估算</span>
            <el-tag v-if="sessionStats.tokenSource === 'chars'" size="small" type="info" effect="plain">
              字符估算
            </el-tag>
            <el-tag v-else-if="sessionStats.tokenSource === 'field'" size="small" type="success" effect="plain">
              后端字段
            </el-tag>
          </div>
          <div v-if="tokenTopSteps.length" class="gm-session-drawer__token-bars">
            <div
              v-for="entry in tokenTopSteps"
              :key="entry.stepId"
              class="gm-session-drawer__token-bar-row"
            >
              <span class="gm-session-drawer__token-bar-label">
                {{ steps.find((s) => s.id === entry.stepId)?.name ?? '步骤' }}
              </span>
              <div class="gm-session-drawer__token-bar-track">
                <div
                  class="gm-session-drawer__token-bar"
                  :style="{
                    width: `${Math.min(100, (entry.tokens / Math.max(1, sessionStats.totalTokens)) * 100)}%`,
                  }"
                ></div>
              </div>
              <span class="gm-session-drawer__token-bar-value">{{ formatTokens(entry.tokens) }}</span>
            </div>
          </div>
        </template>
        <div v-else class="gm-session-drawer__token-degraded">
          <p>Token 数据待接入（后端 SSE 未下发 token 字段）</p>
          <p class="gm-session-drawer__token-degraded-hint">
            当前以降级指标展示：{{ sessionStats.totalSteps }} 步 · {{ formatElapsed(sessionStats.elapsedMs) }}
          </p>
        </div>
      </section>

      <!-- 步骤时间线 -->
      <section class="gm-session-drawer__section">
        <h3 class="gm-session-drawer__section-title">
          <el-icon><Timer /></el-icon>
          步骤时间线（{{ steps.length }} 步 · 已完成 {{ sessionStats.completedSteps }}）
        </h3>
        <ul v-if="steps.length" class="gm-session-drawer__timeline">
          <li
            v-for="step in steps"
            :key="step.id"
            class="gm-session-drawer__step"
            :class="`is-${step.status}`"
          >
            <span class="gm-session-drawer__step-marker">
              <el-icon v-if="stepIconName(step) === 'check'"><CircleCheck /></el-icon>
              <el-icon v-else-if="stepIconName(step) === 'run'" class="is-loading"><Loading /></el-icon>
              <el-icon v-else-if="stepIconName(step) === 'fail'"><CircleClose /></el-icon>
              <span v-else class="gm-session-drawer__step-pending">{{ step.index + 1 }}</span>
            </span>
            <div class="gm-session-drawer__step-body">
              <div class="gm-session-drawer__step-head">
                <span class="gm-session-drawer__step-name">{{ step.name }}</span>
                <span class="gm-session-drawer__step-meta">
                  <span class="gm-session-drawer__step-node">{{ step.nodeName }}</span>
                  <span class="gm-session-drawer__step-duration">{{ formatDuration(step.durationMs) }}</span>
                  <span v-if="step.tokens !== null" class="gm-session-drawer__step-tokens">
                    {{ formatTokens(step.tokens) }}
                  </span>
                </span>
              </div>
              <span class="gm-session-drawer__step-status">{{ stepStatusText(step.status) }}</span>
            </div>
          </li>
        </ul>
        <el-empty v-else description="暂无步骤" :image-size="60" />
      </section>

      <!-- 可回滚节点 -->
      <section class="gm-session-drawer__section">
        <h3 class="gm-session-drawer__section-title">
          <el-icon><RefreshLeft /></el-icon>
          可回滚节点
          <span v-if="sessionStats.checkpointsLoading" class="gm-session-drawer__section-loading">
            <el-icon class="is-loading"><Loading /></el-icon>
          </span>
        </h3>
        <ul v-if="sessionStats.checkpoints?.length" class="gm-session-drawer__checkpoints">
          <li
            v-for="cp in sessionStats.checkpoints"
            :key="cp.checkpointId"
            class="gm-session-drawer__checkpoint"
          >
            <div class="gm-session-drawer__checkpoint-info">
              <span class="gm-session-drawer__checkpoint-name">{{ cp.name }}</span>
              <span class="gm-session-drawer__checkpoint-meta">
                步 {{ cp.stepIndex }} · {{ formatTime(cp.createdAt) }}
              </span>
            </div>
            <el-button
              size="small"
              type="warning"
              plain
              :loading="rollbackLoadingId === cp.checkpointId"
              @click="confirmRewind(cp)"
            >
              回滚到此步
            </el-button>
          </li>
        </ul>
        <div v-else-if="!sessionStats.checkpointsLoading" class="gm-session-drawer__checkpoints-empty">
          暂无可用回滚节点
        </div>
      </section>
    </template>

    <!-- 错误信息 -->
    <el-alert
      v-if="sessionStats.viewStatus === 'error' && sessionStats.errorMessage"
      :title="sessionStats.errorMessage"
      type="error"
      show-icon
      :closable="false"
      class="gm-session-drawer__error"
    />
  </el-drawer>
</template>

<style scoped lang="scss">
.gm-session-drawer {
  --gm-drawer-pad: var(--space-5);
}

.gm-session-drawer :deep(.el-drawer),
:deep(.gm-session-drawer .el-drawer) {
  background: var(--bg-elevated);
  border-left: 1px solid var(--border-default);
  transition: var(--theme-transition);
}

.gm-session-drawer :deep(.el-drawer__body),
:deep(.gm-session-drawer .el-drawer__body) {
  padding: 0;
  overflow-y: auto;
}

/* ── 头部 ── */
.gm-session-drawer__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-4) var(--gm-drawer-pad);
  border-bottom: 1px solid var(--border-muted);
  position: sticky;
  top: 0;
  background: var(--bg-elevated);
  z-index: 1;
}

.gm-session-drawer__title-wrap {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

.gm-session-drawer__title {
  font-family: var(--font-cn);
  font-size: var(--fs-lg);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
  letter-spacing: 0.06em;
}

.gm-session-drawer__close {
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  color: var(--text-secondary);
  font-size: var(--fs-lg);
  cursor: pointer;
  transition: all var(--dur-fast) var(--ease-out-quint);
}

.gm-session-drawer__close:hover {
  color: var(--brand-primary);
  border-color: var(--brand-primary);
  box-shadow: var(--glow-primary-soft);
}

/* ── 概要 ── */
.gm-session-drawer__overview {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-3);
  padding: var(--space-4) var(--gm-drawer-pad);
  border-bottom: 1px solid var(--border-muted);
  background: var(--bg-card);
}

.gm-session-drawer__overview-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}

.gm-session-drawer__overview-label {
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}

.gm-session-drawer__overview-value {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
}

.gm-session-drawer__overview-value.is-running { color: var(--cb-status-normal-fg); }
.gm-session-drawer__overview-value.is-paused { color: var(--cb-status-warning-fg); }
.gm-session-drawer__overview-value.is-error { color: var(--cb-status-critical-fg); }

.gm-session-drawer__session-id {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text-muted);
  word-break: break-all;
}

/* ── 空态 ── */
.gm-session-drawer__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-12) var(--space-6);
  color: var(--text-muted);
  text-align: center;
}

.gm-session-drawer__empty .el-icon {
  font-size: 32px;
  color: var(--text-muted);
}

.gm-session-drawer__empty p {
  margin: 0;
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
}

.gm-session-drawer__empty-hint {
  font-size: var(--fs-xs);
  color: var(--text-muted);
  line-height: var(--lh-normal);
}

/* ── 区块 ── */
.gm-session-drawer__section {
  padding: var(--space-4) var(--gm-drawer-pad);
  border-bottom: 1px solid var(--border-muted);
}

.gm-session-drawer__section-title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin: 0 0 var(--space-3);
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.gm-session-drawer__section-title .el-icon {
  color: var(--brand-primary);
}

.gm-session-drawer__section-loading {
  margin-left: auto;
  color: var(--text-muted);
}

/* ── Token ── */
.gm-session-drawer__token-total {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}

.gm-session-drawer__token-value {
  font-family: var(--font-mono);
  font-size: var(--fs-2xl);
  font-weight: var(--fw-bold);
  color: var(--brand-primary);
  font-variant-numeric: tabular-nums;
}

.gm-session-drawer__token-unit {
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}

.gm-session-drawer__token-bars {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.gm-session-drawer__token-bar-row {
  display: grid;
  grid-template-columns: 110px 1fr 56px;
  align-items: center;
  gap: var(--space-2);
}

.gm-session-drawer__token-bar-label {
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.gm-session-drawer__token-bar-track {
  height: 6px;
  border-radius: 3px;
  background: var(--bg-input);
  overflow: hidden;
}

.gm-session-drawer__token-bar {
  height: 100%;
  border-radius: 3px;
  background: linear-gradient(90deg, var(--brand-primary), var(--brand-accent));
  transition: width var(--dur-base) var(--ease-out-quint);
}

.gm-session-drawer__token-bar-value {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text-muted);
  text-align: right;
}

.gm-session-drawer__token-degraded {
  padding: var(--space-3);
  border: 1px dashed var(--border-default);
  border-radius: var(--radius-md);
  background: var(--bg-card);
}

.gm-session-drawer__token-degraded p {
  margin: 0;
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  color: var(--text-secondary);
}

.gm-session-drawer__token-degraded-hint {
  margin-top: var(--space-1) !important;
  font-size: var(--fs-xs) !important;
  color: var(--text-muted) !important;
}

/* ── 时间线 ── */
.gm-session-drawer__timeline {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}

.gm-session-drawer__step {
  display: flex;
  gap: var(--space-3);
  padding: var(--space-3) 0;
  border-bottom: 1px dashed var(--border-muted);
}

.gm-session-drawer__step:last-child {
  border-bottom: none;
}

.gm-session-drawer__step-marker {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  flex-shrink: 0;
  border-radius: 50%;
  border: 1px solid var(--border-default);
  color: var(--text-muted);
  background: var(--bg-card);
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
}

.gm-session-drawer__step.is-completed .gm-session-drawer__step-marker {
  color: var(--cb-status-normal-fg);
  border-color: var(--cb-status-normal-fg);
  background: var(--cb-status-normal-soft);
}

.gm-session-drawer__step.is-running .gm-session-drawer__step-marker {
  color: var(--brand-primary);
  border-color: var(--brand-primary);
  background: var(--brand-primary-soft);
}

.gm-session-drawer__step.is-failed .gm-session-drawer__step-marker {
  color: var(--cb-status-critical-fg);
  border-color: var(--cb-status-critical-fg);
  background: var(--cb-status-critical-soft);
}

.gm-session-drawer__step-pending {
  font-size: var(--fs-xs);
}

.gm-session-drawer__step-body {
  flex: 1;
  min-width: 0;
}

.gm-session-drawer__step-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.gm-session-drawer__step-name {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  font-weight: var(--fw-medium);
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.gm-session-drawer__step-meta {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}

.gm-session-drawer__step-node {
  color: var(--text-secondary);
}

.gm-session-drawer__step-status {
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}

.gm-session-drawer__step.is-running .gm-session-drawer__step-status {
  color: var(--brand-primary);
}

/* ── 回滚节点 ── */
.gm-session-drawer__checkpoints {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.gm-session-drawer__checkpoint {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3);
  border: 1px solid var(--border-muted);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  transition: var(--theme-transition);
}

.gm-session-drawer__checkpoint-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.gm-session-drawer__checkpoint-name {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  font-weight: var(--fw-medium);
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.gm-session-drawer__checkpoint-meta {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}

.gm-session-drawer__checkpoints-empty {
  padding: var(--space-4);
  text-align: center;
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  color: var(--text-muted);
  border: 1px dashed var(--border-default);
  border-radius: var(--radius-md);
}

.gm-session-drawer__error {
  margin: var(--space-4) var(--gm-drawer-pad);
}
</style>
