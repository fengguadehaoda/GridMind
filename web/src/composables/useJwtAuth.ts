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
 *   - **不放在 localStorage**（防 XSS；架构 §1.5.2 红色风险）
 *   - **不放在 sessionStorage**（防 session 标签页劫持）
 *   - dev 默认 token 是 dev-only token，生产部署必须替换为真实登录流
 *
 * 作者：寇豆码（T01 工程师）
 */

/**
 * 读取 dev JWT token。
 *
 * 顺序：import.meta.env.VITE_DEV_JWT_TOKEN ?? 'gridmind-dev-token'
 *
 * `import.meta.env` 是 Vite 提供的类型安全方式；非 Vite 场景下退化为
 * process.env 或硬编码值。沙箱/单元测试环境通常返回 undefined。
 */
export function getJwtToken(): string {
  // Vite 在编译期将 import.meta.env.VITE_DEV_JWT_TOKEN 替换为字符串字面量
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
