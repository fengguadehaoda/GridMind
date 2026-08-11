<template>
  <div class="graph-qa-panel" data-test="graph-qa-panel">
    <!-- 头部行：图谱答案 + backend 徽标 + 降级弱提示 + 跳数截断标注 -->
    <div class="gqa-header">
      <span class="gqa-title">[图谱] 图谱答案</span>
      <el-tag
        size="small"
        :type="backendTagType"
        effect="plain"
        class="gqa-badge"
        data-test="gqa-backend-badge"
      >
        backend: {{ graphAnswer.backend }}
      </el-tag>
      <el-tag
        v-if="graphAnswer.degraded"
        size="small"
        type="warning"
        effect="plain"
        class="gqa-badge"
        data-test="gqa-degraded-badge"
      >
        ⚠ 当前为降级图谱，数据有限
      </el-tag>
      <el-tag
        v-if="hasOverThreeHops"
        size="small"
        type="info"
        effect="plain"
        class="gqa-badge"
      >
        hops 上限 3
      </el-tag>
    </div>

    <!-- 图谱图 + 路径列表（nodes/edges 非空 → 正常渲染，US-4 降级规则 ①） -->
    <template v-if="hasGraph">
      <div class="gqa-body">
        <div class="gqa-graph">
          <ForceGraphView
            :nodes="forceNodes"
            :edges="forceEdges"
            :tooltip-formatter="tooltipFormatter"
            :tooltip-show-delay="200"
            :on-click-node="openDetail"
            :legend-data="legendData"
            :legend-colors="legendColors"
            :height="340"
            :min-height="260"
            data-test="graph-qa-force-graph"
          />
        </div>

        <div class="gqa-paths">
          <div class="gqa-paths-header">
            <span class="gqa-paths-title">路径列表</span>
            <el-radio-group v-model="hopFilter" size="small" class="gqa-hop-filter">
              <el-radio-button value="all">全部</el-radio-button>
              <el-radio-button value="1">1</el-radio-button>
              <el-radio-button value="2">2</el-radio-button>
              <el-radio-button value="3">3</el-radio-button>
            </el-radio-group>
          </div>

          <div v-if="filteredPaths.length" class="gqa-paths-list">
            <div v-for="group in groupedPaths" :key="group.hop" class="gqa-hop-group">
              <div class="gqa-hop-label">{{ group.hop }} 跳</div>
              <div
                v-for="(path, idx) in group.paths"
                :key="`${group.hop}-${idx}`"
                class="gqa-path-row"
                :class="{ active: isPathActive(path) }"
                role="button"
                tabindex="0"
                @click="togglePath(path)"
                @keydown.enter="togglePath(path)"
                data-test="gqa-path-row"
              >
                <div class="gqa-path-meta">
                  <span class="gqa-path-name">路径 {{ pathNumber(path) }}</span>
                  <span class="gqa-path-hop">({{ path.hops }}跳)</span>
                  <span class="gqa-path-conf">置信度 {{ formatPct(path.confidence) }}</span>
                </div>
                <div class="gqa-path-chain">
                  <template v-for="(nid, j) in path.nodes" :key="j">
                    <el-tag
                      size="small"
                      :type="isSeed(nid) ? 'primary' : 'info'"
                      effect="plain"
                      class="gqa-path-node"
                    >{{ nodeName(nid) }}</el-tag>
                    <span v-if="j < path.nodes.length - 1" class="gqa-path-rel">{{ path.relations[j] }}</span>
                  </template>
                </div>
              </div>
            </div>
          </div>
          <div v-else class="gqa-paths-empty">无符合当前筛选的路径</div>
        </div>
      </div>

      <div class="gqa-legend">
        图例：seed 高亮 | 1跳 | 2跳 | 3跳
      </div>
    </template>

    <!-- 降级回退：graph_answer 存在但 nodes/edges 空（US-4 降级规则 ②，复用路径文字样式） -->
    <template v-else>
      <el-alert
        class="gqa-fallback-alert"
        title="当前为降级图谱，数据有限，以下为路径文字"
        type="warning"
        :closable="false"
        show-icon
      />
      <div v-if="fallbackPathsList.length" class="gqa-fallback-paths">
        <div v-for="(path, i) in fallbackPathsList" :key="i" class="gqa-fallback-path">
          <div class="gqa-fallback-label">路径 {{ i + 1 }}</div>
          <div class="gqa-fallback-nodes">
            <template v-for="(node, j) in path" :key="j">
              <el-tag size="small" type="primary" class="gqa-fallback-node">{{ node }}</el-tag>
              <el-icon v-if="j < path.length - 1" class="gqa-fallback-arrow"><ArrowRight /></el-icon>
            </template>
          </div>
        </div>
      </div>
      <div v-else class="gqa-fallback-empty">暂无图谱路径数据</div>
    </template>

    <!-- 实体详情浮层（US-2：属性表 + 关联来源 CitationCard + 复制 doc_id） -->
    <el-dialog
      v-model="detailVisible"
      :title="detailTitle"
      width="min(520px, 92vw)"
      append-to-body
      class="gqa-detail-dialog"
      data-test="gqa-detail-dialog"
    >
      <div v-if="detailNode" class="gqa-detail">
        <table class="gqa-props-table">
          <tbody>
            <tr>
              <th>名称</th>
              <td>{{ detailNode.name }}</td>
            </tr>
            <tr>
              <th>类型</th>
              <td>{{ detailNode.type }}</td>
            </tr>
            <tr>
              <th>距 seed</th>
              <td>{{ detailNode.hop === 0 ? 'seed' : detailNode.hop == null ? '未知' : `${detailNode.hop} 跳` }}</td>
            </tr>
            <tr>
              <th>置信度</th>
              <td>{{ detailNode.confidence == null ? '—' : `${(detailNode.confidence * 100).toFixed(0)}%` }}</td>
            </tr>
            <tr v-for="(v, k) in detailProps" :key="k">
              <th>{{ k }}</th>
              <td>{{ v }}</td>
            </tr>
          </tbody>
        </table>

        <div class="gqa-detail-sources">
          <div class="gqa-detail-sources-title">关联来源（{{ detailSources.length }}）</div>
          <CitationCard
            v-for="(source, i) in detailSources"
            :key="i"
            :source="source"
            :index="i"
          />
          <div v-if="!detailSources.length" class="gqa-detail-no-sources">暂无关联来源</div>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
