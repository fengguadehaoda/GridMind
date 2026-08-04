<template>
  <div class="health-card">
    <h4 class="card-title">
      <el-icon style="margin-right: 6px"><DataAnalysis /></el-icon>
      设备健康评分
    </h4>

    <div class="score-list">
      <div
        v-for="score in scores"
        :key="score.device_id"
        class="score-item"
        :class="score.health_level"
      >
        <div class="score-header">
          <span class="device-name">{{ score.device_name }}</span>
          <el-tag :type="levelTagType(score.health_level)" size="small" effect="dark">
            {{ score.health_score.toFixed(1) }} 分
          </el-tag>
        </div>

        <div class="progress-bar">
          <el-progress
            :percentage="Math.round(score.health_score)"
            :color="progressColor(score.health_score)"
            :stroke-width="8"
            :show-text="false"
          />
        </div>

        <div class="score-summary">{{ score.summary }}</div>

        <!-- 异常清单 -->
        <el-collapse v-if="score.anomalies?.length" style="margin-top: 8px">
          <el-collapse-item :title="`异常详情（${score.anomalies.length} 项）`" name="anomalies">
            <div v-for="(anomaly, i) in score.anomalies" :key="i" class="anomaly-item">
              <div class="anomaly-header">
                <span class="anomaly-metric">{{ anomaly.metric }}</span>
                <el-tag
                  :type="severityTag(anomaly.severity)"
                  size="small"
                >
                  {{ severityLabel(anomaly.severity) }}
                </el-tag>
              </div>
              <div class="anomaly-detail">
                <span>值: {{ anomaly.value.toFixed(2) }}</span>
                <span>Z-score: {{ anomaly.z_score.toFixed(2) }}</span>
              </div>
              <div class="anomaly-desc">{{ anomaly.description }}</div>
            </div>
          </el-collapse-item>
        </el-collapse>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { DataAnalysis } from '@element-plus/icons-vue'
import type { HealthScoreResult, HealthLevel, AnomalySeverity } from '../types'

defineProps<{ scores: HealthScoreResult[] }>()

function levelTagType(level: HealthLevel): 'success' | 'warning' | 'danger' {
  switch (level) {
    case 'normal': return 'success'
    case 'warning': return 'warning'
    case 'critical': return 'danger'
  }
}

function progressColor(score: number): string {
  if (score >= 80) return 'var(--status-success)'
  if (score >= 60) return 'var(--status-warning)'
  return 'var(--status-danger)'
}

function severityTag(severity: AnomalySeverity): 'info' | 'warning' | 'danger' {
  switch (severity) {
    case 'low': return 'info'
    case 'medium': return 'warning'
    case 'high': return 'danger'
  }
}

function severityLabel(severity: AnomalySeverity): string {
  switch (severity) {
    case 'low': return '轻度'
    case 'medium': return '中度'
    case 'high': return '严重'
  }
}
</script>

<style scoped>
.health-card {
  background: var(--bg-card);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md);
  padding: var(--space-4);
  clip-path: var(--clip-corner-sm);
  transition: var(--theme-transition);
}

.card-title {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-secondary);
  margin-bottom: var(--space-3);
  display: flex;
  align-items: center;
  text-transform: uppercase;
  letter-spacing: 0.1em;
}

.score-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.score-item {
  padding: var(--space-3);
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-default);
  background: var(--bg-card);
  transition: var(--theme-transition);
}

.score-item.critical {
  border-left: 3px solid var(--status-danger);
  background: var(--status-danger-soft);
}

.score-item.warning {
  border-left: 3px solid var(--status-warning);
  background: var(--status-warning-soft);
}

.score-item.normal {
  border-left: 3px solid var(--status-success);
  background: var(--status-success-soft);
}

.score-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-2);
}

.device-name {
  font-family: var(--font-cn);
  font-weight: var(--fw-semibold);
  font-size: var(--fs-md);
  color: var(--text-primary);
}

.score-summary {
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  line-height: var(--lh-normal);
  margin-top: var(--space-1);
}

.anomaly-item {
  padding: var(--space-2) 0;
  border-bottom: 1px dashed var(--border-muted);
}

.anomaly-item:last-child {
  border-bottom: none;
}

.anomaly-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-1);
}

.anomaly-metric {
  font-weight: var(--fw-medium);
  font-size: var(--fs-sm);
  color: var(--text-primary);
  font-family: var(--font-mono);
}

.anomaly-detail {
  display: flex;
  gap: var(--space-4);
  font-size: var(--fs-xs);
  color: var(--text-muted);
  font-family: var(--font-mono);
  margin-bottom: var(--space-1);
}

.anomaly-desc {
  font-size: var(--fs-xs);
  color: var(--text-secondary);
  line-height: var(--lh-normal);
}
</style>
