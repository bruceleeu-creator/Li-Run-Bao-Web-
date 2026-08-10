"""利润宝 · 模板版 Excel 读写（T6.1 + T6.5）。

读取用户原始模板（只读，不修改原文件），导出时可写入项目 `demo_output/`
或用户选择的新路径。所有写出的 Excel 公式与 Python 计算保持一致，且
空白态不得出现 `#DIV/0!/#VALUE!/#REF!`。

- `read_template(path)`：读取原模板 → BudgetPlan
- `write_template(plan, path)`：写出 BudgetPlan → Excel（保留模板结构）
- 工会经费 R90 补齐 F/H/J 公式
- 修正自动筛选范围为 A13:J98
- 冻结窗格 A14
- 行业参考 Sheet 含来源/年度/地区/核验状态

依赖 openpyxl（ADR-004/007）。
"""
from __future__ import annotations

import os
from typing import List, Optional

from .budget_categories import (
    EXPENSE_CATEGORIES,
    TEMPLATE_BALANCE_ROW,
    TEMPLATE_FIRST_ROW,
    TEMPLATE_HEADER_ROW,
    TEMPLATE_LAST_ROW,
    TEMPLATE_TOTAL_ROW,
)
from .budget import (
    BudgetPlan,
    ExpenseLine,
    TopInputs,
    compute_all,
    make_empty_plan,
)


class TemplateError(Exception):
    """模板读写错误。"""


def _require_openpyxl():
    try:
        import openpyxl
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
        from openpyxl.worksheet.datavalidation import DataValidation
        return openpyxl, Alignment, Border, Font, PatternFill, Side, get_column_letter, DataValidation
    except ImportError as e:
        raise TemplateError(
            "未安装 openpyxl，无法读写 Excel 模板。"
            "请运行：python3 -m pip install openpyxl"
        ) from e


# ── 读取 ─────────────────────────────────────────────────────────────────

