/**
 * markdown.ts · Markdown subset 渲染器（v1.6.0 P1-2）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构决策（p1-iteration-architecture §1 P1-2 + §7 共享知识 #8）：
 *   - 自研 subset（约 200 LOC），不引 marked（连带 XSS 清理成本）
 *   - 支持：标题 h1-h6 / 段落 / **加粗** / `行内代码` / 围栏代码块 / 表格 /
 *     无序-有序列表 / 引用 / 分隔线 / 链接 / mermaid 围栏块 → 占位容器
 *   - 内容仅限 web/public/help/ 内置受信精选文档；不解析原始 HTML 标签（转义输出）
 *   - 渲染器输出经 HelpCenter.vue 的 v-html 展示
 */

import type { MarkdownHeading } from '@/types/theme'

/** HTML 转义（防注入：先转义再拼标签） */
export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/** 生成标题锚点 id（与 extractHeadings 一致） */
function slugify(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/\s+/g, '-')
    .replace(/[^\w\u4e00-\u9fa5-]/g, '')
    .slice(0, 64)
}

/** 行内解析：代码 / 加粗 / 斜体 / 链接（输入为已转义文本，输出 HTML 片段） */
function renderInline(src: string): string {
  let out = ''
  let i = 0
  const n = src.length
  while (i < n) {
    const ch = src[i]!

    // 行内代码 `...`
    if (ch === '`') {
      const end = src.indexOf('`', i + 1)
      if (end > i) {
        const code = src.slice(i + 1, end)
        out += `<code>${code}</code>`
        i = end + 1
        continue
      }
    }

    // 链接 [text](url)
    if (ch === '[') {
      const closeBracket = src.indexOf(']', i + 1)
      if (closeBracket > i && src[closeBracket + 1] === '(') {
        const closeParen = src.indexOf(')', closeBracket + 2)
        if (closeParen > closeBracket) {
          const text = src.slice(i + 1, closeBracket)
          const url = src.slice(closeBracket + 2, closeParen).trim()
          const safeUrl = sanitizeUrl(url)
          if (safeUrl) {
            out += `<a href="${safeUrl}" target="_blank" rel="noopener noreferrer">${text}</a>`
            i = closeParen + 1
            continue
          }
        }
      }
    }

    // 加粗 **text**
    if (ch === '*' && src[i + 1] === '*') {
      const end = src.indexOf('**', i + 2)
      if (end > i) {
        const inner = renderInline(src.slice(i + 2, end))
        out += `<strong>${inner}</strong>`
        i = end + 2
        continue
      }
    }

    // 斜体 *text*（单星号，不与加粗冲突）
    if (ch === '*' && src[i + 1] !== '*') {
      const end = src.indexOf('*', i + 1)
      if (end > i) {
        out += `<em>${renderInline(src.slice(i + 1, end))}</em>`
        i = end + 1
        continue
      }
    }

    out += ch
    i += 1
  }
  return out
}

/** 仅允许 http/https/mailto 协议链接 */
function sanitizeUrl(url: string): string {
  const trimmed = url.trim()
  if (/^(https?:\/\/|mailto:)/i.test(trimmed)) return escapeHtml(trimmed)
  return ''
}

interface CodeBlock {
  lang: string
  code: string
}

/**
 * 渲染整个 Markdown 源文本 → HTML。
 * 结构：逐行状态机，优先处理围栏代码块 / 表格 / 列表，再处理标题 / 引用 / 分隔线 / 段落。
 */
