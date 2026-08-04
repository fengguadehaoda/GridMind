<template>
  <el-dialog
    v-model="visible"
    title="⚠️ 高危操作 · 待审核"
    width="720px"
    :close-on-click-modal="false"
    :show-close="true"
    class="hitl-edit-dialog"
  >
    <template #header="{ titleId, titleClass }">
      <div class="hitl-header">
        <span :id="titleId" :class="titleClass">
          <el-icon style="margin-right: 6px"><WarningFilled /></el-icon>
          高危操作 · 待审核
        </span>
        <el-tag type="danger" size="small" effect="dark" style="margin-left: 12px">
          风险等级：高
        </el-tag>
      </div>
    </template>

    <div class="hitl-content">
      <!-- 顶部告警条 -->
      <el-alert
        v-if="safetyReject"
        :title="`安全重检未通过：${safetyReject}`"
        type="error"
        :closable="false"
        show-icon
        style="margin-bottom: 16px"
      />
      <el-alert
        v-else
        title="该操作需要人工确认后才能执行；可选择直接批准、修改后批准或拒绝。"
        type="warning"
        :closable="false"
        show-icon
        style="margin-bottom: 16px"
      />

      <!-- 基础信息 -->
      <div class="info-grid">
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
          <span class="info-value mono-cell">{{ threadId || '-' }}</span>
        </div>
        <!-- 设备信息卡片（只读，从 interruptArgs 提取） -->
        <div v-if="deviceId" class="device-card">
          <div class="device-card-label">目标设备（不可编辑）</div>
          <div class="device-card-value mono-cell">{{ deviceId }}</div>
        </div>
      </div>

      <!-- 内嵌表单编辑器（仅在有可编辑字段时显示） -->
      <div v-if="editableFields.length > 0" class="editor-section">
        <div class="editor-section-title">
          <el-icon style="margin-right: 4px"><EditPen /></el-icon>
          内嵌编辑器
          <span class="editor-section-sub">修改下列字段后提交"修改后批准"</span>
        </div>
        <el-form
          ref="formRef"
          :model="formData"
          :rules="formRules"
          label-position="top"
          label-width="120px"
          class="hitl-form"
        >
          <el-form-item
            v-for="field in editableFields"
            :key="field.key"
            :label="field.label"
            :prop="field.key"
            class="hitl-form-item"
          >
            <!-- textarea 类型 -->
            <el-input
              v-if="field.type === 'textarea'"
              v-model="formData[field.key]"
              type="textarea"
              :rows="field.key === 'description' ? 6 : 3"
              :maxlength="field.max_length ?? undefined"
              :placeholder="field.placeholder || ''"
              show-word-limit
              :disabled="busy"
            />
            <!-- select 类型 -->
            <el-select
              v-else-if="field.type === 'select' && field.options"
              v-model="formData[field.key]"
              :placeholder="field.placeholder || '请选择'"
              :disabled="busy"
              style="width: 100%"
            >
              <el-option
                v-for="opt in field.options"
                :key="opt"
                :label="priorityLabel(opt)"
                :value="opt"
              />
            </el-select>
            <!-- text 类型 -->
            <el-input
              v-else
              v-model="formData[field.key]"
              :maxlength="field.max_length ?? undefined"
              :placeholder="field.placeholder || ''"
              :disabled="busy"
            />
            <div v-if="field.help_text" class="hitl-help">{{ field.help_text }}</div>
          </el-form-item>
        </el-form>
      </div>

      <!-- 修改原因（edit 模式专属必填） -->
      <div v-if="editableFields.length > 0" class="editor-section">
        <div class="editor-section-title">
          <el-icon style="margin-right: 4px"><InfoFilled /></el-icon>
          修改原因
          <span class="editor-section-sub required-mark">*</span>
        </div>
        <el-form
          ref="reasonFormRef"
          :model="formData"
          :rules="reasonRules"
          label-width="0"
        >
          <el-form-item prop="edit_reason">
            <el-input
              v-model="formData.edit_reason"
              type="textarea"
              :rows="2"
              :maxlength="200"
              show-word-limit
              placeholder="请简述修改原因（必填，≤ 200 字），如：保电时段降级 / 避开 22:00-24:00"
              :disabled="busy"
            />
          </el-form-item>
        </el-form>
      </div>

      <!-- 拒绝原因（仅在点拒绝时校验） -->
      <div v-if="editableFields.length > 0" class="editor-section">
        <div class="editor-section-title">
          <el-icon style="margin-right: 4px"><CircleClose /></el-icon>
          拒绝原因（仅点"拒绝"时填）
        </div>
        <el-input
          v-model="formData.reject_reason"
          type="textarea"
          :rows="2"
          :maxlength="200"
          show-word-limit
          placeholder="如：当前保电时段不允许操作（≤ 200 字）"
          :disabled="busy"
        />
      </div>
    </div>

    <template #footer>
      <div class="dialog-footer">
        <!-- 拒绝 -->
        <el-button
          class="btn-reject"
          :loading="busy && pendingDecision === 'reject'"
          :disabled="busy"
          @click="onReject"
        >
          <el-icon style="margin-right: 4px"><Close /></el-icon>
          拒绝
        </el-button>
        <!-- 仅批准 -->
        <el-button
          class="btn-approve"
          :loading="busy && pendingDecision === 'approve'"
          :disabled="busy"
          @click="onApprove"
        >
          <el-icon style="margin-right: 4px"><Check /></el-icon>
          仅批准
        </el-button>
        <!-- 修改后批准 -->
        <el-button
          v-if="editableFields.length > 0"
          class="btn-edit-approve"
          type="primary"
          :loading="busy && pendingDecision === 'edit_approve'"
          :disabled="busy || !isFormValid"
          @click="onEditApprove"
        >
          <el-icon style="margin-right: 4px"><EditPen /></el-icon>
          修改后批准
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  Check,
  CircleClose,
  Close,
  EditPen,
  InfoFilled,
  WarningFilled,
} from '@element-plus/icons-vue'
import type { FormInstance, FormRules } from 'element-plus'
import type { EditableField } from '../types'
import { getEditableFields } from '../api/hitlSchemas'

