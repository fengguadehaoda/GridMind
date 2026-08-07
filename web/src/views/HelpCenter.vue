<script setup lang="ts">
/**
 * HelpCenter.vue · 帮助中心（v1.6.0 P1-2 + V1.7 KB Upload）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构决策（p1-iteration-architecture §1 P1-2 + §4.3 时序图）：
 *   - 左侧目录（manifest 白名单）+ 右侧 Markdown 渲染 + 全文搜索
 *   - 搜索命中标题 / 章节 / 正文三域，分组展示 + 高亮
 *   - Markdown 由 utils/markdown.ts 渲染（受信内置文档，v-html 安全）
 *   - mermaid 围栏 → 占位容器
 *
 * V1.7 KB Upload（架构 kb-upload-architecture-2026-08-06 §5 T04）：
 *   - 主区顶部新增 Tab：「帮助文档 / 知识库管理」
 *   - ``?tab=knowledge`` 直达知识库管理（App.vue 快捷入口透传）
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Search, Document, ArrowLeft, Loading, Collection } from '@element-plus/icons-vue'
import { useHelpStore } from '@/stores/help'
import { useDisplay } from '@/composables/useDisplay'
import TechBackground from '@/components/background/TechBackground.vue'
import KnowledgeUpload from '@/components/controls/KnowledgeUpload.vue'
import type { SearchHit } from '@/types/theme'

const help = useHelpStore()
const route = useRoute()
const router = useRouter()
const { bgIntensity } = useDisplay()

/** 主区 Tab：docs=帮助文档 / kb=知识库管理（架构 T04 最终方案） */
const activeTab = ref<'docs' | 'kb'>('docs')

const searchInput = ref('')
const searchResults = ref<SearchHit[]>([])
const searchActive = ref(false)

const articles = computed(() => help.sortedManifest())
const current = computed(() => help.currentArticle())

/** 搜索命中按文章分组 */
const groupedHits = computed(() => {
  const map = new Map<string, SearchHit[]>()
  for (const hit of searchResults.value) {
    const list = map.get(hit.articleId) ?? []
    list.push(hit)
    map.set(hit.articleId, list)
  }
  return Array.from(map.entries()).map(([articleId, hits]) => ({
    articleId,
    meta: articles.value.find((a) => a.id === articleId),
    hits,
  }))
})

/** 目录：当前文章 headings（h2/h3 优先） */
const toc = computed(() => {
  const hs = current.value?.headings ?? []
  return hs.filter((h) => h.level <= 3)
})

onMounted(() => {
  // V1.7 KB Upload：支持 /help?tab=knowledge 直达知识库管理
  if (route.query.tab === 'knowledge') {
    activeTab.value = 'kb'
  }
  void help.loadManifest()
  // 支持 /help?doc=xxx 直达
  const docId = route.query.doc
  if (typeof docId === 'string' && docId) {
    void help.loadArticle(docId)
  } else if (!help.currentId) {
    void help.loadManifest().then(() => {
      if (help.manifest.length && !help.currentId) {
        void help.loadArticle(help.manifest[0]!.id)
      }
    })
  }
})

/** 监听路由 ?tab= 变化（App.vue 快捷入口 / 浏览器前进后退） */
watch(
  () => route.query.tab,
  (tab) => {
    activeTab.value = tab === 'knowledge' ? 'kb' : 'docs'
  },
)

/** 切换主区 Tab，同步 URL query（docs 时移除 tab 参数） */
function switchTab(tab: 'docs' | 'kb'): void {
  activeTab.value = tab
  const query = { ...route.query }
  if (tab === 'kb') {
    query.tab = 'knowledge'
  } else {
    delete query.tab
  }
  void router.replace({ query })
}

async function selectArticle(id: string): Promise<void> {
  searchActive.value = false
  searchInput.value = ''
  searchResults.value = []
  await help.loadArticle(id)
  void router.replace({ query: { doc: id } })
}

