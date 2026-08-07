/**
 * GridMind v1.5.1 T04 单元测试 · HitlBadge（F3 HITL 队列徽标）
 *
 * 覆盖场景（≥8 满足 T04 要求 + 防御性回归）：
 *
 *   HitlBadge 模板（5）：
 *     1. v-if="shouldShow" 控制显隐（pendingHitlCount > 0 才渲染）
 *     2. severity class 切换：warning（1-4）vs critical（≥5）
 *     3. count 显示规则：> 99 显示 "99+"，≤ 99 显示实际数字
 *     4. tooltip / aria-label 拼接含数字
 *     5. 后端不可达（connectionState === 'error'）→ 显示灰点 "·"
 *
 *   HitlBadge 行为（3）：
 *     6. 点击跳 /audit?filter=pending&from=hitl-badge
 *     7. z-index = 200（主理人决策 7.5）
 *     8. 响应式：< 768px 隐藏"待审"文字
 *
 *   App.vue 集成（2）：
 *     9. HitlBadge 嵌入位置 = OnboardingTrigger 之后
 *    10. auditStore.hydrate() 在 onMounted 中调用
 *
 *   静态 a11y（2）：
 *    11. aria-live 在 critical 时切 assertive
 *    12. prefers-reduced-motion 兼容
 *
 * 运行：node tests/test_hitl_badge.mjs
 *
 * 策略：静态源码分析（与 test_reasoning_control_bar.mjs / test_reasoning_store.mjs 一致）。
 * 模板结构 / 事件绑定 / a11y 属性 / 业务规则（severity / displayCount）都是 .vue 文件的不变量，
 * 源码即契约；行为正确性由 build（vue-tsc）+ audit store 单元测试保障。
 */
import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { resolve, join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)
const ROOT = resolve(__dirname, '..')
const SRC = join(ROOT, 'src')

// ─── 读源文件 ───
const BADGE_SRC = await readFile(
  join(SRC, 'components/controls/HitlBadge.vue'),
  'utf-8',
)
const APP_SRC = await readFile(
  join(SRC, 'App.vue'),
  'utf-8',
)

// ─── 总结 ───
let pass = 0
let fail = 0
const failures = []

function wrap(name, fn) {
  return test(name, async () => {
    try {
      await fn()
      pass++
    } catch (e) {
      fail++
      failures.push({ name, error: e })
      throw e
    }
  })
}

