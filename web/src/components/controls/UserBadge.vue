<!--
  web/src/components/controls/UserBadge.vue · V1.8.0 真实登录（T05）
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  架构 auth-architecture §1.4 + §3.7 + PRD US-4（AC4-1~4-5）：

  - 点击 UserBadge 弹出 el-dropdown：
      · 头部：展示名 + 角色徽标（复用 ROLE_META）
      · 用户管理（仅 admin 显示）→ /admin/users
      · 切换账号 → clear() + /login?redirect=<当前页>
      · 退出登录 → authStore.logout() + /login?redirect=<当前页>
      · (dev) 以 X 角色登录（5 角色子菜单 → authStore.devLogin）
  - 未登录（生产）：点击 → /login?redirect=<当前页>（不弹下拉）；
    dev 匿名：弹下拉（含登录 + dev 快速登录）。
  - 展示名/角色：已登录读 authStore；dev 匿名回退 getJwtDisplayName/getJwtRole
    （dev 零破坏：不可解析 dev token → 访客/调度员）。
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
-->
<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ArrowDown,
  Lock,
  Management,
  SwitchButton,
  User,
  UserFilled,
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../../stores/auth'
import { getJwtDisplayName, getJwtRole, type JwtRole } from '../../composables/useJwtAuth'
import type { Role } from '../../types'

const props = withDefaults(
  defineProps<{
    /** el-dropdown 弹出位置（默认 bottom-end，Header 右侧贴边） */
    placement?: 'bottom' | 'bottom-start' | 'bottom-end'
    /** 触发方式（默认 click） */
    trigger?: 'click' | 'hover'
  }>(),
  { placement: 'bottom-end', trigger: 'click' },
)

const meta = (import.meta as { env?: Record<string, boolean | undefined> }).env
/** dev 模式（生产构建 false；决定是否渲染「以 X 角色登录」子菜单） */
const isDev = meta?.DEV === true
/** 生产构建（dev 不拦截 / 匿名点击直接跳登录） */
const isProd = meta?.PROD === true

const authStore = useAuthStore()
const route = useRoute()
const router = useRouter()

/** 角色 → 展示文案 / Element tag type（视觉区分） */
const ROLE_META: Record<JwtRole, { label: string; type: 'primary' | 'success' | 'warning' | 'info' | 'danger' }> = {
  dispatcher: { label: '调度员', type: 'primary' },
  operator: { label: '运维', type: 'success' },
  kb_admin: { label: '知识管理员', type: 'warning' },
  auditor: { label: '审计', type: 'info' },
  admin: { label: '管理员', type: 'danger' },
}

/** dev 快速登录角色清单（与后端 5 角色枚举一致） */
const DEV_ROLES: Array<{ value: Role; label: string }> = [
  { value: 'dispatcher', label: '调度员' },
  { value: 'operator', label: '运维' },
  { value: 'kb_admin', label: '知识管理员' },
  { value: 'auditor', label: '审计' },
  { value: 'admin', label: '管理员' },
]

const isAuthenticated = computed(() => authStore.isAuthenticated)
/** 展示名：已登录 → authStore；dev 匿名 → getJwtDisplayName（dev token） */
const displayName = computed<string>(() => authStore.user?.display_name || getJwtDisplayName())
const role = computed<JwtRole>(() => (authStore.user?.role as JwtRole | undefined) ?? getJwtRole())
const roleMeta = computed(() => ROLE_META[role.value] ?? ROLE_META.dispatcher)
const isAdmin = computed(() => role.value === 'admin')

/**
 * 是否渲染 el-dropdown：
 * - 生产匿名 → false（点击直接跳登录，不弹下拉）；
 * - dev 匿名 / 已登录 → true（dev 匿名含「登录 + 以 X 角色登录」）。
 */
const showDropdown = computed(() => !isProd || isAuthenticated.value)

/** 生产匿名点击：跳 /login?redirect=<当前页> */
function onAnonymousClick(): void {
  void router.push({ path: '/login', query: { redirect: route.fullPath } })
}

/** 下拉菜单命令分发（el-dropdown @command） */
async function onCommand(command: string | number | object): Promise<void> {
  const cmd = String(command)
  if (cmd === 'login') {
    void router.push({ path: '/login', query: { redirect: route.fullPath } })
    return
  }
  if (cmd === 'admin-users') {
    void router.push('/admin/users')
    return
  }
  if (cmd === 'switch-account') {
    // 切换账号：仅本地清 token（不调 logout），跳登录
    authStore.clear()
    void router.push({ path: '/login', query: { redirect: route.fullPath } })
    return
  }
  if (cmd === 'logout') {
    await authStore.logout()
    ElMessage.success('已退出登录')
    void router.push({ path: '/login', query: { redirect: route.fullPath } })
    return
  }
  if (cmd.startsWith('dev-login:')) {
    const roleValue = cmd.slice('dev-login:'.length) as Role
    try {
      await authStore.devLogin(roleValue)
      ElMessage.success(`已以「${ROLE_META[roleValue as JwtRole]?.label ?? roleValue}」身份登录（dev）`)
      if (route.path === '/login') {
        void router.replace(route.query.redirect ? String(route.query.redirect) : '/')
      }
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      ElMessage.error(detail || 'dev 登录失败')
    }
    return
  }
}
</script>

