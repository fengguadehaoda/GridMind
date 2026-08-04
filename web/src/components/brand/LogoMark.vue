<script setup lang="ts">
/**
 * 简版图形：六边形+电枢指针+4 节点
 * 不用图片，纯 SVG 内联，可独立配色
 */
import { computed } from 'vue'
import { useTheme } from '@/composables/useTheme'

const props = withDefaults(defineProps<{
  size?: number
}>(), {
  size: 32,
})

const { isDark } = useTheme()

// 直接绑定 CSS 变量（双主题自动响应）：用 fill/stroke="currentColor" + CSS color 桥接
const cssColor = computed(() => (isDark.value ? 'var(--brand-primary)' : 'var(--brand-primary)'))
const cssAccent = computed(() => (isDark.value ? 'var(--brand-accent)' : 'var(--brand-accent)'))
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 40 40"
    fill="none"
    class="gm-logo-mark"
    :style="{ color: cssColor }"
    role="img"
    aria-label="GridMind"
  >
    <polygon
      points="20,2 36,11 36,29 20,38 4,29 4,11"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linejoin="round"
    />
    <polygon
      points="20,8 31,14 31,26 20,32 9,26 9,14"
      fill="currentColor"
      fill-opacity="0.08"
      stroke="currentColor"
      stroke-width="1"
      stroke-opacity="0.4"
      stroke-linejoin="round"
    />
    <path
      d="M20 12 L26 24 L20 28 L14 24 Z"
      fill="currentColor"
    />
    <circle cx="20" cy="20" r="2" fill="var(--brand-accent)" />
    <circle cx="20" cy="2" r="1.5" fill="currentColor" />
    <circle cx="36" cy="11" r="1.5" fill="currentColor" fill-opacity="0.6" />
    <circle cx="36" cy="29" r="1.5" fill="var(--brand-accent)" />
    <circle cx="20" cy="38" r="1.5" fill="currentColor" fill-opacity="0.6" />
    <circle cx="4" cy="29" r="1.5" fill="currentColor" fill-opacity="0.6" />
    <circle cx="4" cy="11" r="1.5" fill="currentColor" />
  </svg>
</template>
