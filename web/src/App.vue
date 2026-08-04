<template>
  <el-container class="app-container">
    <!-- 顶栏 — 赛博控制中心 HUD -->
    <el-header class="app-header">
      <!-- 左：Logo + 品牌名 -->
      <div class="header-left">
        <LogoHorizontal :size="36" />
      </div>

      <!-- 中：水平导航 -->
      <el-menu
        class="app-nav"
        mode="horizontal"
        router
        :default-active="route.path"
        :ellipsis="false"
      >
        <el-menu-item index="/">
          <el-icon><ChatDotRound /></el-icon>
          智能对话
        </el-menu-item>
        <el-menu-item index="/monitor">
          <el-icon><Monitor /></el-icon>
          实时监控
        </el-menu-item>
      </el-menu>

      <!-- 右侧：服务状态 + 状态条 + 主题切换 + 操作 -->
      <div class="header-right">
        <!-- 服务连接状态 -->
        <div class="status-badge" :class="{ connected }">
          <PulseDot :tone="connected ? 'success' : 'danger'" :size="8" :speed="1.8" />
          <span class="status-text">{{ connected ? '服务已连接' : '服务未连接' }}</span>
        </div>

        <!-- 状态条：CPU / MEM / 时钟 -->
        <div class="status-strip">
          <DataStreamBadge
            label="CPU"
            :value="cpuLoad.toFixed(0)"
            unit="%"
            :tone="cpuTone"
            :pulse="true"
          />
          <DataStreamBadge
            label="MEM"
            :value="memLoad.toFixed(0)"
            unit="%"
            :tone="memTone"
          />
          <DataStreamBadge
            label="AGT"
            :value="agentCount"
            :tone="agentCount > 0 ? 'success' : 'info'"
          />
          <DataStreamBadge
            label="CLK"
            :value="currentTime"
            tone="accent"
          />
        </div>

        <!-- 主题切换 -->
        <ThemeToggle size="md" />

        <!-- 新对话 -->
        <el-button size="small" class="ghost-btn" @click="store.resetChat()">
          <el-icon><Refresh /></el-icon>
          <span>新对话</span>
        </el-button>
      </div>
    </el-header>

    <!-- 主体 -->
    <el-main class="app-main">
      <router-view />
    </el-main>

    <!-- HITL 对话框（Edit & Continue 模式，三按钮：拒绝 / 仅批准 / 修改后批准） -->
    <HitlEditDialog
      v-model="showHitl"
      :interrupt-node="store.interruptNode"
      :interrupt-msg="store.interruptMsg"
      :thread-id="store.pendingThreadId"
      :interrupt-args="store.interruptArgs"
      :busy="store.hitlBusy"
      :safety-reject="store.hitlSafetyReject"
      @approve="onApprove"
      @reject="onReject"
      @edit-approve="onEditApprove"
    />
  </el-container>
</template>

<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted, computed } from 'vue'
import { useRoute } from 'vue-router'
import { Refresh, ChatDotRound, Monitor } from '@element-plus/icons-vue'
import { useChatStore } from './stores/chatStore'
import { healthCheck } from './api/chat'
import HitlEditDialog from './components/HitlEditDialog.vue'
import LogoHorizontal from './components/brand/LogoHorizontal.vue'
import ThemeToggle from './components/controls/ThemeToggle.vue'
import PulseDot from './components/background/PulseDot.vue'
import DataStreamBadge from './components/background/DataStreamBadge.vue'
import { useThemeStore } from './stores/theme'

const store = useChatStore()
const themeStore = useThemeStore()
const route = useRoute()

const showHitl = ref(false)
const connected = ref(false)
let healthTimer: ReturnType<typeof setInterval> | null = null

// ── 顶栏状态条模拟数据（M1 阶段用模拟值，后续可接入后端 metrics）──
const cpuLoad = ref(23)
const memLoad = ref(41)
const agentCount = ref(4)
const currentTime = ref(formatTime(new Date()))

