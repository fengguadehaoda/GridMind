/**
 * GridMind v1.5.1 T02 ChatView 集成测试
 *
 * 覆盖场景（≥3）：
 *   1. ChatView 导入 ReasoningControlBar + useReasoningStore + subscribeSessionEvents
 *   2. 模板挂载 <ReasoningControlBar v-if="reasoning.isActive" />
 *   3. SSE 事件 handler 映射：step_completed / step_failed / reasoning_* 全部覆盖
 *   4. SSE lifecycle：watch(sessionId) + onUnmounted abort
 *   5. 不破坏 v1.5.0 现有 imports（useChatStore / useDisplay / 5 个旧组件）
 *
 * 运行：node tests/test_chatview_integration.mjs
 *
 * 策略：静态源码分析（与 test_reasoning_control_bar.mjs 一致）。
 */
import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { resolve, join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)
const ROOT = resolve(__dirname, '..')
const CHATVIEW_SRC = await readFile(
  join(ROOT, 'src/components/ChatView.vue'),
  'utf-8',
)

let pass = 0
let fail = 0
const failures = []

function wrap(name, fn) {
  return test(name, async () => {
    try {
      await fn()
      pass++
    } catch (e) {
      fail++
      failures.push({ name, error: e })
      throw e
    }
  })
}

