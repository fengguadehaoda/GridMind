/**
 * v1.5.1 T01 基础设施 · useFocusTrap composable
 *
 * F4 HITL 弹窗前置（架构 §1.4 + §3.4）专用：自实现 focus trap，
 * 不引入第三方库（保持依赖最小化；架构 §1.6.2 决策）。
 *
 * 关键点：
 *   - Tab / Shift+Tab 在容器内可聚焦元素间循环
 *   - Esc 键可由容器处理（通过自定义事件 'focus-trap-escape'，避免
 *     双向耦合）
 *   - 激活时记录原焦点元素；卸载时把焦点还回去（焦点回收）
 *   - 自动聚焦容器内第一个可聚焦元素（"仅批准"按钮通常是第一个）
 *
 * 用法（HitlEditDialog.vue）：
 *   ```vue
 *   <script setup lang="ts">
 *   import { ref } from 'vue'
 *   import { useFocusTrap } from '@/composables/useFocusTrap'
 *   const dialogRef = ref<HTMLElement | null>(null)
 *   const trap = useFocusTrap(dialogRef)
 *   </script>
 *   <template>
 *     <div ref="dialogRef" @focus-trap-escape="closeWithConfirm">
 *       <button>拒绝</button>
 *       <button>仅批准</button>
 *       <button>修改后批准</button>
 *       <button @click="closeWithConfirm">×</button>
 *     </div>
 *   </template>
 *   ```
 *
 * 作者：寇豆码（T01 工程师）
 * 参考：frontend-v151-architecture-2026-08-04.md §6.4
 */
import { nextTick, onMounted, onUnmounted, ref, type Ref } from 'vue'

/** 可聚焦元素 CSS 选择器（按 WAI-ARIA Authoring Practices） */
const FOCUSABLE_SELECTOR = [
  'a[href]',
  'area[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
  'audio[controls]',
  'video[controls]',
  'iframe',
  'object',
  'embed',
  '[contenteditable]:not([contenteditable="false"])',
].join(',')

export interface FocusTrapOptions {
  /** 容器模板 ref（必填）*/
  containerRef: Ref<HTMLElement | null>
  /** mount 后是否自动激活（默认 true）*/
  autoActivate?: boolean
  /** Esc 按下时是否自动 deactivate（默认 false；交由调用方决定）*/
  escapeDeactivates?: boolean
}

/**
 * 在指定容器内启用焦点 trap。
 *
 * 返回值提供 activate / deactivate / isActive 三个 API，便于
 * 父组件手动控制（v-if mount 时自动 activate，unmount 时自动 deactivate）。
 */
export function useFocusTrap(options: FocusTrapOptions) {
  const isActive = ref(false)
  /** 打开 trap 之前的活跃元素（用于焦点回收）*/
  let previouslyFocused: HTMLElement | null = null

  /**
   * 列出容器内**当前可见且未禁用**的可聚焦元素。
   *
   * 排除规则：
   *   - offsetParent === null → 元素或其祖先 display:none
   *   - tabindex=-1 → 显式不可聚焦
   *   - disabled 属性 → 原生禁用
   */
  function getFocusableElements(): HTMLElement[] {
    const container = options.containerRef.value
    if (!container) return []
    const all = Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
    return all.filter((el) => {
      // 排除不可见元素（display:none、visibility:hidden、隐藏在祖先中）
      if (el.offsetParent === null && el !== document.body) {
        // offsetParent 为 null 不一定不可见（如 position:fixed），用 getClientRects 双判
        const rects = el.getClientRects()
        if (rects.length === 0) return false
      }
      if (el.hasAttribute('disabled')) return false
      if (el.getAttribute('aria-hidden') === 'true') return false
      return true
    })
  }

  function handleKeydown(e: KeyboardEvent): void {
    if (!isActive.value) return
    if (e.key === 'Escape') {
      // 派发 'focus-trap-escape' 自定义事件，给容器组件处理关闭逻辑
      // （避免 composable 直接绑死"关弹窗"语义）
      options.containerRef.value?.dispatchEvent(
        new CustomEvent('focus-trap-escape', { bubbles: false, cancelable: true }),
      )
      if (options.escapeDeactivates) {
        deactivate()
      }
      return
    }
    if (e.key !== 'Tab') return
    const focusables = getFocusableElements()
    if (focusables.length === 0) {
      // 容器内无任何可聚焦元素 → 阻止 Tab 出逃
      e.preventDefault()
      return
    }
    const first = focusables[0]
    const last = focusables[focusables.length - 1]
    const active = document.activeElement as HTMLElement | null
    if (e.shiftKey) {
      if (active === first || !focusables.includes(active!)) {
        e.preventDefault()
        last.focus()
      }
    } else {
      if (active === last || !focusables.includes(active!)) {
        e.preventDefault()
        first.focus()
      }
    }
  }

  /**
   * 激活 trap：记录当前焦点 + 注册 keydown 监听器 + 异步聚焦首个元素。
   *
   * 异步聚焦（nextTick）：等容器渲染完再找元素，避免 v-if 刚 mount
   * 时 querySelectorAll 找不到 .hitl-dialog 内部按钮。
   */
  function activate(): void {
    if (isActive.value) return
    isActive.value = true
    previouslyFocused = (document.activeElement as HTMLElement) ?? null
    document.addEventListener('keydown', handleKeydown, true)
    nextTick(() => {
      const focusables = getFocusableElements()
      focusables[0]?.focus()
    })
  }

  /**
   * 解除 trap：移除 keydown 监听器 + 把焦点还给原触发元素。
   *
   * 多次 deactivate 安全：内部有 isActive 守卫。
   */
  function deactivate(): void {
    if (!isActive.value) return
    isActive.value = false
    document.removeEventListener('keydown', handleKeydown, true)
    // 焦点回收：直接调 focus 而非包 nextTick，避免 previouslyFocused 在
    // queue 期间被清空（架构 §6.4 同步实现）。
    const target = previouslyFocused
    previouslyFocused = null
    if (target && typeof target.focus === 'function') {
      target.focus()
    }
  }

  onMounted(() => {
    if (options.autoActivate !== false) {
      activate()
    }
  })

  onUnmounted(() => {
    deactivate()
  })

  return { isActive, activate, deactivate }
}
