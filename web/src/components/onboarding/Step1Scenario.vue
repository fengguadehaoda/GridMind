<template>
  <div class="gm-step-scenario">
    <div class="gm-step-scenario__intro">
      <h2 class="gm-step-scenario__title">第一步 · 选择你的演练场景</h2>
      <p class="gm-step-scenario__desc">
        GridMind 包含 5 个核心视图，下面 4 个场景覆盖了最常被用到的路径。点选任意一个开始体验。
      </p>
    </div>

    <div class="gm-step-scenario__grid">
      <button
        v-for="sc in scenarios"
        :key="sc.id"
        type="button"
        class="gm-step-scenario__card"
        :class="{ 'is-selected': selectedId === sc.id }"
        :aria-pressed="selectedId === sc.id"
        :aria-label="`选择场景：${sc.title}`"
        @click="onSelect(sc.id)"
      >
        <div class="gm-step-scenario__card-head">
          <el-icon class="gm-step-scenario__icon">
            <component :is="iconFor(sc.icon)" />
          </el-icon>
          <el-icon
            v-if="selectedId === sc.id"
            class="gm-step-scenario__check"
            :size="16"
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="3"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <polyline points="20 6 9 17 4 12" />
            </svg>
          </el-icon>
        </div>
        <div class="gm-step-scenario__card-title">{{ sc.title }}</div>
        <div class="gm-step-scenario__card-desc">{{ sc.description }}</div>
        <div class="gm-step-scenario__card-preview">
          <span class="gm-step-scenario__card-preview-label">种子问题：</span>
          <span class="gm-step-scenario__card-preview-text">{{ sc.starterMessage }}</span>
        </div>
      </button>
    </div>

    <div class="gm-step-scenario__hint">
      <el-icon><InfoFilled /></el-icon>
      <span>选择后会自动跳到第 2 步，你也可以点底部"下一步"按钮手动推进。</span>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * Step1Scenario · 引导 wizard 第 1 步
 * 4 个固定场景（架构 §3.2 主理人决策 #1 采纳）：
 *   - monitor-overview    实时监控全览
 *   - fault-diagnosis     故障诊断演练
 *   - knowledge-rag       知识库检索
 *   - grayscale-rollout   灰度切换
 *
 * V1.6：场景文案改由知识库下发（`useFeatureIntro().scenarios`），
 * API 不可用时自动回落 `ONBOARDING_SCENARIOS_FALLBACK`，交互零变化。
 */
import { onMounted, ref } from 'vue'
import {
  InfoFilled,
  Monitor,
  FirstAidKit,
  Reading,
  Switch,
  DataAnalysis,
  WarningFilled,
  Document,
  Connection,
  Cpu,
  Grid,
  ChatDotRound,
} from '@element-plus/icons-vue'
import { useFeatureIntro } from '@/composables/useFeatureIntro'
import type { OnboardingScenarioId } from '@/types/theme'

const emit = defineEmits<{
  (e: 'navigate', payload: { scenarioId: OnboardingScenarioId }): void
}>()

/** 场景数据源：知识库优先，失败自动兜底（composable 内部已静默降级） */
const { scenarios, load } = useFeatureIntro()

const selectedId = ref<OnboardingScenarioId | null>(null)

onMounted(() => {
  void load()
})

/** Element Plus icon component lookup for the dynamic `icon` field */
const ICON_MAP: Record<string, unknown> = {
  Monitor,
  FirstAidKit,
  Reading,
  Switch,
  DataAnalysis,
  WarningFilled,
  Document,
  Connection,
  Cpu,
  Grid,
  ChatDotRound,
}

function iconFor(name: string) {
  return ICON_MAP[name] ?? Monitor
}

function onSelect(id: OnboardingScenarioId): void {
  selectedId.value = id
  // 不停留，notify 父级直接进入 step 2
  emit('navigate', { scenarioId: id })
}
</script>

<style scoped>
.gm-step-scenario {
  display: flex;
  flex-direction: column;
  gap: var(--space-5, 20px);
  width: 100%;
}

