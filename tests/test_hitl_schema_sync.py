"""R-1e：HITL 可编辑字段 schema 前后端一致性 CI 测试。

`api/services/hitl_editable_schemas.py`（后端 SSOT）与镜像
`web/src/api/hitlSchemas.ts`（前端）注释均声明「CI 校验字段名一致」，但此前
**没有实际断言**。本测试补上这道护栏：

- 后端：直接 ``import`` ``EDITABLE_SCHEMA``（Pydantic ``EditableField`` 实例），
  取 ``tool_name -> [field.key]``；
- 前端：从 TS 源文件用正则 + 括号配平提取 ``EDITABLE_SCHEMA`` 字面量，
  解析顶层 schema 键与各 schema 内 ``key: '...'`` 字段名（不改动源文件，
  只做静态解析，避免依赖 ts-node/前端构建产物）。

断言两侧**schema 键集合**一致、**每个 schema 的字段名集合**一致
（含 ``dispatch_work_order`` / ``suggest_shutdown`` / ``diagnosis_review`` 全部键）。

运行：
    python -m pytest tests/test_hitl_schema_sync.py -q
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TS_SCHEMA_PATH = PROJECT_ROOT / "web" / "src" / "api" / "hitlSchemas.ts"

#: 前端字面量起始标记（``export const EDITABLE_SCHEMA: Record<string, EditableField[]> = {``）
_TS_MARKER = "EDITABLE_SCHEMA: Record<string, EditableField[]> = {"

#: 顶层 schema 键：2 空格缩进 + 标识符 + ``: [``（仅命中 schema 级，不命中字段级）
_TS_TOP_KEY_RE = re.compile(r"^\s{2}([A-Za-z_]\w*):\s*\[\s*$")
#: 字段键：任意缩进 + ``key: 'xxx'``
_TS_FIELD_KEY_RE = re.compile(r"\bkey:\s*'([^']+)'")


def _extract_frontend_schema(ts_text: str) -> dict[str, list[str]]:
    """从 TS 源文本提取 ``EDITABLE_SCHEMA`` 字面量（schema key -> field keys）。

    Args:
        ts_text: ``hitlSchemas.ts`` 全文。

    Returns:
        ``{tool_name: [field.key, ...]}``；解析失败（字面量缺失）抛 ``ValueError``。
    """
    start = ts_text.find(_TS_MARKER)
    if start < 0:
        raise ValueError(f"前端 schema 中找不到标记 {_TS_MARKER!r}")
    brace_start = ts_text.index("{", start)

    # 括号配平：找到与 brace_start 匹配的右花括号（字面量结尾）
    depth = 0
    end = brace_start
    for i in range(brace_start, len(ts_text)):
        ch = ts_text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if depth != 0:
        raise ValueError("前端 schema 字面量括号未配平")
    block = ts_text[brace_start + 1 : end]

    result: dict[str, list[str]] = {}
    current: str | None = None
    for line in block.splitlines():
        top = _TS_TOP_KEY_RE.match(line)
        if top:
            current = top.group(1)
            result[current] = []
            continue
        if current is None:
            continue
        fm = _TS_FIELD_KEY_RE.search(line)
        if fm:
            result[current].append(fm.group(1))
    return result


def _backend_schema() -> dict[str, list[str]]:
    """从后端 Python 模块读取 schema（SSOT）。"""
    from api.services.hitl_editable_schemas import EDITABLE_SCHEMA

    return {tool: [f.key for f in fields] for tool, fields in EDITABLE_SCHEMA.items()}


def test_hitl_schema_keys_match() -> None:
    """schema 顶层键集合一致（dispatch_work_order / suggest_shutdown / diagnosis_review 全含）。"""
    backend = _backend_schema()
    assert TS_SCHEMA_PATH.is_file(), f"前端 schema 文件缺失: {TS_SCHEMA_PATH}"
    frontend = _extract_frontend_schema(TS_SCHEMA_PATH.read_text(encoding="utf-8"))

    # 明确覆盖全部 schema 键（回归：新增 schema 键时必须同步两侧）
    assert set(backend) == {"dispatch_work_order", "suggest_shutdown", "diagnosis_review"}, (
        f"后端 schema 键异常: {sorted(backend)}"
    )

    assert set(backend) == set(frontend), (
        "前后端 schema 键不一致\n"
        f"  仅后端: {sorted(set(backend) - set(frontend))}\n"
        f"  仅前端: {sorted(set(frontend) - set(backend))}"
    )


def test_hitl_schema_field_names_match() -> None:
    """每个 schema 键下字段名集合一致（顺序不敏感）。"""
    backend = _backend_schema()
    frontend = _extract_frontend_schema(TS_SCHEMA_PATH.read_text(encoding="utf-8"))

    for tool in sorted(backend):
        be_fields = set(backend[tool])
        fe_fields = set(frontend.get(tool, []))
        assert be_fields == fe_fields, (
            f"schema '{tool}' 字段名不一致\n"
            f"  仅后端: {sorted(be_fields - fe_fields)}\n"
            f"  仅前端: {sorted(fe_fields - be_fields)}"
        )
        assert be_fields, f"schema '{tool}' 不应为空（至少一个可编辑字段）"


def test_hitl_schema_ts_parser_detects_known_keys() -> None:
    """解析器自检：前端字面量应能解析出全部三个 schema 键。"""
    text = TS_SCHEMA_PATH.read_text(encoding="utf-8")
    parsed = _extract_frontend_schema(text)
    assert set(parsed) == {"dispatch_work_order", "suggest_shutdown", "diagnosis_review"}, (
        f"前端解析出的 schema 键: {sorted(parsed)}"
    )
