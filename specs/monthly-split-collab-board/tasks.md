# Tasks · 年度预算二段式生成与月度拆分 + 协同任务看板

| 项目 | 内容 |
|---|---|
| Spec 名称 | monthly-split-collab-board |
| 版本 | v1.0（2026-08-20） |
| 来源文档 | 桌面《利润宝-执行文档-预算月度拆分与协同看板-20260820.md》（已定稿，本文为 spec-workflow 整理版） |
| 环境 | Windows 主机，项目根 `C:\Users\Administrator\Desktop\Li-Run-Bao-Web-`，`.venv\Scripts\python`；前端命令在 `web_frontend/`（模块 A）与 `collab_board/board_frontend/`（模块 B）内执行 |
| 命名约定 | 所有新文件带 `_WB-CO-TR-20260820` 后缀；新增模块必须登记 `.hooks/project_guardian.py` 的 `EXPECTED_MODULES` |

**执行规则**（spec-workflow Phase 4）：
- 每完成一个任务把 `- [ ]` 改 `- [x]`；一个阶段全绿并跑过该阶段验收命令后才进入下一阶段。
- 关键路径：P0→P1→P2→P3→P7；模块 B 侧 P4→P5→P6→P7；两模块可并行。
- 每阶段收尾在下方「执行日志」追加一行（日期/阶段/结果）。
- 改项目源文件走 Edit/Write 工具（Bash 写源会被 Mimosa 门拦截）；commit/push 按 AGENTS.md「GitHub 推送教程」。
- **文档生命周期（owner 2026-08-20 定）**：`specs/monthly-split-collab-board/` 是**执行期工作文档**——P0 单独入库便于 git 追踪任务勾选，P8 完成 AGENTS.md/README 规整后**整目录删除**（沿用 v22「docs/ 整合进 AGENTS.md 后删除」先例）。持久记录 = AGENTS.md（唯一真源）+ README + `demo_output/联调记录` 归档。桌面三份原件不属仓库，不动。

---

## P0 · 基线入库（新增，先于一切）

