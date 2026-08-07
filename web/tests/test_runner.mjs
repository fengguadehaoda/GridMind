/**
 * Lightweight test harness for GridMind v1.5.0 (T05 QA)
 * - 自研，不依赖 vitest / jest（沙箱无 test runner）
 * - 编译策略：esbuild 转译 .ts → 写入临时目录 → 动态 import
 * - Mock：localStorage / window / document / pinia createPinia
 *
 * 用法：
 *   node tests/test_runner.mjs
 */
import { test, describe, before, after, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { build } from 'esbuild'
import { mkdtemp, rm, writeFile, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, dirname, resolve, basename, relative } from 'node:path'
import { pathToFileURL } from 'node:url'
import { createServer } from 'node:http'
import { existsSync } from 'node:fs'

// ─── 路径配置 ────────────────────────────────────
const ROOT = resolve(import.meta.dirname, '..')
const SRC = join(ROOT, 'src')
const TMP = await mkdtemp(join(tmpdir(), 'gridmind-test-'))

// ─── 简单的 log 工具 ─────────────────────────────
const PASS = '\x1b[32m✓\x1b[0m'
const FAIL = '\x1b[31m✗\x1b[0m'
let passedCount = 0
let failedCount = 0
const failedTests = []

process.on('test:pass', () => { passedCount++ })
process.on('test:fail', (e) => {
  failedCount++
  failedTests.push(e)
})

// ─── 用 esbuild 打包指定 src 目录到 tmp ──────────────
async function compileFiles(entries) {
  const arr = Array.isArray(entries) ? entries : [entries]
  await build({
    entryPoints: arr,
    outdir: TMP,
    bundle: true,
    format: 'esm',
    platform: 'node',
    target: 'node20',
    external: ['pinia', 'vue'],
    alias: {
      '@': SRC,
    },
    loader: { '.ts': 'ts', '.vue': 'ts', '.js': 'js' },
    logLevel: 'silent',
    write: true,
  })
  // 在 tmp 下创建 node_modules 软链，让 import 'pinia'/'vue' 找得到
  const { symlinkSync, existsSync } = await import('node:fs')
  const nmTarget = join(ROOT, 'node_modules')
  const nmLink = join(TMP, 'node_modules')
  if (!existsSync(nmLink)) {
    try {
      symlinkSync(nmTarget, nmLink, 'junction')
    } catch {
      // 软链失败：fallback - 使用绝对路径 in-place via import.meta.resolve
    }
  }
}

// ─── 全局 mock：localStorage / window / document / pinia ──

// 简易 localStorage mock（每次 beforeEach 清空；保留初始值）
let localStorageData = {}
globalThis.localStorage = {
  getItem(k) { return Object.hasOwn(localStorageData, k) ? localStorageData[k] : null },
  setItem(k, v) { localStorageData[k] = String(v) },
  removeItem(k) { delete localStorageData[k] },
  clear() { localStorageData = {} },
  key(i) { return Object.keys(localStorageData)[i] ?? null },
  get length() { return Object.keys(localStorageData).length },
}

// 简易 document mock（让 :root setAttribute 不崩）
let dataAttrs = {}
globalThis.document = {
  documentElement: {
    setAttribute(k, v) { dataAttrs[k] = String(v) },
    getAttribute(k) { return dataAttrs[k] ?? null },
  },
}

globalThis.window = { __test__: true }

// 简易 pinia mock（每 createPinia() 返回新单例，不复用 store）
// 由于测试需要隔离 store，每次 import store 后 createPinia 一次。
// 但我们不直接用 pinia 实例 —— 改用 storeToRefs 风格的"获取"接口。
// 这里改为：每个测试 import store 后用 setActivePinia(createPinia()) 隔离。
import { setActivePinia, createPinia } from 'pinia'

// ─── 测试套件 ─────────────────────────────────────

// 顶层提前读取所有静态分析所需的源码（必须在 describe 之前）
const pulseDotSrc = await readFile(join(SRC, 'components/background/PulseDot.vue'), 'utf-8')
const statusIconSrc = await readFile(join(SRC, 'components/controls/StatusIcon.vue'), 'utf-8')
const themeTypesSrc = await readFile(join(SRC, 'types/theme.ts'), 'utf-8')
const tokensSrc = await readFile(join(SRC, 'styles/tokens.scss'), 'utf-8')
const darkSrc = await readFile(join(SRC, 'styles/tokens.dark.scss'), 'utf-8')
const lightSrc = await readFile(join(SRC, 'styles/tokens.light.scss'), 'utf-8')
const routerSrc = await readFile(join(SRC, 'router/index.ts'), 'utf-8')
const tourSrc = await readFile(join(SRC, 'components/onboarding/OnboardingTour.vue'), 'utf-8')
const triggerSrc = await readFile(join(SRC, 'components/controls/OnboardingTrigger.vue'), 'utf-8')
const appSrc = await readFile(join(SRC, 'App.vue'), 'utf-8')

// 顶层编译所有需要的源文件到 TMP（一次编译，所有 describe 共享）
// 解决之前 beforeExit 清理 TMP 导致后续 suite 找不到模块的问题
await compileFiles([
  join(SRC, 'stores/display.ts'),
  join(SRC, 'stores/onboarding.ts'),
  join(SRC, 'composables/useDisplay.ts'),
  join(SRC, 'composables/useOnboarding.ts'),
])

console.log(`\n\x1b[1mGridMind v1.5.0 T05 QA Test Runner\x1b[0m`)
console.log(`  tmp: ${TMP}\n`)

let totalPass = 0
let totalFail = 0
const testFailures = []

// 包装 node:test 的 test() 以汇总结果
function wrapTest(name, fn) {
  return test(name, async (t) => {
    try {
      await fn(t)
      totalPass++
    } catch (e) {
      totalFail++
      testFailures.push({ name, error: e })
      throw e
    }
  })
}

// ═══════════════════════════════════════════════════════
// 测试 1: display store hydrate round-trip
// ═══════════════════════════════════════════════════════
describe('display store (hydrate round-trip)', () => {
  let displayStore

  beforeEach(() => {
    // 每次测试前：清空 localStorage + 切换 pinia 实例
    globalThis.localStorage.clear()
    dataAttrs = {}
    setActivePinia(createPinia())
    // 重新 import store（esbuild 输出 = 保持原目录结构：stores/display.js）
    return import(`${pathToFileURL(TMP)}/stores/${'display.js'}`).then(m => {
      displayStore = m.useDisplayStore
    })
  })

  wrapTest('defaults: displayMode=standard, colorBlind=default, bgIntensity=off', () => {
    const s = displayStore()
    assert.equal(s.displayMode, 'standard', 'displayMode default should be standard')
    assert.equal(s.colorBlind, 'default', 'colorBlind default should be default')
    assert.equal(s.bgIntensity, 'off', 'bgIntensity default should be off')
    // pinia auto-unwraps refs when accessed on store proxy
    assert.equal(s.isStandard, true, 'isStandard should be true')
    assert.equal(s.isPresentation, false, 'isPresentation should be false')
    assert.equal(s.isColorBlindActive, false, 'isColorBlindActive should be false')
  })

  wrapTest('hydrate: read valid displayMode + colorBlind from localStorage', () => {
    localStorage.setItem('gridmind.displayMode', 'presentation')
    localStorage.setItem('gridmind.colorBlindPalette', 'okabe-ito')
    const s = displayStore()
    s.hydrate()
    assert.equal(s.displayMode, 'presentation', 'displayMode hydrated to presentation')
    assert.equal(s.colorBlind, 'okabe-ito', 'colorBlind hydrated to okabe-ito')
    assert.equal(s.bgIntensity, 'high', 'bgIntensity should be high when presentation')
  })

  wrapTest('hydrate: ignore invalid colorBlind value (fallback to default)', () => {
    localStorage.setItem('gridmind.colorBlindPalette', 'invalid-palette')
    const s = displayStore()
    s.hydrate()
    assert.equal(s.colorBlind, 'default', 'invalid palette should fall back to default')
  })

  wrapTest('hydrate: ignore invalid displayMode value (fallback to standard)', () => {
    localStorage.setItem('gridmind.displayMode', 'bogus')
    const s = displayStore()
    s.hydrate()
    assert.equal(s.displayMode, 'standard', 'invalid mode should fall back to standard')
  })

  wrapTest('setDisplayMode(presentation): persist + applyAttrs + recompute bgIntensity', () => {
    const s = displayStore()
    s.hydrate()
    s.setDisplayMode('presentation')
    assert.equal(s.displayMode, 'presentation')
    assert.equal(s.bgIntensity, 'high')
    assert.equal(localStorage.getItem('gridmind.displayMode'), 'presentation')
    assert.equal(dataAttrs['data-display-mode'], 'presentation')
  })

  wrapTest('setColorBlindPalette(okabe-ito): persist + applyAttrs', () => {
    const s = displayStore()
    s.hydrate()
    s.setColorBlindPalette('okabe-ito')
    assert.equal(s.colorBlind, 'okabe-ito')
    // pinia auto-unwraps refs
    assert.equal(s.isColorBlindActive, true)
    assert.equal(localStorage.getItem('gridmind.colorBlindPalette'), 'okabe-ito')
    assert.equal(dataAttrs['data-cb-palette'], 'okabe-ito')
  })

  wrapTest('all 4 valid palettes accepted by setColorBlindPalette', () => {
    const s = displayStore()
    s.hydrate()
    const palettes = ['default', 'ibm-cb-safe', 'okabe-ito', 'colorbrewer-rdylbu']
    for (const p of palettes) {
      s.setColorBlindPalette(p)
      assert.equal(s.colorBlind, p, `${p} should be set`)
      assert.equal(dataAttrs['data-cb-palette'], p, `data-cb-palette should be ${p}`)
    }
  })

  wrapTest('round-trip: set → persist → new store → hydrate → same values', () => {
    // Phase 1: 写
    const s1 = displayStore()
    s1.hydrate()
    s1.setDisplayMode('presentation')
    s1.setColorBlindPalette('colorbrewer-rdylbu')

    // Phase 2: 模拟"刷新页面" —— 新建 store + 重 hydrate
    setActivePinia(createPinia())
    const s2 = displayStore()
    s2.hydrate()
    assert.equal(s2.displayMode, 'presentation', 'after reload, displayMode persists')
    assert.equal(s2.colorBlind, 'colorbrewer-rdylbu', 'after reload, colorBlind persists')
    assert.equal(s2.bgIntensity, 'high', 'after reload, bgIntensity derived correctly')
  })

  wrapTest('localStorage write failure: silent catch (does not throw)', () => {
    const s = displayStore()
    s.hydrate()
    // Monkey-patch localStorage.setItem to throw
    const orig = globalThis.localStorage.setItem
    globalThis.localStorage.setItem = () => { throw new Error('QuotaExceeded') }
    try {
      // Should NOT throw
      s.setDisplayMode('presentation')
      s.setColorBlindPalette('okabe-ito')
    } finally {
      globalThis.localStorage.setItem = orig
    }
  })
})

// ═══════════════════════════════════════════════════════
// 测试 2: onboarding store state machine
// ═══════════════════════════════════════════════════════
describe('onboarding store (state machine)', () => {
  let onboardingStore

  beforeEach(() => {
    globalThis.localStorage.clear()
    dataAttrs = {}
    setActivePinia(createPinia())
    return import(`${pathToFileURL(TMP)}/stores/${'onboarding.js'}`).then(m => {
      onboardingStore = m.useOnboardingStore
    })
  })

  wrapTest('initial state: hasOnboarded=false, currentStep=1, scenarioId=null', () => {
    const s = onboardingStore()
    assert.equal(s.hasOnboarded, false)
    assert.equal(s.currentStep, 1)
    assert.equal(s.scenarioId, null)
    assert.equal(s.startedAt, null)
    assert.equal(s.completedAt, null)
  })

  wrapTest('start(): currentStep=1, startedAt=now (ISO), scenarioId=null', () => {
    const s = onboardingStore()
    s.start()
    assert.equal(s.currentStep, 1)
    assert.equal(s.scenarioId, null)
    assert.ok(s.startedAt, 'startedAt should be set')
    // ISO string
    assert.ok(!isNaN(Date.parse(s.startedAt)), 'startedAt should be valid ISO')
  })

  wrapTest('selectScenario(monitor-overview): scenarioId set + localStorage persisted', () => {
    const s = onboardingStore()
    s.selectScenario('monitor-overview')
    assert.equal(s.scenarioId, 'monitor-overview')
    assert.equal(localStorage.getItem('gridmind.onboarding.scenarioId'), 'monitor-overview')
  })

  wrapTest('selectScenario: 4 valid IDs accepted', () => {
    const s = onboardingStore()
    const ids = ['monitor-overview', 'fault-diagnosis', 'knowledge-rag', 'grayscale-rollout']
    for (const id of ids) {
      s.selectScenario(id)
      assert.equal(s.scenarioId, id, `${id} should be set`)
    }
  })

  wrapTest('next(): 1→2→3, then no-op at 3', () => {
    const s = onboardingStore()
    s.start()
    assert.equal(s.currentStep, 1)
    s.next()
    assert.equal(s.currentStep, 2)
    s.next()
    assert.equal(s.currentStep, 3)
    s.next()
    assert.equal(s.currentStep, 3, 'next at 3 should be no-op')
  })

  wrapTest('prev(): 3→2→1, then no-op at 1', () => {
    const s = onboardingStore()
    s.start()
    s.next()
    s.next()
    assert.equal(s.currentStep, 3)
    s.prev()
    assert.equal(s.currentStep, 2)
    s.prev()
    assert.equal(s.currentStep, 1)
    s.prev()
    assert.equal(s.currentStep, 1, 'prev at 1 should be no-op')
  })

  wrapTest('complete(): hasOnboarded=true, completedAt=now, localStorage persisted', () => {
    const s = onboardingStore()
    s.start()
    s.selectScenario('fault-diagnosis')
    s.complete()
    assert.equal(s.hasOnboarded, true)
    assert.ok(s.completedAt, 'completedAt should be set')
    assert.equal(localStorage.getItem('gridmind.onboarded'), 'true')
    assert.equal(localStorage.getItem('gridmind.onboardedAt'), s.completedAt)
  })

  wrapTest('reset(): all state cleared + localStorage wiped', () => {
    const s = onboardingStore()
    s.start()
    s.selectScenario('knowledge-rag')
    s.next()
    s.next()
    s.complete()
    s.reset()
    assert.equal(s.hasOnboarded, false)
    assert.equal(s.currentStep, 1)
    assert.equal(s.scenarioId, null)
    assert.equal(s.startedAt, null)
    assert.equal(s.completedAt, null)
    assert.equal(localStorage.getItem('gridmind.onboarded'), null)
    assert.equal(localStorage.getItem('gridmind.onboardedAt'), null)
    assert.equal(localStorage.getItem('gridmind.onboarding.scenarioId'), null)
  })

  wrapTest('hydrate: read all 3 localStorage keys on existing user', () => {
    localStorage.setItem('gridmind.onboarded', 'true')
    localStorage.setItem('gridmind.onboardedAt', '2026-08-04T00:00:00.000Z')
    localStorage.setItem('gridmind.onboarding.scenarioId', 'grayscale-rollout')
    const s = onboardingStore()
    s.hydrate()
    assert.equal(s.hasOnboarded, true)
    assert.equal(s.completedAt, '2026-08-04T00:00:00.000Z')
    assert.equal(s.scenarioId, 'grayscale-rollout')
  })

  wrapTest('hydrate: onboarded=true missing → hasOnboarded=false', () => {
    const s = onboardingStore()
    s.hydrate()
    assert.equal(s.hasOnboarded, false)
  })

  wrapTest('round-trip: start → selectScenario → next → next → complete → reset → start', () => {
    const s = onboardingStore()
    s.start()
    s.selectScenario('monitor-overview')
    s.next()
    s.next()
    s.complete()
    assert.equal(s.hasOnboarded, true)
    s.reset()
    assert.equal(s.hasOnboarded, false)
    s.start()
    assert.equal(s.currentStep, 1)
  })
})

// ═══════════════════════════════════════════════════════
// 测试 3: setupOnboardingGuard 路由守卫（4 分支）
// ═══════════════════════════════════════════════════════
describe('setupOnboardingGuard (4 branches)', () => {
  let guard, useOnboardingStore

  beforeEach(() => {
    globalThis.localStorage.clear()
    setActivePinia(createPinia())
    // 从 composables 拿 guard，从 stores 拿 store（useOnboarding.js 不 re-export store）
    return Promise.all([
      import(`${pathToFileURL(TMP)}/composables/${'useOnboarding.js'}`),
      import(`${pathToFileURL(TMP)}/stores/${'onboarding.js'}`),
    ]).then(([m1, m2]) => {
      guard = m1.setupOnboardingGuard
      useOnboardingStore = m2.useOnboardingStore
    })
  })

  /** 构造一个伪 Router（beforeEach 接受 callback） */
  function makeFakeRouter() {
    const handlers = []
    return {
      beforeEach(fn) { handlers.push(fn) },
      _run(to) {
        // 执行第一个 handler
        return new Promise((resolve) => {
          let result
          handlers[0](to, { path: '/from' }, (r) => { result = r })
          resolve(result)
        })
      },
    }
  }

  wrapTest('Branch 1: 未完成 && path !== /onboarding && 无 ?tour → redirect /onboarding', async () => {
    const s = useOnboardingStore()
    s.hydrate()
    assert.equal(s.hasOnboarded, false)
    const router = makeFakeRouter()
    guard(router)
    const result = await router._run({ path: '/monitor', query: {} })
    assert.equal(result.path, '/onboarding', 'should redirect to /onboarding')
  })

  wrapTest('Branch 2: 未完成 && path === /onboarding → 不重定向（自免疫）', async () => {
    const s = useOnboardingStore()
    s.hydrate()
    const router = makeFakeRouter()
    guard(router)
    const result = await router._run({ path: '/onboarding', query: {} })
    assert.equal(result === undefined, true, 'should call next() with no arg (allow through)')
  })

  wrapTest('Branch 3: 未完成 && ?tour=monitor → 直接放行（白名单）', async () => {
    const s = useOnboardingStore()
    s.hydrate()
    const router = makeFakeRouter()
    guard(router)
    const result = await router._run({ path: '/monitor', query: { tour: 'monitor' } })
    assert.equal(result === undefined, true, 'should allow through with tour query')
  })

  wrapTest('Branch 4: 未完成 && ?tour=invalid → 重定向 /onboarding（白名单严格）', async () => {
    const s = useOnboardingStore()
    s.hydrate()
    const router = makeFakeRouter()
    guard(router)
    const result = await router._run({ path: '/monitor', query: { tour: 'unknown' } })
    assert.equal(result.path, '/onboarding', 'invalid tour should still redirect')
  })

  wrapTest('已完成 → 所有路由放行', async () => {
    const s = useOnboardingStore()
    s.hydrate()
    s.complete()
    assert.equal(s.hasOnboarded, true)
    const router = makeFakeRouter()
    guard(router)
    const result = await router._run({ path: '/monitor', query: {} })
    assert.equal(result === undefined, true, 'completed user should pass through')
  })

  wrapTest('未完成 && ?force=1 重看入口 → 放行', async () => {
    const s = useOnboardingStore()
    s.hydrate()
    const router = makeFakeRouter()
    guard(router)
    const result = await router._run({ path: '/onboarding', query: { force: '1' } })
    assert.equal(result === undefined, true, 'force=1 should allow through')
  })

  wrapTest('无循环: /onboarding 不会触发 redirect', async () => {
    const s = useOnboardingStore()
    s.hydrate()
    const router = makeFakeRouter()
    guard(router)
    const result1 = await router._run({ path: '/onboarding', query: {} })
    const result2 = await router._run({ path: '/onboarding', query: {} })
    assert.equal(result1 === undefined && result2 === undefined, true, 'no loop')
  })

  wrapTest('5 个 tour 白名单均放行: chat/monitor/grayscale/audit/system', async () => {
    const s = useOnboardingStore()
    s.hydrate()
    const router = makeFakeRouter()
    guard(router)
    const tours = ['chat', 'monitor', 'grayscale', 'audit', 'system']
    for (const t of tours) {
      const result = await router._run({ path: `/${t === 'chat' ? '' : t}`, query: { tour: t } })
      assert.equal(result === undefined, true, `tour=${t} should pass through`)
    }
  })
})

// ═══════════════════════════════════════════════════════
// 测试 4: PulseDot 向后兼容（静态源码分析）
// ═══════════════════════════════════════════════════════
describe('PulseDot backward compatibility (static source analysis)', () => {
  wrapTest('PulseDotProps.shape 默认值 = "circle" (向后兼容)', () => {
    // 在 PulseDot.vue 中查找 withDefaults(...shape: 'circle'...)
    assert.match(pulseDotSrc, /shape:\s*['"]circle['"]/, 'shape default should be "circle"')
  })

  wrapTest('PulseDotProps.glyph 默认值 = "dot" (向后兼容)', () => {
    assert.match(pulseDotSrc, /glyph:\s*['"]dot['"]/, 'glyph default should be "dot"')
  })

  wrapTest('PulseDotProps.tone 类型是 optional（?）', () => {
    // shape/glyph 在 theme.ts 的 PulseDotProps 中是 optional（?）
    assert.match(themeTypesSrc, /shape\?:\s*['"]circle['"]/, 'shape must be optional in types')
    // glyph 是 union type,检查 ? 修饰
    assert.match(themeTypesSrc, /glyph\?:\s*['"][^'"]*['"][^}]*['"][^}]*['"]/, 'glyph must be optional in types')
  })

  wrapTest('旧 tone "success"/"danger" 通过 TONE_MAP 映射为 normal/critical', () => {
    assert.match(pulseDotSrc, /success:\s*['"]normal['"]/, 'success should map to normal')
    assert.match(pulseDotSrc, /danger:\s*['"]critical['"]/, 'danger should map to critical')
  })

  wrapTest('5 shape 全部 clip-path 已实现', () => {
    const expected = ['circle', 'triangle', 'square', 'diamond', 'hexagon']
    for (const s of expected) {
      // shape key 在 SHAPE_CLIP 中作为 key 出现（unquoted）
      const keyRe = new RegExp(`(?:^|[\\s,{(])${s}\\s*:`)
      assert.match(pulseDotSrc, keyRe, `${s} shape key should be present in SHAPE_CLIP`)
    }
    // 还要有 SHAPE_CLIP 多边形定义
    assert.match(pulseDotSrc, /SHAPE_CLIP/, 'SHAPE_CLIP map should exist')
  })

  wrapTest('5 glyph 字符全部映射', () => {
    const expected = ['check', 'bang', 'cross', 'info', 'dot']
    for (const g of expected) {
      assert.ok(
        pulseDotSrc.includes(g),
        `glyph '${g}' should be present in GLYPH_CHAR`,
      )
    }
  })

  wrapTest('aria-label 自动拼装含中文 tone + shape + glyph', () => {
    // TONE_ZH 字典含 5 个 tone
    const tones = ['normal', 'warning', 'critical', 'info', 'accent']
    for (const t of tones) {
      assert.match(pulseDotSrc, new RegExp(`${t}:\\s*['"][^'"]+['"]`), `${t} should have CN label`)
    }
    // 自动 label 拼装
    assert.match(pulseDotSrc, /状态：/, 'should have "状态：" prefix')
    assert.match(pulseDotSrc, /autoLabel/, 'autoLabel computed should exist')
  })

  wrapTest('5 status icons 映射到 STATUS_PRESENTATION 中', () => {
    // 5 tone 都有 shape + glyph + iconName + textCode
    for (const t of ['normal', 'info', 'warning', 'critical', 'accent']) {
      const block = new RegExp(`${t}:\\s*\\{[^}]*shape:[^}]*glyph:[^}]*iconName:[^}]*textCode:`)
      assert.match(themeTypesSrc, block, `${t} should have full 4-tuple in STATUS_PRESENTATION`)
    }
  })

  wrapTest('v1.4.0 旧调用方（仅传 tone）仍可用 - status-badge 验证', () => {
    // App.vue 旧调用方：仅传 tone + size + speed
    // 旧调用方写法
    assert.match(appSrc, /:tone="connected \? 'success' : 'danger'"/, 'legacy tone usage should be present')
    // 没传 shape/glyph - 默认值生效
  })
})

// ═══════════════════════════════════════════════════════
// 测试 5: StatusIcon aria-label 准确性（静态源码分析）
// ═══════════════════════════════════════════════════════
describe('StatusIcon aria-label (WCAG 2.2 §4.1.2)', () => {
  wrapTest('role="img" + aria-label 必备（架构 §7.3 a11y 约定）', () => {
    assert.match(statusIconSrc, /role="img"/, 'should have role="img"')
    assert.match(statusIconSrc, /:aria-label="ariaLabel"/, 'should bind aria-label')
  })

  wrapTest('5 status 中文映射存在', () => {
    const map = {
      normal: '正常',
      warning: '警告',
      critical: '严重',
      info: '信息',
      accent: '重点',
    }
    for (const [k, v] of Object.entries(map)) {
      assert.match(statusIconSrc, new RegExp(`${k}:\\s*['"]${v}['"]`), `${k} should map to "${v}"`)
    }
  })

  wrapTest('5 shape 中文映射存在', () => {
    const map = {
      circle: '圆形',
      triangle: '三角',
      square: '方形',
      diamond: '菱形',
      hexagon: '六边',
    }
    for (const [k, v] of Object.entries(map)) {
      assert.match(statusIconSrc, new RegExp(`${k}:\\s*['"]${v}['"]`), `${k} should map to "${v}"`)
    }
  })

  wrapTest('aria-label 格式: "状态：{tone}（{shape} + {glyph}）"', () => {
    assert.match(statusIconSrc, /状态：/, 'should have 状态： prefix')
    assert.match(statusIconSrc, /（\$\{SHAPE_ZH/, 'should embed SHAPE_ZH')
    assert.match(statusIconSrc, /\+\s*\$\{GLYPH_CHAR/, 'should embed GLYPH_CHAR')
    assert.match(statusIconSrc, /）$/m, 'should end with closing parenthesis')
  })

  wrapTest('SVG 内嵌 path 库（5 形状）', () => {
    assert.match(statusIconSrc, /SHAPE_PATH/, 'should have SHAPE_PATH map')
    for (const s of ['circle', 'triangle', 'square', 'diamond', 'hexagon']) {
      assert.ok(statusIconSrc.includes(`${s}:`), `SHAPE_PATH should have ${s}`)
    }
  })

  wrapTest('supports prefers-reduced-motion: transition disabled', () => {
    assert.match(statusIconSrc, /prefers-reduced-motion/, 'should respect prefers-reduced-motion')
  })
})

// ═══════════════════════════════════════════════════════
// 测试 6: SCSS 4 palette × 2 theme = 8 套组合（静态分析）
// ═══════════════════════════════════════════════════════
describe('4 palette × 2 theme (8 combinations, static SCSS analysis)', () => {
  const expectedPalettes = ['default', 'ibm-cb-safe', 'okabe-ito', 'colorbrewer-rdylbu']

  wrapTest('4 palette 标识符全部定义（:root[data-cb-palette]）', () => {
    for (const p of expectedPalettes) {
      const re = new RegExp(`\\[data-cb-palette=["']${p}["']\\]`)
      const inDark = re.test(darkSrc)
      const inLight = re.test(lightSrc)
      assert.equal(inDark, true, `dark must have ${p} palette`)
      assert.equal(inLight, true, `light must have ${p} palette`)
    }
  })

  wrapTest('dark theme: 4 套 palette × 5 status 完整 token', () => {
    for (const p of expectedPalettes) {
      assert.match(darkSrc, new RegExp(`\\[data-cb-palette=["']${p}["']\\]`), `dark must declare ${p}`)
    }
    // 每个 palette 必须包含 normal/warning/critical/info/accent 5 tone 的 fg + soft
    const paletteBlocks = darkSrc.match(/\[data-cb-palette=[^\]]+\]\s*\{[^}]+\}/gs) || []
    assert.equal(paletteBlocks.length >= 4, true, `dark must have ≥4 palette blocks (got ${paletteBlocks.length})`)
    for (const block of paletteBlocks) {
      for (const tone of ['normal', 'warning', 'critical', 'info', 'accent']) {
        assert.match(block, new RegExp(`--cb-status-${tone}-fg`), `block must have ${tone}-fg`)
        assert.match(block, new RegExp(`--cb-status-${tone}-soft`), `block must have ${tone}-soft`)
      }
    }
  })

  wrapTest('light theme: 4 套 palette × 5 status 完整 token', () => {
    const paletteBlocks = lightSrc.match(/\[data-cb-palette=[^\]]+\]\s*\{[^}]+\}/gs) || []
    assert.equal(paletteBlocks.length >= 4, true, `light must have ≥4 palette blocks (got ${paletteBlocks.length})`)
    for (const block of paletteBlocks) {
      for (const tone of ['normal', 'warning', 'critical', 'info', 'accent']) {
        assert.match(block, new RegExp(`--cb-status-${tone}-fg`), `block must have ${tone}-fg`)
        assert.match(block, new RegExp(`--cb-status-${tone}-soft`), `block must have ${tone}-soft`)
      }
    }
  })

  wrapTest('5 onboarding scenarios 全部 4 套（主理人决策 #1）', () => {
    const ids = ['monitor-overview', 'fault-diagnosis', 'knowledge-rag', 'grayscale-rollout']
    for (const id of ids) {
      assert.match(themeTypesSrc, new RegExp(`id:\\s*['"]${id}['"]`), `${id} should be in ONBOARDING_SCENARIOS`)
    }
  })

  wrapTest('5 onboarding scenarios 都有 starterMessage 真实样例', () => {
    // 只匹配对象字面量（含 starterMessage: 'xxx'），不匹配 interface 定义
    const scenarioBlocks = themeTypesSrc.match(/\{\s*id:\s*['"][^'"]+['"][\s\S]+?starterMessage:\s*['"][^'"]+['"][\s\S]+?\}/gs) || []
    assert.ok(scenarioBlocks.length === 4, `should have exactly 4 ONBOARDING_SCENARIOS object literals (got ${scenarioBlocks.length})`)
    for (const block of scenarioBlocks) {
      assert.match(block, /starterMessage:\s*['"][^'"]{10,}['"]/, 'starterMessage should be a non-trivial string (≥10 chars)')
    }
  })
})

// ═══════════════════════════════════════════════════════
// 测试 7: 路由 + 5 tour 集成（静态）
// ═══════════════════════════════════════════════════════
describe('Routes + 5 tour integration', () => {
  wrapTest('6 路由全部定义（5 core + /onboarding）', () => {
    const routes = ['/', '/monitor', '/grayscale', '/audit', '/system', '/onboarding']
    for (const r of routes) {
      const re = new RegExp(`path:\\s*['"]${r === '/' ? '\\/' : r}['"]`)
      assert.match(routerSrc, re, `route ${r} should be defined`)
    }
  })

  wrapTest('OnboardingTour 5 tour 全部定义（chat/monitor/grayscale/audit/system）', () => {
    const tours = ['chat', 'monitor', 'grayscale', 'audit', 'system']
    const tourTypeBlock = tourSrc.match(/export type TourName[\s\S]+?\[/)
    assert.ok(tourTypeBlock, 'should have TourName type')
    for (const t of tours) {
      assert.match(tourTypeBlock[0], new RegExp(`['"]${t}['"]`), `TourName should include ${t}`)
      assert.match(tourSrc, new RegExp(`${t}:\\s*\\[`), `TOUR_STEPS.${t} should be defined`)
    }
  })

  wrapTest('driver.js 中文按钮：下一步/上一步/完成/关闭', () => {
    assert.match(tourSrc, /nextBtnText:\s*['"]下一步['"]/, 'nextBtnText should be 下一步')
    assert.match(tourSrc, /prevBtnText:\s*['"]上一步['"]/, 'prevBtnText should be 上一步')
    assert.match(tourSrc, /doneBtnText:\s*['"]完成['"]/, 'doneBtnText should be 完成')
    assert.match(tourSrc, /closeBtnText:\s*['"]关闭['"]/, 'closeBtnText should be 关闭')
  })

  wrapTest('OnboardingTrigger 跳转 force=1 跳进 /onboarding', () => {
    assert.match(triggerSrc, /path:\s*['"]\/onboarding['"]/, 'should route to /onboarding')
    assert.match(triggerSrc, /query:\s*\{\s*force:\s*['"]1['"]/, 'should include force=1 query')
  })

  wrapTest('5 view 全部含 data-tour anchor', async () => {
    const files = [
      'components/ChatView.vue',
      'components/MonitoringView.vue',
      'views/GrayscalePanel.vue',
      'views/AuditLogViewer.vue',
      'views/SystemOverview.vue',
    ]
    for (const f of files) {
      const src = await readFile(join(SRC, f), 'utf-8')
      const anchors = src.match(/data-tour=/g) || []
      assert.ok(anchors.length >= 1, `${f} should have ≥1 data-tour anchor (got ${anchors.length})`)
    }
  })

  wrapTest('OnboardingTour ≥18 个 tour anchor 全覆盖', () => {
    const anchors = tourSrc.match(/\[data-tour=/g) || []
    // 4+5+4+3+3 = 19 个
    assert.ok(anchors.length >= 18, `should have ≥18 tour anchor references (got ${anchors.length})`)
  })
})

// ═══════════════════════════════════════════════════════
// 测试 8: 路由守卫不循环（额外 1 项）
// ═══════════════════════════════════════════════════════
describe('Route guard: no infinite loop', () => {
  let guard, useOnboardingStore
  beforeEach(() => {
    globalThis.localStorage.clear()
    setActivePinia(createPinia())
    return Promise.all([
      import(`${pathToFileURL(TMP)}/composables/${'useOnboarding.js'}`),
      import(`${pathToFileURL(TMP)}/stores/${'onboarding.js'}`),
    ]).then(([m1, m2]) => {
      guard = m1.setupOnboardingGuard
      useOnboardingStore = m2.useOnboardingStore
    })
  })

  function makeFakeRouter() {
    const handlers = []
    return {
      beforeEach(fn) { handlers.push(fn) },
      _run(to) {
        return new Promise((resolve) => {
          let result
          handlers[0](to, { path: '/from' }, (r) => { result = r })
          resolve(result)
        })
      },
    }
  }

  wrapTest('Simulate 5 sequential /  /onboarding  round-trips, no loop', async () => {
    const s = useOnboardingStore()
    s.hydrate()
    const router = makeFakeRouter()
    guard(router)
    // 5 轮: 访问 / 5 次（每次都重定向到 /onboarding）
    for (let i = 0; i < 5; i++) {
      const r1 = await router._run({ path: '/', query: {} })
      assert.equal(r1.path, '/onboarding', `round ${i}: /  → /onboarding`)
      const r2 = await router._run({ path: '/onboarding', query: {} })
      assert.equal(r2 === undefined, true, `round ${i}: /onboarding → no-op`)
    }
  })
})

// ─── 总结 ─────────────────────────────────────────
// 真正退出时清理 tmp（用 'exit' 而非 'beforeExit'，避免 node:test 多次触发）
process.on('exit', () => {
  // 同步清理（exit 阶段只能用同步 IO）
  try {
    const { rmSync, existsSync } = require('node:fs')
    if (existsSync(TMP)) rmSync(TMP, { recursive: true, force: true })
  } catch { /* ignore */ }

  // 打印总结到 stderr（确保不被 node:test TAP 输出覆盖）
  process.stderr.write(`\n\x1b[1m── T05 QA Test Summary ──\x1b[0m\n`)
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
