#!/usr/bin/env python3
"""
利润宝 · 项目守护者自检脚本（Project Guardian）
=================================================
职责：
  1. 文档 vs 代码一致性检查     — 开发方案中声明的模块是否已实现
  2. Python 语法/导入错误检查   — 每一 .py 文件语法正确
  3. 架构一致性验证              — 目录结构、层间依赖方向是否正确
  4. 合规红线检测                — 文案中是否有敏感表述
  5. ADR 遵循检查                — 技术选型是否偏离设计决策
  6. AGENTS.md 时效性检查        — 是否需要更新项目记忆

用法：
  python3 .hooks/project_guardian.py            # 完整检查
  python3 .hooks/project_guardian.py --quick     # 快速（仅语法+架构）
  python3 .hooks/project_guardian.py --list      # 列出所有检查项

返回码：0 = 全部通过，1 = 有警告，2 = 有错误

智能体标识：WB-CO-TR-20260717
"""

import ast
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Tuple

# ── 配置 ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 开发方案中声明的模块结构（基于利润宝_开发方案_v1.0.md §5.5 + Web 重构设计说明）
EXPECTED_MODULES = {
    "":      ["requirements.txt"],
    "core":  ["__init__.py", "models.py", "industry.py", "parser.py",
              "finance.py", "diagnostic.py", "interactive.py",
              "report.py", "action_pack.py", "ai_engine.py", "narrative.py",
              "budget.py", "budget_categories.py", "budget_template.py",
              "compliance_policy.py", "reconciliation.py", "pipeline.py",
              "case_manifest.py", "numeric_audit.py",
              "CO_financial_scan_WB-CO-TR-20260806140818.py",
              "CO_deepseek_parse_WB-CO-TR-20260806140818.py",
              "CO_full_pdf_reader_WB-CO-TR-20260807113737.py",
              "CO_budget_export_WB-CO-TR-20260810.py",
              "CO_budget_advice_WB-CO-TR-20260810.py",
              "CO_report_analysis_WB-CO-TR-20260818.py",
              "CO_monthly_split_WB-CO-TR-20260820.py"],
    "data":  ["__init__.py", "make_sample.py"],
    "tests": ["__init__.py", "test_finance.py", "test_diagnostic.py",
              "test_industry.py",
              "test_interactive.py", "test_parser.py",
              "test_budget.py", "test_template_export.py",
              "test_action_pack_entertain.py",
              "test_order_independence.py",
              "test_t7_acceptance.py",
              "CO_test_web_health_WB-CO-TR-20260805160732.py",
              "CO_test_web_import_WB-CO-TR-20260805160732.py",
              "CO_test_web_import_ext_WB-CO-TR-20260805160732.py",
              "CO_test_web_ai_WB-CO-TR-20260805160732.py",
              "CO_test_scan_recognition_WB-CO-TR-20260806140818.py",
              "CO_test_deepseek_parse_WB-CO-TR-20260806140818.py",
              "CO_test_full_ai_report_WB-CO-TR-20260807113737.py",
              "CO_test_ai_report_pipeline_WB-CO-TR-20260807113737.py",
              "test_pdf_scan_parse.py",
              "CO_test_web_diagnosis_interaction_WB-CO-TR-20260809121546.py",
              "CO_test_ai_diagnosis_discover_WB-CO-TR-20260810.py",
              "CO_test_import_history_WB-CO-TR-20260818.py",
              "test_numeric_audit.py",
              "CO_test_export_story_word_WB-CO-TR-20260810.py",
              "CO_test_budget_export_3sheet_WB-CO-TR-20260810.py",
              "CO_test_budget_advice_WB-CO-TR-20260810.py",
              "CO_test_report_analysis_WB-CO-TR-20260818.py",
              "CO_test_monthly_split_WB-CO-TR-20260820.py",
              "CO_test_monthly_api_WB-CO-TR-20260820.py",
              "CO_test_monthly_excel_WB-CO-TR-20260820.py",
              "test_pipeline_phase_a.py",
              "test_case_manifest_phase_b.py",
              "test_reconciliation_audit.py",
              ],
    "web_backend": ["__init__.py", "CO_app_WB-CO-TR-20260805160732.py",
                    "CO_run_WB-CO-TR-20260805160732.py",
                    "CO_session_WB-CO-TR-20260805160732.py",
                    "CO_import_WB-CO-TR-20260805160732.py",
                    "CO_ai_WB-CO-TR-20260805160732.py",
                    "CO_ai_report_job_WB-CO-TR-20260807113737.py",
                    "CO_ai_report_pipeline_WB-CO-TR-20260807113737.py",
                    "CO_ai_route_WB-CO-TR-20260805160732.py",
                    "CO_budget_WB-CO-TR-20260805160732.py",
                    "CO_db_WB-CO-TR-20260805160732.py",
                    "CO_diagnosis_WB-CO-TR-20260805160732.py",
                    "CO_interaction_WB-CO-TR-20260805160732.py",
                    "CO_export_WB-CO-TR-20260810.py",
                    "CO_budget_export_job_WB-CO-TR-20260810.py",
                    "CO_monthly_WB-CO-TR-20260820.py"],
    "collab_board": ["board_e2e_server.py"],
    "collab_board/board_backend": [
                    "__init__.py",
                    "CO_app_WB-CO-TR-20260820.py",
                    "CO_db_WB-CO-TR-20260820.py",
                    "CO_auth_WB-CO-TR-20260820.py",
                    "CO_rooms_WB-CO-TR-20260820.py",
                    "CO_tasks_WB-CO-TR-20260820.py",
                    "CO_template_WB-CO-TR-20260820.py",
                    "requirements-board.txt"],
    "collab_board/board_backend/tests_board": [
                    "__init__.py",
                    "conftest.py",
                    "test_board_auth.py",
                    "test_board_rooms.py",
                    "test_board_tasks.py",
                    "test_board_template.py"],
}

