<template>
  <div class="chat-view">
    <!-- 科技感背景层（最底） -->
    <TechBackground intensity="low" :show-glow="true" />

    <!-- CRT 扫描线（仅暗主题可见） -->
    <ScanlineOverlay :opacity="0.4" :speed="8" />

    <!-- 消息列表 -->
    <div ref="scrollRef" class="message-list">
      <!-- 空白引导 -->
      <div v-if="!messages.length" class="welcome">
        <div class="welcome-illustration">
          <svg width="120" height="120" viewBox="0 0 120 120" fill="none" aria-hidden="true">
            <polygon
              points="60,8 104,32 104,72 60,96 16,72 16,32"
              fill="none"
              stroke="var(--brand-primary)"
              stroke-width="1.5"
              stroke-opacity="0.5"
            />
            <polygon
              points="60,20 92,38 92,68 60,86 28,68 28,38"
              fill="var(--brand-primary-soft)"
              stroke="var(--brand-primary)"
              stroke-width="1"
              stroke-opacity="0.4"
            />
            <path
              d="M60 32 L78 64 L60 76 L42 64 Z"
              fill="var(--brand-primary)"
            />
            <circle cx="60" cy="56" r="4" fill="var(--brand-accent)" />
            <circle cx="60" cy="8" r="2.5" fill="var(--brand-primary)" />
            <circle cx="104" cy="32" r="2.5" fill="var(--brand-primary)" fill-opacity="0.6" />
            <circle cx="104" cy="72" r="2.5" fill="var(--brand-accent)" />
            <circle cx="60" cy="96" r="2.5" fill="var(--brand-primary)" fill-opacity="0.6" />
            <circle cx="16" cy="72" r="2.5" fill="var(--brand-primary)" fill-opacity="0.6" />
            <circle cx="16" cy="32" r="2.5" fill="var(--brand-primary)" />
          </svg>
        </div>
        <h2 class="welcome-title">灵枢电网</h2>
        <p class="welcome-sub">GridMind · 在下方输入问题或点击快捷指令开始演示</p>

        <DemoShortcuts
          :shortcuts="store.demoShortcuts"
          :loading="store.loading"
          @send="onShortcutSend"
        />
      </div>

      <TransitionGroup name="slide-up">
        <MessageBubble
          v-for="msg in messages"
          :key="msg.id"
          :msg="msg"
        />
      </TransitionGroup>

      <div ref="bottomRef" />
    </div>

    <!-- 输入区 -->
    <div class="input-area">
      <div class="input-bar">
        <el-input
          v-model="inputText"
          type="textarea"
          :rows="1"
          :autosize="{ minRows: 1, maxRows: 4 }"
          placeholder="输入电力运维相关的问题…"
          :disabled="store.loading"
          @keydown.enter.prevent="onSend"
          resize="none"
          class="chat-input"
        />
        <el-button
          type="primary"
          :icon="Promotion"
          :loading="store.loading"
          :disabled="!inputText.trim()"
          @click="onSend"
          class="send-btn"
        >
          发送
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, nextTick, watch, onMounted } from 'vue'
import { Promotion } from '@element-plus/icons-vue'
import { useChatStore } from '../stores/chatStore'
import MessageBubble from './MessageBubble.vue'
import DemoShortcuts from './DemoShortcuts.vue'
import TechBackground from './background/TechBackground.vue'
import ScanlineOverlay from './background/ScanlineOverlay.vue'

const store = useChatStore()
const inputText = ref('')
const scrollRef = ref<HTMLElement | null>(null)
const bottomRef = ref<HTMLElement | null>(null)

const messages = computed(() => store.messages)

function scrollToBottom() {
  nextTick(() => {
    bottomRef.value?.scrollIntoView({ behavior: 'smooth' })
  })
}

watch(() => store.messages.length, scrollToBottom)
watch(() => store.loading, scrollToBottom)
watch(() => store.streaming, scrollToBottom)

async function onSend() {
  const text = inputText.value.trim()
  if (!text || store.loading) return
  inputText.value = ''
  await store.sendMessage(text)
}

function onShortcutSend(message: string) {
  inputText.value = message
  onSend()
}

onMounted(() => {
  scrollToBottom()
})
</script>

<style scoped>
.chat-view {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

/* ── 消息列表 ───────────────────── */
.message-list {
  position: relative;
  z-index: var(--z-base);
  flex: 1;
  overflow-y: auto;
  padding: var(--space-5) 0;
}

/* ── 欢迎页 ─────────────────────── */
.welcome {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: var(--space-10) var(--space-8) var(--space-8);
  text-align: center;
}

.welcome-illustration {
  width: 120px;
  height: 120px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  margin-bottom: var(--space-6);
  filter: drop-shadow(0 0 20px var(--brand-primary-soft));
}

.welcome-illustration > svg {
  display: block;
  width: 100%;
  height: 100%;
  max-width: 120px;
  max-height: 120px;
}

.welcome-title {
  font-family: var(--font-cn);
  font-size: var(--fs-2xl);
  font-weight: var(--fw-bold);
  color: var(--text-primary);
  margin-bottom: var(--space-2);
  letter-spacing: 0.15em;
  transition: var(--theme-transition);
}

.welcome-sub {
  font-family: var(--font-cn);
  font-size: var(--fs-md);
  color: var(--text-muted);
  margin-bottom: var(--space-8);
  letter-spacing: 0.05em;
  transition: var(--theme-transition);
}

/* ── 输入区 ─────────────────────── */
.input-area {
  position: relative;
  z-index: var(--z-sticky);
  padding: var(--space-3) var(--space-5) var(--space-4);
  border-top: 1px solid var(--border-default);
  background: var(--bg-elevated);
  transition: var(--theme-transition);
}

.input-area::before {
  content: '';
  position: absolute;
  top: 0;
  left: 20%;
  right: 20%;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--brand-primary), transparent);
  opacity: 0.4;
}

.input-bar {
  display: flex;
  gap: var(--space-3);
  align-items: flex-end;
}

.chat-input {
  flex: 1;
}

.chat-input :deep(.el-textarea__inner) {
  border-radius: var(--radius-md);
  min-height: 42px;
  padding: 10px var(--space-4);
  font-size: var(--fs-md);
}

.send-btn {
  height: 42px;
  padding: 0 var(--space-5);
  font-size: var(--fs-md);
  font-weight: var(--fw-semibold);
  border-radius: var(--radius-md);
  clip-path: var(--clip-corner-sm);
}

/* ── 列表过渡动画 ───────────────── */
.slide-up-enter-active {
  transition: all var(--dur-slow) var(--ease-out-quint);
}
.slide-up-leave-active {
  transition: all var(--dur-fast) var(--ease-in-out-cubic);
}
.slide-up-enter-from {
  opacity: 0;
  transform: translateY(12px);
}
.slide-up-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}
</style>
