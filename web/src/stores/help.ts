/**
 * stores/help.ts · 帮助中心 store（v1.6.0 P1-2）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构决策（p1-iteration-architecture §1 P1-2 + §4.3 时序图）：
 *   - manifest.json 白名单 → 按需 fetch md → extractHeadings 建目录 +
 *     extractSearchText 建纯文本索引
 *   - 查询在标题 / 章节 / 正文三域匹配，命中高亮（复用 fuzzy 归一化思路）
 *   - 语料 ≤8 篇 × 几十 KB，内存索引 < 1MB，≤100ms 无压力
 *   - 数据获取失败保留旧数据 / 空态 + ElMessage 提示（共享知识 #10）
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { HelpArticleMeta, MarkdownHeading, SearchHit } from '@/types/theme'
import { extractHeadings, extractSearchText, render } from '@/utils/markdown'
import { normalizeQuery } from '@/utils/fuzzy'
import { ElMessage } from 'element-plus'

const MANIFEST_URL = '/help/manifest.json'
const CURRENT_DOC_KEY = 'gridmind.help.currentDoc'

/** 文章运行时形态（meta + 渲染产物 + 纯文本索引） */
export interface HelpArticle {
  meta: HelpArticleMeta
  source: string
  html: string
  headings: MarkdownHeading[]
  plainText: string
  searchText: string
}

