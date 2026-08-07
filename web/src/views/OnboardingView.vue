<template>
  <div class="gm-onboarding-view">
    <div class="gm-onboarding-shell">
      <!-- 顶部进度条 -->
      <header class="gm-onboarding-shell__head">
        <div class="gm-onboarding-shell__brand">
          <LogoHorizontal :size="28" />
          <span class="gm-onboarding-shell__brand-name">新调度员引导</span>
        </div>
        <div class="gm-onboarding-shell__progress" aria-label="引导步骤进度">
          <div
            v-for="n in TOTAL_STEPS"
            :key="n"
            class="gm-onboarding-shell__step-dot"
            :class="{ 'is-active': n === currentStep, 'is-done': n < currentStep }"
            :aria-current="n === currentStep ? 'step' : undefined"
          >
            <span class="gm-onboarding-shell__step-num">{{ n }}</span>
            <span class="gm-onboarding-shell__step-label">{{ STEP_LABELS[n - 1] }}</span>
          </div>
        </div>
        <button
          v-if="hasOnboarded && !forceMode"
          type="button"
          class="gm-onboarding-shell__skip"
          aria-label="跳过引导"
          @click="onSkip"
        >
          <el-icon><Close /></el-icon>
          <span>跳过</span>
        </button>
      </header>

      <!-- 步骤主体（单页切换，不污染历史栈） -->
      <main class="gm-onboarding-shell__body">
        <Transition :name="transitionName" mode="out-in">
          <component :is="currentStepComponent" :key="currentStep" @navigate="onNavigate" />
        </Transition>
      </main>

      <!-- 底部控制条 -->
      <footer class="gm-onboarding-shell__foot">
        <div class="gm-onboarding-shell__foot-meta">
          <span class="gm-onboarding-shell__foot-step">
            第 {{ currentStep }} / {{ TOTAL_STEPS }} 步
          </span>
          <span v-if="scenarioId" class="gm-onboarding-shell__foot-scenario">
            场景：{{ currentScenarioLabel }}
          </span>
        </div>

        <div class="gm-onboarding-shell__foot-actions">
          <el-button
            v-if="currentStep > 1"
            type="default"
            plain
            :icon="ArrowLeft"
            aria-label="上一步"
            @click="onPrev"
          >
            上一步
          </el-button>
          <el-button
            v-if="currentStep < TOTAL_STEPS"
            type="primary"
            :disabled="currentStep === 1 && !scenarioId"
            :icon="ArrowRight"
            :icon-position="isRtl ? 'left' : 'right'"
            aria-label="下一步"
            @click="onNext"
          >
            下一步
          </el-button>
          <el-button
            v-else
            type="success"
            :icon="Check"
            aria-label="完成引导"
            @click="onFinish"
          >
            完成，开始体验
          </el-button>
        </div>
      </footer>
    </div>

    <TechBackground :intensity="'low'" :show-glow="true" />
  </div>
</template>

<script setup lang="ts">
/**
 * OnboardingView · 3 步新手引导 wizard 主页（v1.5.0 P0-4）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构（v1.5.0 §5 T04）：
 *   1. 单页 + Step 子组件切换，不进 step 路由（不污染 history）
 *   2. 状态全部来自 useOnboarding() / useChatStore()
 *   3. 路由守卫会兜底跳转：首次未完成 → /onboarding
 *   4. `?force=1` query 允许"重看"入口跳过 hasOnboarded 拦截
 *   5. 完成 → router.replace('/?tour=chat') 主动开启一轮单页 tour
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, ArrowRight, Check, Close } from '@element-plus/icons-vue'
import LogoHorizontal from '@/components/brand/LogoHorizontal.vue'
import TechBackground from '@/components/background/TechBackground.vue'
import Step1Scenario from '@/components/onboarding/Step1Scenario.vue'
import Step2Dialogue from '@/components/onboarding/Step2Dialogue.vue'
import Step3Monitor from '@/components/onboarding/Step3Monitor.vue'
import { useOnboarding } from '@/composables/useOnboarding'
import { ONBOARDING_SCENARIOS } from '@/types/theme'
import type { OnboardingScenarioId } from '@/types/theme'

const TOTAL_STEPS = 3
const STEP_LABELS = ['选场景', '试对话', '看监控']

const route = useRoute()
const router = useRouter()

const {
  hasOnboarded,
  currentStep,
  scenarioId,
  selectScenario,
  start,
  next,
  prev,
  complete,
} = useOnboarding()

/** force=1 query 触发"重看"模式：允许跳进 wizard 重复演练 */
const forceMode = computed(() => route.query.force === '1')

