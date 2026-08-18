# 利润宝 · 项目记忆（AGENTS.md）

> 更新：2026-08-18 v25 | 经营分析报告链路（导出页两段式：①DeepSeek 前世今生分析→Word/PDF 同源导出 + 分割线 + ②费用编制建议→测算模型/预算三表；数字白名单只提示不改数）；修复「AI 整理后未进导入记录」（onSummarize 在 selectedFiles 为空〔如「已保存预览」〕时静默跳过自动导入 → 改为明确警告 + 按钮文案如实 + 选新文件重置状态）；UI 名称统一「经营分析报告」，README 交付物/闭环/3.6 导出交付小节同步重写；v24 数字质检引擎（core/numeric_audit 双层防护）+ 导入记录/报告记录完整案例载入 + AI 整理即导入；v23 工作区重构（合并页/五工作区）；v22 整合全部文档进本文件（唯一文档真源）

---

## 项目基线
- 产品：利润宝 · 企业财税优化顾问（Web 端），目标用户为财税/代账机构
- 闭环：导入三年财报 → 行业对标诊断 → 每轮 A/B/C 互动 → 第二稿 → 落地判定 → Word/PDF/Excel 导出
- 唯一事实源：`./`
- 数据口径：金额单位默认元；增值税税负率为估算值；小微/高新优惠判定为简化规则，正式申报以税务口径为准

## 版本与发布状态
- 当前版本：**v1.2.0**（2026-08-09 诊断闭环）；v1.0.0 MVP（2026-07-26，CO T8 Gate 8 签字通过）
- 适用平台：macOS 12+ / Python 3.11+（**注意：rapidocr_onnxruntime 需 Python <3.13，推荐 3.11/3.12**）
- 分发方式：GitHub 私有仓库（不开源，无 LICENSE）
- Tk 桌面端已于 2026-08 移除（`gui.py`/`main.py`/Tk 测试删除），**Web 为唯一入口**

## 版本历史

### v1.3.0 - 2026-08-18 · 工作区重构 + 完整案例载入 + 数字质检
- **工作区重构**：总览+财报导入合并为「导入财报」单页（主列：经营概况→数字质检→导入区→AI 合并报告；右栏：导入记录卡片+报告记录）；模板工作台(budget)前端移除（预算功能保留在导出页）；「一键补全互动并解锁」「载入示例数据」按钮删除；流程条精简为编号圆点
- **导入历史（import_history 表）**：每次导入自动存档（上限 50、内容指纹去重）；点击卡片**完整载入案例**——财务数据+诊断结果+互动进度（决策重放）+导出解锁态+已生成 AI 报告一并恢复（`restored` 契约）；报告记录点击按 `session_version` 反查同口径载入
- **AI 整理并导入**：`onSummarize` 成功即自动 `runImport`（整理结果自动存报告库 kind=summarize）
- **数字质检引擎 `core/numeric_audit.py`**：双层防护（OCR 原文字面 + 数字层恒等式/错位归因），高风险发现强制导出前人工核验；详见「技术架构要点」专节
- **数据反序列化修复**：新增 `session.from_persisted_dict`（忠实还原 parsed_meta 与年份结构），历史载入与重启恢复共用；解锁判定对齐 core（CONFIRMATION 且 已确认或落地性≥95%）
- **e2e 夹具重建**：workspaces 三年 xlsx + 样例 docx/pdf（make_sample 数据 + STSong-Light），三格式解析验证通过
- 测试：+11 数字质检 +3 历史载入 +1 报告点击载入；e2e 34 全绿

### v1.2.0 - 2026-08-09 · 诊断闭环
- **行业选择**：`core/industry.py` 10 行业（各带 `desc`）+ `POST /api/industries/recommend`（AI 优先，失败回退规则关键词）
- **诊断 API**：`web_backend/CO_diagnosis_*`，`POST /api/diagnosis/run`（规则 + 可选 AI 增强 A/B/C）+ `GET /api/diagnosis`（持久化 `diagnosis_results` 表，绑定 `session_version`）+ clear；重新导入自动清空
- **互动 API**：`web_backend/CO_interaction_*`，start/state/decide/confirm 驱动状态机（FINDING_LOOP → DRAFT2 → CONFIRMATION → FINAL），持久化 `interaction_sessions` 表；严格不跳项
- **前端**：`WorkflowRail` 全局步骤条 + `StepNav` 上一步/下一步（前置校验拦截）；诊断/互动/导出页接真实 API
- **修复**：互动锁改 RLock（嵌套获取死锁）；重导相同数据 session_version 不变时增加 DB 记录存在性校验；前端回调改 `useCallback` 稳定引用（内联箭头导致无限重渲染卡死）
- 测试：12 接口测试 + 4 e2e

### v1.0.0 - 2026-07-26 · 首个可交付 MVP
- **核心引擎 F1-F9**：models（数据模型+同义词词典）/ industry（行业基准）/ parser（Excel/CSV 三表解析）/ finance（确定性计算）/ diagnostic（初稿建议）/ interactive（多轮交互）/ report（Word/PDF 7 章节）/ action_pack（Excel 测算模型）/ ai_engine（可选大模型）
- **模板版预算引擎 T6**：84 行三 Sheet（费用预算表 / 行业参考 / 诊断与行动清单）；`read_template` 校验 Sheet 名+关键标签 A1/A12/A13/D13/G13/I13+84 行；原模板防覆盖（`os.path.realpath` 同路径抛 `TemplateError`）；A/B/C 列优先读用户文件
- **质量门禁**：`.hooks/project_guardian.py` 7 维度 + `check.sh` + pre-commit；退出码 0/1（警告不阻塞）/2（错误阻塞）
- **macOS 封装 T10**：4 个双击脚本（安装/启动 Web/环境检查/质量检查）+ 构建发布包脚本（release/ + ZIP + SHA-256 + 敏感字符串扫描，v1.0.0 包 56 文件、敏感扫描 0 命中）
- 验收：全量 129 通过/2 跳过；T7 P0/P1 全量整改；CO T8 Gate 8 签字

### 规划中
- v1.1：行业基准校准（按机构客户样本）+ 模板自定义（非 84 行固定结构）
- v2.0：多客户组合管理、直连金蝶/用友 API 取数、大模型深度润色（原规划的 OCR 已提前落地）

