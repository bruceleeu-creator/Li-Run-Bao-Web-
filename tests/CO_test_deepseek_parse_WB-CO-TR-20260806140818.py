"""利润宝 · DeepSeek 扫描件解析测试。

覆盖：上年/期初对比列不应成为独立年份（2022 报告 years=[2022]，不含 2021）。
"""

from __future__ import annotations

import importlib

import pytest

ds = importlib.import_module("core.CO_deepseek_parse_WB-CO-TR-20260806140818")


def test_fill_table_income_ignores_prior_year():
    """利润表「上年」是本年报告的对比参考，不应变成独立年份。"""
    table = ds._fill_table(
        {"income_statement": {"营业收入": {"本年": 222632373.93, "上年": 397746460.76}}},
        "income_statement",
        year=2022,
        year_keys=("本年", "上年"),
    )
    # 只保留报告年份 2022，不把「上年」变成 2021
    assert table["营业收入"] == {2022: 222632373.93}


def test_map_deepseek_result_years_only_report_year():
    """解析 2022 报告：years 只含 2022，不把上年列并入。"""
    content = (
        '{"company_name":"云南艺康","report_year":2022,'
        '"income_statement":{"营业收入":{"本年":222632373.93,"上年":397746460.76},'
        '"净利润":{"本年":6949739.6,"上年":8331572.31}}}'
    )
    data = ds._map_deepseek_result(content, company_name="x", industry="制造业")
    assert data.years == [2022]
    # 营业收入只存 2022 年值；上年列不产生 2021 年
    assert data.income_statement["营业收入"] == {2022: 222632373.93}


def test_balance_sheet_ignores_prior_period():
    """资产负债表「期初」是年初参考，不应变成独立年份。"""
    table = ds._fill_table(
        {"balance_sheet": {"资产总额": {"期末": 186968496.25, "期初": 172000000.0}}},
        "balance_sheet",
        year=2022,
        year_keys=("期末", "期初"),
    )
    assert table["资产总额"] == {2022: 186968496.25}