.gm-step-scenario__intro {
  text-align: center;
}

.gm-step-scenario__title {
  margin: 0 0 var(--space-2, 8px);
  font-family: var(--font-cn, 'PingFang SC', sans-serif);
  font-size: var(--fs-xl, 20px);
  font-weight: var(--fw-bold, 700);
  color: var(--text-primary);
  letter-spacing: 0.05em;
}

.gm-step-scenario__desc {
  margin: 0;
  font-family: var(--font-cn, 'PingFang SC', sans-serif);
  font-size: var(--fs-sm, 12px);
  color: var(--text-secondary);
  line-height: var(--lh-loose, 1.7);
}

.gm-step-scenario__grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4, 16px);
}

@media (max-width: 720px) {
  .gm-step-scenario__grid {
    grid-template-columns: 1fr;
  }
}

.gm-step-scenario__card {
  text-align: left;
  background: var(--bg-card);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md, 8px);
  padding: var(--space-4, 16px);
  cursor: pointer;
  transition: all 200ms var(--ease-out-quint);
  color: inherit;
  font-family: inherit;
  position: relative;
  overflow: hidden;
  clip-path: var(--clip-corner-sm);
}

.gm-step-scenario__card::before {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(
    135deg,
    transparent 0%,
    var(--brand-primary-soft, rgba(97, 92, 237, 0.08)) 50%,
    transparent 100%
  );
  opacity: 0;
  transition: opacity 200ms var(--ease-out-quint);
  pointer-events: none;
}

.gm-step-scenario__card:hover {
  border-color: var(--brand-primary, #00e5ff);
  transform: translateY(-2px);
  box-shadow: var(--glow-primary-soft);
}

.gm-step-scenario__card:hover::before {
  opacity: 1;
}

.gm-step-scenario__card.is-selected {
  border-color: var(--brand-primary, #00e5ff);
  background: var(--brand-primary-soft, rgba(0, 229, 255, 0.1));
  box-shadow: var(--glow-primary-soft);
}

.gm-step-scenario__card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-2, 8px);
}

.gm-step-scenario__icon {
  font-size: 24px;
  color: var(--brand-primary, #00e5ff);
}

.gm-step-scenario__check {
  color: var(--brand-primary, #00e5ff);
}

.gm-step-scenario__card-title {
  font-family: var(--font-cn, 'PingFang SC', sans-serif);
  font-size: var(--fs-md, 14px);
  font-weight: var(--fw-semibold, 600);
  color: var(--text-primary);
  margin-bottom: 4px;
}

.gm-step-scenario__card-desc {
  font-family: var(--font-cn, 'PingFang SC', sans-serif);
  font-size: var(--fs-xs, 11px);
  color: var(--text-secondary);
  line-height: var(--lh-normal, 1.5);
  margin-bottom: var(--space-2, 8px);
}

.gm-step-scenario__card-preview {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--space-2, 8px);
  background: var(--bg-base, rgba(0, 0, 0, 0.3));
  border-radius: var(--radius-sm, 4px);
  font-family: var(--font-cn, 'PingFang SC', sans-serif);
  font-size: 11px;
}

.gm-step-scenario__card-preview-label {
  color: var(--text-muted);
  letter-spacing: 0.05em;
  text-transform: uppercase;
}

.gm-step-scenario__card-preview-text {
  color: var(--text-secondary);
  line-height: var(--lh-normal, 1.5);
}

.gm-step-scenario__hint {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2, 8px);
  align-self: center;
  padding: var(--space-2, 8px) var(--space-3, 12px);
  background: var(--bg-card);
  border: 1px dashed var(--border-default);
  border-radius: var(--radius-md, 8px);
  font-family: var(--font-cn, 'PingFang SC', sans-serif);
  font-size: var(--fs-xs, 11px);
  color: var(--text-muted);
}

.gm-step-scenario__hint .el-icon {
  color: var(--brand-primary, #00e5ff);
}
</style>
