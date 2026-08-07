<script setup lang="ts">
/**
 * NavDrawer.vue · 紧凑模式汉堡导航（v1.6.0 P1-5）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构决策（p1-iteration-architecture §1 P1-5）：
 *   - compact 断点（1024-1280）下左侧导航折叠为汉堡按钮 + el-drawer（5 路由）
 *   - 复用 Element Plus drawer，无新依赖
 *   - 由 App.vue 在 isCompact 时挂载；路由激活态与顶部菜单一致
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  ChatDotRound,
  Monitor,
  Histogram,
  Document,
  DataBoard,
  Menu as MenuIcon,
} from '@element-plus/icons-vue'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ (e: 'update:modelValue', value: boolean): void }>()

const route = useRoute()
const router = useRouter()

const open = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

interface NavItem {
  path: string
  label: string
  icon: unknown
}

const NAV_ITEMS: NavItem[] = [
  { path: '/', label: '智能对话', icon: ChatDotRound },
  { path: '/monitor', label: '实时监控', icon: Monitor },
  { path: '/grayscale', label: '灰度面板', icon: Histogram },
  { path: '/audit', label: 'HITL 审计', icon: Document },
  { path: '/system', label: '系统总览', icon: DataBoard },
]

function navigate(path: string): void {
  open.value = false
  void router.push(path)
}
</script>

<template>
  <div class="gm-nav-drawer">
    <!-- 汉堡触发按钮（仅 compact 挂载时可见） -->
    <button
      type="button"
      class="gm-nav-drawer__trigger"
      data-test="nav-drawer-trigger"
      :aria-label="open ? '关闭导航' : '打开导航'"
      :aria-expanded="open"
      @click="open = !open"
    >
      <el-icon :size="20"><MenuIcon /></el-icon>
    </button>

    <el-drawer
      :model-value="open"
      direction="ltr"
      size="260px"
      :with-header="true"
      class="gm-nav-drawer__panel"
      @update:model-value="(v: boolean) => (open = v)"
    >
      <template #header>
        <span class="gm-nav-drawer__title">导航</span>
      </template>

      <nav class="gm-nav-drawer__nav">
        <button
          v-for="item in NAV_ITEMS"
          :key="item.path"
          type="button"
          class="gm-nav-drawer__item"
          :class="{ 'is-active': route.path === item.path }"
          :data-test="`nav-drawer-item-${item.path}`"
          @click="navigate(item.path)"
        >
          <el-icon :size="16"><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
        </button>
      </nav>

      <div class="gm-nav-drawer__foot">
        <span>GridMind · 灵枢电网</span>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped lang="scss">
.gm-nav-drawer {
  display: inline-flex;
  align-items: center;
}

.gm-nav-drawer__trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  color: var(--text-primary);
  cursor: pointer;
  transition: all var(--dur-fast) var(--ease-out-quint);
}

.gm-nav-drawer__trigger:hover {
  border-color: var(--brand-primary);
  color: var(--brand-primary);
  box-shadow: var(--glow-primary-soft);
}

.gm-nav-drawer__panel :deep(.el-drawer) {
  background: var(--bg-elevated);
  border-right: 1px solid var(--border-default);
  transition: var(--theme-transition);
}

.gm-nav-drawer__panel :deep(.el-drawer__header) {
  margin-bottom: 0;
  padding: var(--space-4);
  border-bottom: 1px solid var(--border-muted);
  color: var(--text-primary);
}

.gm-nav-drawer__title {
  font-family: var(--font-cn);
  font-size: var(--fs-md);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
  letter-spacing: 0.1em;
}

.gm-nav-drawer__nav {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4);
}

.gm-nav-drawer__item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--border-muted);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  color: var(--text-secondary);
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  cursor: pointer;
  transition: all var(--dur-fast) var(--ease-out-quint);
}

.gm-nav-drawer__item:hover {
  border-color: var(--brand-primary);
  color: var(--brand-primary);
  background: var(--brand-primary-soft);
}

.gm-nav-drawer__item.is-active {
  border-color: var(--brand-primary);
  color: var(--brand-primary);
  background: var(--brand-primary-soft);
  box-shadow: var(--glow-primary-soft);
  font-weight: var(--fw-semibold);
}

.gm-nav-drawer__foot {
  padding: var(--space-4);
  border-top: 1px solid var(--border-muted);
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}
</style>
