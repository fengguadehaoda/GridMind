/**
 * web/tests/e2e/header_redesign.spec.ts
 * GridMind · 顶部 Header 重构 端到端回归（T05）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 设计依据（header-redesign-architecture-2026-08-06 §5 T05 + §7.8）：
 *   - 顶栏 ≤5 元素（验收硬指标）：Logo / 主导航 / 菜单 / 帮助 / 更多(移动端)
 *   - CPU/MEM/AGT/CLK 不再出现在顶栏（迁移至右下角 StatusFloatingCard）
 *   - 右侧 MenuDrawer 四分组（视图/主题/系统/调试）+ 快捷区，route/action 跳转正确
 *   - P1-1 抽屉搜索过滤
 *   - StatusFloatingCard 折叠 / 展开 / 隐藏（localStorage 持久化）
 *   - ⌘K 命令 action_status_card_toggle 切换卡片显隐（round-trip）
 *   - 跨六页一致（/ /monitor /grayscale /audit /system /help）
 *   - axe-core 快查：展开卡片 + 抽屉分组 0 critical/serious
 *
 * 约束（沿用现有 e2e 体系）：workers=1（playwright.config.ts）；baseURL localhost:5173；
 *   before 注入 onboarding 已完成的 localStorage，避免路由守卫跳转 /onboarding。
 *
 * 作者：寇豆码（T05 工程师）
 */