def read_template(path: str, company_name: str = "", industry: str = "制造业", year: int = 0) -> BudgetPlan:
    """读取用户原始模板（只读）。

    - 顶部 A2:E9 字段映射到 TopInputs
    - A14:J97 的 84 行费用明细映射到 ExpenseLine
    - 行业参考 Sheet 仅保留表头时记入 plan.industry_benchmarks 为空列表
    - 不修改原文件

    P1-3：导入时验证目标 Sheet、关键标签、行号与 84 行结构；
          无关 Excel 不得静默当模板。
    P1-3：A/B/C 列读取用户文件中的科目/费用名称/发票名称并规范化，
          不再永远用硬编码字典覆盖用户维护内容。
    """
    if not os.path.exists(path):
        raise TemplateError(f"模板文件不存在：{path}")
    openpyxl = _require_openpyxl()[0]
    try:
        wb = openpyxl.load_workbook(path, data_only=False, read_only=False)
    except Exception as e:
        raise TemplateError(f"无法打开模板文件 {os.path.basename(path)}：{e}") from e

    # P1-3：验证 Sheet 存在
    ws_name = "费用预算表"
    if ws_name not in wb.sheetnames:
        # 容错：取第一个 Sheet 但提示用户
        if len(wb.sheetnames) == 0:
            raise TemplateError("Excel 文件无任何 Sheet，不是有效的利润宝模板。")
        ws = wb.worksheets[0]
        # 验证首个 Sheet 是否符合模板结构（A13 应为「科目名称」）
        a13_val = ws.cell(row=13, column=1).value
        if a13_val != "科目名称":
            raise TemplateError(
                f"首个 Sheet 「{ws.title}」不是有效的费用预算表：A13 应为「科目名称」，实际「{a13_val}」。"
                "请使用利润宝标准模板（含「费用预算表」Sheet）。"
            )
    else:
        ws = wb[ws_name]

    # P1-3：验证关键标签（A1/A12/A13/D13/G13 等）
    # P0-A（CO T7 重开 v3）：容忍匹配——用户原模板常含「公司名/年度/（元）」前后缀，
    # 且 A12/D13 在真实用户文件中可能是「XXXX有限公司XXXX年费用计划表」「上年同期实际发生费用（元）」
    # 等语义等价变体。每个 coord 提供多个语义替代串，任一匹配即通过。
    expected_labels = {
        "A1": ["企业成本计划表"],
        # A12 真实样例：「XXXX有限公司XXXX年费用计划表   」→ 接受「公司年度费用计划表」或「费用计划表」
        "A12": ["公司年度费用计划表", "费用计划表"],
        "A13": ["科目名称"],
        # D13 真实样例：「上年同期实际发生费用（元）」→ 接受「上年同期实际费用」或「上年同期实际发生费用」
        "D13": ["上年同期实际费用", "上年同期实际发生费用"],
        "G13": ["预算费用金额"],
        "I13": ["实际已发生费用金额"],
    }
    missing_labels = []
    for coord, alternatives in expected_labels.items():
        actual = ws[coord].value
        if not any(_label_matches(actual, alt) for alt in alternatives):
            missing_labels.append(
                f"{coord} 应包含以下任一：「{' / '.join(alternatives)}」，实际「{actual}」"
            )
    if missing_labels:
        raise TemplateError(
            "模板结构不匹配，无法载入：\n  - " + "\n  - ".join(missing_labels)
            + "\n请使用利润宝标准模板。"
        )

    # P1-3：验证 84 行结构（A14:A97 必须有数据或留空但行存在）
    # 检查 A14 与 A97 是否存在有效行（A 列可为空，但行号必须存在）
    if ws.max_row < TEMPLATE_LAST_ROW:
        raise TemplateError(
            f"模板行数不足：应有 {TEMPLATE_LAST_ROW} 行，实际 {ws.max_row} 行。"
            "请使用利润宝标准模板（A14:J97 共 84 行明细）。"
        )

    plan = make_empty_plan(company_name=company_name, industry=industry, year=year)
    plan.source_path = path

    # 顶部输入区
    ti = plan.top_inputs
    ti.budget_revenue = _read_num(ws, "C2") or 0.0
    ti.budget_cost = _read_num(ws, "C3") or 0.0
    ti.last_year_revenue = _read_num(ws, "C6") or 0.0
    ti.last_year_cost = _read_num(ws, "C8") or 0.0
    e2 = _read_num(ws, "E2")
    e3 = _read_num(ws, "E3")
    e4 = _read_num(ws, "E4")
    if e2 is not None:
        ti.industry_contribution_rate = float(e2)
    if e3 is not None:
        ti.company_contribution_rate = float(e3)
    if e4 is not None:
        ti.income_tax_rate = float(e4)
    ti.industry_rate_source = "模板默认（待核验）"
    ti.industry_rate_verified = False

    # P1-3：明细行 - 优先读取用户文件中的科目/费用名称/发票名称（A/B/C 列）
    # 仅当用户文件对应行为空时，才回退到默认字典
    for line in plan.lines:
        r = line.row
        # A/B/C 列：读取用户维护的字典内容（不强制覆盖）
        a_val = ws.cell(row=r, column=1).value
        b_val = ws.cell(row=r, column=2).value
        c_val = ws.cell(row=r, column=3).value
        if a_val and str(a_val).strip():
            line.subject = str(a_val).strip()
        if b_val and str(b_val).strip():
            line.expense_name = str(b_val).strip()
        if c_val and str(c_val).strip():
            line.invoice_name = str(c_val).strip()
        # D/G/I 输入列
        line.last_year_actual = _read_num(ws, f"D{r}") or 0.0
        line.budget_amount = _read_num(ws, f"G{r}") or 0.0
        line.actual_amount = _read_num(ws, f"I{r}") or 0.0

    # 行业参考 Sheet
    if "行业企业所得税贡献率参考" in wb.sheetnames:
        ws2 = wb["行业企业所得税贡献率参考"]
        benchmarks: List[dict] = []
        for r in range(3, ws2.max_row + 1):
            industry_name = ws2.cell(row=r, column=1).value
            if not industry_name:
                continue
            benchmarks.append({
                "industry": str(industry_name).strip(),
                "rate": _read_num(ws2, f"B{r}") or 0.0,
                "source": str(ws2.cell(row=r, column=3).value or "").strip(),
                "year": str(ws2.cell(row=r, column=4).value or "").strip(),
                "region": str(ws2.cell(row=r, column=5).value or "").strip(),
                "verified": str(ws2.cell(row=r, column=6).value or "").strip() in ("是", "True", "true", "1"),
            })
        plan.industry_benchmarks = benchmarks

    try:
        wb.close()
    except Exception:
        pass

    # 计算所有派生字段
    compute_all(plan)
    return plan


def _read_num(ws, coord) -> Optional[float]:
    """安全读取单元格数值；公式时取 value，文本返回 None。"""
    v = ws[coord].value
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s == "" or s.startswith("="):
        return None
    try:
        return float(s)
    except ValueError:
        return None


# P0-A：全/半角括号 + 「元 / Yuan」后缀 + 多余空白规范化
import re as _re
_LABEL_SUFFIX_RE = _re.compile(r"[\(（]\s*(?:元|Yuan|yuan|YUAN)\s*[\)）]\s*$")


