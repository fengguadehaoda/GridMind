<script setup lang="ts">
/**
 * TopologyGraph.vue · ECharts 力导向拓扑图（v1.6.0 P1-4 → M-4 T03 泛化委托）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * M-4 重构（架构 §3.5，决策 5）：
 *   - 把 ECharts 力导向渲染抽取为 props 驱动子组件 `ForceGraphView.vue`，
 *     本组件只做「store 数据 → ForceGraphView props」的映射 + plan 勾选逻辑；
 *   - **对外 API 不变**（零 props）——GrayscalePanel.vue 仍以 `<TopologyGraph />`
 *     调用，灰度页（探索/规划勾选/load 大小/errorRate 颜色/source mock 提示）
 *     与重构前一致（零回归）；
 *   - 职责边界（§7 #6）：颜色/大小/业务 tooltip 由本组件算好成 props，
 *     ForceGraphView 不感知 grayscaleGraph store。
 *
 * F8 已知依赖公告豁免（同 v1.6.0）：
 *   - echarts 锁定 ^5.6.0；tooltip 文本统一经共享 util `escapeTooltip()`
 *     转义（原内联实现上移为 web/src/utils/escape.ts，行为不变）。
 */
import { computed } from 'vue'
import { useGrayscaleGraphStore } from '@/stores/grayscaleGraph'
import { readPalette, errorRateColor } from '@/utils/echartsTheme'
import type { EchartsThemePalette } from '@/utils/echartsTheme'
import type { GrayscaleGraphNode } from '@/types/theme'
import type { ForceGraphEdgeInput, ForceGraphNodeInput } from '@/types'
import { escapeTooltipText } from '@/utils/escape'
import ForceGraphView, {
  type ForceGraphNodeClick,
  type ForceGraphTooltipParams,
} from './ForceGraphView.vue'

const graphStore = useGrayscaleGraphStore()

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

/** store 节点 → ForceGraphView 节点 props（大小=负载率；颜色=错误率/类型色阶） */
const nodes = computed<ForceGraphNodeInput[]>(() => {
  const palette = readPalette()
  return graphStore.graph.nodes.map((n) => {
    const base = typeColor(n.type, palette)
    // 错误率越界 → 红色（状态色阶；与监控面板一致）
    const color = n.errorRate > 0.05 ? errorRateColor(n.errorRate, palette) : base
    const selected = graphStore.mode === 'plan' && graphStore.selectedNodeIds.includes(n.id)
    return {
      id: n.id,
      name: n.name,
      symbolSize: 18 + n.load * 0.5,
      color,
      borderColor: selected ? palette.accent : palette.bgCard,
      borderWidth: selected ? 4 : 1,
      shadowBlur: selected ? 12 : 0,
      shadowColor: palette.accent,
      category: n.type,
      // 透传给 tooltip formatter 的业务载荷（保持原 tooltip 内容不变）
      raw: n as unknown as Record<string, unknown>,
    }
  })
})

/** store 边 → ForceGraphView 边 props */
const edges = computed<ForceGraphEdgeInput[]>(() => {
  const palette = readPalette()
  return graphStore.graph.edges.map((e) => ({
    source: e.source,
    target: e.target,
    label: e.label,
    color: palette.border,
    width: (e.weight ?? 1) * 1.5,
    curveness: 0.1,
    opacity: 0.6,
  }))
})

const legendData = computed<string[]>(() => [
  'backend',
  'candidate',
  'alarm',
  'metric',
  'checkpoint',
])

const legendColors = computed<string[]>(() => {
  const palette = readPalette()
  return [palette.brand, palette.success, palette.danger, palette.info, palette.warning]
})

/** tooltip 格式化（转义；内容与重构前一致） */
function tooltipFormatter(params: ForceGraphTooltipParams): string {
  if (params.dataType !== 'node' || !params.data?.raw) return ''
  const n = params.data.raw as unknown as GrayscaleGraphNode
  const typeLabel: Record<string, string> = {
    backend: '后端',
    candidate: '候选',
    alarm: '告警',
    metric: '指标',
    checkpoint: '回滚点',
  }
  return [
    `<b>${escapeTooltipText(n.name)}</b>`,
    `类型：${typeLabel[n.type] ?? n.type}`,
    `负载率：${n.load.toFixed(0)}%`,
    `错误率：${(n.errorRate * 100).toFixed(2)}%`,
    `状态：${n.status}`,
  ].join('<br/>')
}

/** 规划模式点击节点 → 勾选/取消；探索模式只读 */
function onClickNode(node: ForceGraphNodeClick): void {
  if (graphStore.mode !== 'plan') return
  graphStore.toggleNode(node.id)
}
</script>

<template>
  <div class="gm-topology-graph">
    <ForceGraphView
      :nodes="nodes"
      :edges="edges"
      :legend-data="legendData"
      :legend-colors="legendColors"
      :tooltip-formatter="tooltipFormatter"
      :on-click-node="onClickNode"
      data-test="topology-graph"
    />
    <!-- 图例提示行（保持重构前样式与文案，零回归） -->
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
</style>
