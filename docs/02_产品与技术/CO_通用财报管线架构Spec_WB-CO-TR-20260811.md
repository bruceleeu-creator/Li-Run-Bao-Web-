# 通用财报管线架构 Spec（任意企业 / 任意审计包）

| 项 | 内容 |
|----|------|
| 状态 | **A+B+C+D 已落地**（可迭代打磨） |
| 日期 | 2026-08-11 |
| 触发 | 艺康三年审计修复后，需保证「其他案例同样可操作」，且避免架构继续堆特例 |
| 读者 | 产品 / 实现 / 验收 |
| 相关 | `core/reconciliation.py`、`parser.py`、`CO_import_*`、`gold_yikang.json`、预算/导出链路 |

---

## 1. 背景与问题

### 1.1 已有成果（保留）

- 统一内存模型 `FinancialData`（科目 × 年）
- 导入合并 `merge_years`、会话持久化
- 合规三硬规则 + 费用预算三/四表导出
- 勾稽 / 科目归一 / E3 合成（`reconciliation.enrich_financial_data`）
- E4 默认高新 15%；E3 = max(WB 中枢, 最近有效历史贡献率)
- 艺康金标 `gold_yikang.json`（**回归样例**，不是产品逻辑入口）

### 1.2 当前混乱点（为什么要动架构）

| # | 现象 | 风险 |
|---|------|------|
| A | **案例包硬编码** `audit_3years` + 固定三文件名 + 固定公司名/行业 | 换企业必须改代码或改文件名 |
| B | **导入有 3+ 入口**（`/import`、`/import/sample`、`/import/case/{id}`）逻辑复制粘贴 | 修一处漏一处；enrich 调用不一致 |
| C | **解析路径分裂**：`parser.parse_*` / DeepSeek 整本 / full_pdf_reader / financial_scan 并行 | 同一 PDF 不同入口结果不同 |
| D | **政策与税率散落**：budget / industry / diagnostic / ai_engine / compliance 各自默认 | 艺康修好了，别的链路仍可能 25% |
| E | **会话全局单例** `_data` | 多企业并行、A/B 对比、后台任务易串数据 |
| F | **金标与业务耦合风险** | 若把艺康数字写进代码默认，其他案例失真 |
| G | **文件名 `CO_*_WB-CO-TR-*` 海量** | 发现路径难、importlib 满天飞，不利于分层 |
| H | **data_quality 后端有、前端/导出未统一契约** | 用户看不到「这批数据能不能信」 |

**一句话：当前是「以艺康修好的路径为中心」的功能堆叠，不是「任意案例的管线」。**

---

## 2. 目标与非目标

### 2.1 目标

1. **任意企业、任意份数审计/财报**（1～N 份 PDF/Excel/…）走**同一条管线**。
2. **案例包 = 预置文件清单 + 元数据**，零业务特判；与手动上传字节级等价。
3. **单一真源**：解析 → 归一 → 勾稽 → 政策合成 → 预算/诊断/导出，下游只读 `CaseBundle` / `FinancialData` + `PolicySnapshot`。
4. **可回归**：每个案例可挂可选 `gold.json`；无金标也能跑通，仅置信度更低。
5. **可解释**：每次导入产出稳定的 `data_quality` / 勾稽 / E2·E3·E4 来源，前后端同构。

### 2.2 非目标（本 Spec 不做）

- 多租户 SaaS 账号体系、权限 RBAC
- 完整会计引擎 / 准则自动转换分录
- 重命名全仓库 `CO_*` 文件（可列为 Phase 后置）
- 保证扫描件 OCR 100% 准确（做质量门禁 + 人工改数）

### 2.3 成功标准（验收）

