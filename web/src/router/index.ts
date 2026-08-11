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
// 全局守卫：setupOnboardingGuard 在 main.ts 中注册

import { createRouter, createWebHistory } from 'vue-router'
import ChatView from '../components/ChatView.vue'

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
  ],
})

/** 路由切换时同步 document.title（中文在前） */
router.afterEach((to) => {
  const base = '灵枢电网 · GridMind'
  const pageTitle = to.meta?.title as string | undefined
  document.title = pageTitle ? `${pageTitle} · 控制中心` : `${base} · 控制中心`
})

export default router
