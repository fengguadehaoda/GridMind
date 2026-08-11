/**
 * web/src/data/navItems.ts · M-5 主导航唯一数据源（T05）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构 session-mgmt-architecture F08/F14：
 *   - Header 水平导航（App.vue）与紧凑模式汉堡导航（NavDrawer.vue）
 *     **共享同一数据源**，避免两处硬编码漂移；
 *   - 5 项路由入口带可选 ``roles?: Role[]``（缺省 = 全员可见）；
 *   - 角色矩阵与后端 require_role 同源（架构 §7 #3）：
 *       灰度 = operator/admin；审计 = auditor/operator/admin；系统 = admin；
 *       对话 / 监控 = 全员。
 *   - 前端仅展示层 UX，安全由后端 RBAC 兜底（403/404）。
 * 作者：寇豆码（工程师）
 */
import type { Component } from 'vue'
import type { Role } from '@/types'
import {
  ChatDotRound,
  Monitor,
  Histogram,
  Document,
  DataBoard,
} from '@element-plus/icons-vue'

/** 主导航项 */
export interface NavItem {
  path: string
  label: string
  icon: Component
  /** 可见角色（缺省 = 全员） */
  roles?: Role[]
}

/** 角色矩阵常量（menuDrawerGroups 复用，保证同源） */
export const ROLES_GRAYSCALE: Role[] = ['operator', 'admin']
export const ROLES_AUDIT: Role[] = ['auditor', 'operator', 'admin']
export const ROLES_SYSTEM: Role[] = ['admin']

/** 5 路由主导航（对话/监控全员；灰度/审计/系统按角色） */
export const NAV_ITEMS: NavItem[] = [
  { path: '/', label: '智能对话', icon: ChatDotRound },
  { path: '/monitor', label: '实时监控', icon: Monitor },
  { path: '/grayscale', label: '灰度面板', icon: Histogram, roles: ROLES_GRAYSCALE },
  { path: '/audit', label: 'HITL 审计', icon: Document, roles: ROLES_AUDIT },
  { path: '/system', label: '系统总览', icon: DataBoard, roles: ROLES_SYSTEM },
]

/**
 * 按角色过滤导航项（缺省 roles = 全员可见；fail-closed：角色不在 roles 内 → 隐藏）。
 */
export function visibleNavItems(role: Role): NavItem[] {
  return NAV_ITEMS.filter((item) => !item.roles || item.roles.includes(role))
}