| ID | 标准 |
|----|------|
| S1 | 手动上传「未知企业」2～3 份年报 PDF，不改代码，可完成：导入 → 诊断 → 预算 → 导出 |
| S2 | 新增案例仅需：`cases/<id>/manifest.json` + 文件；可选 `gold.json` |
| S3 | 艺康与「合成假企业」回归均绿；艺康金标不进入 production 默认值 |
| S4 | E4/E3/费用帽策略只在一处配置（Policy），诊断/AI/导出一致 |
| S5 | 导入 API 与导出合规表使用**同一** `PipelineResult` 字段名 |

---

## 3. 领域概念（统一语言）

```text
SourceFile     一份上传/案例文件（path, sha256, name, report_year?, mime）
ExtractResult  单文件抽取结果（raw tables/text/ocr + years + confidence）
FinancialData  规范科目×年（已有）
PolicySnapshot 行业、E2/E3/E4、费用带、增速帽、稳健费用率（只读快照）
DataQuality    置信度、勾稽、异常、是否需确认
CaseBundle     一次「业务案例」：Sources + FinancialData + Policy + Quality + meta
CaseManifest   案例包描述（文件列表、默认公司/行业、金标路径）— 无业务 if 公司名
Session        当前工作区绑定的 CaseBundle（可未来多 session）
```

**原则：**

- **金标（gold）** 只用于测试与对账，不参与运行时默认。
- **案例包（case pack）** 只解决「文件从哪来」，不解决「数怎么算」。
- **政策（policy）** 只解决「预算/合规怎么约束」，不解决「OCR 怎么认字」。

---

## 4. 目标架构

### 4.1 分层（强制单向依赖）

```text
┌─────────────────────────────────────────────────────────────┐
│  Adapter / API（web_backend）                                 │
│  HTTP 上传 · 案例清单 · Session · Job · 响应 DTO               │
└───────────────────────────┬─────────────────────────────────┘
                            │ 只调 Pipeline facade
┌───────────────────────────▼─────────────────────────────────┐
│  Pipeline（新建 core/pipeline/ 或 core/case_pipeline.py）      │
│  run_ingest → run_extract → run_normalize → run_reconcile    │
│  → run_policy → CaseBundle                                   │
└───┬─────────┬──────────┬──────────┬─────────────────────────┘
    │         │          │          │
    ▼         ▼          ▼          ▼
 Extract   Normalize  Reconcile   Policy
 parser/   models/    reconcil.   industry +
 deepseek  subjects   quality     compliance +
 full_pdf                         budget defaults
    │
    └──────────────► FinancialData（真源事实）
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼
      Diagnostic         Budget            Export
      interactive        advice/export     template/report
```

**禁止：**

- `budget_template` 直接读艺康路径或硬编码公司
- `import/case` 另写一套金额逻辑
- `diagnostic` 私自 `cit_rate=0.25`（必须读 PolicySnapshot）

### 4.2 核心流水线（任意案例同一条）

```mermaid
flowchart LR
  subgraph Ingest
    U[Upload / CaseManifest]
    S[SourceFile[]]
  end
  subgraph Extract
    E1[按扩展名路由]
    E2[文本层 / OCR / LLM]
    E3[ExtractResult per file]
  end
  subgraph Unify
    N[科目归一 + 年份对齐]
    M[merge_years]
    R[勾稽 + 异常]
    Q[DataQuality]
  end
  subgraph Policy
    P[行业解析]
    T[E2/E3/E4 合成]
    F[费用带/增速帽/稳健比率]
  end
  U --> S --> E1 --> E2 --> E3 --> N --> M --> R --> Q --> P --> T --> F
  F --> B[CaseBundle]
  B --> D[诊断/预算/导出]
```

### 4.3 单一门面 API（建议）

