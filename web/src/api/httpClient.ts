/**
 * V1.8.0 认证（T04）· 共享 axios 实例 + 401 自动刷新拦截器
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构 auth-architecture §1.4 + §4.2（US-5 / AC5-1~5-4）：
 *
 * - 请求拦截器：统一经 `getAuthHeaders()` 注入 `Authorization: Bearer <token>`
 *   （token 来源：authStore 内存 access 优先，无则 dev token——见 useJwtAuth）；
 * - 响应拦截器 401：**仅非 auth 端点自身**且未重试 → 单例 refresh
 *   （`refreshPromise` 并发去重，其余请求等待同一结果）→ 用新 access 重放原请求；
 * - refresh 也失败 → `authStore.clear()` + 跳 `/login?redirect=<当前页>`；
 * - SSE（fetch）不参与本拦截器（流中 401 直接报错，由下一次 REST 触发 refresh，
 *   架构共享知识 #9 可接受边界）。
 *
 * 循环依赖规避（架构共享知识 #11）：httpClient 与 authStore 相互引用——
 * 拦截器内一律**函数体内动态 import**，禁止模块级互相 import。
 *
 * chat.ts / auth.ts 等 API 模块统一复用本实例（既有 axios 行为零变化）。
 *
 * 作者：寇豆码（工程师）
 */
import axios from 'axios'
import type { AxiosError, InternalAxiosRequestConfig } from 'axios'
import { getAuthHeaders } from '../composables/useJwtAuth'

/** 基础 URL（默认 '/api'，Vite dev proxy → 9900；VITE_API_BASE 可覆盖）。
 *
 * 与旧 chat.ts resolveBaseUrl 逻辑一致（F4 修复：生产兜底相对路径，绝不
 * 回退 http://localhost:9900）。chat.ts 通过 `export { resolveBaseUrl }` 转发，
 * 保持既有 importers（knowledgeUpload / sessions / ChatView）零改动。
 */
export function resolveBaseUrl(): string {
  const metaEnv = (import.meta as { env?: Record<string, string | undefined> }).env
  if (typeof metaEnv?.VITE_API_BASE === 'string' && metaEnv.VITE_API_BASE.length > 0) {
    return metaEnv.VITE_API_BASE.replace(/\/$/, '')
  }
  const procEnv = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process?.env
  if (typeof procEnv?.VITE_API_BASE === 'string' && procEnv.VITE_API_BASE.length > 0) {
    return procEnv.VITE_API_BASE.replace(/\/$/, '')
  }
  return '/api'
}

const BASE = resolveBaseUrl()

/** 共享 axios 实例（chat.ts 复用；auth 端点也走本实例） */
const httpClient = axios.create({
  baseURL: BASE,
  timeout: 60000,
})

/** auth 端点自身（login/refresh/logout/dev-login）不做 401 重放——避免死循环 */
const AUTH_ENDPOINT_RE = /^\/auth\/(login|refresh|logout|dev-login)/

function isAuthEndpoint(url: string): boolean {
  return AUTH_ENDPOINT_RE.test(url)
}

/** 请求拦截器：注入 Bearer（getAuthHeaders 为空时不覆盖既有 header） */
httpClient.interceptors.request.use((config) => {
  const headers = getAuthHeaders()
  if (headers.Authorization && !config.headers.Authorization) {
    config.headers.Authorization = headers.Authorization
  }
  return config
})

/** 单例 refresh Promise（并发 401 去重，其余请求等待同一结果） */
let refreshPromise: Promise<string> | null = null

/** 跳转登录页（带 redirect 回跳；动态 import router 防循环依赖） */
async function redirectToLogin(): Promise<void> {
  try {
    const { default: router } = await import('../router')
    const current =
      typeof window !== 'undefined'
        ? window.location.pathname + window.location.search
        : '/'
    if (current !== '/login') {
      void router.push({ path: '/login', query: { redirect: current } })
    }
  } catch {
    // router 不可用（如初始化前）→ 忽略，由路由守卫兜底
  }
}

/** 响应拦截器：401 → 单例 refresh → 重放原请求 */
httpClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as
      | (InternalAxiosRequestConfig & { _retry?: boolean })
      | undefined
    if (!original) return Promise.reject(error)
    const url = original.url ?? ''

    // auth 端点自身 / 非 401 / 已重试过 → 直接抛出（不重放）
    if (isAuthEndpoint(url)) return Promise.reject(error)
    if (!error.response || error.response.status !== 401 || original._retry) {
      return Promise.reject(error)
    }

    try {
      original._retry = true
      if (!refreshPromise) {
        // 并发去重：同一时刻只发一次 /auth/refresh
        refreshPromise = (async () => {
          const { useAuthStore } = await import('../stores/auth')
          const store = useAuthStore()
          return store.refresh()
        })().finally(() => {
          refreshPromise = null
        })
      }
      const newAccess = await refreshPromise
      if (newAccess) {
        original.headers = original.headers ?? {}
        original.headers.Authorization = `Bearer ${newAccess}`
      }
      // 用新 access 重放原请求
      return httpClient(original)
    } catch (refreshError) {
      // refresh 也失效 → 清 token + 跳登录
      try {
        const { useAuthStore } = await import('../stores/auth')
        useAuthStore().clear()
      } catch {
        // ignore
      }
      await redirectToLogin()
      return Promise.reject(refreshError)
    }
  },
)

export default httpClient
