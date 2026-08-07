/**
 * GridMind v1.5.1 T02 单元测试 · ReasoningControlBar + ReasoningStatusBadge
 *
 * 覆盖场景（≥8 满足 T02 要求 + 防御性回归测试）：
 *
 *   ReasoningStatusBadge（3）：
 *     1. 8 状态 keys 全部映射
 *     2. "accent" 业务 tone → "primary" Element Plus type（EP 不支持 accent）
 *     3. el-tag 含 aria-label（a11y）
 *
 *   ReasoningControlBar 模板（5）：
 *     4. region 角色 + 中文 aria-label（架构 §6.3 a11y）
 *     5. 步骤计数 aria-live="polite"（屏幕阅读器友好）
 *     6. 暂停按钮 v-if="showPauseButton" + aria-label + data-action
 *     7. 继续按钮 v-if="showResumeButton" + aria-label + data-action
 *     8. 中止按钮 v-if="showAbortButton" + aria-label + data-action
 *
 *   ReasoningControlBar 行为（≥3）：
 *     9. 暂停 → 调 reasoning.pause('user_requested')
 *    10. 恢复 → 调 reasoning.resume()
 *    11. 中止 → 调 ElMessageBox.confirm + 取消/关闭 → no-op
 *    12. 中止确认 → 调 reasoning.abortWithApi('user_aborted')
 *
 *   步骤计数 computed（1）：
 *    13. 格式 "completed / total 步"
 *
 * 运行：node tests/test_reasoning_control_bar.mjs
 *
 * 策略：静态源码分析（regex / includes）。理由：
 *   - 与现有 test_runner.mjs / test_reasoning_store.mjs 一致
 *   - 不依赖 vue-test-utils / @vue/runtime-dom 完整栈（环境已 mock 复杂）
 *   - 模板结构 / 事件绑定 / a11y 属性是 .vue 文件的不变量，源码即契约
 *   - 行为正确性由 build (vue-tsc) + reasoning store 单元测试（test_reasoning_store）保障
 */
import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { resolve, join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)
const ROOT = resolve(__dirname, '..')
const SRC = join(ROOT, 'src')

// ─── 读源文件 ───
const BADGE_SRC = await readFile(
  join(SRC, 'components/reasoning/ReasoningStatusBadge.vue'),
  'utf-8',
)
const BAR_SRC = await readFile(
  join(SRC, 'components/reasoning/ReasoningControlBar.vue'),
  'utf-8',
)

// ─── 总结 ───
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