# __init__.py 文件会自动被所有子目录期望（不视为意外）
EXPECTED_INIT = {"__init__.py"}

# 架构层间依赖规则（谁可以导入谁）
# 格式：{层目录: [允许导入的层目录]}
# 核心原则：GUI 层可以调 core；core 不能调 GUI；data/tests/web_backend 可调 core
LAYER_DEP_RULES = {
    ".":        ["core", "data"],
    "core":     [],          # core 是底层，不能导入上层
    "data":     ["core"],
    "tests":    ["core", "data"],
    "web_backend": ["core", "data"],  # Web 后端适配 core 与内置示例数据，不得反向依赖 GUI
}

# 合规敏感词（禁止出现在任何代码/文案中）
FORBIDDEN_PATTERNS = [
    r"虚开.*发票",
    r"隐匿.*收入",
    r"虚构.*成本",
    r"买发票",
    r"走账",
    r"套现",
    r"洗钱",
    r"逃税",
]

# 开发方案中的 ADR（Architecture Decision Records）
ADRS = {
    "ADR-001": {"title": "Tk 基线界面与 FastAPI 本地 Web 服务共存",   "check": lambda: not any_file_uses("PyQt") and not any_file_uses("Electron") and not any_file_uses("flask")},
    "ADR-002": {"title": "确定性数学引擎 vs AI 兜底",   "check": lambda: True},  # 软检查，在代码审查中覆盖
    "ADR-003": {"title": "规则引擎兜底",                 "check": lambda: True},
    "ADR-004": {"title": "Excel 作为数据交换格式",       "check": lambda: True},
    "ADR-005": {"title": "同义词词典硬编码 + 可扩展",     "check": lambda: True},
    "ADR-006": {"title": "ReportLab + python-docx",      "check": lambda: not any_file_uses("pandoc") and not any_file_uses("WeasyPrint")},
    "ADR-007": {"title": "openpyxl",                     "check": lambda: not any_file_uses("xlsxwriter")},
    "ADR-008": {"title": "Matplotlib 渲染图表",           "check": lambda: not any_file_uses("plotly") and not any_file_uses("pyecharts")},
}

# ── 辅助函数 ─────────────────────────────────────────────────────────────

def find_py_files(root: Path) -> List[Path]:
    """递归查找项目下所有 .py 文件（排除 .git / .venv / __pycache__ / release 等发布产物）"""
    py_files = []
    for f in root.rglob("*.py"):
        rel = f.relative_to(root)
        if any(p.startswith(".") and p != "." for p in rel.parts):
            continue
        if "__pycache__" in rel.parts:
            continue
        # release/ 为构建产物，与 .venv/ 一样不参与模块声明检查
        if "release" in rel.parts:
            continue
        py_files.append(f)
    return sorted(py_files)


def py_files_relative(root: Path) -> List[str]:
    # Windows 下 relative_to 产出反斜杠，统一为正斜杠以匹配期望清单
    return [str(f.relative_to(root)).replace("\\", "/") for f in find_py_files(root)]


