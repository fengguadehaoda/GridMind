/**
 * GridMind v1.5.1 T07 · QA R-X5 + R-X6 安全补丁测试
 *
 * 背景（QA 验收报告 2026-08-04）：
 *   R-X5 (P1 中危)：5 处 String(err)/(e as Error).message 通过 ElMessage 暴露给用户
 *                含路径 / token / 内部变量名等敏感信息
 *   R-X6 (P1 中危)：ChatView SSE 断线无重连（subscribeSessionEvents 无重连逻辑）
 *
 * 覆盖场景（10）：
 *
 *   R-X5 静态源码分析（6 场景）：
 *     S1. ReasoningControlBar.vue · pause 失败通用 message + console.error
 *     S2. ReasoningControlBar.vue · resume 失败通用 message + console.error
 *     S3. ReasoningControlBar.vue · abort 失败通用 message + cancel/close no-op
 *     S4. StepInlineEditor.vue · rerun 失败通用 message + console.error
 *     S5. StepEditButton.vue · edit 通用 message + 业务错误码分支保留
 *     S6. 4 个文件统一保留 console（开发调试用，工作约束 #4）
 *
 *   R-X5 全文件回归（1 场景）：
 *     S7. 4 个文件中所有 catch 块 ElMessage.error 调用均不含泄漏模式
 *         （防止回归 R-X5 — 含 R-X5 未明列的位置）
 *
 *   R-X6 SSE 重连（3 场景）：
 *     S8. ChatView 不再使用 subscribeSessionEvents，改用 useSseStream
 *         + 退避 [1000, 5000, 15000, 30000] + 30s 心跳
 *     S9. ChatView SSE onError 显示通用 message（"实时连接中断，正在自动重连..."）
 *     S10. useSseStream 运行时：fetch 失败触发重连 + retryAttempt 自增
 *
 * 运行：node tests/test_safety_patch.mjs
 *
 * 策略：与现有 test_runner.mjs / test_chatview_integration.mjs 一致
 *   - 静态：readFile + regex / includes
 *   - 运行时：esbuild bundle useSseStream + mock fetch（与 test_sse_stream.mjs 同模式）
 */