function onSearchInput(): void {
  const q = searchInput.value.trim()
  if (!q) {
    searchActive.value = false
    searchResults.value = []
    return
  }
  searchActive.value = true
  void help.search(q).then((hits) => {
    searchResults.value = hits
  })
}

function jumpToHeading(id: string): void {
  const el = document.getElementById(id)
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

function openArticleFromHit(hit: SearchHit): void {
  searchActive.value = false
  searchInput.value = ''
  searchResults.value = []
  void help.loadArticle(hit.articleId).then(() => {
    if (hit.type === 'heading') {
      nextTickJump(hit.text)
    }
  })
}

function nextTickJump(headingText: string): void {
  requestAnimationFrame(() => {
    const heading = current.value?.headings.find((h) => h.text === headingText)
    if (heading) {
      const el = document.getElementById(heading.id)
      el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  })
}

watch(
  () => help.currentId,
  () => {
    // 文档切换时清空搜索态
    searchActive.value = false
    searchResults.value = []
  },
)
</script>

<template>
  <div class="help-center">
    <TechBackground :intensity="bgIntensity" :show-glow="false" />

    <div class="help-center__inner">
      <!-- 侧栏：目录 -->
      <aside class="help-center__sidebar">
        <div class="help-center__sidebar-head">
          <span class="help-center__brand">
            <el-icon><Document /></el-icon>
            帮助中心
          </span>
          <router-link to="/" class="help-center__back">
            <el-icon><ArrowLeft /></el-icon>
            <span>返回</span>
          </router-link>
        </div>

        <div v-loading="help.loadingManifest" class="help-center__nav">
          <button
            v-for="article in articles"
            :key="article.id"
            type="button"
            class="help-center__nav-item"
            :class="{ 'is-active': help.currentId === article.id }"
            @click="selectArticle(article.id)"
          >
            <span class="help-center__nav-title">{{ article.title }}</span>
            <span class="help-center__nav-summary">{{ article.summary }}</span>
          </button>
        </div>
      </aside>

      <!-- 主区 -->
      <main class="help-center__main">
        <!-- V1.7 KB Upload：主区顶部 Tab（帮助文档 / 知识库管理） -->
        <div class="help-center__tabs" data-test="help-tabs">
          <button
            type="button"
            class="help-center__tab"
            :class="{ 'is-active': activeTab === 'docs' }"
            data-test="help-tab-docs"
            @click="switchTab('docs')"
          >
            <el-icon><Document /></el-icon>
            帮助文档
          </button>
          <button
            type="button"
            class="help-center__tab"
            :class="{ 'is-active': activeTab === 'kb' }"
            data-test="help-tab-kb"
            @click="switchTab('kb')"
          >
            <el-icon><Collection /></el-icon>
            知识库管理
          </button>
        </div>

        <template v-if="activeTab === 'docs'">
        <!-- 搜索 -->
        <div class="help-center__search">
          <el-icon class="help-center__search-icon"><Search /></el-icon>
          <input
            v-model="searchInput"
            type="text"
            class="help-center__search-input"
            placeholder="全文搜索，例如 灰度切流 / checkpoint / token…"
            autocomplete="off"
            spellcheck="false"
            data-test="help-search-input"
            @input="onSearchInput"
          />
          <span v-if="help.searching" class="help-center__search-loading">
            <el-icon class="is-loading"><Loading /></el-icon>
          </span>
        </div>

        <!-- 搜索结果 -->
        <div v-if="searchActive" class="help-center__results" data-test="help-search-results">
          <p v-if="searchResults.length === 0 && !help.searching" class="help-center__results-empty">
            未找到「{{ searchInput }}」相关内容，试试其他关键词
          </p>
          <section v-for="group in groupedHits" :key="group.articleId" class="help-center__result-group">
            <h3 class="help-center__result-group-title">{{ group.meta?.title ?? group.articleId }}</h3>
            <button
              v-for="(hit, idx) in group.hits"
              :key="idx"
              type="button"
              class="help-center__result-hit"
              @click="openArticleFromHit(hit)"
            >
              <span class="help-center__result-tag" :class="`is-${hit.type}`">
                {{ hit.type === 'title' ? '标题' : hit.type === 'heading' ? '章节' : '正文' }}
              </span>
              <span class="help-center__result-text" v-html="hit.snippet"></span>
            </button>
          </section>
        </div>

        <!-- 文章内容 -->
        <article v-else class="help-center__article" data-test="help-article">
          <div v-if="!current" class="help-center__article-empty">
            <el-icon><Document /></el-icon>
            <p>选择左侧文档开始阅读</p>
          </div>

          <template v-else>
            <header class="help-center__article-head">
              <h1 class="help-center__article-title">{{ current.meta.title }}</h1>
              <p class="help-center__article-summary">{{ current.meta.summary }}</p>
            </header>

            <!-- 文内目录 -->
            <nav v-if="toc.length > 1" class="help-center__toc">
              <span class="help-center__toc-label">目录</span>
              <button
                v-for="h in toc"
                :key="h.id"
                type="button"
                class="help-center__toc-item"
                :class="`is-h${h.level}`"
                @click="jumpToHeading(h.id)"
              >
                {{ h.text }}
              </button>
            </nav>

            <!-- Markdown 渲染（受信内置文档） -->
            <div class="help-center__md" v-html="current.html"></div>

            <footer class="help-center__article-foot">
              <span>文档 ID：{{ current.meta.id }}</span>
              <span>随前端发版更新 · 如需修改请走 docs/help-src/ 流程</span>
            </footer>
          </template>
        </article>
        </template>

        <!-- V1.7 KB Upload：知识库管理（拖拽上传 + 进度 + 列表删除） -->
        <KnowledgeUpload v-else />
      </main>
    </div>
  </div>
</template>

<style scoped lang="scss">
.help-center {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 100vh;
}

.help-center__inner {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  gap: 0;
  max-width: 1600px;
  margin: 0 auto;
  min-height: calc(100vh - 60px);
}

/* ── 侧栏 ── */
.help-center__sidebar {
  border-right: 1px solid var(--border-muted);
  background: var(--bg-elevated);
  padding: var(--space-5) var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  transition: var(--theme-transition);
}

.help-center__sidebar-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.help-center__brand {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-family: var(--font-cn);
  font-size: var(--fs-md);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
  letter-spacing: 0.08em;
}

.help-center__brand .el-icon {
  color: var(--brand-primary);
}

.help-center__back {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--fs-xs);
  color: var(--text-muted);
  text-decoration: none;
  transition: color var(--dur-fast) var(--ease-out-quint);
}

.help-center__back:hover {
  color: var(--brand-primary);
}

.help-center__nav {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-height: 200px;
}

.help-center__nav-item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  padding: var(--space-3);
  border: 1px solid var(--border-muted);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  text-align: left;
  cursor: pointer;
  transition: all var(--dur-fast) var(--ease-out-quint);
}

