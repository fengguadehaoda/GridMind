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
        <!-- v1.5.0 P0-2 状态四重区分：score-item 起始位置加 PulseDot 四元组 -->
        <div class="score-row">
          <PulseDot
            :tone="levelToTone(score.health_level)"
            :shape="levelToShape(score.health_level)"
            :glyph="levelToGlyph(score.health_level)"
            :size="10"
            :speed="1.8"
            class="score-row__dot"
          />
          <div class="score-header">
            <span class="device-name">
              <!-- v1.5.0 P0-2：左上角加 StatusIcon（颜色 + 形状 + 图标 + 文字码）-->
              <StatusIcon
                :status="levelToTone(score.health_level)"
                :size="16"
                class="device-name__icon"
              />
              {{ score.device_name }}
            </span>
            <el-tag
              :type="levelTagType(score.health_level)"
              size="small"
              effect="dark"
            >
              <!-- v1.5.0 P0-2：el-tag 前置 StatusIcon（增强可访问性）-->
              <StatusIcon
                :status="levelToTone(score.health_level)"
                :size="11"
                class="tag-icon"
              />
              {{ score.health_score.toFixed(1) }} 分
            </el-tag>
          </div>
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
                <span class="anomaly-metric">
                  <!-- 异常项起始：PulseDot + 异常类型 icon -->
                  <PulseDot
                    :tone="severityToTone(anomaly.severity)"
                    :shape="severityToShape(anomaly.severity)"
                    :glyph="severityToGlyph(anomaly.severity)"
                    :size="8"
                    :speed="1.5"
                    class="anomaly-metric__dot"
                  />
                  {{ anomaly.metric }}
                </span>
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
import type {
  HealthScoreResult,
  HealthLevel,
  AnomalySeverity,
} from '../types'
import type { Status, StatusGlyph, StatusShape } from '@/types/theme'
import { STATUS_PRESENTATION } from '@/types/theme'
import StatusIcon from './controls/StatusIcon.vue'
import PulseDot from './background/PulseDot.vue'

defineProps<{ scores: HealthScoreResult[] }>()

/* ── HealthLevel → Status / 形状 / glyph 映射（保留原 visual + 加四重区分）── */
function levelToTone(level: HealthLevel): Status {
  return level // 'normal' | 'warning' | 'critical'
}

function levelToShape(level: HealthLevel): StatusShape {
  return STATUS_PRESENTATION[level].shape
}

function levelToGlyph(level: HealthLevel): StatusGlyph {
  return STATUS_PRESENTATION[level].glyph
}

function levelTagType(level: HealthLevel): 'success' | 'warning' | 'danger' {
  switch (level) {
    case 'normal':
      return 'success'
    case 'warning':
      return 'warning'
    case 'critical':
      return 'danger'
  }
}

function progressColor(score: number): string {
  if (score >= 80) return 'var(--status-success)'
  if (score >= 60) return 'var(--status-warning)'
  return 'var(--status-danger)'
}

/* ── AnomalySeverity → Status 映射 ── */
function severityToTone(severity: AnomalySeverity): Status {
  switch (severity) {
    case 'low':
      return 'info'
    case 'medium':
      return 'warning'
    case 'high':
      return 'critical'
  }
}

function severityToShape(severity: AnomalySeverity): StatusShape {
  return STATUS_PRESENTATION[severityToTone(severity)].shape
}

function severityToGlyph(severity: AnomalySeverity): StatusGlyph {
  return STATUS_PRESENTATION[severityToTone(severity)].glyph
}

function severityTag(severity: AnomalySeverity): 'info' | 'warning' | 'danger' {
  switch (severity) {
    case 'low':
      return 'info'
    case 'medium':
      return 'warning'
    case 'high':
      return 'danger'
  }
}

function severityLabel(severity: AnomalySeverity): string {
  switch (severity) {
    case 'low':
      return '轻度'
    case 'medium':
      return '中度'
    case 'high':
      return '严重'
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
  position: relative;
}

/* ── 保留旧视觉：border-left + bg-soft（向后兼容 T02 之前的设计）── */
.score-item.critical {
  border-left: 3px solid var(--cb-status-critical-fg, var(--status-danger));
  background: var(--cb-status-critical-soft, var(--status-danger-soft));
}

.score-item.warning {
  border-left: 3px solid var(--cb-status-warning-fg, var(--status-warning));
  background: var(--cb-status-warning-soft, var(--status-warning-soft));
}

.score-item.normal {
  border-left: 3px solid var(--cb-status-normal-fg, var(--status-success));
  background: var(--cb-status-normal-soft, var(--status-success-soft));
}

/* ── v1.5.0 P0-2 状态四重区分：score-row（PulseDot + 设备名 + 分数）── */
.score-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
}

.score-row__dot {
  flex-shrink: 0;
}

.score-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex: 1;
  min-width: 0;
  gap: var(--space-2);
}

.device-name {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-family: var(--font-cn);
  font-weight: var(--fw-semibold);
  font-size: var(--fs-md);
  color: var(--text-primary);
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.device-name__icon {
  flex-shrink: 0;
}

.tag-icon {
  margin-right: 4px;
  vertical-align: -1px;
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
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-weight: var(--fw-medium);
  font-size: var(--fs-sm);
  color: var(--text-primary);
  font-family: var(--font-mono);
}

.anomaly-metric__dot {
  flex-shrink: 0;
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
