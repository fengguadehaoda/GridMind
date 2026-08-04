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

import App from './App.vue'
import router from './router'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })
app.mount('#app')
