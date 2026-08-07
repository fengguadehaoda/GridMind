<script setup lang="ts">
/**
 * BackgroundModeToggle · 背景演示/标准模式切换
 *
 * 设计要点（v1.5.0 P0-1 · 架构 §5 T02）：
 * 1. iOS SegmentedControl 风格 2 档（标准 / 演示），200ms 切换动画
 * 2. 标准模式：完全关闭 background 系列（保留 PulseDot / DataStreamBadge 业务微动效）
 * 3. 演示模式：background 全开（汇报/录屏场景）
 * 4. 接 display store；持久化由 store.hydrate() 自动处理
 * 5. a11y：role="radiogroup" + role="radio" + aria-checked；键盘可达（Enter/Space 切换）
 * 6. 响应式：1024px 以下字号缩为紧凑模式（保留双段文字），与 FAB 不冲突
 */
import { computed } from 'vue'
import { useDisplay } from '@/composables/useDisplay'
import type { DisplayMode } from '@/types/theme'

/** 2 档配置（顺序即 visual order，不可乱） */
const SEGMENTS: ReadonlyArray<{ value: DisplayMode; label: string; aria: string }> = [
  { value: 'standard', label: '标准', aria: '标准模式（背景降噪，适合长时间盯盘）' },
  { value: 'presentation', label: '演示', aria: '演示模式（背景动效全开，适合汇报录屏）' },
] as const

const { displayMode, setDisplayMode } = useDisplay()

/** 当前激活 segment 索引（0/1）—— 驱动滑块 transform */
const activeIndex = computed(() =>
  displayMode.value === 'presentation' ? 1 : 0,
)

/** radiogroup 整体 aria-label（中文） */
const groupLabel = computed(() => '背景模式切换')

/** radio 选中态 aria-label（带当前态语义） */
const radioLabel = (seg: { value: DisplayMode; label: string; aria: string }) => {
  return seg.aria
}

function selectMode(mode: DisplayMode): void {
  if (displayMode.value === mode) return
  setDisplayMode(mode)
}
</script>

<template>
  <div
    class="gm-bg-mode-toggle"
    role="radiogroup"
    :aria-label="groupLabel"
  >
    <!-- 滑动指示器（绝对定位，根据 activeIndex 平移） -->
    <div
      class="gm-bg-mode-toggle__indicator"
      :class="{ 'gm-bg-mode-toggle__indicator--right': activeIndex === 1 }"
      aria-hidden="true"
    />

    <button
      v-for="seg in SEGMENTS"
      :key="seg.value"
      type="button"
      class="gm-bg-mode-toggle__segment"
      :class="{ 'gm-bg-mode-toggle__segment--active': displayMode === seg.value }"
      role="radio"
      :aria-checked="displayMode === seg.value"
      :aria-label="radioLabel(seg)"
      :title="seg.aria"
      :tabindex="displayMode === seg.value ? 0 : -1"
      @click="selectMode(seg.value)"
      @keydown.enter.prevent="selectMode(seg.value)"
      @keydown.space.prevent="selectMode(seg.value)"
    >
      {{ seg.label }}
    </button>
  </div>
</template>

<style scoped>
/* ── 容器（iOS SegmentedControl 外壳） ─────────── */
.gm-bg-mode-toggle {
  position: relative;
  display: inline-flex;
  align-items: stretch;
  height: 32px;
  padding: 2px;
  background: var(--bg-card);
  border: 1px solid var(--border-default);
  border-radius: 999px;
  /* 与 ThemeToggle 一致的 HUD 切角 */
  clip-path: var(--clip-corner-sm, none);
  transition: background var(--dur-fast) var(--ease-out-quint);
  flex-shrink: 0;
}

/* ── 滑块（活动指示器，200ms 滑动动画） ─────── */
.gm-bg-mode-toggle__indicator {
  position: absolute;
  top: 2px;
  left: 2px;
  bottom: 2px;
  width: calc(50% - 2px);
  background: var(--bg-elevated);
  border: 1px solid var(--border-default);
  border-radius: 999px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.12), 0 0 8px var(--brand-primary-soft, rgba(97, 92, 237, 0.08));
  transition: transform 200ms var(--ease-out-quint, cubic-bezier(0.22, 1, 0.36, 1));
  z-index: 0;
  pointer-events: none;
}

.gm-bg-mode-toggle__indicator--right {
  transform: translateX(100%);
}

/* ── 段（radio 按钮） ─────────────────────── */
.gm-bg-mode-toggle__segment {
  position: relative;
  z-index: 1;
  flex: 1 1 0;
  min-width: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 var(--space-3, 12px);
  background: transparent;
  border: none;
  border-radius: 999px;
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: var(--fs-xs, 11px);
  font-weight: var(--fw-semibold, 600);
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  cursor: pointer;
  user-select: none;
  white-space: nowrap;
  transition: color 200ms var(--ease-out-quint, ease);
  outline: none;
}

.gm-bg-mode-toggle__segment--active {
  color: var(--brand-primary);
}

.gm-bg-mode-toggle__segment:hover:not(.gm-bg-mode-toggle__segment--active) {
  color: var(--text-primary);
}

.gm-bg-mode-toggle__segment:focus-visible {
  box-shadow: 0 0 0 2px var(--brand-primary, #5b6cff);
  outline: 2px solid var(--brand-primary, #5b6cff);
  outline-offset: 1px;
}

.gm-bg-mode-toggle__segment:active {
  transform: scale(0.97);
}

/* ── 响应式：1024px 以下缩为紧凑布局（保留双段文字） ── */
@media (max-width: 1024px) {
  .gm-bg-mode-toggle {
    height: 28px;
    padding: 2px;
  }
  .gm-bg-mode-toggle__segment {
    padding: 0 var(--space-2, 8px);
    font-size: 10px;
    letter-spacing: 0.05em;
  }
}

@media (max-width: 640px) {
  /* 极窄屏：保留控件（2 档是核心功能），padding 进一步压缩 */
  .gm-bg-mode-toggle__segment {
    padding: 0 6px;
  }
}

/* ── 暗主题下，滑块边框略强化 ─────────────── */
:global([data-theme="dark"]) .gm-bg-mode-toggle__indicator {
  background: var(--bg-base);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.4), 0 0 10px var(--brand-primary-soft, rgba(97, 92, 237, 0.18));
}
</style>