def any_file_uses(keyword: str) -> bool:
    """检查项目中是否有任何 .py 文件引用了某个关键字"""
    for f in find_py_files(PROJECT_ROOT):
        try:
            content = f.read_text(encoding="utf-8")
            if keyword.lower() in content.lower():
                return True
        except Exception:
            pass
    return False


def read_py_source(path: Path) -> Tuple[bool, str]:
    """读取并解析 Python 源码，返回 (is_valid, error_msg)"""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        return False, f"无法读取文件：{e}"

    try:
        ast.parse(text, filename=str(path))
        return True, ""
    except SyntaxError as e:
        return False, f"语法错误（行 {e.lineno}）：{e.msg}"


def find_missing(expected: dict, actual_py: List[str], root: Path) -> Tuple[List[str], List[str]]:
    """对比期望文件列表和实际文件列表，返回 (缺失, 意外)。

    expected 中既可能含 .py 也可能含其他类型文件（如 requirements.txt）；
    .py 用 actual_py 判断，非 .py 直接在磁盘上判断存在性。
    """
    expected_set = set()
    for category_dir, files in expected.items():
        for f in files:
            if category_dir:
                expected_set.add(f"{category_dir}/{f}")
            else:
                expected_set.add(f)

    actual_set = set(actual_py)
    # 补充非 .py 文件的存在性（按 expected 列表在磁盘上探测）
    for category_dir, files in expected.items():
        for f in files:
            if f.endswith(".py"):
                continue
            rel = f"{category_dir}/{f}" if category_dir else f
            full = root / rel
            if full.exists():
                actual_set.add(rel)

    # __init__.py 在所有子目录中都算正常，不视为意外
    filtered_actual = set()
    for f in actual_set:
        if f.endswith("/__init__.py"):
            expected_set.add(f)  # 只要是子目录下的 __init__ 都算预期
        filtered_actual.add(f)

    missing = sorted(expected_set - filtered_actual)
    unexpected = sorted(filtered_actual - expected_set)
    return missing, unexpected


def extract_imports(source: str) -> List[str]:
    """从源码中提取 import 语句的目标模块"""
    imports = []
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module.split(".")[0])
    except SyntaxError:
        pass
    return imports


def detect_forbidden_text(content: str, patterns: List[str]) -> List[str]:
    """检测是否包含违禁表述（排除"严禁/禁止"等否定语境中的引用）"""
    hits = []
    for pattern in patterns:
        matches = re.finditer(pattern, content)
        for m in matches:
            start = max(0, m.start() - 20)
            preceding = content[start:m.start()]
            # 如果违禁词前面 20 个字符以内有"严禁/禁止/不得/不可/避免"，则认为是合规声明而非违规
            if re.search(r"(严禁|禁止|不得|不可|避免)", preceding):
                continue
            hits.append(pattern)
            break  # 每种 pattern 只报告一次
    return hits


# ── 检查器 ───────────────────────────────────────────────────────────────

