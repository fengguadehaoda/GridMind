/**
 * web/src/components/export/sessionExport.ts · M-5 对话导出纯函数（T04）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构 session-mgmt-architecture §3.5 + PRD §四 4.2：
 *   - buildMarkdown：标题/thread_id/导出时间/导出人/模型 + 按时间顺序消息 +
 *     assistant 消息的来源引用（sources）+ 图谱（graph_answer），缺省字段跳过；
 *   - buildJson：``format_version:1`` 结构化 JSON，``messages[].knowledge_answer``
 *     含 sources/graph_answer 原样保留（与 ChatMessage.knowledgeAnswer 映射）；
 *   - downloadFile：Blob + URL.createObjectURL + a.click + revokeObjectURL；
 *   - 文件名 ``{title}-{thread_id 尾 8 位}-{YYYYMMDD-HHmmss}.md|.json``，
 *     title 中 ``/ \ : * ? " < > |`` 替换为 ``_``（浏览器安全）。
 *
 * 数据源 = 当前激活会话前端内存 ChatMessage[]（P1 仅导出当前激活会话，
 * Q3 决策：历史会话导出属 P2，零后端改动）。
 * 作者：寇豆码（工程师）
 */
import type { ChatMessage } from '../../types'

/** 导出元信息（Header 用户/角色，来源 useJwtAuth） */
export interface ExportMeta {
  user_id: string
  role: string
}

/** 导出文件扩展名 */
export type ExportFormat = 'md' | 'json'

/* ────────────────────────────────────────────────────────────
 * 时间格式化工具
 * ──────────────────────────────────────────────────────────── */

/** 导出时间（ISO）→ ``YYYY-MM-DD HH:mm:ss``（本地时区） */
export function formatExportTime(iso: string | Date): string {
  const d = typeof iso === 'string' ? new Date(iso) : iso
  if (Number.isNaN(d.getTime())) return String(iso)
  const pad = (n: number): string => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  )
}

/** 文件时间戳 ``YYYYMMDD-HHmmss`` */
export function formatFileTimestamp(d: Date): string {
  const pad = (n: number): string => String(n).padStart(2, '0')
  return (
    `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-` +
    `${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`
  )
}