export const useHelpStore = defineStore('help', () => {
  // ── State ──
  const manifest = ref<HelpArticleMeta[]>([])
  const articles = ref<Record<string, HelpArticle>>({})
  const currentId = ref<string>('')
  const query = ref<string>('')
  const loadingManifest = ref(false)
  const loadingArticleId = ref<string>('')
  const searching = ref(false)
  const errorMessage = ref<string>('')

  // ── Getters 由组件 computed 完成（保持 store 精简）──

  const sortedManifest = () =>
    [...manifest.value].sort((a, b) => a.order - b.order)

  const currentArticle = (): HelpArticle | null =>
    currentId.value ? (articles.value[currentId.value] ?? null) : null

  // ── Actions ──

  /** 加载 manifest（幂等：已加载直接返回） */
  async function loadManifest(force = false): Promise<void> {
    if (manifest.value.length && !force) return
    if (loadingManifest.value) return
    loadingManifest.value = true
    errorMessage.value = ''
    try {
      const resp = await fetch(MANIFEST_URL)
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const data = (await resp.json()) as { articles: HelpArticleMeta[] }
      manifest.value = Array.isArray(data.articles) ? data.articles : []
      // 恢复上次浏览文档（localStorage 键：gridmind.help.currentDoc）
      if (!currentId.value && manifest.value.length) {
        const saved = readStoredDoc()
        const valid = manifest.value.some((a) => a.id === saved)
        currentId.value = valid ? saved : manifest.value[0]!.id
      }
    } catch (err) {
      console.error('[help.loadManifest]', err)
      errorMessage.value = '帮助文档清单加载失败'
      ElMessage.warning('帮助文档清单加载失败，请稍后重试')
    } finally {
      loadingManifest.value = false
    }
  }

  /** 加载单篇文章（按需 fetch + 渲染 + 建索引；幂等缓存） */
  async function loadArticle(id: string): Promise<void> {
    if (articles.value[id]) {
      currentId.value = id
      persistStoredDoc(id)
      return
    }
    if (loadingArticleId.value === id) return
    // 确保 manifest 就绪（找到 meta）
    if (!manifest.value.length) await loadManifest()
    const meta = manifest.value.find((m) => m.id === id)
    if (!meta) {
      ElMessage.warning(`未找到帮助文档：${id}`)
      return
    }
    loadingArticleId.value = id
    try {
      const resp = await fetch(meta.path)
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const source = await resp.text()
      articles.value[id] = {
        meta,
        source,
        html: render(source),
        headings: extractHeadings(source),
        plainText: extractSearchText(source),
        searchText: normalizeQuery(extractSearchText(source)),
      }
      currentId.value = id
      persistStoredDoc(id)
    } catch (err) {
      console.error('[help.loadArticle]', id, err)
      ElMessage.warning(`文档「${meta.title}」加载失败`)
    } finally {
      loadingArticleId.value = ''
    }
  }

  /** 确保所有文章已加载（全文搜索前调用；7 篇小文件并行） */
  async function ensureAllLoaded(): Promise<void> {
    await loadManifest()
    const missing = manifest.value.filter((m) => !articles.value[m.id])
    if (missing.length) {
      await Promise.allSettled(missing.map((m) => loadArticle(m.id)))
    }
  }

  /**
   * 全文搜索：标题 / 章节 / 正文三域匹配。
   * - 标题命中 score 最高（×3）
   * - 章节标题命中次之（×2）
   * - 正文命中 ×1
   */
  async function search(rawQuery: string): Promise<SearchHit[]> {
    query.value = rawQuery
    const q = normalizeQuery(rawQuery)
    if (!q) return []
    searching.value = true
    try {
      await ensureAllLoaded()
      const hits: SearchHit[] = []
      for (const meta of manifest.value) {
        const article = articles.value[meta.id]
        if (!article) continue

        // 标题域
        if (normalizeQuery(meta.title).includes(q)) {
          hits.push({
            articleId: meta.id,
            type: 'title',
            text: meta.title,
            snippet: highlight(meta.title, q),
            score: 300 + meta.title.length,
          })
        }

        // 章节域（headings）
        for (const h of article.headings) {
          if (normalizeQuery(h.text).includes(q)) {
            hits.push({
              articleId: meta.id,
              type: 'heading',
              text: h.text,
              snippet: highlight(h.text, q),
              score: 200 + h.text.length,
            })
          }
        }

        // 正文域（纯文本片段，取首处命中上下文 ±30 字符）
        const bodyHit = findBodyHit(article.plainText, q)
        if (bodyHit) {
          hits.push({
            articleId: meta.id,
            type: 'body',
            text: meta.title,
            snippet: highlight(bodyHit, q),
            score: 100 + bodyHit.length,
          })
        }
      }
      hits.sort((a, b) => b.score - a.score)
      return hits.slice(0, 50)
    } finally {
      searching.value = false
    }
  }

  /** 清空搜索 */
  function clearSearch(): void {
    query.value = ''
  }

  // ── 内部工具 ──

  /** 在正文中找首处命中的上下文片段 */
  function findBodyHit(plainText: string, q: string): string | null {
    const idx = plainText.toLowerCase().indexOf(q)
    if (idx < 0) return null
    const start = Math.max(0, idx - 30)
    const end = Math.min(plainText.length, idx + q.length + 60)
    const prefix = start > 0 ? '…' : ''
    const suffix = end < plainText.length ? '…' : ''
    return prefix + plainText.slice(start, end) + suffix
  }

  /** 高亮命中片段（受信内容，输出含 <mark>） */
  function highlight(text: string, q: string): string {
    const idx = text.toLowerCase().indexOf(q)
    if (idx < 0) return text
    return (
      text.slice(0, idx) +
      '<mark>' +
      text.slice(idx, idx + q.length) +
      '</mark>' +
      text.slice(idx + q.length)
    )
  }

  function readStoredDoc(): string {
    try {
      return localStorage.getItem(CURRENT_DOC_KEY) ?? ''
    } catch {
      return ''
    }
  }

  function persistStoredDoc(id: string): void {
    try {
      localStorage.setItem(CURRENT_DOC_KEY, id)
    } catch {
      /* 隐私模式静默失败 */
    }
  }

  return {
    // state
    manifest,
    articles,
    currentId,
    query,
    loadingManifest,
    loadingArticleId,
    searching,
    errorMessage,
    // getters（函数形态，组件内转 computed）
    sortedManifest,
    currentArticle,
    // actions
    loadManifest,
    loadArticle,
    ensureAllLoaded,
    search,
    clearSearch,
  }
})
