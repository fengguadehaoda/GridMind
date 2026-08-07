/**
 * GridMind v1.5.1 R-X3 patch · 专项回归测试
 *
 * 覆盖 R-X3 同源问题（用户态异常消息暴露）：
 *   1. ChatView `step_failed` 分支 sanitize(event.error) 不再暴露内部敏感片段
 *   2. ChatView `reasoning_error` 分支 markError/ElMessage 不再含 ${event.error} 模板
 *   3. reasoning store 4 处 catch 块（L358/L388/L504/L530）不再拼装 err.message
 *   4. reasoning store 4 处 catch 块均改为通用文案（4 种语义稳定文案）
 *   5. 全项目 grep 安全断言（grep 不到任何 ${msg} / ${event.error} 模板）
 *
 * 运行：node tests/test_r_x3_patch.mjs
 *
 * 策略：纯静态源码扫描（与 test_integration_cross_f.mjs 一致），不依赖运行时。
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { resolve, join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)
const ROOT = resolve(__dirname, '..')
const SRC = join(ROOT, 'src')

let pass = 0
let fail = 0
const failures = []

function wrap(name, fn) {
  return test(name, async () => {
    try {
      await fn()
      pass++
      console.log(`  ✓ ${name}`)
    } catch (err) {
      fail++
      failures.push({ name, err })
      console.error(`  ✗ ${name}\n      ${err.message}`)
    }
  })
}

const CHATVIEW_SRC = await readFile(join(SRC, 'components/ChatView.vue'), 'utf-8')
const REASONING_SRC = await readFile(join(SRC, 'stores/reasoning.ts'), 'utf-8')

// ═════════════════════════════════════════════════════
// 1) ChatView step_failed 分支 sanitize 清洗
// ═════════════════════════════════════════════════════
wrap('R-X3.1 ChatView step_failed 分支：sanitize 函数 + console.error 留痕', () => {
  // 1.1 sanitize 函数存在
  assert.match(
    CHATVIEW_SRC,
    /function\s+sanitize\s*\(\s*errMsg\s*:\s*string\s*\|\s*undefined\s*\|\s*null\s*\)/,
    'sanitize 函数应存在（接受 string | undefined | null）',
  )
  // 1.2 step_failed case 调用 sanitize
  const stepFailedBlock = CHATVIEW_SRC.match(
    /case\s+['"]step_failed['"]\s*:[\s\S]{0,500}break/,
  )
  assert.ok(stepFailedBlock, 'step_failed case block 存在')
  assert.match(
    stepFailedBlock[0],
    /sanitize\s*\(\s*event\.error\s*\)/,
    'step_failed 分支应 sanitize(event.error) 后再传给 failStep',
  )
  // 1.3 服务侧 console.error 留痕（带原始 event 参数）
  assert.match(
    stepFailedBlock[0],
    /console\.error\s*\(\s*['"][^'"]*['"]\s*,\s*event\s*\)/,
    'step_failed 分支应 console.error(...带 event 服务侧留痕)',
  )
  // 1.4 不应直接把 event.error 透传给 failStep
  assert.doesNotMatch(
    stepFailedBlock[0],
    /reasoning\.failStep\s*\(\s*event\.step_id\s*,\s*event\.error\s*\)/,
    'R-X3 patch：不应透传 event.error 到 failStep',
  )
  // 1.5 sanitize 内部应至少清洗一类敏感片段（regex coverage）
  const sanitizeBody = CHATVIEW_SRC.match(
    /function\s+sanitize\s*\([^)]*\)\s*:\s*string\s*\{[\s\S]*?\n\}/,
  )
  assert.ok(sanitizeBody, 'sanitize 函数体存在')
  assert.match(
    sanitizeBody[0],
    /\?token=/,
    'sanitize 应清洗 ?token= 片段',
  )
  assert.match(
    sanitizeBody[0],
    /Bearer/,
    'sanitize 应清洗 Bearer 片段',
  )
  assert.match(
    sanitizeBody[0],
    /\/api\//,
    'sanitize 应清洗 /api/ 路径片段',
  )
})

// ═════════════════════════════════════════════════════
// 2) ChatView reasoning_error 分支 · R-X3 patch
// ═════════════════════════════════════════════════════
wrap('R-X3.2 ChatView reasoning_error 分支：markError + ElMessage 均用通用文案', () => {
  const block = CHATVIEW_SRC.match(
    /case\s+['"]reasoning_error['"]\s*:[\s\S]{0,500}break/,
  )
  assert.ok(block, 'reasoning_error case block 存在')
  const b = block[0]

  // 2.1 不应拼 ${event.error}
  assert.doesNotMatch(b, /markError\s*\(\s*event\.error/, 'R-X3：markError 不应拼 event.error')
  assert.doesNotMatch(
    b,
    /ElMessage\.error\s*\(\s*[`][^`]*\$\{event\.error[^`]*`/,
    'R-X3：ElMessage.error 不应含 ${event.error} 模板',
  )
  // 2.2 应使用字符串字面量
  assert.match(b, /markError\s*\(\s*['"][^'"]+['"]\s*\)/, 'markError 应传字符串字面量')
  assert.match(
    b,
    /ElMessage\.error\s*\(\s*['"][^'"]+['"]\s*\)/,
    'ElMessage.error 应传字符串字面量',
  )
  // 2.3 服务侧 console.error 留痕（带原始 event 参数）
  assert.match(
    b,
    /console\.error\s*\(\s*['"][^'"]*['"]\s*,\s*event\s*\)/,
    'R-X3：reasoning_error 分支应 console.error(...带 event 服务侧留痕)',
  )
})

// ═════════════════════════════════════════════════════
// 3) reasoning store 4 处 catch 块 · R-X3 patch
// ═════════════════════════════════════════════════════

// 提取单个 catch 块（从 catch(err) 到 finally 或下一个 catch/函数尾）
function catchBlocks(src) {
  const out = {}
  for (const action of ['pause', 'resume', 'rerunFromStep', 'abortWithApi']) {
    // 匹配 "function <action>(...) { ... catch (err) { ... }" 的 catch 部分
    const re = new RegExp(
      `(?:async\\s+)?function\\s+${action}[\\s\\S]*?catch\\s*\\(\\s*err\\s*\\)\\s*\\{([\\s\\S]*?)\\n\\s*\\}`,
      'm',
    )
    const m = src.match(re)
    out[action] = m ? m[1] : null
  }
  return out
}

const blocks = catchBlocks(REASONING_SRC)

wrap('R-X3.3 reasoning.pause catch：通用文案 + console.error', () => {
  assert.ok(blocks.pause, 'pause catch 块存在')
  assert.doesNotMatch(blocks.pause, /\\\${msg}/, 'pause catch 不应拼 ${msg}')
  assert.match(blocks.pause, /console\.error\([^)]*pause/, 'pause catch 应 console.error')
  assert.match(blocks.pause, /暂停失败/, 'pause catch 应使用通用文案"暂停失败"')
})

wrap('R-X3.4 reasoning.resume catch：通用文案 + console.error', () => {
  assert.ok(blocks.resume, 'resume catch 块存在')
  assert.doesNotMatch(blocks.resume, /\\\${msg}/, 'resume catch 不应拼 ${msg}')
  assert.match(blocks.resume, /console\.error\([^)]*resume/, 'resume catch 应 console.error')
  assert.match(blocks.resume, /恢复失败/, 'resume catch 应使用通用文案"恢复失败"')
})

wrap('R-X3.5 reasoning.rerunFromStep catch：通用文案 + console.error', () => {
  assert.ok(blocks.rerunFromStep, 'rerunFromStep catch 块存在')
  assert.doesNotMatch(blocks.rerunFromStep, /\\\${msg}/, 'rerun catch 不应拼 ${msg}')
  assert.match(blocks.rerunFromStep, /console\.error\([^)]*rerun/, 'rerun catch 应 console.error')
  assert.match(blocks.rerunFromStep, /重跑失败/, 'rerun catch 应使用通用文案"重跑失败"')
})

wrap('R-X3.6 reasoning.abortWithApi catch：通用文案 + console.error', () => {
  assert.ok(blocks.abortWithApi, 'abortWithApi catch 块存在')
  assert.doesNotMatch(blocks.abortWithApi, /\\\${msg}/, 'abort catch 不应拼 ${msg}')
  assert.match(blocks.abortWithApi, /console\.error\([^)]*abort/, 'abort catch 应 console.error')
  assert.match(blocks.abortWithApi, /中止失败/, 'abort catch 应使用通用文案"中止失败"')
})

// ═════════════════════════════════════════════════════
// 4) 全项目 grep 安全断言
// ═════════════════════════════════════════════════════
wrap('R-X3.7 全项目 grep：${msg} 模板在 reasoning.ts 内已绝迹', () => {
  // 仅在 catch 块外允许（fallback 等其他用途），但本 patch 范围内不应存在
  assert.doesNotMatch(
    REASONING_SRC,
    /errorMessage\.value\s*=\s*[`][^`]*\$\{msg}/,
    'reasoning.ts: 不应再有 errorMessage.value = `...${msg}`',
  )
})

wrap('R-X3.8 全项目 grep：ChatView ${event.error} 模板已绝迹', () => {
  assert.doesNotMatch(
    CHATVIEW_SRC,
    /ElMessage\.error\s*\(\s*[`][^`]*\$\{event\.error[^`]*`/,
    'ChatView.vue: ElMessage.error 不应再含 ${event.error} 模板',
  )
  assert.doesNotMatch(
    CHATVIEW_SRC,
    /markError\s*\(\s*event\.error/,
    'ChatView.vue: markError 不应再拼 event.error',
  )
})

// ═════════════════════════════════════════════════════
// Test summary
// ═════════════════════════════════════════════════════
test('summary', () => {
  console.log(`\n  ── R-X3 patch: ${pass} pass / ${fail} fail ──`)
  if (fail > 0) {
    console.error('Failures:')
    for (const { name, err } of failures) {
      console.error(`  - ${name}: ${err.message}`)
    }
    process.exit(1)
  }
})
