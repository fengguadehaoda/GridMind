<script setup lang="ts">
/**
 * 横版 Logo：图形 + 中文「灵枢电网」+ 英文「GridMind」+ 副标
 * 双主题：颜色通过 CSS 变量响应
 */
import { useTheme } from '@/composables/useTheme'
import LogoMark from './LogoMark.vue'

withDefaults(defineProps<{
  size?: number
  showSubtitle?: boolean
}>(), {
  size: 32,
  showSubtitle: true,
})

const { isDark } = useTheme()
</script>

<template>
  <div class="gm-logo-h" :class="{ 'gm-logo-h--dark': isDark }">
    <LogoMark :size="size" />
    <div class="gm-logo-h__text">
      <div class="gm-logo-h__cn">灵枢电网</div>
      <div v-if="showSubtitle" class="gm-logo-h__en">GridMind · 电力 AI 调度中枢</div>
    </div>
  </div>
</template>

<style scoped>
.gm-logo-h {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
  user-select: none;
}

.gm-logo-h__text {
  display: flex;
  flex-direction: column;
  line-height: 1.1;
}

.gm-logo-h__cn {
  font-family: var(--font-cn);
  font-size: var(--fs-xl);
  font-weight: var(--fw-bold);
  color: var(--text-primary);
  letter-spacing: 0.1em;
  transition: var(--theme-transition);
}

.gm-logo-h__en {
  font-family: var(--font-display);
  font-size: var(--fs-xs);
  font-weight: var(--fw-medium);
  color: var(--brand-primary);
  letter-spacing: 0.15em;
  margin-top: 2px;
  transition: var(--theme-transition);
}
</style>
