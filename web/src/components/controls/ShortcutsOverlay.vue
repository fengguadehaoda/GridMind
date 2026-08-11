<script setup lang="ts">
/**
 * ShortcutsOverlay.vue · 快捷键速查浮层（v1.6.0 P1-2）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构决策（p1-iteration-architecture §1 P1-2 + §7 共享知识 #1）：
 *   - `?` 键任意页面唤起（输入框内不拦截），ESC 关闭
 *   - 经 hotkey 注册中心注册（? priority 10 / ESC priority 90）
 *   - 内容与帮助中心「快捷键总表」同源
 */
import { onMounted, onUnmounted, ref } from 'vue'
import { registerHotkey, isEditableTarget, isPlainKeyEvent, ESC_PRIORITY } from '@/utils/hotkeys'

const open = ref(false)

let unregisterQuestion: (() => void) | null = null
let unregisterEsc: (() => void) | null = null

function openOverlay(): void {
  open.value = true
}

function closeOverlay(): void {
  open.value = false
}

onMounted(() => {
  unregisterQuestion = registerHotkey({
    id: 'shortcuts-overlay-toggle',
    match: (e) => {
      if (e.key !== '?' || !isPlainKeyEvent(e)) return false
      // 输入框内不拦截（避免聊天输入 "?" 误触）
      if (isEditableTarget(e.target)) return false
      return true
    },
    priority: 10,
    preventDefault: true,
    handler: openOverlay,
  })
  unregisterEsc = registerHotkey({
    id: 'shortcuts-overlay-esc',
    key: 'Escape',
    priority: ESC_PRIORITY.shortcutsOverlay,
    preventDefault: true,
    enabled: () => open.value,
    handler: closeOverlay,
  })
})

onUnmounted(() => {
  unregisterQuestion?.()
  unregisterEsc?.()
})
</script>

<template>
  <Teleport to="body">
    <Transition name="gm-overlay-fade">
      <div
        v-if="open"
        class="gm-shortcuts-overlay"
        data-test="shortcuts-overlay"
        @click.self="closeOverlay"
      >
        <div class="gm-shortcuts-overlay__panel" role="dialog" aria-modal="true" aria-label="快捷键速查">
          <div class="gm-shortcuts-overlay__head">
            <span class="gm-shortcuts-overlay__title">快捷键速查</span>
            <button
              type="button"
              class="gm-shortcuts-overlay__close"
              aria-label="关闭速查浮层"
              @click="closeOverlay"
            >×</button>
          </div>

          <div class="gm-shortcuts-overlay__body">
            <section class="gm-shortcuts-overlay__group">
              <h4 class="gm-shortcuts-overlay__group-title">全局</h4>
              <ul class="gm-shortcuts-overlay__list">
                <li class="gm-shortcuts-overlay__row">
                  <span class="gm-shortcuts-overlay__desc">打开命令面板</span>
                  <span class="gm-shortcuts-overlay__keys"><kbd>⌘K</kbd><i>/</i><kbd>Ctrl+K</kbd></span>
                </li>
                <li class="gm-shortcuts-overlay__row">
                  <span class="gm-shortcuts-overlay__desc">打开本速查浮层</span>
                  <span class="gm-shortcuts-overlay__keys"><kbd>?</kbd></span>
                </li>
                <li class="gm-shortcuts-overlay__row">
                  <span class="gm-shortcuts-overlay__desc">关闭 / 返回</span>
                  <span class="gm-shortcuts-overlay__keys"><kbd>ESC</kbd></span>
                </li>
              </ul>
            </section>

            <section class="gm-shortcuts-overlay__group">
              <h4 class="gm-shortcuts-overlay__group-title">路由直达</h4>
              <p class="gm-shortcuts-overlay__note">Windows / Linux 用 Ctrl+Shift+数字 · Mac 用 ⌘⇧+数字（行为一致）</p>
              <ul class="gm-shortcuts-overlay__list">
                <li class="gm-shortcuts-overlay__row">
                  <span class="gm-shortcuts-overlay__desc">智能对话</span>
                  <span class="gm-shortcuts-overlay__keys"><kbd>Ctrl+Shift+5</kbd></span>
                </li>
                <li class="gm-shortcuts-overlay__row">
                  <span class="gm-shortcuts-overlay__desc">实时监控</span>
                  <span class="gm-shortcuts-overlay__keys"><kbd>Ctrl+Shift+1</kbd></span>
                </li>
                <li class="gm-shortcuts-overlay__row">
                  <span class="gm-shortcuts-overlay__desc">灰度面板</span>
                  <span class="gm-shortcuts-overlay__keys"><kbd>Ctrl+Shift+2</kbd></span>
                </li>
                <li class="gm-shortcuts-overlay__row">
                  <span class="gm-shortcuts-overlay__desc">HITL 审计</span>
                  <span class="gm-shortcuts-overlay__keys"><kbd>Ctrl+Shift+3</kbd></span>
                </li>
                <li class="gm-shortcuts-overlay__row">
                  <span class="gm-shortcuts-overlay__desc">系统总览</span>
                  <span class="gm-shortcuts-overlay__keys"><kbd>Ctrl+Shift+4</kbd></span>
                </li>
              </ul>
            </section>

            <section class="gm-shortcuts-overlay__group">
              <h4 class="gm-shortcuts-overlay__group-title">命令面板内</h4>
              <ul class="gm-shortcuts-overlay__list">
                <li class="gm-shortcuts-overlay__row">
                  <span class="gm-shortcuts-overlay__desc">选择上 / 下一条</span>
                  <span class="gm-shortcuts-overlay__keys"><kbd>↑</kbd><i>/</i><kbd>↓</kbd></span>
                </li>
                <li class="gm-shortcuts-overlay__row">
                  <span class="gm-shortcuts-overlay__desc">执行选中命令</span>
                  <span class="gm-shortcuts-overlay__keys"><kbd>Enter</kbd></span>
                </li>
              </ul>
            </section>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped lang="scss">