/**
 * GraphQAPanel.vue · M-4 图谱问答面板（T04）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 职责（架构 §3.6 + §7 共享知识 #6/#7）：
 *   - 图谱图（ForceGraphView props 驱动）+ 路径列表（跳数分组/置信度）
 *   - backend/degraded 徽标；hops 超 3 截断标注
 *   - hover tooltip（0.3s 内，escapeTooltip 转义，US-2）
 *   - 点击节点 → 实体详情浮层（属性表 + 关联来源 CitationCard + 复制 doc_id）
 *   - 路径行点击 → 图谱高亮该路径（P1）
 *   - 降级回退：nodes/edges 空 → 降级横幅 + 路径文字 chips（不白屏不报错）
 *
 * 输入类型（架构 §3.6）：graphAnswer / fallbackPaths / sources。
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ArrowRight } from '@element-plus/icons-vue'
import type {
  GraphAnswer,
  GraphAnswerNode,
  GraphPath,
  SourceRef,
} from '../types'
import ForceGraphView, {
  type ForceGraphNodeClick,
  type ForceGraphTooltipParams,
} from './grayscale/ForceGraphView.vue'
import CitationCard from './kb/CitationCard.vue'
import { readPalette, watchThemeChange } from '../utils/echartsTheme'
import { escapeTooltipText } from '../utils/escape'
import {
  buildForceEdges,
  buildForceNodes,
  groupPathsByHops,
  groupSourcesByDocIds,
  pathNodeIds,
  type GraphNodePayload,
} from '../composables/useGraphAnswer'
import { formatScore } from '../composables/useKbSources'

const props = defineProps<{
  /** 图谱问答答案（RagPanel 在 answer.graph_answer 存在时渲染） */
  graphAnswer: GraphAnswer
  /** answer.graph_paths（nodes/edges 空时的降级路径文字回退） */
  fallbackPaths?: string[][]
  /** answer.sources（节点详情关联来源聚合，US-5 与来源卡片区同源） */
  sources?: SourceRef[]
}>()

// ── 图谱图 / 路径列表 ──────────────────────────────

