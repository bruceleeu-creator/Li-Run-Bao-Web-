#!/bin/bash
#
# 利润宝 · 项目自检快速运行脚本
# 一键运行完整的项目守护者检查
#
# 用法：bash .hooks/check.sh
#
# Python 选择优先级（避免误用系统 Python 3.9）：
#   1. $PYTHON_BIN（环境变量显式指定）
#   2. .venv/bin/python（项目虚拟环境）
#   3. python3（最后回退，会校验版本 ≥ 3.11）
#

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "┌─────────────────────────────────────────────┐"
echo "│  利润宝 · 项目自检                          │"
echo "│  Project Guardian Check                     │"
echo "└─────────────────────────────────────────────┘"
echo ""
echo "项目路径：$PROJECT_ROOT"
echo ""

cd "$PROJECT_ROOT"

# ── 选择 Python 解释器（优先 .venv，避免误用系统 Python 3.9）──────────
PY_BIN=""
if [ -n "$PYTHON_BIN" ] && command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    PY_BIN="$PYTHON_BIN"
elif [ -f "$PROJECT_ROOT/.venv/bin/python" ]; then
    PY_BIN="$PROJECT_ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    # 回退到系统 python3，但需校验版本 ≥ 3.11
    PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")
    PY_MAJOR="${PY_VER%%.*}"
    PY_MINOR="${PY_VER#*.}"
    if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
        echo "❌ 系统 python3 版本过低（$PY_VER < 3.11），请使用 .venv 或设置 PYTHON_BIN"
        echo "    推荐：bash scripts/安装利润宝_WB-CO-TR-20260726.command"
        exit 2
    fi
    PY_BIN="python3"
fi

if [ -z "$PY_BIN" ]; then
    echo "❌ 未找到可用的 Python 解释器"
    echo "    请先运行：bash scripts/安装利润宝_WB-CO-TR-20260726.command"
    exit 2
fi

echo "使用 Python：$PY_BIN ($("$PY_BIN" --version 2>&1))"
echo ""

# 运行完整检查（不使用 set -e，保留退出码处理）
"$PY_BIN" .hooks/project_guardian.py "$@"
exit_code=$?

echo ""
if [ $exit_code -eq 0 ]; then
    echo "✅ 全部检查通过"
elif [ $exit_code -eq 1 ]; then
    echo "⚠️  通过但存在警告"
else
    echo "❌ 存在错误，请查看上方报告"
fi
echo ""

exit $exit_code
