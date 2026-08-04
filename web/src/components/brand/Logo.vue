<script setup lang="ts">
/**
 * Logo 主组件
 * 根据 variant + theme 渲染对应 SVG
 * 暗/亮主题由 useThemeStore 自动驱动
 */
import { computed } from 'vue'
import { useTheme } from '@/composables/useTheme'
import type { LogoProps, LogoTheme } from '@/types/theme'

const props = withDefaults(defineProps<LogoProps>(), {
  variant: 'horizontal',
  theme: 'auto',
  size: 32,
  showWordmark: true,
  alt: '灵枢电网 · GridMind',
})

const { theme: currentTheme } = useTheme()

/** 解析 theme prop（auto → 当前主题） */
const resolvedTheme = computed<LogoTheme>(() => {
  if (props.theme !== 'auto') return props.theme
  return currentTheme.value
})

/** 映射到实际 SVG 路径 */
const logoSrc = computed(() => {
  const t = resolvedTheme.value
  switch (props.variant) {
    case 'horizontal':
      return t === 'light' ? '/logo/logo-primary-horizontal-light.svg' : '/logo/logo-primary-horizontal.svg'
    case 'vertical':
      return '/logo/logo-primary-vertical.svg'
    case 'mark':
      return t === 'light' ? '/logo/logo-mark-light.svg' : '/logo/logo-mark.svg'
    case 'mono':
      return t === 'light' ? '/logo/logo-mono-dark.svg' : '/logo/logo-mono-light.svg'
    default:
      return '/logo/logo-primary-horizontal.svg'
  }
})

/** 横版的实际尺寸（高 32, 宽按 SVG viewBox 240:56 比例计算） */
const displaySize = computed(() => {
  if (props.variant === 'vertical') {
    return { width: 80, height: 107 }
  }
  if (props.variant === 'mark' || props.variant === 'mono') {
    return { width: Number(props.size), height: Number(props.size) }
  }
  // horizontal
  const h = Number(props.size)
  return { width: Math.round(h * (240 / 56)), height: h }
})
</script>

<template>
  <img
    :src="logoSrc"
    :alt="alt"
    :width="displaySize.width"
    :height="displaySize.height"
    class="gm-logo"
    :class="`gm-logo--${variant}`"
    draggable="false"
  />
</template>

<style scoped>
.gm-logo {
  display: inline-block;
  vertical-align: middle;
  user-select: none;
  transition: opacity var(--dur-fast) var(--ease-out-quint);
}
.gm-logo--mark,
.gm-logo--mono {
  filter: drop-shadow(var(--glow-primary-soft));
}
</style>