const hasGraph = computed(
  () => !!(props.graphAnswer.nodes?.length && props.graphAnswer.edges?.length),
)
const backendTagType = computed(() =>
  props.graphAnswer.backend === 'neo4j' ? 'success' : 'warning',
)
const hasOverThreeHops = computed(
  () => (props.graphAnswer.paths || []).some((p) => p.hops > 3),
)

// 主题联动：主题/色盲 palette 变化 → 重算节点/边颜色（ForceGraphView 内部
// 只重渲染 option，颜色由本面板算好成 props，故需在此监听重算）
const themeTick = ref(0)
let unwatchTheme: (() => void) | null = null
onMounted(() => {
  unwatchTheme = watchThemeChange(() => {
    themeTick.value += 1
  })
})
onUnmounted(() => {
  unwatchTheme?.()
})

/** 当前高亮路径（点击路径行切换） */
const activePath = ref<GraphPath | null>(null)
const activePathIds = computed<Set<string>>(() =>
  activePath.value ? pathNodeIds(activePath.value) : new Set<string>(),
)

const forceNodes = computed(() => {
  void themeTick.value
  return buildForceNodes(props.graphAnswer, readPalette(), activePathIds.value)
})
const forceEdges = computed(() => {
  void themeTick.value
  return buildForceEdges(props.graphAnswer, readPalette())
})
const legendData = ['seed', '1跳', '2跳', '3跳']
const legendColors = computed(() => {
  void themeTick.value
  const palette = readPalette()
  return [palette.brand, palette.info, palette.warning, palette.warning]
})

// ── hover tooltip（US-2：名称/类型/关键属性/跳数/关联来源数；转义）──

function tooltipFormatter(params: ForceGraphTooltipParams): string {
  if (params.dataType !== 'node' || !params.data?.raw) return ''
  const p = params.data.raw as unknown as GraphNodePayload
  const propsList = Object.entries(p.properties || {})
    .slice(0, 3)
    .map(([k, v]) => `${k}: ${String(v)}`)
    .map((s) => escapeTooltipText(s))
  const hopLabel = p.hop === 0 ? 'seed' : p.hop == null ? '未知' : `${p.hop} 跳`
  return [
    `<b>${escapeTooltipText(p.name)}</b>`,
    `类型：${escapeTooltipText(p.type)}`,
    `距 seed：${hopLabel}`,
    `关联来源：${(p.doc_ids || []).length} 个`,
    ...propsList,
  ].join('<br/>')
}

// ── 点击节点 → 实体详情浮层（US-2）────────────────

const detailVisible = ref(false)
const detailNode = ref<GraphAnswerNode | null>(null)

function openDetail(node: ForceGraphNodeClick): void {
  const found = (props.graphAnswer.nodes || []).find((n) => n.id === node.id)
  if (!found) return
  detailNode.value = found
  detailVisible.value = true
}

const detailTitle = computed(() => detailNode.value?.name ?? '实体详情')

const detailProps = computed<Record<string, string>>(() => {
  const raw = detailNode.value?.properties || {}
  const out: Record<string, string> = {}
  for (const [k, v] of Object.entries(raw)) {
    if (Object.keys(out).length >= 8) break
    if (v === null || v === undefined) continue
    if (typeof v === 'object') continue
    out[k] = String(v)
  }
  return out
})

const detailSources = computed<SourceRef[]>(() => {
  const node = detailNode.value
  if (!node) return []
  const allSources = props.sources?.length
    ? props.sources
    : (props.graphAnswer.sources || [])
  return groupSourcesByDocIds(allSources, node.doc_ids || [])
})

// ── 路径列表（US-3：跳数分组/排序 + 置信度；路径行点击高亮）──

const hopFilter = ref<'all' | '1' | '2' | '3'>('all')

const allPaths = computed<GraphPath[]>(() => props.graphAnswer.paths || [])

interface PathsGroup {
  hop: number
  paths: GraphPath[]
}

const groupedPaths = computed<PathsGroup[]>(() => {
  const filtered = allPaths.value.filter((p) => {
    if (hopFilter.value === 'all') return true
    return p.hops === Number(hopFilter.value)
  })
  return Array.from(groupPathsByHops(filtered).entries()).map(([hop, paths]) => ({
    hop,
    paths,
  }))
})

const filteredPaths = computed<GraphPath[]>(() =>
  groupedPaths.value.flatMap((g) => g.paths),
)

const fallbackPathsList = computed<string[][]>(() => props.fallbackPaths || [])

