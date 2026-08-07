# 寇豆码 · T07 完成报告

主理人：T07（QA Bug 修复）已 100% 完成。R-X5 + R-X6 必修，文件改动 ≤ 5 个，自带测试 12/12 PASS。

## 1. 修改/新增的 5 个文件（绝对路径）

| # | 类型 | 路径 |
|---|------|------|
| 1 | ✏️ 修改 | `F:/GridMind · 灵枢电网/web/src/components/reasoning/ReasoningControlBar.vue` |
| 2 | ✏️ 修改 | `F:/GridMind · 灵枢电网/web/src/components/reasoning/StepInlineEditor.vue` |
| 3 | ✏️ 修改 | `F:/GridMind · 灵枢电网/web/src/components/reasoning/StepEditButton.vue` |
| 4 | ✏️ 修改 | `F:/GridMind · 灵枢电网/web/src/components/ChatView.vue` |
| 5 | 🆕 新增 | `F:/GridMind · 灵枢电网/web/tests/test_safety_patch.mjs` |

## 2. R-X5 修复 · 修复前后对比（5 处：3+1+1+1）

### 2.1 ReasoningControlBar · `handlePause`（3 处之一）

```typescript
// 之前（暴露内部异常）：
} catch (e) {
  const msg = e instanceof Error ? e.message : String(e)
  ElMessage.error(`暂停失败: ${msg}`)
}

// 之后（用户友好 + dev 留 traceback）：
} catch (e) {
  console.error('[ReasoningControlBar.pause] 操作失败：', e)
  ElMessage.error('暂停失败，请稍后重试')
}
```

### 2.2 ReasoningControlBar · `handleResume`

```typescript
// 之前：
} catch (e) {
  const msg = e instanceof Error ? e.message : String(e)
  ElMessage.error(`恢复失败: ${msg}`)
}
// 之后：
} catch (e) {
  console.error('[ReasoningControlBar.resume] 操作失败：', e)
  ElMessage.error('恢复失败，请稍后重试')
}
```

### 2.3 ReasoningControlBar · `handleAbort`（保留 cancel/close no-op）

```typescript
// 之前：
} catch (e) {
  if (e === 'cancel' || e === 'close') return
  const msg = e instanceof Error ? e.message : String(e)
  ElMessage.error(`中止失败: ${msg}`)
}
// 之后：
} catch (e) {
  if (e === 'cancel' || e === 'close') return
  console.error('[ReasoningControlBar.abort] 操作失败：', e)
  ElMessage.error('中止失败，请稍后重试')
}
```

### 2.4 StepInlineEditor · `handleRerun`

```typescript
// 之前：
} catch (err) {
  const msg = err instanceof Error ? err.message : String(err)
  ElMessage.error(`重跑失败：${msg}`)
}
// 之后：
} catch (err) {
  console.error('[StepInlineEditor.rerun] 操作失败：', err)
  ElMessage.error('重跑失败，请稍后重试')
}
```

### 2.5 StepEditButton · `handleEdit`（保留业务错误码分支）

```typescript
// 之前（兜底分支暴露异常）：
} catch (err) {
  const msg = err instanceof Error ? err.message : String(err)
  if (msg === 'REASONING_NOT_EDITABLE_STATE') { ElMessage.warning('...') }
  else if (msg === 'STEP_NOT_EDITABLE') { ElMessage.warning('...') }
  else { ElMessage.error(`进入编辑态失败：${msg}`) }
}
// 之后（兜底分支通用 message）：
} catch (err) {
  if (err instanceof Error && err.message === 'REASONING_NOT_EDITABLE_STATE') {
    ElMessage.warning('当前推理状态不可编辑（仅 running / paused 允许）')
  } else if (err instanceof Error && err.message === 'STEP_NOT_EDITABLE') {
    ElMessage.warning('该步骤不可编辑')
  } else {
    console.error('[StepEditButton.beginEdit] 操作失败：', err)
    ElMessage.error('编辑失败，请稍后重试')
  }
}
```

## 3. R-X6 修复 · ChatView SSE 订阅重构

### 之前（subscribeSessionEvents，无重连）

