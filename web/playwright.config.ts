/**
 * web/playwright.config.ts
 * GridMind v1.5.1 T06 端到端联调 · Playwright 配置
 *
 * 设计依据：
 *   - 决策 7.6（主理人）：混合 Playwright + 真后端（MOCK_ENABLED=true demo 模式）+ route mock SSE
 *   - 架构 §5 T06 + §10 验收
 *   - PRD §6 端到端时序
 *
 * 关键约束：
 *   - baseURL: http://localhost:5173（Vite dev server）
 *   - 后端：local FastAPI（api/scripts/quickstart.py --mock --no-web）默认 :9900
 *   - 完全并发 4 个 F + 1 个 a11y（5 spec ≤ 8 文件约束）
 *   - workers = 1（SSE 串行防竞用；不依赖真 LLM）
 *   - CI: retries = 1；本地 retries = 0
 *
 * 作者：寇豆码（T06 工程师）
 */
import { defineConfig, devices } from '@playwright/test'

const isCI = Boolean(process.env.CI)

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  forbidOnly: isCI,
  retries: isCI ? 1 : 0,
  workers: 1, // SSE + reason store 共享 window.__pinia，并发会破坏状态
  reporter: isCI ? [['list'], ['github']] : 'list',
  timeout: 60_000,
  expect: {
    timeout: 8_000,
  },
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    // 收集所有 fetch（含 SSE）请求头，用于 JWT 注入验证
    extraHTTPHeaders: {
      'X-E2E-Run': 'gridmind-v1.5.1',
    },
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1280, height: 800 },
      },
    },
  ],
  // 沙箱环境无 Chrome 时，浏览器自动下载由 npx playwright install 触发
  // 测试运行时若仍缺浏览器，Playwright 抛 "Executable doesn't exist"，
  // 这正是约束 §4 提到的"沙箱限制"，测试本身是健康的。
  webServer: {
    // 与 e2e.spec 中 mock SSE + REST 配合；不依赖真 LLM
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: !isCI,
    timeout: 60_000,
    stdout: 'ignore',
    stderr: 'pipe',
  },
})