/** 当前步骤对应的子组件（key 不同触发 transition） */
const currentStepComponent = computed(() => {
  switch (currentStep.value) {
    case 1:
      return Step1Scenario
    case 2:
      return Step2Dialogue
    case 3:
    default:
      return Step3Monitor
  }
})

/** 步骤切换动画名（正向 / 反向） */
const transitionName = ref<'slide-next' | 'slide-prev'>('slide-next')
const isRtl = ref(false)

/** 当前选中场景的展示名称 */
const currentScenarioLabel = computed(() => {
  if (!scenarioId.value) return '未选择'
  return ONBOARDING_SCENARIOS.find((s) => s.id === scenarioId.value)?.title ?? '未选择'
})

/** Step 1 → Step 2 事件：子组件 emit('navigate', { scenarioId }) */
function onNavigate(payload: { scenarioId: OnboardingScenarioId }): void {
  if (payload?.scenarioId) {
    selectScenario(payload.scenarioId)
    // 选完后立即 next，加快节奏
    isRtl.value = false
    transitionName.value = 'slide-next'
    next()
  }
}

function onPrev(): void {
  isRtl.value = true
  transitionName.value = 'slide-prev'
  prev()
}

function onNext(): void {
  isRtl.value = false
  transitionName.value = 'slide-next'
  next()
}

function onFinish(): void {
  complete()
  // 完成后主动跳转 chat，并触发 ?tour=chat 让 OnboardingTour 组件接续引导
  router.replace({ path: '/', query: { tour: 'chat' } })
}

/** 跳过按钮（已完成的"重看"模式才显示） */
function onSkip(): void {
  router.replace({ path: '/' })
}

onMounted(() => {
  // 每次进入 wizard 都重置 step（保留 hasOnboarded 不动，让守卫仍生效）
  // 场景选择保留：有用户中途退出然后重新进入体验
  start()
})
</script>

<style scoped>
.gm-onboarding-view {
  position: relative;
  /* 本视图是 <el-main class="app-main"> 的 flex 子元素，
     .app-main 的可用高度为 calc(100vh - 60px)（Header 60px）且 overflow:hidden。
     因此这里必须继承父级可用高度（100%），而不是视口高度（100vh），
     否则内容按 100vh 排版，底部 60px 会被 .app-main 裁掉。 */
  height: 100%;
  min-height: 100%;
  display: flex;
  align-items: stretch;
  justify-content: center;
  background: var(--bg-base);
  padding: var(--space-6) var(--space-4);
}

.gm-onboarding-shell {
  position: relative;
  z-index: var(--z-base);
  width: 100%;
  max-width: 1080px;
  /* 全局 box-sizing:border-box（styles/reset.scss），且百分比高度以父级
     "内容盒"为基准 —— .gm-onboarding-view 的上下 padding 已被自动扣除，
     故此处用 100% 即可（若再写 calc(100% - var(--space-6) * 2) 会重复扣除
     一次 padding，导致 shell 底部凭空空出 48px）。 */
  max-height: 100%;
  background: var(--bg-elevated);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg, 12px);
  box-shadow: var(--shadow-modal, 0 12px 40px rgba(0, 0, 0, 0.55));
  display: flex;
  flex-direction: column;
  overflow: hidden;
  clip-path: var(--clip-corner-md);
}

/* ── 顶部进度条 ───────────────────────────── */
.gm-onboarding-shell__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--border-muted, rgba(255, 255, 255, 0.1));
  background: linear-gradient(
    90deg,
    transparent 0%,
    var(--brand-primary-soft, rgba(97, 92, 237, 0.08)) 50%,
    transparent 100%
  );
}

.gm-onboarding-shell__brand {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
}

.gm-onboarding-shell__brand-name {
  font-family: var(--font-cn);
  font-size: var(--fs-md, 14px);
  font-weight: var(--fw-semibold, 600);
  color: var(--text-secondary);
  letter-spacing: 0.08em;
}

.gm-onboarding-shell__progress {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex: 1;
  justify-content: center;
}

.gm-onboarding-shell__step-dot {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: 4px var(--space-3);
  border-radius: 999px;
  background: var(--bg-card);
  border: 1px solid var(--border-default);
  font-family: var(--font-cn, 'PingFang SC', sans-serif);
  font-size: var(--fs-xs, 11px);
  color: var(--text-muted);
  transition: all 200ms var(--ease-out-quint);
}

