// M-5 角色感知 UI 独立实证（QA 严过关）：getJwtRole / parseJwtPayload / navItems 过滤
// 用法：cd web && node ../scripts/qa_m5_role_verify.mjs
import { createRequire } from 'module'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const rootDir = resolve(__dirname, '..')
const webDir = resolve(rootDir, 'web')
const webRequire = createRequire(resolve(webDir, 'package.json'))
const { build } = webRequire('esbuild')

// useJwtAuth 依赖 import.meta.env —— esbuild define 注入
await build({
  entryPoints: [resolve(webDir, 'src/composables/useJwtAuth.ts')],
  outfile: resolve(webDir, 'qa_m5_role_bundle.cjs'),
  bundle: true,
  platform: 'node',
  format: 'cjs',
  logLevel: 'silent',
  absWorkingDir: webDir,
  define: {
    'import.meta.env': '{}',
  },
})

const require = createRequire(import.meta.url)
const auth = require(resolve(webDir, 'qa_m5_role_bundle.cjs'))

let pass = 0, fail = 0
function check(name, cond, detail = '') {
  if (cond) { pass++; console.log(`  [PASS] ${name}`) }
  else { fail++; console.log(`  [FAIL] ${name} — ${detail}`) }
}

// 1) 缺省 dispatcher：dev token 不可解析
// 模拟环境：DEV_DEFAULT_JWT_TOKEN（gridmind-dev-token）→ parseJwtPayload null → dispatcher
check('dev token 解析失败 → dispatcher', auth.getJwtRole() === 'dispatcher', auth.getJwtRole())

// 2) parseJwtPayload 解码合法 JWT（构造一个 base64url payload）
function b64url(obj) {
  const json = JSON.stringify(obj)
  return Buffer.from(json).toString('base64url')
}
const mkToken = (payload) => `header.${b64url(payload)}.sig`

// 直接测 parseJwtPayload
const p1 = auth.parseJwtPayload(mkToken({ role: 'operator', sub: 'u1' }))
check('parseJwtPayload 解码 role=operator', p1 && p1.role === 'operator', JSON.stringify(p1))

// 3) getJwtRole 依赖 getJwtToken → 需 mock。直接验证 parseJwtPayload + 各角色值
// 用构造的 token 测 parseJwtPayload 的 role 提取路径（getJwtRole 内部已由 dev 默认覆盖）
const roles = ['dispatcher', 'operator', 'kb_admin', 'auditor', 'admin']
for (const r of roles) {
  const p = auth.parseJwtPayload(mkToken({ role: r }))
  check(`parseJwtPayload role=${r}`, p && p.role === r, JSON.stringify(p))
}

// 未知 role → 应被 getJwtRole 判为 dispatcher（构造逻辑验证）
const pUnknown = auth.parseJwtPayload(mkToken({ role: 'superuser' }))
check('未知 role 可解析但非法（superuser）', pUnknown && pUnknown.role === 'superuser', '')

// 4) base64url 中文字符（name claim UTF-8）
const pCn = auth.parseJwtPayload(mkToken({ name: '张三', role: 'admin', user_id: 'ZS' }))
check('parseJwtPayload 中文 name 解码', pCn && pCn.name === '张三', JSON.stringify(pCn))
check('parseJwtPayload user_id 提取', pCn && pCn.user_id === 'ZS', JSON.stringify(pCn))

// 5) 非法 token → null 不抛错
check('空 token → null', auth.parseJwtPayload('') === null, '')
check('非法格式 → null', auth.parseJwtPayload('abc.def') === null, '')
check('非 JSON payload → null', (() => {
  try { return auth.parseJwtPayload('h.' + b64url('not-json{') + '.s') === null } catch { return false }
})(), '')

// 6) navItems 过滤（esbuild bundle navItems.ts）
await build({
  entryPoints: [resolve(webDir, 'src/data/navItems.ts')],
  outfile: resolve(webDir, 'qa_m5_nav_bundle.cjs'),
  bundle: true,
  platform: 'node',
  format: 'cjs',
  logLevel: 'silent',
  absWorkingDir: webDir,
  external: ['vue', '@element-plus/icons-vue'],
})
const nav = require(resolve(webDir, 'qa_m5_nav_bundle.cjs'))
const all = nav.NAV_ITEMS
check('NAV_ITEMS 5 项', all.length === 5, `len=${all.length}`)
check('对话全员（无 roles）', !all[0].roles, JSON.stringify(all[0]))
check('监控全员（无 roles）', !all[1].roles, '')
const g = nav.visibleNavItems('operator').map(i => i.path)
check('operator 可见：对话/监控/灰度/审计（无系统）',
  JSON.stringify(g) === JSON.stringify(['/', '/monitor', '/grayscale', '/audit']), JSON.stringify(g))
const a = nav.visibleNavItems('admin').map(i => i.path)
check('admin 可见：全部 5 项',
  JSON.stringify(a) === JSON.stringify(['/', '/monitor', '/grayscale', '/audit', '/system']), JSON.stringify(a))
const d = nav.visibleNavItems('dispatcher').map(i => i.path)
check('dispatcher 可见：仅对话/监控',
  JSON.stringify(d) === JSON.stringify(['/', '/monitor']), JSON.stringify(d))
const kb = nav.visibleNavItems('kb_admin').map(i => i.path)
check('kb_admin 可见：对话/监控（无灰度/审计/系统）',
  JSON.stringify(kb) === JSON.stringify(['/', '/monitor']), JSON.stringify(kb))
const au = nav.visibleNavItems('auditor').map(i => i.path)
check('auditor 可见：对话/监控/审计',
  JSON.stringify(au) === JSON.stringify(['/', '/monitor', '/audit']), JSON.stringify(au))

// 7) 角色矩阵常量与 PRD 对齐
check('ROLES_GRAYSCALE = [operator, admin]', JSON.stringify(nav.ROLES_GRAYSCALE) === JSON.stringify(['operator', 'admin']), JSON.stringify(nav.ROLES_GRAYSCALE))
check('ROLES_AUDIT = [auditor, operator, admin]', JSON.stringify(nav.ROLES_AUDIT) === JSON.stringify(['auditor', 'operator', 'admin']), JSON.stringify(nav.ROLES_AUDIT))
check('ROLES_SYSTEM = [admin]', JSON.stringify(nav.ROLES_SYSTEM) === JSON.stringify(['admin']), JSON.stringify(nav.ROLES_SYSTEM))

console.log(`\n=== M-5 角色感知实证: PASS=${pass} FAIL=${fail} ===`)
if (fail > 0) process.exit(1)
