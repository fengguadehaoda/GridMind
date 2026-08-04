<script setup lang="ts">
/**
 * ThemeToggle · 主题切换按钮
 * 切角 + 太阳/月亮 SVG + 100ms 旋转
 * 键盘可访问：role="switch" + aria-checked + Enter/Space 切换
 */
import { computed } from 'vue'
import { useTheme } from '@/composables/useTheme'
import type { ThemeToggleProps } from '@/types/theme'

const props = withDefaults(defineProps<ThemeToggleProps>(), {
  size: 'md',
  showLabel: false,
  position: 'inline',
})

const { isDark, toggle, theme } = useTheme()

const sizeMap = { sm: 28, md: 32, lg: 40 } as const
const iconSize = computed(() => sizeMap[props.size] / 2)

const ariaLabel = computed(() => (isDark.value ? '切换到亮色主题' : '切换到暗色主题'))
</script>

<template>
  <button
    type="button"
    class="gm-theme-toggle"
    :class="[`gm-theme-toggle--${size}`, `gm-theme-toggle--${position}`]"
    role="switch"
    :aria-checked="isDark"
    :aria-label="ariaLabel"
    :title="ariaLabel"
    @click="toggle"
    @keydown.enter.prevent="toggle"
    @keydown.space.prevent="toggle"
  >
    <span class="gm-theme-toggle__icon-wrap" :key="theme">
      <!-- 太阳（亮主题下显示 → 点击切到暗） -->
      <svg
        v-if="!isDark"
        class="gm-theme-toggle__icon"
        :width="iconSize"
        :height="iconSize"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        <circle cx="12" cy="12" r="4" />
        <line x1="12" y1="2" x2="12" y2="5" />
        <line x1="12" y1="19" x2="12" y2="22" />
        <line x1="2" y1="12" x2="5" y2="12" />
        <line x1="19" y1="12" x2="22" y2="12" />
        <line x1="4.93" y1="4.93" x2="7.05" y2="7.05" />
        <line x1="16.95" y1="16.95" x2="19.07" y2="19.07" />
        <line x1="4.93" y1="19.07" x2="7.05" y2="16.95" />
        <line x1="16.95" y1="7.05" x2="19.07" y2="4.93" />
      </svg>
      <!-- 月亮（暗主题下显示 → 点击切到亮） -->
      <svg
        v-else
        class="gm-theme-toggle__icon"
        :width="iconSize"
        :height="iconSize"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        stroke-linecap="round"
        stroke-linejoin="round"
      >
        <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
      </svg>
    </span>
    <span v-if="showLabel" class="gm-theme-toggle__label">
      {{ isDark ? '暗' : '亮' }}
    </span>
  </button>
</template>

<style scoped>
.gm-theme-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  padding: 0;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  color: var(--text-primary);
  cursor: pointer;
  transition: all var(--dur-fast) var(--ease-out-quint);
  position: relative;
  clip-path: var(--clip-corner-sm);
}

.gm-theme-toggle--sm { width: 28px; height: 28px; }
.gm-theme-toggle--md { width: 32px; height: 32px; }
.gm-theme-toggle--lg {
  height: 40px;
  padding: 0 var(--space-3);
}

.gm-theme-toggle--fixed {
  position: fixed;
  bottom: var(--space-6);
  right: var(--space-6);
  z-index: var(--z-sticky);
  box-shadow: var(--shadow-card);
}

.gm-theme-toggle:hover {
  border-color: var(--brand-primary);
  background: var(--brand-primary-soft);
  color: var(--brand-primary);
  box-shadow: var(--glow-primary-soft);
}

.gm-theme-toggle:active {
  transform: scale(0.95);
}

.gm-theme-toggle__icon-wrap {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  animation: gm-theme-icon-in var(--dur-base) var(--ease-spring);
}

.gm-theme-toggle__label {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
  text-transform: uppercase;
  letter-spacing: 0.1em;
}

@keyframes gm-theme-icon-in {
  0% {
    opacity: 0;
    transform: rotate(-180deg) scale(0.5);
  }
  100% {
    opacity: 1;
    transform: rotate(0deg) scale(1);
  }
}
</style>
