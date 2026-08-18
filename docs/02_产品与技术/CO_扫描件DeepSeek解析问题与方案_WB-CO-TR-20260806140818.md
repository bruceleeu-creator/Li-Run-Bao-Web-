# 扫描件审计报告 DeepSeek 解析 · 问题发现与解决方案

> 状态：已解决（2026-08-06）
> 触发：三份真实扫描件审计报告（云南艺康装饰工程有限公司 2022/2023/2024 年度）导入项目失败/错乱

## 1. 背景

用户提供三份真实扫描件审计报告用于验证：
- `/Users/mac/Desktop/测试文档/2022年审计报告.pdf`（30 页）
- `/Users/mac/Desktop/测试文档/2023年审计报告.pdf`（43 页）
- `/Users/mac/Desktop/测试文档/2024年审计报告.pdf`（49 页）

这三份是扫描件（图片 + 劣质 OCR 文本层），与项目内置的示例数据（虚构的简化数据）完全不同。

## 2. 标准答案（从报告 OCR 提取，跨年交叉验证一致）

| 指标 | 2022 | 2023 | 2024 |
|---|---|---|---|
| 营业收入 | 222,632,373.93 | 372,364,436.57 | 283,347,223.63 |
| 营业成本 | 191,553,497.88 | 296,369,761.31 | 225,605,610.20 |
| 利润总额 | 8,893,277.87 | 24,340,712.36 | 26,385,508.82 |
| 所得税费用 | 89,310.08 | 675,049.48 | 4,967,859.45 |
| 净利润 | 6,949,739.60 | 23,665,662.88 | 21,417,649.37 |

交叉验证：2023 上期 222,632,373.93 = 2022 营收；2024 上年 372,364,436.57 = 2023 营收；
2023 净利 23,665,662.88 与 2024 上年列一致。

## 3. 问题发现

### 3.1 扫描件无法用 pdfplumber 表格提取解析

`core/parser.py::parse_pdf` 依赖 pdfplumber `extract_tables()` 提取网格表格。扫描件 PDF 无网格，导致：
- 2022 勉强解析但错乱（科目错位、年份错标、单位丢失，如营收 22263237393 漏小数点）
- 2023、2024 直接 `ParserError: 未找到可解析的表格`

### 3.2 扫描件 OCR 引擎写了但没接入

`core/CO_financial_scan_*.py`（坐标重建 `rebuild_ocr_rows` / 字段候选 `extract_statement_candidates` / `run_ocr`）
已写好并有测试，但从未被 `parse_pdf` / `CO_import` 调用。

### 3.3 OCR 页数上限截断

`_preview_pdf` 只 OCR 前 10 页（`_OCR_MAX_PAGES=10`），2024 利润表恰在第 10 页起，有截断风险；
且预览 OCR 文本不回传解析层。

## 4. 方案决策

用户明确要求：
1. **完整解析整份 PDF**（不只利润表/资产负债表），交付成果完整，不省成本
2. **DeepSeek 全权解析**，信任其能力，避免本地 OCR 中间层纠错
3. **不设数据安全卡点**，直接让 DeepSeek 处理

技术约束：DeepSeek 文本 API（`deepseek-chat`）**不收图片**（实测 `image_url` 请求报错
`unknown variant image_url`），但能处理文本层。

### 确定方案：PDF 逐页取文本 → DeepSeek 全权解析 → 映射 FinancialData

1. 逐页取 pdfplumber 文本层；文本过短（乱码/图片页）回退 RapidOCR
2. 整份文本带页码标记送 `deepseek-chat`，DeepSeek 全权理解、纠错、输出统一 JSON
3. 后端把 JSON 映射进 `FinancialData`（缺失科目标 0 + 警告）
4. 展示复用确定性引擎 `compute_year_indicators`（与 Tk 基线口径一致）

## 5. 实现

新增 `core/CO_deepseek_parse_WB-CO-TR-20260806140818.py`：
- `extract_pdf_pages_text()`：逐页混合取文本（文本层 → RapidOCR 回退）
- `parse_pdf_with_deepseek()`：整份送 DeepSeek，返回 `FinancialData`
- `_map_deepseek_result()`：JSON → `FinancialData`，年份取文件名优先（修复模型误判年份）
- `_fill_table()`：模型 `{科目:{本年,上年}}` → `{科目:{年:值}}`，上年/期初归为前一年

接入 `web_backend/CO_import`：
- 新增 `_parse_one()`：PDF 且已配置 AI 时走 DeepSeek，否则回退 `parse_smart`
- 多文件导入并发解析（`ThreadPoolExecutor`），缩短三份合并总耗时

`web_backend/CO_ai` 新增 `get_credentials()`：返回含 api_key 的完整凭据（仅供后端内部）。

## 6. 验证结果

单份解析（对照标准答案，全部一致）：
- 2022：营收 222,632,373.93 / 净利 6,949,739.60
- 2023：营收 372,364,436.57 / 净利 23,665,662.88
- 2024：营收 283,347,223.63 / 净利 21,417,649.37（年份修复后 years=[2024]）

三份合并导入（并发）：`years=[2022,2023,2024]`，`matched=30`，营收逐年正确，无警告。

回归：122 测试全过；守护脚本 0 错误（模块已登记 `EXPECTED_MODULES`）。

## 7. 遗留

- 守护脚本对 `core` 内部互引（`from core import parser`）误报「导入上层」——`CO_financial_scan`
  同样存在，非本次引入，建议后续放宽规则
- DeepSeek 解析整份大 PDF 耗时约 60–100 秒/份（完整解析的成本），并发下三份约 100 秒
