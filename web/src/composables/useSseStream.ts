/**
 * v1.5.1 T01 基础设施 · useSseStream composable（fetch + ReadableStream 替代 EventSource）
 *
 * 关键约束（架构 §1.5.1 + §6.2）：
 *   1. EventSource API **不能自定义 header**（浏览器硬限制）
 *      → SSE 鉴权需要 `Authorization: Bearer <jwt>`，必须改用 fetch
 *   2. EventSource API **只有 GET**（不能 POST）
 *   3. EventSource 浏览器**重试策略不可控**
 *      → 本实现遵循主理人决策 §6.3 退避序列（1s / 5s / 15s / 30s）
 *   4. **JWT 注入**通过 useJwtAuth.getJwtToken() 自动完成，调用方无需关心
 *   5. **30s 心跳超时**（防连接假死）—— 后端每 15s 推 `:heartbeat\\n\\n`
 *      comment 行；前端 30s 内无任何消息（含 heartbeat）即视为断线，
 *      主动 abort 并 scheduleReconnect
 *
 * 重连退避策略（架构 §6.3 + 主理人决策 7.2）：
 *   第 1 次：1000ms
 *   第 2 次：5000ms
 *   第 3 次：15000ms
 *   第 4 次及以后：30000ms（最长）
 *   重置条件：连接成功（onOpen 触发）时 retryAttempt = 0
 *
 * 用法（reasoning store 内部使用 / audit store 内部使用）：
 *   ```typescript
 *   const stream = useSseStream<MyEvent>({
 *     url: '/api/sessions/abc/events',
 *     onEvent: (e) => { console.log('got', e) },
 *     onError: (e) => console.error(e),
 *     onOpen: () => console.log('connected'),
 *   })
 *   // 离开页面 / 切路由时调 stream.disconnect() 主动断开
 *   ```
 *
 * 作者：寇豆码（T01 工程师）
 * 参考：frontend-v151-architecture-2026-08-04.md §3.7 + §6.3 + §6.2
 */
import { onUnmounted, readonly, ref, type Ref } from 'vue'
import { getJwtToken } from './useJwtAuth'

/** SSE 流状态机 4 态 */
export type SseState = 'connecting' | 'open' | 'reconnecting' | 'closed'

/** 默认重连退避序列（架构 §6.3 表） */
export const DEFAULT_RETRY_DELAYS_MS: readonly number[] = [1000, 5000, 15000, 30000] as const

/** 默认心跳超时（ms） */
export const DEFAULT_HEARTBEAT_TIMEOUT_MS = 30000

export interface SseStreamOptions<T> {
  /** SSE URL（含 query 参数；不含 protocol/host）*/
  url: string
  /** HTTP 方法（默认 GET；POST 罕见但保留以备 `body` 场景）*/
  method?: 'GET' | 'POST'
  /** 请求 body（POST 时序列化为 JSON）*/
  body?: unknown
  /** 自定义 header（JWT 已在内部注入；外部可加额外字段如 trace-id）*/
  headers?: Record<string, string>
  /** 重连退避序列（默认 [1000, 5000, 15000, 30000]）*/
  retryDelaysMs?: readonly number[]
  /** 心跳超时（ms；默认 30000）*/
  heartbeatTimeoutMs?: number
  /** 每条事件回调 */
  onEvent: (event: T) => void
  /** 错误回调（含 connect 失败、解析失败）*/
  onError?: (err: Error) => void
  /** 连接成功回调 */
  onOpen?: () => void
  /** 关闭回调（含 [DONE] 标记或服务器 EOF）*/
  onClose?: () => void
}

export interface SseStreamHandle<T = unknown> {
  /** 当前状态（readonly，外部用 watch 监听）*/
  readonly state: Readonly<Ref<SseState>>
  /** 重连尝试次数（用于 UI 显示重试中）*/
  readonly retryAttempt: Readonly<Ref<number>>
  /** 最近一条消息到达时间（ms epoch；用于诊断）*/
  readonly lastEventAt: Readonly<Ref<number>>
  /** 手动断开（停止重连） */
  disconnect(): void
  /** 重置 retryAttempt 并立即重连 */
  reconnect(): void
}

/**
 * 解析 SSE chunk 内的所有 event 行（支持多行 data 拼接）。
 *
 * SSE 协议（WHATWG）：
 *   - 空行表示事件结束
 *   - `event: <name>` 行可指定事件 type（可选）
 *   - `data: <payload>` 行携带数据；多行 data 用 `\n` 拼接
 *   - `:comment` 行为心跳，可忽略
 *
 * 本实现每次 `reader.read()` 拿到的 Uint8Array 都先 TextDecoder 解码，
 * 然后按 `\n\n`（双换行）切分事件；半条事件会留在 buffer 留给下次。
 */
interface SseChunk {
  event: string
  data: string
}

function parseSseBuffer(buffer: string): { events: SseChunk[]; remainder: string } {
  const events: SseChunk[] = []
  // 用 \n\n 切分；最后一段若不是以 \n\n 结束则视为 remainder
  const parts = buffer.split('\n\n')
  const remainder = parts.pop() ?? ''
  for (const part of parts) {
    if (!part) continue
    let eventName = 'message'
    const dataLines: string[] = []
    for (const line of part.split('\n')) {
      if (line.startsWith(':')) {
        // SSE comment（心跳/keep-alive） → 跳过
        continue
      }
      if (line.startsWith('event:')) {
        eventName = line.slice(6).trim()
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trimStart())
      }
    }
    if (dataLines.length > 0) {
      events.push({ event: eventName, data: dataLines.join('\n') })
    }
  }
  return { events, remainder }
}