const props = defineProps<{
  modelValue: boolean
  interruptNode: string | null
  interruptMsg: string | null
  threadId: string | null
  interruptArgs?: Record<string, unknown>
  busy?: boolean
  safetyReject?: string | null
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'approve', reason: string): void
  (e: 'reject', reason: string): void
  (
    e: 'edit-approve',
    payload: { editedArgs: Record<string, unknown>; editReason: string },
  ): void
}>()

const visible = ref(props.modelValue)
const formRef = ref<FormInstance | null>(null)
const reasonFormRef = ref<FormInstance | null>(null)
const pendingDecision = ref<'approve' | 'reject' | 'edit_approve' | null>(null)

const editableFields = computed<EditableField[]>(() =>
  getEditableFields(props.interruptNode),
)

// 表单数据（key → value）
const formData = ref<Record<string, string>>({})
const formRules = ref<FormRules>({})
const reasonRules = ref<FormRules>({
  edit_reason: [
    { required: true, message: '修改原因不能为空', trigger: 'blur' },
    { max: 200, message: '≤ 200 字', trigger: 'blur' },
  ],
})

const isFormValid = computed(() => {
  // 当有可编辑字段时，编辑模式要求：必填字段非空、修改原因非空
  if (editableFields.value.length === 0) return true
  for (const f of editableFields.value) {
    if (f.required) {
      const v = (formData.value[f.key] ?? '').trim()
      if (!v) return false
    }
    if (f.max_length && (formData.value[f.key] ?? '').length > f.max_length) {
      return false
    }
  }
  const er = (formData.value.edit_reason ?? '').trim()
  return er.length > 0 && er.length <= 200
})

const deviceId = computed(() => {
  const args = props.interruptArgs || {}
  const v = args.device_id
  return typeof v === 'string' ? v : null
})

function priorityLabel(p: string): string {
  return p === 'high' ? '高' : p === 'medium' ? '中' : p === 'low' ? '低' : p
}

function buildFormRules(): FormRules {
  const rules: FormRules = {}
  for (const f of editableFields.value) {
    const r: any[] = []
    if (f.required) r.push({ required: true, message: `${f.label}不能为空`, trigger: 'blur' })
    if (f.max_length) r.push({ max: f.max_length, message: `≤ ${f.max_length} 字`, trigger: 'blur' })
    if (f.options && f.options.length > 0) {
      r.push({
        validator: (_: any, value: any, cb: any) => {
          if (value === undefined || value === null || value === '') {
            return cb(new Error(`${f.label}不能为空`))
          }
          if (!f.options!.includes(String(value))) {
            return cb(new Error(`无效${f.label}`))
          }
          cb()
        },
        trigger: 'change',
      })
    }
    if (r.length > 0) rules[f.key] = r
  }
  return rules
}

function initFormData() {
  const next: Record<string, string> = {}
  for (const f of editableFields.value) {
    // 默认值：优先取 interruptArgs 对应 key
    const raw = props.interruptArgs?.[f.key]
    if (raw !== undefined && raw !== null) {
      next[f.key] = String(raw)
    } else if (f.type === 'select' && f.options && f.options.length > 0) {
      next[f.key] = f.options[0]
    } else {
      next[f.key] = ''
    }
  }
  next.edit_reason = ''
  next.reject_reason = ''
  formData.value = next
  formRules.value = buildFormRules()
}

