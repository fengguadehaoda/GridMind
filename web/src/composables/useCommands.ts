/**
 * useCommands.ts · 命令注册中心（v1.6.0 P1-1）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构决策（p1-iteration-architecture §1 P1-1 + §7 共享知识 #2）：
 *   - 命令面板是"插件式"入口：所有命令在 useCommands 注册，禁止在组件内写死
 *   - 路由命令 id 前缀 `route_`，操作命令 `action_`，上下文命令 `ctx_`
 *   - keywords 含中文 / 拼音首字母 / 英文：['监控','jk','monitor']
 *   - 5 路由 + 10 常用操作（默认方案，PRD §5 待确认 #1）
 *
 * 数据为模块级单例：CommandPalette 与其他模块共享同一注册表。
 */

import { ref } from 'vue'
import type { CommandItem, CommandGroup, CommandScope } from '@/types/theme'
import { useChatStore } from '@/stores/chatStore'
import { useThemeStore } from '@/stores/theme'
import { useDisplayStore } from '@/stores/display'
import { useReasoningStore } from '@/stores/reasoning'
import { useSessionStatsStore } from '@/stores/sessionStats'
import router from '@/router'
import { registerHotkey } from '@/utils/hotkeys'
import { ElMessage, ElMessageBox } from 'element-plus'
import { rewindSession as rewindSessionApi } from '@/api/chat'

/** 模块级共享命令表（单例） */
const commands = ref<CommandItem[]>([])
let registered = false

/** 5 个核心路由（PRD §3 P1-1） */
interface RouteCommandSpec {
  id: string
  title: string
  subtitle: string
  path: string
  /** 快捷键展示：['Ctrl+Shift','1']（Mac 用 ⌘⇧+数字，行为一致） */
  shortcut: string[]
  icon: string
  keywords: string[]
}

// 路由直达快捷键：Windows/Linux 用 Ctrl+Shift+数字，Mac 用 ⌘⇧+数字（与 Win 对齐的 Shift 组合）
//   （Ctrl+数字 被浏览器切标签页、Alt+数字 部分浏览器也切标签页，均不可用；Ctrl+Shift/⌘+Shift 无默认行为）
const ROUTE_COMMANDS: RouteCommandSpec[] = [
  {
    id: 'route_chat',
    title: '智能对话',
    subtitle: '对话式诊断 · 知识检索 · 高危确认',
    path: '/',
    shortcut: ['Ctrl+Shift', '5'],
    icon: 'ChatDotRound',
    keywords: ['对话', 'dh', 'chat', '首页'],
  },
  {
    id: 'route_monitor',
    title: '实时监控',
    subtitle: '设备健康评分 · 遥测趋势',
    path: '/monitor',
    shortcut: ['Ctrl+Shift', '1'],
    icon: 'Monitor',
    keywords: ['监控', 'jk', 'monitor', '设备'],
  },
  {
    id: 'route_grayscale',
    title: '灰度面板',
    subtitle: '双 backend 切流 · 拓扑可视化 · 方案对比',
    path: '/grayscale',
    shortcut: ['Ctrl+Shift', '2'],
    icon: 'Histogram',
    keywords: ['灰度', 'hd', 'grayscale', '切流'],
  },
  {
    id: 'route_audit',
    title: 'HITL 审计',
    subtitle: '高危审批记录 · 审计追踪',
    path: '/audit',
    shortcut: ['Ctrl+Shift', '3'],
    icon: 'Document',
    keywords: ['审计', 'sj', 'audit', 'hitl', '审批'],
  },
  {
    id: 'route_system',
    title: '系统总览',
    subtitle: '聚合指标 · 灰度状态 · 模型信息',
    path: '/system',
    shortcut: ['Ctrl+Shift', '4'],
    icon: 'DataBoard',
    keywords: ['系统', 'xt', 'system', '总览'],
  },
]

/** 命令工厂：路由命令 */
function makeRouteCommand(spec: RouteCommandSpec): CommandItem {
  return {
    id: spec.id,
    group: 'routes',
    scope: 'global',
    title: spec.title,
    subtitle: spec.subtitle,
    shortcut: spec.shortcut,
    icon: spec.icon,
    keywords: spec.keywords,
    action: () => {
      void router.push(spec.path)
    },
  }
}

