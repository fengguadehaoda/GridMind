/**
 * data/menuDrawerGroups.ts · 右侧抽屉分组注册表（T01）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构决策（header-redesign-architecture-2026-08-06 §1.3 + §3.2）：
 *   - 抽屉全部入口的唯一事实源（数据驱动注册表）
 *   - 四分组「视图 / 主题 / 系统 / 调试」+ 底部快捷区，100% 收纳原 Header 入口
 *   - component 型条目直接引用复用控件（HitlBadge / SessionBadge / ...），**零改动**
 *   - 新增入口 = 新增一条 registry，不新增 Header 按钮（架构 §7.5 铁律）
 *
 * 说明：action 型条目的 store 获取放在回调内部（点击时才执行），
 *   避免模块顶层在 pinia 未就绪时调用 useChatStore() 崩溃。
 */
import type { Component } from 'vue'
import {
  ChatDotRound,
  Monitor,
  Histogram,
  Document,
  DataBoard,
  QuestionFilled,
  Collection,
  Plus,
  Compass,
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useChatStore } from '@/stores/chatStore'
import BackgroundModeToggle from '@/components/controls/BackgroundModeToggle.vue'
import ThemeToggle from '@/components/controls/ThemeToggle.vue'
import ColorBlindModeToggle from '@/components/controls/ColorBlindModeToggle.vue'
import HitlBadge from '@/components/controls/HitlBadge.vue'
import SessionBadge from '@/components/controls/SessionBadge.vue'
import OnboardingTrigger from '@/components/controls/OnboardingTrigger.vue'
import type { MenuDrawerEntry, MenuDrawerGroup } from '@/types/header'

/** 视图分组：5 路由 + 帮助中心（route 型，跳转后关闭抽屉） */
const VIEW_GROUP: MenuDrawerGroup = {
  id: 'view',
  title: '视图',
  entries: [
    {
      id: 'route-chat',
      type: 'route',
      label: '智能对话',
      icon: ChatDotRound as Component,
      route: '/',
      keywords: ['对话', 'chat', '首页', 'dh'],
    },
    {
      id: 'route-monitor',
      type: 'route',
      label: '实时监控',
      icon: Monitor as Component,
      route: '/monitor',
      keywords: ['监控', 'monitor', 'jk'],
    },
    {
      id: 'route-grayscale',
      type: 'route',
      label: '灰度面板',
      icon: Histogram as Component,
      route: '/grayscale',
      keywords: ['灰度', 'grayscale', '切流', 'hd'],
    },
    {
      id: 'route-audit',
      type: 'route',
      label: 'HITL 审计',
      icon: Document as Component,
      route: '/audit',
      keywords: ['审计', 'audit', 'hitl', '审批', 'sj'],
    },
    {
      id: 'route-system',
      type: 'route',
      label: '系统总览',
      icon: DataBoard as Component,
      route: '/system',
      keywords: ['系统', 'system', '总览', 'xt'],
    },
    {
      id: 'route-help',
      type: 'route',
      label: '帮助中心',
      icon: QuestionFilled as Component,
      route: '/help',
      keywords: ['帮助', 'help', 'bz'],
    },
  ],
}

/** 主题分组：背景模式 / 主题切换 / 色盲 palette（component 型，零改动嵌入） */
const THEME_GROUP: MenuDrawerGroup = {
  id: 'theme',
  title: '主题',
  entries: [
    {
      id: 'component-bg-mode',
      type: 'component',
      label: '背景模式',
      component: BackgroundModeToggle,
      keywords: ['背景', 'background', '演示', '标准'],
    },
    {
      id: 'component-theme-toggle',
      type: 'component',
      label: '主题切换',
      component: ThemeToggle,
      keywords: ['主题', 'theme', '深色', '浅色'],
    },
    {
      id: 'component-cb-mode',
      type: 'component',
      label: '色盲模式',
      component: ColorBlindModeToggle,
      keywords: ['色盲', 'colorblind', 'palette'],
    },
  ],
}

/** 系统分组：HITL 待审 / Session 状态（component 型；服务连接状态见浮动卡片） */
const SYSTEM_GROUP: MenuDrawerGroup = {
  id: 'system',
  title: '系统',
  entries: [
    {
      id: 'component-hitl-badge',
      type: 'component',
      label: 'HITL 待审',
      component: HitlBadge,
      keywords: ['hitl', '待审', '审计', '审批'],
    },
    {
      id: 'component-session-badge',
      type: 'component',
      label: 'Session 状态',
      component: SessionBadge,
      keywords: ['session', '会话', '推理'],
    },
  ],
}

/** 调试分组：新手引导（原散落入口；占位条目暂无则暂不登记） */
const DEBUG_GROUP: MenuDrawerGroup = {
  id: 'debug',
  title: '调试',
  entries: [
    {
      id: 'component-onboarding',
      type: 'component',
      label: '新手引导',
      component: OnboardingTrigger,
      keywords: ['引导', 'onboarding', '新手'],
    },
  ],
}

/** 底部快捷区（P2-2「自定义布局」扩展点） */
const QUICK_ENTRIES: MenuDrawerEntry[] = [
  {
    id: 'action-new-chat',
    type: 'action',
    label: '新对话',
    icon: Plus as Component,
    keywords: ['新对话', '新建', 'new', 'chat'],
    action: () => {
      const chat = useChatStore()
      chat.resetChat()
      ElMessage.success('已新建对话')
    },
  },
  {
    id: 'route-knowledge',
    type: 'route',
    label: '知识库管理',
    icon: Collection as Component,
    route: '/help?tab=knowledge',
    keywords: ['知识库', 'knowledge', '上传', 'kb'],
  },
  {
    id: 'route-onboarding',
    type: 'route',
    label: '消息引导',
    icon: Compass as Component,
    route: '/onboarding?force=1',
    keywords: ['引导', 'onboarding', '消息'],
  },
]

/** 四分组（顺序 = 视觉顺序：视图 → 主题 → 系统 → 调试） */
export const menuDrawerGroups: MenuDrawerGroup[] = [
  VIEW_GROUP,
  THEME_GROUP,
  SYSTEM_GROUP,
  DEBUG_GROUP,
]

/** 底部快捷区条目 */
export const menuDrawerQuickEntries: MenuDrawerEntry[] = QUICK_ENTRIES
