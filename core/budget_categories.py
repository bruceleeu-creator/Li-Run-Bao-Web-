"""利润宝 · 模板版费用科目字典（T6.1）。

源自《企业成本费用计划表（模板）》A14:J97 共 84 行明细，保留
「科目名称 → 费用名称 → 发票名称」三级层级。四大科目为：
销售费用 / 管理费用 / 财务费用 / 营业外支出。

- 字典结构稳定可复现，作为预算工作台与导出的统一底稿
- 工会经费（R90）在原模板缺 F/H/J 公式，模板版统一补齐
- 营业外支出 R96/R97 的发票名称原模板被写成 0，此处修正为文本
- 仅作为科目底稿；金额由用户录入或导入，全部默认 0.0
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

# 四大科目（与模板 A 列一致）
SUBJECT_SALES = "销售费用"
SUBJECT_ADMIN = "管理费用"
SUBJECT_FINANCE = "财务费用"
SUBJECT_NON_OPERATING = "营业外支出"

ALL_SUBJECTS: Tuple[str, ...] = (
    SUBJECT_SALES, SUBJECT_ADMIN, SUBJECT_FINANCE, SUBJECT_NON_OPERATING,
)


@dataclass(frozen=True)
class ExpenseCategory:
    """一条费用明细（模板一行）。

    row: 模板行号（14-97），用于与原模板对齐校验
    subject: 科目名称（四大类）
    expense_name: 费用名称（二级）
    invoice_name: 发票名称（三级 / 票面常见名称）
    """
    row: int
    subject: str
    expense_name: str
    invoice_name: str


# 84 行费用字典（行号、科目、费用名称、发票名称）
# 严格按原模板 R14-R97 顺序；科目与费用名称只在每组首行写出，下方继承
_RAW_ROWS: List[Tuple[int, str, str, str]] = [
    # ── 销售费用（R14-R31，18 行）──
    (14, SUBJECT_SALES, "广宣费", "策划服务"),
    (15, SUBJECT_SALES, "广宣费", "推广服务"),
    (16, SUBJECT_SALES, "广宣费", "视频剪辑"),
    (17, SUBJECT_SALES, "广宣费", "项目推广服务"),
    (18, SUBJECT_SALES, "广宣费", "项目宣传"),
    (19, SUBJECT_SALES, "广宣费", "广告设计"),
    (20, SUBJECT_SALES, "广宣费", "广告印刷/制作"),
    (21, SUBJECT_SALES, "运输费", "运输费/快递物流"),
    (22, SUBJECT_SALES, "装卸费", "装卸费"),
    (23, SUBJECT_SALES, "包装费", "包装费"),
    (24, SUBJECT_SALES, "仓储费", "仓储费"),
    (25, SUBJECT_SALES, "销售人员提成及佣金", "销售服务费"),
    (26, SUBJECT_SALES, "市场推广费", "市场调研"),
    (27, SUBJECT_SALES, "市场推广费", "展会参展"),
    (28, SUBJECT_SALES, "市场推广费", "招商会"),
    (29, SUBJECT_SALES, "保险费（运保费）", "保险费"),
    (30, SUBJECT_SALES, "技术服务费", "技术服务费"),
    (31, SUBJECT_SALES, "会务费", "会务费"),
    # ── 管理费用（R32-R91，60 行）──
    (32, SUBJECT_ADMIN, "职工薪酬", "工资、提成"),
    (33, SUBJECT_ADMIN, "职工薪酬", "奖金"),
    (34, SUBJECT_ADMIN, "职工薪酬", "社保"),
    (35, SUBJECT_ADMIN, "职工薪酬", "公积金"),
    (36, SUBJECT_ADMIN, "职工福利费", "住宿"),
    (37, SUBJECT_ADMIN, "职工福利费", "餐费"),
    (38, SUBJECT_ADMIN, "职工福利费", "交通"),
    (39, SUBJECT_ADMIN, "职工福利费", "职工礼品"),
    (40, SUBJECT_ADMIN, "职工福利费", "团队意外伤害保险"),
    (41, SUBJECT_ADMIN, "职工福利费", "职工医药补助"),
    (42, SUBJECT_ADMIN, "职工福利费", "劳保用品"),
    (43, SUBJECT_ADMIN, "职工教育经费", "培训费"),
    (44, SUBJECT_ADMIN, "职工教育经费", "书籍费"),
    (45, SUBJECT_ADMIN, "职工教育经费", "培训住宿"),
    (46, SUBJECT_ADMIN, "职工教育经费", "培训餐饮"),
    (47, SUBJECT_ADMIN, "职工教育经费", "培训交通"),
    (48, SUBJECT_ADMIN, "业务招待费", "餐饮"),
    (49, SUBJECT_ADMIN, "业务招待费", "住宿"),
    (50, SUBJECT_ADMIN, "业务招待费", "香烟、酒水、礼品"),
    (51, SUBJECT_ADMIN, "业务招待费", "旅游"),
    (52, SUBJECT_ADMIN, "业务招待费", "娱乐活动费"),
    (53, SUBJECT_ADMIN, "汽车费用", "汽车油费"),
    (54, SUBJECT_ADMIN, "汽车费用", "停车费"),
    (55, SUBJECT_ADMIN, "汽车费用", "过路费"),
    (56, SUBJECT_ADMIN, "汽车费用", "维修保养费"),
    (57, SUBJECT_ADMIN, "汽车费用", "保险费"),
    (58, SUBJECT_ADMIN, "汽车费用", "ETC费"),
    (59, SUBJECT_ADMIN, "办公费", "交通费"),
    (60, SUBJECT_ADMIN, "办公费", "饮用水费"),
    (61, SUBJECT_ADMIN, "办公费", "宽带、电话费"),
    (62, SUBJECT_ADMIN, "办公费", "快递费、物流费"),
    (63, SUBJECT_ADMIN, "办公费", "办公耗材（电脑配件、A4纸、墨粉盒等）"),
    (64, SUBJECT_ADMIN, "办公费", "办公设备（家具、灯饰、窗帘、电脑、手机、打印机、茶具等）"),
    (65, SUBJECT_ADMIN, "装修费", "设计费"),
    (66, SUBJECT_ADMIN, "装修费", "装饰服务费"),
    (67, SUBJECT_ADMIN, "专利费（冠名）", "专利费"),
    (68, SUBJECT_ADMIN, "审计费", "审计费"),
    (69, SUBJECT_ADMIN, "公正费", "公正费"),
    (70, SUBJECT_ADMIN, "诉讼费", "诉讼费"),
    (71, SUBJECT_ADMIN, "摊销折旧费", "折旧费"),
    (72, SUBJECT_ADMIN, "摊销折旧费", "摊销费"),
    (73, SUBJECT_ADMIN, "摊销折旧费", "长期待摊"),
    (74, SUBJECT_ADMIN, "差旅费", "餐饮费"),
    (75, SUBJECT_ADMIN, "差旅费", "住宿费"),
    (76, SUBJECT_ADMIN, "差旅费", "高铁票"),
    (77, SUBJECT_ADMIN, "差旅费", "机票"),
    (78, SUBJECT_ADMIN, "房屋租赁费", "经营场地租赁费"),
    (79, SUBJECT_ADMIN, "房屋租赁费", "水电费"),
    (80, SUBJECT_ADMIN, "房屋租赁费", "物业管理费"),
    (81, SUBJECT_ADMIN, "中介机构服务费", "法律顾问费"),
    (82, SUBJECT_ADMIN, "中介机构服务费", "财务顾问费"),
    (83, SUBJECT_ADMIN, "中介机构服务费", "技术咨询费"),
    (84, SUBJECT_ADMIN, "中介机构服务费", "项目咨询费"),
    (85, SUBJECT_ADMIN, "中介机构服务费", "人事咨询费"),
    (86, SUBJECT_ADMIN, "外包服务费", "保洁外包"),
    (87, SUBJECT_ADMIN, "外包服务费", "安保外包"),
    (88, SUBJECT_ADMIN, "外包服务费", "绿化绿植外包"),
    (89, SUBJECT_ADMIN, "研发费用", "研发费用"),
    (90, SUBJECT_ADMIN, "工会经费", "工会经费"),
    (91, SUBJECT_ADMIN, "其它", "其它"),
    # ── 财务费用（R92-R95，4 行）──
    (92, SUBJECT_FINANCE, "利息", "利息"),
    (93, SUBJECT_FINANCE, "手续费", "手续费"),
    (94, SUBJECT_FINANCE, "金融费用", "金融费用"),
    (95, SUBJECT_FINANCE, "保理费", "保理费"),
    # ── 营业外支出（R96-R97，2 行）──
    (96, SUBJECT_NON_OPERATING, "违约金", "违约金"),
    (97, SUBJECT_NON_OPERATING, "罚款支出", "罚款支出"),
]


def _build_categories() -> List[ExpenseCategory]:
    return [
        ExpenseCategory(row=r, subject=s, expense_name=e, invoice_name=i)
        for (r, s, e, i) in _RAW_ROWS
    ]


# 完整 84 行字典（模块级常量，确定性输出）
EXPENSE_CATEGORIES: List[ExpenseCategory] = _build_categories()

# 行号 → 字典索引，便于按模板行号快速定位
ROW_TO_INDEX = {c.row: idx for idx, c in enumerate(EXPENSE_CATEGORIES)}

# 模板行号范围（用于校验）
TEMPLATE_FIRST_ROW = 14
TEMPLATE_LAST_ROW = 97
TEMPLATE_TOTAL_ROWS = TEMPLATE_LAST_ROW - TEMPLATE_FIRST_ROW + 1  # 84
TEMPLATE_HEADER_ROW = 13
TEMPLATE_TOTAL_ROW = 98  # 合计行
TEMPLATE_BALANCE_ROW = 100  # G100 未分配余额


def get_categories_by_subject(subject: str) -> List[ExpenseCategory]:
    """按科目名称筛选明细行。"""
    return [c for c in EXPENSE_CATEGORIES if c.subject == subject]


def list_expense_names(subject: str) -> List[str]:
    """列出某科目下的费用名称（去重，保留顺序）。"""
    seen = set()
    names: List[str] = []
    for c in EXPENSE_CATEGORIES:
        if c.subject == subject and c.expense_name not in seen:
            seen.add(c.expense_name)
            names.append(c.expense_name)
    return names


def validate_categories() -> bool:
    """校验字典完整性：行号连续 14-97，共 84 行。"""
    rows = [c.row for c in EXPENSE_CATEGORIES]
    expected = list(range(TEMPLATE_FIRST_ROW, TEMPLATE_LAST_ROW + 1))
    if rows != expected:
        return False
    if len(EXPENSE_CATEGORIES) != TEMPLATE_TOTAL_ROWS:
        return False
    # 每行字段非空
    for c in EXPENSE_CATEGORIES:
        if not c.subject or not c.expense_name or not c.invoice_name:
            return False
    return True
