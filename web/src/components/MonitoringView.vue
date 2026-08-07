<template>
  <div class="monitoring-view">
    <!-- v1.5.0 T02：intensity 由 display store 注入（标准 = off / 演示 = high） -->
    <TechBackground :intensity="bgIntensity" :show-glow="true" />

    <div class="page-content">
      <!-- 页面标题 -->
      <div class="page-head">
        <div>
          <h2 class="page-title">设备实时监控</h2>
          <p class="page-sub">电网设备运行状态 · 健康评分 · 遥测趋势实时呈现</p>
        </div>
      </div>

      <!-- 顶部统计：4 个 StatHexagon（v1.6.0 P1-6：auto-fit 自适应 + 空数据自动隐藏） -->
      <div v-if="store.devices.length || store.loading" class="stats-hex-row" data-tour="monitor-stats">
        <StatHexagon
          label="设备总数"
          :value="totalCount"
          tone="info"
          :loading="store.loading && !store.devices.length"
        />
        <StatHexagon
          label="正常运行"
          :value="normalCount"
          tone="success"
          :loading="store.loading && !store.devices.length"
        />
        <StatHexagon
          label="预警"
          :value="warningCount"
          tone="warning"
          :loading="store.loading && !store.devices.length"
        />
        <StatHexagon
          label="严重"
          :value="criticalCount"
          tone="danger"
          :loading="store.loading && !store.devices.length"
        />
      </div>

      <!-- 刷新控制条 -->
      <div class="toolbar" data-tour="monitor-toolbar">
        <div class="toolbar-left">
          <span class="refresh-time">
            <el-icon><Timer /></el-icon>
            最后刷新时间: {{ store.lastUpdated || '—' }}
          </span>
          <el-button
            size="small"
            class="ghost-btn"
            :loading="store.loading"
            @click="store.fetchDevices()"
          >
            <el-icon><Refresh /></el-icon>
            手动刷新
          </el-button>
          <span v-if="store.fetchFailed" class="fetch-error">
            <el-icon><WarningFilled /></el-icon>
            数据刷新失败，正在重试...
          </span>
        </div>

        <div class="toolbar-right">
          <span class="auto-label">自动刷新</span>
          <el-switch v-model="store.pollingEnabled" size="small" />
          <span class="auto-hint">{{ store.pollingEnabled ? '每 15 秒' : '已暂停' }}</span>
        </div>
      </div>

      <!-- 设备总览表格 -->
      <div class="table-card" data-tour="monitor-table">
        <el-table
          v-loading="store.loading && !store.devices.length"
          :data="sortedDevices"
          class="monitor-table"
          :row-class-name="rowClass"
          size="default"
        >
          <el-table-column prop="device_id" label="设备ID" min-width="110">
            <template #default="{ row }">
              <span class="mono-cell">{{ row.device_id }}</span>
            </template>
          </el-table-column>

          <el-table-column prop="device_name" label="设备名称" min-width="150" show-overflow-tooltip />

          <el-table-column label="类型" min-width="90">
            <template #default="{ row }">{{ typeLabel(row.device_type) }}</template>
          </el-table-column>

          <el-table-column prop="location" label="位置" min-width="120" show-overflow-tooltip />

          <el-table-column label="运行状态" min-width="100">
            <template #default="{ row }">
              <el-tag :type="statusInfo(row.status).type" size="small" effect="dark">
                {{ statusInfo(row.status).label }}
              </el-tag>
            </template>
          </el-table-column>

          <el-table-column label="健康分" min-width="130">
            <template #default="{ row }">
              <el-progress
                :percentage="Math.round(row.health?.health_score ?? 0)"
                :color="scoreColor(row.health?.health_score ?? 0)"
                :stroke-width="8"
              />
            </template>
          </el-table-column>

          <el-table-column label="健康等级" min-width="100">
            <template #default="{ row }">
              <el-tag
                :type="levelTagType(row.health?.health_level)"
                size="small"
                effect="dark"
              >
                {{ levelLabel(row.health?.health_level) }}
              </el-tag>
            </template>
          </el-table-column>

          <el-table-column label="最新温度/负载" min-width="150">
            <template #default="{ row }">
              <div class="latest-metrics">
                <span class="metric-cell">温度 <b>{{ fmtTemp(row.latest_telemetry?.temperature) }}</b></span>
                <span class="metric-cell">负载 <b>{{ fmtPct(row.latest_telemetry?.current_load) }}</b></span>
              </div>
            </template>
          </el-table-column>

          <el-table-column label="操作" width="90" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" size="small" @click="openDetail(row)">
                <el-icon style="margin-right: 2px"><View /></el-icon>
                详情
              </el-button>
            </template>
          </el-table-column>

          <template #empty>
            <el-empty description="暂无设备数据" :image-size="70" />
          </template>
        </el-table>
      </div>
    </div>

    <!-- 设备详情抽屉 -->
    <el-drawer
      v-model="drawerVisible"
      title="设备详情"
      size="720px"
      :with-header="true"
    >
      <template #header>
        <div class="drawer-head">
          <span class="drawer-title">
            <el-icon><Monitor /></el-icon>
            设备详情
          </span>
          <el-tag
            v-if="detail"
            :type="levelTagType(detail.health.health_level)"
            size="small"
            effect="dark"
          >
            {{ detail.device.device_id }} · {{ levelLabel(detail.health.health_level) }}
          </el-tag>
        </div>
      </template>

      <div v-loading="detailLoading" class="drawer-body">
        <template v-if="detail">
          <!-- a) 基本信息 -->
          <div class="info-card">
            <div class="card-heading">
              <el-icon><InfoFilled /></el-icon>
              基本信息
            </div>
            <div class="info-grid">
              <div class="info-item">
                <span class="info-label">设备ID</span>
                <span class="info-value mono-cell">{{ detail.device.device_id }}</span>
              </div>
              <div class="info-item">
                <span class="info-label">设备名称</span>
                <span class="info-value">{{ detail.device.device_name }}</span>
              </div>
              <div class="info-item">
                <span class="info-label">设备类型</span>
                <span class="info-value">{{ typeLabel(detail.device.device_type) }}</span>
              </div>
              <div class="info-item">
                <span class="info-label">安装位置</span>
                <span class="info-value">{{ detail.device.location }}</span>
              </div>
              <div class="info-item">
                <span class="info-label">安装日期</span>
                <span class="info-value">{{ detail.device.install_date || '—' }}</span>
              </div>
              <div class="info-item">
                <span class="info-label">运行状态</span>
                <span class="info-value">
                  <el-tag :type="statusInfo(detail.device.status).type" size="small" effect="dark">
                    {{ statusInfo(detail.device.status).label }}
                  </el-tag>
                </span>
              </div>
            </div>
          </div>

          <!-- b) 健康评分与异常清单 -->
          <div class="health-card-wrap" data-tour="monitor-health-card">
            <HealthCard :scores="healthCardScores" />
          </div>

          <!-- c) 遥测趋势图 -->
          <div class="chart-card" data-tour="monitor-telemetry">
            <div class="card-heading chart-heading">
              <span class="chart-title">
                <el-icon><TrendCharts /></el-icon>
                遥测趋势
              </span>
              <el-radio-group v-model="telemetryHours" size="small" @change="onHoursChange">
                <el-radio-button :value="6">6h</el-radio-button>
                <el-radio-button :value="24">24h</el-radio-button>
                <el-radio-button :value="48">48h</el-radio-button>
              </el-radio-group>
            </div>
            <TelemetryChart :telemetry="telemetry" />
          </div>

          <!-- d) 巡检记录 -->
          <div class="inspection-card">
            <div class="card-heading">
              <el-icon><Tickets /></el-icon>
              巡检记录
            </div>
            <div v-if="inspections.length" class="inspection-list">
              <div v-for="ins in inspections" :key="ins.inspection_id" class="inspection-item">
                <div class="inspection-head">
                  <span class="inspection-id">{{ ins.inspection_id }}</span>
                  <el-tag :type="inspectionResultTag(ins.result)" size="small" effect="dark">
                    {{ ins.result }}
                  </el-tag>
                </div>
                <div class="inspection-meta">
                  <span>巡检人: {{ ins.inspector }}</span>
                  <span class="mono-cell">{{ ins.inspect_time }}</span>
                </div>
                <div v-if="ins.notes" class="inspection-notes">{{ ins.notes }}</div>
              </div>
            </div>
            <el-empty v-else description="暂无巡检记录" :image-size="60" />
          </div>
        </template>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import {
  CircleCheck,
  CircleClose,
  Warning,
  WarningFilled,
  Timer,
  Refresh,
  View,
  Monitor,
  InfoFilled,
  TrendCharts,
  Tickets,
} from '@element-plus/icons-vue'
import type {
  DeviceOverview,
  DeviceDetailResponse,
  TelemetryReading,
  HealthScoreResult,
  HealthLevel,
} from '../types'
import * as api from '../api/monitor'
import { useMonitorStore } from '../stores/monitorStore'
import { useDisplay } from '../composables/useDisplay'
import HealthCard from './HealthCard.vue'
import TelemetryChart from './TelemetryChart.vue'
import TechBackground from './background/TechBackground.vue'
import StatHexagon from './controls/StatHexagon.vue'

