/**
 * useGraphAnswer.ts · M-4 图谱问答纯逻辑（T04）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 职责（架构 §3.6 + §7 共享知识 #6）：
 *   - hop 色阶 / type→symbolSize 权重（架构 §3.6 规格）
 *   - buildForceNodes / buildForceEdges：GraphAnswer → ForceGraphView props
 *     （颜色/大小/边框由本模块算好，ForceGraphView 不感知业务语义）
 *   - groupSourcesByDocIds：节点 doc_ids → 关联来源聚合（US-2/US-5）
 *   - pathNodeIds：路径行点击 → 高亮节点集合（P1）
 *
 * 字段命名与后端 snake_case 完全一致（K-1），无 camelCase 转换。
 */

import type {
  EchartsThemePalette,
} from '@/utils/echartsTheme'
import type {
  ForceGraphEdgeInput,
  ForceGraphNodeInput,
  GraphAnswer,
  GraphAnswerNode,
  GraphPath,
  SourceRef,
} from '../types'

/** 节点 raw 载荷（透传给 tooltipFormatter / click 的业务字段） */
export interface GraphNodePayload {
  id: string
  name: string
  type: string
  hop: number | null
  doc_ids: string[]
  confidence: number | null
  properties: Record<string, unknown>
  is_seed: boolean
}

/** hop 色阶：seed=0 brand 高亮；1/2/3 跳 brand→info→warning 渐变（架构 §3.6） */
export function hopColor(hop: number | null | undefined, palette: EchartsThemePalette): string {
  if (hop === 0) return palette.brand
  if (hop === 1) return palette.info
  if (hop === 2) return palette.warning
  if (hop === 3) return palette.warning
  return palette.textMuted
}

/** 节点大小按实体类型权重：设备 28 / 故障 24 / 处置 20 / 其他 18（架构 §3.6） */
export function typeSymbolSize(type: string | null | undefined): number {
  switch (type) {
    case '设备':
      return 28
    case '故障':
      return 24
    case '处置':
      return 20
    default:
      return 18
  }
}

/** hop → 图例分类（seed / 1跳 / 2跳 / 3跳 / 其他） */
export function hopCategory(hop: number | null | undefined): string {
  if (hop === 0) return 'seed'
  if (hop === null || hop === undefined) return '其他'
  return `${hop}跳`
}

/**
 * GraphAnswer → ForceGraphView 节点 props。
 *
 * @param answer 图谱答案。
 * @param palette 主题 tokens 快照（readPalette()）。
 * @param activePathIds 当前高亮路径的节点 id 集合（点击路径行时非空）。
 */
export function buildForceNodes(
  answer: GraphAnswer,
  palette: EchartsThemePalette,
  activePathIds: Set<string> = new Set(),
): ForceGraphNodeInput[] {
  const seedIds = new Set(answer.seed_ids || [])
  return (answer.nodes || []).map((n: GraphAnswerNode) => {
    const isSeed = n.hop === 0 || seedIds.has(n.id)
    const highlighted = activePathIds.has(n.id)
    return {
      id: n.id,
      name: n.name,
      symbolSize: typeSymbolSize(n.type),
      color: hopColor(n.hop, palette),
      // seed 高亮边框 accent；路径高亮同样用 accent
      borderColor: highlighted || isSeed ? palette.accent : palette.bgCard,
      borderWidth: highlighted ? 4 : isSeed ? 3 : 1,
      shadowBlur: highlighted || isSeed ? 10 : 0,
      shadowColor: palette.accent,
      category: hopCategory(n.hop),
      raw: {
        id: n.id,
        name: n.name,
        type: n.type,
        hop: n.hop ?? null,
        doc_ids: n.doc_ids || [],
        confidence: n.confidence ?? null,
        properties: n.properties || {},
        is_seed: isSeed,
      } satisfies GraphNodePayload as unknown as Record<string, unknown>,
    }
  })
}

/** GraphAnswer → ForceGraphView 边 props（标签 = relation_type） */
export function buildForceEdges(
  answer: GraphAnswer,
  palette: EchartsThemePalette,
): ForceGraphEdgeInput[] {
  return (answer.edges || []).map((e) => ({
    source: e.source,
    target: e.target,
    label: e.relation_type,
    color: palette.border,
    width: 1.5,
    curveness: 0.1,
    opacity: 0.6,
  }))
}

/**
 * 按节点 doc_ids 聚合关联来源（US-2/US-5）：来源与来源卡片区指向同一批 doc_id。
 * 只返回 doc_id 在 docIds 中的来源；docIds 为空 → []。
 */
export function groupSourcesByDocIds(
  sources: SourceRef[],
  docIds: string[],
): SourceRef[] {
  if (!docIds.length) return []
  const wanted = new Set(docIds)
  return (sources || []).filter((s) => s.doc_id && wanted.has(s.doc_id))
}

/** 路径行点击 → 该路径节点 id 集合（用于图谱高亮，P1） */
export function pathNodeIds(path: GraphPath): Set<string> {
  return new Set(path.nodes || [])
}

/** 路径跳数分组：{ hop: GraphPath[] }，按跳数升序 */
export function groupPathsByHops(paths: GraphPath[]): Map<number, GraphPath[]> {
  const map = new Map<number, GraphPath[]>()
  for (const p of paths || []) {
    const list = map.get(p.hops) || []
    list.push(p)
    map.set(p.hops, list)
  }
  // 跳数升序
  return new Map([...map.entries()].sort((a, b) => a[0] - b[0]))
}
