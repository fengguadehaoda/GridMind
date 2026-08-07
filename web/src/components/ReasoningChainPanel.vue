<template>
  <div class="reasoning-chain-panel" :class="severityClass">
    <!-- 顶部状态栏 -->
    <div class="panel-header" @click="toggleCollapsed">
      <div class="header-left">
        <el-icon class="header-icon"><Aim /></el-icon>
        <span class="header-title">三层推理链 · 可解释性</span>
        <!-- P1-3: 折叠时仍可见的 step 数量标签 -->
        <el-tag size="small" type="info" class="step-count-tag">
          推理链 {{ result.reasoning_chain.length }} 步
        </el-tag>
        <el-tag :type="severityTagType" size="small" class="severity-tag">
          {{ severityLabel }}
        </el-tag>
        <el-tag v-if="result.conflict_detected" type="danger" size="small" class="conflict-tag">
          ⚠ LLM-机理矛盾
        </el-tag>
        <el-tag v-if="result.requires_human_review" type="warning" size="small" class="hitl-tag">
          需人工复核
        </el-tag>
      </div>
      <el-icon class="collapse-icon" :class="{ collapsed }">
        <ArrowDown />
      </el-icon>
    </div>

    <!-- 折叠内容 -->
    <el-collapse-transition>
      <div v-show="!collapsed" class="panel-body">
        <!-- 融合后最终结论 -->
        <div class="final-diagnosis">
          <div class="section-label">融合后结论</div>
          <pre class="diagnosis-text">{{ result.final_diagnosis }}</pre>
        </div>

        <el-collapse v-model="activeLayers">
          <!-- 顶层 · LLM -->
          <el-collapse-item name="llm">
            <template #title>
              <div class="layer-header">
                <span class="layer-badge layer-llm">顶层</span>
                <span class="layer-name">LLM 推理</span>
                <span class="layer-outcome">{{ layerOutcome('llm') }}</span>
              </div>
            </template>
            <div class="layer-content">
              <div class="kv-row">
                <span class="kv-key">fault_type</span>
                <span class="kv-value">{{ result.llm_output.fault_type }}</span>
              </div>
              <div class="kv-row">
                <span class="kv-key">fault_location</span>
                <span class="kv-value">{{ result.llm_output.fault_location }}</span>
              </div>
              <div class="kv-row">
                <span class="kv-key">confidence</span>
                <span class="kv-value">{{ result.llm_output.confidence.toFixed(2) }}</span>
              </div>
              <div class="kv-row">
                <span class="kv-key">severity</span>
                <span class="kv-value">
                  <el-tag :type="severityToTag(result.llm_output.severity)" size="small">
                    {{ result.llm_output.severity }}
                  </el-tag>
                </span>
              </div>
              <div class="reasoning-text">{{ result.llm_output.reasoning_text }}</div>
              <div v-if="result.llm_output.evidence_refs.length" class="evidence-list">
                <div class="evidence-label">证据引用 ({{ result.llm_output.evidence_refs.length }})</div>
                <div
                  v-for="(ev, i) in result.llm_output.evidence_refs"
                  :key="i"
                  class="evidence-item"
                >
                  <el-tag size="small" type="info">[{{ ev.type }}]</el-tag>
                  <span class="evidence-id">{{ ev.id }}</span>
                  <span class="evidence-summary">{{ ev.summary }}</span>
                </div>
              </div>
            </div>
          </el-collapse-item>

          <!-- 中层 · 机理校验 -->
          <el-collapse-item name="mechanical">
            <template #title>
              <div class="layer-header">
                <span class="layer-badge layer-mc">中层</span>
                <span class="layer-name">机理校验</span>
                <span class="layer-outcome">
                  {{ result.mechanical_check.checks.length }} 项
                  · {{ result.mechanical_check.critical_failures }} critical
                </span>
              </div>
            </template>
            <div class="layer-content">
              <div
                v-for="(c, i) in result.mechanical_check.checks"
                :key="i"
                class="check-item"
                :class="{ failed: !c.passed }"
              >
                <div class="check-header">
                  <el-tag :type="!c.passed ? severityToTag(c.severity || 'info') : 'success'" size="small">
                    {{ c.rule_id }}
                  </el-tag>
                  <span class="check-name">{{ c.rule_name }}</span>
                  <el-tag v-if="!c.passed" :type="severityToTag(c.severity || 'warning')" size="small" effect="dark">
                    {{ c.severity || 'warning' }}
                  </el-tag>
                </div>
                <div class="check-explanation">{{ c.explanation }}</div>
                <div v-if="Object.keys(c.evidence).length" class="check-evidence">
                  <span v-for="(v, k) in flattenEvidence(c.evidence)" :key="k" class="kv-mini">
                    <span class="kv-key">{{ k }}:</span> <span class="kv-value">{{ v }}</span>
                  </span>
                </div>
              </div>
            </div>
          </el-collapse-item>

          <!-- 底层 · 规则护栏 -->
          <el-collapse-item name="rules">
            <template #title>
              <div class="layer-header">
                <span class="layer-badge layer-rg">底层</span>
                <span class="layer-name">规则护栏</span>
                <span class="layer-outcome">
                  {{ result.rules_guard.triggered.length }} 条触发
                  <span v-if="result.rules_guard.forced_shutdown" class="forced-shutdown">· 强制停运</span>
                  <span v-else-if="result.rules_guard.forced_hitl" class="forced-hitl">· 强制 HITL</span>
                </span>
              </div>
            </template>
            <div class="layer-content">
              <div v-if="!result.rules_guard.triggered.length" class="empty-rules">
                未触发任何规则
              </div>
              <div
                v-for="(r, i) in result.rules_guard.triggered"
                :key="i"
                class="rule-item"
                :class="`rule-${r.action}`"
              >
                <div class="rule-header">
                  <el-tag :type="severityToTag(r.severity)" size="small" effect="dark">
                    {{ r.action }}
                  </el-tag>
                  <span class="rule-id">{{ r.rule_id }}</span>
                  <span v-if="r.code" class="rule-code">[{{ r.code }}]</span>
                </div>
                <div class="rule-title">{{ r.title }}</div>
                <div class="rule-description">{{ r.description }}</div>
                <div v-if="r.matched_keywords.length" class="rule-keywords">
                  <span class="kv-key">匹配:</span>
                  <el-tag
                    v-for="(kw, j) in r.matched_keywords"
                    :key="j"
                    size="small"
                    type="info"
                    style="margin-left: 4px"
                  >
                    {{ kw }}
                  </el-tag>
                </div>
              </div>
            </div>
          </el-collapse-item>
        </el-collapse>

        <!-- 推理链时间线 -->
        <div class="reasoning-timeline">
          <div class="section-label">
            推理链时间线
            <span class="step-count">({{ result.reasoning_chain.length }} 步)</span>
          </div>
          <div
            v-for="(step, i) in visibleSteps"
            :key="i"
            class="timeline-step"
            :class="`step-${step.layer}`"
          >
            <div class="timeline-marker">
              <span class="step-index">{{ i + 1 }}</span>
            </div>
            <div class="timeline-content">
              <div class="step-title">{{ step.step_name }}</div>
              <div class="step-outcome">{{ step.outcome }}</div>
              <div class="step-elapsed">{{ step.elapsed_ms }}ms</div>
            </div>
          </div>
          <!-- P1-3: 展开更多按钮（推理链 > P1-3_THRESHOLD 步时显示） -->
          <div
            v-if="hasMoreSteps && !timelineFullyExpanded"
            class="timeline-expand-btn-wrapper"
          >
            <el-button size="small" plain @click="loadMoreSteps">
              展开更多（剩余 {{ hiddenStepCount }} 步）
            </el-button>
          </div>
          <div
            v-else-if="hasMoreSteps && timelineFullyExpanded"
            class="timeline-expand-btn-wrapper"
          >
            <el-button size="small" plain @click="collapseSteps">
              收起
            </el-button>
          </div>
        </div>

        <!-- ═══ v1.5.1 T03 F2：实时推理链 · 可编辑步骤（与上述历史时间线并列）═══ -->
        <!-- 仅当 reasoning.store 有实时步骤时渲染；不破坏 v1.5.0 行为 -->
        <div v-if="liveSteps.length > 0" class="live-reasoning">
          <div class="section-label">
            实时推理链 · 可编辑
            <span class="step-count">({{ liveSteps.length }} 步 · 仅 user content 可编辑)</span>
          </div>
          <div
            v-for="step in liveSteps"
            :key="step.id"
            class="live-reasoning-item"
            :class="[
              `live-status-${step.status}`,
              isEditingStep(step.id) && 'live-editing',
            ]"
          >
            <div class="live-reasoning-header">
              <span class="live-step-index">#{{ step.index + 1 }}</span>
              <span class="live-step-name">{{ step.name }}</span>
              <el-tag :type="liveStatusTagType(step.status)" size="small">
                {{ step.status }}
              </el-tag>
              <span v-if="isEditingStep(step.id)" class="editing-indicator">⚙ 编辑中</span>
              <!-- 编辑按钮：仅未在编辑此 step 时显示；StepEditButton 内部还防御性检查 step.isEditable -->
              <StepEditButton
                v-if="!isEditingStep(step.id)"
                :step-id="step.id"
                :disabled="!canEditLive"
                class="live-edit-btn"
              />
            </div>
            <!-- 只读态：显示原 prompt 片段 -->
            <div v-if="!isEditingStep(step.id)" class="live-prompt-fragment">
              <span v-if="step.promptFragment">{{ step.promptFragment }}</span>
              <span v-else class="empty-fragment">(无 prompt 片段)</span>
            </div>
            <!-- 编辑态：挂载 inline editor -->
            <StepInlineEditor
              v-else
              :step-id="step.id"
            />
          </div>
          <div v-if="!canEditLive" class="live-edit-hint">
            <small>
              当前推理状态（{{ reasoning.status }}）下不可编辑；
              仅 <kbd>running</kbd> / <kbd>paused</kbd> 可进入编辑。
            </small>
          </div>
        </div>
      </div>
    </el-collapse-transition>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Aim, ArrowDown, Edit } from '@element-plus/icons-vue'
