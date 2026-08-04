# ============================================================
# GridMind · 灵枢电网 → GitHub 推送脚本（v2 修复版）
# 仓库：https://github.com/fengguadehaoda/GridMind
# ============================================================

# 1. 配置 Git 身份（仅本次生效）
git config user.name "fengguadehaoda"
git config user.email "912307114@qq.com"

# 2. 初始化仓库（已 init 可忽略）
if (-not (Test-Path .git)) {
    git init | Out-Null
    git symbolic-ref HEAD refs/heads/main
}

# 3. 添加远程（HTTPS）
$remoteUrl = "https://github.com/fengguadehaoda/GridMind.git"
$existing = git remote get-url origin 2>$null
if (-not $existing) {
    git remote add origin $remoteUrl
} elseif ($existing -ne $remoteUrl) {
    git remote set-url origin $remoteUrl
}

# 4. 预览
Write-Host ""
Write-Host "===== 待推送文件数 ====="
$count = (git status --short | Measure-Object).Count
Write-Host "Total: $count 个文件"

Write-Host ""
Write-Host "===== 应被忽略的大文件（验证 .gitignore 生效）====="
$ignored = git status --ignored --short | Select-String -Pattern "node_modules|\.env$|\.workbuddy|\.log$|docker-data" | Select-Object -First 5
if ($ignored) {
    Write-Host "（以下命中 ignore 规则，不会被推送）"
    $ignored | ForEach-Object { Write-Host $_.ToString().Trim() }
} else {
    Write-Host "无"
}

Write-Host ""
Write-Host "===== 远程仓库 ====="
git remote -v

Write-Host ""
$confirm = Read-Host "确认推送? (yes/其它取消)"
if ($confirm -ne "yes") {
    Write-Host "已取消"
    exit 0
}

# 5. 准备 commit message（用 here-string 避免 - 开头行被解析为参数）
$commitMsg = @'
Initial commit: GridMind v1.4.0 (灵枢电网)

LangGraph + FastAPI + Vue 3 多智能体电网 AI 系统

核心能力：
* P0-2 知识图谱 Neo4j 完整交付：M0/M1/M2/M3a/M3b/M3c
* 可解释性 AI 三层架构（LLM + 机理校验 + 规则护栏）
* HITL Edit & Continue 模式
* 灰度切流（GrayscaleRouter + AutoRollback + Prometheus）
* 双主题前端（科技风格 · GridMind）

测试覆盖：
* 130+ 知识图谱测试
* 5 场景可解释性测试
* 灰度 e2e 全部 PASS
'@

# 6. add + commit
git add .
git commit -m $commitMsg

# 7. 推送
Write-Host ""
Write-Host "即将推送... 提示输入凭证："
Write-Host "  Username: fengguadehaoda"
Write-Host "  Password: 你的 Personal Access Token（不是 GitHub 密码）"
Write-Host ""

git push -u origin main