/** 10 个常用操作（PRD §3 P1-1 默认方案） */
function makeActionCommands(): CommandItem[] {
  const chat = useChatStore()
  const theme = useThemeStore()
  const display = useDisplayStore()
  const reasoning = useReasoningStore()

  /** 回滚到上一步：取最后一个 completed/edited 步骤，二次确认后 rewind */
  async function rollbackToLastStep(): Promise<void> {
    if (!reasoning.sessionId) {
      ElMessage.warning('当前无进行中的会话')
      return
    }
    const steps = reasoning.steps
    if (!steps.length) {
      ElMessage.warning('当前会话无步骤可回滚')
      return
    }
    const target = [...steps].reverse().find((s) => s.status === 'completed' || s.status === 'edited')
    if (!target) {
      ElMessage.warning('没有可回滚的已完成步骤')
      return
    }
    try {
      await ElMessageBox.confirm(
        `确认回滚到步骤「${target.name}」？该步骤之后的步骤将被丢弃，且不可撤销。`,
        '回滚确认',
        { type: 'warning', confirmButtonText: '确认回滚', cancelButtonText: '取消' },
      )
    } catch {
      return // 用户取消
    }
    try {
      const resp = await rewindSessionApi(reasoning.sessionId, {
        step_index: target.index,
        edited_content: null,
      })
      reasoning.onSseStepReplaced(target.index, resp.new_steps)
      ElMessage.success('已回滚')
    } catch (err) {
      console.error('[useCommands.rollbackToLastStep]', err)
      ElMessage.error('回滚失败，请稍后重试')
    }
  }

  const actions: CommandItem[] = [
    {
      id: 'action_chat_new',
      group: 'actions',
      scope: 'chat',
      title: '新建对话',
      subtitle: '清空消息并开启新会话',
      icon: 'Plus',
      keywords: ['新建', 'xj', 'new', '对话'],
      action: () => {
        chat.resetChat()
        ElMessage.success('已新建对话')
      },
    },
    {
      id: 'action_chat_clear',
      group: 'actions',
      scope: 'chat',
      title: '清空当前对话',
      subtitle: '重置会话上下文',
      icon: 'Delete',
      keywords: ['清空', 'qk', 'clear', '重置'],
      action: () => {
        chat.resetChat()
        ElMessage.success('已清空当前对话')
      },
    },
    {
      id: 'action_theme_toggle',
      group: 'actions',
      scope: 'global',
      title: '切换主题（深/浅）',
      subtitle: theme.isDark ? '当前深色 → 切换浅色' : '当前浅色 → 切换深色',
      icon: 'Moon',
      keywords: ['主题', 'zt', 'theme', '深色', '浅色', 'dark', 'light'],
      action: () => theme.toggle(),
    },
    {
      id: 'action_bg_mode',
      group: 'actions',
      scope: 'global',
      title: '背景模式切换（标准/演示）',
      subtitle: '标准 = 背景降噪 · 演示 = 动效全开',
      icon: 'VideoPlay',
      keywords: ['背景', 'bj', 'background', '演示', '标准', '动效'],
      action: () => {
        const next: 'standard' | 'presentation' =
          display.displayMode === 'presentation' ? 'standard' : 'presentation'
        display.setDisplayMode(next)
        ElMessage.success(next === 'presentation' ? '已切换为演示模式' : '已切换为标准模式')
      },
    },
    {
      id: 'action_cb_palette',
      group: 'actions',
      scope: 'global',
      title: '色盲模式切换（4 palette 循环）',
      subtitle: '默认 → IBM → Okabe-Ito → ColorBrewer',
      icon: 'Brush',
      keywords: ['色盲', 'sm', 'colorblind', 'palette', '调色板'],
      action: () => {
        const order: Array<'default' | 'ibm-cb-safe' | 'okabe-ito' | 'colorbrewer-rdylbu'> = [
          'default',
          'ibm-cb-safe',
          'okabe-ito',
          'colorbrewer-rdylbu',
        ]
        const idx = order.indexOf(display.colorBlind)
        const next = order[(idx + 1) % order.length]!
        display.setColorBlindPalette(next)
        ElMessage.success(`已切换色盲 palette：${next}`)
      },
    },
    {
      id: 'action_reason_pause',
      group: 'actions',
      scope: 'chat',
      title: '暂停当前推理',
      subtitle: '仅推理运行中可用',
      icon: 'VideoPause',
      keywords: ['暂停', 'zt', 'pause', '推理'],
      action: () => {
        void reasoning.pause('user_manual')
      },
    },
    {
      id: 'action_reason_resume',
      group: 'actions',
      scope: 'chat',
      title: '恢复当前推理',
      subtitle: '仅推理暂停时可用',
      icon: 'VideoPlay',
      keywords: ['恢复', 'hf', 'resume', '继续', '推理'],
      action: () => {
        void reasoning.resume()
      },
    },
    {
      id: 'action_session_detail',
      group: 'actions',
      scope: 'global',
      title: '查看 Session 详情',
      subtitle: '打开步骤时间线 · token 消耗 · 回滚节点',
      icon: 'Monitor',
      keywords: ['session', '会话', '详情', 'xq', '步骤', 'token', '回滚'],
      action: () => {
        useSessionStatsStore().openDrawer()
      },
    },
    {
      id: 'action_hitl_queue',
      group: 'actions',
      scope: 'global',
      title: '打开 HITL 审计队列',
      subtitle: '跳转 /audit 并过滤待审批',
      icon: 'Bell',
    keywords: ['审计', 'sj', 'audit', 'hitl', '待审', '队列'],
    action: () => {
      void router.push({ path: '/audit', query: { filter: 'pending', from: 'command-palette' } })
    },
    },
    {
      id: 'action_rollback',
      group: 'actions',
      scope: 'chat',
      title: '回滚到上一步',
      subtitle: '二次确认后回到最近已完成步骤',
      icon: 'RefreshLeft',
      keywords: ['回滚', 'hg', 'rollback', '上一步', 'rewind', '撤销'],
      action: () => {
        void rollbackToLastStep()
      },
    },
  ]
  return actions
}