describe('ChatView · T02 SSE 集成', () => {
  wrap('1) 导入 F1 所需依赖（useReasoningStore + useSseStream + ReasoningControlBar）', () => {
    assert.match(
      CHATVIEW_SRC,
      /import\s*\{[^}]*useReasoningStore[^}]*\}\s*from\s*['"][^'"]*stores\/reasoning['"]/,
      '应导入 useReasoningStore from @/stores/reasoning',
    )
    // R-X6 修复后：SSE 订阅改用 useSseStream composable（带自动重连 + 心跳超时，架构 §6.3）
    assert.match(
      CHATVIEW_SRC,
      /import\s*\{[^}]*useSseStream[^}]*\}\s*from\s*['"][^'"]*composables\/useSseStream['"]/,
      'R-X6：应从 composables/useSseStream 导入 useSseStream（替代旧的 subscribeSessionEvents）',
    )
    assert.match(
      CHATVIEW_SRC,
      /import\s+ReasoningControlBar\s+from\s+['"][^'"]*reasoning\/ReasoningControlBar\.vue['"]/,
      '应导入 ReasoningControlBar',
    )
    assert.match(
      CHATVIEW_SRC,
      /import\s*\{[^}]*\bElMessage\b[^}]*\}\s*from\s*['"]element-plus['"]/,
      '应从 element-plus 导入 ElMessage（用于 SSE 提示）',
    )
    // R-X6 安全断言：ChatView 不再 import 废弃的 subscribeSessionEvents
    assert.doesNotMatch(
      CHATVIEW_SRC,
      /import\s*\{[^}]*\bsubscribeSessionEvents\b[^}]*\}\s*from\s*['"][^'"]*api\/chat['"]/,
      'R-X6：ChatView 不应再 import subscribeSessionEvents（已用 useSseStream 替代）',
    )
  })

  wrap('2) 模板挂载 <ReasoningControlBar v-if="reasoning.isActive" />（架构 §3.1.1）', () => {
    assert.match(
      CHATVIEW_SRC,
      /<ReasoningControlBar\s+v-if="reasoning\.isActive"\s*\/>/,
      '模板应有 <ReasoningControlBar v-if="reasoning.isActive" />',
    )
    // 位置：应在 message-list 之前（顶部控制栏）
    const barIdx = CHATVIEW_SRC.indexOf('<ReasoningControlBar')
    const listIdx = CHATVIEW_SRC.indexOf('class="message-list"')
    assert.ok(barIdx > 0, '应存在 ReasoningControlBar 节点')
    assert.ok(listIdx > 0, '应存在 message-list 节点')
    assert.ok(barIdx < listIdx, 'ReasoningControlBar 应在 message-list 之前（顶部控制栏）')
  })

  wrap('3) SSE 事件 handler 映射 11 种 type 全部覆盖（架构 §3.5）', () => {
    // 必须覆盖的 F1 事件
    const f1Events = [
      'reasoning_paused',
      'reasoning_resumed',
      'reasoning_completed',
      'reasoning_error',
      'step_completed',
      'step_failed',
      'step_replaced',
    ]
    for (const ev of f1Events) {
      assert.match(
        CHATVIEW_SRC,
        new RegExp(`case\\s+['"]${ev}['"]\\s*:`),
        `SSE handler 应包含 case '${ev}':`,
      )
    }
    // step_started 至少应有 case（即使 body 是占位 break）
    assert.match(
      CHATVIEW_SRC,
      /case\s+['"]step_started['"]\s*:/,
      "SSE handler 应包含 case 'step_started':（T03 实现具体逻辑）",
    )
  })

  wrap('4) F1 关键事件 → reasoning store actions 映射正确', () => {
    // reasoning_paused → onSsePaused + ElMessage.info
    assert.match(
      CHATVIEW_SRC,
      /case\s+['"]reasoning_paused['"]\s*:[\s\S]{0,200}reasoning\.onSsePaused\(\)/,
      "reasoning_paused → reasoning.onSsePaused()",
    )
    // reasoning_resumed → onSseResumed
    assert.match(
      CHATVIEW_SRC,
      /case\s+['"]reasoning_resumed['"]\s*:[\s\S]{0,200}reasoning\.onSseResumed\(\)/,
      "reasoning_resumed → reasoning.onSseResumed()",
    )
    // reasoning_completed → markCompleted
    assert.match(
      CHATVIEW_SRC,
      /case\s+['"]reasoning_completed['"]\s*:[\s\S]{0,200}reasoning\.markCompleted\(\)/,
      "reasoning_completed → reasoning.markCompleted()",
    )
    // reasoning_error → markError + ElMessage.error（R-X3 patch：均用通用文案，不拼 event.error）
    const reasoningErrorBlock = CHATVIEW_SRC.match(
      /case\s+['"]reasoning_error['"]\s*:[\s\S]{0,500}break/,
    )
    assert.ok(reasoningErrorBlock, 'reasoning_error case block 存在')
    const block = reasoningErrorBlock[0]
    assert.doesNotMatch(
      block,
      /markError\s*\(\s*event\.error/,
      'R-X3 patch：markError 不应再直接拼装 event.error',
    )
    assert.match(
      block,
      /markError\s*\(\s*['"][^'"]+['"]\s*\)/,
      'R-X3 patch：markError 应传字符串字面量（通用文案）',
    )
    assert.doesNotMatch(
      block,
      /ElMessage\.error\s*\(\s*[`][^`]*\$\{event\.error/,
      'R-X3 patch：ElMessage.error 不应再含 ${event.error} 模板',
    )
    assert.match(
      block,
      /console\.error\s*\(\s*['"][^'"]*['"]\s*,\s*event\s*\)/,
      'R-X3 patch：reasoning_error 分支应 console.error(...带 event 服务侧留痕)',
    )
    // step_completed → completeStep(step_id)
    assert.match(
      CHATVIEW_SRC,
      /case\s+['"]step_completed['"]\s*:[\s\S]{0,200}reasoning\.completeStep\(/,
      "step_completed → reasoning.completeStep(...)",
    )
  })

  wrap('5) SSE lifecycle：watch(sessionId) + onUnmounted disconnect + useSseStream 自动重连（防泄漏）', () => {
    // watch sessionId（架构 §6.3：响应式订阅 sessionId 切换 stream）
    assert.match(
      CHATVIEW_SRC,
      /watch\(\s*\(\)\s*=>\s*reasoning\.sessionId/,
      '应 watch reasoning.sessionId',
    )
    // R-X6 重构后：onUnmounted 调 disposeSse → sseStream.disconnect()（替代旧 sseController.abort）
    assert.match(
      CHATVIEW_SRC,
      /onUnmounted\([\s\S]{0,200}disposeSse\b/,
      'R-X6：onUnmounted 应绑定 disposeSse()（替代旧 sseController.abort）',
    )
    assert.match(
      CHATVIEW_SRC,
      /sseStream\.disconnect\s*\(\s*\)/,
      'R-X6：disposeSse 应调 sseStream.disconnect()',
    )
    // R-X6 修复后：useSseStream 替代 subscribeSessionEvents（带 retryDelaysMs 退避重连 + 30s 心跳超时）
    assert.match(
      CHATVIEW_SRC,
      /useSseStream[\s\S]{0,1000}retryDelaysMs\s*:\s*\[\s*1000\s*,\s*5000\s*,\s*15000\s*,\s*30000\s*\]/,
      'R-X6：应使用 useSseStream 并配置 retryDelaysMs: [1000, 5000, 15000, 30000] 自动重连',
    )
    assert.match(
      CHATVIEW_SRC,
      /useSseStream[\s\S]{0,1000}heartbeatTimeoutMs\s*:\s*30000/,
      'R-X6：应使用 useSseStream 并配置 heartbeatTimeoutMs: 30000（30s 心跳超时）',
    )
    // R-X6 安全断言：ChatView 不再调废弃的 subscribeSessionEvents
    assert.doesNotMatch(
      CHATVIEW_SRC,
      /subscribeSessionEvents\(\s*sessionId\s*,\s*handleSseEvent/,
      'R-X6：ChatView 不应再调 subscribeSessionEvents（已用 useSseStream 替代）',
    )
  })
})

describe('ChatView · 不破坏 v1.5.0 现有功能', () => {
  wrap('6) v1.5.0 关键 import 全部保留（useChatStore + useDisplay + 5 个旧组件）', () => {
    // 现有 store / composable
    assert.match(CHATVIEW_SRC, /useChatStore/, '应保留 useChatStore')
    assert.match(CHATVIEW_SRC, /useDisplay/, '应保留 useDisplay')
    // 5 个旧组件
    assert.match(CHATVIEW_SRC, /import\s+MessageBubble/, '应保留 MessageBubble')
    assert.match(CHATVIEW_SRC, /import\s+DemoShortcuts/, '应保留 DemoShortcuts')
    assert.match(CHATVIEW_SRC, /import\s+ModelSwitcher/, '应保留 ModelSwitcher')
    assert.match(CHATVIEW_SRC, /import\s+TechBackground/, '应保留 TechBackground')
    assert.match(CHATVIEW_SRC, /import\s+ScanlineOverlay/, '应保留 ScanlineOverlay')
  })

  wrap('7) v1.5.0 关键交互函数保留（onSend / onShortcutSend / scrollToBottom）', () => {
    assert.match(CHATVIEW_SRC, /async\s+function\s+onSend\s*\(\s*\)/, 'onSend 应保留')
    assert.match(CHATVIEW_SRC, /function\s+onShortcutSend\s*\(/, 'onShortcutSend 应保留')
    assert.match(CHATVIEW_SRC, /function\s+scrollToBottom\s*\(\s*\)/, 'scrollToBottom 应保留')
    // sendMessage 调用保留
    assert.match(CHATVIEW_SRC, /await\s+store\.sendMessage\s*\(\s*text\s*\)/, 'onSend 应调 store.sendMessage(text)')
  })

  wrap('8) 模板结构保留（welcome / message-list / input-area 三层）', () => {
    assert.match(CHATVIEW_SRC, /class="welcome"/, 'welcome 块应保留')
    assert.match(CHATVIEW_SRC, /class="message-list"/, 'message-list 应保留')
    assert.match(CHATVIEW_SRC, /class="input-area"/, 'input-area 应保留')
    assert.match(CHATVIEW_SRC, /data-tour="chat-input"/, 'onboarding 锚点 chat-input 应保留')
    assert.match(CHATVIEW_SRC, /data-tour="chat-history"/, 'onboarding 锚点 chat-history 应保留')
  })
})

// ─── 总结 ───
process.on('exit', () => {
  process.stderr.write(`\n\x1b[1m── T02 chatview integration test summary ──\x1b[0m\n`)
  process.stderr.write(`  Pass: \x1b[32m${pass}\x1b[0m\n  Fail: \x1b[31m${fail}\x1b[0m\n`)
  if (failures.length) {
    process.stderr.write(`\n  Failures:\n`)
    for (const f of failures) {
      process.stderr.write(`    - ${f.name}\n      ${f.error?.message ?? f.error}\n`)
    }
  }
})