export function render(src: string): string {
  const lines = src.replace(/\r\n/g, '\n').split('\n')
  const html: string[] = []
  let i = 0
  const n = lines.length

  while (i < n) {
    const line = lines[i]!

    // 围栏代码块（含 mermaid）
    const fenceMatch = line.match(/^```(\w*)\s*$/)
    if (fenceMatch) {
      const lang = fenceMatch[1] ?? ''
      const codeLines: string[] = []
      i += 1
      while (i < n && !/^```\s*$/.test(lines[i]!)) {
        codeLines.push(lines[i]!)
        i += 1
      }
      i += 1 // 跳过闭合 ```
      const code = codeLines.join('\n')
      if (lang === 'mermaid') {
        // mermaid → 占位容器（不做客户端渲染，架构 §7 共享知识 #8）
        html.push(`<div class="gm-mermaid-placeholder" role="img" aria-label="流程图占位：${escapeHtml(codeLines[0] ?? 'mermaid 图')}"><span class="gm-mermaid-placeholder__icon">◈</span><span class="gm-mermaid-placeholder__text">流程图占位 · ${escapeHtml(codeLines[0] ?? 'mermaid')}</span></div>`)
      } else {
        html.push(`<pre><code class="language-${escapeHtml(lang || 'text')}">${escapeHtml(code)}</code></pre>`)
      }
      continue
    }

    // 表格：当前行以 | 开头且下一行是分隔行（|---|）
    if (/^\|.*\|$/.test(line) && i + 1 < n && /^\|?[\s:|-]+\|?\s*$/.test(lines[i + 1]!) && lines[i + 1]!.includes('-')) {
      const headerCells = splitTableRow(line)
      i += 2 // 跳过表头 + 分隔行
      const bodyRows: string[][] = []
      while (i < n && /^\|.*\|$/.test(lines[i]!)) {
        bodyRows.push(splitTableRow(lines[i]!))
        i += 1
      }
      html.push(renderTable(headerCells, bodyRows))
      continue
    }

    // 无序列表
    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = []
      while (i < n && /^\s*[-*+]\s+/.test(lines[i]!)) {
        items.push(renderInline(escapeHtml(lines[i]!.replace(/^\s*[-*+]\s+/, ''))))
        i += 1
      }
      html.push(`<ul>${items.map((item) => `<li>${item}</li>`).join('')}</ul>`)
      continue
    }

    // 有序列表
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = []
      while (i < n && /^\s*\d+[.)]\s+/.test(lines[i]!)) {
        items.push(renderInline(escapeHtml(lines[i]!.replace(/^\s*\d+[.)]\s+/, ''))))
        i += 1
      }
      html.push(`<ol>${items.map((item) => `<li>${item}</li>`).join('')}</ol>`)
      continue
    }

    // 引用
    if (/^>\s?/.test(line)) {
      const quoteLines: string[] = []
      while (i < n && /^>\s?/.test(lines[i]!)) {
        quoteLines.push(lines[i]!.replace(/^>\s?/, ''))
        i += 1
      }
      html.push(`<blockquote>${renderInline(escapeHtml(quoteLines.join('\n')))}</blockquote>`)
      continue
    }

    // 标题
    const headingMatch = line.match(/^(#{1,6})\s+(.*)$/)
    if (headingMatch) {
      const level = headingMatch[1]!.length
      const text = headingMatch[2]!.trim()
      const id = slugify(text)
      html.push(`<h${level} id="${id}">${renderInline(escapeHtml(text))}</h${level}>`)
      i += 1
      continue
    }

    // 分隔线
    if (/^(---+|\*\*\*+|___+)\s*$/.test(line)) {
      html.push('<hr />')
      i += 1
      continue
    }

    // 空行 → 跳过
    if (/^\s*$/.test(line)) {
      i += 1
      continue
    }

    // 段落（连续非空行）
    const para: string[] = []
    while (
      i < n &&
      !/^\s*$/.test(lines[i]!) &&
      !/^(#{1,6})\s+/.test(lines[i]!) &&
      !/^```/.test(lines[i]!) &&
      !/^\s*[-*+]\s+/.test(lines[i]!) &&
      !/^\s*\d+[.)]\s+/.test(lines[i]!) &&
      !/^>\s?/.test(lines[i]!)
    ) {
      para.push(lines[i]!)
      i += 1
    }
    if (para.length) {
      html.push(`<p>${renderInline(escapeHtml(para.join(' ')))}</p>`)
    }
  }

  return html.join('\n')
}

/** 拆分表格行（去掉首尾 |，按 | 切分） */
function splitTableRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim())
}

/** 渲染表格 HTML */
function renderTable(header: string[], rows: string[][]): string {
  const thead = `<thead><tr>${header
    .map((h) => `<th>${renderInline(escapeHtml(h))}</th>`)
    .join('')}</tr></thead>`
  const tbody = rows
    .map(
      (row) =>
        `<tr>${row
          .map((cell, idx) => `<td>${renderInline(escapeHtml(cell))}</td>`)
          .join('')}</tr>`,
    )
    .join('')
  return `<div class="gm-markdown-table-wrap"><table><thead>${thead}</thead><tbody>${tbody}</tbody></table></div>`
}

/** 抽取标题（目录导航用） */
export function extractHeadings(src: string): MarkdownHeading[] {
  const headings: MarkdownHeading[] = []
  for (const line of src.replace(/\r\n/g, '\n').split('\n')) {
    const m = line.match(/^(#{1,6})\s+(.*)$/)
    if (m) {
      const text = m[2]!.trim()
      headings.push({ level: m[1]!.length, text, id: slugify(text) })
    }
  }
  return headings
}

/** 抽取纯文本（全文搜索索引用）：去掉围栏代码、标记符号、表格管道 */
export function extractSearchText(src: string): string {
  const lines = src.replace(/\r\n/g, '\n').split('\n')
  const out: string[] = []
  let inFence = false
  for (const line of lines) {
    if (/^```/.test(line)) {
      inFence = !inFence
      continue
    }
    if (inFence) continue
    // 去掉标题井号 / 列表符号 / 引用 / 表格管道
    let t = line
      .replace(/^#{1,6}\s+/, '')
      .replace(/^\s*[-*+]\s+/, '')
      .replace(/^\s*\d+[.)]\s+/, '')
      .replace(/^>\s?/, '')
      .replace(/\|/g, ' ')
      .replace(/[*_`[\]]/g, '')
    t = t.replace(/\[([^\]]*)\]\([^)]*\)/g, '$1') // 链接保留文字
    if (t.trim()) out.push(t.trim())
  }
  return out.join('\n')
}
