<script setup lang="ts">
/**
 * ForceGraphView.vue · ECharts 力导向图通用渲染组件（M-4 T03）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 职责边界（架构 §3.5 + §7 共享知识 #6）：
 *   - 只做 ECharts 力导向渲染（props 驱动 + 主题联动 + 转义 + 点击回调 + 图例）；
 *   - **不感知任何业务语义**——灰度 store（load/errorRate）或图谱问答
 *     （hop/doc_ids）均在调用方（TopologyGraph / GraphQAPanel）算好成 props；
 *   - 颜色/大小/业务 tooltip 一律由调用方传入，组件内部零业务 import
 *     （不 import grayscaleGraph store、不 import GraphAnswer 类型）。
 *
 * F8 已知依赖公告豁免（同 TopologyGraph）：
 *   - echarts 锁定 ^5.6.0；tooltip 富文本可被恶意 data 注入 HTML →
 *     调用方 tooltipFormatter 必须经 `escapeTooltip()` 转义（组件提供
 *     默认安全 formatter 兜底）。
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { GraphChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsCoreOption } from 'echarts/core'
import { readPalette, watchThemeChange } from '@/utils/echartsTheme'
import { escapeTooltipText } from '@/utils/escape'
import type { ForceGraphEdgeInput, ForceGraphNodeInput } from '@/types'

echarts.use([GraphChart, TooltipComponent, LegendComponent, CanvasRenderer])

export interface ForceGraphTooltipParams {
  dataType?: string
  data?: { name?: string; raw?: Record<string, unknown> | null } | null
}

export interface ForceGraphNodeClick {
  id: string
  name?: string
  raw?: Record<string, unknown> | null
}

const props = withDefaults(
  defineProps<{
    nodes: ForceGraphNodeInput[]
    edges: ForceGraphEdgeInput[]
    /** 画布高度 px（默认 420） */
    height?: number
    /** 最小高度 px（默认 320） */
    minHeight?: number
    /** 图例项（可选，字符串数组） */
    legendData?: string[]
    /** 图例项图标颜色（与 legendData 平行；缺省用 tokens 默认色） */
    legendColors?: string[]
    /** 调用方负责 escapeTooltip 转义；缺省用默认安全 formatter */
    tooltipFormatter?: (params: ForceGraphTooltipParams) => string
    /** tooltip 出现延迟 ms（默认 0；GraphQAPanel 传 200 满足 0.3s 内出现） */
    tooltipShowDelay?: number
    /** 节点点击回调（调用方决定业务动作） */
    onClickNode?: (node: ForceGraphNodeClick) => void
    /** 底部图例提示行 */
    hintText?: string[]
    /** data-test 属性值 */
    dataTest?: string
    /** nodes/edges 为空时展示文案 */
    emptyText?: string
  }>(),
  {
    height: 420,
    minHeight: 320,
    legendData: () => [],
    legendColors: () => [],
    tooltipFormatter: undefined,
    tooltipShowDelay: 0,
    onClickNode: undefined,
    hintText: () => [],
    dataTest: 'force-graph-view',
    emptyText: '暂无图谱数据',
  },
)

const containerRef = ref<HTMLElement | null>(null)
let chart: ReturnType<typeof echarts.init> | null = null
let unwatchTheme: (() => void) | null = null

const isEmpty = computed(() => !props.nodes.length || !props.edges.length)

/** 默认安全 tooltip（转义节点名；业务 formatter 由调用方注入） */
function defaultTooltipFormatter(params: ForceGraphTooltipParams): string {
  if (params.dataType !== 'node' || !params.data?.name) return ''
  return `<b>${escapeTooltipText(params.data.name)}</b>`
}

