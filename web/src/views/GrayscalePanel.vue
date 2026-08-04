<template>
  <div class="grayscale-panel">
    <TechBackground intensity="low" :show-glow="true" />

    <div class="page-content">
      <!-- 页面标题 -->
      <div class="page-head">
        <div>
          <h2 class="page-title">灰度可视化面板</h2>
          <p class="page-sub">
            Neo4j 双 backend 灰度切流 · 监控窗口 · 切换历史（自动刷新 {{ store.POLLING_INTERVAL / 1000 }}s）
          </p>
        </div>
        <div class="page-head-right">
          <span class="refresh-time">
            <el-icon><Timer /></el-icon>
            最后刷新：{{ store.lastUpdated || '—' }}
          </span>
          <el-button size="small" :loading="store.loading" @click="store.fetchStatus()">
            立即刷新
          </el-button>
        </div>
      </div>

      <!-- 顶部状态卡：4 个统计卡 -->
      <div class="stats-row">
        <StatHexagon
          label="当前切流比例"
          :value="`${store.ratio}%`"
          :tone="stateTone"
          :loading="!store.status"
        />
        <StatHexagon
          label="状态机"
          :value="store.state"
          :tone="stateTone"
          :loading="!store.status"
        />
        <StatHexagon
          label="5min 错误率"
          :value="formatPct(store.monitor?.error_rate)"
          :tone="errorRateTone"
          :loading="!store.monitor"
        />
        <StatHexagon
          label="回滚次数"
          :value="store.rollbackCount"
          tone="warning"
          :loading="!store.status"
        />
      </div>

      <!-- 操作区：admin token + 切流 + 回滚 -->
      <el-card class="op-card" shadow="hover">
        <template #header>
          <div class="card-head">
            <span class="card-title">手动切流</span>
            <el-tag v-if="store.state" :type="stateTagType" effect="dark">
              {{ store.state }}
            </el-tag>
          </div>
        </template>

        <div class="op-row">
          <el-input
            v-model="adminToken"
            type="password"
            placeholder="X-Admin-Token（环境变量 ADMIN_TOKEN）"
            show-password
            clearable
            class="op-input"
            data-test="grayscale-admin-token"
          />
          <el-input
            v-model.number="targetRatio"
            type="number"
            placeholder="目标比例"
            :min="0"
            :max="100"
            :step="10"
            class="op-input op-input-narrow"
          />
          <el-button
            type="primary"
            :disabled="!canSetRatio"
            :loading="opLoading"
            data-test="grayscale-set-btn"
            @click="handleSetRatio"
          >
            切流到 {{ targetRatio }}%
          </el-button>
          <el-button
            type="danger"
            plain
            :disabled="!adminToken"
            :loading="rollbackLoading"
            data-test="grayscale-rollback-btn"
            @click="handleManualRollback"
          >
            手动回滚
          </el-button>
        </div>

        <!-- 操作反馈 -->
        <el-alert
          v-if="store.operationMsg"
          :title="store.operationMsg"
          :type="store.operationOk === false ? 'error' : 'success'"
          show-icon
          :closable="false"
          class="op-alert"
        />
      </el-card>

      <!-- 监控窗口 + 历史 图表 -->
      <div class="chart-row">
        <!-- 监控窗口 -->
        <el-card class="chart-card" shadow="hover">
          <template #header>
            <span class="card-title">监控窗口（{{ store.monitor?.window_s ?? 300 }}s 滚动）</span>
          </template>
          <div v-if="store.monitor" class="monitor-grid">
            <div class="monitor-item">
              <span class="monitor-label">样本数</span>
              <span class="monitor-value">{{ store.monitor.samples }}</span>
            </div>
            <div class="monitor-item">
              <span class="monitor-label">错误率</span>
              <span class="monitor-value" :class="{ danger: store.monitor.error_rate > (store.monitor.thresholds.error_rate ?? 0.01) }">
                {{ formatPct(store.monitor.error_rate) }}
              </span>
            </div>
            <div class="monitor-item">
              <span class="monitor-label">P95 延迟</span>
              <span class="monitor-value" :class="{ danger: store.monitor.p95_ms > (store.monitor.thresholds.p95_ms ?? 200) }">
                {{ store.monitor.p95_ms.toFixed(1) }} ms
              </span>
            </div>
            <div class="monitor-item">
              <span class="monitor-label">Neo4j 连续失败</span>
              <span class="monitor-value" :class="{ danger: store.monitor.neo4j_consecutive_failures >= (store.monitor.thresholds.neo4j_failures ?? 3) }">
                {{ store.monitor.neo4j_consecutive_failures }}
              </span>
            </div>
          </div>
          <el-empty v-else description="等待监控数据..." />
        </el-card>

        <!-- 切换历史 -->
        <el-card class="chart-card" shadow="hover">
          <template #header>
            <span class="card-title">切换历史（{{ store.history.length }} 条）</span>
          </template>
          <div v-if="store.history.length === 0">
            <el-empty description="暂无切换记录" />
          </div>
          <ul v-else class="history-list">
            <li
              v-for="(entry, idx) in store.history.slice().reverse().slice(0, 10)"
              :key="idx"
              class="history-item"
              :class="{ 'is-rollback': entry.reason && entry.reason !== 'manual_set' }"
            >
              <span class="history-time">{{ formatTime(entry.ts) }}</span>
              <span class="history-actor">{{ entry.actor }}</span>
              <span class="history-arrow">
                <span class="from">{{ entry.from_ratio }}%</span>
                <el-icon><Right /></el-icon>
                <span class="to">{{ entry.to_ratio }}%</span>
              </span>
              <el-tag size="small" :type="reasonTagType(entry.reason)">
                {{ entry.reason }}
              </el-tag>
            </li>
          </ul>
        </el-card>
      </div>

      <!-- Prometheus 摘要 -->
      <el-card class="summary-card" shadow="hover">
        <template #header>
          <div class="card-head">
            <span class="card-title">Prometheus 指标摘要</span>
            <el-button size="small" link @click="copyPromSummary">复制文本</el-button>
          </div>
        </template>
        <pre v-if="store.metricsSummary" class="summary-pre">{{ formatSummary(store.metricsSummary) }}</pre>
        <el-empty v-else description="等待 /metrics/summary 响应..." />
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Right, Timer } from '@element-plus/icons-vue'
import TechBackground from '../components/background/TechBackground.vue'
import StatHexagon from '../components/controls/StatHexagon.vue'
import { useMetricsStore } from '../stores/metrics'