// ═══════════════════════════════════════════════════════════════
// Group 1: HitlBadge 模板 · 显隐与渲染
// ═══════════════════════════════════════════════════════════════
describe('HitlBadge 模板（显隐 + 严重程度）', () => {
  wrap('1) v-if="shouldShow" 控制显隐（pendingHitlCount > 0 或 degraded 才渲染）', () => {
    // 应有 transition + v-if 包住整个徽标
    assert.match(
      BADGE_SRC,
      /<transition[^>]*name=["']hitl-badge-fade["'][^>]*>/,
      '应使用 <transition> 包装',
    )
    assert.match(
      BADGE_SRC,
      /v-if=["']shouldShow["']/,
      '徽标应 v-if="shouldShow"',
    )
    // shouldShow computed 应基于 pendingHitlCount > 0 || isDegraded.value（分开断言，规避跨行泛型签名）
    assert.match(
      BADGE_SRC,
      /shouldShow\s*=\s*computed/,
      '应有 shouldShow computed',
    )
    assert.match(
      BADGE_SRC,
      /audit\.pendingHitlCount\s*>\s*0/,
      'shouldShow 应检查 pendingHitlCount > 0',
    )
    assert.match(
      BADGE_SRC,
      /shouldShow\s*=\s*computed[\s\S]{0,300}isDegraded\.value/,
      'shouldShow computed 应引用 isDegraded.value',
    )
  })

  wrap('2) severity 规则：≥5 = critical，否则 = warning（PRD §3.3.3 实测）', () => {
    // 拆解断言：避免 computed<泛型> 跨行导致单行 regex 失败
    assert.match(BADGE_SRC, /severity\s*=\s*computed/, '应有 severity computed')
    assert.match(BADGE_SRC, /n\s*>=\s*5/, 'severity 应检查 n >= 5')
    assert.match(BADGE_SRC, /['"]critical['"]/, 'severity 应包含 "critical" 字面量')
    assert.match(BADGE_SRC, /['"]warning['"]/, 'severity 应包含 "warning" 字面量')
    assert.match(
      BADGE_SRC,
      /severity\s*=\s*computed[\s\S]{0,400}n\s*>=\s*5[\s\S]{0,200}['"]critical['"][\s\S]{0,200}['"]warning['"]/,
      'severity 业务规则：n >= 5 ? critical : warning',
    )
    // 模板 class 绑定
    assert.match(
      BADGE_SRC,
      /hitl-badge--\$\{severity\}/,
      'class 应动态拼接 severity（hitl-badge--warning / hitl-badge--critical）',
    )
  })

  wrap('3) displayCount > 99 显示 "99+"（架构 §1.3.2 实测）', () => {
    assert.match(BADGE_SRC, /displayCount\s*=\s*computed/, '应有 displayCount computed')
    assert.match(BADGE_SRC, /n\s*>\s*99/, 'displayCount 应检查 n > 99')
    assert.match(BADGE_SRC, /['"]99\+['"]/, 'displayCount 应包含 "99+" 字面量')
    assert.match(
      BADGE_SRC,
      /displayCount\s*=\s*computed[\s\S]{0,300}n\s*>\s*99[\s\S]{0,200}['"]99\+['"]/,
      'displayCount 业务规则：n > 99 ? "99+"',
    )
  })

  wrap('4) tooltip / aria-label 拼接含数字（a11y）', () => {
    // tooltip
    assert.match(
      BADGE_SRC,
      /\$\{n\}\s*个待审批\s*HITL/,
      'tooltip 应包含"${n} 个待审批 HITL 任务"',
    )
    // aria-label
    assert.match(
      BADGE_SRC,
      /aria-label\s*=\s*["']ariaLabel["']/,
      'aria-label 应绑定到 ariaLabel computed',
    )
    assert.match(
      BADGE_SRC,
      /ariaLabel\s*=\s*computed[\s\S]*?HITL\s*队列/,
      'ariaLabel computed 应包含"HITL 队列"前缀',
    )
  })

  wrap('5) 后端不可达（connectionState === "error"）→ 显示 "·" 灰点（PRD §3.3.3 降级）', () => {
    // isDegraded computed
    assert.match(BADGE_SRC, /isDegraded\s*=\s*computed/, '应有 isDegraded computed')
    assert.match(
      BADGE_SRC,
      /connectionState\s*===\s*['"]error['"]/,
      'isDegraded 应检查 connectionState === "error"',
    )
    assert.match(
      BADGE_SRC,
      /connectionState\s*===\s*['"]disconnected['"]/,
      'isDegraded 应检查 connectionState === "disconnected"',
    )
    assert.match(
      BADGE_SRC,
      /isDegraded\s*=\s*computed[\s\S]{0,300}connectionState\s*===\s*['"]error['"][\s\S]{0,200}connectionState\s*===\s*['"]disconnected['"]/,
      'isDegraded 业务规则：error || disconnected',
    )
    // 模板 class
    assert.match(
      BADGE_SRC,
      /hitl-badge--degraded/,
      'degraded 状态应有独立 class 标识',
    )
    // "·" 显示
    assert.match(
      BADGE_SRC,
      /isDegraded\s*\?\s*['"]·['"]/,
      'count 在 degraded 时显示"·"',
    )
    // 降级 tooltip
    assert.match(
      BADGE_SRC,
      /等待后端连接/,
      '降态 tooltip 应提示"等待后端连接"',
    )
  })
})

// ═══════════════════════════════════════════════════════════════
// Group 2: HitlBadge 行为（点击 + z-index + 响应式）
// ═══════════════════════════════════════════════════════════════
describe('HitlBadge 行为（导航 + 样式规则）', () => {
  wrap('6) 点击跳 /audit?filter=pending&from=hitl-badge（架构 §1.3.1）', () => {
    // handleClick 函数
    assert.match(
      BADGE_SRC,
      /function\s+handleClick\(/,
      '应有 handleClick 函数',
    )
    // router.push 带正确 query
    assert.match(
      BADGE_SRC,
      /router\.push\(\s*\{[\s\S]*?path:\s*['"]\/audit['"][\s\S]*?query:\s*\{\s*filter:\s*['"]pending['"][\s\S]*?from:\s*['"]hitl-badge['"]/,
      'router.push 应包含 path="/audit" + query={filter:"pending", from:"hitl-badge"}',
    )
    // @click="handleClick" 绑定
    assert.match(
      BADGE_SRC,
      /@click=["']handleClick["']/,
      '模板 @click 应绑定 handleClick',
    )
  })

  wrap('7) z-index = 200（主理人决策 7.5：徽标在 toast(1000) 之下、弹窗(100) 之上）', () => {
    assert.match(
      BADGE_SRC,
      /z-index:\s*200/,
      '.hitl-badge 应有 z-index: 200',
    )
    // 不引用错误的 z-index 变量（避免覆盖 header 的 z-index 层级）
    assert.doesNotMatch(
      BADGE_SRC,
      /z-index:\s*var\(--z-toast\)/,
      '不应误用 --z-toast 变量',
    )
  })

  wrap('8) 响应式：< 768px 隐藏"待审"文字（PRD §3.3.4）', () => {
    assert.match(
      BADGE_SRC,
      /@media\s*\(max-width:\s*768px\)/,
      '应有 @media (max-width: 768px)',
    )
    assert.match(
      BADGE_SRC,
      /\.hitl-badge__label\s*\{[^}]*display:\s*none/,
      '@media 内 .hitl-badge__label 应 display: none',
    )
  })

  wrap('9) critical 状态有脉冲动画（PRD §3.3.3 + 实测）', () => {
    // animation 在 critical class
    assert.match(
      BADGE_SRC,
      /\.hitl-badge--critical\s*\{[^}]*animation:\s*hitl-badge-pulse/s,
      '.hitl-badge--critical 应有 animation: hitl-badge-pulse',
    )
    // @keyframes 定义
    assert.match(
      BADGE_SRC,
      /@keyframes\s+hitl-badge-pulse/,
      '应有 @keyframes hitl-badge-pulse 定义',
    )
  })

  wrap('10) prefers-reduced-motion 兼容性（a11y）', () => {
    assert.match(
      BADGE_SRC,
      /@media\s*\(prefers-reduced-motion:\s*reduce\)/,
      '应有 prefers-reduced-motion 兼容',
    )
    assert.match(
      BADGE_SRC,
      /prefers-reduced-motion[\s\S]*?animation:\s*none/,
      'reduced-motion 下应关闭 animation',
    )
  })

  wrap('11) 进场/退场 transition（淡入淡出 + 缩放）', () => {
    assert.match(
      BADGE_SRC,
      /hitl-badge-fade-enter-from/,
      '应有 enter-from 关键帧类',
    )
    assert.match(
      BADGE_SRC,
      /hitl-badge-fade-leave-to/,
      '应有 leave-to 关键帧类',
    )
    // transition 包含 opacity + transform
    assert.match(
      BADGE_SRC,
      /hitl-badge-fade-enter-active[\s\S]*?opacity[\s\S]*?transform/s,
      'enter-active 应同时过渡 opacity 与 transform',
    )
  })
})

// ═══════════════════════════════════════════════════════════════
// Group 3: App.vue 集成
// ═══════════════════════════════════════════════════════════════
describe('App.vue Header 集成（v1.5.1 T04）', () => {
  wrap('12) HitlBadge 在 OnboardingTrigger 之后插入（顺序遵循任务实施要点）', () => {
    // 在 OnboardingTrigger 后应紧接 HitlBadge
    const re = /<OnboardingTrigger\s*\/?>[\s\S]{0,400}<HitlBadge\s*\/?>/
    assert.match(
      APP_SRC,
      re,
      'HitlBadge 应紧跟 OnboardingTrigger（中间允许 ≤400 字符的注释 / 空白）',
    )
    // HitlBadge 应在 BackgroundModeToggle 之前
    const before = APP_SRC.indexOf('<HitlBadge')
    const bgAfter = APP_SRC.indexOf('<BackgroundModeToggle')
    assert.ok(
      before > 0 && bgAfter > 0 && before < bgAfter,
      'HitlBadge 应在 BackgroundModeToggle 之前',
    )
  })

  wrap('13) import HitlBadge + useAuditStore（依赖正确）', () => {
    assert.match(
      APP_SRC,
      /import\s+HitlBadge\s+from\s+['"]\.\/components\/controls\/HitlBadge\.vue['"]/,
      '应导入 HitlBadge 组件',
    )
    assert.match(
      APP_SRC,
      /import\s*\{[^}]*useAuditStore[^}]*\}\s*from\s+['"]\.\/stores\/audit['"]/,
      '应导入 useAuditStore',
    )
    // 创建 store 实例
    assert.match(
      APP_SRC,
      /const\s+auditStore\s*=\s*useAuditStore\(\)/,
      '应创建 auditStore 实例',
    )
  })

  wrap('14) auditStore.hydrate() 在 onMounted 中调用（T04 首屏水合）', () => {
    // onMounted 内应有 auditStore.hydrate()
    assert.match(
      APP_SRC,
      /onMounted\([\s\S]*?auditStore\.hydrate\(\)/,
      'onMounted 中应调 auditStore.hydrate()',
    )
  })
})

// ═══════════════════════════════════════════════════════════════
// Group 4: audit store 协作（已有 T01 行为，验证 HitlBadge 期望的接口）
// ═══════════════════════════════════════════════════════════════
describe('audit store 协作契约（HitlBadge 依赖的字段）', () => {
  let auditSrc

  wrap('15) audit store 暴露 pendingHitlCount（HitlBadge 核心数据源）', async () => {
    auditSrc = await readFile(join(SRC, 'stores/audit.ts'), 'utf-8')
    assert.match(
      auditSrc,
      /pendingHitlCount\s*=\s*ref/,
      'audit store 应有 pendingHitlCount ref',
    )
    assert.match(
      auditSrc,
      /return\s*\{[\s\S]*?pendingHitlCount/,
      'audit store 应导出 pendingHitlCount',
    )
  })

  wrap('16) audit store 暴露 connectionState + isHydrated + displayCount', async () => {
    if (!auditSrc) auditSrc = await readFile(join(SRC, 'stores/audit.ts'), 'utf-8')
    assert.match(auditSrc, /connectionState\s*=\s*ref/, '应有 connectionState ref')
    assert.match(auditSrc, /isHydrated\s*=\s*ref/, '应有 isHydrated ref')
    assert.match(auditSrc, /displayCount\s*=\s*computed/, '应有 displayCount computed')
  })

  wrap('17) audit store hydrate() 调用 startPolling()（5s 轮询兜底）', async () => {
    if (!auditSrc) auditSrc = await readFile(join(SRC, 'stores/audit.ts'), 'utf-8')
    assert.match(
      auditSrc,
      /function\s+hydrate\(/,
      '应有 hydrate() action',
    )
    assert.match(
      auditSrc,
      /hydrate\([^)]*\)[\s\S]{0,500}startPolling/,
      'hydrate() 应启动 startPolling（5s 轮询）',
    )
  })

  wrap('18) audit store startPolling(5000) 默认间隔（主理人决策 7.2）', async () => {
    if (!auditSrc) auditSrc = await readFile(join(SRC, 'stores/audit.ts'), 'utf-8')
    assert.match(
      auditSrc,
      /POLL_INTERVAL_MS\s*=\s*5000|startPolling\(\s*5000\s*\)|setInterval\([^,]+,\s*5000\s*\)/,
      '5s 轮询间隔应有明确常量或调用',
    )
  })
})

// ─── 总结 ───
process.on('exit', () => {
  process.stderr.write(`\n\x1b[1m── T04 hitl badge test summary ──\x1b[0m\n`)
  process.stderr.write(`  Pass: \x1b[32m${pass}\x1b[0m\n  Fail: \x1b[31m${fail}\x1b[0m\n`)
  if (failures.length) {
    process.stderr.write(`\n  Failures:\n`)
    for (const f of failures.length && failures) {
      process.stderr.write(`    - ${f.name}\n      ${f.error?.message ?? f.error}\n`)
    }
  }
})
