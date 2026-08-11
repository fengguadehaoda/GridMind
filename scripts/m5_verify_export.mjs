// M-5 导出纯函数实证：buildMarkdown / buildJson / buildExportFilename / downloadFile
// 用法：cd web && node ../scripts/m5_verify_export.mjs（依赖 web/node_modules/.bin/esbuild）
import { createRequire } from 'module'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
// 项目根 = scripts/ 上一级；web 目录 = 根/web
const rootDir = resolve(__dirname, '..')
const webDir = resolve(rootDir, 'web')
// 从 web/node_modules 解析 esbuild（scripts/ 下无 node_modules）
const webRequire = createRequire(resolve(webDir, 'package.json'))
const { build } = webRequire('esbuild')

// 用 esbuild 把 sessionExport.ts 打包为 CJS（types 引用仅类型，运行时无依赖）
await build({
  entryPoints: [resolve(webDir, 'src/components/export/sessionExport.ts')],
  outfile: resolve(webDir, 'm5_export_bundle.cjs'),
  bundle: true,
  platform: 'node',
  format: 'cjs',
  logLevel: 'silent',
  absWorkingDir: webDir,
})

// 路径含空格/特殊字符（·），用 createRequire 加载 CJS 产物更稳
const require = createRequire(import.meta.url)
const exp = require(resolve(webDir, 'm5_export_bundle.cjs'))

const msgs = [
  { id: 'u1', role: 'user', content: '主变过载如何处理', timestamp: '2026-08-10T10:00:00.000Z' },
  {
    id: 'a1',
    role: 'assistant',
    content: '建议按规程处置',
    timestamp: '2026-08-10T10:02:00.000Z',
    knowledgeAnswer: {
      answer: '建议按规程处置',
      citations: ['规程1'],
      graph_paths: [['t1', 't2']],
      confidence: 0.9,
      refuse: false,
      sources: [
        { doc_id: 'd1', filename: '主变运行规程.md', section: '过载处置', score: 0.92, snippet: '过载时应降载' },
      ],
      graph_answer: {
        nodes: [{ id: 't1', name: '主变', type: '设备' }, { id: 't2', name: '过载', type: '故障' }],
        edges: [{ source: 't1', target: 't2', relation_type: '触发', confidence: 0.85 }],
        paths: [{ nodes: ['t1', 't2'], relations: ['触发'], hops: 1, confidence: 0.85 }],
        seed_ids: ['t1'], confidence: 0.85, backend: 'networkx', degraded: true, latency_ms: 12,
      },
    },
  },
  { id: 'u2', role: 'user', content: '好的，继续监控', timestamp: '2026-08-10T10:03:00.000Z' },
]

const meta = { user_id: 'zhangsan', role: 'dispatcher' }

// 1) buildMarkdown 含标题/来源引用/图谱路径
const md = exp.buildMarkdown('thread-abc12345', '#1 主变异常处置', 'qwen-plus', msgs, meta)
const checks = [
  ['标题', md.includes('# 会话复盘：#1 主变异常处置')],
  ['会话ID', md.includes('- 会话 ID：thread-abc12345')],
  ['导出人', md.includes('- 导出人：zhangsan')],
  ['模型', md.includes('- 模型：qwen-plus')],
  ['消息时序', md.includes('### 用户（') && md.includes('### 助手（')],
  ['来源引用', md.includes('#### 来源引用') && md.includes('《主变运行规程.md》·过载处置 — 匹配度 0.92')],
  ['图谱节点', md.includes('- 节点：主变(设备)')],
  ['图谱边', md.includes('- 边：主变 —[触发]→ 过载')],
  ['图谱路径', md.includes('- 路径：主变 → 过载')],
]
for (const [name, ok] of checks) {
  if (!ok) throw new Error(`Markdown 缺 ${name}`)
}
console.log('[PASS] buildMarkdown 含 标题/消息/来源引用/图谱路径')

// 2) buildJson format_version + knowledge_answer 原样
const json = JSON.parse(exp.buildJson('thread-abc12345', '#1 主变异常处置', 'qwen-plus', msgs, meta))
if (json.format_version !== 1) throw new Error('format_version 应为 1')
if (json.exported_by !== 'zhangsan') throw new Error('exported_by 错误')
if (json.messages.length !== 3) throw new Error('messages 数量错误')
const ka = json.messages[1].knowledge_answer
if (!ka || ka.sources[0].filename !== '主变运行规程.md') throw new Error('knowledge_answer.sources 缺失')
if (ka.graph_answer.backend !== 'networkx' || ka.graph_answer.degraded !== true) throw new Error('graph_answer 原样保留失败')
if (json.messages[0].knowledge_answer !== undefined) throw new Error('非 knowledge 轮次不应有 knowledge_answer')
console.log('[PASS] buildJson format_version=1 + knowledge_answer sources/graph_answer 原样')

// 3) 缺省字段跳过：无 knowledgeAnswer 的 assistant → 无来源引用/无 knowledge_answer
const plain = [
  { id: 'u1', role: 'user', content: 'hi', timestamp: '2026-08-10T10:00:00.000Z' },
  { id: 'a1', role: 'assistant', content: 'hello', timestamp: '2026-08-10T10:01:00.000Z' },
]
const mdPlain = exp.buildMarkdown('t-x', '普通会话', null, plain, meta)
if (mdPlain.includes('来源引用') || mdPlain.includes('图谱')) throw new Error('缺省字段不应输出引用块')
const jsonPlain = JSON.parse(exp.buildJson('t-x', '普通会话', null, plain, meta))
if (jsonPlain.messages[1].knowledge_answer !== undefined) throw new Error('缺省 knowledge_answer 应跳过')
console.log('[PASS] 缺省字段自动跳过（非 knowledge_agent 轮次）')

// 4) 文件名约定
const fn = exp.buildExportFilename('#1 主变异常处置', 'thread-abc12345', 'md')
const fnJson = exp.buildExportFilename('#1 主变异常处置', 'thread-abc12345', 'json')
if (!/^#1_主变异常处置-abc12345-\d{8}-\d{6}\.md$/.test(fn)) throw new Error(`文件名约定不符: ${fn}`)
if (!fnJson.endsWith('.json')) throw new Error('json 扩展名错误')
const safe = exp.sanitizeFilenamePart('a/b\\c:d*e?f"g<h>i|j')
if (safe !== 'a_b_c_d_e_f_g_h_i_j') throw new Error(`非法字符替换失败: ${safe}`)
console.log('[PASS] 文件名约定 + 非法字符替换:', fn)

// 5) downloadFile 导出函数存在（浏览器专用，Node 下仅验证签名）
if (typeof exp.downloadFile !== 'function') throw new Error('downloadFile 缺失')
console.log('[PASS] downloadFile（Blob + createObjectURL）导出就绪')

console.log('\n=== M-5 导出纯函数实证全部通过 ===')
