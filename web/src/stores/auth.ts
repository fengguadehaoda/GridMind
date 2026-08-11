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
   */
  async function refresh(): Promise<string> {
    const rt = authApi.getRefreshToken()
    if (!rt) {
      clear()
      throw new Error('no refresh token')
    }
    const resp = await authApi.refresh(rt)
    applyLoginResponse(resp)
    return resp.access_token
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
