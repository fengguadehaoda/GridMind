<template>
  <div class="gm-step-dialogue">
    <div class="gm-step-dialogue__intro">
      <h2 class="gm-step-dialogue__title">第二步 · 体验一次真实对话</h2>
      <p class="gm-step-dialogue__desc">
        下方 4 张卡片对应你刚才选的场景。任意点击都会<strong>真实发送</strong>消息到对话面板（演示模式），观察 LLM 推理过程、可编辑工具调用和 HITL 审批。
      </p>
    </div>

    <div class="gm-step-dialogue__shortcuts">
      <DemoShortcuts
        :shortcuts="localShortcuts"
        :loading="chatStore.loading"
        @send="onSend"
      />
    </div>

    <el-alert
      v-if="cooldownHint"
      type="warning"
      :closable="false"
      show-icon
      class="gm-step-dialogue__cooldown"
    >
      <template #title>
        <span>触发 5 秒冷却（防止 wizard 自动 send 4 次触发限流）</span>
      </template>
    </el-alert>

    <div class="gm-step-dialogue__metrics">
      <div class="gm-step-dialogue__metric">
        <span class="gm-step-dialogue__metric-label">对话条数</span>
        <span class="gm-step-dialogue__metric-value">{{ chatStore.messages.length }}</span>
      </div>
      <div class="gm-step-dialogue__metric">
        <span class="gm-step-dialogue__metric-label">线程 ID</span>
        <code class="gm-step-dialogue__metric-value mono">{{ shortThreadId }}</code>
      </div>
      <div class="gm-step-dialogue__metric">
        <span class="gm-step-dialogue__metric-label">连接状态</span>
        <span
          class="gm-step-dialogue__metric-value"
          :class="{ ok: !chatStore.loading, busy: chatStore.loading }"
        >
          {{ chatStore.loading ? '生成中…' : '可发送' }}
        </span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * Step2Dialogue · 引导 wizard 第 2 步
 *
 * 关键设计（架构 §5 T04 实现要点 #2 + 主理人决策 #4）：
 *   1. **真实发送**到 chatStore.sendMessage()，不能 mock
 *   2. chatStore.sendMessage 已内置 5s cooldown 防止 wizard 自动 4 次触发限流
 *   3. 4 张种子快捷方式复用 DemoShortcuts，仅替换场景化文案
 *   4. 反馈：消息条数 / threadId / loading 状态实时刷新
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import DemoShortcuts from '@/components/DemoShortcuts.vue'
import { useChatStore } from '@/stores/chatStore'
import { useFeatureIntro } from '@/composables/useFeatureIntro'
import type { OnboardingScenarioId } from '@/types/theme'
import type { DemoShortcut } from '@/types'

const emit = defineEmits<{
  (e: 'navigate', payload: { scenarioId: OnboardingScenarioId }): void
}>()

const chatStore = useChatStore()

/**
 * V1.6：场景来自知识库（与 Step1 共享同一份模块级缓存，不会重复请求）；
 * API 不可用时 composable 已自动回落本地兜底，此处无需再判空。
 */
const { scenarios, load } = useFeatureIntro()

onMounted(() => {
  void load()
})

/** 4 个引导场景的真实快捷指令（用场景的 starterMessage 作为 message） */
const localShortcuts = computed<DemoShortcut[]>(() =>
  scenarios.value.map((sc) => ({
    label: sc.title,
    icon: sc.icon,
    message: sc.starterMessage,
    description: sc.description,
  })),
)

/** 最近一次发送是否被 cooldown 拦截（用于顶部 alert 提示） */
const cooldownHint = ref(false)
let cooldownHintTimer: ReturnType<typeof setTimeout> | null = null

/** 短 threadId（前 8 位） */
const shortThreadId = computed(() => chatStore.threadId.slice(0, 12))

async function onSend(message: string): Promise<void> {
  if (chatStore.loading) {
    // chatStore.sendMessage 内部会因 cooldown 静默拒绝；UI 给一个友好提示
    cooldownHint.value = true
    if (cooldownHintTimer) clearTimeout(cooldownHintTimer)
    cooldownHintTimer = setTimeout(() => {
      cooldownHint.value = false
    }, 4000)
    return
  }
  // 不通过 input 直接走 store.sendMessage（更纯粹，绕开 inputText 重置）
  await chatStore.sendMessage(message)
  ElMessage.success({
    message: '已发送到对话面板，可在第 3 步前往"实时监控"查看异常检测结果',
    duration: 2400,
  })
}

// emit 不必实现（Step 2 不触发 navigate，由底部"下一步"按钮推进）
void emit
</script>

<style scoped>
.gm-step-dialogue {
  display: flex;
  flex-direction: column;
  gap: var(--space-5, 20px);
  width: 100%;
}

.gm-step-dialogue__intro {
  text-align: center;
}

.gm-step-dialogue__title {
  margin: 0 0 var(--space-2, 8px);
  font-family: var(--font-cn, 'PingFang SC', sans-serif);
  font-size: var(--fs-xl, 20px);
  font-weight: var(--fw-bold, 700);
  color: var(--text-primary);
  letter-spacing: 0.05em;
}

.gm-step-dialogue__desc {
  margin: 0;
  font-family: var(--font-cn, 'PingFang SC', sans-serif);
  font-size: var(--fs-sm, 12px);
  color: var(--text-secondary);
  line-height: var(--lh-loose, 1.7);
}

.gm-step-dialogue__desc strong {
  color: var(--brand-primary, #00e5ff);
  font-weight: var(--fw-semibold, 600);
}

.gm-step-dialogue__shortcuts {
  display: flex;
  justify-content: center;
}

.gm-step-dialogue__cooldown {
  font-family: var(--font-cn, 'PingFang SC', sans-serif);
}

.gm-step-dialogue__metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-3, 12px);
}

@media (max-width: 720px) {
  .gm-step-dialogue__metrics {
    grid-template-columns: 1fr;
  }
}

.gm-step-dialogue__metric {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--space-3, 12px);
  background: var(--bg-card);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm, 4px);
}

.gm-step-dialogue__metric-label {
  font-family: var(--font-cn, 'PingFang SC', sans-serif);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-muted);
}

.gm-step-dialogue__metric-value {
  font-family: var(--font-mono, monospace);
  font-size: var(--fs-md, 14px);
  font-weight: var(--fw-semibold, 600);
  color: var(--text-primary);
}

.gm-step-dialogue__metric-value.ok {
  color: var(--status-success, #00e676);
}

.gm-step-dialogue__metric-value.busy {
  color: var(--status-warning, #ffb300);
}

.gm-step-dialogue__metric-value.mono {
  font-size: var(--fs-xs, 11px);
  word-break: break-all;
}
</style>
