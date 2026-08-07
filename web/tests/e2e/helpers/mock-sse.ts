/**
 * web/tests/e2e/helpers/mock-sse.ts
 * GridMind v1.5.1 T06 · 端到端联调 · Playwright route mock + pinia store 工具
 *
 * 提供：
 *   1) SSE mock：拦截 `/api/sessions/{id}/events` 返回 text/event-stream 格式
 *   2) REST mock：拦截 pause/resume/rewind/abort/pending-count 等接口
 *   3) pinia store accessor：通过 window.__pinia._s.get(...) 写入状态机
 *   4) JWT 断言工具：捕获 Authorization header 验证
 *
 * 与 ChatView 实际接口对齐：
 *   - SSE URL 形如 `/api/sessions/{tid}/events?token={jwt}`
 *   - pause:  POST /api/sessions/{id}/pause       body={reason: 'user_manual'}
 *   - resume: POST /api/sessions/{id}/resume     body={action: 'continue_from_pause'}
 *   - rewind: POST /api/sessions/{id}/rewind     body={step_index, edited_content}
 *   - abort:  POST /api/sessions/{id}/abort      body={reason}
 *   - hitl approve: POST /api/hitl/{taskId}/approve
 *   - hitl reject:  POST /api/hitl/{taskId}/reject
 *   - audit pending: GET /api/audit/pending-count  → {count: number}
 *   - chat stream: GET /api/chat/stream/{tid}?message=...  → SSE
 *
 * 作者：寇豆码（T06 工程师）
 */
import { type Page, type Route } from '@playwright/test'

/* ─── 类型 ──────────────────────────────────────────── */
export interface MockSseOptions {
  /** SSE 模式：默认 'stream' 返回字节流；'body' 返回完整 chunks */
  responseDelayMs?: number
}

export interface SseMockEvent {
  type: string
  [key: string]: unknown
}

/* ─── pinia store accessor（main.ts DEV 模式暴露） ───── */

/**
 * 读取 pinia 中已注册的 store。可在 page.evaluate 内 await。
 *
 * @example
 *   await page.evaluate(() => {
 *     const reasoning = window.__pinia._s.get('reasoning')
 *     reasoning.start('test-thread', [{ id: 's1', isEditable: true, ... }])
 *   })
 */
export async function getStore<T = unknown>(page: Page, storeId: string): Promise<T> {
  return await page.evaluate((id) => {
    const w = window as unknown as { __pinia?: { _s: Map<string, unknown> } }
    const pinia = w.__pinia
    if (!pinia) throw new Error('window.__pinia not exposed; main.ts DEV gate failed?')
    const store = pinia._s.get(id)
    if (!store) throw new Error(`pinia store '${id}' not found`)
    return store as T
  }, storeId)
}

/**
 * 直接改写 store 的状态字段（适合 audit/chat 中只有 ref 状态可设置场景）。
 */
export async function patchStore(
  page: Page,
  storeId: string,
  patches: Record<string, unknown>,
): Promise<void> {
  await page.evaluate(
    ({ id, patches }) => {
      const w = window as unknown as { __pinia?: { _s: Map<string, Record<string, unknown>> } }
      const store = w.__pinia!._s.get(id) as Record<string, unknown> & {
        $patch?: (p: Record<string, unknown>) => void
      }
      if (store && typeof store.$patch === 'function') {
        store.$patch(patches)
      } else {
        Object.assign(store, patches)
      }
    },
    { id: storeId, patches },
  )
}

/* ─── SSE route mock ───────────────────────────────── */

/**
 * 将一组 SseMockEvent 拼成标准 SSE 协议体（event/data 双行 + 空行分隔）。
 */
function buildSseBody(events: SseMockEvent[]): string {
  return events
    .map((e) => {
      const lines: string[] = []
      lines.push(`event: ${e.type}`)
      lines.push(`data: ${JSON.stringify(e)}`)
      lines.push('') // 空行 = 事件结束
      return lines.join('\n')
    })
    .join('\n')
}

/**
 * 拦截 `/api/sessions/{id}/events` (含 query)，返回 SSE 文本。
 *
 * 重要：text/event-stream 必须用分块传输并保持连接，Playwright route
 * 的 fulfill 在一次性返回时浏览器仍能正确解析（通过 `data:` 行 + 空行）。
 * 若要模拟长连接 + 多事件间隔，可选 delayMs。
 */
