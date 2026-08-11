// ─── Vue Router ──────────────────────────────
// 路由列表（v1.5.0）：
//   /             智能对话（默认）
//   /monitor      实时监控
//   /grayscale    灰度面板
//   /audit        HITL 审计
//   /system       系统总览
//   /onboarding   新调度员引导 wizard（v1.5.0 P0-4 新增）
//   /help         帮助中心（v1.6.0 P1-2 新增，懒加载）
//
// V1.8.0 认证（T05）新增：
//   /login        登录页（public）
//   /admin/users  用户管理（meta.roles=['admin']；后端 require_role 兜底）
//
// 全局守卫：setupOnboardingGuard（main.ts）+ setupAuthGuard（main.ts，
// 仅 import.meta.env.PROD 生效——dev 不拦截保持本地零登录体验）

import { createRouter, createWebHistory, type Router } from 'vue-router'
import { ElMessage } from 'element-plus'
import ChatView from '../components/ChatView.vue'
import { useAuthStore } from '../stores/auth'
import { getRefreshToken } from '../api/auth'
import type { Role } from '../types'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'chat',
      component: ChatView,
      meta: { title: '对话 · 灵枢电网' },
    },
    {
      path: '/monitor',
      name: 'monitor',
      component: () => import('../components/MonitoringView.vue'),
      meta: { title: '实时监控 · 灵枢电网' },
    },
    {
      // M3c 可观测性：灰度可视化面板
      path: '/grayscale',
      name: 'grayscale',
      component: () => import('../views/GrayscalePanel.vue'),
      meta: { title: '灰度面板 · 灵枢电网' },
    },
    {
      // v1.4.0：HITL 审计日志
      path: '/audit',
      name: 'audit',
      component: () => import('../views/AuditLogViewer.vue'),
      meta: { title: 'HITL 审计 · 灵枢电网' },
    },
    {
      // v1.4.0：系统总览（聚合 metrics + grayscale + 模型）
      path: '/system',
      name: 'system',
      component: () => import('../views/SystemOverview.vue'),
      meta: { title: '系统总览 · 灵枢电网' },
    },
    {
      // v1.5.0 P0-4 新增：新手引导 wizard
      path: '/onboarding',
      name: 'onboarding',
      component: () => import('../views/OnboardingView.vue'),
      meta: { title: '新手引导 · 灵枢电网', public: true },
    },
    {
      // v1.6.0 P1-2 新增：帮助中心（Markdown 渲染 + 全文搜索 + 目录）
      // V1.7 KB Upload：支持 ?tab=knowledge 直达知识库管理 Tab
      //   （HelpCenter.vue 内部读取 route.query.tab 切换；不改路径结构）
      path: '/help',
      name: 'help',
      component: () => import('../views/HelpCenter.vue'),
      meta: { title: '帮助中心 · 灵枢电网' },
    },
    {
      // V1.7.0 F-1 新增：大屏模式占位路由（仅接口预留，不实现大屏 UI）
      path: '/bigscreen',
      name: 'bigscreen',
      component: () => import('../views/BigScreenPlaceholder.vue'),
      meta: { title: '大屏模式 · 灵枢电网', public: true },
    },
    {
      // V1.8.0 认证（T05）：登录页（public，幂等可直达）
      path: '/login',
      name: 'login',
      component: () => import('../views/LoginView.vue'),
      meta: { title: '登录 · 灵枢电网', public: true },
    },
    {
      // V1.8.0 认证（T05）：用户管理页（仅 admin；后端 require_role 兜底）
      path: '/admin/users',
      name: 'admin-users',
      component: () => import('../views/UsersView.vue'),
      meta: { title: '用户管理 · 灵枢电网', roles: ['admin'] as Role[] },
    },
  ],
})

/** 路由切换时同步 document.title（中文在前） */
router.afterEach((to) => {
  const base = '灵枢电网 · GridMind'
  const pageTitle = to.meta?.title as string | undefined
  document.title = pageTitle ? `${pageTitle} · 控制中心` : `${base} · 控制中心`
})

/**
 * V1.8.0 认证（T05）· 生产路由守卫（仅 `import.meta.env.PROD` 生效）。
 *
 * 语义（PRD US-1 + 架构 §1.4）：
 * - dev（PROD=false）→ 不注册，保持本地零登录体验（AC10-1）；
 * - 生产：
 *   a. public 路由（/login、/onboarding、/bigscreen）→ 放行；
 *   b. 首帧 hydrate 未完成（status==='idle'）且有 refresh → 等待恢复，
 *      防已登录用户被误跳登录页（AC5-3 无感续期）；
 *   c. 未登录访问受保护路由 → `/login?redirect=<fullPath>`（AC1-1）；
 *   d. `/admin/users` 非 admin → 403 提示 + 回首页（AC6-1 前端 UX，
 *      安全由后端 require_role(ADMIN) 兜底）。
 */
export function setupAuthGuard(routerInstance: Router): void {
  const meta = (import.meta as { env?: Record<string, boolean | undefined> }).env
  if (meta?.PROD !== true) return

  routerInstance.beforeEach(async (to) => {
    if (to.meta?.public) return true

    const store = useAuthStore()
    // 首帧 hydrate 未完成且存在 refresh → 等待恢复（防误跳登录页）
    if (store.status === 'idle' && getRefreshToken()) {
      await store.hydrate()
    }
    if (!store.isAuthenticated) {
      store.setRedirect(to.fullPath)
      return { path: '/login', query: { redirect: to.fullPath } }
    }

    const roles = to.meta?.roles as Role[] | undefined
    if (roles && !roles.includes(store.role)) {
      ElMessage.error('权限不足')
      return { path: '/' }
    }
    return true
  })
}

export default router