import type { DiagnosisFusionResult, DiagnosisOutput, DiagnosisReasoningStep, StepStatus } from '../types'
import { useReasoningStore } from '@/stores/reasoning'
import StepEditButton from './reasoning/StepEditButton.vue'
import StepInlineEditor from './reasoning/StepInlineEditor.vue'

const props = defineProps<{
  result: DiagnosisFusionResult
  initiallyCollapsed?: boolean
}>()

// ═══ v1.5.1 T03 F2：实时推理链（可编辑步骤）═══
// 注意：v1.5.0 的 result.reasoning_chain（历史诊断融合结果）与 v1.5.1 的
// reasoning.steps（AI 实时推理步骤）是两套独立数据，UI 上分段渲染：
//   1) 上半：v1.5.0 已有的三层推理链（DiagnosisFusionResult + 历史时间线）
//   2) 下半：v1.5.1 新增的实时推理链 + 编辑入口（仅 reasoning.steps 非空时渲染）
const reasoning = useReasoningStore()

/** 实时推理步骤列表（保留 completed 之前的所有步骤，含 running/pending/edited） */
const liveSteps = computed(() => reasoning.steps)

/** 是否处于可编辑状态（PRD §3.2.3 + §11.1：仅 running / paused 可进入编辑） */
const canEditLive = computed<boolean>(() =>
  ['running', 'paused'].includes(reasoning.status),
)

