# HITL Edit & Continue 前端手动测试 Checklist

> 本清单为 T04 验收的人工 E2E 步骤，配合 `tests/test_hitl_edit.py` 自动测试覆盖。
> 实际工程化建议：未来转为 Playwright 自动测试。

## 前置条件

1. 后端启动（端口 9900）：
   ```bash
   cd F:/GridOpsAgent
   python scripts/start_all.py  # 或 python -m api.main
   ```
2. 前端启动（端口 5173，Vite HMR 已开）：
   ```bash
   cd F:/GridOpsAgent/web
   npm run dev
   ```
3. 浏览器打开 `http://localhost:5173/`，确认服务已连接。

---

## 场景 1：纯 Approval（不修改字段直接批准）

**目的**：AC-5 验证，确认 Approval 模式未被破坏。

| # | 操作 | 预期 |
|---|---|---|
| 1.1 | 在输入框输入"建议对#1主变压器进行停机检修" | — |
| 1.2 | 点"发送" | 流式出现"⚠️ 检测到高危操作请求…"warning 文本 |
| 1.3 | 等待 ~3 秒 | 弹出 HITL 编辑对话框（720px 宽） |
| 1.4 | 不修改任何字段 | 表单默认值即为 Agent 建议值 |
| 1.5 | 点"仅批准"按钮 | 按钮出现 spinner，对话框关闭 |
| 1.6 | 查看消息列表 | 新增系统消息 "✅ 已批准高危操作" + assistant 消息含"已批准执行" |
| 1.7 | 浏览器开发者工具 → Network → `/interrupt/{tid}/decision` | status=200，body.decision="approve" |

**预期后端日志**（`logs/api.log`）：

```
HITL audit log written: rowid=?, decision=approve
```

**审计验证**：

```sql
sqlite3 F:/GridOpsAgent/data/gridmind.db
SELECT decision, edited_args FROM hitl_audit_log WHERE decision='approve';
-- 期望：edited_args 为 NULL
```

---

## 场景 2：Edit & Continue 修改后批准

**目的**：AC-1, AC-3, AC-4, AC-9 验证。

| # | 操作 | 预期 |
|---|---|---|
| 2.1 | 输入"请给 TR-001 派发工单" → 发送 | 流式输出 → HITL 弹窗弹出 |
| 2.2 | 查看弹窗 | 弹窗宽度 720px（比老 480px 宽），显示"故障描述"+"优先级"+"修改原因" 三个区块 |
| 2.3 | 清空"故障描述"textarea，点"修改后批准" | 字段红框，错误提示"故障描述不能为空"，按钮置灰（disabled） |
| 2.4 | 重新填入描述但粘贴 600 字 | textarea 仅接收 500 字，显示"500/500"红色计数，按钮置灰 |
| 2.5 | 描述填回 100 字合法内容 | 描述红框消失，按钮恢复可用 |
| 2.6 | "修改原因"留空 | 修改原因红框，"修改原因不能为空"，按钮置灰 |
| 2.7 | 修改原因填入"保电时段调整" | 全部校验通过，按钮可用 |
| 2.8 | 点"修改后批准" | spinner → 关闭弹窗 |
| 2.9 | 消息列表出现 | 系统消息 "✏️ 已按编辑后内容批准（修改原因：保电时段调整）"，assistant 消息含"已按编辑后内容执行" |

**审计验证**：

```sql
SELECT decision, edited_args, edit_reason FROM hitl_audit_log WHERE decision='edit_approve' ORDER BY id DESC LIMIT 1;
-- 期望：edited_args 含 priority="medium"（若选择降级），edit_reason="保电时段调整"
```

---

## 场景 3：拒绝（reject）

**目的**：AC-6 验证，确认拒绝路径有效。

| # | 操作 | 预期 |
|---|---|---|
| 3.1 | 触发高危操作弹窗（同上） | 弹窗出现 |
| 3.2 | 在"拒绝原因（仅点拒绝时填）"textarea 填入"保电时段不允许操作" | — |
| 3.3 | 点"拒绝" | spinner → 弹窗关闭 |
| 3.4 | 消息列表 | "❌ 已拒绝高危操作（原因：保电时段不允许操作）"，assistant 消息含"已拒绝执行" |

**审计**：

```sql
SELECT decision, reason FROM hitl_audit_log WHERE decision='reject';
-- reason="保电时段不允许操作"
```

---

## 场景 4：设备 ID 安全校验

**目的**：验证 `device_id` 在前端**不能**被编辑（后端黑名单也兜底）。

| # | 操作 | 预期 |
|---|---|---|
| 4.1 | 触发 dispatch_work_order 弹窗 | 弹窗显示"目标设备：TR-001"只读卡片 |
| 4.2 | 尝试编辑卡片（卡片无 input） | 卡片为只读，无可编辑控件 |

