/**
 * HITL Edit & Continue — 可编辑字段定义（前端镜像）
 *
 * 与 `api/services/hitl_editable_schemas.py` 保持同步。
 * CI 校验阶段会断言两侧字段名一致；本文件只在 SSOT 中修改后镜像。
 *
 * 字段新增/修改流程：
 * 1. 修改 api/services/hitl_editable_schemas.py
 * 2. 同步本文件
 * 3. 重新跑 `npx vue-tsc --noEmit` 验证
 */

import type { EditableField } from '../types'

export const EDITABLE_SCHEMA: Record<string, EditableField[]> = {
  dispatch_work_order: [
    {
      key: 'description',
      type: 'textarea',
      label: '故障描述',
      required: true,
      max_length: 500,
      placeholder: '请描述故障现象、影响范围、初步判断',
      help_text: '必填，≤ 500 字',
    },
    {
      key: 'priority',
      type: 'select',
      label: '优先级',
      required: true,
      options: ['high', 'medium', 'low'],
      placeholder: '',
      help_text: '高危时段请选择 medium 及以下',
    },
    // device_id 不可编辑（LOCKED_FIELDS 黑名单拦截）
  ],
  suggest_shutdown: [
    {
      key: 'reason',
      type: 'textarea',
      label: '停运原因',
      required: true,
      max_length: 200,
      placeholder: '说明停运必要性、预计时长、保电替代方案',
      help_text: '必填，≤ 200 字',
    },
  ],
  // 融合层 HITL（diagnosis_review / FUSION_REVIEW_NODE）：
  // - 标准模式在真实 LLM / mock 推理下可能触发融合层 HITL（机理校验 critical 失败、
  //   规则护栏强制 HITL、LLM↔机理冲突），interrupt_tool = "diagnosis_review"
  // - 后端 _fusion_interrupt_payload 给出 final_severity/forced_action/conflict_detected/
  //   triggered_rules/device_id（只读上下文），但前端 schema 此前缺失该键
  //   → getEditableFields 返回 [] → 弹窗降级为 2 按钮（无编辑器、无"修改后批准"），
  //   与演示模式（dispatch_work_order / suggest_shutdown → 3 按钮 + 编辑器）不一致
  // - 修复：补齐 schema；后端 process_edit_decision 对 diagnosis_review 走
  //   _decision_without_resume（无 pending_tool_plan），edited_args 仅用于审计
  diagnosis_review: [
    {
      key: 'action',
      type: 'textarea',
      label: '处置建议',
      required: true,
      max_length: 500,
      placeholder: '请填写确认后的处置措施 / 后续动作（如：立即隔离故障点、启动备自投、通知现场核查…）',
      help_text: '必填，≤ 500 字',
    },
    {
      key: 'severity',
      type: 'select',
      label: '确认风险等级',
      required: true,
      options: ['high', 'medium', 'low'],
      placeholder: '',
      help_text: '高危时段请选择 medium 及以下',
    },
    // device_id 不可编辑（LOCKED_FIELDS 黑名单拦截）
  ],
}

/** 根据工具名取字段；未知工具返回空数组（视作无字段可编辑）。 */
export function getEditableFields(toolName: string | null | undefined): EditableField[] {
  if (!toolName) return []
  return EDITABLE_SCHEMA[toolName] ?? []
}

/** 黑名单字段（与后端 LOCKED_FIELDS 同步），仅前端二次校验。 */
export const LOCKED_FIELDS_KEYS: ReadonlyArray<string> = [
  'device_id',
  'work_order_id',
  'shutdown_id',
  'created_at',
  'thread_id',
  'audit_id',
  'tool_name',
]

/** 检查给定字段集是否含黑名单。 */
export function containsLockedFields(args: Record<string, unknown> | null | undefined): string[] {
  if (!args) return []
  return Object.keys(args).filter((k) => LOCKED_FIELDS_KEYS.includes(k))
}
