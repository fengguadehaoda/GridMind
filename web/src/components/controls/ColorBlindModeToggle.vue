<script setup lang="ts">
/**
 * ColorBlindModeToggle · 色盲 palette 切换（v1.5.0 P0-2）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 设计（架构 §5 T03 实现要点 #4 + §7.3 a11y）：
 *   1. el-dropdown 4 选 1，每项含中文标签 + 5 色缩略条形预览
 *   2. 切换瞬时生效：触发 store.setColorBlindPalette() → :root[data-cb-palette] 切换
 *   3. 不破坏布局：dropdown 由 EP Teleport 渲染，组件本身不参与 v-if/v-show
 *   4. 持久化由 store 自动处理（localStorage 写入）
 *   5. a11y：aria-haspopup="menu" + 选中态 aria-checked + 键盘可达
 *   6. 200ms 切换动画（与 BackgroundModeToggle 一致的 iOS 风格）
 */
import { computed, ref } from 'vue'
import { ArrowDown } from '@element-plus/icons-vue'
import { useDisplay } from '@/composables/useDisplay'
import {
  type ColorBlindPalette,
  PALETTE_LABEL,
} from '@/types/theme'
import { useDisplayStore } from '@/stores/display'

/* ── 4 套 palette 元数据（顺序即 visual order）── */
interface PaletteEntry {
  value: ColorBlindPalette
  label: string
  shortLabel: string
  description: string
  /** 5 色预览（normal/warning/critical/info/accent 的 fg 值） */
  preview: readonly [string, string, string, string, string]
}

const PALETTES: ReadonlyArray<PaletteEntry> = [
  {
    value: 'default',
    label: PALETTE_LABEL['default'],
    shortLabel: '默认',
    description: '红绿黄蓝（GridMind 原生）',
    // 实际颜色由 :root 默认 status-* 提供（theme-aware）
    preview: [
      'var(--status-success)',
      'var(--status-warning)',
      'var(--status-danger)',
      'var(--status-info)',
      'var(--brand-accent)',
    ],
  },
  {
    value: 'ibm-cb-safe',
    label: PALETTE_LABEL['ibm-cb-safe'],
    shortLabel: 'IBM',
    description: 'IBM Carbon 色盲安全（蓝橙紫）',
    preview: ['#648FFF', '#FE6100', '#DC267F', '#785EF0', '#FFB000'],
  },
  {
    value: 'okabe-ito',
    label: PALETTE_LABEL['okabe-ito'],
    shortLabel: 'Okabe',
    description: 'Okabe-Ito 2007（去红绿色盲）',
    preview: ['#009E73', '#E69F00', '#D55E00', '#56B4E9', '#CC79A7'],
  },
  {
    value: 'colorbrewer-rdylbu',
    label: PALETTE_LABEL['colorbrewer-rdylbu'],
    shortLabel: 'RdYlBu',
    description: 'ColorBrewer RdYlBu（高对比）',
    preview: ['#1A9850', '#FDAE61', '#D73027', '#4575B4', '#FEE090'],
  },
] as const

/* ── Store 绑定（避免重复 storeToRefs） ─────── */
const { colorBlind } = useDisplay()
const displayStore = useDisplayStore()

/* ── 当前 palette 标签（用于触发按钮显示） ── */
const currentLabel = computed(
  () => PALETTES.find((p) => p.value === colorBlind.value)?.shortLabel ?? '默认',
)
const currentFullLabel = computed(
  () => PALETTES.find((p) => p.value === colorBlind.value)?.label ?? '默认',
)

/* ── Dropdown command handler（el-dropdown 内置） ── */
function onSelect(cmd: string | number | null): void {
  if (!cmd) return
  displayStore.setColorBlindPalette(cmd as ColorBlindPalette)
}

/* ── 触发按钮 ref（用于外部 focus 恢复，可选） ── */
const triggerRef = ref<HTMLElement | null>(null)

/* ── 校验：当前激活项 index（用于下拉项高亮） ── */
function isActive(p: PaletteEntry): boolean {
  return p.value === colorBlind.value
}
</script>

<template>
  <el-dropdown
    trigger="click"
    placement="bottom-end"
    :hide-on-click="true"
    @command="onSelect"
  >
    <button
      ref="triggerRef"
      type="button"
      class="gm-cb-mode-toggle"
      :class="{ 'gm-cb-mode-toggle--active': colorBlind !== 'default' }"
      :aria-label="`色盲 palette：当前 ${currentFullLabel}`"
      :aria-haspopup="'menu'"
      :title="`色盲 palette：${currentFullLabel}`"
    >
      <span class="gm-cb-mode-toggle__icon" aria-hidden="true">
        <!-- 调色板 SVG（Element Plus icon 已有 ColorPalette，但为简洁内嵌） -->
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <circle cx="13.5" cy="6.5" r="0.5" fill="currentColor" />
          <circle cx="17.5" cy="10.5" r="0.5" fill="currentColor" />
          <circle cx="8.5" cy="7.5" r="0.5" fill="currentColor" />
          <circle cx="6.5" cy="12.5" r="0.5" fill="currentColor" />
          <path d="M12 2a10 10 0 1 0 0 20 1.5 1.5 0 0 0 1.5-1.5v-1.5a1.5 1.5 0 0 1 1.5-1.5h2.5a4 4 0 0 0 4-4 8 8 0 0 0-9.5-7.5z" />
        </svg>
      </span>
      <span class="gm-cb-mode-toggle__label">{{ currentLabel }}</span>
      <el-icon class="gm-cb-mode-toggle__caret" :size="12">
        <ArrowDown />
      </el-icon>
    </button>

    <template #dropdown>
      <el-dropdown-menu class="gm-cb-mode-dropdown">
        <el-dropdown-item
          v-for="p in PALETTES"
          :key="p.value"
          :command="p.value"
          :disabled="isActive(p)"
          class="gm-cb-mode-dropdown__item"
        >
          <div class="gm-cb-mode-dropdown__row">
            <!-- 5 色缩略条形预览（normal/warning/critical/info/accent）-->
            <div class="gm-cb-mode-dropdown__preview" aria-hidden="true">
              <span
                v-for="(c, i) in p.preview"
                :key="i"
                class="gm-cb-mode-dropdown__chip"
                :style="{ background: c }"
              />
            </div>
            <div class="gm-cb-mode-dropdown__text">
              <span class="gm-cb-mode-dropdown__label">
                {{ p.label }}
                <el-icon
                  v-if="isActive(p)"
                  class="gm-cb-mode-dropdown__check"
                  :size="12"
                >
                  <svg
                    width="12"
                    height="12"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="3"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  >
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                </el-icon>
              </span>
              <span class="gm-cb-mode-dropdown__desc">{{ p.description }}</span>
            </div>
          </div>
        </el-dropdown-item>
      </el-dropdown-menu>
    </template>
  </el-dropdown>
