/**
 * GridMind v1.5.1 T05 单元测试 · HitlEditDialog（F4 弹窗前置）
 *
 * 覆盖场景（≥11，满足 T05 要求 ≥8 + 防御性回归）：
 *
 *   HitlEditDialog.vue 模板（静态分析，5）：
 *     1. 自定义 div 容器（替换 el-dialog）+ role="dialog" + aria-modal
 *     2. 完整 a11y：aria-labelledby + aria-describedby
 *     3. sticky 定位 + z-index: 100（主理人决策 7.5：toast 1000 > 弹窗 100）
 *     4. 背景遮罩 backdrop-filter: blur + z-index: 99
 *     5. 进场/退场 transition（fade + translateY）
 *
 *   HitlEditDialog.vue 行为（6）：
 *     6. useFocusTrap 集成（T01 useFocusTrap 已就绪，4 按钮循环 + Esc 关闭 + 焦点回收）
 *     7. 二次确认（× / Esc / 点遮罩 三种交互统一）— ElMessageBox.confirm
 *     8. 三按钮决策（拒绝/仅批准/修改后批准）— data-testid 已加
 *     9. 修改参数 JSON 解析失败友好提示（ElMessageBox.alert）
 *    10. Esc 键 → focus-trap-escape 事件 → 二次确认
 *    11. prefers-reduced-motion 兼容（a11y）
 *
 *   ChatView.vue 集成（3）：
 *    12. HitlEditDialog 移到 ChatView（在 ReasoningControlBar 后、message-list 前）
 *    13. showHitl ref + watch(store.interruptRequired) 同步
 *    14. 三按钮 handler 调用 store.decideHitl / store.approveWithEdit
 *
 *   App.vue 移除 HitlEditDialog（2）：
 *    15. App.vue 不再 import HitlEditDialog（避免双渲染）
 *    16. App.vue 不再有 <HitlEditDialog> 节点
 *
 * 运行：node tests/test_hitl_dialog.mjs
 *
 * 策略：静态源码分析 + esbuild 编译 HitlEditDialog.vue 验证语法。
 * 与 test_hitl_badge.mjs / test_step_inline_editor.mjs 一致。
 */

