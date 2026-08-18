#!/bin/bash
#
# 利润宝 · macOS 质量检查脚本（双击运行）
# 智能体标识：WB-CO-TR-20260726
#
# 行为：
#   1. 从脚本自身位置解析项目根目录（不写死用户名/绝对路径）
#   2. 检查 .venv/ 就绪；缺失则提示先运行安装脚本
#   3. 依次运行：全量 pytest → make_sample → Web 后端健康检查
#               → project_guardian.py --quick → check.sh
#   4. 汇总各步骤通过/失败状态
#
# 注：Tk 桌面端已移除（2026-08），不再运行 --demo/--headless 与 test_gui_headless。

# ── 解析项目根目录 ─────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo "┌─────────────────────────────────────────────┐"
echo "│  利润宝 · 质量检查                            │"
echo "│  Quality Check (WB-CO-TR-20260726)           │"
echo "└─────────────────────────────────────────────┘"
echo ""
echo "项目根目录：$PROJECT_ROOT"
echo ""

# ── 选择 Python 解释器（优先 .venv，回退 PYTHON_BIN，避免误用系统 Python 3.9）──
PY=""
if [ -n "$PYTHON_BIN" ] && command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    PY="$PYTHON_BIN"
elif [ -f "$PROJECT_ROOT/.venv/bin/python" ]; then
    PY="$PROJECT_ROOT/.venv/bin/python"
else
    echo "❌ 未找到 .venv/bin/python，也未设置 PYTHON_BIN，请先双击运行："
    echo "    scripts/安装利润宝_WB-CO-TR-20260726.command"
    if [ -t 0 ]; then read -p "按回车键关闭窗口..." _dummy; fi
    exit 2
fi
PASS=0
FAIL=0
FAILED_STEPS=""

run_step() {
    local step_name="$1"
    local cmd="$2"
    echo "─────────────────────────────────────────────"
    echo "▶ $step_name"
    echo "─────────────────────────────────────────────"
    eval "$cmd"
    local code=$?
    echo ""
    if [ $code -eq 0 ]; then
        echo "✅ $step_name 通过"
        PASS=$((PASS + 1))
    else
        echo "❌ $step_name 失败（exit $code）"
        FAIL=$((FAIL + 1))
        FAILED_STEPS="$FAILED_STEPS\n    - $step_name"
    fi
    echo ""
}

# ── 1. 全量 pytest ────────────────────────────────────────────────
run_step "1) 全量 pytest 回归" \
    "\"$PY\" -m pytest tests/ -q"

# ── 2. 生成样例数据 ───────────────────────────────────────────────
run_step "2) 生成示例数据 (make_sample)" \
    "\"$PY\" data/make_sample.py"

# ── 3. Web 后端健康检查 ───────────────────────────────────────────
run_step "3) Web 后端健康检查" \
    "\"$PY\" -c \"import importlib; from fastapi.testclient import TestClient; app = importlib.import_module('web_backend.CO_app_WB-CO-TR-20260805160732').create_app(); r = TestClient(app).get('/api/health'); assert r.status_code == 200 and r.json().get('status') == 'ok', r.text\""

# ── 4. project_guardian --quick ───────────────────────────────────
run_step "4) project_guardian.py --quick" \
    "\"$PY\" .hooks/project_guardian.py --quick"

# ── 5. check.sh ───────────────────────────────────────────────────
run_step "5) check.sh 项目自检" \
    "bash .hooks/check.sh"

# ── 汇总 ──────────────────────────────────────────────────────────
echo "┌─────────────────────────────────────────────┐"
echo "│  质量检查汇总                                │"
echo "│  通过：$PASS  失败：$FAIL                       │"
echo "└─────────────────────────────────────────────┘"
if [ $FAIL -gt 0 ]; then
    echo ""
    echo "失败步骤："
    echo -e "$FAILED_STEPS"
    echo ""
    if [ -t 0 ]; then read -p "按回车键关闭窗口..." _dummy; fi
    exit 1
fi

if [ -t 0 ]; then
    read -p "按回车键关闭窗口..." _dummy
fi
exit 0