def _normalize_label(value) -> str:
    """规范化标签：去首尾空白；去除尾部全/半角括号包裹的「元/Yuan」后缀；压缩连续空白。"""
    if value is None:
        return ""
    s = str(value).strip()
    # 反复去除尾部括号后缀（防止「（元）(Yuan)」连写）
    while True:
        new_s = _LABEL_SUFFIX_RE.sub("", s).strip()
        if new_s == s:
            break
        s = new_s
    # 压缩连续空白
    s = _re.sub(r"\s+", "", s)
    return s


def _label_matches(actual, expected: str) -> bool:
    """P0-A：标签容忍匹配。

    规则：
    1. 二者规范化后任一为空 → False
    2. 规范化后 actual 包含 expected（子串）→ True
       例：actual="示例公司 2024 企业成本计划表（元）", expected="企业成本计划表" → True
    3. 反向包含（expected 包含 actual）→ True（罕见但容错）
    4. 否则 False
    """
    a = _normalize_label(actual)
    e = _normalize_label(expected)
    if not a or not e:
        return False
    return (e in a) or (a in e)


# ── 写入 ─────────────────────────────────────────────────────────────────

# 样式常量
_HEADER_FILL = "1E40AF"
_HEADER_FONT_COLOR = "FFFFFF"
_NOTE_FONT_COLOR = "B91C1C"
_RED_FILL = "FEE2E2"  # 预算费用重点（红色语义）
_YELLOW_FILL = "FEF3C7"  # 差额/剩余额度重点（黄色语义）
_INPUT_FILL = "F0F9FF"  # 可输入单元格底色
_FORMULA_FILL = "F8FAFC"  # 公式单元格底色


def write_template(plan: BudgetPlan, path: str, session=None) -> str:
    """写出 BudgetPlan 到 Excel（保留模板结构与红/黄重点语义）。

    - 顶部公式使用 Excel 公式（IF(C2=0,0,...)）
    - 明细 E/F/H/J 使用 Excel 公式
    - 工会经费 R90 补齐 F/H/J 公式
    - E8 = I98 联动
    - G100 = E7 - G98
    - 自动筛选 A13:J98
    - 冻结窗格 A14
    - 空白态 0 公式错误
    - P0-2：同一工作簿创建第三 Sheet「诊断与行动清单」（有 session 时填决策，无则空表头）
    - P0-3：禁止覆盖原始模板（比较规范化真实路径）
    - P1-2：打印设置（横向 A4 / fitToWidth=1 / 重复表头行 / 合理页边距）

    Args:
        plan: 预算计划
        path: 导出路径
        session: 可选诊断会话（core.interactive.Session）；有则第三 Sheet 填决策数据

    Raises:
        TemplateError: 路径冲突或写出失败
    """
    # P0-3：禁止覆盖原始模板
    if plan.source_path:
        src_real = os.path.realpath(plan.source_path)
        dst_real = os.path.realpath(path)
        if src_real == dst_real:
            raise TemplateError(
                f"导出路径与原始模板路径相同（{dst_real}），禁止覆盖原始模板。请另存为新文件。"
            )

    # P0-C（CO T7 重开）：导出前调用 validate_plan；失败时不写入任何文件、不修改 plan
    from .budget import validate_plan
    ok, errors = validate_plan(plan)
    if not ok:
        raise TemplateError(
            "预算计划校验未通过，拒绝导出 Excel：\n  - " + "\n  - ".join(errors)
            + "\n请先在「2. 模板工作台」修正输入后再导出。"
        )

    openpyxl, Alignment, Border, Font, PatternFill, Side, get_column_letter, DataValidation = _require_openpyxl()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    try:
        wb = openpyxl.Workbook()
        _build_budget_sheet(wb, openpyxl, Alignment, Border, Font, PatternFill, Side, DataValidation, plan)
        _build_industry_benchmark_sheet(wb, openpyxl, Alignment, Border, Font, PatternFill, Side, plan)
        # P0-2：第三 Sheet「诊断与行动清单」
        _build_action_summary_sheet(wb, openpyxl, Alignment, Border, Font, PatternFill, Side, plan, session)
        wb.save(path)
        return path
    except TemplateError:
        raise
    except Exception as e:
        raise TemplateError(f"Excel 模板写出失败：{type(e).__name__}: {e}") from e