- [x] 1. 提交工作区遗留的 v26.1 Windows 迁移改动
  - 当前 `git status` 有 11 个未提交文件（.gitignore / guardian / AGENTS.md / core OCR 三件 / parser / requirements / tests×2 / CO_ai_report_job），属上一阶段已验收内容
  - 流程：`.venv\Scripts\python .hooks\project_guardian.py --quick` → `git add -A -- ':!specs'` 暂存遗留改动（**排除 specs/**，勿混入）→ `git status --short` 核对 → `git commit` → 再单独提交 spec 工作文档：`git add specs && git commit -m "docs: specs 执行期工作文档（P8 规整后删除）"` → 推送（代理问题时 `git -c http.proxy= -c https.proxy= push origin main`）
  - 目的：新功能从干净基线开始；specs 单独成提交，任务勾选进度可随各阶段 `git add -A` 自然入库，P8 删除时有清晰历史对照
  - _Requirement: 工程基线（非 FR）_

## P1 · 模块 A 后端（拆分引擎 + API + 持久化），1.5~2 天

- [x] 2. 拆分引擎 `core/CO_monthly_split_WB-CO-TR-20260820.py`
  - `MonthlyRow / SplitResult` 数据类（契约 design.md §2.3）
  - `normalize_weights(w12)`：负值清零、NaN 拒绝、归一化 Σ=1
  - `distribute(annual, weights)`：round 到元 + 尾差归位到 argmax 月；annual=0 全 0
  - `verify(rows) -> {row_failures, total_gap}`：逐行与整表恒等校验
  - 规则形状库：`RIGID_UNIFORM_KEYWORDS`（工资/社保/公积金/五险/房租/租赁/物业/折旧/摊销/利息/通讯/网络）、`PEAK_KEYWORDS`（年终奖/奖金/提成）、`CAMPAIGN_KEYWORDS`（广宣/推广/广告/营销/策划）、`rule_split(plan_rows, answers)`、春节月函数（预算年度→1 或 2 月）
  - `merge_ai_weights(plan_rows, ai_rows)`：AI 行缺失/非法 → 该行回退 uniform 并记 warning
  - 验收：尾差（annual=100,001 均匀权重→仅一月多 1 元）；随机 100 行×随机权重恒等全过（固定种子）；0 元行全 0；形状表驱动四例；AI 缺 3 行→回退 3 行 warning=3
  - _Requirement: AC-A3.1, AC-A3.2, AC-A3.4_
- [x] 3. AI 函数扩展 `web_backend/CO_ai_WB-CO-TR-20260805160732.py`
  - 新增 `generate_monthly_questions(plan_snapshot, hints)` 与 `generate_monthly_weights(plan_snapshot, answers, hints)`；走 `ai_engine.chat_result`，thinking disabled，max_tokens 4096
  - JSON 解析失败/结构非法返回 `(None, err)` 由调用方回退规则
  - 验收：mock 坏 JSON/缺行/负权重 → 均 `(None, err)`；合法 → 正常对象
  - _Requirement: AC-A2.2, AC-A3.3_
- [x] 4. 状态表 `web_backend/CO_db_WB-CO-TR-20260805160732.py`
  - `init_db` 增 `monthly_budget_state` 建表（DDL design.md §2.1）
  - `upsert_monthly_state(version, **fields)` / `get_monthly_state(version)` / `delete_monthly_state(version)`
  - 勾选指纹 = sha256(排序后 `{row,budget_amount}` 列表)
  - 验收：upsert→get 往返一致；指纹对顺序不敏感、对金额敏感
  - _Requirement: AC-A5.1, AC-A5.2_
- [x] 5. 路由 `web_backend/CO_monthly_WB-CO-TR-20260820.py`（11 端点，importlib 注册进 CO_app）
  - draft 任务：调 `start_budget_export_job(advice_items)`（`CO_budget_export_job_WB-CO-TR-20260810.py:260`），完成回调/守护线程把路径+行快照写入 monthly_budget_state，不改旧任务对外契约
  - questions：AI 失败静默回退 rule；answers 必填校验；split 线程：AI 权重重试≤3 → merge → verify 失败则 rule_split → stage=ready，全程 progress
  - download：stage=ready 读 draft xlsx + split_result 交 append_monthly_sheet 后返回；非 ready 409
  - 验收（TestClient 假 AI monkeypatch）：draft 完成 stage=draft 无下载副作用；questions 幂等；answers 缺必填 400；split 失败回退 mode=rule；download 非 ready 409；指纹变化重置；未配 AI 全流程到 ready
  - _Requirement: AC-A1.1~A1.3, AC-A2.1~A2.3, AC-A3.3, AC-A5.1, AC-A5.2_
- [x] 6. P1 阶段门禁
  - `.venv\Scripts\python -m pytest tests/CO_test_monthly_split_WB-CO-TR-20260820.py tests/CO_test_monthly_api_WB-CO-TR-20260820.py -q` 全绿
  - `.venv\Scripts\python .hooks\project_guardian.py --quick` 0 错误
  - _Requirement: 阶段门禁_

## P2 · 模块 A Excel 月度 Sheet，0.5~1 天

- [x] 7. 写入函数 `core/CO_budget_export_WB-CO-TR-20260810.py` 新增 `append_monthly_sheet(xlsx_path, split_result, out_path, mode)`
  - openpyxl load → 追加「月度执行计划」Sheet（列布局/公式 Q=SUM(E:P)、R=Q−D、末尾总计与说明行，design.md §2.4）→ 另存
  - 样式：表头加粗+发丝边框，禁花哨填充；同名 Sheet 重复调用幂等覆盖
  - _Requirement: AC-A4.1, AC-A4.2_
- [x] 8. 兼容回归 `tests/CO_test_monthly_excel_WB-CO-TR-20260820.py`
  - make_sample 生成第一稿 → append → `read_template` 读回成功
  - openpyxl 复算 Q/R：R 全 0、Q==months 行和
  - 重复 append 幂等
  - 门禁：`pytest tests/CO_test_monthly_excel_WB-CO-TR-20260820.py -q` + `.venv\Scripts\python data\make_sample.py`
  - _Requirement: AC-A4.1, AC-A4.2_

## P3 · 模块 A 前端三步向导 + e2e，1~1.5 天

- [x] 9. API 客户端 `web_frontend/src/CO_api_WB-CO-TR-20260805160732.ts`
  - 新增 7 函数：startBudgetDraft / getMonthlyState / genMonthlyQuestions / submitMonthlyAnswers / startMonthlySplit / pollMonthlySplit / downloadMonthlyBudget
  - `CO_types` 扩展 MonthlyState / MonthlyQuestion / SplitResultFE
  - _Requirement: FR-A1~A5 前置_
- [x] 10. ExportPage ② 段改造 `CO_app_WB-CO-TR-20260805160732.tsx`（2095 行起）
  - 竖向四步骤（建议→第一稿→问答→结果与导出），沿用 panel/field 纸墨体系，不加动画，不动 ① 段与分割线
  - 第一稿按钮「生成预算第一稿」→ 摘要卡（营收/总盘子/费用率/非空行/已填建议），无下载动作
  - 问答表单：单选组+文本域+「全部按推荐项」；次级链接「跳过月度拆分（导出旧版）」→ 现有 `downloadBudgetExportAsync`
  - 结果步：进度条复用 budgetProgress；月度透视表（科目×12 月，行级 ✓）+ warnings 提示条；主按钮「导出费用预算三表（含月度拆分）」
  - 挂载 `getMonthlyState` 按 stage 恢复；勾选变化警示条；回调全部 useCallback
  - _Requirement: AC-A1.1, AC-A2.1~A2.3, AC-A3.2, AC-A4.1, AC-A4.3, AC-A5.1, AC-A5.2_
- [x] 11. e2e 更新
  - 预算流程 spec 改走新步骤序列；新增「跳过拆分导出旧版」用例；确认 `diagnosis_flow.spec.ts:78`「导出测算模型」卡不动
  - 门禁：`cd web_frontend && npm run build && npx playwright test`（34 基线不降）+ `.venv\Scripts\python -m pytest tests/ -q` 全量回归（遗留已知失败见 AGENTS.md，不新增）
  - _Requirement: AC-A4.3, 兼容约束_

## P4 · 模块 B 后端服务，2~2.5 天（可与 P1~P3 并行）

- [x] 12. 骨架与数据库 `collab_board/board_backend/`
  - `CO_app`（create_app：/api 路由 + StaticFiles 托管前端产物 + /api/health）+ `CO_db`（psycopg 连接池 env DATABASE_URL、`init_schema()` 幂等执行 design.md §3.2 全部 DDL、`SELECT 1` 健康探针）
  - `requirements-board.txt`（fastapi uvicorn psycopg[binary] bcrypt pyjwt openpyxl python-multipart）+ `Dockerfile`（python:3.12-slim，COPY board_frontend/dist，uvicorn 0.0.0.0:8080）
  - 部署三件套：`docker-compose.yml`（db：postgres:16-alpine+数据卷+健康检查，不映射公网；app：8080 对外+depends_on）、`nginx/board.conf`、`deploy/backup.sh`
  - _Requirement: FR-B1~B7 基础设施_
- [x] 13. 认证 `CO_auth`
  - 注册（用户名 3~32、密码≥8、bcrypt cost 12、8 色板轮询分配）/登录（JWT HS256 7 天）/`GET·PATCH /me`/登录限流（内存桶 10 次/分/IP，超限 429）
  - 统一错误文案「用户名或密码错误」
  - _Requirement: AC-B1.1~B1.3, AC-B4.1_
- [x] 14. 房间 `CO_rooms`
  - 建房（邀请码 8 位 Crockford base32）/我的房间/凭码加入（码错 4 次/时锁定）/owner 重置码/成员列表/移除成员
  - `require_member(rid)` 依赖注入做房间隔离（非成员 403）
  - _Requirement: AC-B2.1~B2.3_
- [x] 15. 任务与看板 `CO_tasks`
  - 任务 CRUD（status→done 记 completed_at/by）；单事务内 tasks 写 + rooms.version+=1 + task_events 插入
  - `GET /board?version=`（相同版本 `{unchanged:true}`；否则 tasks+members+stats：total/done/doing/overdue/due_today，逾期=due<今日且未完成）
  - `/stats` 按成员；`/tasks/batch` ≤200 条（P2 对接预留）
  - _Requirement: AC-B3.1~B3.2, AC-B6.1~B6.2_
- [x] 16. 模板 `CO_template`
  - `GET /template.xlsx`（表头契约 design.md §3.6）；`POST /import.xlsx`（表头严格校验 400；逐行反馈；负责人不存在→待分配；≤500 行）；`GET /export.xlsx`（+I 创建人/J 完成时间；删 I/J 可原样导回）
  - _Requirement: AC-B5.1~B5.2_
- [x] 17. P4 阶段门禁（本地 PG：`DATABASE_URL=postgresql://...`）
  - `cd collab_board/board_backend && ..\.venv\Scripts\python -m pytest tests_board -q`
  - 用例：auth 全分支/限流；rooms 隔离/码重置；tasks CRUD+version 递增+unchanged 短路+stats 口径（构造逾期/今日到期）；template 头校验/导入反馈/往返一致；batch 上限
  - _Requirement: 阶段门禁_

## P5 · 模块 B 前端 SPA，1.5~2 天

- [x] 18. `collab_board/board_frontend/`（Vite+React，独立 package.json）
  - 三页：登录注册 / 房间列表（房间卡+创建+凭码加入）/ 看板页（顶栏完成度环+成员图例+邀请码管理、看板|总列表双视图、筛选、新建/编辑抽屉、创建人色条+负责人色点）
  - 5 秒轮询（visibilitychange 暂停）；移动端响应式（三列改标签页）；纸墨视觉（暖纸底/墨色/靛墨强调/方角/无动画）
  - _Requirement: AC-B3.1~B3.3, AC-B4.1, AC-B6.1_
- [x] 19. P5 门禁：本地 `npm run build` 通过；本地起后端 Playwright 最小剧本（注册 A→建房→注册 B→加入→A 建待办→B 可见→B 完成→A 完成度变化）全绿
  - _Requirement: AC-B2.1, AC-B3.1_

## P6 · 轻量服务器部署（Lighthouse），0.5~1 天

> 前置：owner 已购服务器（2C2G/SSD 40G+/Ubuntu 22.04）。两个特性坑：**防火墙在腾讯云控制台不在服务器内**；**无域名无 HTTPS**（v1 IP 直连过渡）。

- [ ] 20. SSH 准入：本机 `ssh-keygen -t ed25519 -N "" -f ~/.ssh/lrb_board`；owner OrcaTerm（root）粘贴公钥添加命令；`sshd_config` 置 `PasswordAuthentication no` 重启 sshd（保持会话另开终端验证）
- [ ] 21. 环境侦察（不干扰已有服务）：`ss -tlnp`（8080 被占则全阶段改 8081+）、`docker ps`、`df -h`
- [ ] 22. 装 Docker+Compose 插件（apt 官方源）；`docker compose version` 自检
- [ ] 23. 控制台防火墙（owner 操作，字段：自定义 / TCP / 8080 / 0.0.0.0/0 / 允许）；过渡期建议来源改常用出口 IP 白名单
- [ ] 24. 构建上传：本地 `npm run build` 后 `scp -i ~/.ssh/lrb_board -r`（排除 node_modules/.git）至 `/www/wwwroot/collab_board/`
- [ ] 25. 配置 `.env`（chmod 600）：DATABASE_URL / JWT_SECRET（32 位随机）/ BOARD_ENV=prod
- [ ] 26. 拉起：`docker compose up -d --build`；本机与外网 `GET /api/health` 均 200
- [ ] 27. 冒烟与清理：冒烟账号建房建任务 → psql 删冒烟数据；建老板正式账户（凭据单独交付 owner，不入仓库）
- [ ] 28. 自动化：crontab `0 3 * * * /www/wwwroot/collab_board/deploy/backup.sh`（pg_dump 至 /www/backup 留 7 份）+ 恢复演练；更新/回滚 SOP 回填 AGENTS.md
- [ ] 29. P6 门禁：外网 health 200；冒烟写读成功且已清理；手机浏览器登录页可用；/www/backup 出现当日 dump；老板账户可登录
  - _Requirement: AC-B7.1_

## P7 · 配合调试剧本 + 验收归档，0.5~1 天（依赖 P3、P6）

- [ ] 30. 双账户双设备按剧本联调并逐行记录（实测/问题/结论）
  | # | 步骤 | 预期 |
  |---|---|---|
  | 1 | 设备1 注册老板 A，设备2 注册员工 B | 登录成功，颜色不同 |
  | 2 | A 建房「××公司落地跟踪」，邀请码发 B | 建房成功，完成度环 0% |
  | 3 | B 凭码加入 | 双端见同一房间与看板 |
  | 4 | A、B 各建 3 条待办（含负责人/截止/优先级/月份） | 色条区分创建人；5 秒互见 |
  | 5 | B 认领 1 条、推进 1 条、完成 1 条 | 双端 ≤5 秒一致；逾期红字可见 |
  | 6 | 总列表视图核对 | 总数/完成/逾期一致，可按人筛选 |
  | 7 | 模板填 5 行（含 1 行负责人乱填、1 行状态乱填）→导入 | 逐行反馈明确 |
  | 8 | 导出 xlsx→删 I/J 列→重新导入 | 往返一致，条数相符 |
  | 9 | A：成员统计、重置邀请码、移除 B | 旧码失效；B 历史任务色条保留 |
  | 10 | 手机浏览器（A）抽查看板 | 响应式可用 |
  | 11 | 断网 30 秒恢复 | 轮询自动恢复，无重复任务 |
  | 12 | 错误密码连续 6 次 | 第 5 次起 429 限流 |
  | 13 | 模块 A 本地：示例数据走完 建议→第一稿→问答→拆分→导出 | 月度 Sheet 恒等全 0；跳过路径可导旧版 |
  - _Requirement: AC-B1~B7 全量复核, AC-A1~A5 端到端_
- [ ] 31. 归档：剧本填写件 + 问题清单与修复 commit 对照 + 云端 health 截图 → `demo_output/联调记录_WB-CO-TR-20260820/`
  - _Requirement: AC-B7.2_

## P8 · 收尾与发布，0.5 天

- [ ] 32. guardian `EXPECTED_MODULES` 登记全部新文件；requirements 登记说明（板端依赖写 requirements-board.txt，主 requirements 加指引注释）
- [ ] 33. `.gitignore`：`.board_config.json`、`collab_board/board_frontend/node_modules|dist`（dist 默认忽略）
- [ ] 34. AGENTS.md 规整（唯一文档真源）
  - v27 条目：模块 A 状态机与端点、模块 B 服务与部署、联调记录位置、新护栏（AI 只出权重、房间隔离、模板表头契约、财报不上云边界重申）
  - 「规划中」节移除已落地项；「当前实现状态」表新增 CO_monthly_split / CO_monthly / collab_board 行；验收命令补板端测试（tests_board）；Web 化与运维节补 Lighthouse 部署与更新/回滚 SOP
  - v1.4 验收记录条目（P7 剧本结论 + 门禁结果），并**收录 spec 验收标准要点**（AC 清单在 36 删除 specs 前并入，之后以本文件为准）
  - 「GitHub 推送教程」补 Windows 主机变体：本机无 gh CLI，路径 C 用 `git credential fill` 取凭据管理器令牌 + Python urllib/curl 走同一 REST 流程（P0 实测可行，注意仓库名尾连字符拼 URL 必须补斜杠）
- [ ] 35. README 规整（AGENTS.md 为真源，README 面向使用者）
  - 「交付物」「核心闭环」补月度拆分与协同看板两能力
  - 3.6 导出交付改写：② 段四步向导（建议 → 第一稿 → 问答 → 拆分导出）+ 跳过逃生口说明
  - 新增「协同任务看板」功能小节（账户/房间/看板/模板/老板监督/移动端；访问地址 P6 交付后回填）
  - 「核心模块」「目录结构」补 collab_board/；「已知限制与路线图」勾销落地项
  - _Requirement: 交付物清单（AGENTS.md/README 同步）_
- [ ] 36. 删除 spec 工作文档：34/35 规整完成且 P7 归档物已落 `demo_output/联调记录_WB-CO-TR-20260820/` 后，删除 `specs/monthly-split-collab-board/` 整目录并随本次提交入库（v22 先例：整合后删除；桌面三份原件不属仓库，不动）
- [ ] 37. 全量门禁：
  ```bash
  .venv\Scripts\python -m pytest tests/ -q
  .venv\Scripts\python data\make_sample.py
  .venv\Scripts\python .hooks\project_guardian.py --quick
  bash .hooks/check.sh
  cd web_frontend && npm run build && npx playwright test
  ```
- [ ] 38. 推送：AGENTS.md「GitHub 推送教程」路径 A（ZCode 内被 Mimosa 拦按路径 B 分诊或路径 C 须 owner 当次授权）
- [ ] 39. 对照交付物清单核销（执行文档交付物清单；spec 验收标准已随 36 并入 AGENTS.md v27，以其为准）

## 交付物清单（对照核销）

- [ ] 本地端 v1.4：第一稿不下载 / 二轮提问 / AI 月度拆分恒等 / 含月度 Sheet 导出 / 跳过逃生口 / 状态恢复（FR-A1~A5）
- [ ] 轻量服务器 lirunbao-board：账户 / 房间邀请 / 共享看板 5 秒同步 / 颜色区分 / 总列表完成度 / 模板往返 / 老板监督 / 移动端（FR-B1~B6）
- [ ] 服务器地址可访问 + 每日备份生效 + 联调剧本记录归档（FR-B7）
- [ ] 全部测试与门禁绿；AGENTS.md/README 同步

## 执行日志

| 日期 | 阶段 | 结果 |
|---|---|---|
| 2026-08-20 | spec 创建 | 三份桌面文档整合为 requirements/design/tasks，待 owner 确认后进入 P0 |
| 2026-08-20 | 计划修订 | 按 owner 意见：P8 显式「AGENTS.md+README 规整」双任务；新增任务 36 在规整后删除 specs/ 目录（生命周期=执行期工作文档）；P0 暂存命令排除 specs 并单独成提交 |
| 2026-08-20 | P0 完成 | Mimosa 拦 8 项已分诊既有 high → 走路径 C（本机无 gh，改 git credential fill + urllib REST）；4715e1a v26.1 基线 + 6dd4dcd specs 两提交落远端，本地 reset 对齐、status 干净、guardian 0 错误 |
| 2026-08-20 | P1+P2 完成 | 引擎 20 测 + API 13 测 + Excel 4 测 = 37/37 三轮连跑全绿；guardian 0 错误；make_sample 通过。实现要点：①export_budget_3sheet meta 附加 plan_rows/top_summary（与 G 列同源）+ job 全量 meta getter；②修复两个真实竞态（迟到 watcher 回退状态机→stage 单向守卫；指纹重置后旧任务回写→job_id 守卫）；③GET 端点惰性补写快照消除轮询竞态；④尾差语义按设计文档「整体归位 argmax 月」（执行文档例句系简写笔误，测试已按权威契约断言）。Mimosa 对 PBT 固定种子 random.Random 的弱随机告警为误报（确定性要求，非加密） |
| 2026-08-20 | P3 完成 | 前端构建 ✓；e2e 36/36 全绿（基线 34 + 新增月度向导 2 例：无状态不渲染 + 离线全流程含下载事件）；全量 pytest 469 通过、失败全部为 AGENTS 记录的已知遗留（缺真实艺康 PDF / diagnostic / order_independence），无新增失败。Windows 主机环境修复三项：playwright webServer 命令补 Scripts/ 分支、Chromium 浏览器二进制安装、两个文件上传用例中文临时路径致 headless-shell 崩溃（0xC0000409）改 ASCII 前缀；另清除两处孤儿后端进程（曾致 e2e 生产库守卫误报，清洁复跑守卫通过） |
| 2026-08-20 | P4 完成 | 板端 6 模块 + 部署三件套 + tests_board 20 用例三轮连跑全绿（~10 秒）；guardian 0 错误（91 py 语法过）。本地测试环境：便携 PostgreSQL 16.9 解压版（tools/pgsql16，端口 54329、trust 仅本机、board_test 库）。调试中修三个真实 bug：①create/patch 响应缺成员映射；②Content-Disposition 中文文件名 latin-1 崩溃→RFC 5987；③**连接池外复用已归还连接致 idle-in-transaction 持锁**（曾使全量测试挂死 605 秒，lock_timeout 诊断定位后修复并写入护栏注释）；conftest 加 lock_timeout 快速失败 + 池显式关闭 |
| 2026-08-20 | P5 完成 | board_frontend SPA（三页/5 秒轮询+visibilitychange/看板+总列表双视图/筛选/抽屉/创建人色条/owner 邀请码与成员管理/移动端纵向堆叠/纸墨视觉）构建通过；e2e 最小剧本 11.5 秒全绿（双浏览器上下文模拟双账户，B 完成后 A 完成度轮询至 100%）。补设计缺口：后端新增 POST /api/rooms/join-by-code（凭码找房加入，前端无需预知房间 id）。e2e 基建：board_e2e 专用库+启动时清表；修 vite 绑 127.0.0.1、webServer 命令相对路径两项 Windows 环境问题 |
| 2026-08-20 | P1~P5 提交 | 路径 C REST 直推 7419d8f（52 文件：模块 A 三件新模块+改造五处+三份测试，模块 B 六后端模块+前端全套+部署三件套+20 板端测试），本地 fetch+reset 对齐、status 干净；二进制 xlsx 走 base64 content。本地 8765 后端已用新代码重启。**待办：P6 部署/P7 联调需 owner 配合（服务器、控制台防火墙、双设备）；P8 收尾（AGENTS.md/README 规整+删 specs）在 P7 归档后执行** |
| 2026-08-20 | P6 预备完成 | ①**修复 Dockerfile 致命缺陷**：容器 WORKDIR 在包内致 `import board_backend` 找不到 sys.path 根——改 WORKDIR /app + ENV PYTHONPATH=/app，并以容器确切 CMD 形态本机验证通过（health/db:true、SPA 首页、JS/CSS 资源全 200）；②生产形态浏览器冒烟 PROD-SMOKE-OK（FastAPI 静态托管 dist、无 vite 代理：注册→建房→建待办→完成→完成度 100%）；③部署 SSH 密钥已生成 ~/.ssh/lrb_board（ed25519，公钥待 owner 在 OrcaTerm 添加）；④collab_board/.env.example（BOARD_DB_PASSWORD/JWT_SECRET 模板+随机串生成命令）；⑤部署包 tools/lrb_board_deploy/collab_board.tar.gz（112KB/62 项，dist 随包、排除 node_modules/test-results/__pycache__）。**等 owner：服务器 IP + 控制台防火墙放行 8080 + OrcaTerm 加公钥** |
