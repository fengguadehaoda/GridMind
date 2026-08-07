<script setup lang="ts">
/**
 * HexGrid · 六边形拓扑背景（M2 任务，本期占位）
 * 仅渲染静态六边形 pattern + 几个示例节点
 * 完整节点交互与连线将在 M2 阶段实现
 *
 * v1.5.0 T02：接 intensity prop
 * - 'off'  时整体 opacity=0（保留 DOM 避免重渲染）
 * - 其他档位维持原 0.18 stroke-opacity 视觉
 *   注：当前架构仅本组件占位，无视图直接使用，prop 已就绪等待未来 view 接入
 */
import { computed } from 'vue'
import { useTheme } from '@/composables/useTheme'
import type { HexGridProps, BackgroundIntensity } from '@/types/theme'

const props = withDefaults(defineProps<HexGridProps & { intensity?: BackgroundIntensity }>(), {
  cols: 12,
  rows: 8,
  interactive: false,
  intensity: 'mid',
})

const { isDark: _isDark } = useTheme() // 占位：未来用于节点交互高亮
void _isDark

// 使用 CSS 变量（双主题自动适配），不再硬编码颜色
const stroke = computed(() => 'var(--brand-primary)')
const glow = computed(() => 'var(--brand-primary-soft)')

/** intensity 派生 → 整体透明度（off = 0，其他档位 1，由内部 stroke-opacity 控实际浓淡） */
const wrapOpacity = computed(() => (props.intensity === 'off' ? 0 : 1))
</script>

<template>
  <!-- M2 占位：仅静态六边形 pattern + 4 个示例节点 -->
  <div class="gm-hex-grid" aria-hidden="true" :style="{ opacity: wrapOpacity }">
    <svg
      class="gm-hex-grid__bg"
      xmlns="http://www.w3.org/2000/svg"
      preserveAspectRatio="xMidYMid slice"
    >
      <defs>
        <pattern id="gm-hex-pattern" width="60" height="52" patternUnits="userSpaceOnUse">
          <polygon
            points="30,2 56,16 56,38 30,52 4,38 4,16"
            fill="none"
            :stroke="stroke"
            stroke-width="0.8"
            stroke-opacity="0.18"
          />
        </pattern>
      </defs>
      <rect width="100%" height="100%" fill="url(#gm-hex-pattern)" />
    </svg>

    <!-- 4 个示例节点（M2 阶段实现交互） -->
    <div class="gm-hex-grid__nodes">
      <div class="gm-hex-node" :style="{ top: '20%', left: '15%' }">
        <div class="gm-hex-node__label">monitor</div>
      </div>
      <div class="gm-hex-node" :style="{ top: '30%', left: '70%' }">
        <div class="gm-hex-node__label">diagnosis</div>
      </div>
      <div class="gm-hex-node" :style="{ top: '65%', left: '25%' }">
        <div class="gm-hex-node__label">rag</div>
      </div>
      <div class="gm-hex-node gm-hex-node--accent" :style="{ top: '70%', left: '78%' }">
        <div class="gm-hex-node__label">planner</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.gm-hex-grid {
  position: absolute;
  inset: 0;
  z-index: var(--z-base);
  pointer-events: none;
  overflow: hidden;
  transition: opacity var(--dur-base) var(--ease-out-quint);
}

.gm-hex-grid__bg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}

.gm-hex-grid__nodes {
  position: absolute;
  inset: 0;
}

.gm-hex-node {
  position: absolute;
  width: 80px;
  height: 80px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-card);
  border: 1px solid var(--brand-primary);
  clip-path: polygon(25% 0%, 75% 0%, 100% 50%, 75% 100%, 25% 100%, 0% 50%);
  box-shadow: 0 0 12px var(--brand-primary-soft);
  transition: var(--theme-transition);
}

.gm-hex-node__label {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.1em;
}

.gm-hex-node--accent {
  border-color: var(--brand-accent);
  box-shadow: 0 0 16px var(--brand-accent-soft);
}

.gm-hex-node--accent .gm-hex-node__label {
  color: var(--brand-accent);
}
</style>
