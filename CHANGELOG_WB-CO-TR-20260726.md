# 利润宝 更新日志（CHANGELOG）

> 智能体标识：WB-CO-TR-20260726  
> 格式：Keep a Changelog · 语义化版本  
> 注：v1.0.0 之后的架构调整——**Tk 桌面端已于 2026-08 移除（`gui.py`/`main.py`/Tk 测试删除），Web 为本项目唯一入口**。下方 v1.0.0 的 GUI（Tkinter）/ `--demo` / `--headless` / `test_gui_headless.py` 等条目为当时历史记录，现已不适用。

---

## [v1.2.0] - 2026-08-09 · 诊断闭环：行业选择完善 + 诊断/互动 AI 介入 + 全流程导航

### 新增

#### 行业选择（B1）
- `core/industry.py`：行业从 3 个扩展至 10 个（新增建筑业、软件和信息技术服务业、医药制造业、住宿餐饮业、交通运输业、房地产业、电力热力生产供应业），每个行业带一句话说明 `desc`
- `list_industries_with_desc()` / `get_industry_desc()` / `recommend_by_rule()`：行业说明与规则推荐
- `/api/industries` 响应扩展为 `{industries: [{name, desc}], names: [...], default}`（`names` 保持向后兼容）
- `POST /api/industries/recommend`：AI 优先推荐行业（返回 `{industry, reason}`，解析失败回退规则关键词），AI 未配置时规则路径并返回 `source/fallback` 标识

#### 诊断 API（B2）
- 新模块 `web_backend/CO_diagnosis_WB-CO-TR-20260805160732.py`
- `POST /api/diagnosis/run`：规则引擎 `core.diagnostic.diagnose()` + 可选 AI 增强 A/B/C 选项（逐条 try，失败保留规则选项，`ai_used`/`ai_enhanced` 标注）
- `GET /api/diagnosis`：读取已保存诊断（持久化 `diagnosis_results` 表，绑定 `session_version`，不匹配返回 null）
- `POST /api/diagnosis/clear`：清空诊断
- 重新导入（`session.replace`/`clear`）自动清空诊断与互动表，旧结果绝不复用

#### 互动 API（B3）
- 新模块 `web_backend/CO_interaction_WB-CO-TR-20260805160732.py`
- `POST /api/interaction/start` / `GET /api/interaction/state` / `POST /api/interaction/decide` / `POST /api/interaction/confirm`：驱动 `core/interactive.py` 状态机（FINDING_LOOP → DRAFT2 → CONFIRMATION → FINAL）
- 互动会话持久化 `interaction_sessions` 表，刷新/重启后可从 DB 重建（重放决策 + 恢复确认态）
- 严格不跳项：decide 只接受当前发现的 finding_id；draft2 生成后可选 AI 增强操作细节，失败静默回退

#### 前端流程导航与页面真实化（F1-F7）
- `WorkflowRail` 提升为全局组件：所有页面顶部显示「财报导入 → 第一轮诊断 → A/B/C 互动 → 第二稿与导出」，完成态由真实数据（会话/诊断/互动/导出解锁）推导
- 新增 `StepNav` 底部导航：主流程页「上一步/下一步」，带前置校验（未导入/未诊断/未互动时拦截并提示）
- 行业选择完善：下拉展示全部行业 + 说明，新增「AI 推荐行业」按钮（AI/规则双路径，推荐结果卡片可一键应用）
- `DiagnosisPage` 真实化：自动执行诊断，展示真实发现（事实/行业对标/建议/A/B/C 选项），标注 AI 介入状态，可重新诊断
- `InteractionPage` 真实化：A/B/C 选项卡（净影响/可行性/风险/分栏金额）→ 决策推进 → 第二稿 → 落地性评分 → 确认解锁导出
- `ExportPage` 状态联动：按 `is_export_unlocked` 启用导出卡片（真实文件生成后续接入，保持诚实提示）

#### 测试
- `tests/CO_test_web_diagnosis_interaction_WB-CO-TR-20260809121546.py`：12 个接口测试（诊断/互动/行业推荐，含完整状态机走通、会话版本失效、不跳项校验）
- `web_frontend/e2e/diagnosis_flow.spec.ts`：4 个 e2e（步骤条可见、行业推荐应用、完整流程导入→诊断→互动→导出、导航拦截）

### 修复
- 互动 API 死锁：`_lock` 由 `threading.Lock` 改为 `RLock`（`_require_session`→`_ensure_session` 嵌套获取导致）
- 重新导入相同数据时 session_version 不变导致旧互动会话被误用：内存会话有效性增加 DB 记录存在性校验
- 前端子页回调内联箭头导致无限重渲染卡死：`onDiagnosisDone`/`onInteractionChange` 改为 App 层 `useCallback` 稳定引用

---

## [v1.0.0] - 2026-07-26 · 首个可交付 MVP

### 新增

#### 核心引擎（F1-F9）
- `core/models.py`：数据模型 + 科目同义词词典（金蝶/用友项目名归并）
- `core/industry.py`：行业对标基准库（税负率/费用率经验区间）
- `core/parser.py`：Excel/CSV 三表解析（利润表 / 资产负债表 / 科目余额表）
- `core/finance.py`：税负率/成本结构/环比同比/增长测算（确定性数学，可逐笔反算）
- `core/diagnostic.py`：第一轮诊断引擎（初稿建议：该有没的 / 可有可没有 / 真实性风险）
- `core/interactive.py`：多轮交互引擎（每轮 3 选项 → 第二稿 → 完整方案）
- `core/report.py`：Word/PDF 报告生成（7 章节 + 11 列诊断建议分栏）
- `core/action_pack.py`：成本优化测算模型（Excel，可逐月跟踪）
- `core/ai_engine.py`：可选大模型接口（OpenAI 兼容；未配置则规则引擎兜底）