class Checker:
    def __init__(self, root: Path):
        self.root = root
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.info: List[str] = []
        self.py_files = find_py_files(root)
        self.relative_py_files = py_files_relative(root)

    # ── 检查 1：模块完整性 ──
    def check_module_completeness(self):
        """检查开发方案中声明的模块是否都存在"""
        missing, unexpected = find_missing(EXPECTED_MODULES, self.relative_py_files, self.root)

        for f in missing:
            self.errors.append(f"[模块缺失] 开发方案已声明但未找到：{f}")
        for f in unexpected:
            self.warnings.append(f"[意外模块] 未在开发方案中声明的文件：{f}")

        if not missing and not unexpected:
            self.info.append("[模块完整性] 所有已声明模块均存在，新增模块已有记录 ✓")

    # ── 检查 2：Python 语法 ──
    def check_syntax(self):
        """逐文件检查 Python 语法"""
        errors = 0
        for f in self.py_files:
            valid, msg = read_py_source(f)
            if not valid:
                rel = f.relative_to(self.root)
                self.errors.append(f"[语法错误] {rel}：{msg}")
                errors += 1

        if errors == 0:
            self.info.append(f"[语法检查] 全部 {len(self.py_files)} 个 .py 文件语法正确 ✓")

    # ── 检查 3：架构依赖方向 ──
    def check_architecture(self):
        """验证层间依赖方向是否正确"""
        for f in self.py_files:
            rel = f.relative_to(self.root)
            parts = rel.parts
            # 判断文件所在层
            if len(parts) >= 2 and parts[0] == "core":
                layer = "core"
            elif len(parts) >= 2 and parts[0] == "data":
                layer = "data"
            elif len(parts) >= 2 and parts[0] == "tests":
                layer = "tests"
            elif len(parts) >= 2 and parts[0] == "web_backend":
                layer = "web_backend"
            elif len(parts) == 1:
                layer = "."
            else:
                continue  # 其他目录跳过

            allowed_targets = LAYER_DEP_RULES.get(layer, [])
            try:
                source = f.read_text(encoding="utf-8")
            except Exception:
                continue

            imports = extract_imports(source)
            for imp in imports:
                if imp in ("os", "sys", "re", "json", "csv", "math", "time",
                           "datetime", "pathlib", "typing", "ast", "logging",
                           "tkinter", "matplotlib", "openpyxl", "reportlab",
                           "pytest", "unittest", "subprocess", "shutil",
                           "collections", "copy", "itertools", "functools",
                           "abc", "io", "textwrap", "decimal", "enum",
                           "dataclasses", "calendar", "hashlib", "uuid",
                           "requests", "docx",
                           "__future__", "__init__"):
                    continue  # 标准库/已知第三方库，跳过

                # 判断导入的模块是否在其之上
                imp_path = imp.replace(".", "/")
                in_higher_layer = False
                for target_layer, _ in LAYER_DEP_RULES.items():
                    if target_layer == ".":
                        continue
                    if imp_path.startswith(target_layer) and target_layer not in allowed_targets:
                        in_higher_layer = True
                        break

                if in_higher_layer:
                    self.warnings.append(
                        f"[架构警告] {rel} 导入了上层模块 '{imp}'（{layer} 不应导入上层）"
                    )

        # 检查是否存在 core/ 导入 gui/ 或 main 的情况
        for f in self.py_files:
            rel = str(f.relative_to(self.root))
            if rel.startswith("core/"):
                try:
                    source = f.read_text(encoding="utf-8")
                except Exception:
                    continue
                if "import gui" in source or "from gui" in source or "import main" in source or "from main" in source:
                    self.errors.append(f"[架构违规] {rel}：core 层导入了上层模块（gui/main），违反分层设计")

        if not any("架构警告" in e or "架构违规" in e for e in self.errors + self.warnings):
            self.info.append("[架构检查] 层间依赖方向正确 ✓")

    # ── 检查 4：合规红线 ──
    def check_compliance(self):
        """检查代码/文档中是否有合规敏感表述"""
        all_sources = list(self.py_files)
        # 也检查 md 和 docx（docx 本检查跳过，md 覆盖）
        all_sources.extend(list(self.root.glob("*.md")))
        all_sources.extend(list(self.root.glob("*.txt")))
        all_sources.extend(list(self.root.glob("*.html")))

        found = False
        for f in all_sources:
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            hits = detect_forbidden_text(content, FORBIDDEN_PATTERNS)
            if hits:
                rel = f.relative_to(self.root)
                for pattern in hits:
                    self.errors.append(f"[合规红线] {rel} 含违禁表述「{pattern}」")
                found = True

        if not found:
            self.info.append("[合规检查] 未发现违禁表述 ✓")

    # ── 检查 5：ADR 遵循 ──
    def check_adr(self):
        """验证技术选型是否偏离设计决策"""
        violations = []
        for adr_id, adr in ADRS.items():
            if not adr["check"]():
                violations.append(adr_id)

        for vid in violations:
            self.warnings.append(f"[ADR 偏离] {vid}「{ADRS[vid]['title']}」— 检测到不符的技术选型")

        if not violations:
            self.info.append("[ADR 检查] 技术选型遵循设计决策 ✓")

    # ── 检查 6：AGENTS.md 时效性 ──
    def check_agents_md(self):
        """检查 AGENTS.md 是否需要更新"""
        agents_path = self.root / "AGENTS.md"
        if not agents_path.exists():
            self.warnings.append("[AGENTS.md] 未找到 AGENTS.md 项目记忆文件")
            return

        # 检查最后一行是否有旧的日期
        try:
            content = agents_path.read_text(encoding="utf-8")
        except Exception:
            return

        # 检查最新代码文件修改时间
        if self.py_files:
            latest_mtime = max(f.stat().st_mtime for f in self.py_files)
            latest_time = time.strftime("%Y-%m-%d", time.localtime(latest_mtime))

            # 检查 AGENTS.md 中的日期是否匹配
            date_match = re.search(r"更新：(\d{4}-\d{2}-\d{2})", content)
            if date_match and date_match.group(1) < latest_time:
                self.warnings.append(
                    f"[AGENTS.md 过期] 文档更新日期 {date_match.group(1)}，"
                    f"有代码修改至 {latest_time}，建议更新 AGENTS.md"
                )

    # ── 检查 7：exports 一致性 ──
    def check_exports(self):
        """检查 core/__init__.py 是否导出了所有核心模块的公共接口"""
        init_path = self.root / "core" / "__init__.py"
        if not init_path.exists():
            self.warnings.append("[包结构] core/__init__.py 不存在，建议创建以便统一导出")
            return

        try:
            init_content = init_path.read_text(encoding="utf-8")
        except Exception:
            return

        # 检查 core 下的关键模块是否被 import
        core_modules = ["finance", "diagnostic", "interactive", "report", "action_pack",
                        "models", "industry", "parser",
                        "budget", "budget_categories", "budget_template"]
        for mod in core_modules:
            init_path_full = self.root / "core" / f"{mod}.py"
            if init_path_full.exists():
                # 检查 core/__init__.py 中是否导入了此模块
                if f"import {mod}" not in init_content and f"from .{mod}" not in init_content:
                    self.warnings.append(f"[导出检查] core/__init__.py 未导出 core/{mod}.py，"
                                         f"虽然不阻塞运行但建议显式导出")

    # ── 运行全部检查 ──
    def run_all(self):
        self.check_module_completeness()
        self.check_syntax()
        self.check_architecture()
        self.check_compliance()
        self.check_adr()
        self.check_agents_md()
        self.check_exports()

    def report(self) -> int:
        """输出报告，返回退出码"""
        print("\n" + "=" * 60)
        print("  利润宝 · 项目守护者检查报告")
        print("=" * 60)

        total = len(self.errors) + len(self.warnings) + len(self.info)

        if self.errors:
            print(f"\n  ❌ 错误（{len(self.errors)} 项，必须修复）：")
            for e in self.errors:
                print(f"     • {e}")

        if self.warnings:
            print(f"\n  ⚠️  警告（{len(self.warnings)} 项，建议关注）：")
            for w in self.warnings:
                print(f"     • {w}")

        if self.info:
            print(f"\n  ✅ 通过（{len(self.info)} 项）：")
            for i in self.info:
                print(f"     • {i}")

        print("\n" + "-" * 60)
        print(f"  总览：{len(self.errors)} 错误 | {len(self.warnings)} 警告 | {len(self.info)} 通过")

        if self.errors:
            print("  状态：❌ 未通过（存在必须修复的错误）")
            print("  提示：修复后重新提交，或使用 git commit --no-verify 跳过")
            return 2
        elif self.warnings:
            print("  状态：⚠️  通过但有警告")
            return 1
        else:
            print("  状态：✅ 全部通过")
            return 0