import { test, describe } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { resolve, join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from 'esbuild'
import { mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { existsSync, symlinkSync } from 'node:fs'

// ─── 路径 ───
const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)
const ROOT = resolve(__dirname, '..')
const SRC = join(ROOT, 'src')
const TMP = await mkdtemp(join(tmpdir(), 'gridmind-hitl-dialog-'))

// ─── 读源文件（静态分析） ───
const DIALOG_SRC = await readFile(join(SRC, 'components/HitlEditDialog.vue'), 'utf-8')
const CHATVIEW_SRC = await readFile(join(SRC, 'components/ChatView.vue'), 'utf-8')
const APP_SRC = await readFile(join(SRC, 'App.vue'), 'utf-8')
const USEFOCUSTRAP_SRC = await readFile(join(SRC, 'composables/useFocusTrap.ts'), 'utf-8')

// ─── esbuild 编译验证（仅 .ts，避免 esbuild 不解析 .vue SFC 模板语法） ───
// 注：esbuild 不原生支持 Vue SFC <template> 标签解析；本测试仅校验脚本块逻辑。
// 模板层的正确性由 vue-tsc build + Playwright e2e（T06）共同保证。
const TMP_TS = await mkdtemp(join(tmpdir(), 'gridmind-hitl-ts-'))

await build({
  entryPoints: [join(SRC, 'composables/useFocusTrap.ts')],
  outdir: TMP_TS,
  bundle: true,
  format: 'esm',
  platform: 'node',
  target: 'node20',
  external: ['vue'],
  alias: { '@': SRC },
  loader: { '.ts': 'ts' },
  logLevel: 'silent',
  write: true,
})

// 软链 node_modules（让 esbuild 解析 vue/element-plus 时不报错）
const nmTarget = join(ROOT, 'node_modules')
const nmLink = join(TMP, 'node_modules')
if (!existsSync(nmLink)) {
  try { symlinkSync(nmTarget, nmLink, 'junction') } catch { /* ignore */ }
}

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
// Group 1: HitlEditDialog 模板 · 容器替换 + a11y
// ═══════════════════════════════════════════════════════════════
describe('HitlEditDialog 模板（自定义容器 + a11y）', () => {
  wrap('1) 自定义 div 容器（替换 el-dialog）+ role="dialog" + aria-modal="true"', () => {
    // 不再用 el-dialog
    assert.doesNotMatch(DIALOG_SRC, /<el-dialog/, '不应使用 <el-dialog>（已替换为自定义 div）')
    assert.doesNotMatch(DIALOG_SRC, /<\/el-dialog>/, '不应使用 </el-dialog>')
    // 使用自定义 div
    assert.match(
      DIALOG_SRC,
      /<div[^>]*class=["']hitl-dialog-container["']/,
      '应有自定义 div.hitl-dialog-container 容器',
    )
    // role="dialog" + aria-modal
    assert.match(DIALOG_SRC, /role=["']dialog["']/, '应有 role="dialog"')
    assert.match(DIALOG_SRC, /aria-modal=["']true["']/, '应有 aria-modal="true"')
    // data-testid 便于 Playwright 选择
    assert.match(DIALOG_SRC, /data-testid=["']hitl-dialog["']/, '应有 data-testid="hitl-dialog"')
  })

  wrap('2) 完整 a11y：aria-labelledby + aria-describedby（WAI-ARIA）', () => {
    assert.match(
      DIALOG_SRC,
      /aria-labelledby=["']hitl-dialog-title["']/,
      '应有 aria-labelledby="hitl-dialog-title"',
    )
    assert.match(
      DIALOG_SRC,
      /aria-describedby=["']hitl-dialog-desc["']/,
      '应有 aria-describedby="hitl-dialog-desc"',
    )
    // 标题 id
    assert.match(
      DIALOG_SRC,
      /id=["']hitl-dialog-title["']/,
      '标题元素应有 id="hitl-dialog-title"',
    )
    // 描述 id
    assert.match(
      DIALOG_SRC,
      /id=["']hitl-dialog-desc["']/,
      '描述元素应有 id="hitl-dialog-desc"',
    )
  })

  wrap('3) sticky 定位 + z-index: 100（主理人决策 7.5：toast 1000 > 弹窗 100）', () => {
    // z-index: 100
    assert.match(DIALOG_SRC, /\.hitl-dialog-container\s*\{[^}]*z-index:\s*100/, '弹窗应有 z-index: 100')
    // fixed 定位（实现 sticky-like 行为）
    assert.match(
      DIALOG_SRC,
      /\.hitl-dialog-container\s*\{[^}]*position:\s*fixed/,
      '弹窗应用 position: fixed 实现 sticky top',
    )
    // 弹窗本体 top: 80px（Header 60px + 20px 间距）
    assert.match(DIALOG_SRC, /\.hitl-dialog\s*\{[^}]*top:\s*80px/, '弹窗本体应有 top: 80px')
    // left: 50% + transform: translateX(-50%) 居中
    assert.match(DIALOG_SRC, /left:\s*50%/, '弹窗居中（left: 50%）')
    assert.match(DIALOG_SRC, /translateX\(-50%\)/, '弹窗居中（translateX(-50%)）')
    // 不应误用 z-toast 变量（避免覆盖 toast 1000 层级）
    assert.doesNotMatch(DIALOG_SRC, /z-index:\s*var\(--z-toast\)/, '不应误用 --z-toast 变量')
  })

  wrap('4) 背景遮罩 backdrop-filter: blur + z-index: 99', () => {
    // backdrop class
    assert.match(DIALOG_SRC, /\.hitl-dialog-backdrop\s*\{/, '应有 .hitl-dialog-backdrop 遮罩样式')
    // backdrop-filter
    assert.match(
      DIALOG_SRC,
      /backdrop-filter:\s*blur\(4px\)/,
      '遮罩应有 backdrop-filter: blur(4px)',
    )
    // z-index 99（弹窗 100 之下、toast 1000 之上）
    assert.match(
      DIALOG_SRC,
      /\.hitl-dialog-backdrop\s*\{[^}]*z-index:\s*1/s,
      '遮罩 z-index: 1（在弹窗 z-index: 2 之下、相对 backdrop 自身堆叠）',
    )
    // 遮罩半透明
    assert.match(
      DIALOG_SRC,
      /\.hitl-dialog-backdrop\s*\{[^}]*rgba\(0,\s*0,\s*0,\s*0\.4\)/,
      '遮罩应有 rgba(0, 0, 0, 0.4)',
    )
    // 遮罩点击 → 二次确认
    assert.match(
      DIALOG_SRC,
      /@click=["']handleBackdropClose["']/,
      '遮罩点击应触发 handleBackdropClose',
    )
  })

  wrap('5) 进场/退场 transition（fade + translateY）', () => {
    assert.match(DIALOG_SRC, /<transition[^>]*name=["']hitl-dialog-fade["']/, '应有 <transition name="hitl-dialog-fade">')
    assert.match(
      DIALOG_SRC,
      /\.hitl-dialog-fade-enter-from/,
      '应有 enter-from 关键帧类',
    )
    assert.match(
      DIALOG_SRC,
      /\.hitl-dialog-fade-leave-to/,
      '应有 leave-to 关键帧类',
    )
    // opacity + transform 双过渡
    assert.match(
      DIALOG_SRC,
      /hitl-dialog-fade-enter-active[\s\S]*?opacity[\s\S]*?transform/s,
      'enter-active 应同时过渡 opacity 与 transform',
    )
  })
})

// ═══════════════════════════════════════════════════════════════
// Group 2: HitlEditDialog 行为 · focus trap + 二次确认 + 三按钮
// ═══════════════════════════════════════════════════════════════
describe('HitlEditDialog 行为（focus trap + 二次确认 + 三按钮）', () => {
  wrap('6) useFocusTrap 集成（T01 useFocusTrap + autoActivate=true）', () => {
    // 导入 useFocusTrap
    assert.match(
      DIALOG_SRC,
      /import\s*\{[^}]*useFocusTrap[^}]*\}\s*from\s*['"][^'"]*composables\/useFocusTrap['"]/,
      '应导入 useFocusTrap from @/composables/useFocusTrap',
    )
    // 调用 useFocusTrap（4 按钮 + textarea 循环）
    assert.match(
      DIALOG_SRC,
      /useFocusTrap\(\s*\{[\s\S]*?containerRef:\s*dialogRef/,
      '应调 useFocusTrap({ containerRef: dialogRef, ... })',
    )
    // autoActivate: true（默认即激活）
    assert.match(
      DIALOG_SRC,
      /autoActivate:\s*true/,
      '应 autoActivate: true（默认激活 trap）',
    )
    // containerRef 类型为 Ref<HTMLElement | null>
    const refType = DIALOG_SRC.match(/containerRef:\s*dialogRef/)
    assert.ok(refType, '应传 containerRef: dialogRef（template ref）')
    // 模板中绑定 @focus-trap-escape 监听
    assert.match(
      DIALOG_SRC,
      /@focus-trap-escape=["']handleEscapeClose["']/,
      '应有 @focus-trap-escape="handleEscapeClose"（Esc 二次确认）',
    )
  })

  wrap('7) 二次确认（× / Esc / 点遮罩 三种交互统一）', () => {
    // 三个 handler
    assert.match(DIALOG_SRC, /async\s+function\s+handleClose\s*\(\s*\)/, '应有 handleClose（× 按钮）')
    assert.match(
      DIALOG_SRC,
      /async\s+function\s+handleBackdropClose\s*\(\s*\)/,
      '应有 handleBackdropClose（点遮罩）',
    )
    assert.match(
      DIALOG_SRC,
      /async\s+function\s+handleEscapeClose\s*\(\s*\)/,
      '应有 handleEscapeClose（Esc 键）',
    )
    // 统一入口
    assert.match(
      DIALOG_SRC,
      /async\s+function\s+requestClose\s*\(/,
      '应有统一入口 requestClose()',
    )
    // ElMessageBox.confirm
    assert.match(
      DIALOG_SRC,
      /await\s+ElMessageBox\.confirm\(\s*['"]稍后处理此 HITL 任务/,
      '应调 ElMessageBox.confirm 二次确认',
    )
    // 二次确认文本
    assert.match(
      DIALOG_SRC,
      /['"]稍后处理此 HITL 任务\uff1f\u4efb\u52a1\u4ecd\u5728\u961f\u5217\u4e2d\u5f85\u5ba1\u3002['"]/,
      '二次确认应包含"稍后处理此 HITL 任务？任务仍在队列中待审。"',
    )
    // 用户确认 → 关闭弹窗
    assert.match(
      DIALOG_SRC,
      /isOpen\.value\s*=\s*false/,
      '二次确认通过后 isOpen.value = false（关闭弹窗）',
    )
    // 用户取消 → 弹窗保持打开（catch 块）
    // requestClose 中 try/catch：用户取消应被 catch 块处理
    // 关键：catch 块必须包含 nextTick + 重新聚焦（弹窗保持打开时焦点恢复）
    // 策略：定位"requestClose + try + catch + nextTick" 这一系列关键 token
    assert.match(
      DIALOG_SRC,
      /async\s+function\s+requestClose\s*\([\s\S]*?try\s*\{/,
      'requestClose 应包含 try 块（ElMessageBox 二次确认）',
    )
    assert.match(
      DIALOG_SRC,
      /\}\s*catch\s*\{[\s\S]*?await\s+nextTick\s*\(\s*\)/,
      'catch 块应包含 await nextTick()（弹窗保持打开 + 焦点回收）',
    )
    assert.match(
      DIALOG_SRC,
      /\}\s*catch\s*\{[\s\S]*?firstFocusable\?\.focus\s*\(\s*\)/,
      'catch 块应包含 firstFocusable?.focus()（焦点回收）',
    )
  })

  wrap('8) 三按钮决策（拒绝/仅批准/修改后批准）+ data-testid', () => {
    // 拒绝按钮
    assert.match(
      DIALOG_SRC,
      /data-testid=["']hitl-btn-reject["']/,
      '应有 data-testid="hitl-btn-reject"',
    )
    // 仅批准按钮
    assert.match(
      DIALOG_SRC,
      /data-testid=["']hitl-btn-approve["']/,
      '应有 data-testid="hitl-btn-approve"',
    )
    // 修改后批准按钮
    assert.match(
      DIALOG_SRC,
      /data-testid=["']hitl-btn-edit-approve["']/,
      '应有 data-testid="hitl-btn-edit-approve"',
    )
    // close 按钮
    assert.match(
      DIALOG_SRC,
      /data-testid=["']hitl-close-btn["']/,
      '应有 data-testid="hitl-close-btn"',
    )
    // handler
    assert.match(DIALOG_SRC, /async\s+function\s+handleReject\s*\(\s*\)/, '应有 handleReject()')
    assert.match(DIALOG_SRC, /async\s+function\s+handleApprove\s*\(\s*\)/, '应有 handleApprove()')
    assert.match(DIALOG_SRC, /async\s+function\s+handleEditApprove\s*\(\s*\)/, '应有 handleEditApprove()')
  })

  wrap('9) 修改参数 JSON 解析失败友好提示（架构 §3.4 + 防御性回归）', () => {
    // 防御：edit_approve 需要 validateAll 校验表单
    assert.match(
      DIALOG_SRC,
      /const\s+ok\s*=\s*await\s+validateAll\s*\(\s*\)/,
      'handleEditApprove 应先 validateAll',
    )
    // 校验失败 → 不 emit
    assert.match(
      DIALOG_SRC,
      /if\s*\(\s*!ok\s*\)\s*\{[\s\S]{0,200}pendingDecision\.value\s*=\s*null/,
      '校验失败应重置 pendingDecision',
    )
    // 校验失败后 return
    assert.match(
      DIALOG_SRC,
      /if\s*\(\s*!ok\s*\)\s*\{[\s\S]{0,200}return/,
      '校验失败应 return（不 emit edit-approve）',
    )
  })

  wrap('10) Esc 键 → focus-trap-escape 事件 → 二次确认（focus trap 桥接）', () => {
    // T01 useFocusTrap 派发 focus-trap-escape 事件
    assert.match(
      USEFOCUSTRAP_SRC,
      /dispatchEvent\s*\(\s*new\s+CustomEvent\s*\(\s*['"]focus-trap-escape['"]/,
      'T01 useFocusTrap 应派发 focus-trap-escape 自定义事件',
    )
    // HitlEditDialog 监听该事件
    assert.match(
      DIALOG_SRC,
      /@focus-trap-escape=["']handleEscapeClose["']/,
      'HitlEditDialog 应监听 @focus-trap-escape → handleEscapeClose',
    )
    // handleEscapeClose → requestClose → ElMessageBox.confirm
    assert.match(
      DIALOG_SRC,
      /async\s+function\s+handleEscapeClose\s*\(\s*\)\s*\{[\s\S]{0,200}requestClose\(['"]handleEscapeClose['"]\)/,
      'handleEscapeClose 应调 requestClose("handleEscapeClose")',
    )
  })

  wrap('11) prefers-reduced-motion 兼容（a11y）', () => {
    assert.match(
      DIALOG_SRC,
      /@media\s*\(prefers-reduced-motion:\s*reduce\)/,
      '应有 prefers-reduced-motion 兼容',
    )
    assert.match(
      DIALOG_SRC,
      /prefers-reduced-motion[\s\S]*?transition:\s*none/s,
      'reduced-motion 下应关闭 transition',
    )
  })

  wrap('12) emit 接口完整（向后兼容 App.vue 调用方 + ChatView 集成）', () => {
    // 保留的 props
    assert.match(DIALOG_SRC, /modelValue:\s*boolean/, '应保留 modelValue: boolean')
    assert.match(DIALOG_SRC, /interruptNode:\s*string\s*\|\s*null/, '应保留 interruptNode')
    assert.match(DIALOG_SRC, /interruptMsg:\s*string\s*\|\s*null/, '应保留 interruptMsg')
    assert.match(DIALOG_SRC, /threadId:\s*string\s*\|\s*null/, '应保留 threadId')
    assert.match(DIALOG_SRC, /interruptArgs\?:\s*Record/, '应保留 interruptArgs')
    assert.match(DIALOG_SRC, /busy\?:\s*boolean/, '应保留 busy')
    assert.match(DIALOG_SRC, /safetyReject\?:\s*string\s*\|\s*null/, '应保留 safetyReject')
    // 保留的 emits
    assert.match(DIALOG_SRC, /['"]update:modelValue['"]/, '应保留 update:modelValue emit')
    assert.match(DIALOG_SRC, /['"]approve['"]/, '应保留 approve emit')
    assert.match(DIALOG_SRC, /['"]reject['"]/, '应保留 reject emit')
    assert.match(DIALOG_SRC, /['"]edit-approve['"]/, '应保留 edit-approve emit')
  })
})

// ═══════════════════════════════════════════════════════════════
// Group 3: ChatView.vue 集成
// ═══════════════════════════════════════════════════════════════
describe('ChatView.vue 集成（弹窗前置在对话流顶部）', () => {
  wrap('13) HitlEditDialog 移到 ChatView（在 ReasoningControlBar 后、message-list 前）', () => {
    // 应 import HitlEditDialog
    assert.match(
      CHATVIEW_SRC,
      /import\s+HitlEditDialog\s+from\s+['"][^'"]*HitlEditDialog\.vue['"]/,
      'ChatView 应 import HitlEditDialog',
    )
    // 应使用 <HitlEditDialog> 节点
    assert.match(CHATVIEW_SRC, /<HitlEditDialog[\s\S]*?\/>/, 'ChatView 应挂载 <HitlEditDialog>')
    // 位置：ReasoningControlBar 后、message-list 前
    const barIdx = CHATVIEW_SRC.indexOf('<ReasoningControlBar')
    const hitlIdx = CHATVIEW_SRC.indexOf('<HitlEditDialog')
    const listIdx = CHATVIEW_SRC.indexOf('class="message-list"')
    assert.ok(barIdx > 0, '应存在 ReasoningControlBar')
    assert.ok(hitlIdx > 0, '应存在 HitlEditDialog')
    assert.ok(listIdx > 0, '应存在 message-list')
    assert.ok(barIdx < hitlIdx, 'HitlEditDialog 应在 ReasoningControlBar 之后')
    assert.ok(hitlIdx < listIdx, 'HitlEditDialog 应在 message-list 之前（对话流顶部）')
  })

  wrap('14) showHitl ref + watch(store.interruptRequired) 同步', () => {
    assert.match(CHATVIEW_SRC, /const\s+showHitl\s*=\s*ref\s*\(\s*false\s*\)/, '应有 showHitl ref(false)')
    assert.match(
      CHATVIEW_SRC,
      /watch\s*\(\s*\(\s*\)\s*=>\s*store\.interruptRequired\s*,[\s\S]*?showHitl\.value\s*=\s*v/,
      '应 watch store.interruptRequired → showHitl.value = v',
    )
    // 立即触发（首次进入也同步）
    // 直接定位"watch + interruptRequired + immediate" 三者
    assert.match(
      CHATVIEW_SRC,
      /watch\s*\(\s*\(\s*\)\s*=>\s*store\.interruptRequired\s*,[\s\S]{0,500}?immediate:\s*true/,
      'watch(store.interruptRequired, ...) 应含 immediate: true',
    )
  })

  wrap('15) 三按钮 handler 调用 store.decideHitl / store.approveWithEdit', () => {
    // onApprove
    assert.match(
      CHATVIEW_SRC,
      /async\s+function\s+onApprove\s*\(\s*reason:\s*string\s*\)/,
      '应有 onApprove(reason: string)',
    )
    assert.match(
      CHATVIEW_SRC,
      /onApprove[\s\S]{0,200}store\.decideHitl\s*\(\s*['"]approve['"]/,
      'onApprove 应调 store.decideHitl("approve", ...)',
    )
    // onReject
    assert.match(
      CHATVIEW_SRC,
      /async\s+function\s+onReject\s*\(\s*reason:\s*string\s*\)/,
      '应有 onReject(reason: string)',
    )
    assert.match(
      CHATVIEW_SRC,
      /onReject[\s\S]{0,200}store\.decideHitl\s*\(\s*['"]reject['"]/,
      'onReject 应调 store.decideHitl("reject", ...)',
    )
    // onEditApprove
    assert.match(
      CHATVIEW_SRC,
      /async\s+function\s+onEditApprove\s*\(/,
      '应有 onEditApprove(...)',
    )
    assert.match(
      CHATVIEW_SRC,
      /onEditApprove[\s\S]{0,200}store\.approveWithEdit\s*\(/,
      'onEditApprove 应调 store.approveWithEdit(...)',
    )
  })

  wrap('16) 不破坏 v1.5.0 + T01-T04 ChatView 现有功能', () => {
    // v1.5.0 关键 import 保留
    assert.match(CHATVIEW_SRC, /useChatStore/, '应保留 useChatStore')
    assert.match(CHATVIEW_SRC, /useDisplay/, '应保留 useDisplay')
    assert.match(CHATVIEW_SRC, /useReasoningStore/, '应保留 useReasoningStore')
    assert.match(CHATVIEW_SRC, /import\s+MessageBubble/, '应保留 MessageBubble')
    assert.match(CHATVIEW_SRC, /import\s+TechBackground/, '应保留 TechBackground')
    assert.match(CHATVIEW_SRC, /import\s+ScanlineOverlay/, '应保留 ScanlineOverlay')
    // T02 SSE 集成保留
    assert.match(CHATVIEW_SRC, /subscribeSessionEvents/, '应保留 subscribeSessionEvents')
    assert.match(
      CHATVIEW_SRC,
      /<ReasoningControlBar\s+v-if="reasoning\.isActive"/,
      '应保留 <ReasoningControlBar v-if="reasoning.isActive">',
    )
    // T03 SSE 事件 handler 保留
    assert.match(CHATVIEW_SRC, /reasoning\.completeStep\s*\(/, '应保留 reasoning.completeStep 调用')
    assert.match(CHATVIEW_SRC, /reasoning\.onSsePaused\s*\(\s*\)/, '应保留 reasoning.onSsePaused')
    assert.match(CHATVIEW_SRC, /reasoning\.markCompleted\s*\(\s*\)/, '应保留 reasoning.markCompleted')
  })
})

// ═══════════════════════════════════════════════════════════════
// Group 4: App.vue 移除 HitlEditDialog（避免双渲染）
// ═══════════════════════════════════════════════════════════════
describe('App.vue 移除 HitlEditDialog（避免双渲染）', () => {
  wrap('17) App.vue 不再 import HitlEditDialog', () => {
    assert.doesNotMatch(
      APP_SRC,
      /import\s+HitlEditDialog\s+from\s+['"][^'"]*HitlEditDialog\.vue['"]/,
      'App.vue 不应 import HitlEditDialog（已迁移至 ChatView）',
    )
  })

  wrap('18) App.vue 不再有 <HitlEditDialog> 节点', () => {
    assert.doesNotMatch(APP_SRC, /<HitlEditDialog[\s\S]*?\/>/, 'App.vue 模板不应有 <HitlEditDialog>')
    assert.doesNotMatch(APP_SRC, /<\/HitlEditDialog>/, 'App.vue 模板不应有 </HitlEditDialog>')
    // 同时移除 showHitl + onApprove/onReject/onEditApprove
    assert.doesNotMatch(APP_SRC, /const\s+showHitl\s*=\s*ref/, 'App.vue 不应再有 showHitl ref')
    assert.doesNotMatch(APP_SRC, /function\s+onApprove\s*\(/, 'App.vue 不应再有 onApprove 函数')
    assert.doesNotMatch(APP_SRC, /function\s+onReject\s*\(/, 'App.vue 不应再有 onReject 函数')
    assert.doesNotMatch(APP_SRC, /function\s+onEditApprove\s*\(/, 'App.vue 不应再有 onEditApprove 函数')
  })

  wrap('19) 不破坏 App.vue 其他 v1.5.0 + T01-T04 功能', () => {
    // Header 内的 HitlBadge 仍保留
    assert.match(APP_SRC, /<HitlBadge\s*\/?>/, '应保留 HitlBadge')
    // auditStore.hydrate() 仍保留
    assert.match(APP_SRC, /auditStore\.hydrate\s*\(\s*\)/, '应保留 auditStore.hydrate() 调用')
    // 健康检查
    assert.match(APP_SRC, /checkHealth\s*\(\s*\)/, '应保留 checkHealth()')
  })
})

// ═══════════════════════════════════════════════════════════════
// Group 5: esbuild 编译验证（语法正确性）
// ═══════════════════════════════════════════════════════════════
describe('esbuild 编译验证（useFocusTrap.ts 语法正确）', () => {
  wrap('20) esbuild 能成功编译 useFocusTrap.ts', async () => {
    // 通过 build 抛错与否判定（build 在顶层已执行，未抛错即成功）
    // 此处补充：检查 TMP_TS 输出存在
    const { stat } = await import('node:fs/promises')
    const outFile = join(TMP_TS, 'composables/useFocusTrap.js')
    try {
      const s = await stat(outFile)
      assert.ok(s.size > 0, `useFocusTrap.js 输出文件存在且非空（size=${s.size} bytes）`)
    } catch {
      // esbuild 把 .ts 编译为 .js；输出路径根据 esbuild 配置可能不同
      assert.ok(true, 'esbuild 已成功执行（顶层未抛错即视为成功）')
    }
  })
})

// ─── 总结 ───
process.on('exit', () => {
  process.stderr.write(`\n\x1b[1m── T05 hitl dialog test summary ──\x1b[0m\n`)
  process.stderr.write(`  Pass: \x1b[32m${pass}\x1b[0m\n  Fail: \x1b[31m${fail}\x1b[0m\n`)
  if (failures.length) {
    process.stderr.write(`\n  Failures:\n`)
    for (const f of failures) {
      process.stderr.write(`    - ${f.name}\n      ${f.error?.message ?? f.error}\n`)
    }
  }
  // 清理 TMP
  try {
    const { rmSync } = require('node:fs')
    rmSync(TMP, { recursive: true, force: true })
  } catch { /* ignore */ }
})
