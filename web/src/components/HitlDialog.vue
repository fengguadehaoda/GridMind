<!--
  HitlDialog — 兼容壳（P0：Edit & Continue 改造过渡期）

  主入口已迁移到 HitlEditDialog（支持三按钮：拒绝 / 仅批准 / 修改后批准）。
  本组件保留为薄壳，仅作为"仅批准 + 拒绝"两按钮的迁移期回退：
  - props.modelValue 控制可见性
  - emit 'approve' / 'reject'（与旧 API 兼容）
  - 内部不再含编辑字段（请使用 HitlEditDialog）

  该组件将在下一个版本（约 1 季度后）废弃；保留是为前端降级或 A/B 灰度。
-->
<template>
  <el-dialog
    v-model="visible"
    title="⚠️ 高危操作确认"
    width="480px"
    :close-on-click-modal="false"
    :show-close="true"
    class="hitl-dialog"
  >
    <template #header="{ titleId, titleClass }">
      <div class="hitl-header">
        <span :id="titleId" :class="titleClass">
          <el-icon style="margin-right: 6px"><WarningFilled /></el-icon>
          高危操作确认
        </span>
      </div>
    </template>

    <div class="hitl-content">
      <el-alert
        title="该操作需要人工确认后才能执行"
        type="warning"
        :closable="false"
        show-icon
        style="margin-bottom: 16px"
      />

      <div class="info-row">
        <span class="info-label">操作工具：</span>
        <el-tag type="danger">{{ interruptNode || '未知' }}</el-tag>
      </div>

      <div v-if="interruptMsg" class="info-row">
        <span class="info-label">说明：</span>
        <span class="info-value">{{ interruptMsg }}</span>
      </div>

      <div class="info-row">
        <span class="info-label">会话：</span>
        <span class="info-value mono-cell">{{ threadId }}</span>
      </div>

      <el-input
        v-model="reason"
        type="textarea"
        :rows="2"
        placeholder="审批备注（可选）"
        style="margin-top: 12px"
        :maxlength="200"
        show-word-limit
      />

      <div class="legacy-hint">
        <el-icon><InfoFilled /></el-icon>
        <span>提示：新版本已支持「修改后批准」功能，请使用 HitlEditDialog；本组件仅作兼容保留。</span>
      </div>
    </div>

    <template #footer>
      <div class="dialog-footer">
        <el-button class="btn-reject" @click="onReject">
          <el-icon style="margin-right: 4px"><Close /></el-icon>
          拒绝
        </el-button>
        <el-button class="btn-approve" @click="onApprove">
          <el-icon style="margin-right: 4px"><Check /></el-icon>
          批准
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { Check, Close, InfoFilled, WarningFilled } from '@element-plus/icons-vue'

const props = defineProps<{
  modelValue: boolean
  interruptNode: string | null
  interruptMsg: string | null
  threadId: string | null
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'approve', reason: string): void
  (e: 'reject', reason: string): void
}>()

const visible = ref(props.modelValue)
const reason = ref('')

watch(() => props.modelValue, (v) => { visible.value = v })
watch(visible, (v) => { emit('update:modelValue', v) })

watch(visible, (v) => {
  if (v) reason.value = ''
})

function onApprove() {
  emit('approve', reason.value)
  visible.value = false
}

function onReject() {
  emit('reject', reason.value)
  visible.value = false
}
</script>

<style scoped>
.hitl-header {
  position: relative;
  padding: var(--space-2) 0;
  font-family: var(--font-cn);
  font-weight: var(--fw-semibold);
  font-size: var(--fs-md);
  color: var(--text-primary);
}

.hitl-header::before {
  content: '';
  position: absolute;
  left: -20px;
  top: 0;
  bottom: 0;
  width: 3px;
  background: var(--brand-primary);
  box-shadow: 0 0 8px var(--brand-primary);
  border-radius: 2px;
}

.hitl-content {
  padding: var(--space-1) 0;
}

.info-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
  font-size: var(--fs-md);
}

.info-label {
  color: var(--text-muted);
  font-family: var(--font-cn);
  font-weight: var(--fw-medium);
  white-space: nowrap;
  font-size: var(--fs-sm);
}

.info-value {
  color: var(--text-primary);
  font-family: var(--font-cn);
}

.mono-cell {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
}

.legacy-hint {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  margin-top: var(--space-3);
  padding: var(--space-2);
  background: var(--bg-card);
  border-left: 2px solid var(--border-default);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  line-height: 1.5;
}

.dialog-footer {
  display: flex;
  justify-content: center;
  gap: var(--space-4);
}

.btn-reject,
.btn-approve {
  min-width: 100px;
  border-radius: var(--radius-md);
  font-weight: var(--fw-semibold);
  font-family: var(--font-cn);
  transition: all var(--dur-fast) var(--ease-out-quint);
  clip-path: var(--clip-corner-sm);
}

.btn-reject {
  background: var(--status-danger-soft) !important;
  border: 1px solid var(--status-danger) !important;
  color: var(--status-danger) !important;
}

.btn-reject:hover {
  background: var(--status-danger) !important;
  color: var(--text-inverse) !important;
  box-shadow: var(--glow-danger);
}

.btn-approve {
  background: var(--brand-primary) !important;
  border: 1px solid var(--brand-primary) !important;
  color: var(--text-inverse) !important;
}

.btn-approve:hover {
  background: var(--brand-primary-hover) !important;
  border-color: var(--brand-primary-hover) !important;
  box-shadow: var(--glow-primary);
}
</style>