/**
 * 创建并保持一个 SSE 长连接。
 *
 * 内部实现要点：
 *   1. fetch + ReadableStream 替代 EventSource（自定义 header 必需）
 *   2. retryDelaysMs 退避序列；连接成功重置 retryAttempt
 *   3. heartbeatTimer 30s 无任何消息（包 SSE comment）→ 主动断开重连
 *   4. AbortController 持有 fetch signal；abort 后停止 reader 循环
 *   5. onUnmounted 自动 disconnect（防止 Vue 组件卸载后泄漏连接）
 */
export function useSseStream<T = unknown>(options: SseStreamOptions<T>): SseStreamHandle<T> {
  const state = ref<SseState>('connecting')
  const retryAttempt = ref(0)
  const lastEventAt = ref(0)

  let controller: AbortController | null = null
  let heartbeatTimer: ReturnType<typeof setTimeout> | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  /** 用户主动断开后停止所有重连 */
  let intentionalClose = false

  function clearHeartbeat(): void {
    if (heartbeatTimer !== null) {
      clearTimeout(heartbeatTimer)
      heartbeatTimer = null
    }
  }

  function clearReconnect(): void {
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
  }

  function resetHeartbeat(): void {
    clearHeartbeat()
    const timeout = options.heartbeatTimeoutMs ?? DEFAULT_HEARTBEAT_TIMEOUT_MS
    heartbeatTimer = setTimeout(() => {
      // 30s 无消息 → 视为连接假死，主动断开后重连
      // eslint-disable-next-line no-console
      console.warn('[useSseStream] heartbeat timeout, reconnecting...')
      controller?.abort()
      scheduleReconnect()
    }, timeout)
  }

  function scheduleReconnect(): void {
    if (intentionalClose) return
    clearReconnect()
    const delays = options.retryDelaysMs ?? DEFAULT_RETRY_DELAYS_MS
    const idx = Math.min(retryAttempt.value, delays.length - 1)
    const delay = delays[idx]
    retryAttempt.value += 1
    state.value = 'reconnecting'
    reconnectTimer = setTimeout(() => {
      void connect()
    }, delay)
  }

  async function connect(): Promise<void> {
    if (intentionalClose) return
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    state.value = retryAttempt.value === 0 ? 'connecting' : 'reconnecting'
    controller = new AbortController()

    const headers: Record<string, string> = {
      Accept: 'text/event-stream',
      'Cache-Control': 'no-cache',
      // JWT 鉴权（后端 R-X2 修复后生效）
      Authorization: `Bearer ${getJwtToken()}`,
      ...(options.headers ?? {}),
    }

    try {
      const response = await fetch(options.url, {
        method: options.method ?? 'GET',
        headers,
        body: options.body ? JSON.stringify(options.body) : undefined,
        signal: controller.signal,
        cache: 'no-store',
        credentials: 'same-origin',
      })

      if (!response.ok) {
        throw new Error(`SSE connect failed: HTTP ${response.status} ${response.statusText}`)
      }
      if (!response.body) {
        throw new Error('SSE connect failed: response body is null')
      }

      // 连接成功 — 重置退避
      state.value = 'open'
      retryAttempt.value = 0
      options.onOpen?.()
      lastEventAt.value = Date.now()
      resetHeartbeat()

      const reader = response.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''

      while (!intentionalClose) {
        const { done, value } = await reader.read()
        if (done) break
        if (value) {
          buffer += decoder.decode(value, { stream: true })
          const { events, remainder } = parseSseBuffer(buffer)
          buffer = remainder
          for (const ev of events) {
            // SSE 的 comment 行不触发 onEvent，但仍重置心跳
            if (ev.data.length === 0) {
              resetHeartbeat()
              continue
            }
            if (ev.data === '[DONE]') {
              options.onClose?.()
              return
            }
            try {
              const parsed = JSON.parse(ev.data) as T
              options.onEvent(parsed)
              lastEventAt.value = Date.now()
              resetHeartbeat()
            } catch (err) {
              // JSON.parse 失败 — 数据可能是 string 或 comment 当 payload
              // 容错：当 string 类型时，T 应允许 string（unknown 时不抛）
              if (typeof ev.data === 'string' && typeof (options.onEvent as unknown) === 'function') {
                try {
                  // 再尝试一次 string → unknown（最朴素的 fallback）
                  options.onEvent(ev.data as unknown as T)
                  lastEventAt.value = Date.now()
                  resetHeartbeat()
                } catch {
                  options.onError?.(err as Error)
                }
              } else {
                options.onError?.(err as Error)
              }
            }
          }
        }
      }

      // 流自然结束（非 abort 触发）
      if (!intentionalClose && !controller.signal.aborted) {
        options.onClose?.()
      }
    } catch (err) {
      // abort 后会抛 AbortError；不视为错误，仅停止
      if ((err as { name?: string }).name === 'AbortError') {
        return
      }
      options.onError?.(err as Error)
      scheduleReconnect()
    } finally {
      clearHeartbeat()
    }
  }

  function disconnect(): void {
    intentionalClose = true
    clearHeartbeat()
    clearReconnect()
    controller?.abort()
    controller = null
    state.value = 'closed'
  }

  function reconnect(): void {
    if (intentionalClose) {
      // disconnect 后想再开需重新 new useSseStream 实例
      return
    }
    controller?.abort()
    clearHeartbeat()
    clearReconnect()
    retryAttempt.value = 0
    void connect()
  }

  // 立即开始第一次连接
  void connect()

  // 组件 unmount 自动断开
  onUnmounted(() => {
    disconnect()
  })

  return {
    state: readonly(state) as Readonly<Ref<SseState>>,
    retryAttempt: readonly(retryAttempt) as Readonly<Ref<number>>,
    lastEventAt: readonly(lastEventAt) as Readonly<Ref<number>>,
    disconnect,
    reconnect,
  }
}