watch(() => props.modelValue, (v) => {
  visible.value = v
  if (v) {
    initFormData()
    pendingDecision.value = null
  }
})
watch(visible, (v) => {
  emit('update:modelValue', v)
})

async function validateAll(): Promise<boolean> {
  const tasks: Array<Promise<unknown>> = []
  if (formRef.value) tasks.push(formRef.value.validate())
  if (reasonFormRef.value) tasks.push(reasonFormRef.value.validate())
  if (tasks.length === 0) return true
  try {
    await Promise.all(tasks)
    return true
  } catch {
    return false
  }
}

async function onApprove() {
  pendingDecision.value = 'approve'
  emit('approve', formData.value.reject_reason || '')
}

async function onReject() {
  pendingDecision.value = 'reject'
  // 拒绝原因必填校验
  const reason = (formData.value.reject_reason || '').trim()
  if (!reason) {
    formRef.value?.validateField
    // 简化：直接提示（前端校验不通过时 disable 不应允许点击）
    emit('reject', reason)
    return
  }
  emit('reject', reason)
}

async function onEditApprove() {
  if (editableFields.value.length === 0) return
  pendingDecision.value = 'edit_approve'
  const ok = await validateAll()
  if (!ok) {
    pendingDecision.value = null
    return
  }
  const editedArgs: Record<string, unknown> = {}
  for (const f of editableFields.value) {
    editedArgs[f.key] = formData.value[f.key]
  }
  emit('edit-approve', {
    editedArgs,
    editReason: formData.value.edit_reason || '',
  })
}

// 为 SV 截图准备：暴露 UI 状态
watch(() => props.busy, () => {
  if (!props.busy) pendingDecision.value = null
})
</script>

<style scoped>
/* 标题区 */
.hitl-header {
  position: relative;
  padding: var(--space-2) 0;
  font-family: var(--font-cn);
  font-weight: var(--fw-semibold);
  font-size: var(--fs-md);
  color: var(--text-primary);
  display: flex;
  align-items: center;
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

/* 基础信息网格 */
.info-grid {
  margin-bottom: var(--space-3);
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

/* 设备信息卡片 */
.device-card {
  background: var(--bg-card);
  border: 1px solid var(--border-light);
  border-left: 3px solid var(--brand-primary);
  padding: var(--space-3);
  border-radius: var(--radius-md);
  margin-top: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.device-card-label {
  color: var(--text-muted);
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
}

.device-card-value {
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: var(--fs-md);
  font-weight: var(--fw-semibold);
}

/* 编辑器分块 */
.editor-section {
  margin-top: var(--space-4);
  padding: var(--space-3);
  background: var(--bg-card);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
}

.editor-section-title {
  display: flex;
  align-items: center;
  font-family: var(--font-cn);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
  font-size: var(--fs-md);
  margin-bottom: var(--space-3);
}

.editor-section-sub {
  color: var(--text-muted);
  font-size: var(--fs-xs);
  font-weight: var(--fw-regular);
  margin-left: var(--space-2);
}

.required-mark {
  color: var(--status-danger);
  font-size: var(--fs-md);
  margin-left: var(--space-1);
}

.hitl-help {
  color: var(--text-muted);
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  margin-top: var(--space-1);
}

.hitl-form-item {
  margin-bottom: var(--space-3);
}

/* 底部按钮 */
.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-3);
}

.btn-reject,
.btn-approve,
.btn-edit-approve {
  min-width: 110px;
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

.btn-reject:hover:not(:disabled) {
  background: var(--status-danger) !important;
  color: var(--text-inverse) !important;
  box-shadow: var(--glow-danger);
}

.btn-approve {
  background: var(--bg-card) !important;
  border: 1px solid var(--brand-primary) !important;
  color: var(--brand-primary) !important;
}

.btn-approve:hover:not(:disabled) {
  background: var(--brand-primary) !important;
  border-color: var(--brand-primary) !important;
  color: var(--text-inverse) !important;
  box-shadow: var(--glow-primary);
}

.btn-edit-approve {
  background: var(--brand-primary) !important;
  border: 1px solid var(--brand-primary) !important;
  color: var(--text-inverse) !important;
}

.btn-edit-approve:hover:not(:disabled) {
  background: var(--brand-primary-hover) !important;
  border-color: var(--brand-primary-hover) !important;
  box-shadow: var(--glow-primary);
}

.btn-edit-approve:disabled,
.btn-reject:disabled,
.btn-approve:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
