<script setup lang="ts">
/**
 * SystemOverview · 系统总览（v1.4.0 新增）
 *
 * 聚合展示：
 * - 灰度切流状态（state / ratio / 累计切换 / 累计回滚 / 监控窗口）
 * - Prometheus 指标摘要（counters / gauges / histograms）
 * - 关键健康指标
 * - 当前 LLM 模型
 *
 * 5 秒自动刷新
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { getGrayscaleStatus, getGrayscaleHistory } from '@/api/metrics'
import { fetchMetricsSummary } from '@/api/audit'
import { fetchModels } from '@/api/models'

const loading = ref(false)
const error = ref<string | null>(null)

const grayscale = ref<any>(null)
const grayscaleHistory = ref<any[]>([])
const metrics = ref<any>({})
const modelInfo = ref<any>(null)
const lastRefresh = ref<Date>(new Date())

let timer: ReturnType<typeof setInterval> | null = null

async function load() {
  loading.value = true
  error.value = null
  try {
    const [g, gh, m, md] = await Promise.all([
      getGrayscaleStatus(),
      getGrayscaleHistory(10).catch(() => ({ entries: [] })),
      fetchMetricsSummary().catch(() => ({ enabled: false, metrics: {} })),
      fetchModels().catch(() => null),
    ])
    grayscale.value = g
    grayscaleHistory.value = gh.entries || []
    metrics.value = m.metrics || {}
    modelInfo.value = md
    lastRefresh.value = new Date()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  load()
  timer = setInterval(load, 5000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})

function formatNumber(n: number | undefined): string {
  if (n === undefined || n === null) return '-'
  if (n > 1e9) return (n / 1e9).toFixed(2) + 'B'
  if (n > 1e6) return (n / 1e6).toFixed(2) + 'M'
  if (n > 1e3) return (n / 1e3).toFixed(2) + 'K'
  return n.toString()
}

const ratioPercent = computed(() => {
  const r = grayscale.value?.ratio ?? 0
  return `${r}%`
})

const stateBadgeColor = computed(() => {
  const s = grayscale.value?.state
  if (s === 'full100') return '#10b981'
  if (s === 'gray50') return '#3b82f6'
  if (s === 'gray10') return '#8b5cf6'
  if (s === 'off') return '#6b7280'
  return '#f59e0b'
})

const metricsCounters = computed<Record<string, number>>(() => metrics.value?.counters || {})
const metricsGauges = computed<Record<string, number>>(() => metrics.value?.gauges || {})
const metricsHistograms = computed<Record<string, any>>(() => metrics.value?.histograms || {})
</script>

<template>
  <div class="sys-overview">
    <header class="sys-overview__header">
      <div>
        <h1 class="sys-overview__title">系统总览</h1>
        <p class="sys-overview__subtitle">
          实时聚合视图 · 5 秒自动刷新 · 上次更新 {{ lastRefresh.toLocaleTimeString('zh-CN') }}
        </p>
      </div>
      <button class="sys-overview__refresh" :disabled="loading" @click="load">
        {{ loading ? '刷新中…' : '🔄 刷新' }}
      </button>
    </header>

    <div v-if="error" class="sys-overview__error">⚠️ {{ error }}</div>

    <!-- 行 1: 灰度 + 模型 -->
    <section class="sys-overview__row">
      <div class="sys-overview__card sys-overview__card--accent" data-tour="system-grayscale">
        <h2 class="sys-overview__card-title">灰度切流</h2>
        <div class="sys-overview__card-body">
          <div class="sys-overview__big-metric">
            <span class="sys-overview__big-value" :style="{ color: stateBadgeColor }">
              {{ grayscale?.state || 'off' }}
            </span>
            <span class="sys-overview__big-label">当前状态</span>
          </div>
          <div class="sys-overview__mini-grid">
            <div class="sys-overview__mini">
              <div class="sys-overview__mini-value">{{ ratioPercent }}</div>
              <div class="sys-overview__mini-label">Neo4j 路由</div>
            </div>
            <div class="sys-overview__mini">
              <div class="sys-overview__mini-value">{{ grayscale?.rollback_count || 0 }}</div>
              <div class="sys-overview__mini-label">累计回滚</div>
            </div>
            <div class="sys-overview__mini">
              <div class="sys-overview__mini-value">{{ grayscaleHistory.length }}</div>
              <div class="sys-overview__mini-label">最近切换</div>
            </div>
            <div class="sys-overview__mini">
              <div class="sys-overview__mini-value">
                {{ grayscale?.neo4j_enabled ? '✓ 启用' : '✕ 关闭' }}
              </div>
              <div class="sys-overview__mini-label">Neo4j</div>
            </div>
          </div>
        </div>
      </div>

      <div class="sys-overview__card" data-tour="system-model">
        <h2 class="sys-overview__card-title">LLM 模型</h2>
        <div class="sys-overview__card-body">
          <div v-if="modelInfo" class="sys-overview__big-metric">
            <span class="sys-overview__big-value">
              {{ modelInfo.available?.find((m: any) => m.id === modelInfo.current)?.label || modelInfo.current }}
            </span>
            <span class="sys-overview__big-label">当前模型</span>
          </div>
          <div v-else class="sys-overview__big-metric">
            <span class="sys-overview__big-value">-</span>
            <span class="sys-overview__big-label">加载中</span>
          </div>
          <div class="sys-overview__mini-grid">
            <div class="sys-overview__mini">
              <div class="sys-overview__mini-value">{{ modelInfo?.available?.length || 0 }}</div>
              <div class="sys-overview__mini-label">可选模型</div>
            </div>
            <div class="sys-overview__mini">
              <div class="sys-overview__mini-value">{{ modelInfo?.default || '-' }}</div>
              <div class="sys-overview__mini-label">默认</div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 行 2: Prometheus 监控 -->
    <section class="sys-overview__card sys-overview__card--wide" data-tour="system-metrics">
      <h2 class="sys-overview__card-title">Prometheus 指标（M3c 可观测性）</h2>
      <div class="sys-overview__card-body">
        <div v-if="!metrics || Object.keys(metrics).length === 0" class="sys-overview__empty">
          暂无指标数据（首次调用后会出现）
        </div>
        <div v-else class="sys-overview__metrics-grid">
          <div v-if="Object.keys(metricsCounters).length > 0" class="sys-overview__metric-block">
            <h3 class="sys-overview__metric-title">Counter（累计）</h3>
            <div v-for="(v, k) in metricsCounters" :key="k" class="sys-overview__metric-row">
              <code class="sys-overview__metric-name">{{ k }}</code>
              <span class="sys-overview__metric-val">{{ formatNumber(v as number) }}</span>
            </div>
          </div>
          <div v-if="Object.keys(metricsGauges).length > 0" class="sys-overview__metric-block">
            <h3 class="sys-overview__metric-title">Gauge（瞬时）</h3>
            <div v-for="(v, k) in metricsGauges" :key="k" class="sys-overview__metric-row">
              <code class="sys-overview__metric-name">{{ k }}</code>
              <span class="sys-overview__metric-val">{{ formatNumber(v as number) }}</span>
            </div>
          </div>
          <div v-if="Object.keys(metricsHistograms).length > 0" class="sys-overview__metric-block">
            <h3 class="sys-overview__metric-title">Histogram（分布）</h3>
            <div v-for="(v, k) in metricsHistograms" :key="k" class="sys-overview__metric-row">
              <code class="sys-overview__metric-name">{{ k }}</code>
              <span class="sys-overview__metric-val">
                count={{ formatNumber(v.count) }} · p95={{ v.p95?.toFixed(2) }}ms
              </span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 行 3: 最近切换历史 -->
    <section v-if="grayscaleHistory.length > 0" class="sys-overview__card sys-overview__card--wide">
      <h2 class="sys-overview__card-title">最近切换记录</h2>
      <div class="sys-overview__card-body">
        <ul class="sys-overview__history">
          <li v-for="(h, i) in grayscaleHistory.slice(0, 5)" :key="i" class="sys-overview__history-item">
            <span class="sys-overview__history-state">{{ h.to_state }}</span>
            <span class="sys-overview__history-detail">
              ratio {{ h.from_ratio }} → {{ h.to_ratio }} · {{ h.actor }}
            </span>
            <span class="sys-overview__history-time">
              {{ new Date((h.ts || 0) * 1000).toLocaleTimeString('zh-CN') }}
            </span>
          </li>
        </ul>
      </div>
    </section>
  </div>
</template>

<style scoped lang="scss">
.sys-overview {
  max-width: 1200px;
  margin: 0 auto;
  padding: 32px 24px 80px;
  color: var(--gm-text-primary, #e5e7eb);
}

.sys-overview__header {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  margin-bottom: 24px;
}

.sys-overview__title {
  font-size: 28px;
  font-weight: 700;
  margin: 0;
  background: linear-gradient(90deg, #615ced, #1c64f2);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}

.sys-overview__subtitle {
  font-size: 13px;
  color: var(--gm-text-secondary, #9ca3af);
  margin: 6px 0 0;
}

.sys-overview__refresh {
  padding: 8px 16px;
  border-radius: 8px;
  border: 1px solid var(--gm-border, rgba(255, 255, 255, 0.12));
  background: var(--gm-bg-elev, rgba(255, 255, 255, 0.05));
  color: inherit;
  cursor: pointer;
  font-size: 13px;
}

.sys-overview__refresh:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.sys-overview__error {
  padding: 12px 16px;
  background: rgba(239, 68, 68, 0.1);
  border: 1px solid rgba(239, 68, 68, 0.3);
  border-radius: 8px;
  color: #fca5a5;
  font-size: 13px;
  margin-bottom: 16px;
}

.sys-overview__row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-bottom: 16px;
}

.sys-overview__card {
  background: var(--gm-bg-elev, rgba(255, 255, 255, 0.05));
  border: 1px solid var(--gm-border, rgba(255, 255, 255, 0.08));
  border-radius: 14px;
  padding: 20px 24px;
  transition: border-color 0.15s ease;
}

.sys-overview__card:hover {
  border-color: var(--gm-accent, #615ced);
}

.sys-overview__card--accent {
  background: linear-gradient(135deg, rgba(97, 92, 237, 0.08), rgba(28, 100, 242, 0.04));
}

.sys-overview__card--wide {
  grid-column: 1 / -1;
}

.sys-overview__card-title {
  font-size: 13px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--gm-text-secondary, #9ca3af);
  margin: 0 0 16px;
}

.sys-overview__card-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.sys-overview__big-metric {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.sys-overview__big-value {
  font-size: 32px;
  font-weight: 700;
  font-family: var(--font-mono, monospace);
}

.sys-overview__big-label {
  font-size: 12px;
  color: var(--gm-text-secondary, #9ca3af);
}

.sys-overview__mini-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(80px, 1fr));
  gap: 8px;
}

.sys-overview__mini {
  padding: 10px 12px;
  background: rgba(255, 255, 255, 0.04);
  border-radius: 8px;
  text-align: center;
}

.sys-overview__mini-value {
  font-size: 18px;
  font-weight: 600;
  font-family: var(--font-mono, monospace);
}

.sys-overview__mini-label {
  font-size: 11px;
  color: var(--gm-text-secondary, #9ca3af);
  margin-top: 2px;
}

.sys-overview__empty {
  padding: 24px;
  text-align: center;
  color: var(--gm-text-secondary, #9ca3af);
  font-size: 13px;
}

.sys-overview__metrics-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 20px;
}

.sys-overview__metric-block {
  background: rgba(255, 255, 255, 0.03);
  border-radius: 8px;
  padding: 12px 16px;
}

.sys-overview__metric-title {
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--gm-text-secondary, #9ca3af);
  margin: 0 0 10px;
}

.sys-overview__metric-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 4px 0;
  border-bottom: 1px dashed rgba(255, 255, 255, 0.06);
  font-size: 12px;
}

.sys-overview__metric-row:last-child {
  border-bottom: none;
}

.sys-overview__metric-name {
  font-family: var(--font-mono, monospace);
  color: var(--gm-accent, #615ced);
  font-size: 11px;
}

.sys-overview__metric-val {
  font-family: var(--font-mono, monospace);
  color: var(--gm-text-primary, #e5e7eb);
}

.sys-overview__history {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sys-overview__history-item {
  display: grid;
  grid-template-columns: 100px 1fr auto;
  gap: 12px;
  align-items: center;
  padding: 8px 12px;
  background: rgba(255, 255, 255, 0.03);
  border-radius: 6px;
  font-size: 13px;
}

.sys-overview__history-state {
  font-weight: 600;
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  color: var(--gm-accent, #615ced);
}

.sys-overview__history-detail {
  color: var(--gm-text-secondary, #9ca3af);
  font-size: 12px;
}

.sys-overview__history-time {
  color: var(--gm-text-secondary, #9ca3af);
  font-family: var(--font-mono, monospace);
  font-size: 11px;
}

@media (max-width: 768px) {
  .sys-overview__row {
    grid-template-columns: 1fr;
  }
}
</style>