## 私有仓库与合规声明
- **分发**：GitHub 私有仓库，不开源、不附加 LICENSE；未经项目所有者书面授权不得复制到公开仓库、二次分发、转授权、商用转售、移除本声明
- **授权**：仅项目所有者指定的内部财税/代账同事；接收方可克隆到本机使用，不得转授他人
- **数据安全**：客户财务数据只在本机处理；AI 为可选增强，未配置时规则引擎兜底、离线闭环；`.ai_config.json` 已 gitignore（API Key 仅内存持有）；不收集统计、不回传信息
- **合规**：所有建议限于合法税务筹划（研发加计扣除、限额内据实扣除、业务模式优化等）；严禁任何形式的违规筹划表述；增值税税负率为估算值须四处显著标注（UI/Word/PDF/Excel）；执行前建议与主管税务机关或注册税务师确认；作者不对使用损失担责
- **版权**：© 2026 利润宝项目所有者，All Rights Reserved；违规公开/转售/违规筹划可撤销权限、要求删除、追责

## 容易跑偏的地方（护栏）
- **工作区结构（2026-08-18 重构后）**：五工作区 `导入财报(overview) | 诊断 | 互动 | 第二稿与导出 | 设置`。原「总览」与「财报导入」已合并为「导入财报」单页（主列：经营概况→导入区→AI 合并报告；右栏：导入记录卡片+报告记录）；**模板工作台(budget)前端工作区已移除**（预算建议/三表导出仍在导出页）；**「载入示例数据」按钮已移除**（同日，用户要求；e2e 改走 `POST /api/import/sample` API + 重新加载，竞态类测试改 mock `/api/import` 走真实上传路径）；「一键补全互动并解锁」按钮全部删除（解锁走互动页正常流程）；流程条精简为纯编号圆点（文字在 title/aria）
- **报告记录点击 = 预览 + 载入对应案例（2026-08-18）**：`list_reports` 返回 `session_version`；前端点击报告先用它反查导入记录（`history.find(h => h.session_version === version)`），命中则走 `onLoadHistory` 完整载入（财务/诊断/互动/解锁态），未命中（对应卡片已删）仅预览并提示"导入记录不存在"；两条路径都先展示报告详情（`selectedReport`）
- **AI 整理 = 整理并自动导入（2026-08-18）**：`onSummarize` 成功后自动 `runImport(selectedFiles)`——写入会话 + 生成导入记录卡片；整理结果由后端 `/api/ai/summarize` 自动存报告库（kind=summarize，前端显示「财报整理」）；按钮文案「AI 整理并导入」；整理失败不触发导入
- **导入历史卡片 = 完整案例载入（2026-08-18 打通）**：每次导入成功自动存档（`import_history` 表，上限 50 条，内容指纹去重——`parsed_meta` 的 `excel_path` 等工作区路径字段参与指纹前剥离，`session_version` 含随机路径**不能**用作去重键）。**点击卡片不只恢复财务数据**：同时恢复该案例的诊断结果、互动进度（含决策重放）、导出解锁态，并按 `report_id` 带回已生成的 AI 合并报告（前端直接展示不重算）——诊断/互动每次保存经 `db.snapshot_case_progress()` 同步快照到激活行（`is_active` 标记），`GET /api/diagnosis`、`GET /api/interaction/state` 载入后即有内容
- **历史数据反序列化必须用 `session.from_persisted_dict`**：`parser.parse_financial_dict` 会丢 `parsed_meta`（policy/data_quality/reconciliation）并破坏 `account_balances` 年份结构，导致版本指纹漂移、报告关联失效；`restore_from_db`（重启恢复）同此口径。**解锁判定**须与 core.interactive 一致：`FINAL 或 CONFIRMATION 且（user_confirmed 或 落地性 ≥ FEASIBILITY_THRESHOLD）`——只查 user_confirmed 会漏"全选 A 自动解锁"的案例
- **进度快照存解析后载荷**：`snapshot_case_progress` 存 `findings`/`decisions` 列表形态（非 `*_json` 原始行），与 load 路由恢复读取口径一致
- **载入/删除 API**：`GET /api/import/history` 列表 / `POST /api/import/history/{id}/load` 载入（响应 = /import 契约 + `restored{diagnosis_done,interaction_done,export_unlocked,report_id}`）/ `DELETE /api/import/history/{id}`；载入不重复存档、不自动触发 AI 报告重算（前端 `onImported(resp, {autoReport:false, restored})`）
- **合并页防重复渲染**：AI 报告正文/进度条/错误提示**只在「AI 合并报告」面板渲染一次**，导入区的「AI 总结预览」只放状态指引（进行中/失败/完成指向下方），否则同页双份触发 Playwright 严格模式冲突
- **行业选择**：`/api/industries` 返回 `{industries: [{name, desc}], names: [...], default}`（names 向后兼容）；`POST /api/industries/recommend` AI 优先（解析失败回退规则），响应 `{industry, reason, source: "ai"|"rule", fallback}`；规则关键词见 `core/industry.py INDUSTRY_RULE_KEYWORDS`（单字「云」已移除，避免「云南」误匹配软件行业）
- **企业识别**：`POST /api/import/identify` AI/规则双路径识别企业名称+行业（入参 `{files: [{name, text}]}`）；前端选文件后自动调用并显示「AI 识别/规则识别」来源；纯年份文件名不识别为企业名
- **诊断 API**：`POST /api/diagnosis/run`（规则引擎 + 可选 AI 增强 A/B/C，单条失败回退规则）+ `GET /api/diagnosis`（结果持久化 diagnosis_results 表，绑定 session_version，不匹配返回 null）；重新导入（`session.replace`）会清空诊断与互动表
- **互动 API**：start/state/decide/confirm 驱动 `core/interactive.py` 状态机；**锁必须用 RLock**（`_require_session`→`_ensure_session` 嵌套获取）；内存会话有效性校验 = 版本匹配 且 DB 记录存在
- **前端流程导航**：WorkflowRail 全局顶部显示（步骤完成态由 session/诊断/互动真实状态推导）；StepNav 底部仅主流程页显示，带前置校验；**回调传参必须 useCallback 稳定**（内联箭头会导致子页 useEffect 无限循环卡死）
- **诊断/互动状态回调**：App 顶层持有 `diagnosisDone/interactionDone/exportUnlocked`，通过 useCallback 稳定引用下发；重新导入后一律重置
- **增值税税负率**：必须用 `税金及附加 / 0.12 / 营业收入` 估算口径，UI/报告/Excel 三处都要显著标注「估算值（基于税金及附加反推）」；不要伪装为真实应纳税额
- **合规红线**：所有建议限于合法税务筹划；守护脚本 `FORBIDDEN_PATTERNS` 已列违禁词清单，写代码与文档时连同注释都不要出现这些字面
- **离线优先**：AI 引擎仅可选增强，未配置 Base URL/Key/Model 时绝不触网；调用失败必须静默回退规则引擎；`.ai_config.json` 属本地敏感配置，注意文件权限且勿入库
- **AI 配置保存校验**：Base URL / 模型 / API Key 三字段齐全才允许保存；禁止「保存成功但仍未配置」的静默状态
- **AI max_tokens 护栏**：deepseek-v4-flash 输出上限约 384K；预览整理 ≥16,384、分段提取 ≥16,384、最终报告首试 16,384/重试 32,768、扫描件整份解析 ≥16,384；禁止 1,200/4,096 这类过小上限导致 `finish_reason=length` 截断。**多文件 AI 整理必须分阶段**（`_stage_extract` 8,192 → `_stage_merge` 16,384）；禁止把多份 PDF 全部文本一次性塞给模型
- **deepseek-v4-flash 必须禁用 thinking**：长推理 `reasoning_content` 会占满 max_tokens 导致正文 content=0 且 finish_reason=length；`core/ai_engine.py` 默认 `thinking: {"type": "disabled"}`；诊断/提炼/整理类任务必须保持禁用
- **诊断 GET 契约必须含 years**：payload 必须包含 `years`（从 session data 取）；前端 `DiagnosisPage` 渲染 `diagnosis.years.join(" / ")`——缺字段整页白屏。前端对后端返回字段一律防御式访问（`(x || []).join(...)`）
- **HTTP 401/403 检查须兼容测试 mock**：`requests.post` 返回值用 `getattr(resp, "status_code", None)` 获取
- **架构方向**：`core/*` 不得反向导入 web/上层模块；守护脚本会扫描 import 拦截
- **Web 端（唯一入口）**：FastAPI 仅绑定 `127.0.0.1:8765`（`web_backend/`），React+Vite 前端（`web_frontend/`）构建产物挂载根路径；领域功能必须复用 `core/` 不得重写；前端无任意文件系统权限，SQLite 与工作区路径由后端统一管理
- **Web 模块命名**：文件名含智能体标识连字符，Python 代码内用 `importlib.import_module("web_backend.CO_app_WB-CO-TR-20260805160732")` 加载，禁止用 `from` 语法
- **PDF 预览必须用文本型**：`preview_file` 对 PDF 按页采集文本层 + OCR 文字（前 12 页，`_OCR_MAX_PAGES=12`），不渲染 base64 大图；文本层页加 `[第 N 页]` 前缀、OCR 页加 `[第 N 页 OCR]` 前缀；新增 `pdf_type`（scan/text/mixed）
- **PDF OCR 采集**：RapidOCR 惰性单例 `_get_ocr_engine`；OCR 失败/未安装静默降级；文本层乱码页（子集化字体/生僻符号占比≥25%）必须回退 OCR 并标注「OCR·文本层乱码」，禁止把乱码字形喂给 AI
- **AI 合并报告完整性**：正式报告必须走后台完整逐页读取、分段提取、确定性校验和 `finish_reason` 检查；预览截短文本（前 20 页/每页 600 字符）不得作为正式报告输入
- **扫描件识别与导入**：`_pdf_text_coverage` 按前 5 页文本层覆盖率判定 `pdf_type`；扫描件（覆盖率 0）未配 AI 时报「请先配置 AI 后重试」；已配置 AI 时走 `core/CO_deepseek_parse` 整份解析（`_looks_garbled` 识别乱码回退 OCR 后再送模型）
- **三年年报合并**：`account_balances` 带 `{科目:{年:值}}`（旧标量形态 `__post_init__` 兼容）；`core/parser.py merge_years(*datas)` 按 科目+年份 合并；`/api/import` 多文件走 merge_years，单文件走 parse_smart
- **文件夹/拖拽导入**：`webkitdirectory` 上传文件名可能带相对路径前缀，`_save_upload` 必须 `rsplit("/",1)[-1]` 提取纯文件名（防路径穿越）
- **依赖白名单**：禁止 PyQt/Electron/flask/pandoc/WeasyPrint/xlsxwriter/plotly/pyecharts；新增依赖须同步登记并更新 `EXPECTED_MODULES`
- **数据安全**：客户财务数据只在本机处理；未经用户明确授权不得 `git commit`/`git push`/外部上传
- **Excel 三 Sheet 强制**：`write_template` 同一工作簿必须含「费用预算表 / 行业企业所得税贡献率参考 / 诊断与行动清单」；无 session 时第三 Sheet 保留空表头 + 提示，不得伪造建议
- **原模板防覆盖**：`write_template` 比较 `os.path.realpath(plan.source_path)` 与目标 `path`，同路径抛 `TemplateError`
- **模板导入校验**：`read_template` 验证 Sheet 名 + 关键标签 + 84 行结构；A/B/C 列优先读取用户文件维护内容
- **报告章节完整**：Word/PDF 必含 7 章：经营目标 / 预算汇总 / 超支异常明细 / 84 行明细 / 诊断与优化建议 / 完整计算口径 / 合规声明；诊断建议表必须含负责人/期限/成本节约/税收节约/税负影响/净影响分栏
- **输入校验**：`validate_plan` 拒绝负金额 / 越界比例（E2/E3 不在 [0,1]）/ 非法税率（E4 不在 {0.05, 0.15, 0.25}）