def main():
    # 解析参数
    quick_mode = "--quick" in sys.argv
    list_mode = "--list" in sys.argv

    if list_mode:
        print("\n利润宝 · 项目守护者 - 检查项清单\n")
        print("  [1] 模块完整性    — 开发方案声明 vs 实际文件")
        print("  [2] Python 语法   — 逐文件 AST 解析")
        print("  [3] 架构依赖      — 层间依赖方向检查")
        print("  [4] 合规红线      — 违禁表述检测")
        print("  [5] ADR 遵循      — 技术选型偏离检查")
        print("  [6] AGENTS.md     — 项目记忆时效性")
        print("  [7] 导出检查      — core/__init__.py 完整性")
        print("\n参数：")
        print("  --quick     仅运行 [1][2][3] 快速检查")
        print("  --list      显示此清单")
        print("  无参数      运行全部 7 项检查")
        return 0

    print(f"📋 利润宝 · 项目守护者")
    print(f"   项目路径：{PROJECT_ROOT}")
    print(f"   检查时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    if quick_mode:
        print(f"   模式：快速检查\n")

    checker = Checker(PROJECT_ROOT)

    if quick_mode:
        checker.check_module_completeness()
        checker.check_syntax()
        checker.check_architecture()
    else:
        checker.run_all()

    return checker.report()


if __name__ == "__main__":
    # 切换到项目根目录
    os.chdir(PROJECT_ROOT)
    sys.exit(main())
