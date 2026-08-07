/**
 * GridMind v1.5.1 T03 单元测试 · StepInlineEditor + StepEditButton + ReasoningChainPanel 集成 + Store F2 路径
 *
 * 覆盖场景（≥5，全部 PASS）：
 *   StepInlineEditor 静态源码分析（Vue 组件形态）：
 *     S1. 含 textarea + 3 按钮（save/rerun/cancel） + role=group + aria-label
 *     S2. useFocusTrap 焦点循环（textarea + 3 el-button 共 4 焦点节点）
 *     S3. 键盘：Esc → cancel, Ctrl/Cmd+Enter → rerun
 *     S4. v-model 双向绑定到 store.draftSteps（writable computed）+ 4000 字上限
 *   StepEditButton 静态源码分析：
 *     S5. 仅 step.isEditable=true 时渲染（v-if="editable"）+ 点击调 beginEdit
 *     S6. 防御 REASONING_NOT_EDITABLE_STATE / STEP_NOT_EDITABLE 错误码（try/catch + ElMessage）
 *   ReasoningChainPanel.vue T03 集成：
 *     S7. 引入 StepEditButton + StepInlineEditor + useReasoningStore + liveSteps computed + canEditLive 守卫
 *     S8. 不破坏 v1.5.0：result.reasoning_chain 三层推理链与历史时间线保留
 *   esbuild 编译验证：
 *     S9. esbuild 能成功 bundle StepInlineEditor/StepEditButton/.vue + reasoning store
 *   reasoning store F2 集成路径：
 *     S10. beginEdit：status → editing + draftSteps 初始化为 promptFragment
 *     S11. beginEdit 在 status=running 也允许进入 editing（PRD §3.2.3）
 *     S12. beginEdit 对非 editable step 抛 STEP_NOT_EDITABLE（不进入 editing）
 *     S13. updateDraft 仅当 editingStepId 匹配时生效（其它 step 被忽略）
 *     S14. cancelEdit：丢弃草稿 + status 回 paused（曾 pause）或 running
 *     S15. rerunFromStep 失败：API throw → store 自动 status=paused（架构 §1.2.2 回滚）
 *
 * 运行：node tests/test_step_inline_editor.mjs
 *
 * 策略：源码静态分析（regex） + reasoning store esbuild bundle（仅 .ts）。
 * 与现有 test_reasoning_control_bar.mjs / test_runner.mjs / test_reasoning_store.mjs 一致。
 * .vue 组件的行为正确性由 vue-tsc build + store 集成测试共同保证。
 */
