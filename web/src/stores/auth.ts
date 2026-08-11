/**
 * V1.8.0 认证（T04）· authStore（Pinia）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构 auth-architecture §3.6 + PRD US-3/US-5：
 *
 * State：
 * - `accessToken`：**仅内存**（F5 后由 hydrate() 用 refresh 恢复）
 * - `user`：当前用户摘要
 * - `status`：'idle' | 'loading' | 'authenticated' | 'anonymous'
 * - `redirectTarget`：路由守卫记录的 redirect（登录成功后回跳）
 *
 * Actions：
 * - login / devLogin：POST → access 内存 + refresh localStorage + user
 * - refresh：POST /auth/refresh → 轮换双 token（供 401 拦截器复用）
 * - logout：尽力 revoke + clear
 * - fetchMe：GET /auth/me → 校验会话 + 刷新 user（password_expiring 提醒）
 * - hydrate：启动时用 refresh 恢复会话（失败 → clear）
 * - clear：清空（不跳转）
 *
 * 循环依赖规避：authStore → api/auth → httpClient → useJwtAuth，
 * useJwtAuth 经 getActivePinia 懒读本 store（无模块级互 import）。
 *
 * 作者：寇豆码（工程师）
 */
import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import * as authApi from '../api/auth'
import type { AuthUser, LoginResponse, Role } from '../types'

/** refresh token 的 localStorage key（与 api/auth.ts 常量一致） */
export const REFRESH_TOKEN_KEY = authApi.REFRESH_TOKEN_KEY

export type AuthStatus = 'idle' | 'loading' | 'authenticated' | 'anonymous'

/**
 * store 级单例 refresh Promise（P1 竞态修复，QA 记档项）。
 *
 * **问题**：受保护路由整页加载时存在多路并发 ``store.refresh()``：
 *   1. ``main.ts`` ``void useAuthStore().hydrate()``（fire-and-forget）；
 *   2. 生产路由守卫 ``status==='idle' && getRefreshToken()`` 再 ``await hydrate()``；
 *   3. httpClient 401 拦截器（首帧请求未带 access → 401 → 触发 refresh）。
 * 三路都会调用 ``refresh()`` → 对**同一 refresh token** 发起并发 /auth/refresh →
 * 后端轮换使先到者成功、后到者 401 → ``clear()`` → 已登录用户被误登出。
 *
 * **修复**：本模块级 ``refreshInFlight`` 使同一时刻所有 ``refresh()`` 调用共享
 * 同一个 /auth/refresh 请求（hydrate / 路由守卫 / 401 拦截器全部收敛为一次轮换），
 * 其余调用 await 同一 Promise 拿同一新 access。配合后端 refresh 轮换原子化
 * （BEGIN IMMEDIATE）双保险。
 */
let refreshInFlight: Promise<string> | null = null

