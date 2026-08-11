<template>
  <el-container
    class="app-container"
    :class="{
      'app-container--compact': isCompact,
      'app-container--large': isLarge,
      'app-container--card-visible': statusCardVisible,
      'app-container--card-expanded': statusCardVisible && !statusCardCollapsed,
    }"
  >
    <!-- 顶栏 — 精简为 ≤5 元素（header-redesign T03）：
         ①Logo ②主导航（5 路由 / compact 并入 NavDrawer 汉堡）
         ③「菜单」按钮 ④帮助图标 ⑤「更多」折叠点（<768px fallback） -->
    <el-header class="app-header">
      <!-- ① 左：可点击 Logo + 品牌名（点回主页 /） -->
      <router-link to="/" class="header-brand" data-test="header-brand" title="返回主页 · 智能对话">
        <LogoHorizontal :size="36" />
      </router-link>

      <!-- ② 中：水平导航（standard/large 显示；compact 折叠为汉堡 NavDrawer）
           M-5 T05：数据驱动 + roles 过滤（visibleNavItems） -->
      <nav v-if="!isCompact" class="app-nav-wrap" data-test="header-nav">
        <el-menu
          class="app-nav"
          mode="horizontal"
          router
          :default-active="route.path"
          :ellipsis="true"
        >
          <el-menu-item
            v-for="item in visibleNavItems"
            :key="item.path"
            :index="item.path"
          >
            <el-icon><component :is="item.icon" /></el-icon>
            {{ item.label }}
          </el-menu-item>
        </el-menu>
      </nav>
      <!-- compact：汉堡导航抽屉（内部同样按 roles 过滤） -->
      <NavDrawer v-if="isCompact" v-model="navOpen" />

      <!-- 右侧：④帮助图标 ③「菜单」按钮 ⑤「更多」折叠点（移动端）+ M-5 用户徽标 -->
      <div class="header-right">
        <!-- M-5 T05 · AC3-1：用户名 + 角色徽标（Header 右侧） -->
        <UserBadge />

        <!-- ④ 帮助图标 -->
        <el-tooltip content="帮助中心" placement="bottom" :show-after="200">
          <button
            type="button"
            class="help-entry"
            data-test="help-entry"
            aria-label="打开帮助中心"
            @click="goHelp"
          >
            <el-icon :size="16"><QuestionFilled /></el-icon>
          </button>
        </el-tooltip>

        <!-- ③ 「菜单」按钮（主按钮样式，触发右侧 MenuDrawer） -->
        <button
          type="button"
          class="menu-trigger"
          data-test="header-menu-trigger"
          aria-label="打开菜单"
          aria-haspopup="dialog"
          @click="menuOpen = true"
        >
          <el-icon :size="16"><MenuIcon /></el-icon>
          <span>菜单</span>
        </button>

        <!-- ⑤ 「更多」折叠点（<768px fallback：收纳新对话 / 知识库管理等溢出项） -->
        <el-dropdown
          v-if="isMobile"
          trigger="click"
          class="header-more"
          data-test="header-more-trigger"
          @command="onMoreCommand"
        >
          <button type="button" class="more-entry" aria-label="更多功能">
            <el-icon :size="16"><MoreFilled /></el-icon>
          </button>
          <template #dropdown>
            <el-dropdown-menu class="header-more-menu">
              <el-dropdown-item command="new-chat" data-test="more-new-chat">新对话</el-dropdown-item>
              <el-dropdown-item command="knowledge" data-test="more-knowledge">知识库管理</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </el-header>

    <!-- 主体 -->
    <el-main class="app-main">
      <router-view v-slot="{ Component }">
        <transition name="fade-page" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </el-main>

    <!-- 全局返回主页浮动按钮（仅非主页显示；compact 图标化）
         与 StatusFloatingCard 几何错开：卡片可见时上移 bottom:96px，展开时隐藏 -->
    <transition name="fab-fade">
      <router-link
        v-if="route.path !== '/'"
        to="/"
        class="fab-home"
        title="返回主页 · 智能对话"
      >
        <el-icon :size="20"><Back /></el-icon>
        <span>主页</span>
      </router-link>
    </transition>

    <!-- v1.5.0 T04：单页 tour（driver.js）— 监听路由 ?tour=xxx；零 DOM 输出 -->
    <OnboardingTour v-if="!isPureOnboarding" />

    <!-- ═══ header-redesign T03 全局挂载 ═══ -->
    <!-- 右侧菜单抽屉（T01：视图/主题/系统/调试 分组收纳原 Header 入口） -->
    <MenuDrawer v-model="menuOpen" />
    <!-- 右下角浮动系统状态卡片（T02：折叠一行 CPU/内存/AIT/CLK，展开详情/趋势） -->
    <StatusFloatingCard :connected="connected" />

    <!-- ═══ v1.6.0 P1 全局挂载（保留） ═══ -->
    <!-- P1-1：⌘K 命令面板 -->
    <CommandPalette v-model:open="paletteOpen" scope="global" />
    <!-- P1-2：? 快捷键速查浮层（自管理 open 状态） -->
    <ShortcutsOverlay />
    <!-- P1-3：Session 详情抽屉（sessionStats store 控制 open） -->
    <SessionDetailDrawer />
  </el-container>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Back,
  QuestionFilled,
  Menu as MenuIcon,
  MoreFilled,
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useChatStore } from './stores/chatStore'
import { useAuditStore } from './stores/audit'
import { healthCheck } from './api/chat'
import { useViewport } from './composables/useViewport'
import { useStatusCard } from './composables/useStatusCard'
// M-5 T05：主导航数据源 + 角色解析 + 用户徽标
import { visibleNavItems as filterNavItems } from './data/navItems'
import { getJwtRole } from './composables/useJwtAuth'
import { useAuthStore } from './stores/auth'
import UserBadge from './components/controls/UserBadge.vue'
// v1.5.1 T05 · F4 HITL 弹窗已从 App.vue 移至 ChatView.vue（架构 §3.4 + §5 T05）
import LogoHorizontal from './components/brand/LogoHorizontal.vue'
import CommandPalette from './components/controls/CommandPalette.vue'
import ShortcutsOverlay from './components/controls/ShortcutsOverlay.vue'
import SessionDetailDrawer from './components/controls/SessionDetailDrawer.vue'
import NavDrawer from './components/controls/NavDrawer.vue'
import MenuDrawer from './components/controls/MenuDrawer.vue'
import StatusFloatingCard from './components/StatusFloatingCard.vue'
import OnboardingTour from './components/onboarding/OnboardingTour.vue'
import { useThemeStore } from './stores/theme'
import { useDisplayStore } from './stores/display'