```python
# 伪代码 — Spec 契约，非最终实现签名

@dataclass
class PipelineOptions:
    company_name: str = ""          # 可空：从 PDF 抽取
    industry: str = ""              # 可空：规则/AI 推荐
    prefer_llm_parse: bool = True
    require_confirm_on_low_confidence: bool = True

@dataclass
class CaseBundle:
    financial_data: FinancialData
    sources: list[SourceFile]
    policy: PolicySnapshot
    quality: DataQuality
    ocr_texts: list[str]
    meta: dict                      # accounting_standard, parse_notes, ...

def run_case_pipeline(
    paths: list[Path],
    options: PipelineOptions | None = None,
) -> CaseBundle:
    """唯一入口：上传与案例包最终都调用这里。"""
    ...
```

**Web 层只做：**

```text
/import              → 存文件 → run_case_pipeline → session.set(bundle)
/import/case/{id}    → resolve_manifest → 同上
/import/sample       → 内置 sample 路径 → 同上
/budget/from-session → session.bundle → BudgetPlan（读 policy，不重算行业中枢逻辑散落）
/export              → session.bundle + plan → write_template
```

### 4.4 CaseManifest（案例包通用化）

路径建议：`demo_output/cases/<case_id>/manifest.json`

```json
{
  "id": "audit_yikang_3y",
  "label": "艺康三年审计（演示）",
  "description": "仅演示；与手动上传等价",
  "company_name": "云南艺康装饰工程有限公司",
  "industry": "建筑业",
  "files": [
    { "path": "2022年审计报告.pdf", "report_year": 2022 },
    { "path": "2023年审计报告.pdf", "report_year": 2023 },
    { "path": "2024年审计报告.pdf", "report_year": 2024 }
  ],
  "defaults": {
    "income_tax_nominal_rate": 0.15,
    "notes": "高新附注见 2022 报告"
  },
  "gold": "gold.json",
  "tags": ["demo", "construction", "scan+text"]
}
```

**规则：**

- `files[].path` 相对 `cases/<id>/`，也可绝对/外部目录（配置允许列表）
- **禁止** 在 Python 里 `if case_id == "audit_3years"`
- 新案例：复制目录 + 改 manifest，无需改业务代码
- `gold` 可选；有则 CI 跑 diff，无则跳过

### 4.5 PolicySnapshot（政策单点）

```python
@dataclass
class PolicySnapshot:
    industry_key: str
    e2_industry_contribution: float     # WB hub
    e3_company_contribution: float      # max(hub, latest_valid_cit)
    e3_basis: str                       # "hub_only" | "latest_valid" | ...
    e4_income_tax_rate: float           # 0.05 | 0.15 | 0.25
    e4_source: str                      # "default_hnte" | "note_extract" | "user"
    fee_band: dict                      # min/median/max
    fee_growth_cap: float
    fee_growth_mode: str                # raw_plus_buffer | winsor_band_priority
    robust_subject_ratios: dict        # selling/admin/rd/finance
    near_zero_selling: bool
    hard_rules: list[str]               # 引用 compliance HARD_RULES
```

**合成顺序（固定，写进实现与测试）：**

1. 行业 resolve（名 / 经营范围 / 用户输入）
2. E2 ← WB 中枢
3. E3 ← max(E2, 历史有效贡献率)（负税年剔除）
4. E4 ← 附注抽取 > 用户已设 > 默认 15%（可配置 DEFAULT）
5. 费用带 / 增速帽 / 稳健比率 ← compliance + reconciliation
6. 冻结为 PolicySnapshot 写入 bundle（预算顶栏从此快照灌入）

### 4.6 DataQuality（统一契约）

前后端、导出共用字段（已有雏形，Spec 定名）：

```json
{
  "confidence": "high|medium|low",
  "text_layer": true,
  "ocr_used": true,
  "matched_cells": 28,
  "require_confirm": false,
  "export_blocked": false,
  "reconciliation": {
    "ok": true,
    "hard_fail": false,
    "errors": [],
    "warnings": []
  },
  "accounting_standard": { "hint": "企业会计准则" },
  "expense_anomalies": [],
  "cit_synthesis": {},
  "parse_notes": []
}
```

**产品策略（建议默认，可配置）：**