// ═══════════════════════════════════════════════════════════════
// Group 1: ReasoningStatusBadge · 8 状态徽标
// ═══════════════════════════════════════════════════════════════
describe('ReasoningStatusBadge (8 状态徽标子组件)', () => {
  wrap('1) 8 状态 keys 全部映射（idle / running / paused / editing / resuming / completed / error / aborted）', () => {
    const expected = [
      'idle',
      'running',
      'paused',
      'editing',
      'resuming',
      'completed',
      'error',
      'aborted',
    ]
    for (const s of expected) {
      // 形如 "idle:" 或 "'idle':" 都算
      const re = new RegExp(`['"]?${s}['"]?\\s*:`)
      assert.ok(re.test(BADGE_SRC), `应包含状态 key '${s}'`)
    }
  })

  wrap('2) 8 状态对应中文标签（业务可读性）', () => {
    const expectedLabels = [
      ['idle', '空闲'],
      ['running', '推理中'],
      ['paused', '已暂停'],
      ['editing', '编辑中'],
      ['resuming', '恢复中'],
      ['completed', '已完成'],
      ['error', '错误'],
      ['aborted', '已中止'],
    ]
    for (const [status, label] of expectedLabels) {
      // 状态 key 后面 200 字符内应出现中文 label
      const idx = BADGE_SRC.indexOf(`${status}:`)
      assert.ok(idx > 0, `应包含状态 '${status}'`)
      const snippet = BADGE_SRC.slice(idx, idx + 200)
      assert.ok(
        snippet.includes(`label: '${label}'`),
        `'${status}' 的 label 应为 '${label}'（实际片段: ${snippet.slice(0, 120)}...）`,
      )
    }
  })

  wrap('3) "accent" tone → Element Plus "primary" type（EP 5 个合法 type：primary/success/warning/info/danger）', () => {
    // EL_TAG_TYPE_MAP 应包含 accent → primary
    assert.match(
      BADGE_SRC,
      /accent\s*:\s*['"]primary['"]/,
      'accent tone 必须映射到 el-tag 的 primary type',
    )
    // 5 个映射键全有（assert.match 参数顺序：string 在前，regexp 在后）
    for (const k of ['info', 'success', 'warning', 'danger', 'accent']) {
      assert.match(
        BADGE_SRC,
        new RegExp(`${k}\\s*:\\s*['"](?:primary|success|warning|info|danger)['"]`),
        `EL_TAG_TYPE_MAP.${k} 应映射到合法 EP type`,
      )
    }
  })

  wrap('4) el-tag 含 aria-label 属性（a11y：屏幕阅读器友好）', () => {
    assert.match(BADGE_SRC, /aria-label=/, 'el-tag 应绑定 aria-label')
    // 应包含中文 "推理状态"
    assert.match(BADGE_SRC, /推理状态/, 'aria-label 文本应包含"推理状态"')
  })

  wrap('5) el-tag 包含 :data-status 属性（测试 hook + 视觉回归）', () => {
    assert.match(BADGE_SRC, /:data-status="status"/, '应绑定 :data-status 用于 E2E 选择器')
    assert.match(BADGE_SRC, /data-component="reasoning-status-badge"/, '应有 data-component 标识')
  })
})

// ═══════════════════════════════════════════════════════════════
// Group 2: ReasoningControlBar · 模板（a11y + 按钮显示规则）
// ═══════════════════════════════════════════════════════════════
describe('ReasoningControlBar (F1 主组件 · 模板)', () => {
  wrap('6) 顶层 region 角色 + 中文 aria-label（架构 §6.3 a11y）', () => {
    assert.match(
      BAR_SRC,
      /role="region"/,
      '顶层 div 应有 role="region"',
    )
    assert.match(
      BAR_SRC,
      /aria-label="推理控制栏"/,
      '顶层 div 应有 aria-label="推理控制栏"',
    )
  })

  wrap('7) 步骤计数 aria-live="polite" + aria-atomic（屏幕阅读器完整播报）', () => {
    assert.match(
      BAR_SRC,
      /aria-live="polite"/,
      'step-counter 应有 aria-live="polite"',
    )
    assert.match(
      BAR_SRC,
      /aria-atomic="true"/,
      'step-counter 应有 aria-atomic="true"（避免部分播报）',
    )
  })

  wrap('8) 暂停按钮 v-if="showPauseButton" + aria-label="暂停推理" + data-action="pause"', () => {
    assert.match(
      BAR_SRC,
      /v-if="showPauseButton"/,
      '暂停按钮应有 v-if="showPauseButton"',
    )
    assert.match(
      BAR_SRC,
      /aria-label="暂停推理"/,
      '暂停按钮应有独立 aria-label',
    )
    assert.match(
      BAR_SRC,
      /data-action="pause"/,
      '暂停按钮应有 data-action="pause"（E2E 选择器）',
    )
    // 主理人决策 7.3：暂停按钮在 running 之外不应出现
    assert.match(
      BAR_SRC,
      /showPauseButton\s*=\s*computed\(\(\)\s*=>\s*reasoning\.status\s*===\s*['"]running['"]\s*\)/,
      'showPauseButton computed 必须仅在 status === "running" 时为 true',
    )
  })

  wrap('9) 继续按钮 v-if="showResumeButton" + aria-label="继续推理" + data-action="resume"', () => {
    assert.match(
      BAR_SRC,
      /v-if="showResumeButton"/,
      '继续按钮应有 v-if="showResumeButton"',
    )
    assert.match(
      BAR_SRC,
      /aria-label="继续推理"/,
      '继续按钮应有独立 aria-label',
    )
    assert.match(
      BAR_SRC,
      /data-action="resume"/,
      '继续按钮应有 data-action="resume"',
    )
    assert.match(
      BAR_SRC,
      /showResumeButton\s*=\s*computed\(\(\)\s*=>\s*reasoning\.status\s*===\s*['"]paused['"]\s*\)/,
      'showResumeButton computed 必须仅在 status === "paused" 时为 true',
    )
  })

  wrap('10) 中止按钮 v-if="showAbortButton" + aria-label="中止推理" + data-action="abort"', () => {
    assert.match(
      BAR_SRC,
      /v-if="showAbortButton"/,
      '中止按钮应有 v-if="showAbortButton"',
    )
    assert.match(
      BAR_SRC,
      /aria-label="中止推理"/,
      '中止按钮应有独立 aria-label',
    )
    assert.match(
      BAR_SRC,
      /data-action="abort"/,
      '中止按钮应有 data-action="abort"',
    )
    // 中止按钮在 3 个 active 态都显示（running / paused / resuming）
    // 宽松匹配：computed body 可能跨行
    assert.match(
      BAR_SRC,
      /showAbortButton\s*=\s*computed\(\(\)\s*=>\s*\[[^\]]*['"]running['"][^\]]*['"]paused['"][^\]]*['"]resuming['"][^\]]*\]\.includes\(reasoning\.status\)/s,
      'showAbortButton computed 必须在 3 个 active 态（running/paused/resuming）为 true',
    )
  })
})

// ═══════════════════════════════════════════════════════════════
// Group 3: ReasoningControlBar · 行为（事件处理函数）
// ═══════════════════════════════════════════════════════════════
describe('ReasoningControlBar (F1 主组件 · 行为)', () => {
  wrap('11) handlePause 调用 reasoning.pause(\'user_requested\') + 错误处理', () => {
    assert.match(
      BAR_SRC,
      /await\s+reasoning\.pause\(\s*['"]user_requested['"]\s*\)/,
      'handlePause 应 await reasoning.pause("user_requested")',
    )
    // 成功 / 失败两条提示
    assert.match(BAR_SRC, /ElMessage\.success\(['"]已请求暂停推理['"]\)/, '成功应 ElMessage.success')
    // R-X5 修复后：失败提示用通用文案（字符串字面量），不再用 `${msg}` 模板拼接内部异常
    assert.match(
      BAR_SRC,
      /ElMessage\.error\(['"]暂停失败/,
      'R-X5：失败应 ElMessage.error 含通用文案"暂停失败"',
    )
    assert.doesNotMatch(
      BAR_SRC,
      /ElMessage\.error\(`暂停失败/,
      'R-X5：禁止用模板字面量 `${msg}` 暴露内部异常（路径/token/错误对象）',
    )
  })

  wrap('12) handleResume 调用 reasoning.resume() + 错误处理', () => {
    assert.match(
      BAR_SRC,
      /await\s+reasoning\.resume\(\s*\)/,
      'handleResume 应 await reasoning.resume()',
    )
    assert.match(BAR_SRC, /ElMessage\.success\(['"]已恢复推理['"]\)/, '成功应 ElMessage.success')
    // R-X5 修复后：失败提示用通用文案（字符串字面量），不再用 `${msg}` 模板拼接内部异常
    assert.match(
      BAR_SRC,
      /ElMessage\.error\(['"]恢复失败/,
      'R-X5：失败应 ElMessage.error 含通用文案"恢复失败"',
    )
    assert.doesNotMatch(
      BAR_SRC,
      /ElMessage\.error\(`恢复失败/,
      'R-X5：禁止用模板字面量 `${msg}` 暴露内部异常（路径/token/错误对象）',
    )
  })

  wrap('13) handleAbort 显示 ElMessageBox.confirm 二次确认 + 取消/关闭 no-op', () => {
    // 二次确认
    assert.match(
      BAR_SRC,
      /await\s+ElMessageBox\.confirm\(/,
      'handleAbort 应先 await ElMessageBox.confirm',
    )
    // 取消/关闭 no-op
    assert.match(
      BAR_SRC,
      /if\s*\(\s*e\s*===\s*['"]cancel['"]\s*\|\|\s*e\s*===\s*['"]close['"]\s*\)\s*return/,
      '取消/关闭时必须 no-op（不调 abortWithApi）',
    )
  })

  wrap('14) handleAbort 确认后调用 reasoning.abortWithApi(\'user_aborted\')', () => {
    assert.match(
      BAR_SRC,
      /await\s+reasoning\.abortWithApi\(\s*['"]user_aborted['"]\s*\)/,
      '确认后应调 reasoning.abortWithApi("user_aborted")',
    )
  })

  wrap('15) 步骤计数 computed 格式 "${completed} / ${total} 步"', () => {
    // stepCounter computed 实现
    const re = /stepCounter\s*=\s*computed\(\(\)\s*=>\s*\{[\s\S]*?\$\{completed\}\s*\/\s*\$\{total\}[\s\S]*?\}\s*\)/
    assert.match(BAR_SRC, re, 'stepCounter computed 应基于 completed/total 渲染"X / Y 步"')
    // template 中应绑定
    assert.match(BAR_SRC, /\{\{\s*stepCounter\s*\}\}/, '模板应绑定 stepCounter')
  })

  wrap('16) 导入 useReasoningStore 和 ReasoningStatusBadge（依赖正确）', () => {
    assert.match(
      BAR_SRC,
      /import\s*\{[^}]*useReasoningStore[^}]*\}\s*from\s*['"]@\/stores\/reasoning['"]/,
      '应导入 useReasoningStore',
    )
    assert.match(
      BAR_SRC,
      /import\s+ReasoningStatusBadge\s+from\s+['"]\.\/ReasoningStatusBadge\.vue['"]/,
      '应导入 ReasoningStatusBadge',
    )
  })

  wrap('17) pending 状态用于按钮 :loading + :disabled（防双击）', () => {
    // pendingPause / pendingResume / pendingAbort 三个 ref 都有
    assert.match(BAR_SRC, /reasoning\.pendingPause/, '应使用 reasoning.pendingPause')
    assert.match(BAR_SRC, /reasoning\.pendingResume/, '应使用 reasoning.pendingResume')
    assert.match(BAR_SRC, /reasoning\.pendingAbort/, '应使用 reasoning.pendingAbort')
    // :loading 与 :disabled 都绑定
    assert.match(BAR_SRC, /:loading="reasoning\.pendingPause"/, '暂停按钮 :loading')
    assert.match(BAR_SRC, /:loading="reasoning\.pendingResume"/, '继续按钮 :loading')
    assert.match(BAR_SRC, /:loading="reasoning\.pendingAbort"/, '中止按钮 :loading')
  })
})

// ─── 总结 ───
process.on('exit', () => {
  process.stderr.write(`\n\x1b[1m── T02 reasoning control bar test summary ──\x1b[0m\n`)
  process.stderr.write(`  Pass: \x1b[32m${pass}\x1b[0m\n  Fail: \x1b[31m${fail}\x1b[0m\n`)
  if (failures.length) {
    process.stderr.write(`\n  Failures:\n`)
    for (const f of failures) {
      process.stderr.write(`    - ${f.name}\n      ${f.error?.message ?? f.error}\n`)
    }
  }
})