.gm-shortcuts-overlay {
  position: fixed;
  inset: 0;
  /* P2-D（R-1f）：快捷键浮层按 ? 触发，应低于弹窗——弹窗打开时 ? 不应穿透盖住
     审批弹窗 → 由 var(--z-dialog)（1000）改为 var(--z-dropdown)（100）。
     浮层本体仍正常显示（居中面板 + ESC/点遮罩关闭行为不变） */
  z-index: var(--z-dropdown);
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-overlay);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
}

.gm-shortcuts-overlay__panel {
  width: min(520px, calc(100vw - 48px));
  max-height: min(640px, calc(100vh - 96px));
  overflow-y: auto;
  background: var(--bg-elevated);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-modal);
  transition: var(--theme-transition);
}

.gm-shortcuts-overlay__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--border-muted);
}

.gm-shortcuts-overlay__title {
  font-family: var(--font-cn);
  font-size: var(--fs-lg);
  font-weight: var(--fw-semibold);
  color: var(--text-primary);
  letter-spacing: 0.08em;
}

.gm-shortcuts-overlay__close {
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  color: var(--text-secondary);
  font-size: var(--fs-lg);
  cursor: pointer;
  transition: all var(--dur-fast) var(--ease-out-quint);
}

.gm-shortcuts-overlay__close:hover {
  color: var(--brand-primary);
  border-color: var(--brand-primary);
  box-shadow: var(--glow-primary-soft);
}

.gm-shortcuts-overlay__body {
  padding: var(--space-4) var(--space-5) var(--space-5);
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.gm-shortcuts-overlay__group-title {
  margin: 0 0 var(--space-3);
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  font-weight: var(--fw-semibold);
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.14em;
}

.gm-shortcuts-overlay__note {
  margin: -0.25rem 0 var(--space-2);
  font-family: var(--font-cn);
  font-size: var(--fs-xs);
  color: var(--text-muted);
}

.gm-shortcuts-overlay__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.gm-shortcuts-overlay__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--border-muted);
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  transition: var(--theme-transition);
}

.gm-shortcuts-overlay__desc {
  font-family: var(--font-cn);
  font-size: var(--fs-sm);
  color: var(--text-primary);
}

.gm-shortcuts-overlay__keys {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
}

.gm-shortcuts-overlay__keys i {
  font-style: normal;
  color: var(--text-muted);
  font-size: var(--fs-xs);
}

.gm-shortcuts-overlay__keys kbd {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 24px;
  height: 22px;
  padding: 0 var(--space-1);
  border: 1px solid var(--border-strong);
  border-bottom-width: 2px;
  border-radius: var(--radius-sm);
  background: var(--bg-input);
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: var(--fs-xs);
}

/* ── 过渡 ── */
.gm-overlay-fade-enter-active,
.gm-overlay-fade-leave-active {
  transition: opacity 0.18s var(--ease-out-quint);
}
.gm-overlay-fade-enter-from,
.gm-overlay-fade-leave-to {
  opacity: 0;
}
.gm-overlay-fade-enter-active .gm-shortcuts-overlay__panel,
.gm-overlay-fade-leave-active .gm-shortcuts-overlay__panel {
  transition: transform 0.18s var(--ease-out-quint);
}
.gm-overlay-fade-enter-from .gm-shortcuts-overlay__panel,
.gm-overlay-fade-leave-to .gm-shortcuts-overlay__panel {
  transform: scale(0.96) translateY(-8px);
}
</style>
