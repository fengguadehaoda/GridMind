<script setup lang="ts">
/**
 * v1.5.1 T04 · F3 HITL 队列徽标（Header 右上角）
 *
 * 行为（架构 §1.3 + PRD §3.3 + 主理人决策 7.2/7.5）：
 *   - 数据来源：`auditStore.pendingHitlCount`（双通道：5s 轮询 + SSE hitl_interrupt）
 *   - 严重程度配色（实测后）：
 *       1..4    → warning 黄（--status-warning）
 *       ≥5      → critical 红（--status-danger）+ 脉冲动画
 *       0       → 不渲染（v-if 整组 transition 退场）
 *       backend_unreachable → 灰点 ·（tooltip "等待后端连接"）
 *   - 点击 → `router.push('/audit?filter=pending&from=hitl-badge')`
 *   - z-index = 200（主理人决策 7.5：在 toast 1000 之下、弹窗 100 之上）
 *   - a11y：aria-label（拼接 count）+ aria-live（critical 用 assertive 立即播报）
 *
 * 与 audit store 协作：
 *   - audit store 的 hydrate() 已在 main.ts / App.vue onMounted 调用
 *   - 本组件完全无状态 —— 仅做视图层展示与点击导航
 *   - 后端 5xx 时由 audit store connectionState 控制降级显示
 *
 * 作者：寇豆码（T04 工程师）
 * 参考：frontend-v151-architecture-2026-08-04.md §1.3 + §3.3 + §5 T04
 */
import { computed } from 'vue'
import { Bell, Warning, Connection } from '@element-plus/icons-vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuditStore } from '@/stores/audit'

const audit = useAuditStore()
const router = useRouter()
const route = useRoute()

// ═══ 计算属性 ═══

/** 业务规则：≥5 即 critical，否则 warning（PRD §3.3.3 + 实测） */
const severity = computed<'warning' | 'critical'>(() => {
  const n = audit.pendingHitlCount
  return n >= 5 ? 'critical' : 'warning'
})

/** 显示文案：> 99 显示 "99+"，实际值仍在 store */
const displayCount = computed(() => {
  const n = audit.pendingHitlCount
  if (n > 99) return '99+'
  return String(n)
})

/** 后端不可达时（轮询连续失败）→ 显示 "·" 灰点降级 */
const isDegraded = computed(
  () => audit.connectionState === 'error' || audit.connectionState === 'disconnected',
)

/** 是否完全渲染（0 时整组 transition 退场，节省布局空间） */
const shouldShow = computed(() => audit.pendingHitlCount > 0 || isDegraded.value)

/** 选图标：degraded 用 Connection，普通用 Bell，critical 状态用 Warning */
const iconComponent = computed(() => {
  if (isDegraded.value) return Connection
  return severity.value === 'critical' ? Warning : Bell
})

/** 悬停 tooltip 文本 */
const tooltipText = computed(() => {
  if (isDegraded.value) {
    return '等待后端连接 · HITL 待审数暂不可用'
  }
  const n = audit.pendingHitlCount
  if (n === 0) return '当前无 HITL 待审任务'
  return `${n} 个待审批 HITL 任务，点击查看`
})

/** aria-label（屏幕阅读器，与 tooltip 同源；critical 额外播报"严重"） */
const ariaLabel = computed(() => {
  if (isDegraded.value) {
    return 'HITL 队列：等待后端连接，待审数暂不可用'
  }
  const n = audit.pendingHitlCount
  if (n === 0) return 'HITL 队列：当前无待审任务'
  if (severity.value === 'critical') {
    return `HITL 队列严重积压：${n} 个待审，点击进入审计页`
  }
  return `HITL 队列：${n} 个待审任务，点击进入审计页`
})

// ═══ 行为 ═══

/**
 * 点击跳审计页（带 query 标识来源）。
 * 注：AuditLogViewer 在 T04 不修改，filter=pending 由 T05 扩展；
 *      from=hitl-badge 仍写入路由以便后续 T05 监听。
 */
function handleClick() {
  void router.push({
    path: '/audit',
    query: { filter: 'pending', from: 'hitl-badge' },
  }).then(() => {
    // 命中即路由跳完后无需额外动作；保留扩展点
  }).catch(() => {
    /* 同路由跳转可能被 vue-router 静默 reject，无害 */
  })
  // 当用户在 /audit 页时不再触发跳转（避免无意义 history push）—— 已在路由层短路
  void route // 占位以避免 lint 警告未用变量
}
</script>

