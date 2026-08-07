/**
 * GridMind v1.5.1 T01 单元测试 · useFocusTrap composable
 *
 * 覆盖场景（≥3）：
 *   1. Tab 在最后一个 → 循环到第一个（preventDefault + first.focus）
 *   2. Shift+Tab 在第一个 → 循环到最后一个
 *   3. Esc 触发 focus-trap-escape 自定义事件（容器 receive）
 *   4. activate：自动聚焦容器内第一个可聚焦元素
 *   5. deactivate：焦点归还到 previously focused
 *   6. isActive 状态：未激活时 keydown 不参与焦点循环
 *
 * 实现思路：
 *   useFocusTrap 的核心逻辑由 getFocusableElements + handleKeydown 组成。
 *   onMounted/onUnmounted 只是生命周期包装，本测试直接驱动 activate/deactivate
 *   并模拟 dispatchEvent，从而验证核心 focus 循环逻辑。
 *
 * 运行：node tests/test_focus_trap.mjs
 */
import { test, describe, before } from 'node:test'
import assert from 'node:assert/strict'
import { build } from 'esbuild'
import { mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { existsSync, symlinkSync } from 'node:fs'

// ─── 路径 ───
const ROOT = resolve(import.meta.dirname, '..')
const SRC = join(ROOT, 'src')
const TMP = await mkdtemp(join(tmpdir(), 'gridmind-focus-'))

// ─── 编译 useFocusTrap 到 TMP（不打包 vue；我们手动驱动 lifecycle） ───
await build({
  entryPoints: [join(SRC, 'composables/useFocusTrap.ts')],
  outdir: TMP,
  outbase: SRC,
  bundle: true,
  format: 'esm',
  platform: 'node',
  target: 'node20',
  external: ['vue'],
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

// ─── DOM mock ───
/** FakeElement：支持常见 DOM 操作（querySelectorAll 找所有可聚焦元素） */
function FakeElement(tag, opts = {}) {
  if (!(this instanceof FakeElement)) return new FakeElement(tag, opts)
  this.tagName = (tag || 'div').toUpperCase()
  this.attrs = {}
  this.style = {}
  this.children = []
  this.parent = null
  this.listeners = {}
  this.disabled = false
  this.tabIndex = opts.tabIndex ?? 0
  this._textContent = ''
  this._innerHTML = ''
  this.getAttribute = (k) => this.attrs[k] ?? null
  this.setAttribute = (k, v) => { this.attrs[k] = String(v) }
  this.appendChild = (c) => { c.parent = this; this.children.push(c); return c }
  this.removeChild = (c) => {
    const before = this.children.length
    this.children = this.children.filter((x) => x !== c)
    return before !== this.children.length ? c : null
  }
  this.hasAttribute = (k) => Object.hasOwn(this.attrs, k)
  this.addEventListener = (e, fn) => { (this.listeners[e] ??= []).push(fn) }
  this.removeEventListener = (e, fn) => {
    this.listeners[e] = (this.listeners[e] ?? []).filter((f) => f !== fn)
  }
  this.focus = () => {
    globalThis.__ACTIVE_ELEMENT__ = this
    const handlers = this.listeners['focus'] ?? []
    for (const h of handlers) h({ type: 'focus', target: this })
  }
  this.blur = () => {
    if (globalThis.__ACTIVE_ELEMENT__ === this) globalThis.__ACTIVE_ELEMENT__ = null
  }
  this.click = () => {}
  this.contains = (node) => {
    if (node === this) return true
    return this.children.some((c) => c.contains?.(node))
  }
  this.offsetParent = opts.offsetParent ?? { tagName: 'BODY' }
  this.dispatchEvent = (ev) => {
    const handlers = this.listeners[ev.type] ?? []
    for (const h of handlers) h(ev)
  }
  // querySelectorAll —— 简化版，支持常见组合选择器
  this.querySelectorAll = (selector) => {
    return selectAll(this, selector)
  }
  this.querySelector = (selector) => {
    const all = selectAll(this, selector)
    return all[0] ?? null
  }
}

/**
 * 简化版 CSS 选择器解析器 — 仅覆盖 useFocusTrap 需要的子集：
 *   a, a[href], button, button:not([disabled]), [tabindex], input, select, textarea, ...
 *   (逗号分隔)
 */
function selectAll(root, selector) {
  const all = []
  const tokens = selector.split(',').map((s) => s.trim())
  for (const tok of tokens) {
    collect(root, tok, all)
  }
  // 去重
  const seen = new Set()
  return all.filter((el) => {
    if (seen.has(el)) return false
    seen.add(el)
    return true
  })
}

function collect(root, selector, out) {
  // 仅支持简单 selectors
  // 例如："button:not([disabled])" 或 "a[href]"
  let wantTag = null
  let wantAttr = null
  let wantAttrPresence = false
  let notHasAttr = null

  const notMatch = selector.match(/^(\w+):not\((.+?)\)$/)
  if (notMatch) {
    wantTag = notMatch[1].toLowerCase()
    const inner = notMatch[2]
    const disabledMatch = inner.match(/^\[disabled\]$/)
    if (disabledMatch) {
      notHasAttr = 'disabled'
    }
  } else {
    const tm = selector.match(/^(\w+)(?:\[(\w+(?:=[\w-]+)?)\])?/)
    if (tm) {
      wantTag = (tm[1] ?? '').toLowerCase()
      if (tm[2]) {
        if (tm[2].includes('=')) {
          const [k, v] = tm[2].split('=')
          wantAttr = [k, v]
        } else {
          wantAttrPresence = true
          wantAttr = [tm[2], null]
        }
      }
    }
  }

  function walk(node) {
    for (const c of node.children) {
      const tag = (c.tagName ?? '').toLowerCase()
      if (!wantTag || tag === wantTag) {
        if (notHasAttr === 'disabled' && (c.disabled || c.hasAttribute('disabled'))) continue
        if (wantAttr) {
          if (wantAttrPresence) {
            if (!c.hasAttribute(wantAttr[0])) continue
          } else if (wantAttr[1]) {
            if (c.getAttribute(wantAttr[0]) !== wantAttr[1]) continue
          }
        }
        out.push(c)
      }
      walk(c)
    }
  }
  walk(root)
}

if (!globalThis.localStorage) {
  globalThis.localStorage = {
    getItem() { return null }, setItem() {}, removeItem() {}, clear() {},
    key() { return null }, get length() { return 0 },
  }
}
globalThis.window = {
  __test__: true,
  location: { href: 'http://localhost', origin: 'http://localhost', protocol: 'http:', host: 'localhost', hostname: 'localhost' },
  localStorage: globalThis.localStorage,
  addEventListener() {},
  removeEventListener() {},
}
const _documentListeners = {}
globalThis.document = {
  documentElement: new FakeElement('html'),
  createElement(tag) { return new FakeElement(tag) },
  querySelector() { return null },
  addEventListener(name, fn) { (_documentListeners[name] ??= []).push(fn) },
  removeEventListener(name, fn) {
    _documentListeners[name] = (_documentListeners[name] ?? []).filter((f) => f !== fn)
  },
  dispatchEvent(event) {
    const handlers = _documentListeners[event.type] ?? []
    for (const h of handlers) h(event)
    return true
  },
  get activeElement() {
    return globalThis.__ACTIVE_ELEMENT__ ?? null
  },
}
globalThis.__ACTIVE_ELEMENT__ = null
globalThis.activeElement = null

// ─── 总结 ───
let pass = 0, fail = 0
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

describe('useFocusTrap (focus cycling + Esc + restore)', () => {
  let useFocusTrap

  before(async () => {
    const m = await import(`${pathToFileURL(TMP)}/composables/${'useFocusTrap.js'}`)
    useFocusTrap = m.useFocusTrap
  })

  /**
   * 构造测试场景：用手动驱动 onMounted 替代 Vue 组件 mount。
   *
   * reason: useFocusTrap 依赖 Vue 的 onMounted/onUnmounted 生命周期；
   * 直接调 activate()/deactivate() 即可触发相同逻辑（onMounted 的核心是
   * 加 keydown 监听 + nextTick 聚焦第一个元素；activate() 也做这件事）。
   *
   * 返回：{ trap, container, buttons, external }
   */
  function buildScenario() {
    const external = new FakeElement('button')
    globalThis.__ACTIVE_ELEMENT__ = external

    const container = new FakeElement('div')
    const btn1 = new FakeElement('button')
    btn1.setAttribute('class', 'btn-reject')
    const btn2 = new FakeElement('button')
    btn2.setAttribute('class', 'btn-approve')
    const btn3 = new FakeElement('button')
    btn3.setAttribute('class', 'btn-edit-approve')
    const btn4 = new FakeElement('button')
    btn4.setAttribute('class', 'btn-close')
    btn4.setAttribute('aria-label', '关闭')
    container.appendChild(btn1)
    container.appendChild(btn2)
    container.appendChild(btn3)
    container.appendChild(btn4)

    const containerRef = { value: container }
    const trap = useFocusTrap({ containerRef, autoActivate: false })
    return {
      trap,
      container,
      buttons: [btn1, btn2, btn3, btn4],
      external,
    }
  }

  wrap('Tab 在最后一个 → 循环到第一个（preventDefault + first.focus）', async () => {
    const { trap, buttons } = buildScenario()
    trap.activate()
    await new Promise((r) => setTimeout(r, 10))
    // 模拟：用户先点 buttons[3]
    globalThis.__ACTIVE_ELEMENT__ = buttons[3]

    let prevented = false
    document.dispatchEvent({
      type: 'keydown',
      key: 'Tab',
      shiftKey: false,
      preventDefault: () => { prevented = true },
    })
    assert.equal(prevented, true, 'Tab 在最后一个应 preventDefault')
    assert.equal(globalThis.__ACTIVE_ELEMENT__, buttons[0], '应聚焦第一个')
    trap.deactivate()
  })

  wrap('Shift+Tab 在第一个 → 循环到最后一个', async () => {
    const { trap, buttons } = buildScenario()
    trap.activate()
    await new Promise((r) => setTimeout(r, 10))
    // 焦点现已在 buttons[0]；不需要手动设置
    let prevented = false
    document.dispatchEvent({
      type: 'keydown',
      key: 'Tab',
      shiftKey: true,
      preventDefault: () => { prevented = true },
    })
    assert.equal(prevented, true, 'Shift+Tab 在第一个应 preventDefault')
    assert.equal(globalThis.__ACTIVE_ELEMENT__, buttons[3], '应聚焦最后一个')
    trap.deactivate()
  })

  wrap('Esc 触发 focus-trap-escape 自定义事件（容器 receive）', async () => {
    const { trap, container, buttons } = buildScenario()
    trap.activate()
    await new Promise((r) => setTimeout(r, 10))
    globalThis.__ACTIVE_ELEMENT__ = buttons[1]
    let escapeEvent = null
    container.addEventListener('focus-trap-escape', (e) => { escapeEvent = e })
    document.dispatchEvent({
      type: 'keydown',
      key: 'Escape',
      shiftKey: false,
      preventDefault: () => {},
    })
    assert.ok(escapeEvent !== null, 'Esc 应触发 focus-trap-escape 事件')
    assert.equal(escapeEvent.type, 'focus-trap-escape')
    trap.deactivate()
  })

  wrap('activate：自动聚焦容器内第一个可聚焦元素（nextTick）', async () => {
    const { trap, buttons } = buildScenario()
    globalThis.__ACTIVE_ELEMENT__ = buttons[3]  // 起始焦点不在容器内
    trap.activate()
    await new Promise((r) => setTimeout(r, 10))   // 等 nextTick
    assert.equal(globalThis.__ACTIVE_ELEMENT__, buttons[0], 'activate 应自动聚焦第一个')
    assert.equal(trap.isActive.value, true, 'isActive 应为 true')
    trap.deactivate()
  })

  wrap('deactivate：焦点归还到 previously focused', async () => {
    const { trap, buttons, external } = buildScenario()
    // 起始焦点在外部按钮
    assert.equal(globalThis.__ACTIVE_ELEMENT__, external, '起始 activeElement 应是 external')
    trap.activate()
    await new Promise((r) => setTimeout(r, 10))
    // activate 后焦点应在 buttons[0]
    assert.equal(globalThis.__ACTIVE_ELEMENT__, buttons[0], 'activate 后焦点应在容器内')
    trap.deactivate()
    // 等待 nextTick 让 previouslyFocused.focus() 触发
    await new Promise((r) => setTimeout(r, 10))
    assert.equal(globalThis.__ACTIVE_ELEMENT__, external,
      `deactivate 焦点应还给外部; act=${globalThis.__ACTIVE_ELEMENT__?.attrs?.class ?? '?'}`)
    assert.equal(trap.isActive.value, false, 'isActive 应为 false')
  })

  wrap('isActive 状态：未激活时 keydown 不参与焦点循环', async () => {
    const { trap, buttons } = buildScenario()
    assert.equal(trap.isActive.value, false, 'autoActivate=false 时应未激活')
    globalThis.__ACTIVE_ELEMENT__ = buttons[3]

    let prevented = false
    document.dispatchEvent({
      type: 'keydown',
      key: 'Tab',
      shiftKey: false,
      preventDefault: () => { prevented = true },
    })
    assert.equal(prevented, false, '未激活时 Tab 不应被拦截')
    assert.equal(globalThis.__ACTIVE_ELEMENT__, buttons[3], 'activeElement 不变')
  })
})

// ─── 总结 + 清理 ───
process.on('exit', () => {
  process.stderr.write(`\n\x1b[1m── T01 useFocusTrap test summary ──\x1b[0m\n`)
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