/** 判断当前 stepId 是否正在被编辑 */
function isEditingStep(stepId: string): boolean {
  return reasoning.editingStepId === stepId
}

/** step.status → el-tag type 映射（实时区专用） */
function liveStatusTagType(s: StepStatus): 'success' | 'warning' | 'info' | 'danger' | 'primary' {
  switch (s) {
    case 'completed': return 'success'
    case 'edited': return 'warning'
    case 'running': return 'info'
    case 'failed': return 'danger'
    case 'pending':
    default: return 'primary'
  }
}

const collapsed = ref(props.initiallyCollapsed ?? true)
const activeLayers = ref<string[]>([])

// P1-3: 推理链时间线分页渲染（避免超大链 >100 步时卡顿）
// 默认只显示前 P1-3_THRESHOLD 步，点击"展开更多"加载后续步骤
const P1_3_THRESHOLD = 5
const visibleStepCount = ref(P1_3_THRESHOLD)

const visibleSteps = computed(() => {
  const chain = props.result.reasoning_chain
  return chain.slice(0, visibleStepCount.value)
})

const hasMoreSteps = computed(() => {
  return props.result.reasoning_chain.length > visibleStepCount.value
})

const hiddenStepCount = computed(() => {
  return Math.max(0, props.result.reasoning_chain.length - visibleStepCount.value)
})

