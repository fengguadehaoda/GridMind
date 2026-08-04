# ============================================================
# GridMind · 灵枢电网 → GitHub 推送脚本（v3 修复版）
# 仓库：https://github.com/fengguadehaoda/GridMind
# 改进：使用 credential helper 避免命令行粘贴问题
# ============================================================

# 1. 配置 Git 身份（仅本次生效）
git config user.name "fengguadehaoda"
git config user.email "912307114@qq.com"

# 2. 配置 credential helper（让 Git 记住 token，避免命令行粘贴）
#    Windows 上推荐使用 manager（凭据管理器）
git config --global credential.helper manager 2>$null
git config credential.helper manager 2>$null

# 3. 初始化仓库（已 init 可忽略）
if (-not (Test-Path .git)) {
    git init | Out-Null
    git symbolic-ref HEAD refs/heads/main
}

# 4. 添加远程（HTTPS）
$remoteUrl = "https://github.com/fengguadehaoda/GridMind.git"
$existing = git remote get-url origin 2>$null
if (-not $existing) {
    git remote add origin $remoteUrl
} elseif ($existing -ne $remoteUrl) {
    Write-Host "注意：origin 已指向不同地址: $existing"
    Write-Host "当前脚本期望: $remoteUrl"
}

# 5. 预览
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

# 6. 准备 commit message（here-string 安全）
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

# 7. add + commit
git add .
$commitResult = git commit -m $commitMsg 2>&1
Write-Host $commitResult

# 8. 推送（第一次会触发 credential helper 弹窗或弹出 Git 登录对话框）
Write-Host ""
Write-Host "===== 推送 ====="
Write-Host "首次推送会触发凭证输入（credential.helper=manager）："
Write-Host "  Username: fengguadehaoda"
Write-Host "  Password: 粘贴你的 Personal Access Token（右键粘贴或 Ctrl+Shift+V）"
Write-Host ""
Write-Host "或者可能弹出 Windows 登录对话框（在那里粘贴）"
Write-Host ""

# 启用交互式终端（让 git credential prompt 能接收粘贴）
$env:GIT_TERMINAL_PROMPT = "1"
git push -u origin main

Write-Host ""
Write-Host "===== 验证 ====="
if ($LASTEXITCODE -eq 0) {
    Write-Host "推送成功！请访问 https://github.com/fengguadehaoda/GridMind 查看"
} else {
    Write-Host "推送失败（exit code: $LASTEXITCODE）。请检查凭证或网络后重试。"
}