/**
 * V1.8.0 认证（T04）· /auth/* + /users* API 客户端
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 全部走共享 httpClient（请求自动带 Bearer；401 自动 refresh 重放）。
 *
 * refresh token 存储约定（主理人拍板 #7）：
 * - access token **仅存内存（authStore）**，F5 后由 hydrate() 用 refresh 恢复；
 * - refresh token 存 localStorage `gridmind.refresh_token`（仅用于换 access）。
 *
 * 作者：寇豆码（工程师）
 */
import httpClient from './httpClient'
import type {
  ChangePasswordRequest,
  DevLoginRequest,
  LoginResponse,
  LogoutResponse,
  MeResponse,
  RbacMatrixResponse,
  RegisterRequest,
  Role,
  UserCreateRequest,
  UserSummary,
  UsersListResponse,
  UserUpdateRequest,
} from '../types'

/** refresh token 的 localStorage key（架构 §八 待明确 #7） */
export const REFRESH_TOKEN_KEY = 'gridmind.refresh_token'

/** 读取本地 refresh token（localStorage 不可用时返回 null） */
export function getRefreshToken(): string | null {
  try {
    return localStorage.getItem(REFRESH_TOKEN_KEY)
  } catch {
    return null
  }
}

/** 写入 / 清除本地 refresh token（null = 清除） */
export function setRefreshToken(token: string | null): void {
  try {
    if (token) {
      localStorage.setItem(REFRESH_TOKEN_KEY, token)
    } else {
      localStorage.removeItem(REFRESH_TOKEN_KEY)
    }
  } catch {
    // localStorage 不可用（隐私模式等）→ 忽略，仅内存会话
  }
}

/* ── 认证 ─────────────────────────────────────────────────── */

/** POST /auth/login — 用户名+密码 → access+refresh */
export async function login(username: string, password: string): Promise<LoginResponse> {
  const { data } = await httpClient.post<LoginResponse>('/auth/login', {
    username,
    password,
  })
  return data
}

/** POST /auth/register — 开放注册（默认角色 dispatcher，注册即登录） */
export async function register(payload: RegisterRequest): Promise<LoginResponse> {
  const { data } = await httpClient.post<LoginResponse>('/auth/register', payload)
  return data
}

/** GET /rbac/matrix — 角色×端点权限矩阵（仅 admin；dev 放行、X-Admin-Token 等效） */
export async function fetchRbacMatrix(): Promise<RbacMatrixResponse> {
  const { data } = await httpClient.get<RbacMatrixResponse>('/rbac/matrix')
  return data
}

/** POST /auth/refresh — refresh → 新 access + 轮换后新 refresh */
export async function refresh(refreshToken: string): Promise<LoginResponse> {
  const { data } = await httpClient.post<LoginResponse>('/auth/refresh', {
    refresh_token: refreshToken,
  })
  return data
}

/** POST /auth/logout — revoke refresh（幂等；尽力而为） */
export async function logout(refreshToken: string): Promise<LogoutResponse> {
  const { data } = await httpClient.post<LogoutResponse>('/auth/logout', {
    refresh_token: refreshToken,
  })
  return data
}

/** GET /auth/me — 当前用户信息（含密码过期提醒） */
export async function fetchMe(): Promise<MeResponse> {
  const { data } = await httpClient.get<MeResponse>('/auth/me')
  return data
}

/** POST /auth/change-password — 改密（撤销该用户全部 refresh） */
export async function changePassword(payload: ChangePasswordRequest): Promise<{ ok: boolean }> {
  const { data } = await httpClient.post<{ ok: boolean }>('/auth/change-password', payload)
  return data
}

/** POST /auth/dev-login — 仅 dev：签发带 role claim 的真实 JWT（生产 404） */
export async function devLogin(role: Role): Promise<LoginResponse> {
  const { data } = await httpClient.post<LoginResponse>('/auth/dev-login', {
    role,
  } satisfies DevLoginRequest)
  return data
}

/* ── 用户管理（仅管理员，生产强制）────────────────────────── */

/** GET /users 列表参数（role/disabled/q/page/page_size） */
export interface FetchUsersParams {
  role?: Role
  disabled?: number
  q?: string
  page?: number
  page_size?: number
}

/** GET /users — 用户列表（不含 password_hash） */
export async function fetchUsers(params: FetchUsersParams = {}): Promise<UsersListResponse> {
  const { data } = await httpClient.get<UsersListResponse>('/users', { params })
  return data
}

/** POST /users — 创建用户（默认 must_change_password=1） */
export async function createUser(payload: UserCreateRequest): Promise<UserSummary> {
  const { data } = await httpClient.post<UserSummary>('/users', payload)
  return data
}

/** PATCH /users/{id} — 改角色 / 禁用 / 改密 */
export async function updateUser(userId: string, payload: UserUpdateRequest): Promise<UserSummary> {
  const { data } = await httpClient.patch<UserSummary>(
    `/users/${encodeURIComponent(userId)}`,
    payload,
  )
  return data
}