/** 非法字符替换（Windows/浏览器文件名安全） */
export function sanitizeFilenamePart(value: string): string {
  return value
    .replace(/[/\\:*?"<>|]/g, '_')
    .replace(/\s+/g, '_')
    .replace(/_+/g, '_')
    .slice(0, 60)
}

/**
 * 导出文件名约定：``{title}-{thread_id 尾 8 位}-{YYYYMMDD-HHmmss}.{ext}``
 */
export function buildExportFilename(
  title: string,
  threadId: string,
  ext: ExportFormat,
): string {
  const safeTitle = sanitizeFilenamePart(String(title || '会话复盘')) || '会话复盘'
  const tail = String(threadId || '').slice(-8)
  const ts = formatFileTimestamp(new Date())
  return `${safeTitle}-${tail}-${ts}.${ext}`
}

/* ────────────────────────────────────────────────────────────
 * Markdown 导出
 * ──────────────────────────────────────────────────────────── */

/** 来源引用行：``- 《{filename}》·{section} — 匹配度 {score} — {snippet}``（缺省字段跳过） */
function sourceLine(source: {
  filename?: string
  title?: string
  section?: string | null
  score?: number | null
  snippet?: string
}): string {
  const name = source.filename || source.title || '(未知文档)'
  // 《{name}》·{section} 之间不加空格（PRD §4.2 格式）
  let line = `《${name}》`
  if (source.section) line += `·${source.section}`
  const rest: string[] = []
  if (typeof source.score === 'number' && Number.isFinite(source.score)) {
    rest.push(`— 匹配度 ${source.score.toFixed(2)}`)
  }
  if (source.snippet) rest.push(`— ${source.snippet}`)
  if (rest.length > 0) line += ` ${rest.join(' ')}`
  return `- ${line}`
}

/** 图谱推理路径块（仅 graph_answer 非空时输出；缺省字段跳过；ID→名称解析） */
function graphBlock(graph: {
  nodes?: Array<{ id?: string; name?: string; type?: string }>
  edges?: Array<{ source?: string; target?: string; relation_type?: string }>
  paths?: Array<{ nodes?: string[]; relations?: string[]; confidence?: number }>
}): string[] {
  const lines: string[] = ['#### 图谱推理路径']

  // 节点 ID → 名称映射（边/路径引用 ID，展示用名称更可读，PRD §4.2）
  const nameById = new Map<string, string>()
  for (const n of graph.nodes ?? []) {
    if (n.id) nameById.set(n.id, n.name || n.id)
  }
  const resolve = (id: string): string => nameById.get(id) || id

  if (graph.nodes?.length) {
    for (const n of graph.nodes) {
      lines.push(`- 节点：${n.name || n.type || '(未知)'}(${n.type || '未知类型'})`)
    }
  }
  if (graph.edges?.length) {
    for (const e of graph.edges) {
      lines.push(
        `- 边：${resolve(e.source || '?')} —[${e.relation_type || '?'}]→ ${resolve(e.target || '?')}`,
      )
    }
  }
  if (graph.paths?.length) {
    for (const p of graph.paths) {
      const chain = (p.nodes || []).map(resolve).join(' → ')
      const conf =
        typeof p.confidence === 'number' && Number.isFinite(p.confidence)
          ? `（置信度 ${p.confidence.toFixed(2)}）`
          : ''
      lines.push(`- 路径：${chain || '(空路径)'}${conf}`)
    }
  }
  if (lines.length === 1) return [] // 只有标题行 → 无内容，整块跳过
  return lines
}

/**
 * 组装 Markdown 导出文本（PRD §4.2）。
 *
 * 规则：
 * - 仅 assistant 消息且 ``knowledgeAnswer.sources`` 非空 → 来源引用块；
 * - 仅 ``knowledgeAnswer.graph_answer`` 非空 → 图谱推理路径块；
 * - 缺省字段自动跳过，非 knowledge_agent 轮次不产生任何引用块（AC5-1）。
 */
export function buildMarkdown(
  threadId: string,
  title: string,
  modelId: string | null,
  messages: ChatMessage[],
  meta: ExportMeta,
): string {
  const lines: string[] = []
  lines.push(`# 会话复盘：${title || '新会话'}`)
  lines.push(`- 会话 ID：${threadId}`)
  lines.push(`- 导出时间：${formatExportTime(new Date())}`)
  lines.push(`- 导出人：${meta.user_id || '访客'}`)
  lines.push(`- 模型：${modelId || '全局默认'}`)
  lines.push('')
  lines.push('## 消息')
  lines.push('')

  for (const msg of messages) {
    if (msg.role === 'system' || msg.role === 'tool') {
      // 系统/工具消息也按角色输出，保留完整时序
      lines.push(`### ${msg.role === 'system' ? '系统' : '工具'}（${formatExportTime(msg.timestamp)}）`)
      lines.push(msg.content || '')
      lines.push('')
      continue
    }
    const label = msg.role === 'user' ? '用户' : '助手'
    lines.push(`### ${label}（${formatExportTime(msg.timestamp)}）`)
    lines.push(msg.content || '')
    lines.push('')

    // 仅 assistant 消息携带知识引用/图谱（M-3/M-4）
    if (msg.role === 'assistant' && msg.knowledgeAnswer) {
      const ka = msg.knowledgeAnswer
      if (Array.isArray(ka.sources) && ka.sources.length > 0) {
        lines.push('#### 来源引用')
        for (const s of ka.sources) {
          lines.push(sourceLine(s))
        }
        lines.push('')
      }
      if (ka.graph_answer && ka.graph_answer.nodes?.length) {
        lines.push(...graphBlock(ka.graph_answer))
        lines.push('')
      }
    }
  }

  return lines.join('\n')
}

/* ────────────────────────────────────────────────────────────
 * JSON 导出
 * ──────────────────────────────────────────────────────────── */

/** 单条消息 → 导出 DTO（snake_case 与后端 Pydantic 对齐；knowledge_answer 原样保留） */
function toExportMessage(m: ChatMessage): Record<string, unknown> {
  const base: Record<string, unknown> = {
    role: m.role,
    content: m.content,
    timestamp: m.timestamp,
  }
  if (m.role === 'assistant' && m.knowledgeAnswer) {
    const ka = m.knowledgeAnswer
    const entry: Record<string, unknown> = {
      answer: ka.answer,
      citations: ka.citations,
      graph_paths: ka.graph_paths,
      confidence: ka.confidence,
      refuse: ka.refuse,
    }
    if (ka.refuse_reason !== undefined) entry.refuse_reason = ka.refuse_reason
    // M-3：结构化来源（sources）原样保留
    if (Array.isArray(ka.sources)) entry.sources = ka.sources
    // M-4：图谱问答（graph_answer）原样保留（nodes/edges/paths/backend/degraded…）
    if (ka.graph_answer) entry.graph_answer = ka.graph_answer
    base.knowledge_answer = entry
  }
  return base
}

/**
 * 组装 JSON 导出文本（PRD §4.2 · format_version:1）。
 *
 * ``messages[].knowledge_answer`` 含 sources/graph_answer 原样保留；
 * 非 knowledge_agent 轮次无该键（缺省跳过，AC5-1）。
 */
export function buildJson(
  threadId: string,
  title: string,
  modelId: string | null,
  messages: ChatMessage[],
  meta: ExportMeta,
): string {
  const payload = {
    format_version: 1,
    exported_at: new Date().toISOString(),
    exported_by: meta.user_id || '访客',
    exported_role: meta.role || 'dispatcher',
    thread_id: threadId,
    title: title || '新会话',
    model_id: modelId,
    messages: messages.map(toExportMessage),
  }
  return JSON.stringify(payload, null, 2)
}

/* ────────────────────────────────────────────────────────────
 * 下载
 * ──────────────────────────────────────────────────────────── */

/**
 * 浏览器 Blob 下载（零后端调用，纯前端能力）。
 *
 * @param filename - 下载文件名（含扩展名）
 * @param content  - 文件文本内容
 * @param mime     - MIME（text/markdown | application/json）
 */
export function downloadFile(filename: string, content: string, mime: string): void {
  const blob = new Blob([content], { type: `${mime};charset=utf-8` })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.style.display = 'none'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  // 延迟 revoke，确保下载已触发（Safari 需要）
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
