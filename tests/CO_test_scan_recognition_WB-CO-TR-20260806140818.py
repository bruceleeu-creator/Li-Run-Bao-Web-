"""利润宝扫描财报识别回归测试。

每个断言均覆盖真实故障：相对期间表头不能落到报告年度、扫描表格行列
被打乱，以及跨年度重复数据冲突没有阻止 AI 使用。
"""

from __future__ import annotations

import importlib

from core import parser


def _scan_module():
    return importlib.import_module(
        "core.CO_financial_scan_WB-CO-TR-20260806140818"
    )


def test_period_headers_map_current_and_previous_year():
    """“本年/上年”不能再被当成无年份表头。"""
    years, cols = parser._detect_period_headers(
        ["项目", "本年累计金额", "上年累计金额"], 2022
    )

    assert years == [2022, 2021]
    assert cols == [1, 2]


def test_period_headers_map_balance_closing_and_opening():
    """资产负债表“期末/期初”必须映射为报告年及上一年。"""
    years, cols = parser._detect_period_headers(
        ["项目", "期末余额", "期初余额"], 2024
    )

    assert years == [2024, 2023]
    assert cols == [1, 2]


def test_report_year_prefers_document_text_over_filename():
    """正文年度比可能被误命名的文件名更可靠。"""
    year = parser.detect_report_year(
        "2022年审计报告.pdf",
        "云南艺康装饰工程有限公司 2023年度 利润表",
    )

    assert year == 2023


def test_rebuild_ocr_rows_preserves_amount_columns():
    """坐标重建不能把本年、上年金额串成一列。"""
    scan = _scan_module()
    items = [
        ([[10, 20], [150, 20], [150, 40], [10, 40]], "营业收入", 0.98),
        ([[300, 20], [430, 20], [430, 40], [300, 40]], "372,364,436.57", 0.97),
        ([[500, 20], [630, 20], [630, 40], [500, 40]], "222,632,373.93", 0.96),
    ]

    rows = scan.rebuild_ocr_rows(items)

    assert rows[0]["texts"] == [
        "营业收入",
        "372,364,436.57",
        "222,632,373.93",
    ]
    assert rows[0]["confidences"] == [0.98, 0.97, 0.96]


def test_locate_statement_pages_uses_titles_and_core_accounts():
    """目录或审计正文不能被误判为真正的财务报表页。"""
    scan = _scan_module()
    pages = [
        "目录 利润表 6 资产负债表 4-5",
        "审计意见 我们审计了资产负债表和利润表",
        "资产负债表 项目 期末余额 期初余额 资产总计 负债合计",
        "利润表 项目 本年金额 上年金额 营业收入 营业成本 净利润",
    ]

    located = scan.locate_statement_pages(pages)

    assert located == {"income": [4], "balance": [3]}


def test_extract_income_candidates_maps_current_and_previous_columns():
    """利润表同行两个金额必须分别落到报告年和上一年。"""
    scan = _scan_module()
    rows = [
        {
            "texts": ["项目", "本期金额", "上期金额"],
            "xs": [10.0, 300.0, 500.0],
            "confidences": [0.99, 0.99, 0.99],
        },
        {
            "texts": ["其中：营业收入", "372,364,436.57", "222,632,373.93"],
            "xs": [10.0, 300.0, 500.0],
            "confidences": [0.98, 0.97, 0.96],
        },
    ]

    candidates = scan.extract_statement_candidates(
        rows,
        report_year=2023,
        statement_kind="income",
        source_file="2023年审计报告.pdf",
        source_page=7,
    )

    assert [(c["year"], c["value"]) for c in candidates] == [
        (2023, 372364436.57),
        (2022, 222632373.93),
    ]
    assert all(c["field"] == "营业收入" for c in candidates)
    assert all(c["source_page"] == 7 for c in candidates)


def test_clean_ocr_amount_repairs_only_unambiguous_punctuation():
    """千分位点号可确定修复，含未知字母的金额必须留待核对。"""
    scan = _scan_module()

    assert scan.clean_ocr_amount("372.364,436.57") == 372364436.57
    assert scan.clean_ocr_amount("-89,310.08") == -89310.08
    assert scan.clean_ocr_amount("1A0553,497") is None