<template>
  <transition name="hitl-badge-fade">
    <el-tooltip
      v-if="shouldShow"
      :content="tooltipText"
      placement="bottom"
      :show-after="200"
      :hide-after="0"
      popper-class="hitl-badge-tooltip"
    >
      <button
        type="button"
        class="hitl-badge"
        :class="[
          `hitl-badge--${severity}`,
          { 'hitl-badge--degraded': isDegraded },
        ]"
        data-component="hitl-badge"
        :data-count="audit.pendingHitlCount"
        :data-severity="severity"
        :data-from="'hitl-badge'"
        :aria-label="ariaLabel"
        :aria-live="severity === 'critical' ? 'assertive' : 'polite'"
        :title="tooltipText"
        @click="handleClick"
      >
        <span class="hitl-badge__icon" aria-hidden="true">
          <el-icon :size="14">
            <component :is="iconComponent" />
          </el-icon>
        </span>
        <span class="hitl-badge__count" data-test="hitl-badge-count">
          {{ isDegraded ? '·' : displayCount }}
        </span>
        <span class="hitl-badge__label" data-test="hitl-badge-label">待审</span>
      </button>
    </el-tooltip>
  </transition>
</template>

<style scoped>
.hitl-badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: 4px var(--space-3);
  border-radius: var(--radius-pill);
  border: 1px solid var(--border-default);
  background: var(--bg-card);
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  cursor: pointer;
  user-select: none;
  -webkit-tap-highlight-color: transparent;
  transition:
    background var(--dur-fast) var(--ease-out-quint),
    border-color var(--dur-fast) var(--ease-out-quint),
    color var(--dur-fast) var(--ease-out-quint),
    transform var(--dur-fast) var(--ease-out-quint),
    box-shadow var(--dur-fast) var(--ease-out-quint);
  position: relative;
  /* 主理人决策 7.5：徽标在 toast(1000) 之下、弹窗(100) 之上 → z-index: 200 */
  z-index: 200;
  outline: none;
}

.hitl-badge:hover {
  background: var(--brand-primary-soft);
  border-color: var(--brand-primary);
  color: var(--brand-primary);
  transform: translateY(-1px);
  box-shadow: var(--glow-primary-soft);
}

.hitl-badge:focus-visible {
  outline: 2px solid var(--brand-primary);
  outline-offset: 2px;
}

.hitl-badge:active {
  transform: translateY(0);
}

/* ── 严重程度配色（PRD §3.3.3） ── */

.hitl-badge--warning {
  background: var(--status-warning-soft);
  border-color: var(--status-warning);
  color: var(--status-warning);
}

.hitl-badge--warning:hover {
  background: var(--status-warning-soft);
  border-color: var(--status-warning);
  color: var(--status-warning);
  box-shadow: var(--glow-warning);
}

.hitl-badge--critical {
  background: var(--status-danger-soft);
  border-color: var(--status-danger);
  color: var(--status-danger);
  animation: hitl-badge-pulse 2s ease-in-out infinite;
}

.hitl-badge--critical:hover {
  background: var(--status-danger-soft);
  border-color: var(--status-danger);
  color: var(--status-danger);
  box-shadow: var(--glow-danger);
}

/* 后端不可达降级态（PRD §3.3.3） */
.hitl-badge--degraded {
  background: var(--bg-card);
  border-color: var(--border-default);
  color: var(--status-neutral);
  animation: none;
}

.hitl-badge--degraded:hover {
  background: var(--bg-card);
  border-color: var(--status-neutral);
  color: var(--status-neutral);
  box-shadow: none;
  transform: none;
}

/* ── 子元素 ── */

.hitl-badge__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.hitl-badge__count {
  min-width: 18px;
  text-align: center;
  font-weight: var(--fw-bold);
  font-variant-numeric: tabular-nums;
  /* 99+ 也需稳定宽度，避免徽标横向抖动 */
  letter-spacing: -0.02em;
}

.hitl-badge__label {
  font-size: var(--fs-xs);
  letter-spacing: 0.05em;
  /* 字号略小以与 count 形成视觉层级 */
  opacity: 0.85;
}

/* ── 严重徽标脉冲（critical 状态） ── */
@keyframes hitl-badge-pulse {
  0%, 100% {
    box-shadow: 0 0 0 0 var(--status-danger-soft);
  }
  50% {
    box-shadow: 0 0 8px 2px var(--status-danger-soft);
  }
}

/* ── 进场 / 退场动画 ── */
.hitl-badge-fade-enter-active,
.hitl-badge-fade-leave-active {
  transition:
    opacity 0.2s ease,
    transform 0.2s var(--ease-out-quint);
}

.hitl-badge-fade-enter-from {
  opacity: 0;
  transform: scale(0.85) translateY(-2px);
}

.hitl-badge-fade-leave-to {
  opacity: 0;
  transform: scale(0.85);
}

/* ── 响应式：< 768px 隐藏"待审"文字，仅保留数字 ── */
@media (max-width: 768px) {
  .hitl-badge__label {
    display: none;
  }
  .hitl-badge {
    padding: 4px var(--space-2);
  }
}

/* ── 减少动画偏好 ── */
@media (prefers-reduced-motion: reduce) {
  .hitl-badge--critical {
    animation: none;
  }
  .hitl-badge-fade-enter-active,
  .hitl-badge-fade-leave-active {
    transition: opacity 0.1s linear;
  }
}
</style>