<template>
  <div class="user-badge-wrap" data-test="user-badge-wrap">
    <!-- 已登录 / dev 匿名：点击弹下拉 -->
    <el-dropdown
      v-if="showDropdown"
      class="user-badge-dropdown"
      :trigger="props.trigger"
      :placement="props.placement"
      data-test="user-badge-dropdown"
      @command="onCommand"
    >
      <div class="user-badge" data-test="user-badge" @click.stop>
        <el-icon class="user-badge__icon" :size="16"><User /></el-icon>
        <span class="user-badge__name" :title="displayName">{{ displayName }}</span>
        <el-tag
          size="small"
          :type="roleMeta.type"
          effect="dark"
          class="user-badge__role"
          data-test="user-badge-role"
        >
          {{ roleMeta.label }}
        </el-tag>
        <el-icon class="user-badge__caret" :size="12"><ArrowDown /></el-icon>
      </div>
      <template #dropdown>
        <el-dropdown-menu class="user-badge-menu">
          <!-- 头部：展示名 + 角色徽标 -->
          <div class="user-badge-menu__header" data-test="user-badge-header">
            <span class="user-badge-menu__name">{{ displayName }}</span>
            <el-tag size="small" :type="roleMeta.type" effect="dark">
              {{ roleMeta.label }}
            </el-tag>
          </div>

          <!-- 已登录：用户管理（admin）/ 切换账号 / 退出登录 -->
          <template v-if="isAuthenticated">
            <el-dropdown-item
              v-if="isAdmin"
              command="admin-users"
              data-test="user-badge-admin-users"
            >
              <el-icon><Management /></el-icon>用户管理
            </el-dropdown-item>
            <el-dropdown-item command="switch-account" divided data-test="user-badge-switch">
              <el-icon><SwitchButton /></el-icon>切换账号
            </el-dropdown-item>
            <el-dropdown-item command="logout" data-test="user-badge-logout">
              <el-icon><Lock /></el-icon>退出登录
            </el-dropdown-item>
          </template>

          <!-- 匿名（dev）：登录入口 -->
          <template v-else>
            <el-dropdown-item command="login" data-test="user-badge-login">
              <el-icon><UserFilled /></el-icon>登录
            </el-dropdown-item>
          </template>

          <!-- dev 专用：以 X 角色登录（生产构建不渲染） -->
          <template v-if="isDev">
            <el-dropdown-item
              v-for="(r, i) in DEV_ROLES"
              :key="r.value"
              :command="`dev-login:${r.value}`"
              :divided="i === 0"
              data-test="user-badge-dev-login"
            >
              <el-icon><UserFilled /></el-icon>以{{ r.label }}登录
            </el-dropdown-item>
          </template>
        </el-dropdown-menu>
      </template>
    </el-dropdown>

    <!-- 生产匿名：点击直接跳登录（不弹下拉） -->
    <div
      v-else
      class="user-badge"
      data-test="user-badge"
      @click="onAnonymousClick"
    >
      <el-icon class="user-badge__icon" :size="16"><User /></el-icon>
      <span class="user-badge__name" :title="displayName">{{ displayName }}</span>
      <el-tag
        size="small"
        :type="roleMeta.type"
        effect="dark"
        class="user-badge__role"
        data-test="user-badge-role"
      >
        {{ roleMeta.label }}
      </el-tag>
    </div>
  </div>
</template>

<style scoped lang="scss">
.user-badge-wrap {
  display: inline-flex;
  align-items: center;
}

.user-badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: 4px var(--space-3);
  border: 1px solid var(--border-default);
  border-radius: 999px;
  background: var(--bg-card);
  white-space: nowrap;
  cursor: pointer;
  transition: border-color var(--dur-fast) var(--ease-out-quint);
  user-select: none;

  &:hover {
    border-color: var(--brand-primary);
  }
}

.user-badge__icon {
  color: var(--brand-primary);
  flex-shrink: 0;
}

.user-badge__name {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.user-badge__role {
  flex-shrink: 0;
}

.user-badge__caret {
  color: var(--text-secondary);
  flex-shrink: 0;
}

/* 下拉菜单头部（非可点击项） */
.user-badge-menu__header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 8px 16px;
  border-bottom: 1px solid var(--border-default);
  margin-bottom: 4px;
}

.user-badge-menu__name {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
