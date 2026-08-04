"""HITL 可编辑字段集中定义。

由 ``api/schemas/hitl_edit.py`` 复用同套模型；前端 ``web/src/api/hitlSchemas.ts``
为镜像（CI 校验字段名一致）。

修改本文件时务必同步前端镜像。
"""

from __future__ import annotations

from api.schemas.hitl_edit import EditableField


# ── 集中定义：tool_name -> [EditableField] ──────────────────

EDITABLE_SCHEMA: dict[str, list[EditableField]] = {
    "dispatch_work_order": [
        EditableField(
            key="description",
            type="textarea",
            label="故障描述",
            required=True,
            max_length=500,
            placeholder="请描述故障现象、影响范围、初步判断",
            help_text="必填，≤ 500 字",
        ),
        EditableField(
            key="priority",
            type="select",
            label="优先级",
            required=True,
            options=["high", "medium", "low"],
            help_text="高危时段请选择 medium 及以下",
        ),
        # device_id 不可编辑（在 LOCKED_FIELDS 中拦截）
    ],
    "suggest_shutdown": [
        EditableField(
            key="reason",
            type="textarea",
            label="停运原因",
            required=True,
            max_length=200,
            placeholder="说明停运必要性、预计时长、保电替代方案",
            help_text="必填，≤ 200 字",
        ),
    ],
}


def get_editable_fields(tool_name: str) -> list[EditableField]:
    """根据工具名获取可编辑字段定义。

    未知工具返回空列表（视作无可编辑字段，仅支持 approve/reject）。
    """
    return list(EDITABLE_SCHEMA.get(tool_name, []))