const timelineFullyExpanded = computed(() => {
  return visibleStepCount.value >= props.result.reasoning_chain.length
})

function loadMoreSteps() {
  // 一次性加载剩余步骤（不做真正的虚拟列表，简化实现）
  visibleStepCount.value = props.result.reasoning_chain.length
}

function collapseSteps() {
  visibleStepCount.value = P1_3_THRESHOLD
}

watch(
  () => props.result,
  () => {
    // 新结果 → 自动展开（确保用户能看到推理链）
    if (!props.initiallyCollapsed) {
      collapsed.value = false
    }
    // P1-3: 新结果 → 重置分页
    visibleStepCount.value = P1_3_THRESHOLD
  },
)

const severityClass = computed(() => `severity-${props.result.final_severity}`)

const severityLabel = computed(() => {
  const map = { info: '信息', warning: '警告', critical: '严重' }
  return map[props.result.final_severity] || props.result.final_severity
})

const severityTagType = computed(() => severityToTag(props.result.final_severity))

function severityToTag(sev: string | null | undefined): 'success' | 'warning' | 'danger' | 'info' {
  if (sev === 'critical') return 'danger'
  if (sev === 'warning') return 'warning'
  if (sev === 'info') return 'info'
  return 'success'
}

function layerOutcome(layer: 'llm' | 'mechanical' | 'rules'): string {
  const step = props.result.reasoning_chain.find((s: DiagnosisReasoningStep) => s.layer === layer)
  return step ? step.outcome : '—'
}

function toggleCollapsed() {
  collapsed.value = !collapsed.value
}

function flattenEvidence(evidence: Record<string, unknown>): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [k, v] of Object.entries(evidence)) {
    if (v === null || v === undefined) continue
    if (typeof v === 'object') {
      out[k] = JSON.stringify(v)
    } else {
      out[k] = String(v)
    }
  }
  return out
}
</script>

<style scoped>
.reasoning-chain-panel {
  background: var(--bg-card);
  border: 1px solid var(--border-default);
  border-left: 3px solid var(--brand-primary);
  border-radius: var(--radius-md);
  margin-top: var(--space-2);
  overflow: hidden;
  transition: var(--theme-transition);
}

.reasoning-chain-panel.severity-warning {
  border-left-color: var(--status-warning);
}

.reasoning-chain-panel.severity-critical {
  border-left-color: var(--status-danger);
}

.reasoning-chain-panel.severity-critical .panel-header {
  background: var(--status-danger-fade);
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-3) var(--space-4);
  cursor: pointer;
  user-select: none;
  transition: background var(--dur-fast) var(--ease-out-quint);
}

.panel-header:hover {
  background: var(--brand-primary-fade);
}