## 产品需求基线（原 PRD v1.0 要点）
- **痛点**：手工分析依赖顾问经验、口径不一；对标缺乏系统化工具易遗漏；建议一次性结论缺乏互动与跟踪；客户对「该有没的/可有可没有」缺乏直观认知
- **成功指标**：单客户诊断 ≤5 分钟；方案落地率 ≥60%；节税测算准确度 ±15% 内；报告一次通过率 ≥80%；离线可用率 100%
- **F1 导入**：支持 Excel/CSV（现已扩展 5 格式含 PDF）；三 Sheet 自动识别或分文件合并；无法解析时明确提示中止，不得静默生成空报告；同义词词典归并金蝶/用友科目
- **F2 分析**：基于近三年计算增值税税负率（估算）/所得税税负率/综合税负率/毛利率/各项费用率/期间费用率/净利率；营收为 0 时比率置 0 标「基数缺失」不除零；展示时叠加行业区间参考线
- **F3 诊断**：按严重度（高/中/低）排序；每条发现含事实/对标/建议；识别「该有没的」（研发费用缺失、福利/教育经费未用足限额）与「可有可没有/应控」（招待费超限、咨询费占比偏高）；VAT 低于行业下限且收入正常 → 真实性关注（高风险）
- **F4 互动**：每轮针对当前未决发现给 A/B/C 选项（含影响测算与落地可行性）；战略意图记录不强制立刻选择；无关输入礼貌引导回主题
- **F5 第二稿**：每条决策含环比同比、增值比例与幅度（当前值→目标值→变动幅度→预计节税，可由原始报表反算复核）、操作细节、注意事项；无历史序列标注「无历史，仅列最新值」不伪造同比
- **F6 落地判定**：落地性 ≥95% 或用户确认 → 完整方案；落地性评分可解释（未决高风险项 + 暂维持选择 + 可行性加权）；<95% 未确认时提示风险不直接输出交付物；生成后解锁导出
- **F7/F8 导出**：Word/PDF（综合分析+诊断+第二稿+完整方案+合规声明）；Excel 测算模型（方案概览/行动测算/执行清单可逐月勾选）；导出失败保留内存方案数据不丢失
- **F9 AI 可选**：OpenAI 兼容配置；未配置时规则引擎离线闭环；调用失败静默回退并提示
- **边界场景**：单年数据同比置 0 标注；科目余额表缺失跳过费用细化并提示；行业未匹配回退制造业基准并提示；括号负数正确转负值
- **验收 AC-1~9**：导入错误率 0；税负率与手工复算一致（±0.01pp）；示例数据识别典型问题且严重度正确；互动闭环可走通；默认全选 A 落地性=100%；三交付物可生成可打开；离线闭环可跑通；报告含合规声明无违规表述
- **待确认（历史遗留）**：行业基准校准方式（Q-2）、多客户管理（Q-3，v2）、大模型选型（Q-4，默认 OpenAI 兼容机构自备 key）、直连金蝶/用友（Q-5，v2 评估）、测算模型可编辑公式（Q-6，当前静态值）

