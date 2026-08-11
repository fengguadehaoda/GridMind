/**
 * M-3 知识库来源引用纯逻辑（T03 · useKbSources）
 *
 * 职责（架构 kb-citation-architecture-2026-08-10 §2 T03）：
 * - groupSourcesByDoc()：按 doc_id 归组（文档名 + 命中数 + 最高匹配度）
 * - filterSourcesByDoc()：按 doc_id 过滤（纯前端，不重新请求后端，P1-1）
 * - useSourcesCollapse()：引用区折叠状态 localStorage 记忆（P2-2，
 *   键 `gridmind.kbSourcesCollapsed`）
 * - formatScore()：0-1 score → 百分比字符串（保留 0 位小数；null 返回 null）
 * - sourceLabel()：filename/title/(未知文档) 降级（K-5）
 *
 * 字段命名与后端 SourceRef snake_case 完全一致（K-1），无 camelCase 转换。
 */

import { ref, watch } from 'vue'
import type { SourceRef } from '../types'

/** localStorage 折叠状态键（架构 T03 验收：gridmind.kbSourcesCollapsed） */
export const KB_SOURCES_COLLAPSE_KEY = 'gridmind.kbSourcesCollapsed'

/** 单文档来源归组 */
export interface SourceGroup {
  /** doc_id（空串表示未知文档组） */
  doc_id: string
  /** 文档显示名（filename/title/(未知文档)，K-5） */
  label: string
  /** 该文档下全部来源（保持传入顺序，通常已按 score 降序） */
  sources: SourceRef[]
  /** 命中数 */
  count: number
  /** 最高匹配度（无 score 时为 null） */
  maxScore: number | null
}

/**
 * 来源降级标签（K-5）：doc_id/filename/title 均空 → `(未知文档)`。
 */
export function sourceLabel(source: SourceRef | null | undefined): string {
  if (!source) return '(未知文档)'
  const name = source.filename || source.title || ''
  return name.trim() || '(未知文档)'
}

/**
 * score → 百分比字符串（0-1，保留 0 位小数，如 0.87 → "87%"）。
 * null / undefined / NaN → null（前端据此不显示匹配度标签，K-5）。
 */
export function formatScore(score: number | null | undefined): string | null {
  if (score === null || score === undefined || Number.isNaN(score)) return null
  return `${(score * 100).toFixed(0)}%`
}

/**
 * 按 doc_id 归组（P1-1 多文档聚合）。
 *
 * @param sources 来源列表（后端已按 score 降序；组内保持该顺序）。
 * @returns 归组列表（按组内最高 score 降序；无 score 的组排后）。
 */
export function groupSourcesByDoc(sources: SourceRef[]): SourceGroup[] {
  const map = new Map<string, SourceGroup>()
  for (const s of sources) {
    const docId = s.doc_id || ''
    let group = map.get(docId)
    if (!group) {
      group = {
        doc_id: docId,
        label: sourceLabel(s),
        sources: [],
        count: 0,
        maxScore: null,
      }
      map.set(docId, group)
    }
    group.sources.push(s)
    group.count += 1
    if (s.score !== null && s.score !== undefined && !Number.isNaN(s.score)) {
      group.maxScore = group.maxScore === null ? s.score : Math.max(group.maxScore, s.score)
    }
  }
  return Array.from(map.values()).sort((a, b) => {
    const sa = a.maxScore === null ? -1 : a.maxScore
    const sb = b.maxScore === null ? -1 : b.maxScore
    return sb - sa
  })
}

/**
 * 按 doc_id 过滤（纯前端筛选，P1-1）。
 *
 * @param sources 来源列表。
 * @param docId 目标 doc_id；null / 空串表示「全部」。
 * @returns 过滤后的来源列表。
 */
export function filterSourcesByDoc(sources: SourceRef[], docId: string | null): SourceRef[] {
  if (!docId) return sources
  return sources.filter((s) => (s.doc_id || '') === docId)
}

/**
 * 引用区折叠状态（P2-2）：localStorage 记忆，默认折叠（true）。
 *
 * @param storageKey localStorage 键（默认 `gridmind.kbSourcesCollapsed`）。
 * @returns { collapsed, toggle } —— collapsed=true 表示折叠。
 */
export function useSourcesCollapse(storageKey: string = KB_SOURCES_COLLAPSE_KEY) {
  let initial = true
  try {
    const raw = localStorage.getItem(storageKey)
    initial = raw === null ? true : raw === '1'
  } catch {
    initial = true // SSR / 隐私模式等场景降级为默认折叠
  }
  const collapsed = ref<boolean>(initial)

  function toggle(): void {
    collapsed.value = !collapsed.value
  }

  watch(collapsed, (value) => {
    try {
      localStorage.setItem(storageKey, value ? '1' : '0')
    } catch {
      /* localStorage 不可用时静默降级（折叠记忆非关键能力） */
    }
  })

  return { collapsed, toggle }
}
