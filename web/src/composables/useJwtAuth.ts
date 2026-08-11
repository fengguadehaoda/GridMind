/**
 * v1.5.1 T01 基础设施 · JWT 鉴权辅助 composable
 *
 * 设计依据：
 *   - 主理人决策 7.1（A 方案）：
 *     "T01 加 VITE_DEV_JWT_TOKEN 环境变量，默认 gridmind-dev-token；
 *      生产部署时再接真实登录流"
 *   - 架构 §6.1 JWT 注入
 *   - 后端架构 SSE 鉴权已修复（R-X2 已 PASS）—— 前端必须注入 Bearer
 *
 * V1.8.0 认证（T04）增量（架构 auth-architecture F06 + 共享知识 #11）：
 *   - ``getJwtToken()`` 优先读 authStore 内存 access token（真实登录后），
 *     无则回退 dev token（dev 本地零破坏）；
 *   - 读 store 采用 ``getActivePinia()`` + ``_s.get('auth')``（懒读取），
 *     避免模块级循环 import（stores/auth → api/auth → httpClient → useJwtAuth）。
 *
 * 使用：
 *   import { getJwtToken, getAuthHeaders } from '@/composables/useJwtAuth'
 *
 *   // 普通 REST
 *   const headers = { 'Content-Type': 'application/json', ...getAuthHeaders() }
 *
 *   // SSE（useSseStream composable 内部已注入；外部不必重复加）
 *   const token = getJwtToken()
 *
 * 注意：
 *   - **access token 不存 localStorage**（防 XSS；架构 §1.5.2 红色风险；
 *     V1.8.0 拍板 #7：access 仅内存 / refresh 存 localStorage）
 *   - dev 默认 token 是 dev-only token，生产部署必须替换为真实登录流
 *
 * 作者：寇豆码（T01 工程师）
 */
import { getActivePinia } from 'pinia'

/**
 * 读取当前 JWT token。
 *
 * 顺序：
 * 1. authStore 内存 access token（真实登录后；getActivePinia 懒读防循环）；
 * 2. dev JWT：import.meta.env.VITE_DEV_JWT_TOKEN ?? 'gridmind-dev-token'
 *    （非 Vite 场景退化为 process.env 或硬编码值）。
 */
export function getJwtToken(): string {
  // 1. 真实登录：authStore.accessToken 优先（lazy 读取，避免模块级循环 import）
  try {
    const pinia = getActivePinia()
    if (pinia) {
      const stores = (pinia as unknown as { _s: Map<string, { accessToken: string | null }> })._s
      const store = stores.get('auth')
      if (store && typeof store.accessToken === 'string' && store.accessToken.length > 0) {
        return store.accessToken
      }
    }
  } catch {
    // 无 active pinia（Node 单测 / 初始化前）→ 回退 dev token
  }

  // 2. dev token 回退（Vite 在编译期将 VITE_DEV_JWT_TOKEN 替换为字符串字面量）
  const fromEnv = (import.meta as { env?: Record<string, string | undefined> }).env?.VITE_DEV_JWT_TOKEN
  if (typeof fromEnv === 'string' && fromEnv.length > 0) {
    return resolveDevToken(fromEnv)
  }
  // 兼容 Node 单测（import.meta.env 不存在）
  const procRef = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process
  if (procRef && procRef.env && typeof procRef.env.VITE_DEV_JWT_TOKEN === 'string') {
    return resolveDevToken(procRef.env.VITE_DEV_JWT_TOKEN)
  }
  return resolveDevToken(DEV_DEFAULT_JWT_TOKEN)
}

/**
 * F5 修复（QA F5 P1）· fail-closed 分支：
 * 生产构建（import.meta.env.PROD === true，Vite 编译期注入）下，
 * 若 token 仍是可预测的 dev 默认值 / 占位符（生产未注入真实 JWT）
 * → 返回空串（getAuthHeaders 将不携带默认凭据，请求 fail-closed）。
 * dev 模式保持现状（后端 dev 端接受 gridmind-dev-token）。
 */
function resolveDevToken(token: string): string {
  const isProd = (import.meta as { env?: Record<string, boolean | undefined> }).env?.PROD === true
  if (isProd && (token === DEV_DEFAULT_JWT_TOKEN || token === DEV_TOKEN_PLACEHOLDER)) {
    // eslint-disable-next-line no-console
    console.warn('[useJwtAuth] 生产环境检测到 dev 默认/占位 token，已 fail-closed（不发送鉴权头）')
    return ''
  }
  return token
}

/**
 * 生成标准 `Authorization` header 对象。
 *
 * 后端 R-X2 已修复：SSE `/sessions/{id}/events` 端点通过
 * `Depends(verify_thread_ownership)` 校验 `Authorization: Bearer <jwt>`。
 *
 * @returns `{ Authorization: 'Bearer <token>' }` —— token 为空时返回空对象
 */