## 技术架构要点

### 通用财报管线（现行架构，2026-08-11，Phase A+B+C+D 已落地）
- **目标**：任意企业、任意份数财报（1~N 份）走同一条管线；案例包=预置文件清单+元数据，零业务特判；单一真源 `CaseBundle`；金标可回归；数据质量可解释
- **领域概念**：SourceFile（一份文件）/ ExtractResult（单文件抽取）/ FinancialData（规范科目×年）/ PolicySnapshot（政策只读快照）/ DataQuality（置信度+勾稽）/ CaseBundle（一次业务案例）/ CaseManifest（案例包描述）/ Session（工作区绑定 bundle）
- **三原则**：金标（gold）只用于测试对账，不参与运行时默认；案例包只解决「文件从哪来」；政策只解决「预算/合规怎么约束」
- **分层强制单向依赖**：web_backend（Adapter/API，只调 Pipeline facade）→ pipeline（`core/pipeline.py` run_case_pipeline 编排）→ Extract/Normalize/Reconcile/Policy → FinancialData（真源事实）→ Diagnostic/Budget/Export 只读
- **禁止**：`budget_template` 硬编码公司/行业；`import/case` 另写金额逻辑；`diagnostic` 私设 `cit_rate=0.25`（必须读 PolicySnapshot）；为新客户写 `if company == "xxx"`；把金标金额写进 `DEFAULT_*`；export 阶段重新 OCR；增加第四条导入入口不走 pipeline
- **PolicySnapshot 合成顺序（固定）**：①行业 resolve → ②E2 ← WB 中枢 → ③E3 ← max(E2, 最近有效历史贡献率，负税年剔除) → ④E4 ← 附注抽取 > 用户已设 > 默认 15%（高新） → ⑤费用带/增速帽/稳健比率 ← compliance+reconciliation → ⑥冻结写入 bundle（预算顶栏从此快照灌入）
- **DataQuality 契约**：`confidence(high|medium|low)` / `text_layer` / `ocr_used` / `matched_cells` / `require_confirm` / `export_blocked` / `reconciliation{ok,hard_fail,errors,warnings}` / `expense_anomalies` / `parse_notes`；产品策略：hard_fail 或 low 置信度 → 允许进会话但导出默认需人工确认（require_confirm=true）
- **CaseManifest**：`demo_output/cases/<id>/manifest.json`（id/label/company_name/industry/files[{path,report_year}]/defaults/gold/tags）；文件可相对 cases 目录或外部搜索根（含上级「测试文件」目录）；新增案例 = 复制目录 + 改 manifest，无需改代码；已有 `audit_3years`、`audit_yikang_3y`（艺康金标为回归样例，非产品逻辑入口）
- **导入 API 统一响应**：`{summary, years, indicators, previews, sources, data_quality, policy{industry_key,e2,e3,e4,e3_basis,e4_source}, case_id}`；三入口（/import、/import/case/{id}、/import/sample）response 键集合必须一致；`GET /import/cases` 动态扫描 manifest
- **Session**：V1 单会话存完整 CaseBundle JSON（含 policy+quality）；V2 多 session_id 仅预留字段