def _apply_header_style(cell, Font, PatternFill, Alignment):
    cell.font = Font(color=_HEADER_FONT_COLOR, bold=True, size=11)
    cell.fill = PatternFill("solid", fgColor=_HEADER_FILL)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _apply_input_style(cell, Font, PatternFill, Alignment, Border, Side):
    cell.fill = PatternFill("solid", fgColor=_INPUT_FILL)
    cell.font = Font(size=10)
    cell.alignment = Alignment(horizontal="right", vertical="center")
    _apply_border(cell, Border, Side)


def _apply_formula_style(cell, Font, PatternFill, Alignment, Border, Side):
    cell.fill = PatternFill("solid", fgColor=_FORMULA_FILL)
    cell.font = Font(size=10, color="555555")
    cell.alignment = Alignment(horizontal="right", vertical="center")
    _apply_border(cell, Border, Side)


def _apply_border(cell, Border, Side):
    side = Side(style="thin", color="BFBFBF")
    cell.border = Border(left=side, right=side, top=side, bottom=side)


def _build_budget_sheet(
    wb, openpyxl, Alignment, Border, Font, PatternFill, Side, DataValidation, plan: BudgetPlan
) -> None:
    """Sheet1：费用预算表（A1:J100，保留模板结构与红/黄重点语义）。"""
    from openpyxl.utils import get_column_letter
    ws = wb.active
    ws.title = "费用预算表"

    # ── 标题 ──
    ws["A1"] = "企业成本计划表"
    ws["A1"].font = Font(bold=True, size=16, color="1E3A8A")
    ws.merge_cells("A1:J1")
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    # ── 顶部输入区 A2:E9 ──
    ti = plan.top_inputs
    # 标签列（A/B）+ 输入列（C）+ 标签列（D）+ 输入列（E）
    ws["A2"] = "预算营业收入"
    ws["C2"] = ti.budget_revenue
    ws["D2"] = "行业所得税贡献率"
    ws["E2"] = ti.industry_contribution_rate
    ws["A3"] = "预算营业成本"
    ws["C3"] = ti.budget_cost
    ws["D3"] = "企业预算所得税贡献率"
    ws["E3"] = ti.company_contribution_rate
    ws["A4"] = "毛利"
    ws["D4"] = "所得税税率"
    ws["E4"] = ti.income_tax_rate
    ws["A5"] = "毛利率"
    ws["D5"] = "企业应交所得税预算"
    ws["A6"] = "上年度同期营业收入"
    ws["C6"] = ti.last_year_revenue
    ws["D6"] = "利润总额预算"
    ws["A7"] = "收入增长率"
    ws["D7"] = "预算费用上限"
    ws["A8"] = "上年度同期营业成本"
    ws["C8"] = ti.last_year_cost
    ws["D8"] = "实际已发生费用"
    ws["A9"] = "上年度毛利率"
    ws["D9"] = "费用差额"

    # 输入单元格样式（C2/C3/C6/C8/E2/E3/E4）
    for coord in ("C2", "C3", "C6", "C8", "E2", "E3", "E4"):
        _apply_input_style(ws[coord], Font, PatternFill, Alignment, Border, Side)
    # 标签样式
    for r in range(2, 10):
        for col in ("A", "D"):
            cell = ws[f"{col}{r}"]
            cell.font = Font(bold=True, size=10)
            cell.alignment = Alignment(horizontal="left", vertical="center")
            _apply_border(cell, Border, Side)

    # 顶部公式（C4/C5/C7/C9/E5/E6/E7/E8/E9）— 全部加入零值保护
    ws["C4"] = "=C2-C3"
    ws["C5"] = "=IF(C2=0,0,C4/C2)"
    ws["C7"] = "=IF(C6=0,0,(C2-C6)/C6)"
    ws["C9"] = "=IF(C6=0,0,(C6-C8)/C6)"
    ws["E5"] = "=E3*C2"
    ws["E6"] = "=IF(E4=0,0,E5/E4)"
    ws["E7"] = "=C4-E6"
    ws["E8"] = "=I98"  # 与明细联动
    ws["E9"] = "=E7-E8"
    for coord in ("C4", "C5", "C7", "C9", "E5", "E6", "E7", "E8", "E9"):
        _apply_formula_style(ws[coord], Font, PatternFill, Alignment, Border, Side)
    # 比例格式
    for coord in ("C5", "C7", "C9", "E2", "E3", "E4"):
        ws[coord].number_format = "0.00%"
    # 金额格式
    for coord in ("C2", "C3", "C4", "C6", "C8", "E5", "E6", "E7", "E8", "E9"):
        ws[coord].number_format = '#,##0.00'

    # E7 费用预算上限：红色重点（小于 0 时显示高风险提示）
    ws["E7"].fill = PatternFill("solid", fgColor=_RED_FILL)
    ws["E7"].font = Font(size=10, bold=True, color="B91C1C")
    # E9 费用差额：黄色重点
    ws["E9"].fill = PatternFill("solid", fgColor=_YELLOW_FILL)
    ws["E9"].font = Font(size=10, bold=True)

    # ── 明细表 A12:J98 ──
    # 第 12 行：表标题
    ws["A12"] = "公司年度费用计划表"
    ws["A12"].font = Font(bold=True, size=12, color="1E3A8A")
    ws.merge_cells("A12:J12")
    ws["A12"].alignment = Alignment(horizontal="center", vertical="center")

    # 第 13 行：表头（A13:J13）
    headers = [
        ("A13", "科目名称"),
        ("B13", "费用名称"),
        ("C13", "发票名称"),
        ("D13", "上年同期实际费用"),
        ("E13", "上年费用占上年收入比例"),
        ("F13", "参考费用金额"),
        ("G13", "预算费用金额"),
        ("H13", "预算费用占预算收入比例"),
        ("I13", "实际已发生费用金额"),
        ("J13", "差额"),
    ]
    for coord, h in headers:
        cell = ws[coord]
        cell.value = h
        _apply_header_style(cell, Font, PatternFill, Alignment)
        _apply_border(cell, Border, Side)
    ws.row_dimensions[13].height = 32

    # 第 14-97 行：84 行明细
    for line in plan.lines:
        r = line.row
        # 字典列
        ws.cell(row=r, column=1, value=line.subject)
        ws.cell(row=r, column=2, value=line.expense_name)
        ws.cell(row=r, column=3, value=line.invoice_name)
        # 输入列：D / G / I
        ws.cell(row=r, column=4, value=round(line.last_year_actual, 2))
        ws.cell(row=r, column=7, value=round(line.budget_amount, 2))
        ws.cell(row=r, column=9, value=round(line.actual_amount, 2))
        # 公式列：E / F / H / J
        ws.cell(row=r, column=5, value=f"=IF($C$6=0,0,D{r}/$C$6)")
        ws.cell(row=r, column=6, value=f"=D{r}*(1+$C$7)")
        ws.cell(row=r, column=8, value=f"=IF($C$2=0,0,G{r}/$C$2)")
        ws.cell(row=r, column=10, value=f"=G{r}-I{r}")
        # 样式
        for col in (1, 2, 3):
            cell = ws.cell(row=r, column=col)
            cell.font = Font(size=10)
            cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
            _apply_border(cell, Border, Side)
        for col in (4, 7, 9):
            cell = ws.cell(row=r, column=col)
            _apply_input_style(cell, Font, PatternFill, Alignment, Border, Side)
            cell.number_format = '#,##0.00'
        for col in (5, 8):
            cell = ws.cell(row=r, column=col)
            _apply_formula_style(cell, Font, PatternFill, Alignment, Border, Side)
            cell.number_format = '0.00%'
        for col in (6, 10):
            cell = ws.cell(row=r, column=col)
            _apply_formula_style(cell, Font, PatternFill, Alignment, Border, Side)
            cell.number_format = '#,##0.00'
        # 工会经费 R90：补齐 F/H/J（已通过通用公式补齐，此处仅标记）
        if line.row == 90:
            ws.cell(row=r, column=6).comment = None  # 不留旧注释
        # G 列（预算费用）红色重点
        ws.cell(row=r, column=7).fill = PatternFill("solid", fgColor=_RED_FILL)
        ws.cell(row=r, column=7).font = Font(size=10, bold=True)
        # J 列（差额）黄色重点
        ws.cell(row=r, column=10).fill = PatternFill("solid", fgColor=_YELLOW_FILL)
        ws.cell(row=r, column=10).font = Font(size=10, bold=True)

    # 第 98 行：合计
    ws.cell(row=98, column=1, value="合计")
    ws.cell(row=98, column=1).font = Font(bold=True, size=11)
    ws.cell(row=98, column=1).alignment = Alignment(horizontal="center", vertical="center")
    ws.merge_cells("A98:C98")
    # D98/G98/I98/J98 求和；E98/F98/H98 联动
    ws["D98"] = f"=SUM(D{TEMPLATE_FIRST_ROW}:D{TEMPLATE_LAST_ROW})"
    ws["E98"] = "=IF($C$6=0,0,D98/$C$6)"
    ws["F98"] = "=D98*(1+$C$7)"
    ws["G98"] = f"=SUM(G{TEMPLATE_FIRST_ROW}:G{TEMPLATE_LAST_ROW})"
    ws["H98"] = "=IF($C$2=0,0,G98/$C$2)"
    ws["I98"] = f"=SUM(I{TEMPLATE_FIRST_ROW}:I{TEMPLATE_LAST_ROW})"
    ws["J98"] = f"=SUM(J{TEMPLATE_FIRST_ROW}:J{TEMPLATE_LAST_ROW})"
    for col in (4, 6, 7, 9, 10):
        ws.cell(row=98, column=col).number_format = '#,##0.00'
        ws.cell(row=98, column=col).font = Font(bold=True, size=11)
    for col in (5, 8):
        ws.cell(row=98, column=col).number_format = '0.00%'
        ws.cell(row=98, column=col).font = Font(bold=True, size=11, color="555555")
    for col in range(1, 11):
        cell = ws.cell(row=98, column=col)
        _apply_border(cell, Border, Side)
        cell.fill = PatternFill("solid", fgColor="F1F5F9")
    ws.row_dimensions[98].height = 24

    # G100 未分配余额
    ws["A100"] = "未分配余额"
    ws["A100"].font = Font(bold=True, size=11, color="B91C1C")
    ws["A100"].alignment = Alignment(horizontal="right", vertical="center")
    ws.merge_cells("A100:F100")
    ws["G100"] = "=E7-G98"
    ws["G100"].font = Font(bold=True, size=11, color="B91C1C")
    ws["G100"].fill = PatternFill("solid", fgColor=_YELLOW_FILL)
    ws["G100"].number_format = '#,##0.00'
    ws["G100"].alignment = Alignment(horizontal="right", vertical="center")
    _apply_border(ws["G100"], Border, Side)
    ws["H100"] = "（负数表示预算分配超上限）"
    ws["H100"].font = Font(size=9, color="B91C1C", italic=True)
    ws.merge_cells("H100:J100")

    # 自动筛选范围 A13:J98（原模板错误为 A13:P98）
    ws.auto_filter.ref = f"A13:J98"

    # 冻结窗格：A14 以上冻结
    ws.freeze_panes = "A14"

    # 列宽
    widths = [14, 18, 24, 16, 16, 14, 16, 16, 16, 14]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    # 顶部说明
    ws["A11"] = "说明：D/G/I 为输入列（蓝色底）；E/F/H/J 为公式列（灰色底，自动计算）；红色为重点字段；黄色为差额/剩余额度。"
    ws["A11"].font = Font(size=9, italic=True, color="555555")
    ws.merge_cells("A11:J11")

    # P1-2：E4 所得税率下拉数据验证（5% / 15% / 25%）+ 状态列条件格式
    try:
        dv = DataValidation(
            type="list",
            formula1='"0.05,0.15,0.25"',
            allow_blank=False,
            showErrorMessage=True,
            errorTitle="税率非法",
            error="E4 所得税率必须为 0.05 / 0.15 / 0.25 之一（5%/15%/25%）；项目默认值待核验。",
            showInputMessage=True,
            promptTitle="E4 所得税率",
            prompt="请选择 0.05（小型微利）/ 0.15（高新技术企业）/ 0.25（标准税率）",
        )
        dv.add("E4")
        ws.add_data_validation(dv)
    except Exception:
        # 数据验证添加失败不阻塞导出
        pass

    # P1-2：J 列差额条件格式（负数红底，超支提示）
    try:
        from openpyxl.formatting.rule import CellIsRule
        red_fill_cf = PatternFill("solid", fgColor="FECACA")
        # J14:J97 范围内负数标红
        ws.conditional_formatting.add(
            f"J{TEMPLATE_FIRST_ROW}:J{TEMPLATE_LAST_ROW}",
            CellIsRule(operator="lessThan", formula=["0"], fill=red_fill_cf),
        )
    except Exception:
        pass

    # P1-2：应用打印设置（横向 A4 / fitToWidth=1 / 重复表头行 13）
    _apply_print_settings(ws, landscape=True, fit_width=1, repeat_rows="1:13")


