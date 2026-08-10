"""利润宝 · 核心引擎包（S1-S9 + T6 模板版）。

包含：数据模型 / 行业基准 / 解析 / 确定性计算 / 诊断 / 互动 / AI 增强 /
报告导出 / 行动包 / 预算领域模型 / 模板读写 / 费用字典。
架构红线：core 不得反向依赖 gui / main。
"""
from . import models
from . import industry
from . import parser
from . import finance
from . import diagnostic
from . import interactive
from . import ai_engine
from . import report
from . import action_pack
from . import budget
from . import budget_categories
from . import budget_template

__all__ = [
    "models", "industry", "parser", "finance",
    "diagnostic", "interactive", "ai_engine",
    "report", "action_pack",
    "budget", "budget_categories", "budget_template",
]
