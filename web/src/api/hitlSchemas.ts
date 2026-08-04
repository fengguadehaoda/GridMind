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