.help-center__nav-item:hover {
  border-color: var(--brand-primary);
  background: var(--brand-primary-soft);
}

.help-center__nav-item.is-active {
  border-color: var(--brand-primary);
  background: var(--brand-primary-soft);
  box-shadow: var(--glow-primary-soft);
}

.help-center__nav-title {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
}

.help-center__nav-summary {
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  color: var(--text-muted);
  line-height: var(--lh-tight);
}

/* ── 主区 ── */
.help-center__main {
  padding: var(--space-6);
  overflow-y: auto;
  max-height: calc(100vh - 60px);
}

/* ── V1.7 KB Upload：主区 Tab（帮助文档 / 知识库管理）── */
.help-center__tabs {
  display: inline-flex;
  gap: var(--space-2);
  margin-bottom: var(--space-5);
  padding: 4px;
  border: 1px solid var(--border-muted);
  border-radius: var(--radius-md);
  background: var(--bg-elevated);
}

.help-center__tab {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-4);
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all var(--dur-fast) var(--ease-out-quint);
}

.help-center__tab:hover {
  color: var(--brand-primary);
}

.help-center__tab.is-active {
  background: var(--brand-primary);
  color: var(--text-on-brand, #fff);
  box-shadow: var(--glow-primary-soft);
}

.help-center__search {
  position: relative;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-5);
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  background: var(--bg-input);
  transition: border-color var(--dur-fast) var(--ease-out-quint), box-shadow var(--dur-fast) var(--ease-out-quint);
}

