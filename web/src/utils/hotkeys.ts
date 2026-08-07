/**
 * hotkeys.ts · 全局快捷键注册中心（v1.6.0 P1-1）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构决策（p1-iteration-architecture §1 P1-1 + §7 共享知识 #1）：
 *   - 单一 document keydown 监听器 + 注册表 + 优先级
 *   - ⌘K / ? / ESC / Alt+1-5 / ⌘1-5 / ↑↓ / Enter 统一管理
 *   - 组件自注册、onUnmounted 自注销；禁止组件内直接 addEventListener
 *   - ESC 按优先级仲裁：命令面板(100) > 速查浮层(90) > Session drawer(80) > 其他(10)
 *
 * 用法：
 *   const un = registerHotkey({ id: 'palette-toggle', match: e => ..., priority: 50, handler: ... })
 *   onUnmounted(un)
 */

export interface HotkeyBinding {
  /** 全局唯一 id（注册时覆盖同 id 旧绑定） */
  id: string
  /** 按键（如 'k' / '?' / 'Escape'）；与 match 二选一，优先 match */
  key?: string
  ctrl?: boolean
  meta?: boolean
  shift?: boolean
  alt?: boolean
  /** 自定义匹配函数（覆盖 key/modifiers） */
  match?: (e: KeyboardEvent) => boolean
  /** 冲突仲裁：同一次 keydown 命中多个绑定，取优先级最高者 */
  priority?: number
  /** 命中后是否 preventDefault */
  preventDefault?: boolean
  /** 派发时实时判断是否可用（如"仅面板打开时消费 ESC"） */
  enabled?: () => boolean
  handler: (e: KeyboardEvent) => void
}

const registry = new Map<string, HotkeyBinding>()

let initialized = false

/** 判断事件目标是否在可编辑控件内（输入框 / 文本域 / contenteditable） */
function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) return true
  if (target.isContentEditable) return true
  return false
}

function matches(binding: HotkeyBinding, e: KeyboardEvent): boolean {
  if (binding.match) return binding.match(e)
  if (!binding.key) return false
  if (e.key !== binding.key) return false
  // 防御：合成事件可能缺少修饰符字段，按 false 处理
  const ctrl = e.ctrlKey ?? false
  const meta = e.metaKey ?? false
  const shift = e.shiftKey ?? false
  const alt = e.altKey ?? false
  if (ctrl !== !!binding.ctrl) return false
  if (meta !== !!binding.meta) return false
  if (shift !== !!binding.shift) return false
  if (alt !== !!binding.alt) return false
  return true
}

function onKeydown(e: KeyboardEvent): void {
  const candidates: HotkeyBinding[] = []
  for (const binding of registry.values()) {
    if (binding.enabled && !binding.enabled()) continue
    if (matches(binding, e)) candidates.push(binding)
  }
  if (candidates.length === 0) return

  candidates.sort((a, b) => (b.priority ?? 0) - (a.priority ?? 0))
  const chosen = candidates[0]!
  if (chosen.preventDefault) e.preventDefault()
  chosen.handler(e)
}

function ensureInit(): void {
  if (initialized || typeof document === 'undefined') return
  document.addEventListener('keydown', onKeydown)
  initialized = true
}

/**
 * 注册一个全局快捷键绑定。
 * @returns 注销函数（组件 onUnmounted 调用）
 */
export function registerHotkey(binding: HotkeyBinding): () => void {
  registry.set(binding.id, binding)
  ensureInit()
  return () => {
    registry.delete(binding.id)
  }
}

/** 注销指定 id 的绑定（幂等） */
export function unregisterHotkey(id: string): void {
  registry.delete(id)
}

/** 判断当前 keydown 事件是否命中"全局无修饰键单键"类快捷键（用于 ? 的输入框守卫） */
export function isPlainKeyEvent(e: KeyboardEvent): boolean {
  return !e.metaKey && !e.ctrlKey && !e.altKey
}

export { isEditableTarget }

/** 预定义 ESC 优先级常量（共享知识 #1 仲裁顺序） */
export const ESC_PRIORITY = {
  commandPalette: 100,
  shortcutsOverlay: 90,
  sessionDrawer: 80,
  other: 10,
} as const
