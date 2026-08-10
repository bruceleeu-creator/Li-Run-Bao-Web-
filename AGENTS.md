# 利润宝 · 项目记忆（AGENTS.md）

> 更新：2026-08-09 v21 | 行业选择完善（10 行业+说明+AI/规则推荐）+ 诊断/互动 API 真实化 + 全局步骤条与上一步/下一步导航

---

## 项目基线
- 产品：利润宝 · 企业财税优化顾问（Web 端），目标用户为财税/代账机构
- 闭环：导入三年财报 → 行业对标诊断 → 每轮 A/B/C 互动 → 第二稿 → 落地判定 → Word/PDF/Excel 导出
- 唯一事实源：`./`

## 容易跑偏的地方（护栏）
- **行业选择**：`/api/industries` 返回 `{industries: [{name, desc}], names: [...], default}`（names 向后兼容）；`POST /api/industries/recommend` AI 优先（解析失败回退规则关键词），响应 `{industry, reason, source: "ai"|"rule", fallback}`；规则关键词见 `core/industry.py INDUSTRY_RULE_KEYWORDS`（单字「云」已移除，避免「云南」误匹配软件行业）
- **企业识别**：`POST /api/import/identify` AI/规则双路径识别企业名称+行业（入参 `{files: [{name, text}]}`），AI 可用时综合文件名+内容文本识别，失败/未配置回退规则（文件名去年份/后缀提取企业名 + 行业关键词）；前端选文件后自动调用并填入表单，显示「AI 识别/规则识别」来源标识；纯年份文件名不识别为企业名（返回空，留给用户或 AI 内容识别）
- **诊断 API**：`web_backend/CO_diagnosis_WB-CO-TR-20260805160732.py`，`POST /api/diagnosis/run`（规则引擎 + 可选 AI 增强 A/B/C 选项，单条失败回退规则）+ `GET /api/diagnosis`（结果持久化 diagnosis_results 表，绑定 session_version，不匹配返回 null）；重新导入（`session.replace`）会清空诊断与互动表
- **互动 API**：`web_backend/CO_interaction_WB-CO-TR-20260805160732.py`，start/state/decide/confirm 驱动 `core/interactive.py` 状态机；互动会话持久化 interaction_sessions 表；**锁必须用 RLock**（`_require_session`→`_ensure_session` 嵌套获取）；内存会话有效性校验 = 版本匹配 且 DB 记录存在（重新导入相同数据时版本可能不变，仅版本校验会误用旧会话）
- **前端流程导航**：WorkflowRail 全局显示在内容顶部（步骤完成态由 session/诊断/互动真实状态推导）；StepNav 底部「上一步/下一步」仅主流程页显示，带前置校验（未导入/未诊断/未互动时拦截提示）；**回调传参必须 useCallback 稳定**（内联箭头会导致子页 useEffect 无限循环卡死）
- **诊断/互动状态回调**：App 顶层持有 `diagnosisDone/interactionDone/exportUnlocked`，通过 `handleDiagnosisDone`/`handleInteractionChange`（useCallback 稳定引用）下发给子页面；重新导入后一律重置
- **增值税税负率**：必须用 `税金及附加 / 0.12 / 营业收入` 估算口径，UI/报告/Excel 三处都要显著标注「估算值（基于税金及附加反推）」；不要伪装为真实应纳税额
- **合规红线**：所有建议限于合法税务筹划（研发加计扣除、限额内据实扣除、业务模式优化等）；严禁违规筹划表述，守护脚本 `FORBIDDEN_PATTERNS` 已列出违禁词清单，写代码与文档时连同注释都不要出现这些字面
- **离线优先**：AI 引擎仅可选增强，未配置 Base URL/Key/Model 时绝不触网；调用失败必须静默回退规则引擎，不阻塞主流程；`.ai_config.json` 持久化 base_url/model/api_key（用户选择「配一次全局可用、重启免重输」），属本地敏感配置，注意文件权限且勿入库
- **AI 配置保存校验**：Base URL / 模型 / API Key 三字段齐全才允许保存；缺任一项时后端返回 `error` 缺失项说明、前端禁用保存并提示，禁止「保存成功但仍未配置」的静默状态
- **AI max_tokens 护栏**：deepseek-v4-flash 输出上限约 384K；预览整理 ≥16,384、分段提取 ≥16,384、最终报告首试 16,384/重试 32,768、扫描件整份解析 ≥16,384，禁止用 1,200/4,096 这类过小上限导致 `finish_reason=length` 截断。**多文件 AI 整理必须分阶段**：`summarize_for_markdown`/`summarize_years_for_markdown` 均按「文件：」拆分 → 每份独立提炼（`_stage_extract` 8,192）→ 合并跨年对比（`_stage_merge` 16,384）；禁止把多份 PDF 全部文本一次性塞给模型
- **deepseek-v4-flash 必须禁用 thinking**：该模型对复杂任务（财报提炼/JSON 提取）会产生长推理 `reasoning_content`，会**占满 max_tokens 导致正文 content=0 且 finish_reason=length**（实测 4,096 token 全被思考消耗）。`core/ai_engine.py` 默认 `thinking: {"type": "disabled"}`（extra 可覆盖）；`core/CO_deepseek_parse` 同样禁用。诊断/提炼/整理类任务必须保持禁用，否则即使 max_tokens 提到 16,384 也会截断。
- **诊断 GET 契约必须含 years**：`CO_diagnosis.get_diagnosis` 返回的 payload 必须包含 `years`（从 session data 取，诊断表未持久化该字段），前端 `DiagnosisPage` 渲染 `diagnosis.years.join(" / ")`——缺字段会 `Cannot read properties of undefined (reading 'join')` 导致整页白屏。前端对后端返回字段一律用防御式访问（`(x || []).join(...)`）
- **HTTP 401/403 检查须兼容测试 mock**：`requests.post` 返回值用 `getattr(resp, "status_code", None)` 获取，测试 mock（无 status_code 属性）不会被 401 分支误伤
- **架构方向**：`core/*` 不得反向导入 web/上层模块；守护脚本会扫描源码 import 语句拦截
- **Web 端（唯一入口）**：FastAPI 仅绑定 `127.0.0.1:8765`（`web_backend/`），React+Vite 前端（`web_frontend/`）构建产物挂载根路径；领域功能必须复用 `core/` 不得重写；**Tk 桌面端已于 2026-08 移除**（`gui.py`/`main.py`/Tk 测试已删除），Web 为唯一入口
- **Web 模块命名**：文件名含智能体标识连字符，Python 代码内用 `importlib.import_module("web_backend.CO_app_WB-CO-TR-20260805160732")` 加载，禁止用 `from` 语法
- **PDF 预览必须用文本型**：`preview_file` 对 PDF 不再渲染 base64 大图（三份年报会占 ~60MB 响应拖垮浏览器），改为按页采集文本层 + OCR 文字，前端渲染 `<p>`；`images` 恒为空，新增 `pdf_type`（scan/text/mixed）供前端提示「扫描件需配置 AI」；文本层页加 `[第 N 页]` 前缀、OCR 页加 `[第 N 页 OCR]` 前缀；`_OCR_MAX_PAGES=12`
- **PDF OCR 采集**：`_preview_pdf` 用 RapidOCR 对前 12 页 OCR（惰性单例 `_get_ocr_engine`），文本存入 notes；前端 AI 整理对 PDF 优先取 OCR 文本（DeepSeek 读不到图片）；OCR 失败/未安装时静默降级不阻塞；文本层乱码页（子集化字体/生僻符号占比≥25%）必须回退 OCR 并标注「OCR·文本层乱码」，禁止把乱码字形喂给 AI
- **AI 合并报告完整性**：正式报告必须走后台完整逐页读取、分段提取、确定性校验和 `finish_reason` 检查；预览截短文本不得作为正式报告输入
- **扫描件识别与导入**：`_pdf_text_coverage` 按前 5 页文本层覆盖率判定 `pdf_type`；`parse_pdf` 找不到表格时，扫描件（覆盖率 0）报「请先在预览区配置 AI 后重试」，乱码文本报「配置 AI 后重试」；已配置 AI 时扫描件走 `core/CO_deepseek_parse` 整份解析（`_looks_garbled` 识别子集化字形乱码，回退 OCR 后再送模型）
- **三年年报合并**：`account_balances` 已带年份维度 `{科目:{年:值}}`（旧标量形态由 `__post_init__` 兼容）；`core/parser.py` 的 `merge_years(*datas)` 按 科目+年份 合并多份完整年报；`/api/import` 多文件走 merge_years，单文件走 parse_smart
- **文件夹/拖拽导入**：前端「三年文件夹」模式用 `webkitdirectory` input，上传的文件名可能带相对路径前缀，`_save_upload` 必须 `rsplit("/",1)[-1]` 提取纯文件名（防路径穿越 + 避免目录不存在报错）
- **依赖白名单**：禁止 PyQt/Electron/flask/pandoc/WeasyPrint/xlsxwriter/plotly/pyecharts；新增依赖（含 fastapi/uvicorn/httpx2/pdfplumber/pypdfium2）须同步登记并更新 `EXPECTED_MODULES`
- **数据安全**：客户财务数据只在本机处理；未经用户明确授权不得 `git commit`/`git push`/飞书/IMA/外部上传
- **Excel 三 Sheet 强制**：`write_template` 同一工作簿必须包含「费用预算表 / 行业企业所得税贡献率参考 / 诊断与行动清单」三 Sheet；无 session 时第三 Sheet 保留空表头 + 「尚未完成诊断」提示，不得伪造建议
- **原模板防覆盖**：`write_template` 必须比较 `os.path.realpath(plan.source_path)` 与 `os.path.realpath(path)`，同路径直接抛 `TemplateError`
- **模板导入校验**：`read_template` 必须验证 Sheet 名「费用预算表」+ 关键标签 A1/A12/A13/D13/G13/I13 + 84 行结构；A/B/C 列优先读取用户文件维护内容
- **报告章节完整**：Word/PDF 必须包含 7 章：经营目标 / 预算汇总 / 超支异常明细 / 84 行明细 / 诊断与优化建议 / 完整计算口径 / 合规声明；诊断建议表必须含负责人/期限/成本节约/税收节约/税负影响/净影响分栏
- **输入校验**：`validate_plan` 拒绝负金额 / 越界比例（E2/E3 不在 [0,1]）/ 非法税率（E4 不在 {0.05, 0.15, 0.25}）；GUI 在写入 TopInputs 前必须调用

