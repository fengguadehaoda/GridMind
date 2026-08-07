/**
 * fuzzy.ts · 自研模糊搜索（v1.6.0 P1-1）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构决策（p1-iteration-architecture §1 P1-1）：
 *   - 不引 fuse.js / pinyin-pro：语料极小（5 路由 + 10 操作 + 少量上下文命令）
 *   - 中文 / 拼音首字母 / 英文三种语义由命令注册时显式携带 keywords 覆盖：
 *     如 "实时监控" → ['监控', 'jk', 'monitor']
 *   - 匹配器对 title/subtitle/keywords 做归一化 + 子串/子序列打分
 *
 * 打分语义（越高越优，0 = 不命中）：
 *   - 完全子串：100 + (100 - 起始位置) + 前缀加成 50
 *   - 子序列：60 + 命中数 * 8 - 跳跃惩罚
 *   - title 命中额外 +20，keyword 命中额外 +15
 */

export interface FuzzyMatchable {
  title: string
  subtitle?: string
  keywords?: string[]
}

/** 归一化查询串：小写 + 去首尾空白 + 压缩连续空白 */
export function normalizeQuery(s: string): string {
  return s
    .trim()
    .toLowerCase()
    .replace(/\s+/g, ' ')
}

/** 归一化候选文本（查询前对每个候选字段调用） */
function normalizeText(s: string): string {
  return s.toLowerCase()
}

/** 计算 query 在单个文本上的命中分数（0 = 不命中） */
function scoreText(text: string, query: string): number {
  if (!query) return 0
  const t = normalizeText(text)
  if (!t) return 0

  // 1) 完全子串
  const idx = t.indexOf(query)
  if (idx >= 0) {
    let score = 100 + (100 - idx)
    if (idx === 0) score += 50 // 前缀命中加成
    return score
  }

  // 2) 子序列匹配（字符按顺序出现，允许跳跃）
  let ti = 0
  let hits = 0
  let gapPenalty = 0
  let lastHit = -1
  for (let qi = 0; qi < query.length; qi++) {
    const ch = query[qi]
    // 跳过空白字符（归一化后 query 不含连续空白，但可能含单个空格）
    if (ch === ' ') continue
    let found = -1
    for (let j = ti; j < t.length; j++) {
      if (t[j] === ch) {
        found = j
        break
      }
    }
    if (found < 0) return 0 // 子序列失败 → 不命中
    hits += 1
    if (lastHit >= 0) {
      gapPenalty += found - lastHit - 1
    }
    lastHit = found
    ti = found + 1
  }
  // 短 query（≤2 字符）的子序列需要完全连续才可信，避免噪音命中
  if (query.length <= 2 && gapPenalty > 0) return 0
  return 60 + hits * 8 - gapPenalty * 2
}

/**
 * 对一组候选文本（title/subtitle/keywords）计算综合分数。
 * @returns 0 = 不命中；>0 = 命中分数
 */
export function matchScore(texts: string[], query: string): number {
  const q = normalizeQuery(query)
  if (!q) return 0

  let best = 0
  for (let i = 0; i < texts.length; i++) {
    const text = texts[i] ?? ''
    const score = scoreText(text, q)
    if (score > best) best = score
  }
  return best
}

/**
 * 过滤 + 排序命令列表。
 * - query 为空 → 返回全部（保持原顺序，score=1 便于 UI 区分）
 * - 否则按分数降序
 */
export function filter<T extends FuzzyMatchable>(
  items: T[],
  query: string,
): Array<{ item: T; score: number }> {
  const q = normalizeQuery(query)
  if (!q) {
    return items.map((item) => ({ item, score: 1 }))
  }

  const results: Array<{ item: T; score: number }> = []
  for (const item of items) {
    const texts = [item.title, item.subtitle ?? '', ...(item.keywords ?? [])]
    let score = matchScore(texts, q)

    // title 命中加成（title 单独再算一次，提升中文标题的优先级）
    if (score > 0) {
      const titleScore = scoreText(item.title, q)
      if (titleScore > 0) score += 20
      if (item.keywords?.some((k) => normalizeText(k).includes(q))) {
        score += 15 // 显式关键词（含拼音首字母）命中加成
      }
      results.push({ item, score })
    }
  }

  results.sort((a, b) => b.score - a.score)
  return results
}

/** 工具：把 query 在文本中命中的片段包上 <mark>（用于命令面板高亮；文本为受信自有数据） */
export function highlightMatch(text: string, query: string): string {
  const q = normalizeQuery(query)
  if (!q) return escapeHtml(text)
  const t = normalizeText(text)
  const idx = t.indexOf(q)
  if (idx < 0) return escapeHtml(text)
  return (
    escapeHtml(text.slice(0, idx)) +
    '<mark>' +
    escapeHtml(text.slice(idx, idx + q.length)) +
    '</mark>' +
    escapeHtml(text.slice(idx + q.length))
  )
}

/** 简单 HTML 转义（高亮拼接用） */
function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}