| 条件 | 行为 |
|------|------|
| `hard_fail` | 允许进入会话，**导出默认需确认**（`require_confirm=true`），合规表标红 |
| `confidence=low` | 同上；UI 黄条提示 |
| 正常 | 无阻断 |

不因单案例「严」到别的案例导入失败。

---

## 5. 模块职责边界

| 模块 | 职责 | 不负责 |
|------|------|--------|
| `parser` / extractors | 字节 → 原始表/文本/年 | 预算税率、合规红线 |
| `reconciliation` | 归一、勾稽、异常、CIT 序列 | HTTP、Excel 样式 |
| `industry` + `compliance_policy` | 行业带、硬规则、增速帽 | 读 PDF |
| **`pipeline`（新）** | 编排 + CaseBundle | UI 文案细节 |
| `budget` / export / advice | 读 bundle 出计划/表 | 再解析 PDF |
| `diagnostic` / `ai_engine` | 读 PolicySnapshot 的 E4 | 私自默认 25% |
| `web_backend` import | 文件 I/O、session、DTO | 复制 enrich 逻辑 |
| `cases/*` | 文件 + manifest + 可选 gold | Python 业务 |

---

## 6. 与现状映射（迁移怎么切）

### 6.1 保留并收敛

| 现状 | 迁入 |
|------|------|
| `enrich_financial_data` | pipeline 的 normalize+reconcile 阶段（可保留函数，由 facade 调用一次） |
| `merge_years` | pipeline unify |
| `apply_wb_top_rates` + `apply_historical_contribution` | pipeline policy 阶段 → PolicySnapshot |
| `HARD_RULES_*` | 不变，Policy 引用 |
| `gold_yikang.json` | `cases/audit_yikang_3y/gold.json` |

### 6.2 删除/收缩的特例

| 现状 | 处理 |
|------|------|
| `if case_id != "audit_3years"` | → manifest 注册表 `list_cases()` |
| case 内写死 company/industry | → manifest 字段；可被上传表单覆盖 |
| import / case 两套 response 拼装 | → `bundle_to_import_response(bundle)` |
| from-session 再算一遍 WB | → 优先用 `session.policy`；无则 pipeline 重放 policy-only |

### 6.3 Session 演进（可选两档）

**V1（最小，推荐先做）：** 仍单会话，但存完整 `CaseBundle` JSON（含 policy + quality），不要只存 `FinancialData`。

**V2（后续）：** `session_id` 多会话；导出 job 绑定 session_id。

本 Spec **要求 V1**；V2 仅预留字段。

---

## 7. API / 前端契约变更（摘要）

### 7.1 导入响应（统一）

```json
{
  "summary": {},
  "years": [2022, 2023, 2024],
  "indicators": [],
  "previews": [],
  "sources": [],
  "data_quality": {},
  "policy": {
    "industry_key": "建筑业",
    "e2": 0.015,
    "e3": 0.0175,
    "e4": 0.15,
    "e3_basis": "latest_valid",
    "e4_source": "default_hnte"
  },
  "case_id": null
}
```

`case_id` 仅案例入口有值；手动上传为 `null`。

### 7.2 案例列表

`GET /import/cases` → 扫描 `cases/*/manifest.json`，不再写死一个 case。

### 7.3 前端（后续迭代）

- 导入结果区固定展示：置信度、勾稽、E3/E4 来源
- `require_confirm` 时导出前 Checkbox「已人工核验关键金额」
- 案例选择器：动态列表，不要写死「三年审计」按钮文案逻辑

---

## 8. 测试策略

| 层 | 内容 |
|----|------|
| 单元 | 归一、R2、CIT 剔除负税、E3 max、winsor 增速 |
| 金标 | 每个有 `gold.json` 的 case：关键科目容差 ≤ 1 元 |
| 合成案例 | 最小文本 PDF/Excel 两公司两行业，无艺康路径 |
| 契约 | import / case / sample 三者 response 键集合一致 |
| 回归 | 现有 budget/export/t7 全绿 |