.gm-onboarding-shell__step-dot.is-done {
  background: var(--status-success-soft, rgba(0, 230, 118, 0.15));
  border-color: var(--status-success, #00e676);
  color: var(--status-success, #00e676);
}

.gm-onboarding-shell__step-dot.is-active {
  background: var(--brand-primary-soft, rgba(0, 229, 255, 0.15));
  border-color: var(--brand-primary, #00e5ff);
  color: var(--brand-primary, #00e5ff);
  box-shadow: 0 0 8px var(--brand-primary-soft);
}

.gm-onboarding-shell__step-num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.08);
  font-family: var(--font-mono, monospace);
  font-size: 10px;
  font-weight: var(--fw-bold, 700);
}

.gm-onboarding-shell__step-dot.is-active .gm-onboarding-shell__step-num {
  background: var(--brand-primary, #00e5ff);
  color: var(--text-inverse, #050b1a);
}

.gm-onboarding-shell__step-label {
  font-weight: var(--fw-medium, 500);
  letter-spacing: 0.04em;
}

.gm-onboarding-shell__skip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px var(--space-2, 8px);
  background: transparent;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm, 4px);
  color: var(--text-secondary);
  font-family: var(--font-cn, 'PingFang SC', sans-serif);
  font-size: var(--fs-xs, 11px);
  cursor: pointer;
  transition: all 200ms var(--ease-out-quint);
}

.gm-onboarding-shell__skip:hover {
  border-color: var(--status-danger, #ff4757);
  color: var(--status-danger, #ff4757);
}

/* ── 主体 ─────────────────────────────── */
.gm-onboarding-shell__body {
  flex: 1;
  /* min-height:0 让该 flex 子项可以收缩到内容高度以下，
     否则（默认 min-height:auto = 内容高度）会把 shell 撑破，
     head/foot 被挤出可视区。滚动交给 overflow-y:auto。 */
  min-height: 0;
  padding: var(--space-6, 24px);
  overflow-y: auto;
  overscroll-behavior: contain;
  position: relative;
  scrollbar-width: thin;
  scrollbar-color: var(--border-default) transparent;
}

/* WebKit 窄滚动条（暗色主题一致） */
.gm-onboarding-shell__body::-webkit-scrollbar {
  width: 6px;
}

.gm-onboarding-shell__body::-webkit-scrollbar-track {
  background: transparent;
}

.gm-onboarding-shell__body::-webkit-scrollbar-thumb {
  background: var(--border-default);
  border-radius: 999px;
}

.gm-onboarding-shell__body::-webkit-scrollbar-thumb:hover {
  background: var(--text-muted);
}

.gm-onboarding-shell__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-4) var(--space-5);
  border-top: 1px solid var(--border-muted, rgba(255, 255, 255, 0.1));
  background: var(--bg-base);
}

.gm-onboarding-shell__foot-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-family: var(--font-cn, 'PingFang SC', sans-serif);
  font-size: var(--fs-xs, 11px);
  color: var(--text-muted);
}

.gm-onboarding-shell__foot-step {
  font-weight: var(--fw-semibold, 600);
  color: var(--text-secondary);
  letter-spacing: 0.05em;
}

.gm-onboarding-shell__foot-scenario {
  font-family: var(--font-mono, monospace);
}

.gm-onboarding-shell__foot-actions {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2, 8px);
}

/* ── 步骤切换动画 ───────────────────────── */
.slide-next-enter-active,
.slide-next-leave-active,
.slide-prev-enter-active,
.slide-prev-leave-active {
  transition: opacity 220ms var(--ease-out-quint), transform 240ms var(--ease-out-quint);
}
.slide-next-enter-from {
  opacity: 0;
  transform: translateX(24px);
}
.slide-next-leave-to {
  opacity: 0;
  transform: translateX(-24px);
}
.slide-prev-enter-from {
  opacity: 0;
  transform: translateX(-24px);
}
.slide-prev-leave-to {
  opacity: 0;
  transform: translateX(24px);
}

/* ── 响应式 ───────────────────────────── */
@media (max-width: 768px) {
  .gm-onboarding-shell__head {
    flex-wrap: wrap;
  }
  .gm-onboarding-shell__progress {
    order: 3;
    width: 100%;
    margin-top: var(--space-2, 8px);
  }
  .gm-onboarding-shell__step-label {
    display: none;
  }
  .gm-onboarding-shell__body {
    padding: var(--space-4, 16px);
  }
  .gm-onboarding-shell__foot {
    flex-direction: column;
    align-items: stretch;
  }
  .gm-onboarding-shell__foot-actions {
    justify-content: flex-end;
  }
}
</style>