```typescript
import { subscribeSessionEvents } from '../api/chat'
let sseController: AbortController | null = null

function setupSse(sessionId: string): void {
  if (sseController) { sseController.abort(); sseController = null }
  if (!sessionId) return
  sseController = subscribeSessionEvents(
    sessionId,
    handleSseEvent,
    (err) => { console.warn('[SSE] error:', err) },  // 无重连
  )
}

watch(() => reasoning.sessionId,
  (newId) => setupSse(newId ?? ''),
  { immediate: true })

onUnmounted(() => { sseController?.abort() })
```

### 之后（useSseStream composable，带自动重连）

```typescript
import { useSseStream } from '../composables/useSseStream'
import type { SseStreamHandle } from '../composables/useSseStream'

let sseStream: SseStreamHandle<SseEvent> | null = null

function disposeSse(): void {
  if (sseStream) { sseStream.disconnect(); sseStream = null }
}

function attachSse(sessionId: string): void {
  disposeSse()
  const base = resolveApiBase()  // VITE_API_BASE → localhost:9900 → /api
  const url = `${base}/sessions/${encodeURIComponent(sessionId)}/events`
  sseStream = useSseStream<SseEvent>({
    url,
    retryDelaysMs: [1000, 5000, 15000, 30000],  // 退避序列（架构 §6.3）
    heartbeatTimeoutMs: 30000,                    // 30s 心跳超时
    onEvent: handleSseEvent,
    onError: (err) => {
      // R-X5: 通用 message（不暴露 err.message）
      console.warn('[SSE] 连接异常：', err)
      ElMessage.warning('实时连接中断，正在自动重连...')
    },
  })
}

watch(() => reasoning.sessionId,
  (newId) => { if (newId) attachSse(newId); else disposeSse() },
  { immediate: true })

onUnmounted(disposeSse)
```

## 4. 单元测试结果

### T07 自带测试（`test_safety_patch.mjs`）：**12/12 PASS**（≥5 要求）

| 测试组 | 场景数 | 结果 |
|--------|--------|------|
| R-X5 静态分析（S1-S6）| 6 | 6 PASS |
| R-X5 全文件回归扫描（S7）| 1 | 1 PASS |
| R-X6 SSE 重连静态（S8 + S8.1 + S9）| 3 | 3 PASS |
| R-X6 SSE 重连运行时（S10 + S10.1）| 2 | 2 PASS |

### 合并运行测试：216 PASS（满足 ≥213 要求）

| Test File | Pre-T07 | Post-T07 | 备注 |
|-----------|---------|----------|------|
| `test_safety_patch.mjs` (新) | — | **+12 PASS** | T07 自带 |
| `test_sse_stream.mjs` | 6 | 6 PASS | 无变化 |
| `test_step_inline_editor.mjs` | 18 | 18 PASS | 无变化 |
| `test_reasoning_control_bar.mjs` | 17 | 15 PASS + **2 FAIL** ⚠️ | 复用旧 buggy 模式断言 |
| `test_chatview_integration.mjs` | 8 | 6 PASS + **2 FAIL** ⚠️ | 复用旧 buggy 模式断言 |
| `test_hitl_badge.mjs` | 18 | 18 PASS | 无变化 |
| `test_focus_trap.mjs` | 6 | 6 PASS | 无变化 |
| `test_hitl_dialog.mjs` | 20 | 20 PASS | 无变化 |
| `test_reasoning_store.mjs` | 15 | 15 PASS | 无变化 |
| 总计（已统计 + 已知其他） | 208 | **216 PASS** | ✅ ≥213 达标 |

⚠️ **T02 测试的 4 项 FAIL 属"测试-断言了-buggy 模式"的预期 fallout**：
- `test_reasoning_control_bar` 测试 11/12：正则匹配 `\`暂停失败: ${msg}\``（旧 buggy 模板字面量）。我的新代码用 `'暂停失败，请稍后重试'`（字符串字面量），所以正则匹配失败。**这是 R-X5 修复成功的副作用**。
- `test_chatview_integration` 测试 1/5：要求 import + 调用 `subscribeSessionEvents(...)`。R-X6 修复后改用 `useSseStream`。**这是 R-X6 修复成功的副作用**。
- **建议**：T08（或下一轮 QA 复测前）将这 2 个 T02 测试文件的 4 项 regex 模式更新为 `ElMessage\.error\(['"]暂停失败，请稍后重试['"]\)` 等新模式（非本 T07 范围，超出 ≤5 文件限制）。

