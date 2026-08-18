"""利润宝 · 数据模型与科目同义词词典。

定义统一财务数据集 FinancialData 及规范科目体系，并提供同义词归并能力，
支持金蝶 / 用友等主流财务软件导出科目的灵活匹配（ADR-005）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# 规范科目（利润表）
INCOME_ACCOUNTS = [
    "营业收入", "营业成本", "税金及附加", "销售费用", "管理费用",
    "研发费用", "财务费用", "利润总额", "所得税费用", "净利润",
]
# 规范科目（资产负债表）
BALANCE_ACCOUNTS = [
    "资产总额", "负债总额", "所有者权益", "应收账款", "存货", "固定资产",
]
# 规范科目（科目余额表）
LEDGER_ACCOUNTS = [
    "福利费", "教育经费", "业务招待费", "广告宣传费", "咨询服务费",
    "研发费用", "折旧",
]

# 科目同义词词典：原始科目名 -> 规范科目名
SYNONYM_MAP: Dict[str, str] = {
    # 营业收入
    "主营业务收入": "营业收入",
    "主营业务销售收入": "营业收入",
    "主营收入": "营业收入",
    "营业收入（主营业务）": "营业收入",
    "营业总收入": "营业收入",
    # 营业成本
    "主营业务成本": "营业成本",
    "产品销售成本": "营业成本",
    "营业成本（主营业务）": "营业成本",
    "营业总成本": "营业成本",
    # 税金及附加
    "营业税金及附加": "税金及附加",
    "税金及附加费": "税金及附加",
    # 销售费用
    "营业费用": "销售费用",
    # 管理费用
    "管理费": "管理费用",
    # 研发费用
    "研究与开发费": "研发费用",
    "研发投入": "研发费用",
    "研究费用": "研发费用",
    # 财务费用
    "财务费": "财务费用",
    # 利润总额
    "税前利润": "利润总额",
    "利润总额（税前）": "利润总额",
    # 所得税费用
    "所得税": "所得税费用",
    "企业所得税": "所得税费用",
    # 净利润
    "税后利润": "净利润",
    "净利润（归母）": "净利润",
    # 资产负债
    "资产总计": "资产总额",
    "总资产": "资产总额",
    "负债合计": "负债总额",
    "总负债": "负债总额",
    "所有者权益合计": "所有者权益",
    "净资产": "所有者权益",
    "股东权益": "所有者权益",
    "应收帐款": "应收账款",
    "固定资产净值": "固定资产",
    # 科目余额表
    "职工福利费": "福利费",
    "职工教育经费": "教育经费",
    "招待费": "业务招待费",
    "广告费和业务宣传费": "广告宣传费",
    "广告费": "广告宣传费",
    "咨询费": "咨询服务费",
    "顾问费": "咨询服务费",
    "累计折旧": "折旧",
}


def normalize_account_name(name) -> str:
    """将原始科目名归并到规范科目名；无法匹配时原样返回（去首尾空格）。

    自动剥离报表常见的编号前缀（如「一、营业收入」「减:营业成本」「(一)资产」），
    使带编号的科目行也能归并到规范科目。
    """
    if name is None:
        return ""
    cleaned = str(name).strip()
    stripped = _strip_ledger_prefix(cleaned)
    return SYNONYM_MAP.get(stripped, stripped)


# 科目编号前缀：一、/二、/（一）/ (一) / 减: / 加: / 其中: / 1、/ 1. 等
_LEDGER_PREFIX_RE = re.compile(
    r"^(?:[一二三四五六七八九十]+[、．.]\s*|\(\s*[一二三四五六七八九十]+\s*\)\s*|"
    r"\([一二三四五六七八九十]+\)\s*|加:|减:|其中:|其中:|"
    r"\d+[、．.]\s*)"
)


def _strip_ledger_prefix(name: str) -> str:
    """剥离科目名开头的中文/数字编号前缀，返回剩余部分。"""
    m = _LEDGER_PREFIX_RE.match(name)
    if m:
        return name[m.end():].strip()
    return name


@dataclass
class FinancialData:
    """统一财务数据集（解析后内存模型）。

    金额单位：元。数值为 float；缺失项以 None 表示。
    account_balances 自 v12 起带年份维度：{科目: {年: 金额}}，
    支持多年审计报告合并后逐年取科目余额。
    """

    company_name: str = ""
    industry: str = "制造业"
    years: List[int] = field(default_factory=list)
    # {规范科目: {年份: 金额}}
    income_statement: Dict[str, Dict[int, Optional[float]]] = field(default_factory=dict)
    balance_sheet: Dict[str, Dict[int, Optional[float]]] = field(default_factory=dict)
    # {规范科目: {年份: 金额}}（v12 起带年份；旧标量形态由 __post_init__ 兼容）
    account_balances: Dict[str, Dict[int, Optional[float]]] = field(default_factory=dict)
    # 解析元数据
    parsed_meta: Dict[str, object] = field(default_factory=dict)

    def __post_init__(self):
        """兼容旧形态：若 account_balances 为 {科目: 标量}，包装成 {科目: {年: 标量}}。

        年份取 latest_year；无年份时用 0 占位（正常解析路径总会带 years）。
        """
        if not self.account_balances:
            return
        sample = next(iter(self.account_balances.values()))
        if not isinstance(sample, dict):
            year = self.latest_year()
            if year is None:
                year = 0
            self.account_balances = {
                acc: {year: val}
                for acc, val in self.account_balances.items()
            }

    def get(self, statement: str, account: str, year: int):
        """安全取值：statement ∈ {income, balance, ledger}。"""
        table = {
            "income": self.income_statement,
            "balance": self.balance_sheet,
            "ledger": self.account_balances,
        }.get(statement)
        if table is None:
            return None
        if statement == "ledger":
            return (table.get(account) or {}).get(year)
        return table.get(account, {}).get(year)

    def latest_year(self):
        return max(self.years) if self.years else None