const store = useChatStore()
const themeStore = useThemeStore()
const auditStore = useAuditStore()
const displayStore = useDisplayStore()
const authStore = useAuthStore()
const route = useRoute()
const router = useRouter()

// V1.8.0 认证（T05）：生产会话失效安全网——status 变 anonymous（登出 /
// refresh 失败 / hydrate 失败）且当前不在登录页 → 跳 /login?redirect=。
// 仅 import.meta.env.PROD 生效（dev 不拦截，AC10-1）；登录页由路由守卫兜底。
watch(
  () => authStore.status,
  (status) => {
    const meta = (import.meta as { env?: Record<string, boolean | undefined> }).env
    if (meta?.PROD !== true) return
    if (status === 'anonymous' && route.path !== '/login') {
      void router.push({ path: '/login', query: { redirect: route.fullPath } })
    }
  },
)

// V1.7.0 F-1：大屏模式扩展点——仅暴露计算属性（isBigScreen），
// 本批不接入任何大屏布局逻辑；后续大屏 UI 在此读取。
const isBigScreen = computed(() => displayStore.isBigScreen)

// M-5 T05 · AC3-2：当前角色（base64url 解析 JWT role，缺省 dispatcher）
const currentRole = computed(() => getJwtRole())
/** 按角色过滤后的可见导航项（Header + NavDrawer 共享规则） */
const visibleNavItems = computed(() => filterNavItems(currentRole.value))

// v1.6.0 P1-5：三档断点（large ≥1920 / standard 1280-1920 / compact ≤1279.98）
// header-redesign T04：isMobile <768px（「更多」折叠点 fallback）
const { isLarge, isCompact, isMobile } = useViewport()

// header-redesign T02：状态卡片单例（显隐/折叠/数据/采样）
const {
  visible: statusCardVisible,
  collapsed: statusCardCollapsed,
  setServiceConnected: statusCardSetServiceConnected,
  start: statusCardStart,
  stop: statusCardStop,
} = useStatusCard()

// v1.6.0 P1-5：compact 断点强制背景降级为"标准模式"强度（不修改用户持久化偏好）
watch(
  isCompact,
  (compact) => {
    displayStore.setBgOverride(compact ? 'off' : null)
  },
  { immediate: true },
)