export async function mockSseStream(
  page: Page,
  threadId: string,
  events: SseMockEvent[],
  options: MockSseOptions = {},
): Promise<void> {
  const body = buildSseBody(events)
  const responder = async (route: Route) => {
    await route.fulfill({
      status: 200,
      headers: {
        'Content-Type': 'text/event-stream; charset=utf-8',
        'Cache-Control': 'no-cache, no-transform',
        Connection: 'keep-alive',
        'X-Accel-Buffering': 'no',
      },
      body,
    })
    // 可选：让浏览器有视觉流式感知（不影响断言）
    if (options.responseDelayMs && options.responseDelayMs > 0) {
      await new Promise((r) => setTimeout(r, options.responseDelayMs))
    }
  }

  // 同时匹配 query (?token=xxx) 和无 query 两种形式
  await page.route(new RegExp(`/api/sessions/${threadId}/events(\\?.*)?$`), responder)
}

/**
 * 拦截 chat 流端点：`/api/chat/stream/{tid}?message=...`
 *   - 返回 SSE 格式：先多个 token 事件，再 done 事件
 *   - done 事件可携带 interrupt_required 触发弹窗前置
 */
export async function mockChatStream(
  page: Page,
  threadId: string,
  opts: {
    tokens?: string[]
    interruptRequired?: boolean
    interruptNode?: string
    interruptMsg?: string
  } = {},
): Promise<void> {
  const tokens = opts.tokens ?? ['诊断完成。', '电网运行正常。']
  const events: SseMockEvent[] = []
  for (const t of tokens) {
    events.push({ type: 'token', content: t })
  }
  events.push({
    type: 'done',
    interrupt_required: opts.interruptRequired === true,
    interrupt_node: opts.interruptNode ?? null,
    interrupt_msg: opts.interruptMsg ?? null,
    thread_id: threadId,
  })
  const body = buildSseBody(events)
  await page.route(new RegExp(`/api/chat/stream/${threadId}(\\?.*)?$`), async (route) => {
    await route.fulfill({
      status: 200,
      headers: {
        'Content-Type': 'text/event-stream; charset=utf-8',
        'Cache-Control': 'no-cache',
      },
      body,
    })
  })
}

/* ─── REST route mock ───────────────────────────────── */

export async function mockPauseApi(page: Page, threadId: string): Promise<void> {
  await page.route(new RegExp(`/api/sessions/${threadId}/pause$`), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        pausedAt: new Date().toISOString(),
        pausedStep: 2,
        pausedNode: 'diagnosis_agent',
      }),
    })
  })
}

export async function mockResumeApi(page: Page, threadId: string): Promise<void> {
  await page.route(new RegExp(`/api/sessions/${threadId}/resume$`), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        resumedAt: new Date().toISOString(),
        currentNode: 'supervisor',
      }),
    })
  })
}

export async function mockRewindApi(
  page: Page,
  threadId: string,
  newSteps: unknown[] = [],
): Promise<void> {
  await page.route(new RegExp(`/api/sessions/${threadId}/rewind$`), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        rewoundTo: { step_index: 2, checkpoint_id: 'cp-rewind', timestamp: new Date().toISOString() },
        new_steps: newSteps,
      }),
    })
  })
}

export async function mockAbortApi(page: Page, threadId: string): Promise<void> {
  await page.route(new RegExp(`/api/sessions/${threadId}/abort$`), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ abortedAt: new Date().toISOString() }),
    })
  })
}

export async function mockPendingCountApi(page: Page, count: number): Promise<void> {
  // 失败 1 次可以模拟 "5s 漂移" 场景；这里默认持续成功
  await page.route('**/api/audit/pending-count', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ count }),
    })
  })
}

/* ─── JWT 注入验证辅助 ───────────────────────────────── */

/**
 * 收集所有 SSE/REST 请求的 Authorization header，用于断言 JWT 真注入。
 *
 * 原理：page.on('request') 监听所有 fetch；按 URL glob 过滤；返回结果数组。
 */
export interface CapturedRequest {
  url: string
  method: string
  authorization?: string
  authorizationQueryToken?: string
}

export async function startJwtCapture(page: Page): Promise<{
  stop: () => CapturedRequest[]
}> {
  const captured: CapturedRequest[] = []
  const handler = (req: import('@playwright/test').Request) => {
    const url = req.url()
    if (!url.includes('/api/')) return
    const headers = req.headers()
    let authorization = headers['authorization']
    if (!authorization) {
      // Playwright 默认小写化 header 名
      authorization = headers['Authorization']
    }
    const u = new URL(url, 'http://localhost')
    const tok = u.searchParams.get('token')
    captured.push({
      url,
      method: req.method(),
      authorization,
      authorizationQueryToken: tok ?? undefined,
    })
  }
  page.on('request', handler)
  return {
    stop: () => {
      page.off('request', handler)
      return captured
    },
  }
}