## 智能体标识
- WORKBUDDY → `WB`，CODEX → `CO`，Claude Code → `CC`，TRAE → `TR`
- 新增文件命名后缀：`{文件名}_WB-CO-TR-{日期}.{ext}`
- 样例产物持久化目录：`demo_output/`（不再被忽略，含 `_WB-CO-TR-20260726` 样例）

## 容易踩坑的 Python 环境
- 系统默认 `python3` 为 3.9.6，改用 3.11+；不要假设 `python3.13` 默认可用
- macOS 默认无 `xvfb-run`，Web 后端测试用 TestClient/Playwright 而非 xvfb
- 验收命令统一使用项目 `.venv/bin/python`（正式支持 Python 3.11+），不再用 `python3` 触发系统解释器

## 守护脚本（Project Guardian）
- 路径：`.hooks/project_guardian.py`，Hook：`.hooks/pre-commit`
- 7 维度检查：模块完整性 / Python 语法 / 架构依赖 / 合规红线 / ADR 遵循 / AGENTS.md 时效性 / 导出完整性
- 退出码：0=通过 / 1=警告 / 2=错误（错误阻塞提交，警告不阻塞）
- `find_missing()` 兼容 `.py` 与非 `.py` 文件（如 `requirements.txt`），避免误报
- 修复问题后请同步更新 `EXPECTED_MODULES`，新增模块必须显式登记（如 `tests/test_t7_acceptance.py`）

