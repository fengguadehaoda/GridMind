<template>
  <transition name="hitl-dialog-fade">
    <div
      v-if="isOpen"
      ref="dialogRef"
      class="hitl-dialog-container"
      role="dialog"
      aria-modal="true"
      aria-labelledby="hitl-dialog-title"
      aria-describedby="hitl-dialog-desc"
      data-testid="hitl-dialog"
      @focus-trap-escape="handleEscapeClose"
    >
      <!-- 背景遮罩（决策 7.5：遮罩 < 弹窗 1000 < toast 2000） -->
      <div
        class="hitl-dialog-backdrop"
        aria-hidden="true"
        @click="handleBackdropClose"
      ></div>

      <!-- 弹窗内容 -->
      <div class="hitl-dialog" role="document">
        <!-- ── 头部 ──────────────────────────────────────── -->
        <header class="hitl-dialog-header">
          <h3 id="hitl-dialog-title" class="hitl-dialog-title">
            <el-icon style="margin-right: 6px"><WarningFilled /></el-icon>
            高危操作 · 待审核
          </h3>
          <el-tag type="danger" size="small" effect="dark" style="margin-left: 12px">
            风险等级：高
          </el-tag>
          <div class="hitl-dialog-header-spacer" />
          <el-button
            ref="closeBtnRef"
            :icon="Close"
            size="small"
            circle
            plain
            aria-label="关闭弹窗"
            data-testid="hitl-close-btn"
            @click="handleClose"
          />
        </header>

        <!-- ── 主体 ──────────────────────────────────────── -->
        <div id="hitl-dialog-desc" class="hitl-dialog-body">
          <!-- 顶部告警条 -->
          <el-alert
            v-if="safetyReject"
            :title="`安全重检未通过：${safetyReject}`"
            type="error"
            :closable="false"
            show-icon
            class="hitl-alert"
          />
          <el-alert
            v-else
            title="该操作需要人工确认后才能执行；可选择直接批准、修改后批准或拒绝。"
            type="warning"
            :closable="false"
            show-icon
            class="hitl-alert"
          />

          <!-- 基础信息 -->
          <div class="info-grid">
            <div class="info-row">
              <span class="info-label">操作工具：</span>
              <el-tag type="danger" data-testid="hitl-interrupt-node">
                {{ interruptNode || '未知' }}
              </el-tag>
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

        <!-- ── 底部（决策三按钮）────────────────────────────── -->
        <footer class="hitl-dialog-footer">
          <!-- 拒绝 -->
          <el-button
            ref="rejectBtnRef"
            class="btn-reject"
            data-testid="hitl-btn-reject"
            :loading="busy && pendingDecision === 'reject'"
            :disabled="busy"
            @click="handleReject"
          >
            <el-icon style="margin-right: 4px"><Close /></el-icon>
            拒绝
          </el-button>
          <!-- 仅批准 -->
          <el-button
            ref="approveBtnRef"
            class="btn-approve"
            data-testid="hitl-btn-approve"
            :loading="busy && pendingDecision === 'approve'"
            :disabled="busy"
            @click="handleApprove"
          >
            <el-icon style="margin-right: 4px"><Check /></el-icon>
            仅批准
          </el-button>
          <!-- 修改后批准 -->
          <el-button
            v-if="editableFields.length > 0"
            ref="editApproveBtnRef"
            class="btn-edit-approve"
            type="primary"
            data-testid="hitl-btn-edit-approve"
            :loading="busy && pendingDecision === 'edit_approve'"
            :disabled="busy || !isFormValid"
            @click="handleEditApprove"
          >
            <el-icon style="margin-right: 4px"><EditPen /></el-icon>
            修改后批准
          </el-button>
        </footer>
      </div>
    </div>
  </transition>
</template>

<script setup lang="ts">
/**
 * GridMind v1.5.1 T05 · F4 HITL 弹窗前置
 *
 * 改造要点（架构 §3.4 + §6.4 + 主理人决策 7.5）：
 *   - 替换 el-dialog → 自定义 div（class="hitl-dialog-container"）保留 props 接口
 *   - sticky 定位：position: fixed; top: 80px; z-index: var(--z-dialog)（弹窗 1000 < toast 2000）
 *   - backdrop blur 遮罩：z-index < 弹窗 1000
 *   - focus trap：T01 useFocusTrap composable，4 按钮循环 + Esc 关闭 + 焦点回收
 *   - 二次确认：× 关闭 / 点遮罩 / Esc 键 三种交互统一弹 ElMessageBox "稍后处理"
 *   - a11y：role="dialog" + aria-modal + aria-labelledby + aria-describedby
 *   - 保留原有三按钮（拒绝 / 仅批准 / 修改后批准）+ el-form 内嵌编辑器
 *
 * 作者：寇豆码（T05 工程师）
 * 参考：frontend-v151-architecture-2026-08-04.md §3.4 + §5 T05 + §6.4
 */
