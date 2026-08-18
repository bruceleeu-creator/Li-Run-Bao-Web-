#!/bin/bash
#
# 利润宝 · macOS 安装脚本（双击运行）
# 智能体标识：WB-CO-TR-20260726
#
# 行为：
#   1. 从脚本自身位置解析项目根目录（不写死用户名/绝对路径）
#   2. 按优先级查找 Python：LRB_PYTHON → python3.13 → python3.12 → python3.11 → python3
#      每个候选都逐个验证 Python≥3.11，选第一个真正合格者
#   3. 原子安装流程（直接在 .venv 创建，不 mv，避免破坏 pip shebang）：
#      a. 旧 .venv 先改名为 .venv.old（备份）
#      b. 在最终路径 .venv 直接创建新环境
#      c. pip 升级 + 安装依赖
#      d. 验证依赖 + Web 后端健康自检
#      e. 全部成功后才删除 .venv.old
#      f. 任何步骤失败：删除新 .venv，恢复 .venv.old 为 .venv（回滚）
#   4. 不使用 set -e，每个关键步骤显式判断退出码；失败给中文提示
#
# 注：Tk 桌面端已移除（2026-08），Web 为唯一入口，故不再校验 Tk。
#

# ── 解析项目根目录 ─────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo "┌─────────────────────────────────────────────┐"
echo "│  利润宝 · macOS 安装                          │"
echo "│  Install Script (WB-CO-TR-20260726)          │"
echo "└─────────────────────────────────────────────┘"
echo ""
echo "项目根目录：$PROJECT_ROOT"
echo ""

# ── 工具函数：失败时回滚并退出 ─────────────────────────────────────
# 回滚逻辑：删除新建的 .venv，将 .venv.old 恢复为 .venv
rollback_and_fail() {
    local reason="$1"
    echo ""
    echo "❌ 安装失败：$reason"
    echo ""
    # 回滚：删除新 .venv，恢复 .venv.old
    if [ -d "$PROJECT_ROOT/.venv" ]; then
        echo "⚙️  回滚：删除未通过验证的新 .venv/ ..."
        rm -rf "$PROJECT_ROOT/.venv"
    fi
    if [ -d "$PROJECT_ROOT/.venv.old" ]; then
        echo "⚙️  回滚：恢复 .venv.old/ → .venv/ ..."
        mv "$PROJECT_ROOT/.venv.old" "$PROJECT_ROOT/.venv"
        if [ $? -eq 0 ]; then
            echo "✅ 已回滚到旧 .venv/（仍可使用旧版本）"
        else
            echo "❌ 回滚失败：.venv.old → .venv 移动异常，请手动检查"
        fi
    else
        echo "ℹ️  无 .venv.old 备份可恢复（首次安装）"
    fi
    echo ""
    if [ -t 0 ]; then
        read -p "按回车键关闭窗口..." _dummy
    fi
    exit 1
}

# ── 工具函数：验证 Python 版本 ≥ 3.11 ──────────────────────────────
# 返回 0=合格，1=不合格
check_python_version() {
    local py_bin="$1"
    local py_ver
    py_ver=$("$py_bin" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")
    local major="${py_ver%%.*}"
    local minor="${py_ver#*.}"
    if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 11 ]; }; then
        return 1
    fi
    return 0
}

# ── 查找 Python：逐个验证 Python≥3.11 ──────────────────────────────
echo "▶ 查找合格的 Python 解释器（Python≥3.11）..."
PYTHON_BIN=""
for candidate in "$LRB_PYTHON" python3.13 python3.12 python3.11 python3; do
    # 跳过空候选（如未设置 LRB_PYTHON）
    if [ -z "$candidate" ]; then
        continue
    fi
    # 跳过不存在的候选
    if ! command -v "$candidate" >/dev/null 2>&1; then
        continue
    fi
    # 验证 Python 版本
    if ! check_python_version "$candidate"; then
        echo "  ⚠️  $candidate 版本过低（< 3.11），跳过"
        continue
    fi
    # 合格
    PYTHON_BIN="$candidate"
    PY_VER=$("$candidate" --version 2>&1)
    echo "  ✅ 选中：$candidate（$PY_VER）"
    break
done

if [ -z "$PYTHON_BIN" ]; then
    echo ""
    echo "❌ 未找到合格的 Python（需 Python≥3.11）。"
    echo ""
    echo "请安装 Homebrew Python 3.13："
    echo "    /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    echo "    brew install python@3.13"
    echo ""
    echo "或设置环境变量指向已合格 Python："
    echo "    export LRB_PYTHON=/path/to/python3.13"
    echo ""
    if [ -t 0 ]; then read -p "按回车键关闭窗口..." _dummy; fi
    exit 1
fi
echo ""

# ── 原子安装：直接在 .venv 创建（不 mv，避免破坏 pip shebang）──────
NEW_VENV="$PROJECT_ROOT/.venv"
OLD_VENV_BACKUP="$PROJECT_ROOT/.venv.old"

# 清理可能残留的 .venv.old
if [ -d "$OLD_VENV_BACKUP" ]; then
    echo "ℹ️  发现残留的备份 .venv.old/，先清理。"
    rm -rf "$OLD_VENV_BACKUP"
fi

