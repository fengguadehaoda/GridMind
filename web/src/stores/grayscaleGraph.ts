/**
 * stores/grayscaleGraph.ts · KG 灰度可视化 store（v1.6.0 P1-4）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构决策（p1-iteration-architecture §1 P1-4 + §8 待明确 #2 默认方案）：
 *   - 数据源默认前端模拟（基于 metricsStore.status + 固定拓扑模板，节点 ≤200）；
 *     后端 GET /grayscale/graph 就绪后 fetchGraph() 优先走 API（404 → 回落模拟）
 *   - 节点编码：大小 = 负载率(load)，颜色 = 错误率(errorRate → status 色阶)
 *   - 双模式：explore（AI 推荐方案，只读）/ plan（勾选节点 → 生成方案）
 *   - 方案"应用" → metricsStore.setRatio(plan.targetRatio, 'panel', adminToken)
 */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useMetricsStore } from '@/stores/metrics'
import { getGrayscaleGraph } from '@/api/metrics'
import { ElMessage } from 'element-plus'
import type {
  GrayscaleGraph,
  GrayscaleGraphNode,
  GrayscaleMode,
  GrayscalePlan,
  GrayscalePlanScore,
} from '@/types/theme'

/** 模拟节点名池（电网场景） */
const BACKEND_NAMES = ['backend A', 'backend B']
const CANDIDATE_NAMES = ['候选节点 1', '候选节点 2', '候选节点 3', '候选节点 4', '候选节点 5']
const ALARM_NAMES = ['告警 T1-过温', '告警 B3-负载越限', '告警 L2-潮流异常']
const METRIC_NAMES = ['潮流指标', '电压指标', '频率指标']
const CHECKPOINT_NAMES = ['checkpoint C1', 'checkpoint C2', 'checkpoint C3']

function clampLoad(v: number): number {
  return Math.max(5, Math.min(98, Math.round(v)))
}

/** 确定性伪随机（避免每次刷新拓扑跳动） */
function seeded(seed: number): () => number {
  let s = seed
  return () => {
    s = (s * 9301 + 49297) % 233280
    return s / 233280
  }
}