</template>

<style scoped>
/* ── 触发按钮（iOS 风格，200ms 切换） ───────── */
.gm-cb-mode-toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1, 4px);
  height: 32px;
  padding: 0 var(--space-2, 8px);
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
}

.gm-cb-mode-toggle:hover {
  background: var(--brand-primary-soft, rgba(0, 229, 255, 0.15));
  border-color: var(--brand-primary);
  color: var(--brand-primary);
  box-shadow: var(--glow-primary-soft, 0 0 8px rgba(0, 229, 255, 0.3));
}

.gm-cb-mode-toggle:active {
  transform: scale(0.97);
}

.gm-cb-mode-toggle:focus-visible {
  outline: 2px solid var(--brand-primary, #00e5ff);
  outline-offset: 1px;
}

/* 激活态：非 default palette 时高亮（提示用户当前在色盲模式）*/
.gm-cb-mode-toggle--active {
  color: var(--brand-primary);
  border-color: var(--brand-primary);
  background: var(--brand-primary-soft, rgba(0, 229, 255, 0.15));
}

.gm-cb-mode-toggle__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  flex-shrink: 0;
}

.gm-cb-mode-toggle__label {
  font-weight: var(--fw-semibold, 600);
}

.gm-cb-mode-toggle__caret {
  margin-left: 2px;
  transition: transform var(--dur-fast) var(--ease-out-quint);
}

.gm-cb-mode-toggle:hover .gm-cb-mode-toggle__caret {
  transform: translateY(1px);
}

/* ── 响应式：1024px 以下压缩 padding（与 BackgroundModeToggle 同步）── */
@media (max-width: 1024px) {
  .gm-cb-mode-toggle {
    height: 28px;
    padding: 0 6px;
    font-size: 10px;
  }
}
</style>

<!--
  Element Plus Dropdown 由 Teleport 渲染到 body，scoped style 不生效。
  使用 unscoped 样式（BEM 命名空间避免泄漏）专门样式下拉内容。
-->
<style>
/* ── Dropdown 容器 ─────────────────────────────── */
.gm-cb-mode-dropdown {
  min-width: 280px !important;
  padding: 4px !important;
  background: var(--bg-elevated) !important;
  border: 1px solid var(--border-default) !important;
  border-radius: var(--radius-md, 8px) !important;
  box-shadow: var(--shadow-card, 0 4px 16px rgba(0, 0, 0, 0.35)) !important;
  backdrop-filter: blur(var(--glass-blur, 12px));
  -webkit-backdrop-filter: blur(var(--glass-blur, 12px));
}

.gm-cb-mode-dropdown__item {
  padding: 8px 12px !important;
  border-radius: var(--radius-sm, 4px) !important;
  margin: 2px 0 !important;
  line-height: 1.4 !important;
  transition: background var(--dur-fast) var(--ease-out-quint);
}

.gm-cb-mode-dropdown__item:hover:not(.is-disabled) {
  background: var(--brand-primary-soft, rgba(0, 229, 255, 0.15)) !important;
}

.gm-cb-mode-dropdown__item.is-disabled {
  opacity: 0.7;
  cursor: default;
}

.gm-cb-mode-dropdown__row {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
}

/* ── 5 色缩略条形预览 ─────────────────────────── */
.gm-cb-mode-dropdown__preview {
  display: inline-flex;
  gap: 2px;
  flex-shrink: 0;
  border-radius: 2px;
  overflow: hidden;
  box-shadow: 0 0 0 1px var(--border-default);
}

.gm-cb-mode-dropdown__chip {
  display: inline-block;
  width: 12px;
  height: 24px;
}

/* ── 文字区（标题 + 描述） ─────────────────── */
.gm-cb-mode-dropdown__text {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
  gap: 2px;
}

.gm-cb-mode-dropdown__label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-family: var(--font-cn, 'PingFang SC', 'Microsoft YaHei', sans-serif);
  font-size: var(--fs-sm, 12px);
  font-weight: var(--fw-semibold, 600);
  color: var(--text-primary);
}

.gm-cb-mode-dropdown__check {
  color: var(--brand-primary, #00e5ff);
  display: inline-flex;
}

.gm-cb-mode-dropdown__desc {
  font-family: var(--font-cn, 'PingFang SC', sans-serif);
  font-size: 10px;
  color: var(--text-muted);
  line-height: 1.3;
}
</style>