## 当前实现状态（2026-07-27 v5）
| 模块 | 文件 | 状态 |
|------|------|------|
| 数据模型/同义词 | `core/models.py` | ✅ |
| 行业基准 | `core/industry.py` | ✅ |
| 行业基准 v1.0 对齐 | `core/industry.py` + `core/diagnostic.py` | ✅ WB 四大指标 22 行业参考区间/预警线/预算区间（`INDUSTRY_REFERENCE_DB`），新增所得税/毛利率/净利率判定与 VAT 分级预警，8+4 测试 |
| Excel/CSV 解析 | `core/parser.py` | ✅ |
| 确定性计算 | `core/finance.py` | ✅ |
| 诊断引擎 | `core/diagnostic.py` | ✅ |
| 互动状态机 | `core/interactive.py` | ✅ |
| AI 增强（可选） | `core/ai_engine.py` | ✅ |
| Word/PDF 报告 | `core/report.py` | ✅ T7 P0-4（7 章节 + 11 列分栏）|
| Excel 测算模型 | `core/action_pack.py` | ✅ |
| 模板版预算引擎 | `core/budget.py` + `budget_template.py` | ✅ T7 P0-2/P0-3/P1-1/P1-2/P1-3 |
| T7 验收回归测试 | `tests/test_t7_acceptance.py` | ✅ 29 测试覆盖 P0-2/P0-3/P0-4 + P1-1~P1-3（Tk 用例随桌面端移除）|
| 单元测试 | `tests/test_*.py` | ✅ 115 通过、3 跳过 |
| Web 后端骨架 | `web_backend/` + `tests/CO_test_web_health_*` | 🚧 外壳阶段（health/启动器已验，领域 API 未接）|
| Web 后端导入 | `web_backend/CO_import_*` + `CO_session_*` + `tests/CO_test_web_import_*` | ✅ 导入/示例/会话/行业 API，13 测试 |
| Web 前端外壳 | `web_frontend/`（React+Vite，7 工作区+模拟数据） | 🚧 外壳阶段（E2E 待最终验收）|
| Web 前端导入 | `web_frontend/src/CO_app_*` + `CO_api_*` | ✅ 导入页/总览接真实 API，E2E 2 测试 |
| parser 多格式 | `core/parser.py`（docx/pptx/pdf 表格→网格复用提取；preview_file） | ✅ 5 格式解析+预览，14 扩展测试 |
| Web 导入支持 | `web_backend/CO_import_*` + 前端导入页 | ✅ 5 格式导入 + 文件预览，E2E 3 测试 |
| Web AI 整理 | `web_backend/CO_ai_*` + `CO_ai_route_*` + 前端预览区 | ✅ DeepSeek/OpenAI 兼容 markdown 整理，key 持久化（重启免重输），8 测试 |
| Web 预算计划 | `web_backend/CO_budget_*` + 前端模板工作台 | ✅ 从会话提取营收/成本本年+上年填 TopInputs，2 测试 |
| 预算 AI 识别 | `CO_ai.extract_budget_indicators` + `CO_budget` AI 优先 | ✅ OCR 原文→DeepSeek 识别 4 指标，回退结构化，method 字段，6 测试 |
| 总览 AI 合并 | `CO_ai.summarize_years_for_markdown` + `POST /api/ai/years-summary` | ✅ 多份报告 OCR→跨年对比 markdown，timeout 60s，4 测试 |
| 导入页精简 | `CO_app.tsx` ImportPage | ✅ 拖拽区+文件夹+多文件两按钮，删单文件/分文件模式，7 E2E |
| 导入后隐藏预览 | `runImport` 成功后 `setPreviews(null)` | ✅ 导入成功隐藏文件预览区，只留结果 |
| 导入预览持久化 | `CO_db`/`CO_session`/`CO_import` + 前端导入页 | ✅ 导入时自动把文件预览存 SQLite，`GET /api/import/saved-previews` 恢复，前端显示「预览已保存」入口，切页/刷新后可查看，2 测试 |
| 总览自动 AI 报告 | `OverviewPage` useEffect 首次自动调 years-summary | ✅ 有会话自动生成，按钮改「重新生成」 |
| SQLite 持久化 | `web_backend/CO_db_*` + `CO_session` 写穿 | ✅ 会话/OCR/AI 报告存 app.db，刷新重启恢复，3 测试 |
| 报告记录管理 | `POST /api/ai/years-summary` 自动保存 + `/api/ai/reports` CRUD | ✅ 总览报告记录面板（列表/查看/删除），2 测试 |
| 扫描件 PDF 导入 | `CO_import` 多文件容忍 ParserError + `parser.make_empty_data` | ✅ 扫描件无文本层也能导入，OCR 文本进 session 供 AI |
| 行业选择完善 | `core/industry.py` 10 行业+desc + `POST /api/industries/recommend` | ✅ 说明展示 + AI/规则双路径推荐一键应用，导入 API 3 测试 |
| 诊断 API | `CO_diagnosis`（run/GET/clear）+ `diagnosis_results` 表 | ✅ 规则+AI 增强 A/B/C，绑定 session_version，接口测试 |
| 互动 API | `CO_interaction`（start/state/decide/confirm）+ `interaction_sessions` 表 | ✅ 完整状态机走通，持久化恢复，接口测试 |
| 前端流程导航 | `WorkflowRail` 全局步骤条 + `StepNav` 上一步/下一步 | ✅ 状态驱动完成态 + 前置校验拦截，4 个 e2e |
| 诊断/互动页真实化 | `DiagnosisPage`/`InteractionPage`/`ExportPage` 接真实 API | ✅ 自动诊断、A/B/C 决策、第二稿、确认解锁导出 |