**单元测试覆盖**（后端 Pydantic 黑名单）：

```python
from api.schemas.hitl_edit import EditInterruptRequest, EditDecisionEnum
try:
    EditInterruptRequest(
        decision=EditDecisionEnum.edit_approve,
        edited_args={"device_id": "TR-002", "priority": "low"},
        edit_reason="bad",
    )
except Exception as e:
    assert "device_id" in str(e)
# ✅ device_id 黑名单拦截
```

> 直接绕过前端通过 API 调用也无效——后端 Pydantic 黑名单是兜底。

---

## 场景 5：暗/亮主题切换

**目的**：AC-10 验证。

| # | 操作 | 预期 |
|---|---|---|
| 5.1 | 默认启动在暗主题（推测） | HitlEditDialog 弹窗背景为深色 |
| 5.2 | 顶栏切主题 → 亮主题 | 弹窗关闭再触发一次，看弹窗颜色 |
| 5.3 | 切回暗主题 | 弹窗切回深色，无残留 |

**审计**：无颜色字面量泄漏
```bash
grep -rEn "#[0-9a-fA-F]{6}" F:/GridOpsAgent/web/src/components/HitlEditDialog.vue
# 期望：无任何输出
```

---

## 场景 6：Safety 重检失败（演示 fail-closed）

**目的**：AC-2 验证。

| # | 操作 | 预期 |
|---|---|---|
| 6.1 | 输入"建议对#1主变压器进行停机检修" | 触发 suggest_shutdown HITL |
| 6.2 | 在"停运原因"textarea 填入"测试性短时频繁停运 5 分钟" | 该关键词触发 manditory 安规匹配 |
| 6.3 | "修改原因"填入任意 | 修改后批准按钮可用 |
| 6.4 | 点"修改后批准" | 后端 safety 重检 → 检测到 mandatory → 弹窗**不关闭**，顶部红色横幅"安全重检未通过：匹配 X 条安规条款；发现 mandatory 级别冲突" |
| 6.5 | 验证 audit | 审计写入 decision='edit_approve' 但 safety_recheck_result.passed=false |

---

## 场景 7：审计日志 API

**目的**：验证 `/audit/hitl/{thread_id}` 可追溯。

| # | 操作 | 预期 |
|---|---|---|
| 7.1 | 触发任一 HITL 决策（approve/reject/edit_approve） | 后端写 hitl_audit_log |
| 7.2 | `curl -s http://localhost:9900/api/audit/hitl/<thread_id>` | 返回该 thread 的所有审计行，decision / edited_args / edit_reason 字段齐全 |
| 7.3 | `curl -s http://localhost:9900/api/audit/hitl?decision=edit_approve` | 列出最近 N 条 edit_approve |

---

## 验收矩阵

| AC | 场景 | 自动化测试 | 手动 checklist |
|---|---|---|---|
| AC-1 编辑生效 | 场景 2 | `test_edit_continue` | ✓ |
| AC-2 安全失败 | 场景 6 | `test_edit_safety_fail` | ✓ |
| AC-3 必填校验 | 场景 2.3 | 前端 el-form | ✓ |
| AC-4 长度超限 | 场景 2.4 | 前端 maxlength | ✓ |
| AC-5 仅批准 | 场景 1 | `test_pure_approval` | ✓ |
| AC-6 拒绝 | 场景 3 | `test_reject` | ✓ |
| AC-8 审计完整 | 场景 7 | `test_audit_query_by_decision` | ✓ |
| AC-9 修改原因必填 | 场景 2.6 | 前端 form rules | ✓ |
| AC-10 暗/亮主题 | 场景 5 | grep 颜色审计 | ✓ |

---

## 已知遗留（与本期范围无关）

- Q6（网络中断 localStorage 暂存）→ P2 再说（PRD 范围外）
- Conflict detection（AC-7）→ P1 再说
- 多工具批量编辑 → 不支持（按 tool 顺序逐个弹窗）

---

## 故障排查

| 现象 | 排查 |
|---|---|
| 弹窗未弹出 | 检查 devtools console，看 SSE 流式消息 `interrupt_required` |
| 按钮始终 disabled | 检查 `formData` 默认值、`required` 字段、`max_length`、`edit_reason` 非空 |
| safety 红色横幅未出现 | 检查 `safety_rules` 表是否 seed，关键词是否匹配 |
| 审计未写入 | 检查 `mcp_tools/db/database.py::init_db()` 触发；SQLite 文件路径 |
| 兼容壳 /approve 仍工作 | 用老 HitlDialog 路径；`/interrupt/{tid}/approve` 老端点保留 |
