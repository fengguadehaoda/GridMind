/**
 * types/header.ts · 顶部 Header 重构 · 类型定义（T01/T02）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构决策（header-redesign-architecture-2026-08-06 §3.1）：
 *   - MenuDrawer 分组 / 状态卡片的 TypeScript 类型唯一事实源
 *   - 抽屉条目三态：component 型直接嵌入复用控件 / route 型跳转 / action 型执行回调
 *   - 扩展点：条目附带可选 keywords[] 供 P1-1 抽屉搜索（架构 §3.1 未列，属合理假设）
 */
import type { Component } from 'vue'

/** 抽屉条目：component 型直接嵌入复用控件；route 型跳转；action 型执行回调 */
export type MenuDrawerEntry =
  | { id: string; type: 'component'; label: string; component: Component; keywords?: string[] }
  | { id: string; type: 'route'; label: string; icon?: Component; route: string; keywords?: string[] }
  | { id: string; type: 'action'; label: string; icon?: Component; action: () => void; keywords?: string[] }

/** 抽屉分组：视图 / 主题 / 系统 / 调试 */
export interface MenuDrawerGroup {
  id: string
  title: string
  entries: MenuDrawerEntry[]
}

/** 浮动卡片指标（M1 模拟，后续可接 metrics store / 后端） */
export interface StatusCardData {
  cpu: number      // CPU 百分比 0-100
  mem: number      // 内存百分比 0-100
  ait: number      // 在线 Agent 数（现状 agentCount）
  clk: string      // HH:mm:ss 时钟（24h）
  serviceConnected: boolean // 后端连接状态（App.vue healthCheck 传入）
}

/** 趋势历史采样点（近 1h，12 点环形覆盖） */
export interface StatusMetricSample {
  t: number  // epoch ms
  cpu: number
  mem: number
}

/** 状态卡片全局状态（useStatusCard 单例持有） */
export interface StatusCardState {
  visible: boolean
  collapsed: boolean
  position: 'bottom-right'
  data: StatusCardData
  history: StatusMetricSample[]
}