**原则：艺康是回归集的一员，不是唯一路径。**

---

## 9. 分阶段实施（评审通过后执行）

### Phase A — 管线门面 + 统一入口（高收益 / 低风险）

1. 新增 `core/pipeline.py`（或包）：`run_case_pipeline`
2. `/import`、`/import/case`、`/import/sample` 全部只调门面
3. Session 持久化 `policy` + `data_quality`
4. 契约测试：三入口 response schema 一致

**交付：** 行为与现网基本一致，结构不再分叉。

### Phase B — CaseManifest 通用案例包

1. `manifest.json` 规范 + 扫描器
2. 迁移艺康为第一个 manifest
3. 文档：如何新增案例（复制目录）
4. 删除硬编码 `audit_3years` 分支

**交付：** 第二家企业案例可零代码接入（放文件即可）。

### Phase C — Policy 单点 + 诊断/AI 收口

1. `PolicySnapshot` 数据类
2. diagnostic / ai_engine 税率只读 policy
3. from-session / export 顶栏只灌 policy
4. 合规表第五节改读 policy（已有雏形）

**交付：** 任意案例税率/贡献率行为一致可测。

### Phase D — 质量 UX + 可选多 session

1. 前端质量条 + 导出确认
2. （可选）session_id
3. （可选）模块更名减 importlib 噪音

---

## 10. 风险与决策点（请拍板）

| # | 决策 | 选项 | 建议 |
|---|------|------|------|
| D1 | 默认 E4 | 一直 15% / 按行业 / 按附注优先 | **附注 > 用户 > 默认 15%** |
| D2 | E3 | max(中枢,最近年) / max(中枢,中位) | **维持 max(中枢,最近有效年)** |
| D3 | 勾稽失败 | 硬阻断导出 / 确认后导出 | **确认后导出（顾问场景）** |
| D4 | 案例文件位置 | 仅 `demo_output/cases` / 允许多 search path | **search path 列表（已有测试文件目录）** |
| D5 | Phase A～D 是否一次做完 | 全做 / 只 A+B | **先 A+B，C 紧随；D UX 可并行** |

---

## 11. 明确不做什么（防再次混乱）

1. **不为新客户写 `if company == "xxx"`**
2. **不把金标金额写进 `DEFAULT_*`**
3. **不在 export 阶段重新 OCR**
4. **不在 advice 提示词里复制一套 E3 公式**
5. **不增加第四条导入入口**而不走 pipeline

---

## 12. 评审清单（你看完可直接勾）

- [ ] 同意「案例包 = manifest + 文件，无业务特判」
- [ ] 同意「唯一 `run_case_pipeline` 门面」
- [ ] 同意 PolicySnapshot 单点（E2/E3/E4/费用帽）
- [ ] 同意 D1～D5 建议值，或给出修改
- [ ] 实施顺序：A → B → C → D，或调整
- [ ] 是否需要在 Spec 通过后立刻开工 Phase A

---

## 13. 附录：目标目录草图（Phase A/B 后）

```text
core/
  models.py              # FinancialData（保留）
  pipeline.py            # NEW: run_case_pipeline, CaseBundle, PolicySnapshot
  reconciliation.py      # 归一/勾稽/质量（保留，被 pipeline 调用）
  parser.py              # 抽取（逐步只暴露 extract_one）
  industry.py
  compliance_policy.py
  budget.py
  budget_template.py
  ...
demo_output/cases/
  audit_yikang_3y/
    manifest.json
    gold.json
    2022….pdf …
  <any_new_case>/
    manifest.json
    *.pdf
web_backend/
  CO_import_*.py         # 变薄：I/O + pipeline + session
```

---

**文档结束。** 请按 §10 / §12 反馈；确认后按 Phase A 开工，不再先堆艺康特例。