/** 注册全部命令（幂等：模块级 registered 守卫） */
function ensureRegistered(): void {
  if (registered) return
  registered = true
  commands.value.push(...ROUTE_COMMANDS.map(makeRouteCommand))
  commands.value.push(...makeActionCommands())
  // QA R1 P1-1：Ctrl+Shift+1-5 / ⌘⇧+1-5 路由直达热键（与 ROUTE_COMMANDS 映射一致）
  //   数字顺序：1=实时监控 / 2=灰度面板 / 3=HITL 审计 / 4=系统总览 / 5=智能对话
  registerRouteHotkeys()
}

/** 注册 5 个路由直达热键（模块级单例，随命令注册一次；无需注销）
 * 数字显式列出，映射与 ROUTE_COMMANDS.shortcut 一一对应（QA R1 P1-1 验证点） */
function registerRouteHotkeys(): void {
  const digits = ['1', '2', '3', '4', '5'] as const
  for (const digit of digits) {
    const spec = ROUTE_COMMANDS.find((s) => s.shortcut[s.shortcut.length - 1] === digit)
    if (!spec) continue
    registerHotkey({
      id: `route-direct-${digit}`,
      // Windows/Linux：Ctrl+Shift+数字；Mac：⌘+Shift+数字（主流浏览器均无默认行为）
      // 说明：Ctrl+数字（Chrome/Edge/Firefox 切标签页）、Alt+数字（部分浏览器也切标签页）
      //       均被浏览器抢占，页面收不到事件，因此路由直达改用 Ctrl+Shift / ⌘+Shift。
      //       注意 Shift 组合下 e.key 会变为符号（Shift+1 → '!'），数字必须用物理键位 e.code 匹配。
      match: (e) =>
        e.code === `Digit${digit}` &&
        !e.altKey &&
        // Windows/Linux：Ctrl+Shift+数字；Mac：⌘+Shift+数字（主流浏览器均无默认行为）
        ((e.ctrlKey && !e.metaKey) || (e.metaKey && !e.ctrlKey)) && e.shiftKey,
      priority: 30,
      preventDefault: true,
      handler: () => {
        void router.push(spec.path)
      },
    })
  }
}

/** 使用命令注册中心 */
export function useCommands() {
  ensureRegistered()

  /** 注册新命令（插件式扩展点；同 id 幂等） */
  function register(item: CommandItem): void {
    if (commands.value.some((c) => c.id === item.id)) return
    commands.value.push(item)
  }

  /** 按分组取命令 */
  function getByGroup(group: CommandGroup): CommandItem[] {
    return commands.value.filter((c) => c.group === group)
  }

  /** 按 scope 取命令（上下文组过滤用） */
  function getByScope(scope: CommandScope): CommandItem[] {
    return commands.value.filter((c) => c.scope === scope)
  }

  /** 执行命令（disabled 时 no-op） */
  async function execute(id: string): Promise<void> {
    const cmd = commands.value.find((c) => c.id === id)
    if (!cmd || cmd.disabled) return
    await cmd.action()
  }

  return {
    commands,
    register,
    getByGroup,
    getByScope,
    execute,
  }
}
