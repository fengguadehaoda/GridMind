<!--
  web/src/components/reasoning/ReasoningControlBar.vue
  GridMind v1.5.1 T02 · F1 推理暂停/恢复主组件

  职责：
    - 顶部控制栏：状态徽标 + 步骤计数 + 暂停/继续/中止按钮
    - 与 reasoning store 集成：按钮显示规则严格按主理人决策 7.3
    - a11y：role=region + aria-label / 步骤计数 aria-live=polite
    - abort 必须二次确认（ElMessageBox），取消不触发后端调用

  业务规则（主理人决策 7.3）：
    - 暂停按钮：仅 status === 'running' 时显示
    - 继续按钮：仅 status === 'paused' 时显示
    - 中止按钮：running / paused / resuming 三个 active 态都显示
      （保留逃生通道；completed/error/aborted/editing 不显示）

  父组件：ChatView（在 v-if="reasoning.isActive" 时挂载）

  作者：寇豆码（T02 工程师）
  参考：frontend-v151-architecture-2026-08-04.md §3.1.1 / §6.3 a11y
       ui-v151-p0-3-prd-2026-08-04.md §3.1.2 / §3.1.5
-->
<script setup lang="ts">
import { computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { VideoPause, VideoPlay, CircleClose } from '@element-plus/icons-vue'
import { useReasoningStore } from '@/stores/reasoning'
import ReasoningStatusBadge from './ReasoningStatusBadge.vue'

const reasoning = useReasoningStore()

/* ────────────────────────────────────────────────────────────
 * 显示规则（主理人决策 7.3：仅 running 显示暂停按钮）
 * ──────────────────────────────────────────────────────────── */

/** 暂停按钮：仅 status === 'running' */
const showPauseButton = computed(() => reasoning.status === 'running')

/** 继续按钮：仅 status === 'paused' */
const showResumeButton = computed(() => reasoning.status === 'paused')

/** 中止按钮：3 个 active 态都显示（保留逃生通道） */
const showAbortButton = computed(() =>
  ['running', 'paused', 'resuming'].includes(reasoning.status),
)

/* ────────────────────────────────────────────────────────────
 * 派生数据
 * ──────────────────────────────────────────────────────────── */

/** 步骤计数 "已完成 / 总数 步" —— 屏幕阅读器友好（aria-live=polite） */
const stepCounter = computed(() => {
  const total = reasoning.totalSteps
  const completed = reasoning.completedSteps.length
  return `${completed} / ${total} 步`
})

/** 各按钮的 disabled 状态：sessionId 缺失 或 API 进行中（防双击） */
const isPauseDisabled = computed(
  () => reasoning.pendingPause || !reasoning.sessionId,
)
const isResumeDisabled = computed(
  () => reasoning.pendingResume || !reasoning.sessionId,
)
const isAbortDisabled = computed(
  () => reasoning.pendingAbort || !reasoning.sessionId,
)

/* ────────────────────────────────────────────────────────────
 * 事件处理
 * ──────────────────────────────────────────────────────────── */

async function handlePause(): Promise<void> {
  if (!reasoning.sessionId) return
  try {
    await reasoning.pause('user_requested')
    ElMessage.success('已请求暂停推理')
  } catch (e) {
    // T07 · R-X5 修复：异常信息仅写控制台（开发调试），用户侧仅显示通用 message
    // 原因：原始 e.message 可能包含路径 / token / 变量名等内部实现细节（架构 §6.8）
    console.error('[ReasoningControlBar.pause] 操作失败：', e)
    ElMessage.error('暂停失败，请稍后重试')
  }
}

async function handleResume(): Promise<void> {
  if (!reasoning.sessionId) return
  try {
    await reasoning.resume()
    ElMessage.success('已恢复推理')
  } catch (e) {
    // T07 · R-X5 修复：仅 dev 控制台记录完整异常，用户侧通用 message
    console.error('[ReasoningControlBar.resume] 操作失败：', e)
    ElMessage.error('恢复失败，请稍后重试')
  }
}

/**
 * abort 必须二次确认（PRD §3.1.5）：
 *   - 确认 → reasoning.abortWithApi('user_aborted') 调后端 + 本地状态
 *   - 取消 → no-op（不调 API，不改状态）
 *   - 关闭弹窗（点 X）→ 等同取消
 *
 * ElMessageBox 取消时 throw 'cancel'；关闭时 throw 'close'。
 */
async function handleAbort(): Promise<void> {
  if (!reasoning.sessionId) return
  try {
    await ElMessageBox.confirm(
      '确认中止当前推理？已执行的工具副作用可能不可逆，且无法继续。',
      '中止推理',
      {
        confirmButtonText: '确认中止',
        cancelButtonText: '取消',
        type: 'warning',
        confirmButtonClass: 'el-button--danger',
        lockScroll: false,
        // 弹窗 z-index 100（决策 7.5：toast 1000 > 弹窗 100）
        // Element Plus 默认 dialog z-index 2000+ 与 EP 主题层一致；
        // 这里通过自定义 class 由 element-overrides.scss 调整。
        customClass: 'reasoning-abort-dialog',
      },
    )
    await reasoning.abortWithApi('user_aborted')
    ElMessage.warning('已中止推理')
  } catch (e) {
    // 'cancel' / 'close' 是 ElMessageBox 主动取消/关闭的标记，no-op
    if (e === 'cancel' || e === 'close') return
    // T07 · R-X5 修复：异常仅写控制台，用户侧通用 message
    console.error('[ReasoningControlBar.abort] 操作失败：', e)
    ElMessage.error('中止失败，请稍后重试')
  }
}
</script>

<template>
  <div
    class="reasoning-control-bar"
    role="region"
    aria-label="推理控制栏"
    data-component="reasoning-control-bar"
  >
    <!-- 左侧：状态徽标 + 步骤计数 -->
    <div class="bar-left">
      <ReasoningStatusBadge :status="reasoning.status" />
      <span
        class="step-counter"
        aria-live="polite"
        aria-atomic="true"
      >{{ stepCounter }}</span>
    </div>

    <!-- 右侧：操作按钮组 -->
    <div class="bar-right" role="group" aria-label="推理操作">
      <el-button
        v-if="showPauseButton"
        :icon="VideoPause"
        size="small"
        type="warning"
        :loading="reasoning.pendingPause"
        :disabled="isPauseDisabled"
        aria-label="暂停推理"
        data-action="pause"
        @click="handlePause"
      >
        暂停
      </el-button>

      <el-button
        v-if="showResumeButton"
        :icon="VideoPlay"
        size="small"
        type="success"
        :loading="reasoning.pendingResume"
        :disabled="isResumeDisabled"
        aria-label="继续推理"
        data-action="resume"
        @click="handleResume"
      >
        继续
      </el-button>

      <el-button
        v-if="showAbortButton"
        :icon="CircleClose"
        size="small"
        type="danger"
        plain
        :loading="reasoning.pendingAbort"
        :disabled="isAbortDisabled"
        aria-label="中止推理"
        data-action="abort"
        @click="handleAbort"
      >
        中止
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.reasoning-control-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  background: var(--bg-elevated);
  border-bottom: 1px solid var(--border-default);
  gap: 12px;
  transition: var(--theme-transition);
  flex-shrink: 0;
  min-height: 48px;
  position: relative;
  z-index: var(--z-sticky);
}

.bar-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
  flex: 1;
}

.step-counter {
  font-family: var(--font-mono);
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  letter-spacing: 0.02em;
}

.bar-right {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
</style>