#### 模板版预算引擎（T6.1-T6.6）
- `core/budget.py` + `core/budget_template.py`：84 行企业成本费用计划表
- `read_template`：校验 Sheet 名 + 关键标签 A1/A12/A13/D13/G13/I13 + 84 行结构
- `write_template`：同一工作簿三 Sheet（费用预算表 / 行业参考 / 诊断与行动清单）
- 原模板防覆盖：比较 `os.path.realpath` 后同路径直接抛 `TemplateError`
- A/B/C 列优先读取用户文件维护内容

#### GUI（Tkinter）
- `gui.py` + `main.py`：5-Tab 桌面界面（首页 / 模板工作台 / 诊断 / 互动 / 导出）
- grid 布局 + 滚动条 + 三个导出按钮（Word / PDF / 测算模型）
- 入口支持 `--demo`（演示）/ `--headless`（无头验收）模式
- 旧 `.ai_config.json` 含 `api_key` 时启动 GUI 原子重写为仅 `base_url/model`

#### 质量门禁
- `.hooks/project_guardian.py`：7 维度检查（模块完整性 / Python 语法 / 架构依赖 / 合规红线 / ADR 遵循 / AGENTS.md 时效性 / 导出完整性）
- `.hooks/check.sh` + `.hooks/pre-commit`：快速自检与提交前门禁
- 退出码：0=通过 / 1=警告（不阻塞）/ 2=错误（阻塞提交）

#### 测试
- `tests/`：129 单元测试通过 / 2 跳过（可选 PDF 文本提取器）
- `tests/test_t7_acceptance.py`：19 测试覆盖 P0-1~P0-4 + P1-1~P1-4
- `data/test_gui_headless.py`：端到端无头测试（用 `update_idletasks()` 推进，不依赖 xvfb）

#### T10 macOS 封装（本次新增）
- `scripts/安装利润宝_WB-CO-TR-20260726.command`：双击安装（自动查找 Python 3.13/3.12/3.11）
- `scripts/启动利润宝_WB-CO-TR-20260726.command`：双击启动（缺 .venv 时提示先安装）
- `scripts/环境检查_WB-CO-TR-20260726.command`：返回明确退出码（0/1/2）
- `scripts/质量检查_WB-CO-TR-20260726.command`：全量 pytest + demo + headless + Guardian + check.sh
- `scripts/构建发布包_WB-CO-TR-20260726.sh`：构建 release/ 目录 + ZIP + SHA-256，自动扫描敏感字符串
- `docs/01_使用与发布/利润宝_macOS安装使用指南_WB-CO-TR-20260726.md`：macOS 同事使用指南
- `docs/01_使用与发布/利润宝_v1.0.0私有发布说明_WB-CO-TR-20260726.md`：本发布说明
- `VERSION_WB-CO-TR-20260726` / `CHANGELOG_WB-CO-TR-20260726.md` / `PRIVATE_REPOSITORY_NOTICE_WB-CO-TR-20260726.md`

### 变更

#### T10 保守清理（清理力度 1）
- 删除 7 项可再生缓存/临时：`.DS_Store`、`__pycache__/`、`core/__pycache__/`、`data/__pycache__/`、`tests/__pycache__/`、`.pytest_cache/`、`demo_output/.~budget_3sheet_WB-CO-TR-20260726.xlsx`
- `.gitignore` 补充：`.pytest_cache/`、`~$*.xlsx`、`.~*.xlsx`、`release/`
- `tests/test_t7_acceptance.py` 真实微信模板路径改为环境变量 `LRB_REAL_TEMPLATE_PATH`：
  - 未设置时跳过该真实文件用例
  - 设置时继续执行真实模板验收
  - 不降低其余测试覆盖
- `README.md` 修正：
  - 使用真实目录名 `利润宝/`（原误为 `lirunbao/`）
  - 使用项目 `.venv/bin/python`（原误为 `python3.11`）
  - 删除不可用的 `xvfb-run` 假设
  - 新增 macOS 双击脚本使用方式
  - 标注私有仓库 / 离线优先 / AI 可选 / 数据不出本机

### 安全与合规
- 发布包已扫描本机用户路径、微信容器路径、用户名标识等敏感字符串，命中即中止构建（具体模式见构建脚本）
- `AGENTS.md` 与 `CO T8 验收记录` 发布副本已脱敏（替换为相对路径）
- 真实 API Key/Token/密码不会进入仓库；`.ai_config.json` 在 `.gitignore` 中忽略
- 增值税税负率三处显著标注「估算值（基于税金及附加反推）」
- 所有建议限于合法税务筹划范畴

### 验收
- CO T8 最终独立验收通过（Gate 8 签字）
- T7 P0/P1 全量整改完成
- T10 保守清理与 macOS 封装完成（本批次）
- 全量回归：129 通过 / 2 跳过

---

## 后续版本

### [v1.1] - 规划中
- 行业基准校准（按机构客户样本）
- 模板自定义（非 84 行固定结构）

### [v2.0] - 规划中
- OCR 识别纸质报表
- 多客户组合管理
- 直连金蝶/用友 API 取数
