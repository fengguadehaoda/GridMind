<script setup lang="ts">
/**
 * PlanComparePanel.vue · 方案对比面板（v1.6.0 P1-4）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构决策（p1-iteration-architecture §1 P1-4）：
 *   - ≥3 方案卡片，每卡 3 维打分条（操作开关数量 / 负载率 / 保护适配性）+ 总分
 *   - 探索模式只读；规划模式可"应用"（联动 metricsStore.setRatio）
 *   - 推荐方案高亮
 */
import { computed } from 'vue'
import { useGrayscaleGraphStore } from '@/stores/grayscaleGraph'

const graphStore = useGrayscaleGraphStore()

const plans = computed(() => graphStore.plans)
const canApply = computed(() => !!adminToken.value)

const adminToken = defineModel<string>('adminToken', { default: '' })

const DIMENSION_COLOR: Record<string, string> = {
  switchCount: 'var(--brand-primary)',
  loadRate: 'var(--status-success)',
  protectionFit: 'var(--status-warning)',
}
</script>

<template>
  <div class="gm-plan-compare" data-test="plan-compare">
    <div class="gm-plan-compare__head">
      <span class="gm-plan-compare__title">方案对比</span>
      <span class="gm-plan-compare__hint">
        {{ graphStore.mode === 'plan' ? '规划模式 · 勾选节点后自动生成' : '探索模式 · 系统推荐' }}
      </span>
    </div>

    <div v-if="plans.length === 0" class="gm-plan-compare__empty">
      暂无方案（{{ graphStore.mode === 'plan' ? '请先勾选候选节点' : '等待拓扑数据' }}）
    </div>

    <div v-else class="gm-plan-compare__grid">
      <el-card
        v-for="plan in plans"
        :key="plan.id"
        class="gm-plan-compare__card"
        :class="{ 'is-recommended': plan.recommended, 'is-applied': graphStore.lastAppliedPlanId === plan.id }"
        shadow="hover"
      >
        <template #header>
          <div class="gm-plan-compare__card-head">
            <span class="gm-plan-compare__card-name">{{ plan.name }}</span>
            <el-tag v-if="plan.recommended" size="small" type="success" effect="dark">推荐</el-tag>
            <el-tag v-else-if="graphStore.lastAppliedPlanId === plan.id" size="small" type="primary" effect="dark">
              已应用
            </el-tag>
            <span class="gm-plan-compare__total">总分 <b>{{ plan.total }}</b></span>
          </div>
        </template>

        <div class="gm-plan-compare__scores">
          <div v-for="score in plan.scores" :key="score.dimension" class="gm-plan-compare__score">
            <div class="gm-plan-compare__score-head">
              <span class="gm-plan-compare__score-label">{{ score.label }}</span>
              <span class="gm-plan-compare__score-raw">
                {{ score.raw }} · {{ score.value }} 分
              </span>
            </div>
            <div class="gm-plan-compare__score-track">
              <div
                class="gm-plan-compare__score-bar"
                :style="{
                  width: `${score.value}%`,
                  background: DIMENSION_COLOR[score.dimension] ?? 'var(--brand-primary)',
                }"
              ></div>
            </div>
          </div>
        </div>

        <div class="gm-plan-compare__foot">
          <span class="gm-plan-compare__target">目标切流：{{ plan.targetRatio }}%</span>
          <el-button
            size="small"
            type="primary"
            :disabled="!canApply"
            :data-test="`apply-plan-${plan.id}`"
            @click="graphStore.applyPlan(plan, adminToken)"
          >
            应用方案
          </el-button>
        </div>
      </el-card>
    </div>

    <p v-if="!canApply" class="gm-plan-compare__token-hint">
      输入 X-Admin-Token 后即可应用方案
    </p>
  </div>
</template>

<style scoped lang="scss">
.gm-plan-compare {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.gm-plan-compare__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-3);
}

.gm-plan-compare__title {
  font-family: var(--font-cn);
  font-size: var(--fs-md);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
  letter-spacing: 0.06em;
}

.gm-plan-compare__hint {
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}

.gm-plan-compare__empty {
  padding: var(--space-8);
  text-align: center;
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  color: var(--text-muted);
  border: 1px dashed var(--border-default);
  border-radius: var(--radius-md);
}

.gm-plan-compare__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: var(--space-4);
}

.gm-plan-compare__card {
  transition: var(--theme-transition);
}

.gm-plan-compare__card.is-recommended {
  border-color: var(--status-success);
}

.gm-plan-compare__card.is-applied {
  border-color: var(--brand-primary);
}

.gm-plan-compare__card :deep(.el-card__header) {
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--border-muted);
}

.gm-plan-compare__card :deep(.el-card__body) {
  padding: var(--space-4);
}

.gm-plan-compare__card-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.gm-plan-compare__card-name {
  font-family: var(--font-cn);
  font-size: var(--fs-md);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
}

.gm-plan-compare__total {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}

.gm-plan-compare__total b {
  font-size: var(--fs-lg);
  color: var(--brand-primary);
  margin-left: 4px;
}

.gm-plan-compare__scores {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.gm-plan-compare__score-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  margin-bottom: 4px;
}

.gm-plan-compare__score-label {
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  color: var(--text-secondary);
}

.gm-plan-compare__score-raw {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}

.gm-plan-compare__score-track {
  height: 8px;
  border-radius: 4px;
  background: var(--bg-input);
  overflow: hidden;
}

.gm-plan-compare__score-bar {
  height: 100%;
  border-radius: 4px;
  transition: width var(--dur-base) var(--ease-out-quint);
}

.gm-plan-compare__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-top: var(--space-4);
  padding-top: var(--space-3);
  border-top: 1px solid var(--border-muted);
}

.gm-plan-compare__target {
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}

.gm-plan-compare__token-hint {
  margin: 0;
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}
</style>
