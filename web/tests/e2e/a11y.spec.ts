/**
 * web/tests/e2e/a11y.spec.ts
 * GridMind v1.5.1 T06 · axe-core 无障碍扫描 e2e
 *
 * 范围（架构 §6.3 + 决策 7.5 a11y 列表）：
 *   - 主页（ChatView 默认态）
 *   - 推理控制栏 (.reasoning-control-bar)
 *   - 推理状态徽标 (ReasoningStatusBadge)
 *   - HITL 徽标 (HitlBadge)
 *   - HITL 弹窗 (HitlEditDialog 打开状态)
 *
 * 规则：0 critical + 0 serious violations
 *
 * @playwright/test + @axe-core/playwright — 浏览器内执行 axe.run()，
 * 与生产构建无差异（无需修改 main.ts）。
 *
 * 作者：寇豆码（T06 工程师）
 */
import { test, expect, type Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { mockPendingCountApi } from './helpers/mock-sse'

async function setupOnboarding(page: Page): Promise<void> {
  await page.addInitScript(() => {
    try {
      localStorage.setItem('gridmind.onboarded', 'true')
      localStorage.setItem('gridmind.onboardedAt', new Date().toISOString())
      localStorage.setItem('gridmind.onboarding.scenarioId', 'first-visit')
      localStorage.removeItem('gridmind.reattach_thread_id')
    } catch {
      /* ignore */
    }
  })
}

/** 过滤 critical/serious 级别，返回剩余违规。用于断言 0 个。 */
function criticalSeriousViolations(results: Awaited<ReturnType<AxeBuilder['analyze']>>): number {
  return results.violations.filter((v) => v.impact === 'critical' || v.impact === 'serious').length
}

test.describe('a11y (axe-core)', () => {
  test.beforeEach(async ({ page }) => {
    await setupOnboarding(page)
  })

  test('a11y · 主页 ChatView 默认态 0 critical/serious', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveURL(/\/(?!onboarding)/)

    // 等 chat input 出现（路由 + 组件 mount）
    await expect(page.locator('textarea.el-textarea__inner').first()).toBeVisible({
      timeout: 8000,
    })
    // 等待徽标等 dynamic 内容稳定
    await page.waitForTimeout(500)

    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice'])
      .analyze()

    if (criticalSeriousViolations(results) > 0) {
      // eslint-disable-next-line no-console
      console.log(
        '[a11y · chat] violations:',
        JSON.stringify(
          results.violations.map((v) => ({
            id: v.id,
            impact: v.impact,
            help: v.help,
            nodes: v.nodes.length,
          })),
          null,
          2,
        ),
      )
    }
    expect(criticalSeriousViolations(results)).toBe(0)
  })

  test('a11y · 推理控制栏 (running 态) 0 critical/serious', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveURL(/\/(?!onboarding)/)
    await page.waitForFunction(
      () => Boolean((window as unknown as { __pinia?: unknown }).__pinia),
      { timeout: 5000 },
    )

    // 构造 running 态（与 F1 一致）
    await page.evaluate(() => {
      const r = (window as unknown as { __pinia: { _s: Map<string, any> } }).__pinia._s.get('reasoning')
      r.start('t-a11y-running', [
        {
          id: 's-a11y-1',
          index: 0,
          nodeName: 'supervisor',
          name: '监督',
          description: 'ds',
          promptFragment: 'pf',
          draftPromptFragment: null,
          contentHash: null,
          status: 'running',
          role: 'system',
          startedAt: new Date().toISOString(),
          finishedAt: null,
          durationMs: null,
          output: null,
          isEditable: false,
        },
      ])
    })

    await expect(page.locator('[data-component="reasoning-control-bar"]')).toBeVisible({
      timeout: 5000,
    })
    await page.waitForTimeout(500)

    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      // 仅扫推理控制栏区域
      .include('[data-component="reasoning-control-bar"]')
      .analyze()

    if (criticalSeriousViolations(results) > 0) {
      // eslint-disable-next-line no-console
      console.log(
        '[a11y · control-bar] violations:',
        JSON.stringify(results.violations, null, 2),
      )
    }
    expect(criticalSeriousViolations(results)).toBe(0)
  })

  test('a11y · HITL 徽标 (warning 态) 0 critical/serious', async ({ page }) => {
    await mockPendingCountApi(page, 3) // < 5 → warning severity
    await page.goto('/')
    await expect(page).toHaveURL(/\/(?!onboarding)/)

    await page.waitForFunction(
      () => {
        const a = (window as unknown as { __pinia?: { _s: Map<string, any> } }).__pinia?._s.get('audit')
        return a?.pendingHitlCount === 3
      },
      { timeout: 8000 },
    )
    await expect(page.locator('[data-component="hitl-badge"]')).toBeVisible()
    await page.waitForTimeout(500)

    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .include('[data-component="hitl-badge"]')
      .analyze()

    if (criticalSeriousViolations(results) > 0) {
      // eslint-disable-next-line no-console
      console.log(
        '[a11y · badge] violations:',
        JSON.stringify(results.violations, null, 2),
      )
    }
    expect(criticalSeriousViolations(results)).toBe(0)
  })

  test('a11y · HITL 弹窗前置 (打开态) 0 critical/serious', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveURL(/\/(?!onboarding)/)
    await page.waitForFunction(
      () => Boolean((window as unknown as { __pinia?: unknown }).__pinia),
      { timeout: 5000 },
    )

    // 构造弹窗打开态（与 F4 一致 — chatStore.interruptRequired=true）
    await page.evaluate(() => {
      const c = (window as unknown as { __pinia: { _s: Map<string, any> } }).__pinia._s.get('chat')
      c.interruptRequired = true
      c.interruptNode = 'dispatch_work_order'
      c.interruptMsg = 'a11y 测试弹窗内容，需判定高危'
      c.pendingThreadId = 't-a11y-dialog'
      c.interruptArgs = { device_id: 'T-001', priority: 'high' }
    })

    const dialog = page.locator('[data-testid="hitl-dialog"]')
    await expect(dialog).toBeVisible({ timeout: 3000 })
    // 等 focus trap 激活后焦点稳定
    await page.waitForTimeout(500)

    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .include('[data-testid="hitl-dialog"]')
      // Element Plus 会渲染额外 el-overlay；忽略（不在 dialog 内）
      .exclude('.el-overlay')
      .analyze()

    if (criticalSeriousViolations(results) > 0) {
      // eslint-disable-next-line no-console
      console.log(
        '[a11y · dialog] violations:',
        JSON.stringify(
          results.violations.map((v) => ({
            id: v.id,
            impact: v.impact,
            help: v.help,
            nodes: v.nodes.length,
          })),
          null,
          2,
        ),
      )
    }
    expect(criticalSeriousViolations(results)).toBe(0)
  })
})