function pathNumber(path: GraphPath): number {
  const idx = allPaths.value.indexOf(path)
  return idx >= 0 ? idx + 1 : 0
}

function formatPct(confidence: number | null | undefined): string {
  return formatScore(confidence == null ? null : confidence) ?? '—'
}

function isSeed(nid: string): boolean {
  const node = (props.graphAnswer.nodes || []).find((n) => n.id === nid)
  if (node) return node.hop === 0
  return (props.graphAnswer.seed_ids || []).includes(nid)
}

function nodeName(nid: string): string {
  const node = (props.graphAnswer.nodes || []).find((n) => n.id === nid)
  return node?.name ?? nid
}

function togglePath(path: GraphPath): void {
  activePath.value = activePath.value === path ? null : path
}

function isPathActive(path: GraphPath): boolean {
  return activePath.value === path
}
</script>

<style scoped lang="scss">
.graph-qa-panel {
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  padding: var(--space-3);
  margin-bottom: var(--space-3);
  transition: var(--theme-transition);
}

.gqa-header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
}

.gqa-title {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
}

.gqa-badge {
  font-family: var(--font-mono);
}

.gqa-body {
  display: grid;
  grid-template-columns: minmax(0, 3fr) minmax(0, 2fr);
  gap: var(--space-3);
}

@media (max-width: 767.98px) {
  .gqa-body {
    grid-template-columns: 1fr;
  }
}

.gqa-graph {
  min-width: 0;
}

.gqa-paths {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.gqa-paths-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.gqa-paths-title {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
}

.gqa-paths-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  max-height: 340px;
  overflow-y: auto;
}

.gqa-hop-group {
  border: 1px solid var(--border-muted);
  border-radius: var(--radius-sm);
  padding: var(--space-2);
}

.gqa-hop-label {
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  color: var(--text-muted);
  margin-bottom: var(--space-1);
}

.gqa-path-row {
  cursor: pointer;
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-sm);
  border: 1px solid transparent;
  transition: var(--theme-transition);
}

.gqa-path-row:hover {
  background: var(--brand-primary-fade);
}

.gqa-path-row.active {
  border-color: var(--brand-accent);
  background: var(--brand-primary-fade);
}

.gqa-path-meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.gqa-path-name {
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  color: var(--text-primary);
}

.gqa-path-hop {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}

.gqa-path-conf {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--brand-primary);
}

.gqa-path-chain {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-1);
  margin-top: var(--space-1);
}

.gqa-path-node {
  font-size: var(--fs-xs);
}

.gqa-path-rel {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}

.gqa-paths-empty {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  color: var(--text-muted);
  padding: var(--space-2);
}

.gqa-legend {
  margin-top: var(--space-2);
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}

/* ── 降级回退（路径文字 chips，复用 RagPanel graph_paths 样式语义）── */
.gqa-fallback-alert {
  margin-bottom: var(--space-2);
}

.gqa-fallback-paths {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.gqa-fallback-path {
  padding: var(--space-2) 0;
}

.gqa-fallback-label {
  font-size: var(--fs-xs);
  color: var(--text-muted);
  margin-bottom: var(--space-1);
  font-family: var(--font-cn);
}

.gqa-fallback-nodes {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-1);
}

.gqa-fallback-node {
  font-size: var(--fs-xs);
}

.gqa-fallback-arrow {
  color: var(--text-muted);
  font-size: var(--fs-xs);
}

.gqa-fallback-empty {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  color: var(--text-muted);
  padding: var(--space-2);
}

/* ── 实体详情浮层 ── */
.gqa-detail {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.gqa-props-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-sm);
}

.gqa-props-table th,
.gqa-props-table td {
  border: 1px solid var(--border-muted);
  padding: var(--space-1) var(--space-2);
  text-align: left;
  vertical-align: top;
}

.gqa-props-table th {
  width: 120px;
  font-family: var(--font-cn);
  color: var(--text-muted);
  background: var(--bg-base);
  font-weight: var(--fw-medium);
  white-space: nowrap;
}

.gqa-props-table td {
  color: var(--text-secondary);
  word-break: break-all;
}

.gqa-detail-sources-title {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
  margin-bottom: var(--space-2);
}

.gqa-detail-no-sources {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  color: var(--text-muted);
  padding: var(--space-2);
}
</style>
