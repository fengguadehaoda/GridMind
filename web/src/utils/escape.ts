/**
 * escape.ts · ECharts tooltip XSS 转义共享工具（M-4 T03）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 背景（F8 已知依赖公告豁免 · GHSA-fgmj-fm8m-jvvx）：
 *   - echarts 锁定 ^5.6.0（<6.1.0 存在 moderate XSS 公告：tooltip/富文本渲染
 *     可被恶意 data 注入 HTML）。
 *   - **缓解措施**：所有进入 tooltip 的节点/边文本一律经 `escapeTooltip()`
 *     （< / > → &lt; / &gt;）转义，杜绝 HTML 注入向量。
 *
 * 三处复用（架构 §3.5）：
 *   - grayscale/TopologyGraph.vue（原内联实现上移为共享 util，行为不变）
 *   - grayscale/ForceGraphView.vue（props 驱动组件内部不感知业务，但调用方
 *     tooltipFormatter 应使用本 util 转义）
 *   - kb/GraphQAPanel.vue（图谱问答面板 tooltip / 详情文本）
 */

/**
 * 转义 `<` / `>` 为 HTML 实体（ECharts tooltip 富文本安全）。
 *
 * @param s 任意用户/数据来源字符串。
 * @returns 转义后的字符串（null/undefined 原样返回 null/undefined）。
 */
export function escapeTooltip(s: string | null | undefined): string | null | undefined {
  if (s === null || s === undefined) return s
  return String(s).replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

/** 与 escapeTooltip 相同但保证返回 string（业务文本安全拼接用） */
export function escapeTooltipText(s: string | null | undefined, fallback = ''): string {
  const out = escapeTooltip(s)
  return out === null || out === undefined ? fallback : out
}