const store = useMetricsStore()

// ── 表单 state ──────────────────────────────────
const adminToken = ref('')
const targetRatio = ref<number>(50)
const opLoading = ref(false)
const rollbackLoading = ref(false)

const canSetRatio = computed(
  () => !!adminToken.value && targetRatio.value !== null && [0, 10, 50, 100].includes(targetRatio.value),
)

// ── 映射颜色 / tag type ──────────────────────────────────
const stateTone = computed<'info' | 'success' | 'warning' | 'danger'>(() => {
  switch (store.state) {
    case 'off':
      return 'info'
    case 'rollback':
      return 'danger'
    case 'gray10':
    case 'gray50':
    case 'full100':
      return 'warning'
    case 'stable':
    case 'monitoring_24h':
      return 'success'
    default:
      return 'info'
  }
})

const stateTagType = computed<'info' | 'success' | 'warning' | 'danger'>(() => stateTone.value)

const errorRateTone = computed(() => {
  const er = store.monitor?.error_rate ?? 0
  const th = store.monitor?.thresholds.error_rate ?? 0.01
  if (er > th) return 'danger'
  if (er > th * 0.5) return 'warning'
  return 'success'
})

function reasonTagType(reason: string | undefined): 'success' | 'warning' | 'danger' | 'info' {
  if (!reason) return 'info'
  if (reason === 'manual_set') return 'success'
  if (reason.startsWith('auto_')) return 'danger'
  return 'warning'
}

function formatPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return `${(v * 100).toFixed(2)}%`
}

function formatTime(ts: number | undefined): string {
  if (!ts) return '—'
  try {
    return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
  } catch {
    return String(ts)
  }
}

function formatSummary(s: unknown): string {
  try {
    return JSON.stringify(s, null, 2)
  } catch {
    return String(s)
  }
}

