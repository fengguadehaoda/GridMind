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
  ],
})

/** 路由切换时同步 document.title（中文在前） */
router.afterEach((to) => {
  const base = '灵枢电网 · GridMind'
  const pageTitle = to.meta?.title as string | undefined
  document.title = pageTitle ? `${pageTitle} · 控制中心` : `${base} · 控制中心`
})

export default router