# 步骤 a：旧 .venv 先改名为 .venv.old（备份）
if [ -d "$NEW_VENV" ]; then
    echo "⚙️  步骤 1/5：备份旧 .venv/ → .venv.old/ ..."
    mv "$NEW_VENV" "$OLD_VENV_BACKUP"
    if [ $? -ne 0 ]; then
        echo "❌ 备份旧 .venv 失败（mv 异常），中止安装。"
        if [ -t 0 ]; then read -p "按回车键关闭窗口..." _dummy; fi
        exit 1
    fi
    echo "  ✅ 旧 .venv/ 已备份为 .venv.old/"
else
    echo "⚙️  步骤 1/5：无旧 .venv/，跳过备份。"
fi
echo ""

# 步骤 b：在最终路径 .venv 直接创建新环境
echo "⚙️  步骤 2/5：在 $NEW_VENV 直接创建虚拟环境 ..."
"$PYTHON_BIN" -m venv "$NEW_VENV"
if [ ! -f "$NEW_VENV/bin/python" ]; then
    rollback_and_fail "虚拟环境创建失败（.venv/bin/python 不存在）"
fi
echo "  ✅ 虚拟环境已创建"
echo ""

# 步骤 c：pip 升级 + 安装依赖
echo "⚙️  步骤 3/5：升级 pip ..."
"$NEW_VENV/bin/python" -m pip install --upgrade pip >/dev/null 2>&1
PIP_UPGRADE_CODE=$?
if [ $PIP_UPGRADE_CODE -ne 0 ]; then
    echo "  ⚠️  pip 升级失败（exit $PIP_UPGRADE_CODE），继续使用默认版本。"
fi

echo "⚙️  步骤 3/5：安装依赖（requirements.txt）..."
"$NEW_VENV/bin/python" -m pip install -r "$PROJECT_ROOT/requirements.txt"
PIP_INSTALL_CODE=$?
if [ $PIP_INSTALL_CODE -ne 0 ]; then
    rollback_and_fail "依赖安装失败（pip exit $PIP_INSTALL_CODE）。请检查网络或 requirements.txt。"
fi
echo "  ✅ 依赖安装完成"
echo ""

# 步骤 d：验证依赖 + Web 后端健康自检
echo "⚙️  步骤 4/5：验证关键依赖可导入 ..."
# 严格按 requirements.txt 检查（不包含 pandas：核心计算未使用）
VERIFY_FAIL=0
for pkg in openpyxl matplotlib reportlab docx requests pytest fastapi uvicorn; do
    if "$NEW_VENV/bin/python" -c "import $pkg" >/dev/null 2>&1; then
        echo "  ✅ $pkg"
    else
        echo "  ❌ $pkg 不可导入"
        VERIFY_FAIL=1
    fi
done
if [ $VERIFY_FAIL -ne 0 ]; then
    rollback_and_fail "依赖验证失败（部分包安装后仍不可导入）"
fi

# 应用自检：Web 后端可导入并响应健康检查
echo "▶ 应用自检：Web 后端健康检查 ..."
"$NEW_VENV/bin/python" -c "
import importlib
from fastapi.testclient import TestClient
app = importlib.import_module('web_backend.CO_app_WB-CO-TR-20260805160732').create_app()
r = TestClient(app).get('/api/health')
assert r.status_code == 200 and r.json().get('status') == 'ok', r.text
print('  健康检查通过')
" >/tmp/lrb_install_selfcheck.log 2>&1
APP_CODE=$?
if [ $APP_CODE -ne 0 ]; then
    echo "  ❌ Web 后端健康检查失败（exit $APP_CODE），日志末尾："
    tail -n 20 /tmp/lrb_install_selfcheck.log 2>/dev/null || true
    rm -f /tmp/lrb_install_selfcheck.log
    rollback_and_fail "应用自检失败（Web 后端健康检查异常）"
fi
rm -f /tmp/lrb_install_selfcheck.log
echo "  ✅ 应用自检通过（Web 后端 /api/health 正常）"
echo ""

# ── 最终验证（必须在删除 .venv.old 备份之前完成）─────────────────
# CO 阻断修复 v4：原实现先 rm -rf .venv.old 再做最终验证，导致最终验证失败时
# 已丢失备份无法回滚。现调整为：最终验证通过后才删除备份。
if [ ! -f "$NEW_VENV/bin/python" ]; then
    rollback_and_fail "最终验证失败：.venv/bin/python 不存在（异常状态）"
fi
"$NEW_VENV/bin/python" -c "import openpyxl, matplotlib, reportlab, docx, requests, pytest, fastapi, uvicorn; print('  最终验证：全部依赖可导入')" >/dev/null 2>&1
FINAL_CODE=$?
if [ $FINAL_CODE -ne 0 ]; then
    rollback_and_fail "最终验证失败（exit $FINAL_CODE）"
fi
echo "✅ 最终验证通过（全部依赖可导入）"
echo ""

# 步骤 e：所有验证通过后才删除备份
echo "⚙️  步骤 5/5：所有验证通过，清理备份 .venv.old/ ..."
if [ -d "$OLD_VENV_BACKUP" ]; then
    rm -rf "$OLD_VENV_BACKUP"
    echo "  ✅ 已清理 .venv.old/ 备份"
fi
echo ""

echo "┌─────────────────────────────────────────────────────┐"
echo "│  ✅ 安装完成                                         │"
echo "│  双击「启动利润宝Web_WB-CO-TR-20260805160732.command」│"
echo "│  即可启动 Web 客户端。                               │"
echo "└─────────────────────────────────────────────────────┘"
echo ""
if [ -t 0 ]; then
    read -p "按回车键关闭窗口..." _dummy
fi
exit 0