import { test, expect, type Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'

/** 过滤 critical/serious 级别违规数（与 a11y.spec.ts 同源） */
function criticalSeriousViolations(results: Awaited<ReturnType<AxeBuilder['analyze']>>): number {
  return results.violations.filter((v) => v.impact === 'critical' || v.impact === 'serious').length
}

/** 注入 onboarding 完成 + 状态卡片默认显隐，保证用例确定性 */
async function setupState(page: Page): Promise<void> {
  await page.addInitScript(() => {
    try {
      localStorage.setItem('gridmind.onboarded', 'true')
      localStorage.setItem('gridmind.onboardedAt', new Date().toISOString())
      localStorage.setItem('gridmind.onboarding.scenarioId', 'first-visit')
      localStorage.removeItem('gridmind.reattach_thread_id')
      // 状态卡片持久化复位（避免上一轮用例残留）
      localStorage.setItem('gridmind.statusCard.visible', 'true')
      localStorage.setItem('gridmind.statusCard.collapsed', 'true')
    } catch {
      /* ignore */
    }
  })
}

test.describe('Header 重构 e2e', () => {
  test.beforeEach(async ({ page }) => {
    await setupState(page)
  })

  test('T05-1 顶栏 ≤5 元素 + CPU/MEM/AGT/CLK 已移除（桌面 1280）', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveURL(/\/(?!onboarding)/)
    await expect(page.locator('[data-test="header-brand"]')).toBeVisible()

    // 顶栏功能元素：brand + nav + help + menu = 4（more 仅移动端）≤5 ✔
    const headerControls = page.locator(
      [
        '[data-test="header-brand"]',
        '[data-test="header-nav"]',
        '[data-test="nav-drawer-trigger"]',
        '[data-test="help-entry"]',
        '[data-test="header-menu-trigger"]',
        '[data-test="header-more-trigger"]',
      ].join(', '),
    )
    const count = await headerControls.count()
    expect(count).toBeLessThanOrEqual(5)

    // CPU / MEM / AGT / CLK 不再出现在顶栏
    const header = page.locator('.app-header')
    await expect(header.getByText(/CPU|MEM|AGT|CLK/)).toHaveCount(0)
    // 旧状态条 / 连接徽标 DOM 移除
    await expect(page.locator('.status-strip')).toHaveCount(0)
    await expect(page.locator('.status-badge')).toHaveCount(0)
    // 新对话 / 主题等散落按钮收纳进抽屉（不再占用顶栏）
    await expect(header.getByText('新对话')).toHaveCount(0)
  })

  test('T05-2 顶栏 ≤5 元素（移动端 <768px，更多折叠点 fallback）', async ({ page }) => {
    await page.setViewportSize({ width: 700, height: 800 })
    await page.goto('/')
    await expect(page).toHaveURL(/\/(?!onboarding)/)
    await expect(page.locator('[data-test="header-brand"]')).toBeVisible()

    // 移动端：brand + NavDrawer 汉堡 + help + menu + more = 5 ≤5 ✔
    const headerControls = page.locator(
      [
        '[data-test="header-brand"]',
        '[data-test="header-nav"]',
        '[data-test="nav-drawer-trigger"]',
        '[data-test="help-entry"]',
        '[data-test="header-menu-trigger"]',
        '[data-test="header-more-trigger"]',
      ].join(', '),
    )
    const count = await headerControls.count()
    expect(count).toBeLessThanOrEqual(5)

    // 更多折叠点可见 + 收纳项可达
    const more = page.locator('[data-test="header-more-trigger"]')
    await expect(more).toBeVisible()
    await more.click()
    await expect(page.locator('[data-test="more-new-chat"]')).toBeVisible()
    await expect(page.locator('[data-test="more-knowledge"]')).toBeVisible()
  })

  test('T05-3 菜单抽屉：打开 / 四分组 / route 跳转 / 自动关闭', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('[data-test="header-menu-trigger"]')).toBeVisible()

    // 打开抽屉
    await page.locator('[data-test="header-menu-trigger"]').click()
    const drawer = page.locator('[data-test="menu-drawer"]')
    await expect(drawer).toBeVisible()

    // 四分组完整渲染
    await expect(page.locator('[data-test="menu-drawer-group-view"]')).toBeVisible()
    await expect(page.locator('[data-test="menu-drawer-group-theme"]')).toBeVisible()
    await expect(page.locator('[data-test="menu-drawer-group-system"]')).toBeVisible()
    await expect(page.locator('[data-test="menu-drawer-group-debug"]')).toBeVisible()

    // 视图分组含 5 路由 + 帮助
    await expect(page.locator('[data-test="menu-drawer-entry-route-monitor"]')).toBeVisible()
    await expect(page.locator('[data-test="menu-drawer-entry-route-help"]')).toBeVisible()

    // 点击 route 型条目 → 抽屉关闭 + 跳转 /monitor
    await page.locator('[data-test="menu-drawer-entry-route-monitor"]').click()
    await expect(page).toHaveURL(/\/monitor/)
    await expect(drawer).not.toBeVisible()
  })

  test('T05-4 菜单抽屉：搜索过滤 + Esc 关闭 + ⌘\\ 快捷开关（P1-1/P1-2）', async ({ page }) => {
    await page.goto('/')
    await page.locator('[data-test="header-menu-trigger"]').click()
    const search = page.locator('[data-test="menu-drawer-search"]')
    await expect(search).toBeVisible()

    // P1-1：搜索「主题」→ 仅主题分组保留
    await search.fill('主题')
    await expect(page.locator('[data-test="menu-drawer-group-theme"]')).toBeVisible()
    await expect(page.locator('[data-test="menu-drawer-group-view"]')).toHaveCount(0)
    await expect(page.locator('[data-test="menu-drawer-group-debug"]')).toHaveCount(0)

    // 清空搜索恢复全部分组
    await search.fill('')
    await expect(page.locator('[data-test="menu-drawer-group-view"]')).toBeVisible()

    // Esc 关闭
    await page.keyboard.press('Escape')
    await expect(page.locator('[data-test="menu-drawer"]')).not.toBeVisible()

    // P1-2：⌘\ / Ctrl+\ 快捷开关
    await page.keyboard.press('Control+\\')
    await expect(page.locator('[data-test="menu-drawer"]')).toBeVisible()
    await page.keyboard.press('Control+\\')
    await expect(page.locator('[data-test="menu-drawer"]')).not.toBeVisible()
  })

  test('T05-5 状态卡片：折叠一行 / 展开详情 / 隐藏持久化', async ({ page }) => {
    await page.goto('/')
    const card = page.locator('[data-test="status-card"]')
    await expect(card).toBeVisible()

    // 折叠态一行包含 CPU / 内存 / AIT / CLK
    const collapsedRow = page.locator('[data-test="status-card-toggle"]')
    await expect(collapsedRow).toBeVisible()
    await expect(card.getByText(/CPU/)).toBeVisible()
    await expect(card.getByText(/AIT/)).toBeVisible()
    await expect(card.getByText(/CLK/)).toBeVisible()

    // 点击展开 → 详情 / 趋势 / 服务连接 / 最近活动
    await collapsedRow.click()
    await expect(page.locator('[data-test="status-card-expanded"]')).toBeVisible()
    await expect(card.getByText('系统状态')).toBeVisible()
    await expect(card.getByText(/服务(已连接|未连接)/)).toBeVisible()
    await expect(card.getByText(/最近活动/)).toBeVisible()

    // 「隐藏」→ 卡片消失 + localStorage 持久化
    await page.locator('[data-test="status-card-hide"]').click()
    await expect(card).not.toBeVisible()
    const stored = await page.evaluate(() => localStorage.getItem('gridmind.statusCard.visible'))
    expect(stored).toBe('false')

    // 刷新后仍隐藏（持久化生效）
    await page.reload()
    await expect(page.locator('[data-test="status-card"]')).not.toBeVisible()
  })

  test('T05-6 ⌘K 命令面板：action_status_card_toggle 切换卡片显隐（round-trip）', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('[data-test="status-card"]')).toBeVisible()

    // 1. 先隐藏卡片（展开 → 隐藏）
    await page.locator('[data-test="status-card-toggle"]').click()
    await page.locator('[data-test="status-card-hide"]').click()
    await expect(page.locator('[data-test="status-card"]')).not.toBeVisible()

    // 2. ⌘K 打开命令面板 → 搜索「状态」→ 执行切换命令 → 卡片重新显示
    await page.keyboard.press('Control+K')
    const paletteInput = page.locator('[data-test="command-palette-input"]')
    await expect(paletteInput).toBeVisible({ timeout: 5000 })
    await paletteInput.fill('状态')

    const cmd = page.locator('[data-test="command-item-action_status_card_toggle"]')
    await expect(cmd).toBeVisible({ timeout: 5000 })
    await cmd.click()

    // 3. 面板关闭 + 卡片重新显示 + 持久化复位
    await expect(page.locator('[data-test="command-palette-input"]')).not.toBeVisible()
    await expect(page.locator('[data-test="status-card"]')).toBeVisible()
    const stored = await page.evaluate(() => localStorage.getItem('gridmind.statusCard.visible'))
    expect(stored).toBe('true')
  })

  test('T05-7 跨六页一致性：Header / 抽屉 / 卡片均可达', async ({ page }) => {
    for (const path of ['/', '/monitor', '/grayscale', '/audit', '/system', '/help']) {
      await page.goto(path)
      await expect(page).toHaveURL(new RegExp(path.replace('/', '\\/') + '($|\\?)'))
      await expect(page.locator('[data-test="header-brand"]')).toBeVisible()
      await expect(page.locator('[data-test="header-menu-trigger"]')).toBeVisible()
      await expect(page.locator('[data-test="status-card"]')).toBeVisible()

      // 抽屉在每页均可打开
      await page.locator('[data-test="header-menu-trigger"]').click()
      await expect(page.locator('[data-test="menu-drawer-group-view"]')).toBeVisible()
      await page.keyboard.press('Escape')
      await expect(page.locator('[data-test="menu-drawer"]')).not.toBeVisible()
    }
  })

  test('T05-8 a11y 快查：展开卡片 + 抽屉分组 0 critical/serious', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('[data-test="status-card"]')).toBeVisible()

    // 展开卡片后扫描（含趋势 / 服务连接 / 最近活动）
    await page.locator('[data-test="status-card-toggle"]').click()
    await expect(page.locator('[data-test="status-card-expanded"]')).toBeVisible()
    await page.waitForTimeout(300)
    const cardResults = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .include('[data-test="status-card"]')
      .analyze()
    if (criticalSeriousViolations(cardResults) > 0) {
      // eslint-disable-next-line no-console
      console.log('[a11y · status-card]', JSON.stringify(cardResults.violations, null, 2))
    }
    expect(criticalSeriousViolations(cardResults)).toBe(0)

    // 打开抽屉，扫描视图分组（route 条目区；避开 EP overlay 全屏）
    await page.locator('[data-test="header-menu-trigger"]').click()
    await expect(page.locator('[data-test="menu-drawer-group-view"]')).toBeVisible()
    await page.waitForTimeout(300)
    const drawerResults = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .include('[data-test="menu-drawer-group-view"]')
      .analyze()
    if (criticalSeriousViolations(drawerResults) > 0) {
      // eslint-disable-next-line no-console
      console.log('[a11y · drawer]', JSON.stringify(drawerResults.violations, null, 2))
    }
    expect(criticalSeriousViolations(drawerResults)).toBe(0)
  })
})