def _build_industry_benchmark_sheet(
    wb, openpyxl, Alignment, Border, Font, PatternFill, Side, plan: BudgetPlan
) -> None:
    """Sheet2：行业企业所得税贡献率参考（含来源/年度/地区/核验状态）。"""
    ws = wb.create_sheet("行业企业所得税贡献率参考")

    ws["A1"] = "行业企业所得税贡献率参考表"
    ws["A1"].font = Font(bold=True, size=14, color="1E3A8A")
    ws.merge_cells("A1:F1")
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")

    # 表头
    headers = ["行业", "企业所得税贡献率", "来源", "适用年度", "适用地区", "是否已核验"]
    for ci, h in enumerate(headers, start=1):
        cell = ws.cell(row=2, column=ci, value=h)
        _apply_header_style(cell, Font, PatternFill, Alignment)
        _apply_border(cell, Border, Side)
    ws.row_dimensions[2].height = 28

    # 数据行
    benchmarks = plan.industry_benchmarks or []
    if benchmarks:
        for ri, b in enumerate(benchmarks, start=3):
            ws.cell(row=ri, column=1, value=b.get("industry", ""))
            ws.cell(row=ri, column=2, value=float(b.get("rate", 0.0)))
            ws.cell(row=ri, column=3, value=b.get("source", ""))
            ws.cell(row=ri, column=4, value=b.get("year", ""))
            ws.cell(row=ri, column=5, value=b.get("region", ""))
            ws.cell(row=ri, column=6, value="是" if b.get("verified") else "否")
            for ci in range(1, 7):
                cell = ws.cell(row=ri, column=ci)
                _apply_border(cell, Border, Side)
                cell.alignment = Alignment(horizontal="left" if ci != 2 else "right",
                                           vertical="center", wrap_text=True)
            ws.cell(row=ri, column=2).number_format = '0.00%'
    else:
        ws.cell(row=3, column=1, value="（暂无行业基准数据；用户可维护）")
        ws.cell(row=3, column=1).font = Font(italic=True, color="888888")
        ws.merge_cells("A3:F3")

    # 核验状态提示
    tip_row = (len(benchmarks) + 4) if benchmarks else 5
    ws.cell(row=tip_row, column=1, value=(
        "提示：行业贡献率须标注来源与适用年度；无来源数据应标『模板默认（待核验）』，"
        "不得包装为官方数据。本工具不提供虚构的行业基准。"
    ))
    ws.cell(row=tip_row, column=1).font = Font(size=9, italic=True, color="B91C1C")
    ws.merge_cells(start_row=tip_row, start_column=1, end_row=tip_row, end_column=6)
    ws.cell(row=tip_row, column=1).alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    ws.row_dimensions[tip_row].height = 32

    # 列宽
    widths = [16, 18, 32, 12, 14, 12]
    for i, w in enumerate(widths, start=1):
        from openpyxl.utils import get_column_letter
        ws.column_dimensions[get_column_letter(i)].width = w

    ws.freeze_panes = "A3"
    _apply_print_settings(ws, landscape=False, fit_width=1, repeat_rows="1:2")


