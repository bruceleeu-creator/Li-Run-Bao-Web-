#!/bin/bash
#
# 利润宝 · macOS 环境检查脚本（双击运行）
# 智能体标识：WB-CO-TR-20260726
#
# 行为：
#   1. 从脚本自身位置解析项目根目录（不写死用户名/绝对路径）
#   2. 优先用 .venv/bin/python 或 PYTHON_BIN 环境变量，避免误用系统 Python 3.9
#   3. 检查 Python 版本、.venv 是否就绪、关键依赖是否安装、Web 后端健康
#   4. 返回明确退出码：0=全部通过 / 1=警告（可运行但有提示）/ 2=错误（阻断运行）
#
# 注：Tk 桌面端已移除（2026-08），Web 为唯一入口，故不再检查 Tk。

# ── 解析项目根目录 ─────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo "┌─────────────────────────────────────────────┐"
echo "│  利润宝 · 环境检查                            │"
echo "│  Environment Check (WB-CO-TR-20260726)       │"
echo "└─────────────────────────────────────────────┘"
echo ""
echo "项目根目录：$PROJECT_ROOT"
echo ""

EXIT_CODE=0
WARN_COUNT=0
ERR_COUNT=0

# ── 选择 Python 解释器（优先 .venv，避免误用系统 Python 3.9）──────────
PY=""
if [ -n "$PYTHON_BIN" ] && command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    PY="$PYTHON_BIN"
    echo "✅ 使用 PYTHON_BIN 指定的解释器：$PY"
elif [ -f "$PROJECT_ROOT/.venv/bin/python" ]; then
    PY="$PROJECT_ROOT/.venv/bin/python"
    echo "✅ 虚拟环境就绪：$PY"
else
    echo "❌ [错误] 未找到 .venv/bin/python，且未设置 PYTHON_BIN 环境变量"
    echo "    请先运行：bash scripts/安装利润宝_WB-CO-TR-20260726.command"
    ERR_COUNT=$((ERR_COUNT + 1))
    EXIT_CODE=2
    echo ""
    echo "┌─────────────────────────────────────────────┐"
    echo "│  ❌ 环境检查未通过（错误 $ERR_COUNT）         │"
    echo "└─────────────────────────────────────────────┘"
    if [ -t 0 ]; then read -p "按回车键关闭窗口..." _dummy; fi
    exit $EXIT_CODE
fi
echo ""

# ── Python 版本 ───────────────────────────────────────────────────
PY_VERSION="$("$PY" --version 2>&1)"
echo "Python 版本：$PY_VERSION"
PY_VER_NUM="$("$PY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)"
PY_MAJOR="${PY_VER_NUM%%.*}"
PY_MINOR="${PY_VER_NUM#*.}"
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
    echo "❌ [错误] Python 版本过低（$PY_VER_NUM < 3.11）"
    ERR_COUNT=$((ERR_COUNT + 1))
    EXIT_CODE=2
else
    echo "✅ Python 版本符合要求（≥ 3.11）"
fi
echo ""

# ── 关键依赖 ──────────────────────────────────────────────────────
echo "依赖检查："
# 严格按 requirements.txt 检查（不包含 pandas：核心计算未使用）
for pkg in openpyxl matplotlib reportlab docx requests pytest fastapi uvicorn; do
    if "$PY" -c "import $pkg" >/dev/null 2>&1; then
        echo "  ✅ $pkg"
    else
        echo "  ❌ [错误] 缺失 $pkg"
        ERR_COUNT=$((ERR_COUNT + 1))
        EXIT_CODE=2
    fi
done
echo ""

# ── Web 后端健康自检 ─────────────────────────────────────────────
echo "Web 后端健康检查："
if "$PY" -c "
import importlib
from fastapi.testclient import TestClient
app = importlib.import_module('web_backend.CO_app_WB-CO-TR-20260805160732').create_app()
r = TestClient(app).get('/api/health')
assert r.status_code == 200 and r.json().get('status') == 'ok'
" >/dev/null 2>&1; then
    echo "  ✅ /api/health 正常"
else
    echo "  ❌ [错误] Web 后端健康检查失败"
    ERR_COUNT=$((ERR_COUNT + 1))
    EXIT_CODE=2
fi
echo ""

# ── 总结 ──────────────────────────────────────────────────────────
if [ $EXIT_CODE -eq 0 ]; then
    echo "┌─────────────────────────────────────────────┐"
    echo "│  ✅ 环境检查全部通过                          │"
    echo "└─────────────────────────────────────────────┘"
elif [ $EXIT_CODE -eq 1 ]; then
    echo "┌─────────────────────────────────────────────┐"
    echo "│  ⚠️  通过但存在 $WARN_COUNT 项警告           │"
    echo "└─────────────────────────────────────────────┘"
else
    echo "┌─────────────────────────────────────────────┐"
    echo "│  ❌ 环境检查未通过（错误 $ERR_COUNT / 警告 $WARN_COUNT）"
    echo "└─────────────────────────────────────────────┘"
fi
echo ""

if [ -t 0 ]; then
    read -p "按回车键关闭窗口..." _dummy
fi
exit $EXIT_CODE
