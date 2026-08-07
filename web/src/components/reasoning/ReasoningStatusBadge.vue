<!--
  web/src/components/reasoning/ReasoningStatusBadge.vue
  GridMind v1.5.1 T02 · F1 推理状态徽标子组件

  职责：
    - 纯展示型组件：根据 reasoning store 的 8 态（idle / running / paused /
      editing / resuming / completed / error / aborted）渲染对应的 el-tag
    - 配色按 PRD §3.1.2：success=完成 / warning=暂停 / danger=错误+中止 /
      info=空闲+运行+恢复 / accent（→primary）=编辑中
    - a11y：aria-label="推理状态: {中文标签}"，icon 标记 aria-hidden

  不在范围（明确边界）：
    - 不调用任何 action / 不依赖 useReasoningStore（纯展示型）
    - 父组件 ReasoningControlBar 负责把它嵌入到控制栏左侧

  作者：寇豆码（T02 工程师）
  参考：frontend-v151-architecture-2026-08-04.md §3.1.4 / §6.3 a11y
-->
<script setup lang="ts">
import { computed } from 'vue'
import type { ReasoningStatus } from '@/types'

/** 8 态配置表（架构 §3.1.4 配色表 + 中文标签） */
interface BadgeConfig {
  /** 业务/逻辑 tone：5 类（info / success / warning / danger / accent） */
  tone: 'info' | 'success' | 'warning' | 'danger' | 'accent'
  /** 状态标签（中文，用于 UI 显示） */
  label: string
  /** 状态图标（emoji，aria-hidden 让屏幕阅读器忽略） */
  icon: string
}

const STATUS_MAP: Record<ReasoningStatus, BadgeConfig> = {
  idle:      { tone: 'info',    label: '空闲',   icon: '🟢' },
  running:   { tone: 'info',    label: '推理中', icon: '⏳' },
  paused:    { tone: 'warning', label: '已暂停', icon: '⏸' },
  editing:   { tone: 'accent',  label: '编辑中', icon: '✎' },
  resuming:  { tone: 'info',    label: '恢复中', icon: '▶' },
  completed: { tone: 'success', label: '已完成', icon: '✓' },
  error:     { tone: 'danger',  label: '错误',   icon: '⚠' },
  aborted:   { tone: 'danger',  label: '已中止', icon: '✕' },
}

/**
 * Element Plus el-tag 合法 type 映射（仅 5 个：primary/success/warning/info/danger）
 *
 * 业务 tone 'accent'（编辑中）映射到 'primary'，与 PRD §3.1.2 视觉一致
 * —— Element Plus 不支持自定义 type，只能用这 5 个。
 */
const EL_TAG_TYPE_MAP: Record<BadgeConfig['tone'], 'primary' | 'success' | 'warning' | 'info' | 'danger'> = {
  info:    'info',
  success: 'success',
  warning: 'warning',
  danger:  'danger',
  accent:  'primary',
}

const props = withDefaults(
  defineProps<{
    /** 推理状态（来自 reasoning store） */
    status: ReasoningStatus
    /** compact 模式：浅色背景（plain）而非深色（dark）；用于次要展示位 */
    compact?: boolean
  }>(),
  { compact: false },
)

const config = computed<BadgeConfig>(() => STATUS_MAP[props.status])
const elTagType = computed(() => EL_TAG_TYPE_MAP[config.value.tone])
</script>

<template>
  <el-tag
    :type="elTagType"
    :effect="compact ? 'plain' : 'dark'"
    size="small"
    round
    :aria-label="`推理状态: ${config.label}`"
    data-component="reasoning-status-badge"
    :data-status="status"
  >
    <span class="status-icon" aria-hidden="true">{{ config.icon }}</span>
    <span class="status-label">{{ config.label }}</span>
  </el-tag>
</template>

<style scoped>
.status-icon {
  margin-right: 4px;
  font-style: normal;
  font-size: 12px;
  line-height: 1;
}

.status-label {
  font-weight: 500;
  letter-spacing: 0.02em;
}
</style>