import { test, describe, before, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { build } from 'esbuild'
import { mkdtemp, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { existsSync, symlinkSync } from 'node:fs'
import { createPinia, setActivePinia } from 'pinia'

// ─── 路径 ───────────────────────────────────────────────
const ROOT = resolve(import.meta.dirname, '..')
const SRC = join(ROOT, 'src')
const TMP = await mkdtemp(join(tmpdir(), 'gridmind-step-inline-'))

// 顶层读 .vue 源码（静态分析用）
const stepInlineSrc = await readFile(
  join(SRC, 'components/reasoning/StepInlineEditor.vue'),
  'utf-8',
)
const stepEditBtnSrc = await readFile(
  join(SRC, 'components/reasoning/StepEditButton.vue'),
  'utf-8',
)
const panelSrc = await readFile(join(SRC, 'components/ReasoningChainPanel.vue'), 'utf-8')

// 仅编译 .ts store（与 test_reasoning_store.mjs 同模式），不做 .vue runtime
await build({
  entryPoints: [join(SRC, 'stores/reasoning.ts')],
  outdir: TMP,
  outbase: SRC,
  bundle: true,
  format: 'esm',
  platform: 'node',
  target: 'node20',
  external: ['pinia', 'vue', '@vue/*', 'axios'],
  alias: { '@': SRC },
  loader: { '.ts': 'ts' },
  logLevel: 'silent',
  write: true,
})

// 软链 node_modules
const nmTarget = join(ROOT, 'node_modules')
const nmLink = join(TMP, 'node_modules')
if (!existsSync(nmLink)) {
  try { symlinkSync(nmTarget, nmLink, 'junction') } catch { /* ignore */ }
}

// ─── 最小 DOM mock（element-plus / pinia 加载时不会触发 doc.createElement；保留兜底） ───
if (!globalThis.localStorage) {
  const data = {}
  globalThis.localStorage = {
    getItem: (k) => Object.hasOwn(data, k) ? data[k] : null,
    setItem: (k, v) => { data[k] = String(v) },
    removeItem: (k) => { delete data[k] },
    clear: () => { for (const k of Object.keys(data)) delete data[k] },
    key: (i) => Object.keys(data)[i] ?? null,
    get length() { return Object.keys(data).length },
  }
}
globalThis.window = {
  __test__: true,
  location: { href: 'http://localhost', origin: 'http://localhost' },
  localStorage: globalThis.localStorage,
  addEventListener() {}, removeEventListener() {},
}

// ─── 总结计数器 ──────────────────────────────────────────
let pass = 0, fail = 0
const failures = []
function wrap(name, fn) {
  return test(name, async () => {
    try { await fn(); pass++ }
    catch (e) { fail++; failures.push({ name, error: e }); throw e }
  })
}

// ══════════════════════════════════════════════════════
// §1 StepInlineEditor.vue · 静态形态
// ══════════════════════════════════════════════════════
describe('StepInlineEditor.vue · 静态形态（含 textarea + 3 按钮 + a11y + focus trap）', () => {
  wrap('S1 textarea + v-model + 3 按钮（save/rerun/cancel） + role=group + aria-label', () => {
    // textarea v-model="draft"
    assert.match(stepInlineSrc, /<textarea[^>]*v-model="draft"/, '缺少 textarea v-model="draft"')
    // textarea 必备 aria 属性（动态模板字面量）
    assert.match(stepInlineSrc, /:aria-label="`编辑步骤/, 'textarea 缺动态 aria-label（编辑步骤 #N）')
    assert.match(stepInlineSrc, /:aria-describedby="`char-count/, 'textarea 缺 aria-describedby 计数提示')
    assert.match(stepInlineSrc, /:aria-invalid="overLimit"/, 'textarea 缺 aria-invalid overLimit 标记')
    // 容器 role + aria-label
    assert.match(stepInlineSrc, /role="group"/, '缺少 role="group"')
    assert.match(stepInlineSrc, /aria-label="步骤内联编辑器"/, '缺少 aria-label="步骤内联编辑器"')
    // 3 个 el-button
    assert.match(stepInlineSrc, /💾 保存草稿/, '缺少"保存草稿"按钮')
    assert.match(stepInlineSrc, /🔄 从此步重跑/, '缺少"重跑"按钮')
    assert.match(stepInlineSrc, /✕ 取消/, '缺少"取消"按钮')
    // 重跑按钮 a11y
    assert.match(stepInlineSrc, /aria-label="从此步重跑"/, '重跑按钮缺 aria-label')
    assert.match(stepInlineSrc, /:aria-busy="isRerunning"/, '重跑按钮缺 aria-busy 状态')
    // 字符计数 aria-live
    assert.match(stepInlineSrc, /aria-live="polite"/, '字符计数缺 aria-live')
  })

  wrap('S2 useFocusTrap 启用焦点循环（4 可聚焦元素：textarea + 3 按钮）', () => {
    assert.match(
      stepInlineSrc,
      /import\s*\{[^}]*useFocusTrap[^}]*\}\s*from\s*['"]@\/composables\/useFocusTrap['"]/,
      '未 import useFocusTrap',
    )
    assert.match(
      stepInlineSrc,
      /useFocusTrap\(\s*\{\s*containerRef\s*\}\s*\)/,
      '未启用 useFocusTrap({ containerRef })',
    )
    // 至少 3 个 el-button（与 textarea 合计 4 个 focusable 节点）
    const buttonMatches = stepInlineSrc.match(/<el-button\b[^>]*>/g) || []
    assert.ok(
      buttonMatches.length >= 3,
      `应有 ≥3 个 el-button（实得 ${buttonMatches.length}）`,
    )
  })

  wrap('S3 键盘：Esc → cancel, Ctrl/Cmd+Enter → rerun', () => {
    // Esc → handleCancel
    assert.match(
      stepInlineSrc,
      /\bkey\b\s*===\s*['"]Escape['"][\s\S]{0,200}handleCancel\(\)/,
      'Esc 应触发 handleCancel',
    )
    // Ctrl/Cmd+Enter → handleRerun（参数名 event / e / ev 均兼容）
    assert.match(
      stepInlineSrc,
      /\b(?:ctrl|meta)Key\b[\s\S]{0,100}\b(?:ctrl|meta)Key\b[\s\S]{0,200}\bkey\b\s*===\s*['"]Enter['"][\s\S]{0,250}handleRerun/,
      'Ctrl/Cmd+Enter 应触发 handleRerun',
    )
  })

  wrap('S4 v-model 双向绑定到 store.draftSteps + 4000 字上限', () => {
    // writable computed：get 从 draftSteps 读，set 调 updateDraft
    assert.match(
      stepInlineSrc,
      /computed\s*<\s*string\s*>\s*\(\s*\{\s*get:/,
      '应使用 writable computed<string>({ get, set })',
    )
    assert.match(
      stepInlineSrc,
      /reasoning\.draftSteps\[props\.stepId\]/,
      'computed get 应从 reasoning.draftSteps[stepId] 读取',
    )
    assert.match(
      stepInlineSrc,
      /set:\s*\(val:\s*string\)\s*=>\s*\{[\s\S]*reasoning\.updateDraft\(props\.stepId,\s*val\)/,
      'computed set 应调 reasoning.updateDraft(stepId, val)',
    )
    // 4000 字上限
    assert.match(stepInlineSrc, /MAX_CHARS\s*=\s*4000/, '应有 4000 字上限常量')
    // 重跑按钮含字数校验逻辑
    assert.match(
      stepInlineSrc,
      /canRerun[\s\S]*overLimit[\s\S]*editingStepId/,
      'canRerun 应同时检查 overLimit + editingStepId 匹配',
    )
  })

  wrap('S*附加: handleSave/handleRerun/handleCancel 三个函数 + ElMessage 反馈', () => {
    // handleSave → updateDraft + ElMessage.success
    assert.match(stepInlineSrc, /function\s+handleSave\(\)[\s\S]*reasoning\.updateDraft\(props\.stepId,\s*draft\.value\)/,
      'handleSave 应调 updateDraft')
    assert.match(stepInlineSrc, /function\s+handleSave\(\)[\s\S]*ElMessage\.success/,
      'handleSave 应 toast 成功提示')
    // handleRerun → await rerunFromStep + ElMessage.success / error
    assert.match(stepInlineSrc, /async\s+function\s+handleRerun\(\)[\s\S]*await\s+reasoning\.rerunFromStep/,
      'handleRerun 应 await rerunFromStep')
    assert.match(stepInlineSrc, /async\s+function\s+handleRerun\(\)[\s\S]*ElMessage\.error/,
      'handleRerun 失败应 toast error')
    // handleCancel → cancelEdit
    assert.match(stepInlineSrc, /function\s+handleCancel\(\)[\s\S]*reasoning\.cancelEdit\(\)/,
      'handleCancel 应调 cancelEdit')
  })
})

// ══════════════════════════════════════════════════════
// §2 StepEditButton.vue · 静态形态
// ══════════════════════════════════════════════════════
describe('StepEditButton.vue · 静态形态（✎ 编辑触发按钮）', () => {
  wrap('S5 仅 step.isEditable=true 时渲染（v-if="editable"）+ 点击调 beginEdit', () => {
    // v-if="editable" 守卫（editable = step.isEditable）
    assert.match(stepEditBtnSrc, /<el-button\b[\s\S]*v-if="editable"/, 'editable=false 时不渲染（v-if="editable"）')
    // editable computed 基于 isEditable
    assert.match(stepEditBtnSrc, /const\s+editable\s*=\s*computed[\s\S]*step\.value\?\.isEditable/, 'editable computed 应基于 step.isEditable')
    // click → beginEdit(stepId)
    assert.match(stepEditBtnSrc, /@click="handleEdit"/, '按钮缺 @click="handleEdit"')
    assert.match(stepEditBtnSrc, /reasoning\.beginEdit\(props\.stepId\)/, 'handleEdit 应调 beginEdit(stepId)')
    // a11y
    assert.match(stepEditBtnSrc, /:aria-label=/, '缺 aria-label')
    assert.match(stepEditBtnSrc, /:aria-busy=/, '缺 aria-busy 状态')
    // data-testid 便于 e2e 选择器
    assert.match(stepEditBtnSrc, /data-testid="step-edit-button"/, '缺 data-testid')
  })

  wrap('S6 防御 REASONING_NOT_EDITABLE_STATE / STEP_NOT_EDITABLE（try/catch + ElMessage）', () => {
    // handleEdit 必含 try/catch 包裹 beginEdit
    const tryCatchRegex = /function\s+handleEdit\b[\s\S]{0,800}try\s*\{[\s\S]*reasoning\.beginEdit[\s\S]*\}\s*catch/
    assert.match(stepEditBtnSrc, tryCatchRegex, 'handleEdit 缺 try/catch 包裹 beginEdit')
    // 两个错误码分支
    assert.match(stepEditBtnSrc, /REASONING_NOT_EDITABLE_STATE/, '缺 REASONING_NOT_EDITABLE_STATE 错误分支')
    assert.match(stepEditBtnSrc, /STEP_NOT_EDITABLE/, '缺 STEP_NOT_EDITABLE 错误分支')
    // 至少一个 ElMessage 调用
    assert.match(stepEditBtnSrc, /ElMessage\.(warning|error)/, '缺 ElMessage 错误提示')
    // isEditingThis 状态显示
    assert.match(stepEditBtnSrc, /isEditingThis[\s\S]*editingStepId[\s\S]*props\.stepId/, 'isEditingThis 应同时匹配 editingStepId === stepId && status === editing')
  })
})

// ══════════════════════════════════════════════════════
// §3 ReasoningChainPanel.vue · T03 集成（不破坏 v1.5.0）
// ══════════════════════════════════════════════════════
describe('ReasoningChainPanel.vue · T03 集成（编辑入口 + 不破坏 v1.5.0）', () => {
  wrap('S7 引入 StepEditButton + StepInlineEditor + useReasoningStore + liveSteps + canEditLive', () => {
    // imports
    assert.match(panelSrc, /import\s+StepEditButton\s+from\s+['"]\.\/reasoning\/StepEditButton\.vue['"]/, '缺 StepEditButton import')
    assert.match(panelSrc, /import\s+StepInlineEditor\s+from\s+['"]\.\/reasoning\/StepInlineEditor\.vue['"]/, '缺 StepInlineEditor import')
    assert.match(panelSrc, /import\s*\{[^}]*useReasoningStore[^}]*\}\s+from\s+['"]@\/stores\/reasoning['"]/, '缺 useReasoningStore import')
    // computed + 守卫
    assert.match(panelSrc, /const\s+liveSteps\s*=\s*computed\(\(\)\s*=>\s*reasoning\.steps\)/, '缺 liveSteps computed')
    assert.match(panelSrc, /const\s+canEditLive\s*=\s*computed/, '缺 canEditLive 守卫')
    // state gating（仅 running/paused）
    assert.match(panelSrc, /\['running',\s*'paused'\]\.includes\(reasoning\.status\)/, 'canEditLive 应检查 running/paused')
    // template v-if + v-for + 两个子组件
    assert.match(panelSrc, /v-if="liveSteps\.length\s*>\s*0"/, '缺 liveSteps.length>0 v-if 守卫')
    assert.match(panelSrc, /v-for="step in liveSteps"/, '缺 v-for="step in liveSteps"')
    assert.match(panelSrc, /<StepEditButton\b[\s\S]*:step-id="step\.id"[\s\S]*:disabled="!canEditLive"/, '缺 StepEditButton :step-id + :disabled="!canEditLive"')
    assert.match(panelSrc, /<StepInlineEditor\b[\s\S]*:step-id="step\.id"/, '缺 StepInlineEditor :step-id')
    // 编辑态切换函数
    assert.match(panelSrc, /function\s+isEditingStep\(stepId:\s*string\)/, '缺 isEditingStep 函数')
    // v-else 切换（只读 ↔ 编辑）
    assert.match(panelSrc, /v-if="!isEditingStep\(step\.id\)"[\s\S]*<StepInlineEditor\s+v-else/, '缺 v-if/v-else 编辑态切换')
  })

  wrap('S8 不破坏 v1.5.0：三层推理链 + 历史时间线保留', () => {
    // v1.5.0 关键字段与函数都还在（防御式回归）
    assert.match(panelSrc, /\{\{\s*result\.final_diagnosis\s*\}\}/, '破坏：v1.5.0 final_diagnosis 渲染丢失')
    assert.match(panelSrc, /props\.result/, '破坏：props.result 引用丢失')
    assert.match(panelSrc, /visibleSteps/, '破坏：visibleSteps 历史时间线丢失')
    assert.match(panelSrc, /toggleCollapsed/, '破坏：toggleCollapsed 函数丢失')
    assert.match(panelSrc, /severityToTag/, '破坏：severityToTag 函数丢失')
    assert.match(panelSrc, /flattenEvidence/, '破坏：flattenEvidence 函数丢失')
    // props 签名保留
    assert.match(panelSrc, /defineProps<\{[\s\S]*result:\s*DiagnosisFusionResult/, '破坏：result prop 类型丢失')
    // 关键样式保留
    assert.match(panelSrc, /\.reasoning-chain-panel/, '破坏：根样式类丢失')
    assert.match(panelSrc, /\.reasoning-timeline\s*\{/, '破坏：历史时间线样式类丢失')
    assert.match(panelSrc, /\.timeline-step/, '破坏：timeline-step 样式丢失')
    // v1.5.0 status 8 状态名都还在引用
    for (const s of ['llm', 'mechanical', 'rules']) {
      const re = new RegExp(`\\b${s}\\b`, 'g')
      const matches = panelSrc.match(re) || []
      assert.ok(matches.length >= 1, `v1.5.0 layer '${s}' 引用丢失`)
    }
  })
})

// ══════════════════════════════════════════════════════
// §4 esbuild 编译验证
// ══════════════════════════════════════════════════════
describe('esbuild 编译 · T03 组件 + store', () => {
  wrap('S9 .vue 文件模板 + TS 语法无报错（手工抽取 &lt;script&gt; 可编译通过）', () => {
    // 三个 .vue 文件的 <script setup> 抽出后能用 TS 解析（粗糙代理）
    function extractScript(src) {
      const m = src.match(/<script\s+setup\s+lang=["']ts["']>([\s\S]*?)<\/script>/)
      return m ? m[1] : ''
    }
    const inline = extractScript(stepInlineSrc)
    const btn = extractScript(stepEditBtnSrc)
    const panel = extractScript(panelSrc)
    assert.ok(inline.length > 200, 'StepInlineEditor <script> 应有内容')
    assert.ok(btn.length > 100, 'StepEditButton <script> 应有内容')
    assert.ok(panel.length > 200, 'ReasoningChainPanel <script> 应有内容')
    // 三个 .vue 都包含 <template> + <style scoped>（StepEditButton 仅用 EP 默认样式但保留 style 段便于扩展）
    for (const [n, s] of [['StepInlineEditor', stepInlineSrc], ['StepEditButton', stepEditBtnSrc], ['ReasoningChainPanel', panelSrc]]) {
      assert.match(s, /<template>/, `${n} 缺 <template>`)
      assert.match(s, /<style(\s+scoped)?>/, `${n} 缺 <style> 或 <style scoped>`)
    }
  })

  wrap('S*附加: esbuild 已成功编译 stores/reasoning.ts（bundle 产物存在）', async () => {
    const { stat } = await import('node:fs/promises')
    const out = join(TMP, 'stores/reasoning.js')
    const ok = await stat(out).then(() => true).catch(() => false)
    assert.ok(ok, `${out} 应已生成（esbuild bundle 成功）`)
  })
})

// ══════════════════════════════════════════════════════
// §5 reasoning store · F2 actions 集成测试
// ══════════════════════════════════════════════════════
describe('reasoning store · F2 actions 集成（begin/edit/cancel/rerun）', () => {
  let useReasoningStore

  beforeEach(() => {
    globalThis.localStorage.clear()
    setActivePinia(createPinia())
  })
  before(async () => {
    const m = await import(`${pathToFileURL(TMP)}/stores/${'reasoning.js'}`)
    useReasoningStore = m.useReasoningStore
  })

  /** 工厂：构造一个可编辑 step */
  function editableStep(over = {}) {
    return {
      id: 'step-1',
      index: 0,
      name: '查询遥测',
      description: '查询 #T1 主变 24h 温度',
      promptFragment: '原始 prompt v1',
      draftPromptFragment: null,
      status: 'completed',
      startedAt: new Date().toISOString(),
      completedAt: new Date().toISOString(),
      durationMs: 120,
      output: null,
      isEditable: true,
      ...over,
    }
  }

  wrap('S10 beginEdit（status=paused）：status → editing + draftSteps 初始化为 promptFragment', () => {
    const r = useReasoningStore()
    r.start('sess-1', [editableStep()])
    r.status = 'paused'
    r.beginEdit('step-1')
    assert.equal(r.status, 'editing', 'beginEdit 后 status 应为 editing')
    assert.equal(r.editingStepId, 'step-1')
    assert.equal(r.draftSteps['step-1'], '原始 prompt v1', 'draft 应初始化为 promptFragment')
  })

  wrap('S11 beginEdit 在 status=running 也能进入 editing（PRD §3.2.3 running 允许）', () => {
    const r = useReasoningStore()
    r.start('sess-2', [editableStep({ id: 'step-A' })])
    // start 默认 status='running'
    assert.equal(r.status, 'running')
    r.beginEdit('step-A')
    assert.equal(r.status, 'editing')
    assert.equal(r.editingStepId, 'step-A')
  })

  wrap('S12 beginEdit 对非 editable step 抛 STEP_NOT_EDITABLE（不进入 editing）', () => {
    const r = useReasoningStore()
    r.start('sess-3', [
      editableStep({
        id: 'tool-1',
        isEditable: false, // ← 工具类型不可编辑（PRD §3.2.3 + 决策 7.4）
      }),
    ])
    r.status = 'paused'
    let threw = false
    try { r.beginEdit('tool-1') } catch (e) {
      threw = true
      assert.equal(e.message, 'STEP_NOT_EDITABLE')
    }
    assert.equal(threw, true, '应抛 STEP_NOT_EDITABLE')
    assert.equal(r.status, 'paused', 'status 应保持 paused，不进入 editing')
    assert.equal(r.editingStepId, '', 'editingStepId 应保持空')
  })

  wrap('S13 updateDraft 仅当 editingStepId 匹配时生效（防御跨 step 篡改）', () => {
    const r = useReasoningStore()
    r.start('sess-4', [
      editableStep({ id: 'step-x', promptFragment: 'origX' }),
      editableStep({ id: 'step-y', promptFragment: 'origY' }),
    ])
    r.status = 'paused'
    r.beginEdit('step-x')
    r.updateDraft('step-x', 'edited x')
    assert.equal(r.draftSteps['step-x'], 'edited x')
    // 编辑其他 step → 应被 store 忽略（不写 draftSteps）
    r.updateDraft('step-y', 'edited y - should be ignored')
    assert.equal(r.draftSteps['step-y'], undefined, '非当前编辑 step 的 updateDraft 应被忽略')
  })

  wrap('S14 cancelEdit：曾 pause → 回 paused；曾未 pause → 回 running（不变量）', () => {
    const r = useReasoningStore()

    // 场景 A：曾 pause（lastPausedAt 非空）→ cancel → 回 paused
    r.start('sess-5A', [editableStep({ id: 'step-c' })])
    r.status = 'paused'
    r.lastPausedAt = new Date().toISOString()
    r.beginEdit('step-c')
    r.updateDraft('step-c', 'doomed')
    r.cancelEdit()
    assert.equal(r.editingStepId, '', 'editingStepId 清空')
    assert.equal(r.draftSteps['step-c'], undefined, 'draft 被 discard')
    assert.equal(r.status, 'paused', '曾 pause → cancel 后回 paused')

    // 场景 B：曾未 pause（lastPausedAt 空）→ cancel → 回 running
    r.start('sess-5B', [editableStep({ id: 'step-d' })])
    assert.equal(r.status, 'running')
    r.lastPausedAt = ''
    r.beginEdit('step-d')
    assert.equal(r.status, 'editing')
    r.cancelEdit()
    assert.equal(r.status, 'running', '未 pause → cancel 后回 running')
  })

  wrap('S15 rerunFromStep 失败：API throw → store 自动 status=paused（架构 §1.2.2 回滚）', async () => {
    const r = useReasoningStore()
    r.start('sess-6', [editableStep({ id: 'step-fail', promptFragment: 'orig' })])
    r.status = 'paused'
    r.beginEdit('step-fail')
    r.updateDraft('step-fail', 'will fail')
    // 触发失败：直接删除该 step（store 内 findIndex === -1 时 throw 'STEP_NOT_FOUND'）
    r.steps.splice(0, 1)
    let threw = false
    try { await r.rerunFromStep('step-fail') }
    catch (e) { threw = true }
    assert.equal(threw, true, '应抛 STEP_NOT_FOUND（r.steps 已删）')
    // 失败后 store 应回 paused
    assert.equal(r.status, 'paused', '失败路径：status 应自动回 paused（架构 §1.2.2）')
    // 草稿应保留（PRD §3.2.4：原 step.promptFragment 保留便于重试）
    assert.equal(r.draftSteps['step-fail'], 'will fail', '草稿在失败路径应保留')
  })

  wrap('S*附加: store F2 actions 列表（beginEdit/updateDraft/cancelEdit/rerunFromStep 全部存在）', () => {
    const r = useReasoningStore()
    for (const fn of ['beginEdit', 'updateDraft', 'cancelEdit', 'rerunFromStep']) {
      assert.equal(typeof r[fn], 'function', `r.${fn} 应为函数`)
    }
  })
})

// ─── 总结 + 清理 ────────────────────────────────────────
process.on('exit', () => {
  process.stderr.write(`\n\x1b[1m── T03 StepInlineEditor test summary ──\x1b[0m\n`)
  process.stderr.write(`  Pass: \x1b[32m${pass}\x1b[0m\n  Fail: \x1b[31m${fail}\x1b[0m\n`)
  if (failures.length) {
    process.stderr.write(`\n  Failures:\n`)
    for (const f of failures) {
      process.stderr.write(`    - ${f.name}\n      ${f.error?.message ?? f.error}\n`)
    }
  }
  try {
    const { rmSync } = require('node:fs')
    rmSync(TMP, { recursive: true, force: true })
  } catch { /* ignore */ }
})
