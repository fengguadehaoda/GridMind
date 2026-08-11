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
    # 融合层 HITL（diagnosis_review / FUSION_REVIEW_NODE）：
    # 与前端 web/src/api/hitlSchemas.ts 镜像——标准模式在真实 LLM / mock 推理下
    # 可能触发融合层 HITL（机理校验 critical 失败 / 规则护栏强制 HITL /
    # LLM↔机理冲突），interrupt_tool = "diagnosis_review"。
    # process_edit_decision 对 diagnosis_review 走 _decision_without_resume
    # （无 pending_tool_plan），edited_args 仅写入审计；本 schema 保持前端 /
    # 后端字段名一致（CI 校验断言）。
    "diagnosis_review": [
        EditableField(
            key="action",
            type="textarea",
            label="处置建议",
            required=True,
            max_length=500,
            placeholder="请填写确认后的处置措施 / 后续动作（如：立即隔离故障点、启动备自投、通知现场核查…）",
            help_text="必填，≤ 500 字",
        ),
        EditableField(
            key="severity",
            type="select",
            label="确认风险等级",
            required=True,
            options=["high", "medium", "low"],
            help_text="高危时段请选择 medium 及以下",
        ),
        # device_id 不可编辑（LOCKED_FIELDS 黑名单拦截）
    ],
}


def get_editable_fields(tool_name: str) -> list[EditableField]:
    """根据工具名获取可编辑字段定义。

    未知工具返回空列表（视作无可编辑字段，仅支持 approve/reject）。
    """
    return list(EDITABLE_SCHEMA.get(tool_name, []))
