#!/bin/bash
# 利润宝 · CI 全量 pytest（已知遗留排除清单的单一真源，2026-08-24）
# ============================================================
# CI（.github/workflows/ci_WB-CO-TR-20260824.yml backend job）与本地验收共用本脚本。
# 排除项 = AGENTS.md「本机已知遗留测试失败」，逐条对应：
#   1. tests/CO_test_full_ai_report_*（整文件 --ignore）：
#      需上级「测试文件」目录的三份真实艺康年报 PDF，不入库 → CI 无夹具必失败；
#   2. tests/test_pdf_scan_parse.py::test_real_pdf_fixtures_are_project_portable：
#      同上依赖 web_backend/workspaces/ 下真实审计 PDF 夹具（本机手工放置，不入库）。
# （2026-09-09 v32.1 已清偿：test_industry_fallback_to_manufacturing 与
#   test_order_independence 四例修复回归，对应 --deselect 已删除。）
# -m "not real_pdf"：pytest.ini 已定义的标记，剔除全部依赖真实 PDF 的慢速完整性用例。
# 修复对应问题后，请同步删除本脚本中对应的 --ignore / --deselect 行，让测试回到门禁。
set -euo pipefail

PY="${PYTHON_BIN:-.venv/bin/python}"
if [ ! -x "$PY" ]; then
  PY="$(command -v python3 || true)"
fi
if [ -z "$PY" ]; then
  echo "未找到可用 Python（.venv/bin/python 或 python3）" >&2
  exit 2
fi

exec "$PY" -m pytest tests/ -q -m "not real_pdf" \
  --ignore=tests/CO_test_full_ai_report_WB-CO-TR-20260807113737.py \
  --deselect "tests/test_pdf_scan_parse.py::test_real_pdf_fixtures_are_project_portable" \
  "$@"
