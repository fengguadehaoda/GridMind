#!/usr/bin/env bash
# ============================================================
# GridMind · 灵枢电网 → GitHub 推送脚本（Git Bash / WSL 用）
# 仓库：https://github.com/fengguadehaoda/GridMind
# ============================================================
set -e

# 1. 配置 Git 身份（仅本次生效）
git config user.name "fengguadehaoda"
git config user.email "912307114@qq.com"

# 2. 初始化仓库
git init

# 3. 默认分支 main
git symbolic-ref HEAD refs/heads/main

# 4. 添加远程
git remote add origin https://github.com/fengguadehaoda/GridMind.git

# 5. 预览
echo "===== 待推送文件数 ====="
git status --short | wc -l

echo "===== 应被忽略的大文件 ====="
git status --short | grep -E "node_modules|\.env$|\.workbuddy|\.log$|\.png$" | head -5 || echo "无"

echo ""
read -p "确认推送? (yes/其它取消) " confirm
if [ "$confirm" != "yes" ]; then
    echo "已取消"
    exit 0
fi

# 6. add + commit
git add .
git commit -m "Initial commit: GridMind v1.4.0 (灵枢电网)

- LangGraph + FastAPI + Vue 3 多智能体电网 AI 系统
- P0-2 知识图谱 Neo4j 完整交付：M0/M1/M2/M3a/M3b/M3c
- 可解释性 AI 三层架构（LLM + 机理校验 + 规则护栏）
- HITL Edit & Continue 模式
- 灰度切流（GrayscaleRouter + AutoRollback + Prometheus）
- 双主题前端（科技风格 · GridMind）
- 130+ 知识图谱测试 + 5 场景可解释性测试 + 灰度 e2e 全部 PASS"

# 7. 推送
echo ""
echo "即将推送... 提示输入凭证："
echo "  Username: fengguadehaoda"
echo "  Password: 你的 Personal Access Token（不是 GitHub 密码）"
echo ""

git push -u origin main