.header-left {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.header-icon {
  color: var(--brand-primary);
  font-size: var(--fs-md);
}

.header-title {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.severity-tag,
.conflict-tag,
.hitl-tag,
.step-count-tag {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
}

.collapse-icon {
  color: var(--text-muted);
  transition: transform var(--dur-fast) var(--ease-out-quint);
}

.collapse-icon.collapsed {
  transform: rotate(-90deg);
}

.panel-body {
  padding: 0 var(--space-4) var(--space-4);
  border-top: 1px solid var(--border-default);
}

.final-diagnosis {
  margin: var(--space-3) 0;
  padding: var(--space-3);
  background: var(--bg-card-solid);
  border-radius: var(--radius-sm);
  border: 1px dashed var(--border-default);
}

.section-label {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  margin-bottom: var(--space-1);
}

.diagnosis-text {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
  line-height: var(--lh-loose);
}

.layer-header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.layer-badge {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  font-weight: var(--fw-bold);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.layer-llm {
  background: var(--brand-primary-fade);
  color: var(--brand-primary);
  border: 1px solid var(--brand-primary);
}

.layer-mc {
  background: var(--status-info-fade, var(--brand-primary-fade));
  color: var(--status-info, var(--brand-primary));
  border: 1px solid var(--status-info, var(--brand-primary));
}

.layer-rg {
  background: var(--status-warning-fade, var(--brand-accent-fade));
  color: var(--status-warning, var(--brand-accent));
  border: 1px solid var(--status-warning, var(--brand-accent));
}

.layer-name {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
}

.layer-outcome {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text-muted);
  margin-left: auto;
}

.layer-content {
  padding: var(--space-2) 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.kv-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--fs-sm);
}

.kv-key {
  font-family: var(--font-mono);
  color: var(--text-muted);
  min-width: 110px;
  font-size: var(--fs-xs);
}

.kv-value {
  font-family: var(--font-mono);
  color: var(--text-primary);
  font-size: var(--fs-sm);
}

.kv-mini {
  font-size: var(--fs-xs);
  margin-right: var(--space-2);
  display: inline-block;
}

.reasoning-text {
  padding: var(--space-2);
  background: var(--bg-card-solid);
  border-left: 2px solid var(--border-default);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  font-size: var(--fs-sm);
  line-height: var(--lh-normal);
  font-style: italic;
}

.evidence-list {
  margin-top: var(--space-2);
}

.evidence-label {
  font-size: var(--fs-xs);
  color: var(--text-muted);
  margin-bottom: var(--space-1);
  font-family: var(--font-mono);
}

.evidence-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1) 0;
  font-size: var(--fs-sm);
}

.evidence-id {
  font-family: var(--font-mono);
  color: var(--text-secondary);
  font-size: var(--fs-xs);
}

.evidence-summary {
  color: var(--text-primary);
  font-size: var(--fs-sm);
}

.check-item,
.rule-item {
  padding: var(--space-3);
  background: var(--bg-card-solid);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  border-left: 2px solid var(--status-success);
  transition: var(--theme-transition);
}

.check-item.failed {
  border-left-color: var(--status-danger);
}

.rule-item.rule-force_shutdown {
  border-left-color: var(--status-danger);
  background: var(--status-danger-fade);
}

.rule-item.rule-hitl_required {
  border-left-color: var(--status-warning);
  background: var(--status-warning-fade);
}

.rule-item.rule-warn {
  border-left-color: var(--status-info, var(--brand-primary));
}

.check-header,
.rule-header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  margin-bottom: var(--space-1);
}

.check-name,
.rule-id {
  font-family: var(--font-mono);
  font-size: var(--fs-sm);
  color: var(--text-primary);
  font-weight: var(--fw-semibold);
}

.check-explanation,
.rule-description {
  font-size: var(--fs-sm);
  color: var(--text-secondary);
  line-height: var(--lh-normal);
  margin: var(--space-1) 0;
}

.check-evidence {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
  padding-top: var(--space-1);
  border-top: 1px dashed var(--border-default);
}

.rule-code {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}

.rule-title {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
  margin: var(--space-1) 0;
}

.rule-keywords {
  margin-top: var(--space-1);
  font-size: var(--fs-xs);
}

.forced-shutdown,
.forced-hitl {
  color: var(--status-danger);
  font-weight: var(--fw-semibold);
}

.forced-hitl {
  color: var(--status-warning);
}

.empty-rules {
  padding: var(--space-3);
  text-align: center;
  color: var(--text-muted);
  font-size: var(--fs-sm);
  background: var(--bg-card-solid);
  border-radius: var(--radius-sm);
}

.reasoning-timeline {
  margin-top: var(--space-4);
  padding-top: var(--space-3);
  border-top: 1px solid var(--border-default);
}

.timeline-step {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-2) 0;
  position: relative;
}

.timeline-step:not(:last-child)::after {
  content: '';
  position: absolute;
  left: 14px;
  top: 36px;
  bottom: -8px;
  width: 2px;
  background: var(--border-default);
}

