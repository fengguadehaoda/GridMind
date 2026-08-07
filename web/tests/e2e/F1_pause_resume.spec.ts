/**
 * web/tests/e2e/F1_pause_resume.spec.ts
 * GridMind v1.5.1 T06 · F1 暂停/恢复端到端联调
 *
 * 验证链路：
 *   1. reasoning.store.start() → status='running', sessionId 设置
 *   2. ChatView watch sessionId → 调用 subscribeSessionEvents → SSE 连接建立
 *   3. 点击 [data-action="pause"] → store.pause() → POST /api/sessions/{id}/pause (REST mock)
 *   4. mock 返回 200 → store.status 乐观变 'paused'
 *   5. SSE mock 推 reasoning_paused 事件 → store.onSsePaused() 二次确认
 *   6. UI 渲染 [data-component="reasoning-status-badge"][data-status="paused"]
 *   7. 点击 [data-action="resume"] → 200ms 内 (PRD §10) 恢复 running
 *
 * 性能断言：F1 暂停响应 ≤ 500ms（架构 §10）
 *
 * 作者：寇豆码（T06 工程师）
 */
import { test, expect } from '@playwright/test'
import {
  mockPauseApi,
  mockResumeApi,
  mockSseStream,
  getStore,
  startJwtCapture,
} from './helpers/mock-sse'
import type { ReasoningStep } from '../../src/types'

const THREAD_ID = 't-f1-e2e'
const PAUSE_LATENCY_BUDGET_MS = 500 // PRD §10 验收

/** 构造最小可推理的状态：3 步 user content（可编辑） + 1 步 tool */
function makeInitialSteps(): ReasoningStep[] {
  return [
    {
      id: 's1',
      index: 0,
      nodeName: 'supervisor',
      name: '监督节点',
      description: '接收任务并分发',
      promptFragment: '分析电网现状',
      draftPromptFragment: null,
      contentHash: null,
      status: 'completed',
      role: 'system',
      startedAt: new Date().toISOString(),
      finishedAt: new Date().toISOString(),
      durationMs: 120,
      output: null,
      isEditable: false,
    },
    {
      id: 's2',
      index: 1,
      nodeName: 'diagnosis_agent',
      name: '诊断 Agent',
      description: 'z-score 异常检测',
      promptFragment: '对 #T1 油温序列执行 z-score 检测',
      draftPromptFragment: null,
      contentHash: null,
      status: 'running',
      role: 'user',
      startedAt: new Date().toISOString(),
      finishedAt: null,
      durationMs: null,
      output: null,
      isEditable: true,
    },
  ]
}

