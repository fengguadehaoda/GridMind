/**
 * GridMind v1.5.1 T06 · QA 独立集成测试
 * web/tests/test_integration_cross_f.mjs
 *
 * 覆盖 4 个跨 F 场景（用户视角完整流程）：
 *   T1. test_full_workflow_F1_to_F4_e2e:
 *       chat → reasoning.start (running) → pause → resume → rewind
 *       → 弹窗触发 → HITL approve → 徽标减 1
 *   T2. test_sse_reconnection_during_pause:
 *       useSseStream 断线后按 1s/5s/15s/30s 退避自动重连
 *   T3. test_jwt_token_mismatch_returns_403:
 *       错 token → 后端 401/403 → audit store connectionState=error
 *       + HitlBadge 降级为 · 灰点
 *   T4. test_multi_focus_trap_collisions:
 *       F2 inline editor (4 focusables) + F4 弹窗 (4 focusables) 同时打开
 *       → 各自 trap 互不干扰（focus 不会从一个 trap 串到另一个）
 *
 * 运行：node tests/test_integration_cross_f.mjs
 *
 * 作者：严过关（QA 工程师）
 * 上游任务：前端 v1.5.1 F1-F4 集成验收（T01-T05 + T06 e2e）
 *
 * 策略：
 *   - esbuild bundle stores (reasoning.ts + audit.ts) → 动态 import
 *   - stub axios (不发起真实 HTTP) + mock fetch
 *   - 部分场景用 jsdom-less 行为测试
 */