.timeline-marker {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: var(--bg-card-solid);
  border: 2px solid var(--brand-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.timeline-marker .step-index {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  font-weight: var(--fw-bold);
  color: var(--brand-primary);
}

.timeline-step.step-llm .timeline-marker {
  border-color: var(--brand-primary);
}
.timeline-step.step-llm .step-index {
  color: var(--brand-primary);
}

.timeline-step.step-mechanical .timeline-marker {
  border-color: var(--status-info, var(--brand-primary));
}
.timeline-step.step-mechanical .step-index {
  color: var(--status-info, var(--brand-primary));
}

.timeline-step.step-rules .timeline-marker {
  border-color: var(--status-warning, var(--brand-accent));
}
.timeline-step.step-rules .step-index {
  color: var(--status-warning, var(--brand-accent));
}

.timeline-step.step-fusion .timeline-marker {
  border-color: var(--status-danger);
}
.timeline-step.step-fusion .step-index {
  color: var(--status-danger);
}

.timeline-content {
  flex: 1;
  min-width: 0;
}

.step-title {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
}

.step-outcome {
  font-size: var(--fs-xs);
  color: var(--text-secondary);
  margin-top: 2px;
}

.step-elapsed {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text-muted);
  margin-top: 2px;
}

.step-count {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text-muted);
  margin-left: var(--space-1);
  font-weight: var(--fw-normal);
}

.timeline-expand-btn-wrapper {
  display: flex;
  justify-content: center;
  padding: var(--space-2) 0;
  border-top: 1px dashed var(--border-default);
  margin-top: var(--space-2);
}

/* ═══ v1.5.1 T03 F2 实时推理链 · 可编辑样式 ═══ */
.live-reasoning {
  margin-top: var(--space-4);
  padding-top: var(--space-3);
  border-top: 2px solid var(--brand-primary);
}

.live-reasoning-item {
  padding: var(--space-2) 0;
  border-bottom: 1px dashed var(--border-default);
}

.live-reasoning-item.live-editing {
  background: var(--brand-primary-fade, rgba(76, 194, 255, 0.06));
  border-radius: var(--radius-sm, 4px);
  padding: var(--space-2) var(--space-2);
  margin: var(--space-1) 0;
  border-left: 3px solid var(--brand-primary);
}

.live-reasoning-header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  margin-bottom: var(--space-1);
}

.live-step-index {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
  color: var(--brand-primary);
  min-width: 30px;
}

.live-step-name {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  color: var(--text-primary);
  font-weight: var(--fw-semibold);
  flex: 1;
  min-width: 0;
}

.editing-indicator {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--status-accent-fg, var(--brand-primary));
  font-weight: var(--fw-semibold);
}

.live-edit-btn {
  margin-left: auto;
}

.live-prompt-fragment {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text-secondary);
  padding: var(--space-1) var(--space-2);
  background: var(--bg-card-solid);
  border-radius: var(--radius-sm, 4px);
  border-left: 2px solid var(--border-default);
  line-height: var(--lh-normal);
  word-break: break-word;
  white-space: pre-wrap;
}

.empty-fragment {
  color: var(--text-muted);
  font-style: italic;
}

.live-edit-hint {
  margin-top: var(--space-2);
  padding: var(--space-1) var(--space-2);
  background: var(--bg-card-solid);
  border-radius: var(--radius-sm, 4px);
  text-align: center;
  color: var(--text-muted);
  font-size: var(--fs-xs);
}

.live-edit-hint kbd {
  padding: 1px 4px;
  font-family: var(--font-mono);
  background: var(--bg-base, #14141c);
  border: 1px solid var(--border-default);
  border-radius: 3px;
  font-size: 10px;
}

/* 实时 step 状态色（左侧 border 颜色） */
.live-status-completed { border-left-color: var(--status-success, #4ade80); }
.live-status-running { border-left-color: var(--status-info, #4cc2ff); }
.live-status-pending { border-left-color: var(--text-muted, #888); }
.live-status-edited { border-left-color: var(--status-warning, #fbbf24); }
.live-status-failed { border-left-color: var(--status-danger, #ff5e6c); }
</style>
