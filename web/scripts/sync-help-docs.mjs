#!/usr/bin/env node
/**
 * sync-help-docs.mjs · 构建时帮助文档同步脚本（v1.6.0 P1-2）
 * ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 * 架构决策（p1-iteration-architecture §1 P1-2 + §8 待明确 #3 默认方案）：
 *   - 精选文档集：构建时从 docs/help-src/ 复制到 web/public/help/*.md（随前端打包）
 *   - 清单由 web/public/help/manifest.json 白名单驱动（articles[].source / path）
 *   - 前端产物部署到独立 Web 服务器后无法访问磁盘 docs/，故内置静态资源
 *
 * 用法：npm run sync:help（build 前自动执行）
 * 逻辑：
 *   1. 读 manifest.json
 *   2. 对每个 article：source（仓库根相对路径）→ path（public/help 相对路径）
 *   3. 复制成功输出 ✓；源缺失输出 ⚠ 警告（不中断 build，public/help 内已提交的副本仍可用）
 */

import { readFileSync, mkdirSync, copyFileSync, existsSync } from 'node:fs'
import { dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.resolve(__dirname, '..', '..')
const manifestPath = path.join(repoRoot, 'web', 'public', 'help', 'manifest.json')

if (!existsSync(manifestPath)) {
  console.error('[sync:help] manifest.json 不存在：', manifestPath)
  process.exit(1)
}

const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
const articles = Array.isArray(manifest.articles) ? manifest.articles : []

let copied = 0
let warned = 0

for (const article of articles) {
  if (!article.source || !article.path) {
    console.warn(`[sync:help] ⚠ article ${article.id ?? '(无 id)'} 缺少 source/path，跳过`)
    warned += 1
    continue
  }
  const sourceAbs = path.resolve(repoRoot, article.source)
  const targetRel = article.path.replace(/^\/+/, '')
  const targetAbs = path.join(repoRoot, 'web', 'public', targetRel)

  if (!existsSync(sourceAbs)) {
    console.warn(`[sync:help] ⚠ 源文档不存在，跳过（public/help 内副本仍可用）：${article.source}`)
    warned += 1
    continue
  }

  mkdirSync(dirname(targetAbs), { recursive: true })
  copyFileSync(sourceAbs, targetAbs)
  console.log(`[sync:help] ✓ ${article.id}  ${article.source} → public/${targetRel}`)
  copied += 1
}

console.log(`[sync:help] 完成：复制 ${copied} 篇，警告 ${warned} 条`)