import { test, describe, before } from 'node:test'
import assert from 'node:assert/strict'
import { build } from 'esbuild'
import { mkdtemp, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { existsSync, symlinkSync } from 'node:fs'

// ─── 路径 ───────────────────────────────────────────────────
const ROOT = resolve(import.meta.dirname, '..')
const SRC = join(ROOT, 'src')
const TMP = await mkdtemp(join(tmpdir(), 'gridmind-safety-'))

// ─── 读源文件 ───────────────────────────────────────────────
const BAR_SRC = await readFile(
  join(SRC, 'components/reasoning/ReasoningControlBar.vue'),
  'utf-8',
)
const STEP_SRC = await readFile(
  join(SRC, 'components/reasoning/StepInlineEditor.vue'),
  'utf-8',
)
const BTN_SRC = await readFile(
  join(SRC, 'components/reasoning/StepEditButton.vue'),
  'utf-8',
)
const CHATVIEW_SRC = await readFile(
  join(SRC, 'components/ChatView.vue'),
  'utf-8',
)

// ─── 总结 ───────────────────────────────────────────────────
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

/* ────────────────────────────────────────────────────────────
 * 工具：从 catch 块内提取 ElMessage.error 调用的字符串字面量
 *
 * 不做嵌套大括号计数（怕 lazy match 在内部 `}` 提前终止）；
 * 改用以下启发式："catch (...)" 与下一个 "function"/"async function"/"<template>" 等
 * 顶层结构之间的内容视作 catch 关联作用域。
 * ──────────────────────────────────────────────────────────── */

/**
 * 在源码中找出 catch 块内出现的所有 ElMessage.error 字符串字面量
 * （通过简单的逐行扫描 + catch 起止定位）。
 */
function extractCatchErrorMessages(src) {
  const out = []
  const lines = src.split('\n')
  let inCatch = 0   // 当前 catch 嵌套深度（catch 块中可能有嵌套 catch）
  let catchIndent = -1
  for (const line of lines) {
    // 进入 catch：(  |})?catch\s*\(...
    if (inCatch === 0) {
      const m = line.match(/(?:^|\W)catch\s*\(/)
      if (m) {
        inCatch = 1
        catchIndent = line.search(/\S/)
      }
    }

    if (inCatch > 0) {
      // 提取 ElMessage.error('xxx') / (`xxx`) / ("xxx")
      const em = line.match(
        /ElMessage\.error\(\s*(?:`([^`]*)`|'([^']*)'|"([^"]*)")\s*\)/,
      )
      if (em) {
        out.push(em[1] ?? em[2] ?? em[3] ?? '')
      }
      // catch 块结束：下一段顶层结构（缩进 <= catchIndent）或文件结束
      const indentMatch = line.match(/^(\s*)\S/)
      if (indentMatch && line.trim() !== '') {
        const indent = indentMatch[1].length
        // catch 块通常以 0/2 缩进开括号，遇到更低缩进的同类语句时认为退出
        // 简化策略：遇连续 } 或 indent < catchIndent - 1 视为退出
        if (line.trim() === '}' && indent <= catchIndent) {
          inCatch = 0
          catchIndent = -1
        }
      }
    }
  }
  return out
}

/** 校验一个字符串是否"暴露内部异常"（含 R-X5 列举的所有模式） */
function containsLeakage(text) {
  if (typeof text !== 'string') return false
  const leakPatterns = [
    /e\.message/,
    /err\.message/,
    /error\.message/,
    /String\s*\(\s*e\s*\)/,
    /String\s*\(\s*err\s*\)/,
    /\/[\w.-]+\.(py|ts|tsx|js|vue)/i, // xxx.py / xxx.ts
    /token[=:]/i,
    /secret[=:]/i,
    /\$\{[a-zA-Z_]+\}/, // 模板插值（注意是 ` 而不是单/双引号）
  ]
  return leakPatterns.some((re) => re.test(text))
}

/* ══════════════════════════════════════════════════════════════
 * Group 1: R-X5 静态源码分析 — 5 个文件统一规范
 * ══════════════════════════════════════════════════════════════ */
describe('R-X5 · 异常信息不再暴露给用户（架构 §6.8 安全规则）', () => {
  wrap('S1 ReasoningControlBar · pause 失败：通用 message + console.error 标记', () => {
    // 通用文案
    assert.match(
      BAR_SRC,
      /ElMessage\.error\(\s*['"]暂停失败，请稍后重试['"]\s*\)/,
      'pause 失败应显示通用"暂停失败，请稍后重试"',
    )
    // console.error 标记（用 [\s\S]{0,200}? 允许中间有 )/引号等）
    assert.match(
      BAR_SRC,
      /console\.error\([\s\S]{0,200}?ReasoningControlBar\.pause/,
      '应有 console.error 标记 [ReasoningControlBar.pause]',
    )
  })

  wrap('S2 ReasoningControlBar · resume 失败：通用 message + console.error 标记', () => {
    assert.match(
      BAR_SRC,
      /ElMessage\.error\(\s*['"]恢复失败，请稍后重试['"]\s*\)/,
      'resume 失败应显示通用"恢复失败，请稍后重试"',
    )
    assert.match(
      BAR_SRC,
      /console\.error\([\s\S]{0,200}?ReasoningControlBar\.resume/,
      '应有 console.error 标记 [ReasoningControlBar.resume]',
    )
  })

  wrap('S3 ReasoningControlBar · abort 失败：通用 message + cancel/close 仍 no-op', () => {
    assert.match(
      BAR_SRC,
      /ElMessage\.error\(\s*['"]中止失败，请稍后重试['"]\s*\)/,
      'abort 失败应显示通用"中止失败，请稍后重试"',
    )
    assert.match(
      BAR_SRC,
      /console\.error\([\s\S]{0,200}?ReasoningControlBar\.abort/,
      '应有 console.error 标记 [ReasoningControlBar.abort]',
    )
    // 业务约束：cancel / close 必须 no-op（仍保留）
    assert.match(
      BAR_SRC,
      /if\s*\(\s*e\s*===\s*['"]cancel['"]\s*\|\|\s*e\s*===\s*['"]close['"]\s*\)\s*return/,
      'cancel / close 仍应 no-op（业务约束）',
    )
  })

  wrap('S4 StepInlineEditor · rerun 失败：通用 message + console.error 标记', () => {
    assert.match(
      STEP_SRC,
      /ElMessage\.error\(\s*['"]重跑失败，请稍后重试['"]\s*\)/,
      'rerun 失败应显示通用"重跑失败，请稍后重试"',
    )
    assert.match(
      STEP_SRC,
      /console\.error\([\s\S]{0,200}?StepInlineEditor\.rerun/,
      '应有 console.error 标记 [StepInlineEditor.rerun]',
    )
  })

  wrap('S5 StepEditButton · edit 通用 message + 业务错误码分支保留', () => {
    // 通用 message（catch 兜底分支）
    assert.match(
      BTN_SRC,
      /ElMessage\.error\(\s*['"]编辑失败，请稍后重试['"]\s*\)/,
      '通用 catch 应显示"编辑失败，请稍后重试"',
    )
    assert.match(
      BTN_SRC,
      /console\.error\([\s\S]{0,200}?StepEditButton\.beginEdit/,
      '应有 console.error 标记 [StepEditButton.beginEdit]',
    )
    // 业务错误码分支（NOT 内部异常泄漏 — 是契约错误，保留友好提示）
    assert.match(BTN_SRC, /REASONING_NOT_EDITABLE_STATE/, '应保留 REASONING_NOT_EDITABLE_STATE 业务错误码分支')
    assert.match(BTN_SRC, /STEP_NOT_EDITABLE/, '应保留 STEP_NOT_EDITABLE 业务错误码分支')
    // 旧 "进入编辑态失败：${msg}" 文案应已修复
    assert.doesNotMatch(BTN_SRC, /进入编辑态失败/, '旧"进入编辑态失败：${msg}"文案已修复')
  })

  wrap('S6 4 个文件统一保留 console（开发调试用 · 工作约束 #4）', () => {
    // console.error（Bar 至少 1 处，强制 ≥1）
    const barCount = (BAR_SRC.match(/console\.error\(/g) || []).length
    assert.ok(barCount >= 1, `ReasoningControlBar 至少 1 处 console.error（实际 ${barCount}）`)
    assert.match(STEP_SRC, /console\.error\(/, 'StepInlineEditor 应有 console.error')
    assert.match(BTN_SRC, /console\.error\(/, 'StepEditButton 应有 console.error')
    // ChatView：SSE onError 在 console 里记 err 完整对象（开发调试用）
    assert.match(
      CHATVIEW_SRC,
      /console\.warn\([\s\S]{0,200}?SSE[\s\S]{0,200}?err/,
      'ChatView SSE 错误应有 console.warn（含 err 对象）',
    )
  })
})

/* ══════════════════════════════════════════════════════════════
 * Group 2: R-X5 全文件回归扫描（catch 块内 ElMessage 不含泄漏）
 * ══════════════════════════════════════════════════════════════ */
describe('R-X5 · 全文件回归扫描（catch 块内 ElMessage.error 不含泄漏）', () => {
  wrap('S7 4 个文件的所有 catch 块 ElMessage.error 调用均不含泄漏模式', () => {
    const files = {
      'ReasoningControlBar.vue': BAR_SRC,
      'StepInlineEditor.vue': STEP_SRC,
      'StepEditButton.vue': BTN_SRC,
      'ChatView.vue': CHATVIEW_SRC,
    }
    let totalChecked = 0
    let totalLeaked = 0
    const leakedList = []
    for (const [fname, src] of Object.entries(files)) {
      const messages = extractCatchErrorMessages(src)
      for (const text of messages) {
        totalChecked++
        if (containsLeakage(text)) {
          totalLeaked++
          leakedList.push(`${fname}: "${text}"`)
        }
      }
    }
    assert.equal(totalChecked >= 4, true, `至少应有 4 个 catch 块 ElMessage.error（实际 ${totalChecked}）`)
    assert.equal(totalLeaked, 0,
      `R-X5 修复失败：${totalLeaked} 处仍含泄漏模式 →\n  ${leakedList.join('\n  ')}`)
  })
})

/* ══════════════════════════════════════════════════════════════
 * Group 3: R-X6 SSE 重连（架构 §6.3 + 主理人决策 7.2）
 * ══════════════════════════════════════════════════════════════ */
describe('R-X6 · ChatView SSE 自动重连（useSseStream + 退避 1s/5s/15s/30s）', () => {
  wrap('S8 ChatView 不再调用 subscribeSessionEvents，改用 useSseStream composable', () => {
    // 不应再 import / 调 subscribeSessionEvents
    assert.doesNotMatch(
      CHATVIEW_SRC,
      /import\s*\{[^}]*subscribeSessionEvents[^}]*\}\s*from\s*['"][^'"]*api\/chat['"]/,
      '不应再 import subscribeSessionEvents from @/api/chat',
    )
    assert.doesNotMatch(
      CHATVIEW_SRC,
      /subscribeSessionEvents\s*\(/,
      '不应再调 subscribeSessionEvents(...)',
    )
    // 应改用 useSseStream
    assert.match(
      CHATVIEW_SRC,
      /import\s*\{[^}]*useSseStream[^}]*\}\s*from\s*['"][^'"]*composables\/useSseStream['"]/,
      '应 import useSseStream from ../composables/useSseStream',
    )
    assert.match(
      CHATVIEW_SRC,
      /useSseStream\s*<\s*SseEvent\s*>\s*\(/,
      '应使用 useSseStream<SseEvent>({...}) 泛型调用',
    )
  })

  wrap('S8.1 重连退避序列 [1000, 5000, 15000, 30000] + 30s 心跳超时', () => {
    // 退避序列
    assert.match(
      CHATVIEW_SRC,
      /retryDelaysMs:\s*\[\s*1000\s*,\s*5000\s*,\s*15000\s*,\s*30000\s*\]/,
      'ChatView 应传入退避序列 [1000, 5000, 15000, 30000]',
    )
    // 心跳超时
    assert.match(
      CHATVIEW_SRC,
      /heartbeatTimeoutMs:\s*30000\b/,
      'ChatView 应传入 30s 心跳超时',
    )
  })

  wrap('S9 ChatView SSE onError 显示通用 message（"实时连接中断，正在自动重连..."）', () => {
    // 通用文案（含中文标点 ……）
    assert.match(
      CHATVIEW_SRC,
      /ElMessage\.warning\(\s*['"]实时连接中断，正在自动重连\.{3}['"]\s*\)/,
      'SSE 错误应显示通用"实时连接中断，正在自动重连..."',
    )
    // onError 回调体的代码部分不应有泄漏（中文注释中允许文字提及"异常" 等通用词汇）
    const onErrorMatch = CHATVIEW_SRC.match(
      /onError:\s*\(\s*err\s*\)\s*=>\s*\{([\s\S]*?)\}\s*,?/,
    )
    assert.ok(onErrorMatch, 'SSE onError 回调应存在')
    const onErrorBlock = onErrorMatch[1]
    // 去掉注释行后再检查
    const codeOnly = onErrorBlock
      .split('\n')
      .filter((line) => !line.trim().startsWith('//'))
      .join('\n')
    assert.doesNotMatch(codeOnly, /\berr\.message\b/, 'SSE onError 代码部分不应出现 err.message')
    assert.doesNotMatch(codeOnly, /String\s*\(\s*err\s*\)/, 'SSE onError 代码部分不应 String(err)')
    // ElMessage.warning 不应用模板插值（必须是常量字符串）
    const emInOnError = codeOnly.match(/ElMessage\.warning\([\s\S]*?\)/)
    if (emInOnError) {
      assert.doesNotMatch(emInOnError[0], /\$\{/, 'SSE onError 的 ElMessage 不应使用模板插值')
    }
  })
})

/* ══════════════════════════════════════════════════════════════
 * Group 4: R-X6 运行时验证 — useSseStream 重连退避（esbuild + mock fetch）
 * ══════════════════════════════════════════════════════════════ */
describe('R-X6 · useSseStream 重连退避运行时验证（esbuild bundle）', () => {
  let streamModule

  before(async () => {
    // 编译 useSseStream + useJwtAuth
    await build({
      entryPoints: [
        join(SRC, 'composables/useSseStream.ts'),
        join(SRC, 'composables/useJwtAuth.ts'),
      ],
      outdir: TMP,
      outbase: SRC,
      bundle: true,
      format: 'esm',
      platform: 'node',
      target: 'node20',
      external: ['vue', 'pinia'],
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

    // Vue / window mock（useSseStream 用 onUnmounted / getCurrentInstance）
    if (!globalThis.localStorage) {
      globalThis.localStorage = {
        getItem() { return null }, setItem() {}, removeItem() {}, clear() {},
        key() { return null }, get length() { return 0 },
      }
    }
    if (!globalThis.window) {
      globalThis.window = {
        __test__: true,
        location: { href: 'http://localhost', origin: 'http://localhost', protocol: 'http:', host: 'localhost', hostname: 'localhost' },
        localStorage: globalThis.localStorage,
        addEventListener() {}, removeEventListener() {},
      }
    }
    if (!globalThis.document) {
      globalThis.document = {
        documentElement: { setAttribute() {}, getAttribute() { return null } },
        createElement() {
          return {
            tagName: 'DIV', style: {},
            appendChild() {}, removeChild() {},
            addEventListener() {}, removeEventListener() {},
          }
        },
        querySelector() { return null }, querySelectorAll() { return [] },
        addEventListener() {}, removeEventListener() {},
      }
    }
    streamModule = await import(`${pathToFileURL(TMP)}/composables/${'useSseStream.js'}`)
  })

  wrap('S10 fetch 连续失败触发重连 + retryAttempt 自增', async () => {
    const origFetch = globalThis.fetch
    let attempt = 0
    globalThis.fetch = async () => {
      attempt++
      throw new Error(`ECONNRESET #${attempt}`)
    }

    const events = []
    let errored = false
    const stream = streamModule.useSseStream({
      url: '/api/sse-test-safety',
      retryDelaysMs: [10, 10, 10], // 加速测试
      heartbeatTimeoutMs: 60000,
      onEvent: (e) => events.push(e),
      onError: () => { errored = true },
    })

    // 给重连退避一些时间
    await new Promise((r) => setTimeout(r, 80))
    stream.disconnect()

    assert.equal(errored, true, '至少应触发 1 次 onError（fetch 失败）')
    assert.ok(attempt >= 2, `应至少 2 次重连尝试（实际 ${attempt}）`)
    assert.equal(stream.state.value, 'closed', 'disconnect 后 state 应 closed')
    assert.ok(
      stream.retryAttempt.value >= 1,
      `retryAttempt 应自增（got ${stream.retryAttempt.value}）`,
    )

    globalThis.fetch = origFetch
  })

  wrap('S10.1 DEFAULT_RETRY_DELAYS_MS = [1000, 5000, 15000, 30000] + heartbeat 30s', () => {
    // T01 实现的导出常量，ChatView 应复用之
    const expected = [1000, 5000, 15000, 30000]
    assert.deepEqual(
      [...streamModule.DEFAULT_RETRY_DELAYS_MS],
      expected,
      `DEFAULT_RETRY_DELAYS_MS 应为 ${expected.join('ms / ')}ms`,
    )
    assert.equal(streamModule.DEFAULT_HEARTBEAT_TIMEOUT_MS, 30000,
      'DEFAULT_HEARTBEAT_TIMEOUT_MS 应为 30s')
  })
})

/* ────────────────────────────────────────────────────────────
 * 总结 + 清理
 * ──────────────────────────────────────────────────────────── */
process.on('exit', () => {
  process.stderr.write(`\n\x1b[1m── T07 safety patch (R-X5 + R-X6) test summary ──\x1b[0m\n`)
  process.stderr.write(`  Pass: \x1b[32m${pass}\x1b[0m\n  Fail: \x1b[31m${fail}\x1b[0m\n`)
  if (failures.length) {
    process.stderr.write(`\n  Failures:\n`)
    for (const f of failures) {
      process.stderr.write(`    - ${f.name}\n      ${f.error?.message ?? f.error}\n`)
    }
  }
  try {
    const { rmSync } = require('node:fs')
    rmSync(TMP, { recursive: true, force: true })
  } catch { /* ignore */ }
})
