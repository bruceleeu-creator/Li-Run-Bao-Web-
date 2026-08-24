#!/usr/bin/env bash
# 一键推送部署（2026-08-24）：guardian 守护 → 提交 → 推送 main
# 推送后 GitHub Actions 自动接手：六分组 CI（guardian/后端 pytest/模块A e2e/板端测试/板端 e2e/双镜像构建）
# 全绿 → 自动 deploy（按 diff 判定 app/board/both；纯文档不碰服务器），详见 AGENTS.md「GitHub Actions CI/CD」节
# 用法：bash scripts/一键推送部署_WB-CO-TR-20260824.sh "feat: xxx"
# 进度/日志：https://github.com/bruceleeu-creator/Li-Run-Bao-Web-/actions
set -euo pipefail
cd "$(dirname "$0")/.."

MSG="${1:?用法: bash scripts/一键推送部署_WB-CO-TR-20260824.sh \"提交信息\"}"

PY=.venv/Scripts/python
[ -x "$PY" ] || PY=.venv/bin/python

echo "== 1/4 项目守护（0 错误才继续）=="
"$PY" .hooks/project_guardian.py --quick

echo "== 2/4 暂存并核对清单 =="
git add -A
git status --short

echo "== 3/4 提交 =="
if git diff --cached --quiet; then
  echo "无待提交改动，仅推送。"
else
  git commit -m "$MSG"
fi

echo "== 4/4 推送（绕过本机代理）=="
git -c http.proxy= -c https.proxy= push origin main

echo ""
echo "✅ 已推送。GitHub Actions 将自动执行六分组 CI，全绿后自动部署："
echo "   主应用(core/web_backend/web_frontend 等变更) → 8082"
echo "   协同看板(collab_board/ 变更) → 8081"
echo "   进度: https://github.com/bruceleeu-creator/Li-Run-Bao-Web-/actions"
