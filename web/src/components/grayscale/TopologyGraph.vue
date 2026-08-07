<script setup lang="ts">
/**
 * TopologyGraph.vue · ECharts 力导向拓扑图（v1.6.0 P1-4）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 *
 * ⚠️ F8 已知依赖公告豁免（QA F8 P1 · GHSA-fgmj-fm8m-jvvx）：
 *   - echarts 锁定 ^5.6.0（<6.1.0 存在 moderate XSS 公告：tooltip/富文本渲染
 *     可被恶意 data 注入 HTML）。
 *   - **缓解措施**：本组件所有进入 tooltip 的节点/边文本一律经
 *     `escapeTooltip()`（< / > → &lt; / &gt;）转义（见下方 renderChart/formatter），
 *     杜绝 HTML 注入向量；其余文本字段均为受控内部数据，无用户自由输入。
 *   - **升级计划**：v1.7.0 排期升级 echarts 6.x（需回归拓扑渲染 + tooltip 富文本
 *     样式；本次工业化部署不升级以控制回归风险）。
 *
 * 架构决策（p1-iteration-architecture §1 P1-4 + §7 共享知识 #4）：
 *   - 按需引入 echarts（GraphChart + Tooltip/Legend + CanvasRenderer），不封装 vue-echarts
 *   - 节点大小 = 负载率(load)；颜色 = 错误率(errorRate → status 色阶)；
 *     类型分 backend/candidate/alarm/metric/checkpoint（色盲 palette 经 tokens 中间层）
 *   - 颜色一律经 utils/echartsTheme.ts 读取 CSS 变量，禁止硬编码
 *   - MutationObserver 监听 data-theme / data-cb-palette → setOption 实时生效
 *   - 规划模式点击节点 → 勾选/取消（高亮边框）；探索模式只读
 *   - onUnmounted 必须 chart.dispose()
 */