import { computed, nextTick, ref, watch } from 'vue'
import {
  Check,
  CircleClose,
  Close,
  EditPen,
  InfoFilled,
  WarningFilled,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import type { EditableField } from '@/types'
import { getEditableFields } from '@/api/hitlSchemas'
import { useFocusTrap } from '@/composables/useFocusTrap'

// ─── Props（向后兼容 App.vue 调用方） ──────────────────────
const props = defineProps<{
  modelValue: boolean
  interruptNode: string | null
  interruptMsg: string | null
  threadId: string | null
  interruptArgs?: Record<string, unknown>
  busy?: boolean
  safetyReject?: string | null
}>()

// ─── Emits（向后兼容） ──────────────────────────────────────
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'approve', reason: string): void
  (e: 'reject', reason: string): void
  (
    e: 'edit-approve',
    payload: { editedArgs: Record<string, unknown>; editReason: string },
  ): void
}>()

// ─── v-model 桥接 ─────────────────────────────────────────
const isOpen = computed<boolean>({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

// ─── Refs ─────────────────────────────────────────────────
const dialogRef = ref<HTMLElement | null>(null)
const formRef = ref<FormInstance | null>(null)
const reasonFormRef = ref<FormInstance | null>(null)
const closeBtnRef = ref<unknown>(null)
const rejectBtnRef = ref<unknown>(null)
const approveBtnRef = ref<unknown>(null)
const editApproveBtnRef = ref<unknown>(null)
const pendingDecision = ref<'approve' | 'reject' | 'edit_approve' | null>(null)

/** 上一次的"待审状态"快照（用于焦点回收 / 关闭时判定是否需二次确认） */
let lastOpenValue = false

// ─── Focus trap（T01 useFocusTrap 已就绪，4 按钮 + textarea 循环） ──
// 决策：autoActivate=true + escapeDeactivates=false（Esc 由容器分发 focus-trap-escape）
useFocusTrap({
  containerRef: dialogRef,
  autoActivate: true,
  escapeDeactivates: false,
})

// ─── 表单字段（与 v1.5.0 兼容） ──────────────────────────
const editableFields = computed<EditableField[]>(() =>
  getEditableFields(props.interruptNode),
)

const formData = ref<Record<string, string>>({})
const formRules = ref<FormRules>({})
const reasonRules = ref<FormRules>({
  edit_reason: [
    { required: true, message: '修改原因不能为空', trigger: 'blur' },
    { max: 200, message: '≤ 200 字', trigger: 'blur' },
  ],
})

const isFormValid = computed(() => {
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
    const r: unknown[] = []
    if (f.required) {
      r.push({ required: true, message: `${f.label}不能为空`, trigger: 'blur' })
    }
    if (f.max_length) {
      r.push({ max: f.max_length, message: `≤ ${f.max_length} 字`, trigger: 'blur' })
    }
    if (f.options && f.options.length > 0) {
      r.push({
        validator: (_: unknown, value: unknown, cb: (err?: Error) => void) => {
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
    if (r.length > 0) {
      ;(rules as Record<string, unknown[]>)[f.key] = r
    }
  }
  return rules
}

function initFormData() {
  const next: Record<string, string> = {}
  for (const f of editableFields.value) {
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

// ─── Watch：modelValue 变化时初始化表单 + 清理 pendingDecision ──
watch(
  () => props.modelValue,
  (v) => {
    if (v && !lastOpenValue) {
      // 从关闭 → 打开：初始化表单
      initFormData()
      pendingDecision.value = null
    }
    lastOpenValue = v
  },
  { immediate: true },
)

watch(
  () => props.busy,
  (b) => {
    if (!b) pendingDecision.value = null
  },
)

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

// ─── 三按钮 handler ──────────────────────────────────────
async function handleApprove() {
  pendingDecision.value = 'approve'
  emit('approve', formData.value.reject_reason || '')
  isOpen.value = false
}

async function handleReject() {
  pendingDecision.value = 'reject'
  const reason = (formData.value.reject_reason || '').trim()
  // 无可编辑字段时：直接拒绝；有可编辑字段时也允许空理由（保留向后兼容）
  emit('reject', reason)
  isOpen.value = false
}

async function handleEditApprove() {
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
  isOpen.value = false
}

// ─── 二次确认（× / Esc / 点遮罩 三种交互统一） ────────────
/**
 * 弹 ElMessageBox "稍后处理此 HITL 任务？"
 * - 用户确认"稍后处理" → 关闭弹窗
 * - 用户取消 → 弹窗保持打开（focus trap 已激活）
 *
 * 三种入口：
 *   1. handleClose()           — × 按钮 click
 *   2. handleBackdropClose()   — 点遮罩 click
 *   3. handleEscapeClose()     — focus trap 派发的 Esc 事件
 */
async function handleClose() {
  await requestClose('handleClose')
}
async function handleBackdropClose() {
  await requestClose('handleBackdropClose')
}
async function handleEscapeClose() {
  await requestClose('handleEscapeClose')
}

async function requestClose(_source: 'handleClose' | 'handleBackdropClose' | 'handleEscapeClose'): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '稍后处理此 HITL 任务？任务仍在队列中待审。',
      '稍后处理',
      {
        confirmButtonText: '稍后处理',
        cancelButtonText: '继续审批',
        type: 'info',
        closeOnClickModal: true,
        showClose: true,
        // 二次确认也走焦点循环（ElMessageBox 内置 trap）
        // 注：ElMessageBox 弹出后会自动 focus 到 confirm 按钮，关闭后焦点回到主弹窗
      },
    )
    // 用户确认"稍后处理" → 关闭主弹窗（让 taskStore.audit.latestPending 保留）
    isOpen.value = false
    ElMessage.info('已稍后处理，可在 HITL 审计页继续审批')
  } catch {
    // 用户取消"稍后处理" → 弹窗保持打开
    // focus trap 已激活，无需手动聚焦
    // 兜底：等待 nextTick 后重新聚焦第一个按钮（防止 ElMessageBox 关闭后焦点丢失）
    await nextTick()
    const firstFocusable = dialogRef.value?.querySelector<HTMLElement>(
      'button:not([disabled]), textarea:not([disabled]), input:not([disabled])',
    )
    firstFocusable?.focus()
  }
}

// ─── 暴露（仅 dev/测试用，便于单测断言） ──────────────────
defineExpose({
  /** 重置 pendingDecision（测试断言用）*/
  resetPendingDecision: () => {
    pendingDecision.value = null
  },
  /** 当前焦点容器（测试断言用）*/
  getContainer: () => dialogRef.value,
  /** 三个决策按钮（focus trap 测试断言用）*/
  refs: {
    rejectBtnRef,
    approveBtnRef,
    editApproveBtnRef,
    closeBtnRef,
  },
})
</script>

<style scoped>
/* ═══════════════════════════════════════════════════════════════
 * T05 弹窗前置 · 容器 + 遮罩 + 内容区 + 进场动画
 *
 * 关键决策（主理人 7.5）：弹窗 z-index = var(--z-dialog)；toast 2000 > 弹窗 1000
 * 背景遮罩 < 弹窗（不阻挡 toast 2000；toast 显示时可见）
 * ═══════════════════════════════════════════════════════════════ */

/* 弹窗容器（fixed 定位，遮罩 + 内容绝对布局） */
.hitl-dialog-container {
  position: fixed;
  inset: 0;
  z-index: var(--z-dialog); /* 决策 7.5：弹窗 1000 < toast 2000 */
  pointer-events: auto;
  outline: none; /* 防止 focus trap focus 时浏览器默认 outline 干扰 */
}

/* 背景遮罩（独立 z-index < 弹窗） */
.hitl-dialog-backdrop {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  z-index: 1;
  cursor: pointer;
}

/* 弹窗本体（z-index > 遮罩） */
.hitl-dialog {
  position: absolute;
  top: 80px; /* Header 之下（Header 高度 60px + 20px 间距）*/
  left: 50%;
  transform: translateX(-50%);
  width: 720px;
  max-width: calc(100vw - var(--space-8, 32px));
  max-height: calc(100vh - 120px); /* 顶部 80px + 底部 40px 余量 */
  display: flex;
  flex-direction: column;
  background: var(--bg-elevated, #1e2030);
  border: 1px solid var(--brand-primary, #615ced);
  border-radius: var(--radius-md, 8px);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
  overflow: hidden;
  z-index: var(--z-dialog);
}

/* ── Header ────────────────────────────────────────────── */
.hitl-dialog-header {
  display: flex;
  align-items: center;
  gap: var(--space-2, 8px);
  padding: var(--space-3, 12px) var(--space-4, 16px);
  background: var(--brand-primary-soft, rgba(97, 92, 237, 0.08));
  border-bottom: 1px solid var(--border-default, #2d2f44);
}

.hitl-dialog-title {
  margin: 0;
  font-family: var(--font-cn, 'PingFang SC', 'Microsoft YaHei', sans-serif);
  font-weight: var(--fw-semibold, 600);
  font-size: var(--fs-md, 14px);
  color: var(--text-primary, #e4e6f0);
  display: flex;
  align-items: center;
}

.hitl-dialog-header-spacer {
  flex: 1;
}

/* ── Body（可滚动）─────────────────────────────────────── */
.hitl-dialog-body {
  padding: var(--space-4, 16px);
  overflow-y: auto;
  flex: 1;
}

.hitl-alert {
  margin-bottom: var(--space-3, 12px);
}

/* ── Footer（决策三按钮）───────────────────────────────── */
.hitl-dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-3, 12px);
  padding: var(--space-3, 12px) var(--space-4, 16px);
  background: var(--bg-card, #181a29);
  border-top: 1px solid var(--border-default, #2d2f44);
}

/* ── 按钮样式（沿用 v1.5.0 设计语言）──────────────────── */
.btn-reject,
.btn-approve,
.btn-edit-approve {
  min-width: 110px;
  border-radius: var(--radius-md, 8px);
  font-weight: var(--fw-semibold, 600);
  font-family: var(--font-cn, 'PingFang SC', 'Microsoft YaHei', sans-serif);
  transition: all var(--dur-fast, 200ms) var(--ease-out-quint, ease);
  clip-path: var(--clip-corner-sm, none);
}

.btn-reject {
  background: var(--status-danger-soft, rgba(220, 38, 38, 0.1)) !important;
  border: 1px solid var(--status-danger, #dc2626) !important;
  color: var(--status-danger, #dc2626) !important;
}

.btn-reject:hover:not(:disabled) {
  background: var(--status-danger, #dc2626) !important;
  color: var(--text-inverse, #fff) !important;
  box-shadow: var(--glow-danger, 0 0 12px rgba(220, 38, 38, 0.4));
}

.btn-approve {
  background: var(--bg-card, #181a29) !important;
  border: 1px solid var(--brand-primary, #615ced) !important;
  color: var(--brand-primary, #615ced) !important;
}

.btn-approve:hover:not(:disabled) {
  background: var(--brand-primary, #615ced) !important;
  border-color: var(--brand-primary, #615ced) !important;
  color: var(--text-inverse, #fff) !important;
  box-shadow: var(--glow-primary, 0 0 12px rgba(97, 92, 237, 0.4));
}

.btn-edit-approve {
  background: var(--brand-primary, #615ced) !important;
  border: 1px solid var(--brand-primary, #615ced) !important;
  color: var(--text-inverse, #fff) !important;
}

.btn-edit-approve:hover:not(:disabled) {
  background: var(--brand-primary-hover, #4f48d9) !important;
  border-color: var(--brand-primary-hover, #4f48d9) !important;
  box-shadow: var(--glow-primary, 0 0 12px rgba(97, 92, 237, 0.4));
}

.btn-edit-approve:disabled,
.btn-reject:disabled,
.btn-approve:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ── info-grid（基础信息块，沿用 v1.5.0）───────────────── */
.info-grid {
  margin-bottom: var(--space-3, 12px);
}

.info-row {
  display: flex;
  align-items: center;
  gap: var(--space-2, 8px);
  margin-bottom: var(--space-3, 12px);
  font-size: var(--fs-md, 14px);
}

.info-label {
  color: var(--text-muted, #8b8fa3);
  font-family: var(--font-cn, 'PingFang SC', 'Microsoft YaHei', sans-serif);
  font-weight: var(--fw-medium, 500);
  white-space: nowrap;
  font-size: var(--fs-sm, 12px);
}

.info-value {
  color: var(--text-primary, #e4e6f0);
  font-family: var(--font-cn, 'PingFang SC', 'Microsoft YaHei', sans-serif);
}

.mono-cell {
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: var(--fs-xs, 11px);
}

.device-card {
  background: var(--bg-card, #181a29);
  border: 1px solid var(--border-light, #2d2f44);
  border-left: 3px solid var(--brand-primary, #615ced);
  padding: var(--space-3, 12px);
  border-radius: var(--radius-md, 8px);
  margin-top: var(--space-3, 12px);
  display: flex;
  flex-direction: column;
  gap: var(--space-1, 4px);
}

.device-card-label {
  color: var(--text-muted, #8b8fa3);
  font-family: var(--font-cn, 'PingFang SC', 'Microsoft YaHei', sans-serif);
  font-size: var(--fs-xs, 11px);
}

.device-card-value {
  color: var(--text-primary, #e4e6f0);
  font-family: var(--font-mono, 'JetBrains Mono', monospace);
  font-size: var(--fs-md, 14px);
  font-weight: var(--fw-semibold, 600);
}

/* ── editor-section（沿用 v1.5.0）──────────────────────── */
.editor-section {
  margin-top: var(--space-4, 16px);
  padding: var(--space-3, 12px);
  background: var(--bg-card, #181a29);
  border: 1px solid var(--border-light, #2d2f44);
  border-radius: var(--radius-md, 8px);
}

.editor-section-title {
  display: flex;
  align-items: center;
  font-family: var(--font-cn, 'PingFang SC', 'Microsoft YaHei', sans-serif);
  font-weight: var(--fw-semibold, 600);
  color: var(--text-primary, #e4e6f0);
  font-size: var(--fs-md, 14px);
  margin-bottom: var(--space-3, 12px);
}

.editor-section-sub {
  color: var(--text-muted, #8b8fa3);
  font-size: var(--fs-xs, 11px);
  font-weight: var(--fw-regular, 400);
  margin-left: var(--space-2, 8px);
}

.required-mark {
  color: var(--status-danger, #dc2626);
  font-size: var(--fs-md, 14px);
  margin-left: var(--space-1, 4px);
}

.hitl-help {
  color: var(--text-muted, #8b8fa3);
  font-family: var(--font-cn, 'PingFang SC', 'Microsoft YaHei', sans-serif);
  font-size: var(--fs-xs, 11px);
  margin-top: var(--space-1, 4px);
}

.hitl-form-item {
  margin-bottom: var(--space-3, 12px);
}

/* ── 进场/退场动画（fade + translateY）────────────────── */
.hitl-dialog-fade-enter-active,
.hitl-dialog-fade-leave-active {
  transition: opacity var(--dur-fast, 200ms) var(--ease-out-quint, ease),
    transform var(--dur-fast, 200ms) var(--ease-out-quint, ease);
}

.hitl-dialog-fade-enter-active .hitl-dialog,
.hitl-dialog-fade-leave-active .hitl-dialog {
  transition: opacity var(--dur-fast, 200ms) var(--ease-out-quint, ease),
    transform var(--dur-fast, 200ms) var(--ease-out-quint, ease);
}

.hitl-dialog-fade-enter-from {
  opacity: 0;
}
.hitl-dialog-fade-leave-to {
  opacity: 0;
}

.hitl-dialog-fade-enter-from .hitl-dialog {
  transform: translateX(-50%) translateY(-20px);
  opacity: 0;
}

.hitl-dialog-fade-leave-to .hitl-dialog {
  transform: translateX(-50%) translateY(-20px);
  opacity: 0;
}

/* ── 响应式：< 768px 全宽 ──────────────────────────────── */
@media (max-width: 768px) {
  .hitl-dialog {
    width: calc(100vw - var(--space-4, 16px));
    top: 70px;
    max-height: calc(100vh - 90px);
  }
  .btn-reject,
  .btn-approve,
  .btn-edit-approve {
    min-width: 80px;
    flex: 1;
  }
}

/* ── prefers-reduced-motion（a11y）─────────────────────── */
@media (prefers-reduced-motion: reduce) {
  .hitl-dialog-fade-enter-active,
  .hitl-dialog-fade-leave-active,
  .hitl-dialog-fade-enter-active .hitl-dialog,
  .hitl-dialog-fade-leave-active .hitl-dialog {
    transition: none;
  }
}
</style>
