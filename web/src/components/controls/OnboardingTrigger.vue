<template>
  <button
    type="button"
    class="gm-onboarding-trigger"
    :class="{ 'gm-onboarding-trigger--active': isOnboardingRoute }"
    :aria-label="ariaLabel"
    :title="titleText"
    data-tour="onboarding-trigger"
    @click="onClick"
  >
    <el-icon class="gm-onboarding-trigger__icon" :size="14">
      <Compass />
    </el-icon>
    <span class="gm-onboarding-trigger__label">{{ labelText }}</span>
  </button>
</template>

<script setup lang="ts">
/**
 * OnboardingTrigger · Header "新手引导" 入口（v1.5.0 P0-4）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 设计（架构 §1.3 + §10 主理人决策 #3）：
 *   - 位置：Header 右侧、ThemeToggle 左侧（视觉上形成"显示设置"组）
 *   - 风格：iOS 风兄弟组件（与 BackgroundModeToggle / ColorBlindModeToggle 一致）
 *   - 行为：点击跳转 /onboarding?force=1（force=1 让守卫跳过 hasOnboarded 拦截）
 *   - 文案：动态 —— 未完成显示"开始引导"，已完成显示"重看引导"
 *
 * a11y：
 *   - aria-label 动态生成，已完成态补"已"字
 *   - :active 路由（如 /onboarding）按钮高亮（active class）
 */
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Compass } from '@element-plus/icons-vue'
import { useOnboarding } from '@/composables/useOnboarding'

const route = useRoute()
const router = useRouter()
const { hasOnboarded } = useOnboarding()

const isOnboardingRoute = computed(() => route.path === '/onboarding')

const labelText = computed(() => (hasOnboarded.value ? '重看引导' : '新手引导'))
const titleText = computed(() =>
  hasOnboarded.value
    ? '重新查看新手引导（已完成）'
    : '开始新手引导（3 步 5 分钟上手）',
)
const ariaLabel = computed(() =>
  hasOnboarded.value
    ? '重看新手引导（已完成的调度员可回顾）'
    : '新手引导（新调度员 3 步 5 分钟上手）',
)

function onClick(): void {
  router.push({ path: '/onboarding', query: { force: '1' } })
}
</script>

<style scoped>
/* ── 与 BackgroundModeToggle / ColorBlindModeToggle 视觉一致 ── */
.gm-onboarding-trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 var(--space-3, 12px);
  background: var(--bg-card);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm, 4px);
  color: var(--text-secondary);
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: var(--fs-xs, 11px);
  font-weight: var(--fw-semibold, 600);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  cursor: pointer;
  user-select: none;
  clip-path: var(--clip-corner-sm, none);
  transition: all var(--dur-fast) var(--ease-out-quint);
  flex-shrink: 0;
}

.gm-onboarding-trigger:hover {
  background: var(--brand-primary-soft, rgba(0, 229, 255, 0.15));
  border-color: var(--brand-primary, #00e5ff);
  color: var(--brand-primary, #00e5ff);
  box-shadow: var(--glow-primary-soft, 0 0 8px rgba(0, 229, 255, 0.3));
}

.gm-onboarding-trigger:active {
  transform: scale(0.97);
}

.gm-onboarding-trigger:focus-visible {
  outline: 2px solid var(--brand-primary, #00e5ff);
  outline-offset: 1px;
}

.gm-onboarding-trigger--active {
  background: var(--brand-primary-soft, rgba(0, 229, 255, 0.15));
  border-color: var(--brand-primary, #00e5ff);
  color: var(--brand-primary, #00e5ff);
}

.gm-onboarding-trigger__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  flex-shrink: 0;
}

.gm-onboarding-trigger__label {
  font-weight: var(--fw-semibold, 600);
}

/* ── 响应式：1024px 以下压缩 padding（与 toggle 兄弟组件同步）── */
@media (max-width: 1024px) {
  .gm-onboarding-trigger {
    height: 28px;
    padding: 0 var(--space-2, 8px);
    font-size: 10px;
  }
}

@media (max-width: 768px) {
  .gm-onboarding-trigger__label {
    display: none;
  }
  .gm-onboarding-trigger {
    padding: 0 8px;
    width: 32px;
    justify-content: center;
  }
}
</style>