### 数字质检引擎（core/numeric_audit.py，2026-08-18 新增独立分析层）
- **定位**：`reconciliation` 管表间/跨年勾稽与政策合成；`numeric_audit` 专做「文档数字本身」的体检——因程序核心是文档数据处理与分析，数字错误必须双层防护：**扫描层**（OCR 原文字面质检）+ **数字层**（恒等式与错位归因）
- **检查族**：① 利润表全式恒等（利润总额 ≈ 营收−成本−税金−四费，残差≤2% 营收为杂项正常，>10% 尝试归因）② 资产负债恒等（容差 max(1元, 资产×0.01%)）③ **小数点/量级错位归因**（某科目 ×10^k 后残差降至原 1/100 → 高置信建议，如 22263237393→222,632,373.93；候选含营收自身）④ 逐年跳变（>80% 提示核对）⑤ 合理性（负营收/资产、毛利率>100% 或 <−30%、单项费用>营收、利润总额与净利润反号）⑥ **OCR 字面质检**（形近字母混入 O/0、I·l/1、S/5、B/8、Z/2；全角数字；≥13 位粘连数字；千分位/小数点混用；每文件每模式最多报 3 条）⑦ OCR 来源整体风险提示
- **原则**：只分析与建议、**绝不静默改数**；纯标准库确定性数学可反算；不依赖上层模块
- **集成**：`pipeline.assemble_bundle` 在 enrich/policy 后调用 `audit_numbers(data, ocr_texts=ocr_list)`，报告存 `parsed_meta["numeric_audit"]`（session 只存 FinancialData 即可随历史载入/重启恢复）；`data_quality.numeric_grade/numeric_score` 同步；**任一 high 发现 → `require_confirm=True`**（导出前必须人工核验）
- **契约**：`{engine, version, score(100−25·高−10·中−3·低), grade(≥90高/≥70中/else低), summary, checked, identities[{id,rule,year,status,value,expected,gap,gap_pct}], findings[{id,check,year,subject,severity,value,expected,gap,message,suggestion}]}`；三处响应携带：`/api/import*`（bundle.to_import_response 的 numeric_audit 键）、`GET /api/session`、历史载入（均经 `session.get_numeric_audit()`）
- **前端**：`NumericAuditPanel` 渲染在「导入财报」页经营概况之下（评分徽标 + 恒等式明细表 + 分级发现清单 + 修正建议）；`QualityPolicyBanner` 附加「数字质检 等级(分数)」
- **测试**：`tests/test_numeric_audit.py` 11 例（干净高分、营收×100 错位归因、资产负债破坏、PBT 残差、跳变、负值、毛利率、OCR 字面四类、无 OCR 无发现、管线集成、会话/历史携带）

### 行业基准数据库（数据已落 `core/industry.py`，文档为数据来源说明）
- **四大指标公式**：增值税税负率=当期应纳增值税÷应税销售收入；所得税税负率=实缴所得税÷营业收入；毛利率=(营收−成本)÷营收；净利率=净利润÷营收
- **区间方法论**：利润率类分位数法（参考区间 P25–P75，偏低 <P10，偏高 >P90，预算 P50–P75 进取/P25–P50 稳健）；税负率类中枢±浮动（参考区间 [中枢×0.85, 中枢×1.4]，偏低线 ×0.7，偏高线 ×2.5，预算下限=中枢=合规红线）
- **预警判定**：增值税 <中枢×0.7 一级预警、<×0.5 二级预警、连续 3 月波动 >30% 三级预警、>×2.5 偏高提示；所得税盈利企业 <×0.6 偏低、连续三年亏损预警、>×2.0 偏高；净利率预算=毛利率预算−行业平均费用率（制造业 15~20%、零售 20~25%、软件 30~40%）；季度偏离预算带 >20% 连续 2 季触发修正
- **代码契约**：`INDUSTRY_REFERENCE_DB`（`industry.py`），每行业每指标 7 字段（median/min/max/low_warn/high_warn/budget_min/budget_max），指标 key：`vat_tax_rate`/`income_tax_rate`/`gross_margin`/`net_margin`；`_APP_REF_MAP` 应用层行业聚合映射（如批发零售业→商业批发+商业零售）、`_APP_CUSTOM_REF` 自定义行（制造业/交通运输业/电力热力）；GB/T 4754-2017 分类，22 参考行业
- **声明**：税负率经验值**非税务机关法定标准**，不得作为合规抗辩依据；小微企业对标应下调毛利/净利预期 20%~40%

### 扫描件 DeepSeek 解析方案（2026-08-06 定案，已落地）
- **根因**：扫描件无文本网格致 `parse_pdf` 科目错位/漏小数点或抛 ParserError；OCR 模块已写但未接入；预览 OCR 前 N 页有截断风险；deepseek 文本 API 不收图片只能喂文本层
- **方案**：逐页 pdfplumber 取文本 → 文本过短回退 RapidOCR → 整份带页码标记送 DeepSeek → JSON 映射 `FinancialData`（缺失科目标 0 + 警告）→ 复用确定性引擎 `compute_year_indicators`；年份取文件名优先（修复模型误判）；`_fill_table()` 把 `{科目:{本年,上年}}` 转 `{科目:{年:值}}`（上年/期初归前一年）
- **验证基线（艺康标准答案）**：营收 222,632,373.93 / 372,364,436.57 / 283,347,223.63；净利 6,949,739.60 / 23,665,662.88 / 21,417,649.37（2022/2023/2024）；合并 years=[2022,2023,2024]、matched=30
- **性能**：单份 60–100 秒，并发三份约 100 秒