/** 组装力导向图 option（颜色/大小均为调用方算好的 props） */
function buildOption(): EChartsCoreOption {
  const palette = readPalette()

  const nodes = props.nodes.map((n) => ({
    id: n.id,
    name: n.name,
    symbolSize: n.symbolSize,
    itemStyle: {
      color: n.color,
      borderColor: n.borderColor ?? palette.bgCard,
      borderWidth: n.borderWidth ?? 1,
      shadowBlur: n.shadowBlur ?? 0,
      shadowColor: n.shadowColor ?? palette.accent,
    },
    category: n.category,
    label: {
      show: true,
      formatter: n.name,
      fontSize: 11,
      color: palette.textPrimary,
    },
    // 透传给 tooltipFormatter / click 的业务载荷
    raw: n.raw ?? null,
  }))

  const edges = props.edges.map((e) => ({
    source: e.source,
    target: e.target,
    label: e.label
      ? { show: true, formatter: e.label, fontSize: 10, color: palette.textMuted }
      : undefined,
    lineStyle: {
      color: e.color ?? palette.border,
      width: e.width ?? 1,
      curveness: e.curveness ?? 0.1,
      opacity: e.opacity ?? 0.6,
    },
  }))

  const formatter = props.tooltipFormatter ?? defaultTooltipFormatter
  const hasLegend = props.legendData.length > 0

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      showDelay: props.tooltipShowDelay,
      formatter,
    },
    legend: hasLegend
      ? {
          top: 4,
          right: 8,
          itemWidth: 12,
          itemHeight: 12,
          data: props.legendData.map((name, i) => ({
            name,
            itemStyle: { color: props.legendColors[i] ?? palette.brand },
          })),
        }
      : undefined,
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        data: nodes,
        edges,
        categories: hasLegend ? props.legendData.map((name) => ({ name })) : undefined,
        force: {
          repulsion: 180,
          edgeLength: 90,
          gravity: 0.1,
          friction: 0.6,
        },
        emphasis: {
          focus: 'adjacency',
          lineStyle: { width: 3 },
        },
        lineStyle: { color: palette.border, opacity: 0.6 },
        label: { show: true, fontSize: 11, color: palette.textSecondary },
      },
    ],
  }
}

function renderChart(): void {
  if (!chart) return
  chart.setOption(buildOption(), true)
}

function handleResize(): void {
  chart?.resize()
}

function handleClick(params: unknown): void {
  const p = params as {
    dataType?: string
    data?: { id?: string; name?: string; raw?: Record<string, unknown> | null } | null
  }
  if (p.dataType !== 'node' || !p.data?.id) return
  props.onClickNode?.({ id: p.data.id, name: p.data.name, raw: p.data.raw ?? null })
}

onMounted(() => {
  if (!containerRef.value) return
  chart = echarts.init(containerRef.value)
  renderChart()
  chart.on('click', handleClick)
  window.addEventListener('resize', handleResize)
  // 主题 / 色盲 palette 变化 → 实时重绘
  unwatchTheme = watchThemeChange(() => renderChart())
})

onUnmounted(() => {
  unwatchTheme?.()
  window.removeEventListener('resize', handleResize)
  chart?.dispose()
  chart = null
})

// 数据变化 → 重绘
watch(
  () => [props.nodes, props.edges] as const,
  () => renderChart(),
  { deep: true },
)
</script>

<template>
  <div class="gm-force-graph-view">
    <div
      ref="containerRef"
      class="gm-force-graph-view__canvas"
      :style="{ height: `${height}px`, minHeight: `${minHeight}px` }"
      :data-test="dataTest"
    ></div>
    <div v-if="isEmpty" class="gm-force-graph-view__empty-overlay">{{ emptyText }}</div>
    <div v-if="hintText.length" class="gm-force-graph-view__hint">
      <span v-for="(hint, i) in hintText" :key="i">{{ hint }}</span>
    </div>
  </div>
</template>

<style scoped lang="scss">
.gm-force-graph-view {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.gm-force-graph-view__canvas {
  width: 100%;
  border: 1px solid var(--border-muted);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  transition: var(--theme-transition);
}

.gm-force-graph-view__empty-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  color: var(--text-muted);
  pointer-events: none;
}

.gm-force-graph-view__hint {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2) var(--space-4);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--border-muted);
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}
</style>
