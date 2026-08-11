<script setup lang="ts">
/**
 * ModelSwitcher · LLM 模型切换按钮（v1.4.0 新增）
 *
 * 下拉选择：DashScope (qwen-plus/turbo) + DeepSeek (chat/coder)
 * 实时同步后端运行时模型（session 级）
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useModelStore } from '@/stores/modelStore'
import type { ModelInfo } from '@/types'

const modelStore = useModelStore()
const isOpen = ref(false)

onMounted(() => {
  modelStore.init()
})

// V1.7.0 M-2：会话级模型绑定——展示当前激活会话的生效模型（无激活会话走全局）
const isSessionScoped = computed(() => !!modelStore.activeThreadId)
const activeSessionModelId = computed<string | null>(() => modelStore.activeSessionModel())

const currentLabel = computed(() => modelStore.currentInfo?.label || modelStore.current || '加载中')
const providerLabel = computed(() => {
  const p = modelStore.currentInfo?.provider
  if (p === 'dashscope') return '千问'
  if (p === 'deepseek') return 'DeepSeek'
  return ''
})
const providerColor = computed(() => {
  const p = modelStore.currentInfo?.provider
  if (p === 'dashscope') return '#615ced'  // 紫色
  if (p === 'deepseek') return '#1c64f2'  // 蓝色
  return '#888'
})

async function handleSelect(m: ModelInfo) {
  const active = activeSessionModelId.value ?? modelStore.current
  if (m.id === active) {
    isOpen.value = false
    return
  }
  try {
    await modelStore.switchTo(m.id)
    isOpen.value = false
  } catch (e) {
    // store 内已记 error
    console.error('Model switch failed:', e)
  }
}

function handleClickOutside(e: MouseEvent) {
  const target = e.target as HTMLElement
  if (!target.closest('.gm-model-switcher')) {
    isOpen.value = false
  }
}

onMounted(() => {
  document.addEventListener('click', handleClickOutside)
})

// F3 修复（QA F3 P1）：组件挂载于 ChatView 欢迎区（v-if="!messages.length"），
// 反复卸载/重挂会累积全局 click 监听 → 必须成对移除，防监听泄漏 + 重复触发。
onUnmounted(() => {
  document.removeEventListener('click', handleClickOutside)
})
</script>

<template>
  <div class="gm-model-switcher">
    <button
      type="button"
      class="gm-model-switcher__trigger"
      :class="{ 'gm-model-switcher__trigger--active': isOpen, 'gm-model-switcher__trigger--loading': modelStore.switching }"
      :disabled="modelStore.switching || !modelStore.loaded"
      @click.stop="isOpen = !isOpen"
    >
      <span class="gm-model-switcher__provider" :style="{ color: providerColor }">
        {{ providerLabel }}
      </span>
      <span class="gm-model-switcher__label">{{ currentLabel }}</span>
      <svg width="10" height="6" viewBox="0 0 10 6" fill="none" class="gm-model-switcher__chevron">
        <path d="M1 1L5 5L9 1" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
    </button>

    <transition name="gm-fade">
      <div v-if="isOpen" class="gm-model-switcher__dropdown">
        <div class="gm-model-switcher__title">
          选择 LLM 模型{{ isSessionScoped ? '（当前会话）' : '' }}
        </div>
        <button
          v-for="m in modelStore.available"
          :key="m.id"
          type="button"
          class="gm-model-switcher__option"
          :class="{ 'gm-model-switcher__option--active': m.id === (activeSessionModelId ?? modelStore.current) }"
          :disabled="modelStore.switching"
          @click="handleSelect(m)"
        >
          <div class="gm-model-switcher__option-main">
            <span class="gm-model-switcher__option-provider" :style="{ color: m.provider === 'dashscope' ? '#615ced' : '#1c64f2' }">
              {{ m.provider === 'dashscope' ? '千问' : 'DeepSeek' }}
            </span>
            <span class="gm-model-switcher__option-label">{{ m.label }}</span>
            <span v-if="m.id === (activeSessionModelId ?? modelStore.current)" class="gm-model-switcher__option-check">✓</span>
          </div>
          <div class="gm-model-switcher__option-desc">{{ m.description }}</div>
        </button>
        <div v-if="modelStore.error" class="gm-model-switcher__error">
          ⚠️ {{ modelStore.error }}
        </div>
      </div>
    </transition>
  </div>
</template>

<style scoped lang="scss">
.gm-model-switcher {
  position: relative;
  display: inline-block;
  font-family: inherit;
}

.gm-model-switcher__trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  background: var(--gm-bg-elev, rgba(255, 255, 255, 0.05));
  border: 1px solid var(--gm-border, rgba(255, 255, 255, 0.12));
  border-radius: 999px;
  color: var(--gm-text-primary, #e5e7eb);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.18s ease;
  white-space: nowrap;
}

.gm-model-switcher__trigger:hover:not(:disabled) {
  background: var(--gm-bg-hover, rgba(255, 255, 255, 0.08));
  border-color: var(--gm-accent, #615ced);
}

.gm-model-switcher__trigger--active {
  border-color: var(--gm-accent, #615ced);
  box-shadow: 0 0 0 3px rgba(97, 92, 237, 0.15);
}

.gm-model-switcher__trigger--loading {
  opacity: 0.6;
  cursor: wait;
}

.gm-model-switcher__trigger:disabled {
  cursor: not-allowed;
}

.gm-model-switcher__provider {
  font-weight: 600;
  font-size: 12px;
  letter-spacing: 0.02em;
}

.gm-model-switcher__label {
  font-weight: 500;
}

.gm-model-switcher__chevron {
  margin-left: 2px;
  opacity: 0.6;
  transition: transform 0.18s ease;
}

.gm-model-switcher__trigger--active .gm-model-switcher__chevron {
  transform: rotate(180deg);
}

.gm-model-switcher__dropdown {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  min-width: 280px;
  background: var(--gm-bg-elev-2, rgba(20, 20, 30, 0.98));
  border: 1px solid var(--gm-border, rgba(255, 255, 255, 0.12));
  border-radius: 12px;
  padding: 6px;
  box-shadow: 0 10px 40px rgba(0, 0, 0, 0.3);
  /* P2-D（R-1f）：下拉与弹窗同层 var(--z-dialog) 会在弹窗打开时竞争 →
     改为 var(--z-dropdown)（100），严格低于弹窗（1000） */
  z-index: var(--z-dropdown);
  backdrop-filter: blur(20px);
}