def _build_action_summary_sheet(
    wb, openpyxl, Alignment, Border, Font, PatternFill, Side, plan: BudgetPlan, session=None
) -> None:
    """P0-2：Sheet3「诊断与行动清单」。

    有 session 时填入决策数据（发现/选项/目标/成本节约/税收节约/税负影响/净影响/负责人/期限/执行状态）；
    无 session 时保留完整空表头 + 「尚未完成诊断」提示，不得伪造建议。
    """
    from openpyxl.utils import get_column_letter
    ws = wb.create_sheet("诊断与行动清单")

    ws["A1"] = "诊断与行动清单"
    ws["A1"].font = Font(bold=True, size=14, color="1E3A8A")
    ws.merge_cells("A1:J1")
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 26

    # 表头（10 列）
    headers = [
        "序号", "发现", "选项", "目标值",
        "成本节约(元)", "税收节约(元)", "税负影响(元)", "净影响(元)",
        "负责人", "期限",
    ]
    for ci, h in enumerate(headers, start=1):
        cell = ws.cell(row=2, column=ci, value=h)
        _apply_header_style(cell, Font, PatternFill, Alignment)
        _apply_border(cell, Border, Side)
    ws.row_dimensions[2].height = 28

    # 数据行
    if session is not None and getattr(session, "draft2", None):
        # 有会话：填入决策数据
        for ri, d in enumerate(session.draft2, start=3):
            ws.cell(row=ri, column=1, value=ri - 2)
            ws.cell(row=ri, column=2, value=getattr(d, "finding_title", "") or getattr(d, "finding_id", ""))
            ws.cell(row=ri, column=3, value=getattr(d, "option_name", "") or getattr(d, "option_label", ""))
            ws.cell(row=ri, column=4, value=str(getattr(d, "target_value", "")))
            # 成本节约/税收节约/税负影响/净影响（来自 draft2 条目）
            ws.cell(row=ri, column=5, value=float(getattr(d, "cost_saving", 0.0) or 0.0))
            ws.cell(row=ri, column=6, value=float(getattr(d, "tax_saving", 0.0) or 0.0))
            ws.cell(row=ri, column=7, value=float(getattr(d, "tax_impact", 0.0) or 0.0))
            # P0-B（CO T7 重开）：Draft2Entry 字段为 est_saving（非 net_impact）；
            # 旧代码用 net_impact 永远取到 0，导致 Excel 第三 Sheet 净影响列全 0。
            ws.cell(row=ri, column=8, value=float(getattr(d, "est_saving", 0.0) or 0.0))
            ws.cell(row=ri, column=9, value=str(getattr(d, "owner", "")))   # 负责人（可空）
            ws.cell(row=ri, column=10, value=str(getattr(d, "deadline", "")))  # 期限（可空）
            for ci in range(1, 11):
                cell = ws.cell(row=ri, column=ci)
                _apply_border(cell, Border, Side)
                cell.alignment = Alignment(horizontal="left" if ci in (2, 3, 4) else "right",
                                           vertical="center", wrap_text=True)
            for ci in (5, 6, 7, 8):
                ws.cell(row=ri, column=ci).number_format = '#,##0.00'
        data_rows = len(session.draft2)
        # 汇总行
        sum_row = 3 + data_rows
        ws.cell(row=sum_row, column=1, value="合计")
        ws.cell(row=sum_row, column=1).font = Font(bold=True, size=11)
        ws.cell(row=sum_row, column=1).alignment = Alignment(horizontal="center", vertical="center")
        ws.merge_cells(start_row=sum_row, start_column=1, end_row=sum_row, end_column=4)
        for ci, col_letter in zip((5, 6, 7, 8), ("E", "F", "G", "H")):
            ws.cell(row=sum_row, column=ci,
                    value=f"=SUM({col_letter}3:{col_letter}{sum_row - 1})")
            ws.cell(row=sum_row, column=ci).font = Font(bold=True, size=11)
            ws.cell(row=sum_row, column=ci).number_format = '#,##0.00'
            ws.cell(row=sum_row, column=ci).fill = PatternFill("solid", fgColor="F1F5F9")
            _apply_border(ws.cell(row=sum_row, column=ci), Border, Side)
        tip = f"共 {data_rows} 条诊断行动建议；负责人与期限由企业填写。"
    else:
        # 无会话：空表头 + 明确提示
        ws.cell(row=3, column=1, value="（尚未完成诊断；请先在「2. 诊断」与「3. 互动」完成决策后再生成本清单）")
        ws.cell(row=3, column=1).font = Font(italic=True, color="888888")
        ws.merge_cells("A3:J3")
        ws.cell(row=3, column=1).alignment = Alignment(horizontal="left", vertical="center")
        tip = "本表为空表头；完成诊断互动后将自动填入决策数据，不得伪造建议。"

    # 提示行
    tip_row = ws.max_row + 2
    ws.cell(row=tip_row, column=1, value=tip)
    ws.cell(row=tip_row, column=1).font = Font(size=9, italic=True, color="B91C1C")
    ws.merge_cells(start_row=tip_row, start_column=1, end_row=tip_row, end_column=10)
    ws.cell(row=tip_row, column=1).alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)

    # 列宽
    widths = [6, 28, 24, 18, 14, 14, 14, 14, 12, 12]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    ws.freeze_panes = "A3"
    _apply_print_settings(ws, landscape=True, fit_width=1, repeat_rows="1:2")


def _apply_print_settings(ws, landscape: bool = True, fit_width: int = 1,
                          repeat_rows: str = "", margins_cm: float = 1.5) -> None:
    """P1-2：Excel 打印设置（横向 A4 / fitToWidth / 重复表头行 / 合理页边距）。

    使用通用中文字体优先级：PingFang SC（macOS）→ Microsoft YaHei（Windows）→ SimSun（跨平台）。
    """
    from openpyxl.worksheet.page import PageMargins, PrintOptions
    try:
        if landscape:
            ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
        else:
            ws.page_setup.orientation = ws.ORIENTATION_PORTRAIT
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.page_setup.fitToWidth = fit_width
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_margins = PageMargins(
            left=margins_cm / 2.54, right=margins_cm / 2.54,
            top=margins_cm / 2.54, bottom=margins_cm / 2.54,
            header=0.3, footer=0.3,
        )
        if repeat_rows:
            ws.print_title_rows = repeat_rows
        ws.print_options = PrintOptions(horizontalCentered=True)
    except Exception:
        # 打印设置失败不阻塞导出
        pass