### AI 完整财报合并报告管线（2026-08-07 修复，已落地）
- **根因**：预览通道截短（PDF 前 20 页/OCR 前 12 页/每页 600 字符）；token 硬编码过小（单份 800/合并 2500）；不检查 `finish_reason=length`；SQLite 固定 id=1 单会话可能保存企业/年份不一致的错误报告
- **方案**：「完整 OCR + 分段提取 + 确定性校验 + 最终合并」；预览通道与后台 AI 通道彻底分离；逐页记录（文件名/报告年度/物理页码/提取方式 text|ocr/完整文本/失败状态），单页失败不得静默跳过；分段不跨文件、以完整页为边界；**确定性指标（core.finance）优先于 AI 提取值**，冲突采用确定性值并保存冲突记录；报告身份校验（企业名一致、年份齐、页覆盖完整）
- **常量**：`CHUNK_MAX_CHARS=24_000`、`EXTRACT_MAX_TOKENS=4_096`、`FINAL_MAX_TOKENS=8_192`、`MAX_TRUNCATION_RETRIES=3`；前端轮询 1500ms
- **任务 API**：`POST /api/ai/years-summary/jobs` / `GET .../jobs/active` / `GET .../jobs/{job_id}`；状态机 `queued → running → completed|failed`（终态不可变）；每 session_version 仅一个活跃任务，守护线程执行；OCR 结果按文件 sha256 缓存（`pdf_page_cache` 表）；环境变量 `LIRUNBAO_DB_PATH`/`LIRUNBAO_AI_CONFIG_PATH` 供测试隔离
- **核心接口**：`PDFPageRecord(page_no,total_pages,method,text,status)`、`extract_all_pages(path,on_progress)`、`chunk_pages(pages,max_chars)`、`AIChatResult(content,finish_reason,...)`、`AIEngine.chat_result(user_prompt,system_prompt,max_tokens,extra)`（保留 `chat()` 兼容）
- **扫描财报识别（配套修复）**：直接识别 12 核心字段（营收/成本/税金及附加/四费用/利润总额/所得税/净利润/资产总额/负债总额），派生指标由确定性模块计算；年度证据优先级：报表标题完整日期 > 审计报告正文年度 > 文件名四位年份 > 用户输入；相对表头映射：本年/本期/年末/期末→报告年度，上年/上期/年初/期初→报告年度−1；候选值带来源页码/置信度/状态（trusted/review/conflict/manual）；跨年勾稽校验（2023 上年 vs 2022 本年等）；**缺失值必须 null 不得填零**；AI 只接收已确认结构化 JSON，未确认返回 HTTP 400；数值容差 `abs(a-b) <= max(1.0, max(|a|,|b|)*1e-6)`

### 经营分析报告链路（core/CO_report_analysis_WB-CO-TR-20260818.py，2026-08-18 新增；UI 名称「经营分析报告」，旧称经营预算分析）
- **导出页新顺序（有分割线）**：`① 经营分析报告 · 前世今生`（DeepSeek 先分析 → 导出 **Word/PDF**，同一份内容两种格式）→ **export-divider 分割线** → `② 费用编制建议 → 测算模型 / 费用预算三表`（Excel）。两段的文案均需 DeepSeek 生成后才能导出填入；前端 Word/PDF 卡在 `analysisReady` 前禁用，预算三表卡在勾选建议前禁用
- **分工（准确性优先）**：数字与结构全部来自确定性引擎——跨年表/指标表/折线图/附录在 `core/report.py` 原模板原样生成（`export_word/export_pdf` 新增可选 `narrative` 参数注入已合并叙事）；DeepSeek 只把 `build_analysis_factsheet` 事实清单改写成小白版文案（one_liner/headline/最以前-中间-现在 stages/now_points 正文/now_judgments 管理判断/future_actions）
- **三道防线**：① 提示词硬规则「只能引用事实清单数字、stages 数量与标题/要点标题/指标名必须一致」；② **数字白名单校验** `validate_analysis_numbers`——文案中出现事实清单外数字（含元/万元/亿元换算与舍入容差、年份、0-100 计数豁免）→ `number_warnings` 只提示**绝不静默改数**（与 numeric_audit 同原则）；③ `merge_narrative` 按 `_stage_key`（最以前/中间/现在）与要点标题逐项覆盖，缺项/超长/条数不匹配保留规则引擎原文案（宁缺毋滥）
- **API**：`POST /api/export/analysis`（生成并存内存 `_last_analysis`，按 session_version 匹配；未配置 AI → 503 不静默降级）、`GET /api/export/analysis/last`（同会话恢复，无则 404）；`GET /api/export/status` 新增 `analysis_ready`；Word/PDF 导出端点自动合并最近分析（版本不匹配或缺内容 → 回退规则叙事）
- **AI 整理并导入的静默跳过坑（2026-08-18 修复）**：`onSummarize` 原来只在 `selectedFiles.length > 0` 时自动 `runImport`，「查看已保存预览」恢复的 previews 没有原始 File 对象 → 整理成功但**不发 POST /api/import、无提示、无导入记录**（用户以为导入失败）。修复契约：无文件时必须给出明确警告（`aiSkipImportWarn`）+ 按钮文案改为「AI 整理（预览无文件，不会自动导入）」+ 查看已保存预览时显示仅供查看提示条 + onFolderPick/onDrop/runImport 重置警告与 showSavedPreview。「已保存预览」永远不可导入（File 对象已不存在），正确路径=重新选文件（拖入即自动导入）
- **测试**：`tests/CO_test_report_analysis_WB-CO-TR-20260818.py` 6 例（事实清单结构、合并只改文本不动数字、数字白名单告警、normalize/has_content、无 AI 503、合并叙事注入 Word 模板保留）