// P1-1：命令面板 open（由 CommandPalette 内 ⌘K 热键 emit 更新）
const paletteOpen = ref(false)
// P1-5：紧凑模式汉堡导航 open
const navOpen = ref(false)
// header-redesign T01：右侧菜单抽屉 open
const menuOpen = ref(false)

/** v1.5.0 T04：tour 在 wizard 视图页面跳过（避免无效的 popover 锚点） */
const isPureOnboarding = computed(() => route.path === '/onboarding')

const connected = ref(false)
let healthTimer: ReturnType<typeof setInterval> | null = null

// ── 健康检查（CPU/MEM/AGT/CLK 模拟已迁入 useStatusCard，App.vue 不再持有）──
async function checkHealth() {
  try {
    const resp = await healthCheck()
    connected.value = resp?.status === 'running'
  } catch {
    connected.value = false
  }
  // 服务连接状态同步进状态卡片单例（StatusCardData.serviceConnected）
  statusCardSetServiceConnected(connected.value)
}

// ── P1-2 帮助中心入口 ──
function goHelp() {
  void router.push('/help')
}

// ── V1.7 KB Upload：知识库管理快捷入口（帮助中心 ?tab=knowledge 直达）──
function goKnowledge() {
  void router.push({ path: '/help', query: { tab: 'knowledge' } })
}

// ── header-redesign T04：「更多」折叠点命令（移动端 fallback）──
function onMoreCommand(command: string | number | object): void {
  if (command === 'new-chat') {
    store.resetChat()
    ElMessage.success('已新建对话')
  } else if (command === 'knowledge') {
    goKnowledge()
  }
}

// ── HITL 三按钮 handler 已移至 ChatView.vue（架构 §3.4 + §5 T05）──

onMounted(() => {
  // 主题初始化（读取 localStorage 或跟随系统）
  themeStore.init()

  // v1.5.1 T04 · F3：audit store 首屏水合（启动 5s 轮询 + 拉取首次 pendingHitlCount）
  auditStore.hydrate()

  // 健康检查（15s 轮询；结果同步状态卡片）
  void checkHealth()
  healthTimer = setInterval(() => {
    void checkHealth()
  }, 15000)

  // header-redesign T02：启动状态卡片模拟（时钟 1s + 指标 5s + 采样；幂等）
  statusCardStart()
})

onUnmounted(() => {
  if (healthTimer) clearInterval(healthTimer)
  // header-redesign T02：停止状态卡片模拟（防泄漏）
  statusCardStop()
})
</script>

<style scoped>
.app-container {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--bg-base);
  transition: var(--theme-transition);
}

/* ── 顶栏 ─────────────────────────────────── */
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  background: var(--bg-elevated);
  border-bottom: 1px solid var(--border-default);
  padding: 0 var(--space-6);
  height: 60px;
  position: relative;
  z-index: var(--z-header);
  flex-shrink: 0;
  transition: var(--theme-transition);
}

.app-header::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--brand-primary), transparent);
  opacity: 0.5;
}

/* ── 左：可点击品牌 ────────────────────── */
.header-brand {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  min-width: 200px;
  text-decoration: none;
  color: inherit;
  padding: 4px var(--space-2);
  margin-left: calc(-1 * var(--space-2));
  border-radius: var(--radius-md);
  transition: background var(--dur-fast) var(--ease-out-quint);
  cursor: pointer;
}

.header-brand:hover {
  background: var(--brand-primary-soft, rgba(97, 92, 237, 0.08));
}

.header-brand:active {
  background: var(--brand-primary-soft, rgba(97, 92, 237, 0.16));
}

/* ── 水平导航（header-redesign：包一层 wrap，compact 时整组隐藏）── */
.app-nav-wrap {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
}

.app-nav {
  background: transparent;
  border-bottom: none;
  flex: 1;
  min-width: 0;
  /* Bug fix：与右侧操作区保持最小安全间距 */
  margin-right: var(--space-4);
}

.app-nav :deep(.el-menu-item) {
  height: 60px;
  line-height: 60px;
  font-size: var(--fs-md);
  padding: 0 var(--space-4);
}

.app-nav :deep(.el-menu-item.is-active) {
  font-weight: var(--fw-semibold);
}

/* ── 右侧：帮助图标 + 菜单按钮 + 更多折叠点 ── */
.header-right {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-shrink: 0;
}

/* v1.6.0 P1-2：帮助中心入口按钮（沿用） */
.help-entry {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all var(--dur-fast) var(--ease-out-quint);
}

.help-entry:hover {
  color: var(--brand-primary);
  border-color: var(--brand-primary);
  box-shadow: var(--glow-primary-soft);
}