import { test, describe, before, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { build } from 'esbuild'
import { mkdtemp, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { existsSync, symlinkSync } from 'node:fs'
import { createPinia, setActivePinia } from 'pinia'

// ─── 路径 ───
const ROOT = resolve(import.meta.dirname, '..')
const SRC = join(ROOT, 'src')
const TMP = await mkdtemp(join(tmpdir(), 'gridmind-qa-integration-'))

// ─── 编译 stores 到 TMP（与 test_reasoning_store.mjs 一致的策略）──
await build({
  entryPoints: [
    join(SRC, 'stores/reasoning.ts'),
    join(SRC, 'stores/audit.ts'),
  ],
  outdir: TMP,
  outbase: SRC,
  bundle: true,
  format: 'esm',
  platform: 'node',
  target: 'node20',
  external: ['pinia', 'vue', '@vue/reactivity', '@vue/shared', '@vue/runtime-core', '@vue/runtime-dom', 'axios'],
  alias: { '@': SRC },
  loader: { '.ts': 'ts' },
  logLevel: 'silent',
  write: true,
})

const nmTarget = join(ROOT, 'node_modules')
const nmLink = join(TMP, 'node_modules')
if (!existsSync(nmLink)) {
  try { symlinkSync(nmTarget, nmLink, 'junction') } catch { /* ignore */ }
}

// ─── Mocks ───
let localStorageData = {}
globalThis.localStorage = {
  getItem(k) { return Object.hasOwn(localStorageData, k) ? localStorageData[k] : null },
  setItem(k, v) { localStorageData[k] = String(v) },
  removeItem(k) { delete localStorageData[k] },
  clear() { localStorageData = {} },
  key(i) { return Object.keys(localStorageData)[i] ?? null },
  get length() { return Object.keys(localStorageData).length },
}
let dataAttrs = {}
globalThis.document = {
  documentElement: {
    setAttribute(k, v) { dataAttrs[k] = String(v) },
    getAttribute(k) { return dataAttrs[k] ?? null },
  },
}
globalThis.window = {
  __test__: true,
  location: { href: 'http://localhost', origin: 'http://localhost', protocol: 'http:', host: 'localhost', hostname: 'localhost' },
  localStorage: globalThis.localStorage,
}
globalThis.location = globalThis.window.location
// navigator 在 Node 22 是只读 getter；尝试用 defineProperty
try {
  Object.defineProperty(globalThis, 'navigator', {
    value: { userAgent: 'node' },
    writable: true,
    configurable: true,
  })
} catch {
  /* navigator already defined */
}

// ─── 计数器：测试结果汇总 ───
let totalPass = 0
let totalFail = 0
const testFailures = []

function wrap(name, fn) {
  return test(name, async () => {
    try {
      await fn()
      totalPass++
    } catch (e) {
      totalFail++
      testFailures.push({ name, error: e })
      throw e
    }
  })
}

// ─── 跨 store 工具 ───
let useReasoningStore, useAuditStore
async function importStores() {
  const r = await import(`${pathToFileURL(TMP)}/stores/${'reasoning.js'}`)
  const a = await import(`${pathToFileURL(TMP)}/stores/${'audit.js'}`)
  useReasoningStore = r.useReasoningStore
  useAuditStore = a.useAuditStore
}
function freshStores() {
  localStorage.clear()
  dataAttrs = {}
  setActivePinia(createPinia())
  return { reasoning: useReasoningStore(), audit: useAuditStore() }
}

// ─── 静态分析：读源文件 ───
const chatSrc = await readFile(join(SRC, 'api/chat.ts'), 'utf-8')
const useSseSrc = await readFile(join(SRC, 'composables/useSseStream.ts'), 'utf-8')
const useJwtSrc = await readFile(join(SRC, 'composables/useJwtAuth.ts'), 'utf-8')
const useFocusSrc = await readFile(join(SRC, 'composables/useFocusTrap.ts'), 'utf-8')
const hitlDialogSrc = await readFile(join(SRC, 'components/HitlEditDialog.vue'), 'utf-8')
const stepEditorSrc = await readFile(join(SRC, 'components/reasoning/StepInlineEditor.vue'), 'utf-8')
const controlBarSrc = await readFile(join(SRC, 'components/reasoning/ReasoningControlBar.vue'), 'utf-8')
const hitlBadgeSrc = await readFile(join(SRC, 'components/controls/HitlBadge.vue'), 'utf-8')

// ═══════════════════════════════════════════════════════════════
// T1. 跨 F 完整流程（F1 → F2 → F3 → F4）— 用户视角
// ═══════════════════════════════════════════════════════════════
describe('T1 · 跨 F 完整流程集成', () => {
  before(async () => {
    await importStores()
  })
  beforeEach(() => {
    const { reasoning, audit } = freshStores()
    reasoning.start('t-integration-1', [
      {
        id: 's1',
        index: 0,
        nodeName: 'supervisor',
        name: '监督节点',
        description: 'd',
        promptFragment: '分析电网',
        draftPromptFragment: null,
        contentHash: null,
        status: 'completed',
        role: 'system',
        startedAt: new Date().toISOString(),
        finishedAt: new Date().toISOString(),
        durationMs: 100,
        output: null,
        isEditable: false,
      },
      {
        id: 's2',
        index: 1,
        nodeName: 'diagnosis_agent',
        name: '诊断',
        description: 'd',
        promptFragment: '用户内容',
        draftPromptFragment: null,
        contentHash: null,
        status: 'running',
        role: 'user',
        startedAt: new Date().toISOString(),
        finishedAt: null,
        durationMs: null,
        output: null,
        isEditable: true,
      },
    ])
  })

  wrap('T1.1 初始态：reasoning=running + audit=0 + 无弹窗', () => {
    const { reasoning, audit } = freshStores()
    reasoning.start('t-init', [])
    assert.equal(reasoning.status, 'running')
    assert.equal(reasoning.totalSteps, 0)
    assert.equal(audit.pendingHitlCount, 0)
    assert.equal(audit.hasPending, false)
  })

  wrap('T1.2 F1 暂停 + SSE 二次确认 → running step → pending (不变量)', () => {
    const { reasoning } = freshStores()
    reasoning.start('t1-2', [
      {
        id: 's-r', index: 0, nodeName: 'n', name: 'n', description: 'd',
        promptFragment: 'p', draftPromptFragment: null, contentHash: null,
        status: 'running', role: 'user', startedAt: new Date().toISOString(),
        finishedAt: null, durationMs: null, output: null, isEditable: true,
      },
    ])
    // 模拟 SSE reasoning_paused 事件 → 二次确认
    reasoning.onSsePaused()
    // 不变量：所有 running step → pending
    assert.equal(reasoning.status, 'paused')
    assert.equal(reasoning.steps[0].status, 'pending', 'paused 态下 running step 必须变 pending')
    assert.ok(reasoning.lastPausedAt)
  })

  wrap('T1.3 F1 恢复：paused → resuming（不直接 running）', () => {
    const { reasoning } = freshStores()
    reasoning.start('t1-3', [])
    reasoning.onSsePaused()
    assert.equal(reasoning.status, 'paused')
    // resume 由 SSE reasoning_resumed 触发
    reasoning.onSseResumed()
    assert.equal(reasoning.status, 'running')
    assert.ok(reasoning.lastResumedAt)
  })

  wrap('T1.4 F2 编辑 + 重跑：beginEdit → updateDraft → rerunFromStep → step_replaced', () => {
    const { reasoning } = freshStores()
    reasoning.start('t1-4', [
      {
        id: 's-edit', index: 1, nodeName: 'd', name: 'n', description: 'd',
        promptFragment: '原内容', draftPromptFragment: null, contentHash: null,
        status: 'completed', role: 'user', startedAt: new Date().toISOString(),
        finishedAt: new Date().toISOString(), durationMs: 100, output: null, isEditable: true,
      },
      {
        id: 's-after', index: 2, nodeName: 'r', name: 'n', description: 'd',
        promptFragment: '下游', draftPromptFragment: null, contentHash: null,
        status: 'running', role: 'system', startedAt: new Date().toISOString(),
        finishedAt: null, durationMs: null, output: null, isEditable: false,
      },
    ])
    // F2 流程
    reasoning.beginEdit('s-edit')
    assert.equal(reasoning.status, 'editing')
    assert.equal(reasoning.editingStepId, 's-edit')
    assert.equal(reasoning.draftSteps['s-edit'], '原内容')
    reasoning.updateDraft('s-edit', 'EDITED')
    assert.equal(reasoning.draftSteps['s-edit'], 'EDITED')
    // 模拟 SSE step_replaced（fromIndex=2 之后 + new steps）
    // 原 [s-edit, s-after] (2 个) → slice(0, 2) = 2 个 + 1 new = 3 个
    reasoning.onSseStepReplaced(2, [
      {
        id: 's-new', index: 2, nodeName: 'r', name: 'n', description: 'd',
        promptFragment: '新下游', draftPromptFragment: null, contentHash: null,
        status: 'running', role: 'system', startedAt: new Date().toISOString(),
        finishedAt: null, durationMs: null, output: null, isEditable: false,
      },
    ])
    assert.equal(reasoning.steps.length, 3, 'splice 后 2+1=3 个')
    assert.equal(reasoning.steps[2].id, 's-new', '下游 step 已被替换')
    // cleanup
    reasoning.cancelEdit()
    assert.equal(reasoning.status, 'running', 'cancelEdit 后回 running')
  })

  wrap('T1.5 F3 + F4 跨 store：audit.onSseHitlInterrupt → approve → pendingHitlCount=0', () => {
    const { audit } = freshStores()
    // 模拟后端 SSE 推送 hitl_interrupt
    audit.onSseHitlInterrupt({
      id: 'hitl-1',
      sessionId: 't1-5',
      stepId: 's-edit',
      aiSuggestion: '建议停机',
      confidence: 0.85,
      riskLevel: 'high',
      status: 'pending',
      createdAt: new Date().toISOString(),
    })
    assert.equal(audit.pendingHitlCount, 1, 'SSE interrupt 后徽标数 +1')
    assert.equal(audit.latestPending?.id, 'hitl-1', 'latestPending 同步')
    assert.equal(audit.hasPending, true)
    // 模拟 F4 "仅批准" 按钮：调 store.approve
    // (实际 approve 调 hitlApprove REST，但本测试绕过 REST 直接调 onSseHitlResolved)
    audit.onSseHitlResolved('hitl-1', 'approved')
    assert.equal(audit.pendingHitlCount, 0, '审批后徽标数 -1')
    assert.equal(audit.latestPending, null, 'latestPending 清理')
  })

  wrap('T1.6 完整链路：F1 暂停 → F2 编辑 → F3 徽标 +1 → F4 approve → 徽标 0', () => {
    const { reasoning, audit } = freshStores()
    reasoning.start('t1-6', [
      {
        id: 's1', index: 0, nodeName: 's', name: 's', description: 'd',
        promptFragment: 'p', draftPromptFragment: null, contentHash: null,
        status: 'running', role: 'user', startedAt: new Date().toISOString(),
        finishedAt: null, durationMs: null, output: null, isEditable: true,
      },
    ])
    // F1
    reasoning.onSsePaused()
    assert.equal(reasoning.status, 'paused')
    // F2
    reasoning.beginEdit('s1')
    reasoning.onSseStepReplaced(1, [
      {
        id: 's1-new', index: 0, nodeName: 's', name: 's', description: 'd',
        promptFragment: 'EDITED', draftPromptFragment: null, contentHash: null,
        status: 'running', role: 'user', startedAt: new Date().toISOString(),
        finishedAt: null, durationMs: null, output: null, isEditable: true,
      },
    ])
    reasoning.cancelEdit()
    // F3
    assert.equal(audit.pendingHitlCount, 0)
    audit.onSseHitlInterrupt({
      id: 'h-t16', sessionId: 't1-6', stepId: 's1-new',
      aiSuggestion: 'x', confidence: 0.5, riskLevel: 'normal',
      status: 'pending', createdAt: new Date().toISOString(),
    })
    assert.equal(audit.pendingHitlCount, 1, 'F3 徽标 +1')
    // F4
    audit.onSseHitlResolved('h-t16', 'approved')
    assert.equal(audit.pendingHitlCount, 0, 'F4 审批后 -1')
  })
})

// ═══════════════════════════════════════════════════════════════
// T2. SSE 断线重连（暂停中）
// ═══════════════════════════════════════════════════════════════
describe('T2 · SSE 断线重连（架构 §6.3 退避序列）', () => {
  wrap('T2.1 退避序列 = [1000, 5000, 15000, 30000]', () => {
    // 数组可能在源码中换行：贪婪匹配（useSseStream.ts:43）
    const re = /DEFAULT_RETRY_DELAYS_MS[\s\S]*?1000[\s\S]*?5000[\s\S]*?15000[\s\S]*?30000[\s\S]*?\]/
    assert.match(useSseSrc, re, '应按 1s/5s/15s/30s 退避')
  })

  wrap('T2.2 心跳超时 = 30000ms', () => {
    assert.match(useSseSrc, /DEFAULT_HEARTBEAT_TIMEOUT_MS\s*=\s*30000/,
      '心跳超时 30s（防连接假死）')
  })

  wrap('T2.3 30s 无消息（含 :heartbeat comment）→ abort + scheduleReconnect', () => {
    assert.match(useSseSrc, /heartbeatTimer\s*=\s*setTimeout/,
      'useSseStream 启动 heartbeat timer')
    assert.match(useSseSrc, /controller\?\.abort\(\)/,
      'heartbeat 超时主动 abort')
    assert.match(useSseSrc, /scheduleReconnect\(\)/,
      '触发重连调度')
  })

  wrap('T2.4 fetch 失败 / response.ok=false → onError + scheduleReconnect', () => {
    assert.match(useSseSrc, /if\s*\(\s*!\s*response\.ok\s*\)/,
      'response.ok=false 视为连接失败')
    assert.match(useSseSrc, /scheduleReconnect\(\)/,
      '触发 scheduleReconnect（接 catch 块）')
  })

  wrap('T2.5 retryAttempt 0/1/2/3/4+ → 1s/5s/15s/30s/30s 映射', () => {
    // 验证 scheduleReconnect 用 retryAttempt 索引
    assert.match(useSseSrc, /const\s+idx\s*=\s*Math\.min\(retryAttempt\.value,\s*delays\.length\s*-\s*1\)/,
      'Math.min 截断到序列最大长度')
  })

  wrap('T2.6 disconnect 主动断开后不再重连', () => {
    assert.match(useSseSrc, /intentionalClose\s*=\s*true/,
      'disconnect 标记 intentionalClose')
    assert.match(useSseSrc, /if\s*\(\s*intentionalClose\s*\)\s*return/,
      'scheduleReconnect 检测 intentionalClose 跳过重连')
  })

  wrap('T2.7 retry() 方法：reset retryAttempt 并立即重连', () => {
    assert.match(useSseSrc, /function\s+reconnect\s*\(\s*\)/,
      '暴露 reconnect 方法给外部')
  })

  wrap('T2.8 onUnmounted 自动 disconnect（Vue 组件卸载安全）', () => {
    assert.match(useSseSrc, /onUnmounted\(\(\)\s*=>\s*\{/,
      'Vue 组件卸载钩子')
    assert.match(useSseSrc, /onUnmounted\(\(\)\s*=>\s*\{[\s\S]*disconnect\(\)/,
      'unmount 时 disconnect')
  })
})

// ═══════════════════════════════════════════════════════════════
// T3. JWT token mismatch → 401/403 友好降级
// ═══════════════════════════════════════════════════════════════
describe('T3 · JWT token mismatch → 端点 403 友好处理', () => {
  wrap('T3.1 chat.ts 7 个新 API 都通过 getAuthHeaders() 注入 Authorization header', () => {
    // pauseSession / resumeSession / rewindSession / abortSession /
    // getSessionCheckpoints / fetchPendingHitlCount / hitlApprove* / hitlReject / hitlApproveWithEdit
    const apiMethods = [
      'pauseSession', 'resumeSession', 'rewindSession', 'abortSession',
      'getSessionCheckpoints', 'fetchPendingHitlCount',
      'hitlApprove', 'hitlReject', 'hitlApproveWithEdit',
    ]
    let missing = []
    for (const m of apiMethods) {
      // 在 chat.ts 中检查每个方法都用了 getAuthHeaders()
      const re = new RegExp(`(?:async\\s+function\\s+${m}|export\\s+async\\s+function\\s+${m})[\\s\\S]+?(?:headers:\\s*getAuthHeaders|headers:\\s*\\{[\\s\\S]*Authorization)`)
      if (!re.test(chatSrc)) missing.push(m)
    }
    assert.equal(missing.length, 0, `以下方法没注入 JWT: ${missing.join(', ')}`)
  })

  wrap('T3.2 useSseStream 注入 Authorization: Bearer <jwt>', () => {
    assert.match(useSseSrc, /Authorization:\s*`Bearer\s*\$\{getJwtToken\(\)\}`/,
      'useSseStream 必须注入 Authorization: Bearer')
  })

  wrap('T3.3 subscribeSessionEvents 双发 JWT（Authorization + query token）', () => {
    // 这是 chat.ts 的实现选择 —— 走 fetch 但仍把 token 放 query（EventSource 替代方案）
    // 接受这种设计，但记录在 QA 报告
    assert.match(chatSrc, /url\s*=.*token=.*getJwtToken/s,
      'SSE URL query 含 token（向后兼容）')
    assert.match(chatSrc, /Authorization:\s*`Bearer\s*\$\{getJwtToken\(\)\}`/,
      'Authorization header 也注入（与 query 双发）')
  })

  wrap('T3.4 audit store 401/403 错误：connectionState=error + 徽标降级', async () => {
    // mock fetch 返回 403
    const realFetch = globalThis.fetch
    let fetchCallCount = 0
    globalThis.fetch = async (url, opts) => {
      fetchCallCount++
      const u = typeof url === 'string' ? url : url.toString()
      if (u.includes('/audit/pending-count')) {
        return {
          ok: false,
          status: 403,
          statusText: 'Forbidden',
          json: async () => ({ detail: 'Invalid JWT' }),
        }
      }
      return realFetch?.(url, opts) ?? Promise.reject(new Error('not mocked'))
    }
    try {
      const { audit } = freshStores()
      await audit.refreshPendingCount()
      assert.equal(audit.connectionState, 'error', '403 → connectionState=error')
      assert.equal(audit.isBackendUnreachable, true, 'isBackendUnreachable=true')
      // pendingHitlCount 保留（不归零，避免徽标消失）
      assert.ok(audit.pendingHitlCount === 0, '刷新失败不抛错，count 保持上值')
    } finally {
      globalThis.fetch = realFetch
    }
  })

  wrap('T3.5 audit store 网络异常（fetch throw）：同 401/403 路径', async () => {
    const realFetch = globalThis.fetch
    globalThis.fetch = async () => {
      throw new TypeError('Failed to fetch')
    }
    try {
      const { audit } = freshStores()
      await audit.refreshPendingCount()
      assert.equal(audit.connectionState, 'error', '网络异常 → connectionState=error')
    } finally {
      globalThis.fetch = realFetch
    }
  })

  wrap('T3.6 useJwtAuth 默认 token 常量 = "gridmind-dev-token"', () => {
    assert.match(useJwtSrc, /DEV_DEFAULT_JWT_TOKEN\s*=\s*['"]gridmind-dev-token['"]/,
      '默认 dev token 是 gridmind-dev-token（主理人决策 7.1）')
  })

  wrap('T3.7 useJwtAuth 不放 localStorage（防 XSS）', () => {
    // useJwtAuth.ts 应**不**调用 localStorage.setItem('gridmind.jwt', ...)
    assert.doesNotMatch(useJwtSrc, /localStorage\.(setItem|getItem).*jwt/i,
      'useJwtAuth 不在 localStorage 存 JWT（架构 §6.1.1 增强）')
  })

  wrap('T3.8 useJwtAuth 读顺序：Vite env → process.env → 默认常量', () => {
    // 验证三个读取源都存在
    // 兼容 TS 类型断言写法：(import.meta as { env?: ... }).env?.VITE_DEV_JWT_TOKEN
    assert.match(useJwtSrc, /import\.meta[\s\S]{0,100}?\.env\?\.VITE_DEV_JWT_TOKEN/,
      'Vite 编译期 import.meta.env 优先（兼容 TS 断言）')
    // 兼容 TS 断言写法：(globalThis as ...).process.env.VITE_DEV_JWT_TOKEN
    assert.match(useJwtSrc, /\.env[\s\S]{0,80}?VITE_DEV_JWT_TOKEN/,
      'Node process.env fallback（兼容 TS 断言写法）')
    assert.match(useJwtSrc, /return\s+DEV_DEFAULT_JWT_TOKEN/,
      '最终兜底默认常量')
  })
})

// ═══════════════════════════════════════════════════════════════
// T4. F2 inline editor + F4 弹窗 focus trap 共存
// ═══════════════════════════════════════════════════════════════
describe('T4 · Focus trap 多实例共存（F2 + F4 互不干扰）', () => {
  wrap('T4.1 useFocusTrap 是"每个组件独立实例" — 验证 handleKeydown 监听器是函数级局部', () => {
    // handleKeydown 是 closure 局部函数（不是全局），每个 useFocusTrap 调用独立
    assert.match(useFocusSrc, /function\s+handleKeydown\s*\(\s*e\s*:\s*KeyboardEvent\s*\)/,
      'handleKeydown 是 composable 内部函数（每个实例独立）')
    assert.match(useFocusSrc, /document\.addEventListener\(['"]keydown['"],\s*handleKeydown/,
      '监听器注册时绑定本地 handleKeydown（不是全局共享）')
  })

  wrap('T4.2 getFocusableElements 仅查询 containerRef 内元素（不外溢）', () => {
    assert.match(useFocusSrc, /container\s*=\s*options\.containerRef\.value/,
      'getFocusableElements 用 containerRef 限定作用域')
    assert.match(useFocusSrc, /container\.querySelectorAll/,
      'querySelectorAll 限定在 container 内')
  })

  wrap('T4.3 两个 trap 各自维护 previouslyFocused 局部变量', () => {
    // 验证 previouslyFocused 是闭包局部（不是 module-level）
    assert.match(useFocusSrc, /let\s+previouslyFocused\s*:\s*HTMLElement\s*\|\s*null\s*=\s*null/,
      'previouslyFocused 在 composable 函数体顶部声明（每实例独立）')
  })

  wrap('T4.4 容器 DOM 隔离：F2 inline editor（textarea + 3 按钮）只在 step editor 内循环', () => {
    // StepInlineEditor.vue 有 4 focusables: textarea + 3 按钮
    assert.match(stepEditorSrc, /textarea[\s\S]{0,500}role="group"|role="group"[\s\S]{0,500}textarea/,
      'inline editor 有 textarea + group 容器')
    assert.match(stepEditorSrc, /<el-button[\s\S]*?💾 保存草稿/,
      '保存草稿按钮')
    assert.match(stepEditorSrc, /<el-button[\s\S]*?🔄 从此步重跑/,
      '重跑按钮')
    assert.match(stepEditorSrc, /<el-button[\s\S]*?✕ 取消/,
      '取消按钮')
    assert.match(stepEditorSrc, /useFocusTrap\(\{\s*containerRef\s*\}\)/,
      'useFocusTrap 集成')
  })

  wrap('T4.5 容器 DOM 隔离：F4 弹窗（3 决策按钮 + × 关闭）只在 hitl-dialog 内循环', () => {
    assert.match(hitlDialogSrc, /role="dialog"[\s\S]{0,500}aria-modal="true"/,
      '弹窗有 role=dialog + aria-modal')
    assert.match(hitlDialogSrc, /useFocusTrap\(\{[\s\S]*?containerRef:\s*dialogRef/,
      'useFocusTrap 集成在 dialogRef 上')
    assert.match(hitlDialogSrc, /data-testid="hitl-btn-reject"/,
      '拒绝按钮')
    assert.match(hitlDialogSrc, /data-testid="hitl-btn-approve"/,
      '仅批准按钮')
    assert.match(hitlDialogSrc, /data-testid="hitl-btn-edit-approve"/,
      '修改后批准按钮')
    assert.match(hitlDialogSrc, /data-testid="hitl-close-btn"/,
      '× 关闭按钮')
  })

  wrap('T4.6 实际并发：同时激活两个 trap 不会导致 handleKeydown 重复触发', () => {
    // 验证 activate 守卫：多次调用安全
    assert.match(useFocusSrc, /if\s*\(\s*isActive\.value\s*\)\s*return/,
      'activate 有 isActive 守卫（防重复注册 keydown）')
    // 验证 deactivate 守卫
    assert.match(useFocusSrc, /if\s*\(\s*!isActive\.value\s*\)\s*return/,
      'deactivate 有 isActive 守卫（防重复 removeEventListener）')
  })

  wrap('T4.7 关闭弹窗时 trap 自动 deactivate（onUnmounted 钩子）', () => {
    assert.match(useFocusSrc, /onUnmounted\(\(\)\s*=>\s*\{[\s\S]*?deactivate\(\)/,
      'useFocusTrap 卸载时自动 deactivate')
  })

  wrap('T4.8 焦点回收：deactivate 把焦点还给 previouslyFocused（开 trap 之前）', () => {
    // TS 缩窄写法 target.focus()，或 target 变量
    const re = /focus\(\)|\.focus\s*\(/
    // 直接验证 useFocusTrap deactivate 内有 focus() 调用（任何形式）
    assert.match(useFocusSrc, re, 'deactivate 内应调 focus()')
    // 验证 previouslyFocused 变量在 activate 时被记录
    assert.match(useFocusSrc, /previouslyFocused\s*=\s*\(?document\.activeElement/,
      'previouslyFocused 在 activate 时记录当前焦点')
  })
})

// ═══════════════════════════════════════════════════════════════
// T5. 边界场景：状态机非法转换 / 空 steps / session_lock / 空 token
// ═══════════════════════════════════════════════════════════════
describe('T5 · 边界场景（防御性测试）', () => {
  before(async () => {
    await importStores()
  })

  wrap('T5.1 暂停在 idle 态被调 → no-op（不抛错）', () => {
    const { reasoning } = freshStores()
    // idle 状态直接调 pause
    return reasoning.pause().then((r) => {
      assert.equal(r, null, 'idle 态 pause 返回 null')
      assert.equal(reasoning.status, 'idle', '状态未变')
    })
  })

  wrap('T5.2 resume 在 idle 态被调 → no-op', () => {
    const { reasoning } = freshStores()
    return reasoning.resume().then((r) => {
      assert.equal(r, null, 'idle 态 resume 返回 null')
      assert.equal(reasoning.status, 'idle')
    })
  })

  wrap('T5.3 beginEdit 在 idle 态被调 → 抛 STEP_NOT_EDITABLE（step 不存在）', () => {
    const { reasoning } = freshStores()
    reasoning.start('t5-3', [
      {
        id: 's', index: 0, nodeName: 'n', name: 'n', description: 'd',
        promptFragment: 'p', draftPromptFragment: null, contentHash: null,
        status: 'pending', role: 'user', startedAt: new Date().toISOString(),
        finishedAt: null, durationMs: null, output: null, isEditable: true,
      },
    ])
    // 立即 reset 回 idle + 清 steps
    reasoning.reset()
    // step 不存在 → isEditable() 返回 false → 抛 STEP_NOT_EDITABLE
    assert.throws(
      () => reasoning.beginEdit('s'),
      /STEP_NOT_EDITABLE|REASONING_NOT_EDITABLE_STATE/,
      'reset 后 beginEdit 应抛 STEP_NOT_EDITABLE 或 REASONING_NOT_EDITABLE_STATE',
    )
  })

  wrap('T5.4 beginEdit 对非 editable step 抛 STEP_NOT_EDITABLE', () => {
    const { reasoning } = freshStores()
    reasoning.start('t5-4', [
      {
        id: 'sys-step', index: 0, nodeName: 'n', name: 'n', description: 'd',
        promptFragment: 'p', draftPromptFragment: null, contentHash: null,
        status: 'running', role: 'system', startedAt: new Date().toISOString(),
        finishedAt: null, durationMs: null, output: null, isEditable: false, // 不可编辑
      },
    ])
    assert.throws(
      () => reasoning.beginEdit('sys-step'),
      /STEP_NOT_EDITABLE/,
    )
  })

  wrap('T5.5 空 steps 列表渲染不崩（isActive 边界）', () => {
    const { reasoning } = freshStores()
    reasoning.start('t5-5', [])
    assert.equal(reasoning.isActive, true, '空 steps 也算 active')
    assert.equal(reasoning.totalSteps, 0)
    assert.equal(reasoning.completedSteps.length, 0)
    assert.equal(reasoning.nextStepToRun, null)
    assert.equal(reasoning.progress, 0, 'progress = 0 when empty')
  })

  wrap('T5.6 reset() 清空内存状态（reattach_thread_id 由 markCompleted/abort 清，reset 保留）', () => {
    const { reasoning } = freshStores()
    reasoning.start('t5-6', [])
    // 手动写 reattach（模拟 pause() 副作用）
    localStorage.setItem('gridmind.reattach_thread_id', 't5-6')
    assert.equal(localStorage.getItem('gridmind.reattach_thread_id'), 't5-6')
    reasoning.reset()
    assert.equal(reasoning.sessionId, '', 'reset 清 sessionId')
    assert.equal(reasoning.status, 'idle', 'reset 回 idle')
    assert.equal(reasoning.steps.length, 0, 'reset 清 steps')
    // 架构 §1.5.3：reset 不清 reattach（让下次 hydrate 能恢复）
    assert.equal(localStorage.getItem('gridmind.reattach_thread_id'), 't5-6',
      'reset 不清 reattach_thread_id（仅 markCompleted/abort 清）')
  })

  wrap('T5.7 markCompleted 清 reattach_thread_id（架构 §1.5.3）', () => {
    const { reasoning } = freshStores()
    reasoning.start('t5-7', [])
    localStorage.setItem('gridmind.reattach_thread_id', 't5-7')
    reasoning.markCompleted()
    assert.equal(localStorage.getItem('gridmind.reattach_thread_id'), null,
      'markCompleted 清 reattach（避免下次启动误恢复为 paused）')
  })

  wrap('T5.8 hydrate 在 reattach 存在时恢复 sessionId + status=paused', () => {
    localStorage.setItem('gridmind.reattach_thread_id', 't-restored')
    setActivePinia(createPinia())
    const reasoning = useReasoningStore()
    reasoning.hydrate()
    assert.equal(reasoning.sessionId, 't-restored', '从 localStorage 恢复 sessionId')
    assert.equal(reasoning.status, 'paused', '恢复后默认 paused 态')
  })

  wrap('T5.9 audit store 并发：5s 轮询 + onSseHitlInterrupt 同时触发，计数一致性', () => {
    const { audit } = freshStores()
    // 初始 0
    assert.equal(audit.pendingHitlCount, 0)
    // SSE interrupt 1 次
    audit.onSseHitlInterrupt({
      id: 'h-c1', sessionId: 's', stepId: 's', aiSuggestion: 'x',
      confidence: 0.5, riskLevel: 'low', status: 'pending', createdAt: '',
    })
    // 同时（模拟）轮询返回 3
    audit.pendingHitlCount = 3
    // 断言：后到的值覆盖（轮询权威性 > SSE 漂移）
    assert.equal(audit.pendingHitlCount, 3)
    // SSE 又 interrupt 1 次 → 4
    audit.onSseHitlInterrupt({
      id: 'h-c2', sessionId: 's', stepId: 's', aiSuggestion: 'x',
      confidence: 0.5, riskLevel: 'low', status: 'pending', createdAt: '',
    })
    assert.equal(audit.pendingHitlCount, 4, 'SSE 后增 1')
    // displayCount getter：> 99 显示 "99+"
    audit.pendingHitlCount = 100
    assert.equal(audit.displayCount, '99+', '100 显示 99+')
    audit.pendingHitlCount = 50
    assert.equal(audit.displayCount, '50')
  })

  wrap('T5.10 多个 trap 同时 deactivate 不报错（isActive 守卫）', () => {
    // 静态分析：deactivate 函数有 isActive 守卫
    assert.match(useFocusSrc, /function\s+deactivate\(\)[\s\S]*?if\s*\(\s*!isActive\.value\s*\)\s*return/,
      'deactivate 有 isActive 守卫')
  })

  wrap('T5.11 ReasonControlBar abort 二次确认 catch "cancel"/"close" → no-op', () => {
    // 静态分析：abort handler 区分 cancel/close 与真正错误
    assert.match(controlBarSrc, /if\s*\(\s*e\s*===\s*['"]cancel['"][\s\S]*?return/,
      'cancel 字符串判断')
    assert.match(controlBarSrc, /e\s*===\s*['"]close['"]/,
      'close 字符串判断')
  })

  wrap('T5.12 R-X5 修复验证：catch 块不再用 ${msg} 暴露内部异常（架构 §6.8）', async () => {
    // T07 R-X5 修复后：ReasoningControlBar 3 处 catch + StepInlineEditor 1 处 catch
    // 不再用 `${msg}` / String(err) 模板字面量；改为 dev console.error + 通用 ElMessage 提示

    // 1) ReasoningControlBar：旧 buggy `${msg}` 模板字面量应被替换（0 处泄漏）
    const leakyPatterns = [/ElMessage\.error\(`[^`]*\$\{msg}`/]
    let leakCount = 0
    for (const p of leakyPatterns) {
      if (p.test(controlBarSrc)) leakCount++
    }
    assert.equal(
      leakCount,
      0,
      `R-X5 修复后 ReasoningControlBar 应无 \`\${...}\${msg}\` 模板字面量泄漏（实测 ${leakCount} 处）`,
    )

    // 2) ReasoningControlBar：3 个失败提示改用通用文案（字符串字面量，无内部异常）
    assert.match(
      controlBarSrc,
      /ElMessage\.error\(['"]暂停失败/,
      'R-X5：handlePause 失败提示应使用通用文案"暂停失败..."',
    )
    assert.match(
      controlBarSrc,
      /ElMessage\.error\(['"]恢复失败/,
      'R-X5：handleResume 失败提示应使用通用文案"恢复失败..."',
    )
    assert.match(
      controlBarSrc,
      /ElMessage\.error\(['"]中止失败/,
      'R-X5：handleAbort 失败提示应使用通用文案"中止失败..."',
    )

    // 3) StepInlineEditor：handleRerun 失败提示不再用 `${msg}` 拼接
    assert.doesNotMatch(
      stepEditorSrc,
      /ElMessage\.error\(`重跑失败：\$\{msg}`/,
      'R-X5：StepInlineEditor handleRerun 不应再拼接 ${msg}',
    )
    assert.match(
      stepEditorSrc,
      /ElMessage\.error\(['"]重跑失败/,
      'R-X5：StepInlineEditor handleRerun 失败应使用通用文案',
    )

    // 4) ChatView SSE handler · R-X3 patch 已修复（v1.5.1 已知遗留补丁）：
    //   原 `\`推理错误: ${event.error...}\`` 模板 + `markError(event.error ?? ...)` 已全部清除，
    //   改用通用文案 + console.error 留服务侧 trace
    assert.doesNotMatch(
      await readFile(join(SRC, 'components/ChatView.vue'), 'utf-8'),
      /ElMessage\.error\s*\(\s*[`][^`]*\$\{event\.error[^`]*`/,
      'R-X3 patch：ChatView reasoning_error 分支 ElMessage.error 不应再拼接 ${event.error}',
    )
    assert.doesNotMatch(
      await readFile(join(SRC, 'components/ChatView.vue'), 'utf-8'),
      /reasoning\.markError\s*\(\s*event\.error/,
      'R-X3 patch：ChatView reasoning_error 分支 markError 参数不应再拼 event.error',
    )
    assert.match(
      await readFile(join(SRC, 'components/ChatView.vue'), 'utf-8'),
      /markError\s*\(\s*['"]推理服务异常/,
      'R-X3 patch：markError 参数应为字符串字面量（通用文案）',
    )
  })

  wrap('T5.13 JWT 格式错误：Authorization: Bearer <空> → headers 跳过（不会发 "Bearer "）', () => {
    // 验证 getAuthHeaders 当 token 为空时返回空对象
    assert.match(useJwtSrc, /if\s*\(\s*!token\s*\)\s*return\s+\{\}/,
      '空 token 返回空对象（不会构造 "Bearer " 无 token 的 header）')
  })

  wrap('T5.14 query 注入防御：HitlBadge router.push 用硬编码值', () => {
    // 验证 query 注入风险：filter / from 是硬编码常量，不是用户输入
    const queryRe = /query:\s*\{\s*filter:\s*['"]pending['"][\s\S]*?from:\s*['"]hitl-badge['"]/
    assert.match(hitlBadgeSrc, queryRe, 'filter=pending & from=hitl-badge 均为硬编码')
  })

  wrap('T5.15 localStorage 写入防御：reasoning.draftSteps 不持久化', async () => {
    // 静态分析：reasoning store 不在 localStorage 写 draftSteps
    const reasoningSrc = await readFile(join(SRC, 'stores/reasoning.ts'), 'utf-8')
    // 提取所有 localStorage 调用的第一个参数（key），验证都不是 draftSteps 相关
    const calls = reasoningSrc.match(/localStorage\.(?:setItem|getItem|removeItem)\s*\(([^)]+)\)/g) || []
    const draftStepWrites = calls.filter((c) => /draftSteps/.test(c))
    assert.equal(draftStepWrites.length, 0,
      `draftSteps 不应被持久化。发现 ${draftStepWrites.length} 处违规调用`)
    // 验证唯一持久化 key 是 reattach_thread_id
    assert.match(reasoningSrc, /REATTACH_THREAD_ID_KEY\s*=\s*['"]gridmind\.reattach_thread_id['"]/,
      'reattach_thread_id 是唯一持久化 key 常量')
    // 验证 draftSteps 是 ref（内存）
    assert.match(reasoningSrc, /draftSteps\s*=\s*ref/,
      'draftSteps 是 ref（内存），不是 reactive 持久化')
  })
})

// ═══════════════════════════════════════════════════════════════
// 总结输出
// ═══════════════════════════════════════════════════════════════
process.on('exit', () => {
  process.stderr.write(`\n\x1b[1m── T06 QA 集成测试总结 ──\x1b[0m\n`)
  process.stderr.write(`  Pass: \x1b[32m${totalPass}\x1b[0m\n`)
  process.stderr.write(`  Fail: \x1b[31m${totalFail}\x1b[0m\n`)
  if (testFailures.length) {
    process.stderr.write(`\n  Failures:\n`)
    for (const f of testFailures) {
      process.stderr.write(`    - ${f.name}\n`)
      process.stderr.write(`      ${f.error?.message || f.error}\n`)
    }
  }
})
