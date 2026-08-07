/**
 * web/tests/e2e/F3_hitl_badge.spec.ts
 * GridMind v1.5.1 T06 · F3 HITL 队列徽标实时刷新端到端联调
 *
 * 设计依据：
 *   - 决策 7.2（主理人）：双通道数据流 = 5s 轮询兜底 + SSE 实时推送
 *   - 架构 §1.3.1 + PRD §3.3
 *
 * 链路：
 *   1. 进入主页 → audit store.hydrate() 启动 5s 轮询
 *   2. mock /audit/pending-count → 0 → 徽标不渲染 (shouldShow=false)
 *   3. 改 mock → 7s 内触发下一轮轮询 → audit store refreshPendingCount
 *      → pendingHitlCount=5 → 徽标渲染 + severity=critical
 *   4. 点徽标 → 路由 /audit?filter=pending&from=hitl-badge
 *
 * 性能：徽标实时刷新 ≤ 5s（双通道兜底）
 *
 * 注意：ChatView SSE 监听器暂未对接 hitl_interrupt（v1.5.1 P1 backlog），
 *       故本测试走 5s 轮询兜底路径，符合决策 7.2 设计。
 *
 * 作者：寇豆码（T06 工程师）
 */
import { test, expect } from '@playwright/test'
import { mockPendingCountApi, getStore } from './helpers/mock-sse'

test.describe('F3 HITL 队列徽标 e2e', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      try {
        localStorage.setItem('gridmind.onboarded', 'true')
        localStorage.setItem('gridmind.onboardedAt', new Date().toISOString())
        localStorage.setItem('gridmind.onboarding.scenarioId', 'first-visit')
      } catch {
        /* ignore */
      }
    })
  })

  test('F3 5s 轮询兜底：pending-count 升高 → 徽标实时刷新 → 跳转审计页', async ({ page }) => {
    // ── 1. 初始 mock：pending-count = 0 ────────────────
    await mockPendingCountApi(page, 0)

    // ── 2. 进入主页 ──────────────────────────────────
    await page.goto('/')
    await expect(page).toHaveURL(/\/(?!onboarding)/)

    // ── 3. 等待 audit store hydrate 完成 + 轮询已启 ──
    await page.waitForFunction(
      () => {
        const w = window as unknown as { __pinia?: { _s: Map<string, any> } }
        const a = w.__pinia?._s.get('audit')
        return a?.isHydrated === true
      },
      { timeout: 8000 },
    )

    // ── 4. 断言：徽标元素不存在（pendingHitlCount=0）──
    await page.waitForTimeout(800) // 一次性让 hydration 收尾
    const badge0 = page.locator('[data-component="hitl-badge"]')
    await expect(badge0).toHaveCount(0)

    // ── 5. 模拟后端新增 5 条待审（critical 阈值）──
    // 解除旧 route，挂新 route 返回 count=5
    await page.unroute('**/api/audit/pending-count')
    await mockPendingCountApi(page, 5)

    // ── 6. 等 ≤ 5.5s 轮询触发（POLL_INTERVAL_MS=5000）──
    // audit store 内部 5s setInterval；refreshPendingCount 失败容忍
    await page.waitForFunction(
      () => {
        const a = (window as unknown as { __pinia: { _s: Map<string, any> } }).__pinia._s.get(
          'audit',
        )
        return a.pendingHitlCount === 5
      },
      { timeout: 7000 },
    )

    // ── 7. 断言：徽标可见 + count=5 + severity=critical ─
    const badge = page.locator('[data-component="hitl-badge"]')
    await expect(badge).toBeVisible({ timeout: 3000 })
    await expect(badge).toHaveAttribute('data-severity', 'critical')
    await expect(badge).toHaveAttribute('data-count', '5')
    const countEl = page.locator('[data-test="hitl-badge-count"]')
    await expect(countEl).toHaveText('5')

    // ── 8. 点击徽标 → 跳转审计页 ─────────────────────
    await badge.click()
    await page.waitForURL((url) => {
      return (
        url.pathname === '/audit' &&
        url.searchParams.get('filter') === 'pending' &&
        url.searchParams.get('from') === 'hitl-badge'
      )
    }, { timeout: 5000 })
    expect(page.url()).toContain('/audit')
    expect(page.url()).toContain('filter=pending')
    expect(page.url()).toContain('from=hitl-badge')

    // ── 9. 最终 store 断言 ───────────────────────────
    const storeState = await getStore<{
      pendingHitlCount: number
      connectionState: string
    }>(page, 'audit').then((s) => ({
      count: s.pendingHitlCount,
      state: s.connectionState,
    }))
    expect(storeState.count).toBe(5)
    expect(storeState.state).toBe('connected')
  })

  test('F3 后端 5xx → 徽标降级到 · (degraded 态)', async ({ page }) => {
    // ── 模拟 pending-count 服务异常 → 徽标降级 ──────
    await page.route('**/api/audit/pending-count', async (route) => {
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ error: 'service_unavailable' }),
      })
    })

    await page.goto('/')
    await expect(page).toHaveURL(/\/(?!onboarding)/)

    // 等待轮询失败，徽标降级显示
    await page.waitForFunction(
      () => {
        const a = (window as unknown as { __pinia: { _s: Map<string, any> } }).__pinia._s.get(
          'audit',
        )
        return a.connectionState === 'error'
      },
      { timeout: 10000 },
    )

    // 徽标显示 "·"（降级态）
    const badge = page.locator('[data-component="hitl-badge"]')
    await expect(badge).toBeVisible({ timeout: 3000 })
    await expect(badge).toHaveClass(/hitl-badge--degraded/)
    await expect(page.locator('[data-test="hitl-badge-count"]')).toHaveText('·')
  })
})