/* header-redesign T03：「菜单」主按钮 */
.menu-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  height: 32px;
  padding: 0 var(--space-4);
  border: 1px solid var(--brand-primary);
  border-radius: var(--radius-md);
  background: var(--brand-primary);
  color: #fff;
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  letter-spacing: 0.08em;
  cursor: pointer;
  user-select: none;
  clip-path: var(--clip-corner-sm);
  transition: all var(--dur-fast) var(--ease-out-quint);
}

.menu-trigger:hover {
  background: var(--brand-primary-soft);
  color: var(--brand-primary);
  box-shadow: var(--glow-primary-soft);
}

.menu-trigger:active {
  transform: scale(0.97);
}

.menu-trigger:focus-visible {
  outline: 2px solid var(--brand-primary);
  outline-offset: 2px;
}

/* header-redesign T04：「更多」折叠点（<768px fallback） */
.more-entry {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all var(--dur-fast) var(--ease-out-quint);
}

.more-entry:hover {
  color: var(--brand-primary);
  border-color: var(--brand-primary);
  box-shadow: var(--glow-primary-soft);
}

/* ── 主体 ─────────────────────────────────── */
.app-main {
  flex: 1;
  padding: 0;
  /* 全局主区启用滚动：内容短时不显示滚动条，内容溢出时可滚动（修复 /grayscale 被裁切且无法滚轮滚动） */
  overflow: auto;
  max-width: 1280px;
  width: 100%;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  background: var(--bg-base);
  transition: var(--theme-transition);
}

/* v1.6.0 P1-5：large（≥1920）内容区放宽至 1600px */
.app-container--large .app-main {
  max-width: 1600px;
}

/* ── 全局返回主页 FAB（右下角悬浮；header-redesign §7.2 与状态卡片协调） ── */
.fab-home {
  position: fixed;
  right: var(--space-6);
  bottom: var(--space-6);
  /* header-redesign：与状态卡片同层 z-sticky（原 z-header） */
  z-index: var(--z-sticky);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 10px 16px;
  background: var(--bg-elevated);
  border: 1px solid var(--border-default);
  border-radius: 999px;
  color: var(--text-primary);
  text-decoration: none;
  font-size: var(--fs-sm);
  font-family: var(--font-cn);
  letter-spacing: 0.05em;
  box-shadow: var(--shadow-md, 0 4px 12px rgba(0, 0, 0, 0.15));
  backdrop-filter: blur(10px);
  transition: all var(--dur-fast) var(--ease-out-quint);
}

.fab-home:hover {
  background: var(--brand-primary-soft);
  border-color: var(--brand-primary);
  color: var(--brand-primary);
  box-shadow: var(--glow-primary-soft);
  transform: translateY(-2px);
}

.fab-home:active {
  transform: translateY(0);
}

/* header-redesign §7.2：卡片可见 → FAB 上移（折叠卡片高约 44px + 间隙）；展开 → 隐藏 */
.app-container--card-visible .fab-home {
  right: 16px;
  bottom: 96px;
}

.app-container--card-expanded .fab-home {
  display: none;
}

/* FAB 进场动画 */
.fab-fade-enter-active,
.fab-fade-leave-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}
.fab-fade-enter-from,
.fab-fade-leave-to {
  opacity: 0;
  transform: translateY(12px) scale(0.92);
}

/* 路由切换淡入 */
.fade-page-enter-active,
.fade-page-leave-active {
  transition: opacity 0.18s var(--ease-out-quint, ease);
}
.fade-page-enter-from,
.fade-page-leave-to {
  opacity: 0;
}

/* ── header-redesign T04：移动端响应式 ── */

/* compact：Header 品牌宽度收窄，让位右侧操作区 */
.app-container--compact .header-brand {
  min-width: auto;
}

/* ≤1440px：导航项 padding 压紧，避免顶栏拥挤 */
@media (max-width: 1440px) {
  .app-nav :deep(.el-menu-item) {
    padding: 0 var(--space-3);
  }
}

/* <768px：品牌收窄、Header padding 压缩、菜单按钮文字隐藏仅图标、FAB 图标化 */
@media (max-width: 768px) {
  .header-brand {
    min-width: auto;
  }
  .app-header {
    padding: 0 var(--space-3);
  }
  .menu-trigger span {
    display: none;   /* 小屏只保留「菜单」图标 */
  }
  .menu-trigger {
    padding: 0 var(--space-2);
  }
  .fab-home span {
    display: none;   /* 小屏只保留图标 */
  }
  .fab-home {
    padding: 12px;
  }
}
</style>