.gm-model-switcher__title {
  padding: 8px 12px 4px;
  font-size: 11px;
  color: var(--gm-text-tertiary, #9ca3af);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-weight: 600;
}

.gm-model-switcher__option {
  display: block;
  width: 100%;
  padding: 8px 12px;
  background: transparent;
  border: none;
  border-radius: 8px;
  text-align: left;
  color: var(--gm-text-primary, #e5e7eb);
  cursor: pointer;
  transition: background 0.12s ease;
}

.gm-model-switcher__option:hover:not(:disabled) {
  background: var(--gm-bg-hover, rgba(255, 255, 255, 0.06));
}

.gm-model-switcher__option--active {
  background: var(--gm-bg-active, rgba(97, 92, 237, 0.15));
}

.gm-model-switcher__option:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.gm-model-switcher__option-main {
  display: flex;
  align-items: center;
  gap: 8px;
}

.gm-model-switcher__option-provider {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  min-width: 56px;
}

.gm-model-switcher__option-label {
  flex: 1;
  font-size: 13px;
  font-weight: 500;
}

.gm-model-switcher__option-check {
  color: var(--gm-accent, #615ced);
  font-weight: 700;
}

.gm-model-switcher__option-desc {
  font-size: 11px;
  color: var(--gm-text-tertiary, #9ca3af);
  margin-top: 2px;
  margin-left: 64px;
}

.gm-model-switcher__error {
  padding: 8px 12px;
  margin-top: 6px;
  background: rgba(239, 68, 68, 0.1);
  border: 1px solid rgba(239, 68, 68, 0.3);
  border-radius: 6px;
  color: #fca5a5;
  font-size: 11px;
}

.gm-fade-enter-active, .gm-fade-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}

.gm-fade-enter-from, .gm-fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>