export const useAuthStore = defineStore('auth', () => {
  // ── State ──
  /** access token（仅内存；F5 后经 hydrate() 恢复） */
  const accessToken = ref<string | null>(null)
  /** 当前用户摘要 */
  const user = ref<AuthUser | null>(null)
  /** 会话状态 */
  const status = ref<AuthStatus>('idle')
  /** 路由守卫记录的 redirect（登录成功后回跳） */
  const redirectTarget = ref<string | null>(null)

  // ── Getters ──
  const isAuthenticated = computed(
    () => status.value === 'authenticated' && !!accessToken.value,
  )
  const role = computed<Role>(() => user.value?.role ?? 'dispatcher')
  const displayName = computed<string>(
    () => user.value?.display_name || user.value?.username || '访客',
  )

  // ── Actions ──

  /** 登录：POST /auth/login → access 内存 + refresh localStorage + user */
  async function login(username: string, password: string): Promise<void> {
    status.value = 'loading'
    try {
      const resp = await authApi.login(username, password)
      applyLoginResponse(resp)
    } catch (err) {
      status.value = 'anonymous'
      throw err
    }
  }

  /**
   * 注册：POST /auth/register → 复用 applyLoginResponse（注册即登录）。
   * 默认角色 dispatcher；must_change_password=0（用户自设密码，无需首次改密）。
   */
  async function register(username: string, password: string, email?: string): Promise<void> {
    status.value = 'loading'
    try {
      const resp = await authApi.register({ username, password, email: email ?? null })
      applyLoginResponse(resp)
    } catch (err) {
      status.value = 'anonymous'
      throw err
    }
  }

  /** dev 快速登录（仅 dev；生产 404 fail-closed） */
  async function devLogin(roleValue: Role): Promise<void> {
    status.value = 'loading'
    try {
      const resp = await authApi.devLogin(roleValue)
      applyLoginResponse(resp)
    } catch (err) {
      status.value = 'anonymous'
      throw err
    }
  }

  /**
   * 刷新：POST /auth/refresh → 轮换双 token → 返回新 access。
   * 供 httpClient 401 拦截器复用（单例 refreshPromise 并发去重）。
   *
   * P1 修复：模块级 ``refreshInFlight`` 单例去重——hydrate / 路由守卫 /
   * 401 拦截器并发调用时共享同一 /auth/refresh 请求（同一 refresh token
   * 只轮换一次，杜绝后端轮换竞态导致误登出）。
   */
  async function refresh(): Promise<string> {
    // 已有 in-flight 轮换 → 直接复用（并发去重核心）
    if (refreshInFlight) return refreshInFlight

    const rt = authApi.getRefreshToken()
    if (!rt) {
      clear()
      throw new Error('no refresh token')
    }

    refreshInFlight = (async () => {
      const resp = await authApi.refresh(rt)
      applyLoginResponse(resp)
      return resp.access_token
    })().finally(() => {
      refreshInFlight = null
    })
    return refreshInFlight
  }

  /** 登出：尽力 revoke refresh → clear（不抛错） */
  async function logout(): Promise<void> {
    const rt = authApi.getRefreshToken()
    if (rt) {
      try {
        await authApi.logout(rt)
      } catch {
        // 尽力而为：后端不可达也继续本地清除
      }
    }
    clear()
  }

  /** GET /auth/me：校验会话 + 刷新 user（90 天过期提醒字段） */
  async function fetchMe(): Promise<void> {
    if (!accessToken.value) return
    const me = await authApi.fetchMe()
    user.value = {
      ...(user.value ?? { id: me.id, username: me.username, display_name: me.display_name, role: me.role }),
      id: me.id,
      username: me.username,
      display_name: me.display_name,
      role: me.role,
    }
    status.value = 'authenticated'
  }

  /** 启动水合：有 refresh → refresh() 恢复；失败 → clear() */
  async function hydrate(): Promise<void> {
    const rt = authApi.getRefreshToken()
    if (!rt) {
      status.value = 'anonymous'
      return
    }
    try {
      await refresh()
    } catch {
      clear()
    }
  }

  /** 清空本地会话（不跳转；跳转由调用方 / 路由守卫负责） */
  function clear(): void {
    accessToken.value = null
    user.value = null
    status.value = 'anonymous'
    authApi.setRefreshToken(null)
  }

  /** 记录 redirect（路由守卫调用） */
  function setRedirect(target: string | null): void {
    redirectTarget.value = target
  }

  /** 读取并清除 redirect（登录成功后消费） */
  function consumeRedirect(): string | null {
    const target = redirectTarget.value
    redirectTarget.value = null
    return target
  }

  /** 内部：统一应用登录/刷新响应（access 内存 + refresh localStorage + user） */
  function applyLoginResponse(resp: LoginResponse): void {
    accessToken.value = resp.access_token
    authApi.setRefreshToken(resp.refresh_token)
    user.value = resp.user
    status.value = 'authenticated'
  }

  return {
    accessToken,
    user,
    status,
    redirectTarget,
    isAuthenticated,
    role,
    displayName,
    login,
    register,
    devLogin,
    refresh,
    logout,
    fetchMe,
    hydrate,
    clear,
    setRedirect,
    consumeRedirect,
  }
})