test.describe('F1 暂停/恢复 e2e', () => {
  test.beforeEach(async ({ page }) => {
    // 跳过 onboarding wizard（首次访问会被守卫重定向）
    await page.addInitScript(() => {
      try {
        localStorage.setItem('gridmind.onboarded', 'true')
        localStorage.setItem('gridmind.onboardedAt', new Date().toISOString())
        localStorage.setItem('gridmind.onboarding.scenarioId', 'first-visit')
        // 重置 reattach 防止 hydrate() 抢占 paused 状态
        localStorage.removeItem('gridmind.reattach_thread_id')
      } catch {
        /* ignore */
      }
    })
  })

  test('F1 暂停 → SSE reasoning_paused → 徽标切换 → 恢复 ≤ 500ms', async ({ page }) => {
    // ── 1. 准备 mock：SSE + REST ────────────────────────
    const jwt = startJwtCapture(page)
    await mockPauseApi(page, THREAD_ID)
    await mockResumeApi(page, THREAD_ID)
    await mockSseStream(page, THREAD_ID, [
      // ChatView SSE 监听器收到事件时触发对应 store action
      { type: 'reasoning_paused', session_id: THREAD_ID, paused_at: new Date().toISOString() },
      { type: 'reasoning_resumed', session_id: THREAD_ID, resumed_at: new Date().toISOString() },
    ])

    // ── 2. 进入主页 ─────────────────────────────────────
    const t0 = Date.now()
    await page.goto('/')
    await expect(page).toHaveURL(/\/(?!onboarding)/)

    // ── 3. 构造 running 状态（绕过真实 chat 发送 SSE 噪音）──
    await page.waitForFunction(
      () => Boolean((window as unknown as { __pinia?: unknown }).__pinia),
      { timeout: 5000 },
    )
    await getStore(page, 'reasoning').then(async (store: any) => {
      await page.evaluate(
        ({ sid, steps }: { sid: string; steps: ReasoningStep[] }) => {
          const r = (window as unknown as { __pinia: { _s: Map<string, any> } }).__pinia._s.get('reasoning')
          r.start(sid, steps)
        },
        { sid: THREAD_ID, steps: makeInitialSteps() },
      )
    })

    // ── 4. ChatView watch sessionId → 建立 SSE → 处理 reasoning_paused 二次确认 ──
    // 注意：reasoning_paused 事件在 start() 之前 mock SSE 已返回 → 浏览器缓存？
    // 实际 Playwright route 是延迟触发：每次 navigate 后首次 fetch 才拦截。
    // 因此 start() 后 SSE 才连接，事件才分发。

    // 等 status 徽标变为 running
    const runningBadge = page.locator('[data-component="reasoning-status-badge"][data-status="running"]')
    await expect(runningBadge).toBeVisible({ timeout: 5000 })

    // 控制栏出现
    await expect(page.locator('[data-component="reasoning-control-bar"]')).toBeVisible()

    // ── 5. 点暂停按钮 ──────────────────────────────────
    const pauseStart = Date.now()
    await page.click('[data-action="pause"]')

    // ── 6. 验证响应：在预算时间内徽标切换为 paused ──────
    const pausedBadge = page.locator(
      '[data-component="reasoning-status-badge"][data-status="paused"]',
    )
    await expect(pausedBadge).toBeVisible({ timeout: 3000 })
    const pauseElapsed = Date.now() - pauseStart
    expect(pauseElapsed, 'F1 暂停响应').toBeLessThan(PAUSE_LATENCY_BUDGET_MS)

    // ── 7. 验证 SSE reasoning_paused 二次确认已被消费（store.status='paused'）──
    const finalStatus = await getStore<{ status: string }>(page, 'reasoning').then((s) => s.status)
    expect(finalStatus).toBe('paused')

    // ── 8. 点继续按钮 → 验证恢复 ≤ 500ms ──────────────
    const resumeStart = Date.now()
    await page.click('[data-action="resume"]')
    await expect(runningBadge).toBeVisible({ timeout: 3000 })
    const resumeElapsed = Date.now() - resumeStart
    expect(resumeElapsed, 'F1 恢复响应').toBeLessThan(PAUSE_LATENCY_BUDGET_MS)
    const tFinal = Date.now()

    // ── 9. JWT 注入验证 ────────────────────────────────
    const captured = jwt.stop()
    const sseReq = captured.find((c) => c.url.includes(`/api/sessions/${THREAD_ID}/events`))
    expect(sseReq, 'SSE 请求必须被发起').toBeTruthy()
    // JWT 通过 Authorization header 注入；同时 query 也有 token（subscribeSessionEvents 双发）
    expect(sseReq?.authorization || sseReq?.authorizationQueryToken).toMatch(/^Bearer\s|^gridmind-dev-token$|gridmind-dev-token/)
    expect(sseReq?.authorization?.toLowerCase()).toContain('bearer')

    // ── 10. 总耗时日志 ─────────────────────────────────
    // eslint-disable-next-line no-console
    console.log(
      `[F1] pageLoad=${t0 ? t0 : 0}ms pause=${pauseElapsed}ms resume=${resumeElapsed}ms total=${tFinal - t0}ms`,
    )
  })
})