.help-center__search:focus-within {
  border-color: var(--brand-primary);
  box-shadow: var(--glow-primary-soft);
}

.help-center__search-icon {
  color: var(--text-muted);
}

.help-center__search-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  color: var(--text-primary);
  font-family: var(--font-cn);
  font-size: var(--fs-md);
}

.help-center__search-input::placeholder {
  color: var(--text-muted);
}

.help-center__search-loading {
  color: var(--text-muted);
}

/* ── 搜索结果 ── */
.help-center__results {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.help-center__results-empty {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  color: var(--text-muted);
  padding: var(--space-8);
  text-align: center;
  border: 1px dashed var(--border-default);
  border-radius: var(--radius-md);
}

.help-center__result-group-title {
  margin: 0 0 var(--space-3);
  font-family: var(--font-cn);
  font-size: var(--fs-md);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
}

.help-center__result-hit {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  padding: var(--space-3);
  margin-bottom: var(--space-2);
  border: 1px solid var(--border-muted);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  text-align: left;
  cursor: pointer;
  transition: all var(--dur-fast) var(--ease-out-quint);
}

.help-center__result-hit:hover {
  border-color: var(--brand-primary);
  background: var(--brand-primary-soft);
}

.help-center__result-tag {
  flex-shrink: 0;
  padding: 2px var(--space-2);
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  border: 1px solid var(--border-muted);
  color: var(--text-secondary);
}

.help-center__result-tag.is-title { color: var(--brand-primary); border-color: var(--brand-primary); }
.help-center__result-tag.is-heading { color: var(--status-warning); border-color: var(--status-warning); }
.help-center__result-tag.is-body { color: var(--status-success); border-color: var(--status-success); }

.help-center__result-text {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  color: var(--text-primary);
  line-height: var(--lh-normal);
}

.help-center__result-text :deep(mark) {
  background: var(--brand-primary-soft);
  color: var(--brand-primary);
  border-radius: 2px;
  padding: 0 1px;
}

/* ── 文章 ── */
.help-center__article-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  padding: var(--space-12) 0;
  color: var(--text-muted);
}

.help-center__article-head {
  margin-bottom: var(--space-5);
  padding-bottom: var(--space-4);
  border-bottom: 1px solid var(--border-muted);
}

.help-center__article-title {
  margin: 0 0 var(--space-2);
  font-family: var(--font-cn);
  font-size: var(--fs-2xl);
  font-weight: var(--fw-bold);
  color: var(--text-primary);
  letter-spacing: 0.04em;
}

.help-center__article-summary {
  margin: 0;
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  color: var(--text-secondary);
}

.help-center__toc {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--space-3);
  margin-bottom: var(--space-5);
  border: 1px dashed var(--border-default);
  border-radius: var(--radius-md);
  background: var(--bg-card);
}

.help-center__toc-label {
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
  color: var(--text-muted);
  letter-spacing: 0.12em;
  margin-bottom: var(--space-1);
}

.help-center__toc-item {
  padding: 2px 0;
  border: none;
  background: transparent;
  text-align: left;
  cursor: pointer;
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  color: var(--text-secondary);
  transition: color var(--dur-fast) var(--ease-out-quint);
}

.help-center__toc-item:hover {
  color: var(--brand-primary);
}

.help-center__toc-item.is-h2 { padding-left: 0; }
.help-center__toc-item.is-h3 { padding-left: var(--space-4); }

/* ── Markdown 渲染样式 ── */
.help-center__md {
  font-family: var(--font-cn);
  color: var(--text-primary);
  line-height: var(--lh-loose);
}

