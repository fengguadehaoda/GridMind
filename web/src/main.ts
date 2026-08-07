// ─── 应用入口 ────────────────────────────────
// 顺序：tokens → element-overrides → reset → animations → utilities
// 1. tokens 必须在最先（包含所有 CSS 变量）
// 2. element-overrides 覆写 Element Plus 默认主题
// 3. reset/animations/utilities 提供全局基础样式

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'

// 样式加载顺序很关键
import './styles/tokens.scss'           // CSS 变量入口
import 'element-plus/dist/index.css'    // EP 默认样式（必须在覆写前）
import './styles/element-overrides.scss' // EP 主题覆盖
import './styles/reset.scss'            // 全局 reset
import './styles/animations.scss'       // 关键帧
import './styles/utilities.scss'        // 工具类
// driver.js 自带的 CSS（自动通过其包内 sideEffects 引入；如未引入则手动加载）
import 'driver.js/dist/driver.css'

import App from './App.vue'
import router from './router'
// v1.5.0 T01: 显示策略 store + onboarding store 的 hydrate 必须在 mount 前完成
// 详见架构文档 §3.3 + 任务说明
import { useDisplayStore } from './stores/display'
import { useOnboardingStore } from './stores/onboarding'
import { useReasoningStore } from './stores/reasoning'
import { useAuditStore } from './stores/audit'
import { setupOnboardingGuard } from './composables/useOnboarding'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
app.use(router)
app.use(ElementPlus, { locale: zhCn })

// ─── v1.5.1 T06 端到端联调 · e2e 基础设施（≤ 3 行） ───
// 仅 dev 模式下挂载 pinia 实例到 window，便于 Playwright e2e 通过
// `window.__pinia._s.get('reasoning')` 驱动状态机进入 running 态。
// 这是测试基础设施最小侵入：生产构建 (import.meta.env.PROD === true) 自动剥离。
// 不属于 F1-F4 UI 组件改动，与 T01-T05 单测套件完全隔离。
if ((import.meta as { env?: Record<string, unknown> }).env?.DEV) {
  ;(window as Window & { __pinia?: typeof pinia }).__pinia = pinia
}

// v1.5.0 T01: store hydrate 顺序（必须在 router 解析任何路由前）
// - display.hydrate()  读 gridmind.displayMode / gridmind.colorBlindPalette
//                     → 同步 :root[data-display-mode] / :root[data-cb-palette]
//                     → CSS 选择器瞬时切 token，零重渲染
// - onboarding.hydrate() 读 gridmind.onboarded / gridmind.onboardedAt /
//                        gridmind.onboarding.scenarioId
//                        → T04 路由守卫会用到 hasOnboarded
//
// v1.5.1 T01（架构 §5 T01 第 8 项）：
// - reasoning.hydrate() 读 gridmind.reattach_thread_id（暂停后刷新页面场景）
// - audit.hydrate()      立即拉一次 /audit/pending-count + 启动 5s 轮询
//                       → HitlBadge 首屏即可见，不需要等 5s
useDisplayStore().hydrate()
useOnboardingStore().hydrate()
useReasoningStore().hydrate()
useAuditStore().hydrate()

// v1.5.0 T04: 注册路由守卫（未完成 onboarding → 自动跳转 /onboarding）
// 必须在 app.mount('#app') 之前注册，router 解析首屏路由时即生效
setupOnboardingGuard(router)

app.mount('#app')