export const useGrayscaleGraphStore = defineStore('grayscaleGraph', () => {
  const metrics = useMetricsStore()

  // ═══ State ═══
  const graph = ref<GrayscaleGraph>({ nodes: [], edges: [] })
  const plans = ref<GrayscalePlan[]>([])
  const mode = ref<GrayscaleMode>('explore')
  const selectedNodeIds = ref<string[]>([])
  const loading = ref(false)
  const source = ref<'api' | 'mock'>('mock')
  const fetchFailed = ref(false)
  const lastAppliedPlanId = ref<string>('')

  // ═══ Computed ═══
  const nodeCount = computed(() => graph.value.nodes.length)
  const edgeCount = computed(() => graph.value.edges.length)

  /** 可勾选节点（规划模式：candidate / backend 且非 excluded） */
  const selectableNodes = computed(() =>
    graph.value.nodes.filter(
      (n) => n.status !== 'excluded' && (n.type === 'candidate' || n.type === 'backend'),
    ),
  )

  const selectedNodes = computed(() =>
    graph.value.nodes.filter((n) => selectedNodeIds.value.includes(n.id)),
  )

  // ═══ Actions ═══

  /**
   * 拉取拓扑图：优先后端 GET /grayscale/graph，404/失败回落前端模拟。
   */
  async function fetchGraph(): Promise<void> {
    if (loading.value) return
    loading.value = true
    fetchFailed.value = false
    try {
      const resp = await getGrayscaleGraph()
      const nodes: GrayscaleGraphNode[] = (resp.nodes ?? []).map((n) => ({
        id: n.id,
        name: n.name,
        type: normalizeNodeType(n.type),
        load: clampLoad(n.load),
        errorRate: n.error_rate ?? 0,
        status: n.status === 'excluded' ? 'excluded' : n.status === 'candidate' ? 'candidate' : 'active',
        meta: n.meta,
      }))
      graph.value = {
        nodes,
        edges: (resp.edges ?? []).map((e) => ({
          source: e.source,
          target: e.target,
          label: e.label,
          weight: e.weight,
        })),
      }
      source.value = 'api'
    } catch (err) {
      // 404 / 未排期 → 回落模拟（共享知识 #10：不静默崩溃）
      console.info('[grayscaleGraph] API 不可用，使用前端模拟数据', err)
      graph.value = buildMockGraph()
      source.value = 'mock'
      fetchFailed.value = true
    } finally {
      loading.value = false
      // 模式切换 / 数据就绪后统一生成方案
      buildPlans()
    }
  }

  /** 根据后端状态生成模拟拓扑（≤200 节点：2 backend + 5 candidate + 3 alarm + 3 metric + 3 checkpoint + 边） */
  function buildMockGraph(): GrayscaleGraph {
    const rand = seeded(Math.floor(Date.now() / 60000))
    const baseErrorRate = metrics.monitor?.error_rate ?? 0.02
    const ratio = metrics.ratio

    const nodes: GrayscaleGraphNode[] = []
    const edges: GrayscaleGraph['edges'] = []

    // backend（当前切流状态：比例高 → backend B active）
    BACKEND_NAMES.forEach((name, idx) => {
      const isB = idx === 1
      nodes.push({
        id: `backend_${idx}`,
        name,
        type: 'backend',
        load: clampLoad(45 + rand() * 40),
        errorRate: Math.min(0.2, baseErrorRate + rand() * 0.03),
        status: isB ? (ratio > 0 ? 'active' : 'candidate') : 'active',
      })
    })

    // candidate 候选节点
    CANDIDATE_NAMES.forEach((name, idx) => {
      nodes.push({
        id: `candidate_${idx}`,
        name,
        type: 'candidate',
        load: clampLoad(20 + rand() * 60),
        errorRate: rand() * 0.12,
        status: 'candidate',
      })
    })

    // alarm 关联告警
    ALARM_NAMES.forEach((name, idx) => {
      nodes.push({
        id: `alarm_${idx}`,
        name,
        type: 'alarm',
        load: clampLoad(30 + rand() * 50),
        errorRate: 0.1 + rand() * 0.4,
        status: 'active',
      })
    })

    // metric 指标
    METRIC_NAMES.forEach((name, idx) => {
      nodes.push({
        id: `metric_${idx}`,
        name,
        type: 'metric',
        load: clampLoad(25 + rand() * 55),
        errorRate: rand() * 0.08,
        status: 'active',
      })
    })

    // checkpoint 回滚点
    CHECKPOINT_NAMES.forEach((name, idx) => {
      nodes.push({
        id: `checkpoint_${idx}`,
        name,
        type: 'checkpoint',
        load: clampLoad(15 + rand() * 30),
        errorRate: 0.01,
        status: 'active',
      })
    })

    // 边：backend → candidate；candidate → alarm/metric；backend → checkpoint
    for (let b = 0; b < 2; b++) {
      for (let c = 0; c < CANDIDATE_NAMES.length; c++) {
        edges.push({ source: `backend_${b}`, target: `candidate_${c}`, weight: 1 })
      }
      for (let k = 0; k < CHECKPOINT_NAMES.length; k++) {
        edges.push({ source: `backend_${b}`, target: `checkpoint_${k}`, weight: 0.6 })
      }
    }
    for (let c = 0; c < CANDIDATE_NAMES.length; c++) {
      edges.push({ source: `candidate_${c}`, target: `alarm_${c % ALARM_NAMES.length}`, weight: 0.8 })
      edges.push({ source: `candidate_${c}`, target: `metric_${c % METRIC_NAMES.length}`, weight: 0.8 })
    }

    return { nodes, edges }
  }

  /** 切换探索 / 规划模式 */
  function setMode(next: GrayscaleMode): void {
    mode.value = next
    if (next === 'explore') {
      selectedNodeIds.value = []
    }
    buildPlans()
  }

  /** 规划模式勾选 / 取消节点 */
  function toggleNode(id: string): void {
    if (mode.value !== 'plan') return
    if (selectedNodeIds.value.includes(id)) {
      selectedNodeIds.value = selectedNodeIds.value.filter((n) => n !== id)
    } else {
      selectedNodeIds.value.push(id)
    }
    buildPlans()
  }

  /** 生成 ≥3 方案（A/B/C 三维打分 + 总分） */
  function buildPlans(): void {
    const nodes = graph.value.nodes
    if (!nodes.length) {
      plans.value = []
      return
    }

    const candidates = nodes.filter((n) => n.type === 'candidate')
    const pool =
      mode.value === 'plan' && selectedNodeIds.value.length
        ? nodes.filter((n) => selectedNodeIds.value.includes(n.id))
        : candidates

    const base = pool.length ? pool : candidates

    const makePlan = (
      id: string,
      name: string,
      subset: GrayscaleGraphNode[],
      targetRatio: number,
      recommended: boolean,
    ): GrayscalePlan => {
      const switchCount = Math.max(1, subset.length)
      const avgLoad =
        subset.length > 0
          ? subset.reduce((acc, n) => acc + n.load, 0) / subset.length
          : 50
      const avgError =
        subset.length > 0
          ? subset.reduce((acc, n) => acc + n.errorRate, 0) / subset.length
          : 0.02

      const loadScore = clampScore(100 - Math.abs(avgLoad - 60))
      const switchScore = clampScore(100 - switchCount * 12)
      const protectionScore = clampScore(100 - avgError * 400)

      const scores: GrayscalePlanScore[] = [
        { dimension: 'switchCount', label: '操作开关数量', value: switchScore, raw: `${switchCount} 个` },
        { dimension: 'loadRate', label: '负载率', value: loadScore, raw: `${avgLoad.toFixed(0)}%` },
        { dimension: 'protectionFit', label: '保护适配性', value: protectionScore, raw: protectionRank(protectionScore) },
      ]
      const total = Math.round(
        scores[0]!.value * 0.3 + scores[1]!.value * 0.35 + scores[2]!.value * 0.35,
      )
      return {
        id,
        name,
        mode: mode.value,
        scores,
        total,
        targetRatio,
        selectedNodeIds: subset.map((n) => n.id),
        recommended,
      }
    }

    const generatedPlans: GrayscalePlan[] = []
    if (base.length >= 3) {
      generatedPlans.push(makePlan('plan_a', '方案 A', base.slice(0, Math.ceil(base.length / 3)), 30, false))
      generatedPlans.push(makePlan('plan_b', '方案 B', base.slice(0, Math.ceil(base.length / 2)), 50, false))
      generatedPlans.push(makePlan('plan_c', '方案 C', base, 70, true))
    } else if (base.length >= 1) {
      generatedPlans.push(makePlan('plan_a', '方案 A', base, 30, false))
      generatedPlans.push(makePlan('plan_b', '方案 B', base, 50, false))
      generatedPlans.push(makePlan('plan_c', '方案 C', base, 70, true))
    }
    plans.value = generatedPlans
  }

  /**
   * 应用方案 → 联动现有切流接口（metricsStore.setRatio）。
   * @param plan 方案
   * @param adminToken X-Admin-Token
   */
  async function applyPlan(plan: GrayscalePlan, adminToken: string): Promise<void> {
    if (!adminToken) {
      ElMessage.warning('请输入 X-Admin-Token')
      return
    }
    try {
      await metrics.setRatio(plan.targetRatio, 'grayscale-plan', adminToken)
      lastAppliedPlanId.value = plan.id
      ElMessage.success(`已应用「${plan.name}」，切流至 ${plan.targetRatio}%`)
    } catch (err) {
      console.error('[grayscaleGraph.applyPlan]', err)
      // metricsStore.operationMsg 已记录失败原因
    }
  }

  return {
    // state
    graph,
    plans,
    mode,
    selectedNodeIds,
    loading,
    source,
    fetchFailed,
    lastAppliedPlanId,
    // computed
    nodeCount,
    edgeCount,
    selectableNodes,
    selectedNodes,
    // actions
    fetchGraph,
    buildMockGraph,
    setMode,
    toggleNode,
    buildPlans,
    applyPlan,
  }
})

/** 归一化后端节点类型 */
function normalizeNodeType(t: string): GrayscaleGraphNode['type'] {
  if (t === 'backend' || t === 'candidate' || t === 'alarm' || t === 'metric' || t === 'checkpoint') {
    return t
  }
  return 'metric'
}

function clampScore(v: number): number {
  return Math.max(5, Math.min(100, Math.round(v)))
}

function protectionRank(score: number): string {
  if (score >= 90) return '优'
  if (score >= 75) return '良'
  return '中'
}
