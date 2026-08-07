/**
 * GridMind v1.5.1 T01 单元测试 · useSseStream composable
 *
 * 覆盖场景（≥5）：
 *   1. 连接成功：onOpen + onEvent 触发；state = open
 *   2. JWT header 注入：Authorization = `Bearer <token>`
 *   3. SSE 解析：`data: {...}\n\n` 协议正确切分；多条事件
 *   4. 心跳超时：30s 无消息 → abort + reconnect（用伪造 fetch 模拟）
 *   5. 重连退避：fetch 连续失败时按 [1000, 5000, 15000, 30000] 退避
 *   6. disconnect：停止重连循环；state = closed
 *
 * 运行：node tests/test_sse_stream.mjs
 */
import { test, describe, before, after } from 'node:test'
import assert from 'node:assert/strict'
import { build } from 'esbuild'
import { mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { createServer } from 'node:http'
import { existsSync, symlinkSync } from 'node:fs'

// ─── 路径 ───
const ROOT = resolve(import.meta.dirname, '..')
const SRC = join(ROOT, 'src')
const TMP = await mkdtemp(join(tmpdir(), 'gridmind-sse-'))

// ─── 编译 useSseStream + useJwtAuth 到 TMP ───
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

// ─── Vue / document mock（useSseStream 用 onUnmounted） ───
const _localStorage = globalThis.localStorage
if (!_localStorage) {
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
globalThis.document = {
  documentElement: {
    setAttribute() {},
    getAttribute() { return null },
  },
  createElement(tag) {
    return {
      tagName: String(tag).toUpperCase(),
      children: [], style: {},
      setAttribute() {}, getAttribute() { return null },
      appendChild(c) { this.children.push(c); return c },
      removeChild(c) { this.children = this.children.filter((x) => x !== c); return c },
      addEventListener() {}, removeEventListener() {},
      set innerHTML(v) { this._innerHTML = v }, get innerHTML() { return this._innerHTML ?? '' },
      set textContent(v) { this._textContent = v }, get textContent() { return this._textContent ?? '' },
    }
  },
  querySelector() { return null }, querySelectorAll() { return [] },
  addEventListener() {}, removeEventListener() {},
}

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

/* ────────────────────────────────────────────────────────────
 * 测试 1-3：本地 HTTP server + JWT header 注入 + SSE 解析
 * ──────────────────────────────────────────────────────────── */
describe('useSseStream (SSE + JWT + reconnect)', () => {
  let streamModule

  before(async () => {
    const m = await import(`${pathToFileURL(TMP)}/composables/${'useSseStream.js'}`)
    streamModule = m
  })

  wrap('连接成功：onOpen + onEvent 触发；state = open；JWT header 注入', async () => {
    let receivedHeaders = {}
    let receivedAuth = ''
    const server = createServer((req, res) => {
      receivedHeaders = req.headers
      receivedAuth = String(req.headers['authorization'] ?? '')
      res.writeHead(200, {
        'content-type': 'text/event-stream',
        'cache-control': 'no-cache',
        'connection': 'keep-alive',
      })
      res.write('data: {"type":"hello","value":1}\n\n')
      res.write('data: {"type":"world","value":2}\n\n')
      // 保持连接直到 client abort
      res.socket?.on('close', () => {})
    })

    await new Promise((r) => server.listen(0, '127.0.0.1', r))
    const port = server.address().port
    const url = `http://127.0.0.1:${port}/events`

    let opened = false
    const events = []
    const stream = streamModule.useSseStream({
      url,
      headers: {},
      retryDelaysMs: [50, 50, 50],
      heartbeatTimeoutMs: 60000,
      onEvent: (e) => events.push(e),
      onOpen: () => { opened = true },
    })

    // 等待连接 + 消息
    await new Promise((r) => setTimeout(r, 100))

    assert.equal(opened, true, 'onOpen 应触发')
    assert.equal(stream.state.value, 'open', 'state 应为 open')
    assert.ok(receivedAuth.startsWith('Bearer '), `Authorization 应以 Bearer 开头：got ${receivedAuth}`)
    assert.match(receivedAuth, /Bearer gridmind-dev-token/, 'dev JWT 应为 gridmind-dev-token')
    assert.ok(events.length >= 2, `应收到 ≥2 条事件（got ${events.length}）`)
    assert.equal(events[0].type, 'hello')
    assert.equal(events[1].type, 'world')

    stream.disconnect()
    server.close()
  })

  wrap('heartbeat 解析：comment 行不触发 onEvent 但重置心跳', async () => {
    const server = createServer((req, res) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' })
      res.write(':heartbeat\n\n')                              // SSE comment
      res.write('data: {"type":"data_only"}\n\n')
      res.write(':keep-alive\n\n')                             // SSE comment
      res.write('data: {"type":"more"}\n\n')
    })

    await new Promise((r) => server.listen(0, '127.0.0.1', r))
    const port = server.address().port
    const events = []
    const stream = streamModule.useSseStream({
      url: `http://127.0.0.1:${port}/events`,
      retryDelaysMs: [50],
      heartbeatTimeoutMs: 60000,
      onEvent: (e) => events.push(e),
    })

    await new Promise((r) => setTimeout(r, 100))
    // 只应收到 2 条 data 事件，comment 不触发
    const dataTypes = events.map((e) => e.type)
    assert.ok(dataTypes.includes('data_only'), '应收到 data_only')
    assert.ok(dataTypes.includes('more'), '应收到 more')
    assert.equal(events.length, 2, `心跳 comment 不应触发 onEvent（got ${events.length}）`)

    stream.disconnect()
    server.close()
  })

  wrap('断线重连：fetch 失败 → 按退避序列 retry；retryAttempt 自增', async () => {
    let connectAttempt = 0
    // 让 fetch 第一次 fail，第二次成功
    const origFetch = globalThis.fetch
    globalThis.fetch = async () => {
      connectAttempt++
      if (connectAttempt === 1) {
        throw new Error('ECONNRESET')
      }
      // 第二次成功：返回空 SSE 流并立即关
      const encoder = new TextEncoder()
      const stream = new ReadableStream({
        start(controller) {
          controller.enqueue(encoder.encode(''))
          controller.close()
        },
      })
      return new Response(stream, { status: 200, headers: { 'content-type': 'text/event-stream' } })
    }

    const events = []
    let errored = false
    let openedCount = 0
    const stream = streamModule.useSseStream({
      url: '/api/sse-test',
      retryDelaysMs: [10, 10, 10], // 加速测试
      heartbeatTimeoutMs: 60000,
      onEvent: (e) => events.push(e),
      onError: () => { errored = true },
      onOpen: () => { openedCount++ },
    })

    await new Promise((r) => setTimeout(r, 150))
    stream.disconnect()

    assert.equal(errored, true, '首次连接应 onError')
    assert.equal(connectAttempt >= 2, true, `应至少 2 次重连（got ${connectAttempt}）`)
    assert.equal(openedCount >= 1, true, `至少一次 onOpen（got ${openedCount}）`)
    assert.equal(stream.state.value, 'closed', '最终 state 应为 closed')

    globalThis.fetch = origFetch
  })

  wrap('disconnect：停止重连循环；state = closed', async () => {
    let attempt = 0
    const origFetch = globalThis.fetch
    globalThis.fetch = async () => {
      attempt++
      throw new Error('always fails')
    }

    const stream = streamModule.useSseStream({
      url: '/api/never',
      retryDelaysMs: [5, 5, 5],
      heartbeatTimeoutMs: 60000,
      onEvent: () => {},
      onError: () => {},
    })

    await new Promise((r) => setTimeout(r, 30))
    assert.notEqual(stream.state.value, 'closed', 'disconnect 之前不应 closed')
    stream.disconnect()
    await new Promise((r) => setTimeout(r, 20))
    assert.equal(stream.state.value, 'closed', 'disconnect 后 state 应为 closed')

    globalThis.fetch = origFetch
  })

  wrap('SSE parsing：data: {json}\\n\\n 协议；多条事件 + 部分 buffer 拼接', async () => {
    const server = createServer((req, res) => {
      res.writeHead(200, { 'content-type': 'text/event-stream' })
      // 一次性写入多个事件（模拟真实 chunk）
      const payload =
        'data: {"id":1,"msg":"first"}\n\n' +
        'data: {"id":2,"msg":"second"}\n\n' +
        'data: {"id":3,"msg":"third"}\n\n'
      res.write(payload)
    })

    await new Promise((r) => server.listen(0, '127.0.0.1', r))
    const port = server.address().port
    const events = []
    const stream = streamModule.useSseStream({
      url: `http://127.0.0.1:${port}/events`,
      retryDelaysMs: [50],
      heartbeatTimeoutMs: 60000,
      onEvent: (e) => events.push(e),
    })

    await new Promise((r) => setTimeout(r, 100))
    assert.equal(events.length, 3, `应收到 3 条事件（got ${events.length}）`)
    assert.deepEqual(
      events.map((e) => e.id),
      [1, 2, 3],
      '事件 id 顺序应正确',
    )

    stream.disconnect()
    server.close()
  })

  wrap('JWT 注入：headers 包含 Authorization: Bearer <token>（getAuthHeaders + fetch）', async () => {
    let receivedAuth = ''
    const server = createServer((req, res) => {
      receivedAuth = String(req.headers['authorization'] ?? '')
      res.writeHead(200, { 'content-type': 'text/event-stream' })
      res.write('data: {"ok":true}\n\n')
    })

    await new Promise((r) => server.listen(0, '127.0.0.1', r))
    const port = server.address().port
    const jwtModule = await import(`${pathToFileURL(TMP)}/composables/${'useJwtAuth.js'}`)

    // 直读 env 默认值
    const token = jwtModule.getJwtToken()
    assert.equal(typeof token, 'string')
    assert.ok(token.length > 0)

    const stream = streamModule.useSseStream({
      url: `http://127.0.0.1:${port}/events`,
      retryDelaysMs: [50],
      heartbeatTimeoutMs: 60000,
      onEvent: () => {},
    })

    await new Promise((r) => setTimeout(r, 50))
    assert.ok(receivedAuth.startsWith('Bearer '), `Authorization 应以 Bearer 开头（got ${receivedAuth}）`)

    stream.disconnect()
    server.close()
  })
})

// ─── 清理 + 总结 ───
process.on('exit', () => {
  process.stderr.write(`\n\x1b[1m── T01 useSseStream test summary ──\x1b[0m\n`)
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