## Web 化与运维要点
- **架构**：React+Vite 前端 → FastAPI 后端（`create_app()`，仅监听 `127.0.0.1:8765`，`GET /api/health` 返回 `{"status":"ok","bind":"127.0.0.1"}`）→ 复用 `core/` + SQLite（`app.db`）+ 本地工作区导出
- **七工作区 → 五工作区（2026-08-18）**：`overview(导入财报，含导入+AI报告+历史卡片) | diagnosis | interaction | export | settings`（防导航与 API 路由漂移）；`Workspace` 类型已收窄（import/budget 已移除）；导入提交按钮名为「开始导入」（避免与导航「导入财报」严格模式冲突）；视觉深色颗粒风格
- **启动**：终端 `.venv/bin/python -m web_backend.CO_run_WB-CO-TR-20260805160732`；双击 `scripts/启动利润宝Web_WB-CO-TR-20260805160732.command`（自检 dist 存在 → 启动 → 轮询 health → open 浏览器）
- **安装**：`scripts/安装利润宝_WB-CO-TR-20260726.command`，Python 查找顺序 `LRB_PYTHON` → python3.13/3.12/3.11/3（校验 ≥3.11）——**但 rapidocr 依赖限制实际需 <3.13，见踩坑节**
- **检查脚本**：`环境检查`（退出码 0 通过/1 警告/2 阻断）、`质量检查`（pytest 全量 + make_sample + /api/health + guardian --quick + check.sh）
- **故障排查**：① Gatekeeper 拦 .command → 右键打开；② 找不到 Python 3.11+ → `brew install python@3.12` 或 `export LRB_PYTHON=...`；③ 缺前端产物 → `cd web_frontend && npm install && npm run build`；④ 扫描件提示配 AI → 设置页填 Base URL/模型/Key；⑤ 真实模板验收 → `export LRB_REAL_TEMPLATE_PATH=...` 跑 test_t7_acceptance（未设自动跳过）
- **发布**：历史 v1.0.0 用「构建发布包」脚本产出 release/+ZIP+SHA-256 并扫敏感字符串（该脚本现不在 scripts/，需发布时重建）；发布包排除 .git/.venv/缓存/AI 配置/过程文件
- **GitHub 仓库（2026-08-18 起推送）**：`origin=https://github.com/bruceleeu-creator/Li-Run-Bao-Web-.git`（gh CLI 已登录 bruceleeu-creator，main 直推）；推送流程=清缓存（`__pycache__`/`.pytest_cache`/`node_modules/.vite`）→ guardian --quick → `git add -A && git commit && git push origin main`；`.ai_config.json`（API Key）/运行时 DB（根目录与 workspaces 的 app.db）/workspaces 用户上传文件均被 .gitignore 挡住，仅放行三个 e2e 夹具 xlsx；根目录误生成的空 `app.db` 2026-08-18 已补 ignore
- **git 代理坑**：本机 git 配了 `http.proxy=127.0.0.1:7890`（Clash 类），代理未运行时 push 报 `Failed to connect to 127.0.0.1 port 7890`——绕过：`git -c http.proxy= -c https.proxy= push origin main`；或先启动代理再常规 push

## 验收记录（T8 最终独立验收，2026-07-26）
- **结论：通过（CO Gate 8 签字），允许作为 v1.0 本地可用版本交付**
- 质量门禁：129 通过/2 跳过（仅缺可选 PDF 提取器）；Guardian 0 错 0 警；check.sh 5 项全过；样例生成/无头端到端通过
- 真实模板 GUI 验收：载入用户原始 84 行模板成功，E4 税率 0.05 正确读取，三类导出解锁（该验收对象为 Tk 窗口，功能等价物现为 Web 模板工作台）
- Excel 复核：三 Sheet 尺寸 100×10/5×6/9×10；357 个公式无静态错误；行动清单净影响合计 944,333.33 元
- Word/PDF 复核：docx 8 页 7 章节完整；pdf 4 页 A4 无截断乱码
- **遗留**：LibreOffice 转换 Word/Excel 无法正确回退中文字体（环境字体差异，不阻断交付）；v1.1 计划统一跨平台中文字体族

## 智能体标识
- WORKBUDDY → `WB`，CODEX → `CO`，Claude Code → `CC`，TRAE → `TR`
- 新增文件命名后缀：`{文件名}_WB-CO-TR-{日期}.{ext}`
- 样例产物持久化目录：`demo_output/`（含 cases/ 案例包与 `_WB-CO-TR-20260726` 样例）

## 容易踩坑的 Python 环境
- 系统默认 `python3` 为 3.9.6，改用 3.11+；不要假设 `python3.13` 默认可用
- **rapidocr_onnxruntime>=1.4 需 Python <3.13**（Requires-Python 上限），3.13 下 pip 装依赖直接失败；本机 2026-08-18 实测用 Python 3.11.15 成功；安装脚本与 README 的「3.13 优先」顺序对全新环境不成立
- macOS 默认无 `xvfb-run`，Web 后端测试用 TestClient/Playwright
- 验收命令统一使用项目 `.venv/bin/python`（正式支持 Python 3.11+）

## 守护脚本（Project Guardian）
- 路径：`.hooks/project_guardian.py`，Hook：`.hooks/pre-commit`
- 7 维度检查：模块完整性 / Python 语法 / 架构依赖 / 合规红线 / ADR 遵循 / AGENTS.md 时效性（比对「更新：YYYY-MM-DD」与最新代码 mtime）/ 导出完整性
- 退出码：0=通过 / 1=警告 / 2=错误（错误阻塞提交，警告不阻塞）
- `find_missing()` 兼容 `.py` 与非 `.py` 文件（如 `requirements.txt`）
- 修复问题后请同步更新 `EXPECTED_MODULES`，新增模块必须显式登记
- ADR 清单：ADR-001 本地 Web（禁 PyQt/Electron/flask）/ 002 确定性数学引擎 / 003 规则引擎兜底 / 004 Excel 数据交换 / 005 同义词词典 / 006 ReportLab+python-docx（禁 pandoc/WeasyPrint）/ 007 openpyxl（禁 xlsxwriter）/ 008 Matplotlib（禁 plotly/pyecharts）