const store = useMonitorStore()
// v1.5.0 T02：解构出 bgIntensity（storeToRefs 保留响应性）
const { bgIntensity } = useDisplay()

/* ── 顶部统计 ─────────────────────── */
const totalCount = computed(() => store.devices.length)
const normalCount = computed(() => store.devices.filter((d) => d.health?.health_level === 'normal').length)
const warningCount = computed(() => store.devices.filter((d) => d.health?.health_level === 'warning').length)
const criticalCount = computed(() => store.devices.filter((d) => d.health?.health_level === 'critical').length)

/** 行按健康分升序（最差在前） */
const sortedDevices = computed(() =>
  [...store.devices].sort(
    (a, b) => (a.health?.health_score ?? 0) - (b.health?.health_score ?? 0),
  ),
)

/* ── 表格辅助 ─────────────────────── */
const TYPE_LABELS: Record<string, string> = {
  transformer: '变压器',
  breaker: '断路器',
  cable: '电缆',
  busbar: '母线',
}

function typeLabel(type: string): string {
  return TYPE_LABELS[type] || type
}

function statusInfo(status: string): { type: 'success' | 'warning' | 'danger' | 'info'; label: string } {
  switch (status) {
    case 'normal':
    case 'running': return { type: 'success', label: '正常运行' }
    case 'warning': return { type: 'warning', label: '预警' }
    case 'critical': return { type: 'danger', label: '严重' }
    case 'maintenance': return { type: 'warning', label: '检修中' }
    case 'stopped': return { type: 'danger', label: '停机' }
    default: return { type: 'info', label: status || '未知' }
  }
}

