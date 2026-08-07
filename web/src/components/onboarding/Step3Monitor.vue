<template>
  <div class="gm-step-monitor">
    <div class="gm-step-monitor__intro">
      <h2 class="gm-step-monitor__title">{{ content.title }}</h2>
      <p class="gm-step-monitor__desc">
        <!--
          description 里需要高亮的词由 meta.highlights 指定，
          这里把整段文案切成 [普通文本, 高亮词, 普通文本, ...] 交替渲染，
          既保留原视觉（<strong> 主色），又不引入 v-html（防 XSS）。
        -->
        <template v-for="(seg, idx) in descSegments" :key="idx">
          <strong v-if="seg.highlight">{{ seg.text }}</strong>
          <template v-else>{{ seg.text }}</template>
        </template>
      </p>
    </div>

    <div class="gm-step-monitor__bullets">
      <div
        v-for="bullet in content.bullets"
        :key="bullet.title"
        class="gm-step-monitor__bullet"
      >
        <el-icon>
          <component :is="iconFor(bullet.icon)" />
        </el-icon>
        <div>
          <div class="gm-step-monitor__bullet-title">{{ bullet.title }}</div>
          <div class="gm-step-monitor__bullet-desc">
            {{ bullet.description }}
          </div>
        </div>
      </div>
    </div>

    <div class="gm-step-monitor__cta">
      <el-button
        type="primary"
        size="large"
        :icon="Promotion"
        @click="onGoMonitor"
      >
        {{ content.cta.label }}
      </el-button>
      <span class="gm-step-monitor__cta-hint">
        {{ content.cta.hint }}
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * Step3Monitor · 引导 wizard 第 3 步
 * 仅给个跳转按钮（不强制 redirect，让用户自己点）
 * 完成由 OnboardingView 底部"完成，开始体验"统一收口
 *
 * V1.6：标题 / 说明 / 3 条要点 / CTA 全部改由知识库下发
 * （文档 §5.1 → `useFeatureIntro().step3`），API 不可用时回落
 * `STEP3_FALLBACK`（与改造前逐字一致），视觉与交互零变化。
 */
import { computed, onMounted } from 'vue'
import {
  Monitor,
  DataAnalysis,
  WarningFilled,
  Promotion,
  Document,
  Connection,
  Reading,
  Switch,
  FirstAidKit,
} from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { useFeatureIntro } from '@/composables/useFeatureIntro'

const router = useRouter()

const { step3, load } = useFeatureIntro()

onMounted(() => {
  void load()
})

/** 第 3 步文案（composable 已保证非空：拿不到就是兜底常量） */
const content = computed(() => step3.value)

/** Element Plus 图标名 → 组件（后端只下发字符串名） */
const ICON_MAP: Record<string, unknown> = {
  Monitor,
  DataAnalysis,
  WarningFilled,
  Document,
  Connection,
  Reading,
  Switch,
  FirstAidKit,
}

function iconFor(name: string) {
  return ICON_MAP[name] ?? Monitor
}

/** 描述文本分段（highlight=true 的段用 <strong> 渲染） */
interface DescSegment {
  text: string
  highlight: boolean
}

/**
 * 把 description 按 highlights 词表切分为交替片段。
 *
 * 逐词扫描（而非正则），避免高亮词中含正则元字符时误匹配。
 */
const descSegments = computed<DescSegment[]>(() => {
  const text = content.value.description
  const words = content.value.highlights.filter((w) => w.length > 0)
  if (words.length === 0) return [{ text, highlight: false }]

  const segments: DescSegment[] = []
  let cursor = 0
  while (cursor < text.length) {
    // 在当前位置之后，找最靠前的一个高亮词
    let hitIndex = -1
    let hitWord = ''
    for (const word of words) {
      const idx = text.indexOf(word, cursor)
      if (idx !== -1 && (hitIndex === -1 || idx < hitIndex)) {
        hitIndex = idx
        hitWord = word
      }
    }
    if (hitIndex === -1) {
      segments.push({ text: text.slice(cursor), highlight: false })
      break
    }
    if (hitIndex > cursor) {
      segments.push({ text: text.slice(cursor, hitIndex), highlight: false })
    }
    segments.push({ text: hitWord, highlight: true })
    cursor = hitIndex + hitWord.length
  }
  return segments
})

function onGoMonitor(): void {
  // 进入实时监控后，自动开启 ?tour=xxx 单页 tour（OnboardingTour 组件会捕获）
  const { path, tour } = content.value.cta
  router.push({ path, query: { tour } })
}

// 不发 emit（OnboardingView 完成由底部按钮统一处理）
</script>

<style scoped>
.gm-step-monitor {
  display: flex;
  flex-direction: column;
  gap: var(--space-5, 20px);
  width: 100%;
}

.gm-step-monitor__intro {
  text-align: center;
}

.gm-step-monitor__title {
  margin: 0 0 var(--space-2, 8px);
  font-family: var(--font-cn, 'PingFang SC', sans-serif);
  font-size: var(--fs-xl, 20px);
  font-weight: var(--fw-bold, 700);
  color: var(--text-primary);
  letter-spacing: 0.05em;
}

.gm-step-monitor__desc {
  margin: 0;
  font-family: var(--font-cn, 'PingFang SC', sans-serif);
  font-size: var(--fs-sm, 12px);
  color: var(--text-secondary);
  line-height: var(--lh-loose, 1.7);
}

.gm-step-monitor__desc strong {
  color: var(--brand-primary, #00e5ff);
  font-weight: var(--fw-semibold, 600);
}

.gm-step-monitor__bullets {
  display: flex;
  flex-direction: column;
  gap: var(--space-3, 12px);
}

.gm-step-monitor__bullet {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3, 12px);
  padding: var(--space-3, 12px);
  background: var(--bg-card);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-md, 8px);
  transition: all 200ms var(--ease-out-quint);
}

.gm-step-monitor__bullet:hover {
  border-color: var(--brand-primary, #00e5ff);
  box-shadow: var(--glow-primary-soft);
}

.gm-step-monitor__bullet .el-icon {
  font-size: 20px;
  color: var(--brand-primary, #00e5ff);
  flex-shrink: 0;
  margin-top: 2px;
}

.gm-step-monitor__bullet-title {
  font-family: var(--font-cn, 'PingFang SC', sans-serif);
  font-size: var(--fs-md, 14px);
  font-weight: var(--fw-semibold, 600);
  color: var(--text-primary);
  margin-bottom: 2px;
}

.gm-step-monitor__bullet-desc {
  font-family: var(--font-cn, 'PingFang SC', sans-serif);
  font-size: var(--fs-xs, 11px);
  color: var(--text-secondary);
  line-height: var(--lh-normal, 1.5);
}

.gm-step-monitor__cta {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2, 8px);
  margin-top: var(--space-4, 16px);
}

.gm-step-monitor__cta-hint {
  font-family: var(--font-cn, 'PingFang SC', sans-serif);
  font-size: 11px;
  color: var(--text-muted);
  letter-spacing: 0.04em;
}
</style>