## 当前实现状态
| 模块 | 文件 | 状态 |
|------|------|------|
| 数据模型/同义词 | `core/models.py` | ✅ |
| 行业基准 + v1.0 对齐 | `core/industry.py` + `diagnostic.py` | ✅ 22 行业 7 字段参考区间（`INDUSTRY_REFERENCE_DB`），所得税/毛利率/净利率判定与 VAT 分级预警 |
| Excel/CSV/多格式解析 | `core/parser.py` | ✅ xlsx/csv/docx/pptx/pdf 五格式 |
| 确定性计算 | `core/finance.py` | ✅ |
| 诊断引擎 | `core/diagnostic.py` | ✅ |
| 互动状态机 | `core/interactive.py` | ✅ |
| AI 增强（可选） | `core/ai_engine.py` | ✅ |
| Word/PDF 报告 | `core/report.py` | ✅ 7 章节 + 分栏 |
| Excel 测算模型 | `core/action_pack.py` | ✅ |
| 模板版预算引擎 | `core/budget.py` + `budget_template.py` | ✅ |
| 通用财报管线 | `core/pipeline.py` + `case_manifest.py` + `reconciliation.py` | ✅ Phase A+B+C+D：run_case_pipeline 门面 + 案例包 + PolicySnapshot 单点 |
| 扫描件/AI 解析 | `CO_deepseek_parse` + `CO_full_pdf_reader` + `CO_financial_scan` | ✅ 整份解析 + 逐页读取 + 坐标化识别 |
| 预算建议/导出叙事 | `CO_budget_advice` + `CO_budget_export` + `narrative.py` | ✅ 三表导出 + Word 叙事 |
| 经营预算分析（前世今生） | `CO_report_analysis` + `CO_export./analysis` + `CO_ai.analyze_operating_narrative` | ✅ DeepSeek 文案层 + 数字白名单 + Word/PDF 同源注入（2026-08-18） |
| Web 后端 | `web_backend/` 15 模块 | ✅ 导入/会话/诊断/互动/预算/导出/AI 报告任务全接通，SQLite 持久化 + 导入历史（2026-08-18） |
| Web 前端 | `web_frontend/` React+Vite | ✅ 五工作区真实 API + 流程导航；导入财报合并页（两栏：主列+右侧记录栏）+ 历史卡片快速载入 |
| 数字质检引擎 | `core/numeric_audit.py` | ✅ 双层防护：OCR 字面 + 恒等式/错位归因/跳变/合理性 + 评分；高风险强制人工核验（2026-08-18） |
| 导入历史/完整载入 | `CO_db.import_history` + `CO_import` 载入路由 | ✅ 卡片/报告点击完整恢复案例（财务+诊断+互动+解锁+报告） |
| 单元/接口测试 | `tests/` 30 文件 | ✅ 含 e2e 9 spec 34 用例（Playwright） |

## 验收命令（每次提交前必跑，统一用 `.venv/bin/python`）
```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python data/make_sample.py
.venv/bin/python .hooks/project_guardian.py --quick
bash .hooks/check.sh
```
Web 健康验收：
```bash
.venv/bin/python -m pytest tests/CO_test_web_health_WB-CO-TR-20260805160732.py -q
cd web_frontend && npm run build && cd ..
```
Web 导入验收（需 Playwright Chromium）：
```bash
.venv/bin/python -m pytest tests/CO_test_web_health_WB-CO-TR-20260805160732.py tests/CO_test_web_import_WB-CO-TR-20260805160732.py tests/CO_test_web_import_ext_WB-CO-TR-20260805160732.py -q
cd web_frontend && npm run build && npx playwright test && cd ..
```

## Agent skills 工作流（原 docs/agents，已内联）

### Issue tracker（本地 Markdown 跟踪）
- issue 与规格（PRD）以 Markdown 存放于 `.scratch/`，不用外部 tracker
- 每个功能一个目录 `.scratch/<feature-slug>/`，规格文件 `spec.md`；实现类工单 `.scratch/<feature-slug>/issues/<NN>-<slug>.md` 从 `01` 起编号，不合并成单个工单文件
- 分诊状态写在文件顶部 `Status:` 行（标签词汇见下）；评论以 `## Comments` 标题追加到文件末尾
- 技能指令映射：「publish to the issue tracker」= 在 `.scratch/<feature-slug>/` 下新建文件；「fetch the relevant ticket」= 读取所引用路径文件
- Wayfinding（供 `/wayfinder`）：Map = `.scratch/<effort>/map.md`（Notes/Decisions-so-far/Fog）；Child = `issues/NN-<slug>.md`（`Type:` 行记 research/prototype/grilling/task，`Status:` 行记 claimed/resolved）；Blocking = 顶部 `Blocked by: NN, NN` 行；Frontier = 扫描 open、未阻塞、未认领的工单按编号优先；Claim = 先写 `Status: claimed` 再开工；Resolve = `## Answer` 下追加答案、置 resolved、在 map.md 的 Decisions-so-far 追加指针

### Triage labels
- `needs-triage`（待评估）/ `needs-info`（等报告方补充）/ `ready-for-agent`（规格完整可供 AFK agent）/ `ready-for-human`（需人工）/ `wontfix`（不处理）；技能提到某角色时直接用对应标签字符串写入 `Status:` 行

### Domain docs
- 探索代码库前必读仓库根 `CONTEXT.md`（若存在）；多上下文仓库先读 `CONTEXT-MAP.md`；`docs/adr/` 中读相关 ADR（**注意：docs/ 已删除，ADR 现仅存于守护脚本 ADRs 清单，见上节**）；文件不存在时静默继续，不主动建议创建
- 术语纪律：命名领域概念（issue 标题/重构提案/测试名）必须用 `CONTEXT.md` 术语表定义的词，不得漂移到明确规避的同义词
- 输出与现有 ADR 矛盾时必须明确指出而非静默覆盖，格式：`> _Contradicts ADR-XXXX (...) — but worth reopening because…_`

## 文档整合说明（2026-08-18）
- 原 `VERSION_WB-CO-TR-20260726` / `CHANGELOG_WB-CO-TR-20260805160732` / `PRIVATE_REPOSITORY_NOTICE_WB-CO-TR-20260726` 及整个 `docs/` 目录（文档索引、00_项目管理、01_使用与发布、02_产品与技术、03_验收与记录、agents、superpowers、利润宝_PRD.docx）已整合进本文件后删除
- 安装使用细节见 `README.md`；架构 Spec 全文要点见「技术架构要点」节；如需恢复原文档，从发布包 v1.0.0 ZIP 或本机备份找回

## e2e 夹具说明（2026-08-18）
- `web_backend/workspaces/202{1,2,3}年审计报告.xlsx`、`demo_output/样例财报_WB-CO-TR-20260805.docx`、`demo_output/样例财报中文_WB-CO-TR-20260805.pdf` 为 e2e 必需夹具（本机原缺失，2026-08-18 用 `data/make_sample` 数据 + STSong-Light CID 字体重新生成，parser 三格式解析验证通过）；新机器跑 e2e 前需保证存在
- 本机已知遗留测试失败（与功能无关）：`CO_test_full_ai_report` 9 错误+部分失败（缺真实艺康 PDF，需上级「测试文件」目录）；`test_diagnostic::test_industry_fallback_to_manufacturing` 与 `test_order_independence` ×4（代码行为与测试预期不符：行业回退未保留原名 / 发现项顺序跳项——待修）