.help-center__md :deep(h1),
.help-center__md :deep(h2),
.help-center__md :deep(h3),
.help-center__md :deep(h4),
.help-center__md :deep(h5),
.help-center__md :deep(h6) {
  font-family: var(--font-cn);
  color: var(--text-primary);
  margin: var(--space-6) 0 var(--space-3);
  line-height: var(--lh-tight);
  scroll-margin-top: 24px;
}

.help-center__md :deep(h1) { font-size: var(--fs-2xl); }
.help-center__md :deep(h2) { font-size: var(--fs-xl); border-bottom: 1px solid var(--border-muted); padding-bottom: var(--space-2); }
.help-center__md :deep(h3) { font-size: var(--fs-lg); }
.help-center__md :deep(h4) { font-size: var(--fs-md); }

.help-center__md :deep(p) {
  margin: var(--space-3) 0;
}

.help-center__md :deep(strong) {
  color: var(--text-primary);
  font-weight: var(--fw-semibold);
}

.help-center__md :deep(a) {
  color: var(--brand-primary);
  text-decoration: none;
}

.help-center__md :deep(a:hover) {
  text-decoration: underline;
}

.help-center__md :deep(code) {
  font-family: var(--font-mono);
  font-size: 0.92em;
  background: var(--bg-input);
  border: 1px solid var(--border-muted);
  border-radius: 4px;
  padding: 1px 5px;
  color: var(--text-mono);
}

.help-center__md :deep(pre) {
  background: var(--bg-void);
  border: 1px solid var(--code-border);
  border-radius: var(--radius-md);
  padding: var(--space-4);
  overflow-x: auto;
  margin: var(--space-4) 0;
}

.help-center__md :deep(pre code) {
  background: transparent;
  border: none;
  padding: 0;
  color: var(--code-text);
}

.help-center__md :deep(blockquote) {
  margin: var(--space-4) 0;
  padding: var(--space-3) var(--space-4);
  border-left: 3px solid var(--brand-primary);
  background: var(--brand-primary-fade);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  color: var(--text-secondary);
}

.help-center__md :deep(hr) {
  border: none;
  border-top: 1px solid var(--border-muted);
  margin: var(--space-6) 0;
}

.help-center__md :deep(ul),
.help-center__md :deep(ol) {
  padding-left: var(--space-6);
  margin: var(--space-3) 0;
}

.help-center__md :deep(li) {
  margin: var(--space-1) 0;
}

.help-center__md :deep(.gm-markdown-table-wrap) {
  overflow-x: auto;
  margin: var(--space-4) 0;
}

.help-center__md :deep(table) {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-sm);
}

.help-center__md :deep(th),
.help-center__md :deep(td) {
  border: 1px solid var(--border-default);
  padding: var(--space-2) var(--space-3);
  text-align: left;
}

.help-center__md :deep(th) {
  background: var(--bg-input);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
}

.help-center__md :deep(td) {
  color: var(--text-secondary);
}

/* mermaid 占位容器 */
.help-center__md :deep(.gm-mermaid-placeholder) {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin: var(--space-4) 0;
  padding: var(--space-6);
  border: 1px dashed var(--border-strong);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  color: var(--text-muted);
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
}

.help-center__md :deep(.gm-mermaid-placeholder__icon) {
  font-size: 22px;
  color: var(--brand-primary);
}

.help-center__article-foot {
  display: flex;
  justify-content: space-between;
  gap: var(--space-4);
  margin-top: var(--space-8);
  padding-top: var(--space-4);
  border-top: 1px solid var(--border-muted);
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}

/* ── 响应式 ── */
@media (max-width: 1024px) {
  .help-center__inner {
    grid-template-columns: 1fr;
  }
  .help-center__sidebar {
    border-right: none;
    border-bottom: 1px solid var(--border-muted);
    max-height: 220px;
    overflow-y: auto;
  }
  .help-center__main {
    max-height: none;
    padding: var(--space-4);
  }
}
</style>