## 5. 关键验证项

✅ **R-X5**：所有 `ElMessage.error` 不含 `(e as Error).message` / `String(err)` / 路径 / token
- S7 全文件扫描显示 **0 处泄漏**（命中 4 处 catch 块 ElMessage.error 调用，全部通过 containsLeakage 校验）

✅ **R-X6**：ChatView SSE 断线自动重连（退避 1s/5s/15s/30s）
- 静态：ChatView 含 `retryDelaysMs: [1000, 5000, 15000, 30000]`
- 静态：ChatView 含 `heartbeatTimeoutMs: 30000`
- 运行时：mock fetch 连续失败 → `retryAttempt` 自增 → `onError` 触发（与 T01 useSseStream 测试同模式）

✅ **R-X6**：30s 心跳超时触发主动重连
- `DEFAULT_HEARTBEAT_TIMEOUT_MS === 30000`（T01 实现，ChatView 复用）

✅ **`cd web && npm run build`**：成功（vue-tsc --noEmit + vite build 都 exit 0）

✅ **T07 自带 ≥5 PASS**：实际 12 PASS（远超要求）

✅ **合并 ≥213 PASS**：实际 216 PASS（满足）

## 6. v1.5.1 前端实施最终状态总结

GridMind v1.5.1 前端 (F1 推理暂停/恢复/中止 + F2 编辑重跑 + F3 HITL 待审计 + F4 HITL 三按钮决策 + SSE 自动重连 + 7 项基础设施) 已交付完整。前端 T01-T07 共 7 个任务闭环：T01 建好 `useSseStream` composable（fetch + ReadableStream + JWT 注入 + 退避重连 + 30s 心跳），T02 集成 F1 推理控制栏 + SSE 事件映射（11 种 SSE type），T03 实现 F2 步骤内联编辑器（焦点 trap + 4000 字上限 + rerun），T04 完成 F3 审计 store 与待审计计数，T05 完成 F4 HITL 三按钮决策弹窗（sticky 前置 + focus trap），T06 铺通 v1.5.1 的 chat API（含 `subscribeSessionEvents`）。T07（本任务）修复 QA 验收发现的 2 项 P1 安全/可靠性 bug——R-X5 修掉了 5 处把 `String(e)` / `e.message` 直接暴露给用户的内部异常泄漏，全部改为"dev console.error 留 traceback + 用户侧通用 message"模式（业务错误码如 `REASONING_NOT_EDITABLE_STATE`/`STEP_NOT_EDITABLE` 保留友好提示不混入）；R-X6 修掉了 ChatView SSE 断线无重连的隐患，从 T06 临时用的 `subscribeSessionEvents`（无重连）切到 T01 已实现完整重连退避 (1s/5s/15s/30s) + 30s 心跳超时的 `useSseStream` composable。整个 patch 仅触 4 个 `.vue` 源文件 + 1 个新测试文件（≤5 上限），新增 `test_safety_patch.mjs` 12/12 PASS 自带测试覆盖 R-X5 + R-X6 全部 10 场景（含静态源码分析 + esbuild bundle 运行时验证）。最终前端测试合并运行 216 PASS（208 旧 + 12 新 - 4 R-X5/R-X6 修复副作用 FAIL），vue-tsc + vite build 全绿。已知 actionable：T02 的 `test_reasoning_control_bar.mjs` (2 项) 与 `test_chatview_integration.mjs` (2 项) 因为原本断言的就是 R-X5/R-X6 的旧 buggy 模式（如 `\`暂停失败: ${msg}\`` 模板字面量），4 项 FAIL 属预期 fallback，下一轮（T08 或 QA 复测）应将这 4 项 regex 更新为新模式即恢复全 PASS。v1.5.1 前端可进入 QA 复测 + 端到端联调（决策 §7.6 B 方案：Playwright + 真后端 + route mock SSE）。

—— 寇豆码 (T07 工程师) · 完成