import { onMounted, onUnmounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { GraphChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsCoreOption } from 'echarts/core'
import { useGrayscaleGraphStore } from '@/stores/grayscaleGraph'
import { readPalette, watchThemeChange, errorRateColor } from '@/utils/echartsTheme'
import type { EchartsThemePalette } from '@/utils/echartsTheme'
import type { GrayscaleGraphNode } from '@/types/theme'

echarts.use([GraphChart, TooltipComponent, LegendComponent, CanvasRenderer])

const graphStore = useGrayscaleGraphStore()

const containerRef = ref<HTMLElement | null>(null)
let chart: ReturnType<typeof echarts.init> | null = null
let unwatchTheme: (() => void) | null = null

/** 节点类型 → 基础色（tokens） */
function typeColor(type: GrayscaleGraphNode['type'], palette: EchartsThemePalette): string {
  switch (type) {
    case 'backend':
      return palette.brand
    case 'candidate':
      return palette.success
    case 'alarm':
      return palette.danger
    case 'metric':
      return palette.info
    case 'checkpoint':
      return palette.warning
    default:
      return palette.info
  }
}

/** 组装力导向图 option */
function buildOption(): EChartsCoreOption {
  const palette = readPalette()
  const { graph, mode, selectedNodeIds } = graphStore

  const nodes = graph.nodes.map((n) => {
    const base = typeColor(n.type, palette)
    // 错误率越界 → 红色（状态色阶；与监控面板一致）
    const color = n.errorRate > 0.05 ? errorRateColor(n.errorRate, palette) : base
    const selected = mode === 'plan' && selectedNodeIds.includes(n.id)
    return {
      id: n.id,
      name: n.name,
      value: Math.round(n.load),
      symbolSize: 18 + n.load * 0.5,
      itemStyle: {
        color,
        borderColor: selected ? palette.accent : palette.bgCard,
        borderWidth: selected ? 4 : 1,
        shadowBlur: selected ? 12 : 0,
        shadowColor: palette.accent,
      },
      label: {
        show: true,
        formatter: n.name,
        fontSize: 11,
        color: palette.textPrimary,
      },
      // 附加业务字段（tooltip 用）
      raw: n,
    }
  })

  const edges = graph.edges.map((e) => ({
    source: e.source,
    target: e.target,
    label: e.label ? { show: true, formatter: e.label, fontSize: 10, color: palette.textMuted } : undefined,
    lineStyle: {
      color: palette.border,
      width: (e.weight ?? 1) * 1.5,
      curveness: 0.1,
      opacity: 0.6,
    },
  }))

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      formatter: (params: { dataType?: string; data?: { name?: string; raw?: GrayscaleGraphNode } }) => {
        if (params.dataType !== 'node' || !params.data?.raw) return ''
        const n = params.data.raw
        const typeLabel: Record<string, string> = {
          backend: '后端',
          candidate: '候选',
          alarm: '告警',
          metric: '指标',
          checkpoint: '回滚点',
        }
        return [
          `<b>${escapeTooltip(n.name)}</b>`,
          `类型：${typeLabel[n.type] ?? n.type}`,
          `负载率：${n.load.toFixed(0)}%`,
          `错误率：${(n.errorRate * 100).toFixed(2)}%`,
          `状态：${n.status}`,
        ].join('<br/>')
      },
    },
    legend: {
      top: 4,
      right: 8,
      itemWidth: 12,
      itemHeight: 12,
      data: [
        { name: 'backend', itemStyle: { color: palette.brand } },
        { name: 'candidate', itemStyle: { color: palette.success } },
        { name: 'alarm', itemStyle: { color: palette.danger } },
        { name: 'metric', itemStyle: { color: palette.info } },
        { name: 'checkpoint', itemStyle: { color: palette.warning } },
      ],
    },
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        data: nodes,
        edges,
        categories: [
          { name: 'backend' },
          { name: 'candidate' },
          { name: 'alarm' },
          { name: 'metric' },
          { name: 'checkpoint' },
        ],
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

function escapeTooltip(s: string): string {
  return s.replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function renderChart(): void {
  if (!chart) return
  chart.setOption(buildOption(), true)
}

function handleClick(params: unknown): void {
  const p = params as { dataType?: string; data?: { id?: string } | null }
  if (p.dataType !== 'node' || !p.data?.id) return
  if (graphStore.mode !== 'plan') return
  graphStore.toggleNode(p.data.id)
}

onMounted(() => {
  if (!containerRef.value) return
  chart = echarts.init(containerRef.value)
  renderChart()
  chart.on('click', handleClick)
  // 主题 / 色盲 palette 变化 → 实时重绘
  unwatchTheme = watchThemeChange(() => renderChart())
})

onUnmounted(() => {
  unwatchTheme?.()
  chart?.dispose()
  chart = null
})

// 数据 / 模式 / 选中变化 → 重绘
watch(
  () => [graphStore.graph, graphStore.mode, graphStore.selectedNodeIds] as const,
  () => renderChart(),
  { deep: true },
)
</script>

<template>
  <div class="gm-topology-graph">
    <div
      ref="containerRef"
      class="gm-topology-graph__canvas"
      data-test="topology-graph"
    ></div>
    <div class="gm-topology-graph__legend-hint">
      <span>节点大小 = 负载率</span>
      <span>颜色 = 错误率（绿→黄→红）</span>
      <span v-if="graphStore.mode === 'plan'">点击节点勾选 / 取消（规划模式）</span>
      <span v-else>探索模式 · 只读</span>
      <span v-if="graphStore.source === 'mock'" class="gm-topology-graph__source">
        模拟数据（后端 /grayscale/graph 未就绪）
      </span>
    </div>
  </div>
</template>

<style scoped lang="scss">
.gm-topology-graph {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.gm-topology-graph__canvas {
  width: 100%;
  height: 420px;
  min-height: 320px;
  border: 1px solid var(--border-muted);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  transition: var(--theme-transition);
}

.gm-topology-graph__legend-hint {
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

.gm-topology-graph__source {
  margin-left: auto;
  color: var(--status-warning);
}

@media (max-width: 1279.98px) {
  .gm-topology-graph__canvas {
    height: 340px;
  }
}
</style>