export function getAuthHeaders(): Record<string, string> {
  const token = getJwtToken()
  if (!token) return {}
  return { Authorization: `Bearer ${token}` }
}

/**
 * 默认 token 常量（仅在所有读取来源都失败时使用）。
 *
 * 主理人决策 7.1：默认 dev token = `gridmind-dev-token`。
 * 后端 dev 端会以 `Authorization: Bearer gridmind-dev-token` 通过校验。
 */
export const DEV_DEFAULT_JWT_TOKEN = 'gridmind-dev-token' as const

/** 生产占位符（web/.env 中未注入真实 token 时的占位值；fail-closed 检测用） */
const DEV_TOKEN_PLACEHOLDER = 'REPLACE_ME_IN_PRODUCTION' as const

/* ═══════════════════════════════════════════════════════════
 * M-5 角色感知 UI（T05 · 方案 A：前端 base64url 解码 JWT payload）
 *
 * 职责边界（PRD §3.2/§六 + 架构 §7 #3）：
 *   - 前端角色解析**仅承担展示层 UX**，不承担安全职责；
 *   - 安全由后端 RBAC + owner 校验兜底（403/404 fail-closed）；
 *   - 解析失败 / 缺失 / 未知 role（含 dev token `gridmind-dev-token`）
 *     → 默认 `dispatcher`（fail-closed 到最保守的展示层），**绝不抛错**。
 * ═══════════════════════════════════════════════════════════ */

/** M-5 角色（与后端 5 角色枚举对齐；见 types/index.ts Role） */
export type JwtRole = 'dispatcher' | 'operator' | 'kb_admin' | 'auditor' | 'admin'

/** 合法 role claim 值集合（与后端 ROLE_VALUES 一致） */
const JWT_ROLE_VALUES: ReadonlySet<string> = new Set([
  'dispatcher',
  'operator',
  'kb_admin',
  'auditor',
  'admin',
])

/**
 * base64url 解码 JWT 中段（payload）。
 *
 * @param token - JWT 字符串（`header.payload.signature`）
 * @returns 解码后的 payload 对象；格式非法 / 解码失败 → null（不抛错）
 */
export function parseJwtPayload(token: string): Record<string, unknown> | null {
  if (!token || typeof token !== 'string') return null
  const parts = token.split('.')
  if (parts.length !== 3) return null
  const payloadPart = parts[1]
  if (!payloadPart) return null
  try {
    // base64url → base64（补齐 padding，替换 URL 安全字符）
    let b64 = payloadPart.replace(/-/g, '+').replace(/_/g, '/')
    while (b64.length % 4 !== 0) b64 += '='
    const decoded = decodeURIComponent(
      Array.prototype.map
        .call(atob(b64), (c: string) => `%${c.charCodeAt(0).toString(16).padStart(2, '0')}`)
        .join(''),
    )
    const obj: unknown = JSON.parse(decoded)
    return obj && typeof obj === 'object' ? (obj as Record<string, unknown>) : null
  } catch {
    // 解码失败（dev token / 非法 base64 / 非 JSON）→ null（fail-closed）
    return null
  }
}

/**
 * 解析当前 JWT 的 `role` claim → 角色（缺省 dispatcher）。
 *
 * 规则（架构 §7 #3）：合法值 `dispatcher|operator|kb_admin|auditor|admin`
 * → 对应 Role；缺失 / 未知 / 解码失败（含 dev token）→ `dispatcher`。
 */
export function getJwtRole(): JwtRole {
  const token = getJwtToken()
  const payload = parseJwtPayload(token)
  const raw = payload?.role
  if (typeof raw === 'string' && JWT_ROLE_VALUES.has(raw.trim().toLowerCase())) {
    return raw.trim().toLowerCase() as JwtRole
  }
  return 'dispatcher'
}

/** 从 JWT 解析 user_id（兼容 sub / user_id 双命名；缺失 → null） */
export function getJwtUserId(): string | null {
  const token = getJwtToken()
  const payload = parseJwtPayload(token)
  if (!payload) return null
  const uid = payload.user_id ?? payload.sub
  return typeof uid === 'string' && uid.length > 0 ? uid : null
}

/**
 * 从 JWT 解析展示名：优先 `name` claim，其次 user_id 截断，缺省「访客」。
 */
export function getJwtDisplayName(): string {
  const token = getJwtToken()
  const payload = parseJwtPayload(token)
  if (payload && typeof payload.name === 'string' && payload.name.trim()) {
    return payload.name.trim()
  }
  const uid = getJwtUserId()
  if (uid) return uid.length > 12 ? `${uid.slice(0, 12)}…` : uid
  return '访客'
}