let clockTimer: ReturnType<typeof setInterval> | null = null
let metricsTimer: ReturnType<typeof setInterval> | null = null

function formatTime(d: Date): string {
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
}

function updateMetrics() {
  // 模拟真实波动：CPU 18-40, MEM 35-55
  cpuLoad.value = 18 + Math.random() * 22
  memLoad.value = 35 + Math.random() * 20
}

const cpuTone = computed<'info' | 'warning' | 'danger'>(() => {
  if (cpuLoad.value >= 85) return 'danger'
  if (cpuLoad.value >= 60) return 'warning'
  return 'info'
})

const memTone = computed<'info' | 'warning' | 'danger'>(() => {
  if (memLoad.value >= 85) return 'danger'
  if (memLoad.value >= 60) return 'warning'
  return 'info'
})

// ── 健康检查 ──
async function checkHealth() {
  try {
    const resp = await healthCheck()
    connected.value = resp?.status === 'running'
  } catch {
    connected.value = false
  }
}

// ── HITL ──
async function onApprove(reason: string) {
  await store.decideHitl('approve', { rejectReason: reason })
}
async function onReject(reason: string) {
  await store.decideHitl('reject', { rejectReason: reason })
}
async function onEditApprove(payload: {
  editedArgs: Record<string, unknown>
  editReason: string
}) {
  await store.approveWithEdit(payload.editedArgs, payload.editReason)
}

watch(() => store.interruptRequired, (v) => {
  showHitl.value = v
})

onMounted(() => {
  // 主题初始化（读取 localStorage 或跟随系统）
  themeStore.init()

  // 健康检查
  checkHealth()
  healthTimer = setInterval(checkHealth, 15000)

  // 时钟
  clockTimer = setInterval(() => {
    currentTime.value = formatTime(new Date())
  }, 1000)

  // 模拟指标
  metricsTimer = setInterval(updateMetrics, 5000)
})

onUnmounted(() => {
  if (healthTimer) clearInterval(healthTimer)
  if (clockTimer) clearInterval(clockTimer)
  if (metricsTimer) clearInterval(metricsTimer)
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

.header-left {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  min-width: 200px;
}

/* ── 水平导航 ─────────────────────────── */
.app-nav {
  background: transparent;
  border-bottom: none;
  flex: 1;
  min-width: 0;
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

/* ── 右侧 ─────────────────────────────────── */
.header-right {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-shrink: 0;
}

.status-badge {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 4px var(--space-3);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-pill);
  background: var(--bg-card);
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  letter-spacing: 0.05em;
  transition: var(--theme-transition);
}

.status-badge .status-text {
  color: var(--status-success);
  font-weight: var(--fw-medium);
}

.status-badge:not(.connected) .status-text {
  color: var(--status-danger);
}

.status-strip {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.ghost-btn {
  background: var(--brand-primary-soft) !important;
  border: 1px solid var(--border-default) !important;
  color: var(--text-primary) !important;
  transition: all var(--dur-fast) var(--ease-out-quint);
}

.ghost-btn:hover {
  background: var(--brand-primary-soft) !important;
  border-color: var(--brand-primary) !important;
  color: var(--brand-primary) !important;
  box-shadow: var(--glow-primary-soft);
}

/* ── 主体 ─────────────────────────────────── */
.app-main {
  flex: 1;
  padding: 0;
  overflow: hidden;
  max-width: 1280px;
  width: 100%;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  background: var(--bg-base);
  transition: var(--theme-transition);
}

/* ── 响应式 ───────────────────────────── */
@media (max-width: 1024px) {
  .status-strip {
    display: none;
  }
  .app-nav {
    display: none;
  }
}

@media (max-width: 768px) {
  .header-left {
    min-width: auto;
  }
  .app-header {
    padding: 0 var(--space-3);
  }
}
</style>
