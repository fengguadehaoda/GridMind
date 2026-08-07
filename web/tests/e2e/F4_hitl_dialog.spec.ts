/**
 * web/tests/e2e/F4_hitl_dialog.spec.ts
 * GridMind v1.5.1 T06 · F4 HITL 弹窗前置端到端联调
 *
 * 链路：
 *   1. chatStore.sendMessage → streamChat fetch → mock SSE 返回 done+interrupt_required
 *   2. chatStore.onDone 触发 interruptRequired=true → showHitl=true
 *   3. HitlEditDialog v-model=true → 渲染 [data-testid="hitl-dialog"]
 *   4. 验证 z-index=100（决策 7.5：toast 1000 > 弹窗 100）
 *   5. focus trap：4 个按钮循环 (close / reject / approve / edit-approve)
 *   6. Esc 二次确认 ElMessageBox 弹出
 *
 * 性能：弹窗响应 ≤ 300ms（架构 §10 验收）
 *
 * 作者：寇豆码（T06 工程师）
 */
import { test, expect, type Page } from '@playwright/test'
import { mockChatStream, getStore, patchStore } from './helpers/mock-sse'

const THREAD_ID = 't-f4-e2e'
const DIALOG_LATENCY_BUDGET_MS = 300

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

test.describe('F4 HITL 弹窗前置 e2e', () => {
  test('F4 store.interruptRequired=true → 弹窗前置 ≤ 300ms + z-index=100 + focus trap 4 按钮', async ({
    page,
  }) => {
    await setupOnboarding(page)

    // ── 进入主页 ─────────────────────────────────────
    await page.goto('/')
    await expect(page).toHaveURL(/\/(?!onboarding)/)
    await page.waitForFunction(
      () => Boolean((window as unknown as { __pinia?: unknown }).__pinia),
      { timeout: 5000 },
    )

    // ── 直接 patch chat store.interruptRequired=true（不走 SSE 流式缓冲）──
    // 这是验收"≤ 300ms"性能预算的干净路径：从业务事件触发到 UI 可见
    const triggerStart = Date.now()
    await patchStore(page, 'chat', {
      interruptRequired: true,
      interruptNode: 'dispatch_work_order',
      interruptMsg: '工单 #T-2025-0817-001 涉及保电时段，需人工审批',
      pendingThreadId: THREAD_ID,
      interruptArgs: { device_id: 'T-001', priority: 'high' },
      hitlBusy: false,
      hitlSafetyReject: null,
    })

    // ── 等待弹窗：限时 300ms + 余量 ──────────────
    const dialog = page.locator('[data-testid="hitl-dialog"]')
    await expect(dialog).toBeVisible({ timeout: DIALOG_LATENCY_BUDGET_MS + 200 })
    const dialogElapsed = Date.now() - triggerStart
    expect(
      dialogElapsed,
      `F4 弹窗响应 ≤ ${DIALOG_LATENCY_BUDGET_MS}ms 实测 ${dialogElapsed}ms`,
    ).toBeLessThan(DIALOG_LATENCY_BUDGET_MS)

    // ── 验证：z-index = 100（决策 7.5）──────────────
    const zIndex = await dialog.evaluate((el) => getComputedStyle(el).zIndex)
    expect(zIndex).toBe('100')

    // ── 验证：role=dialog + aria-modal=true ────────
    await expect(dialog).toHaveAttribute('role', 'dialog')
    await expect(dialog).toHaveAttribute('aria-modal', 'true')

    // ── 验证：三按钮均存在 + aria-label ─────────────
    await expect(page.locator('[data-testid="hitl-btn-reject"]')).toBeVisible()
    await expect(page.locator('[data-testid="hitl-btn-approve"]')).toBeVisible()
    await expect(page.locator('[data-testid="hitl-btn-edit-approve"]')).toBeVisible()

    // ── 验证：interrupt node 显示在 data-testid 节点 ──
    await expect(page.locator('[data-testid="hitl-interrupt-node"]')).toContainText(
      'dispatch_work_order',
    )

    // ── focus trap：6 次 Tab 都不应逃出弹窗 ────────
    await dialog.focus()
    for (let i = 0; i < 6; i++) {
      await page.keyboard.press('Tab')
    }
    const focusedInsideDialog = await page.evaluate(() => {
      const dl = document.querySelector('[data-testid="hitl-dialog"]') as HTMLElement | null
      return dl?.contains(document.activeElement) ?? false
    })
    expect(focusedInsideDialog, 'focus 应始终在弹窗内').toBe(true)

    // ── chat store 状态断言 ────────────────────────
    const chatState = await getStore<{
      interruptRequired: boolean
      interruptNode: string | null
      interruptMsg: string | null
    }>(page, 'chat').then((s) => ({
      req: s.interruptRequired,
      node: s.interruptNode,
      msg: s.interruptMsg,
    }))
    expect(chatState.req).toBe(true)
    expect(chatState.node).toBe('dispatch_work_order')
    expect(chatState.msg).toContain('保电时段')

    // eslint-disable-next-line no-console
    console.log(`[F4] store-patch dialog latency=${dialogElapsed}ms`)
  })

  test('F4 完整 SSE 流式流程：chat.sendMessage → streamChat → done interrupt → 弹窗前置', async ({
    page,
  }) => {
    await setupOnboarding(page)
    await mockChatStream(page, THREAD_ID, {
      tokens: ['正在评估风险…'],
      interruptRequired: true,
      interruptNode: 'dispatch_work_order',
      interruptMsg: '工单涉及保电时段',
    })

    await page.goto('/')
    await expect(page).toHaveURL(/\/(?!onboarding)/)
    await page.waitForFunction(
      () => Boolean((window as unknown as { __pinia?: unknown }).__pinia),
      { timeout: 5000 },
    )
    await page.evaluate((tid: string) => {
      const c = (window as unknown as { __pinia: { _s: Map<string, any> } }).__pinia._s.get('chat')
      c.threadId = tid
      c.messages = []
    }, THREAD_ID)

    const chatInput = page.locator('textarea.el-textarea__inner').first()
    await chatInput.fill('建议对#1主变压器执行停机检修')
    await chatInput.press('Enter')

    // SSE + 流式模拟缓冲 ~ 1.8s；上限放宽到 5s
    const dialog = page.locator('[data-testid="hitl-dialog"]')
    await expect(dialog).toBeVisible({ timeout: 5000 })

    const zIndex = await dialog.evaluate((el) => getComputedStyle(el).zIndex)
    expect(zIndex).toBe('100')
    await expect(page.locator('[data-testid="hitl-interrupt-node"]')).toContainText(
      'dispatch_work_order',
    )
  })

  test('F4 Esc 触发二次确认（ElMessageBox）', async ({ page }) => {
    await setupOnboarding(page)

    await page.goto('/')
    await expect(page).toHaveURL(/\/(?!onboarding)/)
    await page.waitForFunction(
      () => Boolean((window as unknown as { __pinia?: unknown }).__pinia),
      { timeout: 5000 },
    )

    // 弹窗前置
    await patchStore(page, 'chat', {
      interruptRequired: true,
      interruptNode: 'risk_assess',
      interruptMsg: '高风险，需审核',
      pendingThreadId: THREAD_ID,
      interruptArgs: { device_id: 'T-002' },
      hitlBusy: false,
      hitlSafetyReject: null,
    })

    const dialog = page.locator('[data-testid="hitl-dialog"]')
    await expect(dialog).toBeVisible({ timeout: 1000 })

    // ── 按 Esc → useFocusTrap 派发 'focus-trap-escape' → HitlEditDialog.handleEscapeClose ──
    await dialog.focus()
    await page.keyboard.press('Escape')

    // Element Plus ElMessageBox.confirm 渲染 .el-message-box 节点
    const confirmBox = page.locator('.el-message-box')
    await expect(confirmBox).toBeVisible({ timeout: 3000 })
    await expect(confirmBox).toContainText('稍后处理')

    // ── 取消二次确认 → 弹窗仍保留 ──────────────────
    await page.locator('.el-message-box__btns button:has-text("继续审批")').click()
    await expect(confirmBox).toHaveCount(0)
    await expect(dialog).toBeVisible()

    // ── 这次确认关闭 → 弹窗消失 ───────────────────
    await dialog.focus()
    await page.keyboard.press('Escape')
    await expect(page.locator('.el-message-box')).toBeVisible({ timeout: 3000 })
    await page.locator('.el-message-box__btns button:has-text("稍后处理")').click()
    await expect(dialog).toHaveCount(0)
  })

  test('F4 仅批准按钮 → 关闭弹窗 + 系统消息', async ({ page }) => {
    await setupOnboarding(page)

    // mock decideInterrupt REST
    await page.route('**/api/interrupt/*/decision', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ thread_id: THREAD_ID, response: '已批准并执行' }),
      })
    })

    await page.goto('/')
    await expect(page).toHaveURL(/\/(?!onboarding)/)
    await page.waitForFunction(
      () => Boolean((window as unknown as { __pinia?: unknown }).__pinia),
      { timeout: 5000 },
    )

    // 弹窗前置
    await patchStore(page, 'chat', {
      interruptRequired: true,
      interruptNode: 'dispatch_work_order',
      interruptMsg: '需要审核',
      pendingThreadId: THREAD_ID,
      interruptArgs: { device_id: 'T-001' },
      hitlBusy: false,
      hitlSafetyReject: null,
    })

    const dialog = page.locator('[data-testid="hitl-dialog"]')
    await expect(dialog).toBeVisible({ timeout: 1000 })

    // 击"仅批准"
    await page.click('[data-testid="hitl-btn-approve"]')

    // 等待弹窗消失
    await expect(dialog).toHaveCount(0, { timeout: 3000 })

    // chat store 状态：interruptRequired=false
    const finalReq = await getStore<{ interruptRequired: boolean }>(page, 'chat').then(
      (s) => s.interruptRequired,
    )
    expect(finalReq).toBe(false)
  })
})