function scoreColor(score: number): string {
  if (score >= 80) return 'var(--status-success)'
  if (score >= 60) return 'var(--status-warning)'
  return 'var(--status-danger)'
}

function levelTagType(level?: HealthLevel): 'success' | 'warning' | 'danger' | 'info' {
  switch (level) {
    case 'normal': return 'success'
    case 'warning': return 'warning'
    case 'critical': return 'danger'
    default: return 'info'
  }
}

function levelLabel(level?: HealthLevel): string {
  switch (level) {
    case 'normal': return '正常'
    case 'warning': return '预警'
    case 'critical': return '严重'
    default: return '未知'
  }
}

function fmtTemp(v?: number): string {
  return v === undefined ? '—' : `${v.toFixed(1)}°C`
}

function fmtPct(v?: number): string {
  return v === undefined ? '—' : `${v.toFixed(1)}%`
}

function rowClass({ row }: { row: DeviceOverview }): string {
  return `health-row-${row.health?.health_level ?? 'normal'}`
}

/* ── 详情抽屉 ─────────────────────── */
const drawerVisible = ref(false)
const detailLoading = ref(false)
const selectedId = ref<string | null>(null)
const detail = ref<DeviceDetailResponse | null>(null)
const telemetry = ref<TelemetryReading[]>([])
const telemetryHours = ref(24)

async function openDetail(row: DeviceOverview) {
  selectedId.value = row.device_id
  detail.value = null
  telemetry.value = []
  drawerVisible.value = true
  await Promise.allSettled([loadDetail(), loadTelemetry()])
}

async function loadDetail() {
  if (!selectedId.value) return
  detailLoading.value = true
  try {
    const resp = await api.getDeviceDetail(selectedId.value)
    detail.value = resp
  } catch {
    detail.value = null
  } finally {
    detailLoading.value = false
  }
}

async function loadTelemetry() {
  if (!selectedId.value) return
  try {
    const resp = await api.getDeviceTelemetry(selectedId.value, telemetryHours.value)
    telemetry.value = resp.telemetry || []
  } catch {
    telemetry.value = []
  }
}

function onHoursChange() {
  loadTelemetry()
}

const healthCardScores = computed<HealthScoreResult[]>(() => {
  if (!detail.value) return []
  return [
    {
      device_id: detail.value.device.device_id,
      device_name: detail.value.device.device_name,
      health_score: detail.value.health.health_score,
      health_level: detail.value.health.health_level,
      anomalies: detail.value.anomalies || [],
      summary: detail.value.health.summary,
    },
  ]
})

const inspections = computed(() => detail.value?.inspections || [])

function inspectionResultTag(result: string): 'success' | 'warning' | 'danger' | 'info' {
  switch (result) {
    case '正常': return 'success'
    case '异常': return 'danger'
    case '待复核': return 'warning'
    default: return 'info'
  }
}

onMounted(() => {
  store.fetchDevices()
  store.startPolling()
})

onUnmounted(() => {
  store.stopPolling()
})
</script>

<style scoped>
.monitoring-view {
  position: relative;
  height: 100%;
  overflow-y: auto;
}

