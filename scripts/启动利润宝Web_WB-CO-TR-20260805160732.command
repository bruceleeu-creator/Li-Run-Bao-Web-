#!/bin/bash
#
# 利润宝 · macOS Web 客户端启动脚本（双击运行）
# 智能体标识：WB-CO-TR-20260726
#
# 行为：
#   1. 从脚本自身位置解析项目根目录
#   2. 检测 .venv 与 Web 前端构建产物 web_frontend/dist/index.html
#   3. 使用 uvicorn 启动后端于 127.0.0.1:8765（仅本机回环，不开放局域网）
#   4. 轮询 /api/health 成功后自动打开默认浏览器
#   5. 退出前中止后端进程
#
# 注意：本脚本是利润宝 Web 客户端唯一启动入口（Tk 桌面端已于 2026-08 移除）。
#

# ── 解析项目根目录 ─────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo "┌─────────────────────────────────────────────┐"
echo "│  利润宝 · Web 客户端                         │"
echo "│  Web Client Launcher (WB-CO-TR-20260726)     │"
echo "└─────────────────────────────────────────────┘"
echo ""
echo "项目根目录：$PROJECT_ROOT"
echo ""

# ── 前置检查 ─────────────────────────────────────────────────────
if [ ! -f "$PROJECT_ROOT/.venv/bin/python" ]; then
    echo "❌ 未找到 .venv/。请先双击「安装利润宝_WB-CO-TR-20260726.command」安装环境。"
    if [ -t 0 ]; then read -p "按回车键关闭窗口..." _dummy; fi
    exit 1
fi

if [ ! -f "$PROJECT_ROOT/web_frontend/dist/index.html" ]; then
    echo "❌ 未找到前端构建产物 web_frontend/dist/index.html。"
    echo "   当前为外壳阶段，需先构建前端（进入 web_frontend 执行 npm run build）。"
    if [ -t 0 ]; then read -p "按回车键关闭窗口..." _dummy; fi
    exit 1
fi

PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"

# 校验后端依赖可用
if ! "$PYTHON_BIN" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
    echo "❌ .venv 缺少 fastapi/uvicorn。请重新运行安装脚本以安装 Web 依赖。"
    if [ -t 0 ]; then read -p "按回车键关闭窗口..." _dummy; fi
    exit 1
fi

echo "✅ 环境与构建产物就绪，启动本机服务 http://127.0.0.1:8765 ..."
echo ""

# ── 启动后端（后台）并轮询健康检查 ───────────────────────────────
PORT=8765
LOG_FILE="/tmp/lrb_web_preview.log"
rm -f "$LOG_FILE"

"$PYTHON_BIN" -m web_backend.CO_run_WB-CO-TR-20260805160732 >"$LOG_FILE" 2>&1 &
SERVER_PID=$!

# 优雅退出：中止后端进程
cleanup() {
    if kill -0 "$SERVER_PID" >/dev/null 2>&1; then
        kill "$SERVER_PID" 2>/dev/null
        wait "$SERVER_PID" 2>/dev/null
    fi
    echo ""
    echo "✅ 本机 Web 服务已停止。"
}
trap cleanup EXIT INT TERM

# 轮询 /api/health（最多 20 次 × 0.5s = 10s）
READY=0
for i in $(seq 1 20); do
    if curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
        READY=1
        break
    fi
    if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
        echo "❌ 后端进程提前退出，日志末尾："
        tail -n 20 "$LOG_FILE" 2>/dev/null || true
        break
    fi
    sleep 0.5
done

if [ "$READY" -ne 1 ]; then
    echo "❌ 本机服务未能在 10 秒内就绪。请检查 $LOG_FILE。"
    exit 1
fi

echo "✅ 本机服务已就绪：http://127.0.0.1:$PORT"
echo ""

# ── 打开默认浏览器 ───────────────────────────────────────────────
open "http://127.0.0.1:$PORT" 2>/dev/null || echo "提示：请手动在浏览器打开 http://127.0.0.1:$PORT"

echo ""
echo "按 Ctrl+C 停止服务并关闭窗口..."
wait "$SERVER_PID"