## 验收命令（每次提交前必跑，统一用 `.venv/bin/python`）
```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python data/make_sample.py
.venv/bin/python .hooks/project_guardian.py --quick
bash .hooks/check.sh
```
Web 后端健康验收：
```bash
.venv/bin/python -m pytest tests/CO_test_web_health_WB-CO-TR-20260805160732.py -q
cd web_frontend && npm run build && cd ..
.venv/bin/python .hooks/project_guardian.py --quick
```
Web 导入验收（需 Playwright Chromium）：
```bash
.venv/bin/python -m pytest tests/CO_test_web_health_WB-CO-TR-20260805160732.py tests/CO_test_web_import_WB-CO-TR-20260805160732.py tests/CO_test_web_import_ext_WB-CO-TR-20260805160732.py -q
cd web_frontend && npm run build && npx playwright test && cd ..
```

## 版本规划
- v1.0 MVP：F1-F9 全部核心闭环 + 模板版 T6.1-T6.6 + T7 P0/P1 全量整改 — CO Gate 8 已签字通过
- v1.1：行业基准校准 + 模板自定义
- v2.0：OCR + 多客户 + 直连财务系统

## Agent skills

### Issue tracker

本仓库用本地 Markdown 跟踪工作：issue 以文件形式存放在 `.scratch/<feature>/`。详见 `docs/agents/issue-tracker.md`。

### Triage labels

分诊角色使用默认标签词汇：needs-triage / needs-info / ready-for-agent / ready-for-human / wontfix。详见 `docs/agents/triage-labels.md`。

### Domain docs

单上下文布局：仓库根目录 `CONTEXT.md` + `docs/adr/`。详见 `docs/agents/domain.md`。
