<!--
  v1.5.1 T03 · F2 子组件 · StepEditButton.vue

  角色：✎ 编辑触发按钮。父组件 ReasoningChainPanel.vue 在 v1.5.1 实时推理链
  段落，每 step 右侧挂一个本组件（仅 step.isEditable=true 时渲染）。

  关键行为：
    1. step.isEditable=false → 按钮不渲染（防御式：调用方也判一次）
    2. status 不在 running/paused → 点击 → 不抛错，toast 提示
    3. 已在编辑此 step → 按钮显示"编辑中"（loading 态 + aria-busy）
    4. a11y：aria-label + 切换时读屏友好
-->
<script setup lang="ts">
import { computed } from 'vue'
import { Edit, Loading as IconLoading } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useReasoningStore } from '@/stores/reasoning'

const props = defineProps<{
  /** 当前 step 的 id */
  stepId: string
  /** 由调用方控制的禁用（例如非 running/paused 状态） */
  disabled?: boolean
}>()

const reasoning = useReasoningStore()

// ═══ computed ═══
/** 当前 step（含 isEditable / name 等） */
const step = computed(() => reasoning.steps.find((s) => s.id === props.stepId) ?? null)
/** 是否正在编辑本 step（用于按钮显示态切换） */
const isEditingThis = computed<boolean>(
  () => reasoning.editingStepId === props.stepId && reasoning.status === 'editing',
)
/** 该 step 是否允许编辑（按主理人决策 7.4：仅 user content 可编辑） */
const editable = computed<boolean>(() => Boolean(step.value?.isEditable))

// ═══ click handler ═══
function handleEdit(): void {
  if (props.disabled) return
  if (!editable.value) {
    // 防御式：理论上前置 v-if 已拦住；万一父组件忘加，提示而不抛错
    ElMessage.warning('该步骤不可编辑（仅 user content 可编辑）')
    return
  }
  try {
    reasoning.beginEdit(props.stepId)
  } catch (err) {
    // T07 · R-X5 修复：
    //   - 已知业务错误码（REASONING_NOT_EDITABLE_STATE / STEP_NOT_EDITABLE）
    //     是契约错误，保留友好提示（非"内部异常泄漏"）
    //   - 其他未知异常：仅 dev 控制台记录 traceback，用户侧通用 message
    //     （原始 err.message 可能包含路径 / token / 变量名等内部实现细节）
    if (err instanceof Error && err.message === 'REASONING_NOT_EDITABLE_STATE') {
      ElMessage.warning('当前推理状态不可编辑（仅 running / paused 允许）')
    } else if (err instanceof Error && err.message === 'STEP_NOT_EDITABLE') {
      ElMessage.warning('该步骤不可编辑')
    } else {
      console.error('[StepEditButton.beginEdit] 操作失败：', err)
      ElMessage.error('编辑失败，请稍后重试')
    }
  }
}
</script>

<template>
  <el-button
    v-if="editable"
    :icon="isEditingThis ? IconLoading : Edit"
    :loading="isEditingThis"
    :disabled="disabled"
    size="small"
    :aria-label="isEditingThis ? '正在编辑此步骤' : '编辑此步骤'"
    :aria-busy="isEditingThis"
    data-testid="step-edit-button"
    @click="handleEdit"
  >
    <template v-if="isEditingThis">编辑中…</template>
    <template v-else>✎ 编辑</template>
  </el-button>
</template>

<style scoped>
/* v1.5.1 T03 F2 · StepEditButton 样式
 * 复用 Element Plus 默认色板 + tokens.shared.scss 全局变量
 * 留空 <style scoped> 便于后续扩展（如 hover 高亮、状态色等）*/
</style>