.page-content {
  position: relative;
  z-index: var(--z-base);
  padding: var(--space-4) var(--space-5) var(--space-7);
}

/* ── 页面标题 ─────────────────────── */
.page-head {
  margin-bottom: var(--space-4);
}

.page-title {
  font-family: var(--font-cn);
  font-size: var(--fs-xl);
  font-weight: var(--fw-bold);
  color: var(--text-primary);
  letter-spacing: 0.1em;
  transition: var(--theme-transition);
}

.page-sub {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  color: var(--text-muted);
  margin-top: var(--space-1);
  transition: var(--theme-transition);
}

/* ── StatHexagon 栅格（v1.6.0 P1-6：auto-fit + minmax 替换固定列模板）── */
.stats-hex-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: var(--space-4);
  margin-bottom: var(--space-5);
}

/* 紧凑断点（<1280）：下限收窄，自动换行 */
@media (max-width: 1279.98px) {
  .stats-hex-row {
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  }
}

@media (max-width: 640px) {
  .stats-hex-row {
    grid-template-columns: 1fr;
  }
}

/* ── 刷新控制条 ───────────────────── */
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
}

.toolbar-left {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.refresh-time {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  font-family: var(--font-mono);
}

.fetch-error {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--fs-sm);
  color: var(--status-danger);
  font-family: var(--font-cn);
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.auto-label {
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  font-family: var(--font-cn);
}

.auto-hint {
  font-size: var(--fs-xs);
  color: var(--text-muted);
  font-family: var(--font-mono);
}

.ghost-btn {
  background: var(--brand-primary-soft) !important;
  border: 1px solid var(--border-default) !important;
  color: var(--text-primary) !important;
  transition: all var(--dur-fast) var(--ease-out-quint);
}

.ghost-btn:hover {
  border-color: var(--brand-primary) !important;
  color: var(--brand-primary) !important;
  box-shadow: var(--glow-primary-soft);
}

/* ── 设备总览表格 ─────────────────── */
.table-card {
  background: var(--bg-card);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  padding: var(--space-4);
  clip-path: var(--clip-corner-sm);
  overflow: hidden;
  transition: var(--theme-transition);
}

.monitor-table {
  width: 100%;
}

.monitor-table :deep(.el-table__row.health-row-critical td:first-child) {
  box-shadow: inset 3px 0 0 0 var(--status-danger);
}

.monitor-table :deep(.el-table__row.health-row-warning td:first-child) {
  box-shadow: inset 3px 0 0 0 var(--status-warning);
}

.monitor-table :deep(.el-table__row.health-row-normal td:first-child) {
  box-shadow: inset 3px 0 0 0 var(--status-success);
}

.mono-cell {
  font-family: var(--font-mono);
  font-size: var(--fs-sm);
}

.latest-metrics {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: var(--fs-xs);
  color: var(--text-secondary);
  font-family: var(--font-cn);
}

.metric-cell b {
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-weight: var(--fw-semibold);
  margin-left: var(--space-1);
}

/* ── 详情抽屉 ─────────────────────── */
.drawer-head {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.drawer-title {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-family: var(--font-cn);
  font-size: var(--fs-md);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
}

.drawer-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.info-card,
.chart-card,
.inspection-card {
  background: var(--bg-card);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  padding: var(--space-4);
  transition: var(--theme-transition);
}

.card-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
  margin-bottom: var(--space-3);
  text-transform: uppercase;
  letter-spacing: 0.1em;
}

.card-heading .el-icon {
  margin-right: var(--space-2);
  color: var(--brand-primary);
}

.chart-heading {
  margin-bottom: var(--space-3);
}

.chart-title {
  display: inline-flex;
  align-items: center;
}

.info-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-3) var(--space-5);
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.info-label {
  font-size: var(--fs-xs);
  color: var(--text-muted);
  font-family: var(--font-cn);
}

.info-value {
  font-size: var(--fs-sm);
  color: var(--text-primary);
  word-break: break-all;
  font-family: var(--font-cn);
}

/* ── 巡检记录 ─────────────────────── */
.inspection-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.inspection-item {
  padding: var(--space-3);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  transition: var(--theme-transition);
}

.inspection-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-2);
}

.inspection-id {
  font-family: var(--font-mono);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
}

.inspection-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  font-size: var(--fs-xs);
  color: var(--text-muted);
  margin-bottom: var(--space-2);
}

.inspection-notes {
  font-size: var(--fs-xs);
  color: var(--text-secondary);
  line-height: var(--lh-normal);
}

/* ── 响应式 ───────────────────────── */
@media (max-width: 720px) {
  .info-grid {
    grid-template-columns: 1fr;
  }
  .page-content {
    padding: var(--space-3);
  }
}
</style>