async function copyPromSummary() {
  if (!store.metricsSummary) return
  const text = formatSummary(store.metricsSummary)
  try {
    if (navigator?.clipboard) {
      await navigator.clipboard.writeText(text)
      ElMessage.success('Prometheus 摘要已复制')
    } else {
      ElMessage.warning('当前浏览器不支持剪贴板 API')
    }
  } catch {
    ElMessage.warning('复制失败')
  }
}

// ── 操作 handlers ──────────────────────────────────
async function handleSetRatio() {
  if (!adminToken.value) {
    ElMessage.warning('请输入 X-Admin-Token')
    return
  }
  opLoading.value = true
  try {
    await store.setRatio(targetRatio.value, 'panel', adminToken.value)
  } catch {
    /* store.operationMsg 已记录 */
  } finally {
    opLoading.value = false
  }
}

async function handleManualRollback() {
  if (!adminToken.value) {
    ElMessage.warning('请输入 X-Admin-Token')
    return
  }
  rollbackLoading.value = true
  try {
    await store.manualRollback('manual', 'panel', adminToken.value)
  } catch {
    /* store.operationMsg 已记录 */
  } finally {
    rollbackLoading.value = false
  }
}

// ── 生命周期：组件挂载时启动轮询，卸载时停止 ────────────────
onMounted(() => {
  store.startPolling()
})

onBeforeUnmount(() => {
  store.stopPolling()
})
</script>

<style scoped lang="scss">
.grayscale-panel {
  position: relative;
  width: 100%;
  min-height: 100vh;
  padding-bottom: 24px;
}
.page-content {
  position: relative;
  z-index: 1;
  max-width: 1280px;
  margin: 0 auto;
  padding: 24px 24px 48px;
}
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 20px;
  gap: 16px;
  flex-wrap: wrap;
}
.page-head-right {
  display: flex;
  align-items: center;
  gap: 12px;
}
.page-title {
  margin: 0 0 4px;
  font-size: 22px;
  font-weight: 600;
}
.page-sub {
  margin: 0;
  font-size: 13px;
  color: var(--brand-text-secondary, #6b7280);
}
.refresh-time {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--brand-text-secondary, #6b7280);
}

.stats-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 16px;
  margin-bottom: 20px;
}

.op-card {
  margin-bottom: 20px;
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.card-title {
  font-weight: 600;
  font-size: 14px;
}
.op-row {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.op-input {
  min-width: 240px;
  flex: 1 1 240px;
}
.op-input-narrow {
  min-width: 100px;
  flex: 0 1 120px;
}
.op-alert {
  margin-top: 12px;
}

.chart-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
  gap: 16px;
  margin-bottom: 20px;
}
.chart-card {
  min-height: 200px;
}

.monitor-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px 24px;
}
.monitor-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.monitor-label {
  font-size: 12px;
  color: var(--brand-text-secondary, #6b7280);
}
.monitor-value {
  font-size: 18px;
  font-weight: 600;
  color: var(--brand-text-primary, #111827);
}
.monitor-value.danger {
  color: var(--el-color-danger, #f56c6c);
}

.history-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.history-item {
  display: grid;
  grid-template-columns: 80px 110px 1fr auto;
  gap: 8px;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px dashed var(--brand-border-light, #e5e7eb);
  font-size: 13px;
}
.history-item:last-child {
  border-bottom: none;
}
.history-item.is-rollback {
  background: rgba(245, 108, 108, 0.04);
}
.history-time {
  color: var(--brand-text-secondary, #6b7280);
  font-size: 12px;
}
.history-actor {
  font-weight: 500;
}
.history-arrow {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.history-arrow .from {
  color: var(--brand-text-secondary, #6b7280);
}
.history-arrow .to {
  font-weight: 600;
}

.summary-card {
  margin-bottom: 24px;
}
.summary-pre {
  margin: 0;
  padding: 12px 16px;
  background: #f5f7fa;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.5;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  max-height: 320px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

@media (max-width: 640px) {
  .op-row {
    flex-direction: column;
    align-items: stretch;
  }
  .op-input,
  .op-input-narrow {
    min-width: 100%;
    flex: 1 1 100%;
  }
  .monitor-grid {
    grid-template-columns: 1fr;
  }
}
</style>
