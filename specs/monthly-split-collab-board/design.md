# Design · 年度预算二段式生成与月度拆分 + 协同任务看板

| 项目 | 内容 |
|---|---|
| Spec 名称 | monthly-split-collab-board |
| 版本 | v1.0（2026-08-20） |
| 来源文档 | 桌面《利润宝-设计文档-预算月度拆分与协同看板-20260820.md》（已定稿，本文为 spec-workflow 整理版） |
| 代码锚点（已核实） | `web_backend/CO_budget_export_job_WB-CO-TR-20260810.py:260 start_budget_export_job`；`web_frontend/src/CO_app_WB-CO-TR-20260805160732.tsx:2095 ExportPage`；`.hooks/project_guardian.py:35 EXPECTED_MODULES` |

---

## 1. 总体架构

```
┌───────────────────── 本地端（现有，改造） ─────────────────────┐
│ React+Vite（web_frontend/）「第二稿与导出」页 ② 段四步向导        │
│ FastAPI（web_backend/，仍仅绑 127.0.0.1:8765）                  │
│   ├─ CO_budget_export_job（现有任务管线 → 产出第一稿）           │
│   ├─ CO_monthly（新）：问答 / AI 权重拆分 / 恒等校验 / 下载       │
│   └─ SQLite app.db：新增 monthly_budget_state 表                │
│ core/CO_monthly_split（新）：权重→金额、尾差、校验、规则兜底      │
│ 客户财报数据不出本机（红线不变）                                  │
└──────────────────────────────────────────────────────────────┘
┌───────────────────── 云端（全新，独立部署单元） ─────────────────┐
│ collab_board/（新顶层目录，不依赖 core/ 与 web_backend/）        │
│   ├─ board_backend/  FastAPI：账户/房间/看板任务/模板导入导出     │
│   │    └─ 同进程托管 board_frontend 构建产物（单服务）           │
│   ├─ board_frontend/  React+Vite 轻量 SPA（登录/房间/看板）      │
│   └─ Dockerfile + docker-compose.yml（Lighthouse 自部署）        │
│ PostgreSQL 16（同机容器，数据卷持久化，仅容器内网）               │
│ 访问：v1 IP 直连 http://<公网IP>:8080；正式形态 域名+Nginx+TLS   │
└──────────────────────────────────────────────────────────────┘
```

**架构决策**

1. **协同看板独立云服务，不并入本地 FastAPI**——本地端承载财报（不出本机红线）且仅绑 127.0.0.1；协同天然要求公网多账户。安全边界清晰：云端只存任务与进度，云端故障不影响本地闭环。
2. **云端选型 Lighthouse 自部署 + Docker Compose（owner 指定）**——包月成本可控、无平台绑定、Compose 一键拉起/升级/回滚；代价与对策：自运维（每日 pg_dump 留 7 份 + SSH 密钥禁密码）、无域名无 HTTPS（v1 IP 直连过渡，限流+白名单缓解，上域名仅前置 Nginx 零改码）、部署前侦察端口绝不抢占（轻量云防火墙在腾讯云控制台而非服务器内）。弃选 CloudBase 云托管（按量计费+平台绑定）与 Serverless（openpyxl/轮询场景容器更简单）。
3. **模块 A 严格执行数字哲学**——AI 只决定「形状」（权重/节奏标签），金额全部由确定性引擎计算并恒等校验；AI 文案数字走白名单告警。
4. **离线优先**——模块 A 题库与拆分均有规则兜底，未配 AI 全流程可用。

## 2. 模块 A 设计

### 2.1 流程与状态机

改造对象：`CO_app.tsx` 中 `ExportPage`（2095 行起）② 段；后端 `CO_budget_export_job` 保留复用。

```
[编制建议生成+勾选]（现状不变）
    │ 点击「生成预算第一稿」
    ▼
draft_job（复用现有管线 prepare→extract_top→extract_lines→apply_advice→write）
  产出：①第一稿 xlsx（存 workspaces/exports，不触发下载）②BudgetPlan 快照 JSON + 顶部指标 + meta
    ▼ stage=draft ──自动──▶ stage=questions（AI 出题 → 失败回退规则题库）
    │ 用户作答/全部默认
    ▼ stage=answered ──「开始月度拆分」──▶ split_job（AI 权重→引擎算金额→恒等校验；重试≤3→回退 rule）
    ▼ stage=ready ──主按钮──▶ 第一稿 xlsx 追加「月度执行计划」Sheet → 下载
    └─ 逃生口「跳过月度拆分，导出旧版」──▶ 现有异步导出原样（stage=skipped）
```

**状态存储**：SQLite 新表 `monthly_budget_state`（每 session_version 一行 UPSERT）；重新生成建议或勾选集变化 → 行删除重置（比较勾选指纹 = sha256(排序后 `{row,budget_amount}` 列表)，对顺序不敏感、对金额敏感）。

```sql
CREATE TABLE IF NOT EXISTS monthly_budget_state (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_version TEXT NOT NULL UNIQUE,
  stage TEXT NOT NULL,              -- draft|questions|answered|splitting|ready|failed|skipped
  advice_fingerprint TEXT NOT NULL,
  draft_job_id TEXT DEFAULT '',
  draft_path TEXT DEFAULT '',
  plan_snapshot TEXT DEFAULT '',    -- JSON：[{row,subject,expense_name,annual}] + 顶部指标
  draft_meta TEXT DEFAULT '',
  question_source TEXT DEFAULT '',  -- ai|rule
  questions TEXT DEFAULT '',        -- JSON：[{id,type,title,options[],default,placeholder}]
  answers TEXT DEFAULT '',          -- JSON：[{id,value}]
  split_job_id TEXT DEFAULT '',
  split_mode TEXT DEFAULT '',       -- ai|rule
  split_result TEXT DEFAULT '',     -- JSON：matrix + checks + warnings
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
```

### 2.2 API 设计（`web_backend/CO_monthly_WB-CO-TR-20260820.py`）

| 方法与路径 | 说明 |
|---|---|
| `POST /api/export/budget/draft/jobs` | 启动第一稿任务（复用 `start_budget_export_job(advice_items)`，mode="draft"：完成时保存快照与路径，不返回下载）。体 `{advice_items:[...]}` |
| `GET .../draft/jobs/active` / `.../jobs/{job_id}` | 查询进度（沿用 job 契约 + `summary`：营收/费用总盘子/费用率/非空行数/已填建议数） |
| `GET /api/export/budget/monthly/state` | 月度流程总状态（stage + 各步骤产物有无），刷新恢复用 |
| `POST /api/export/budget/monthly/questions` | 生成并持久化问题集（AI 优先回退规则）；幂等 |
| `GET /api/export/budget/monthly/questions` | 读取问题集（含 `source: ai|rule`） |
| `POST /api/export/budget/monthly/answers` | 提交答案；必填校验后置 stage=answered |
| `POST /api/export/budget/monthly/split/jobs` | 启动拆分任务（queued→running→completed/failed） |
| `GET .../split/jobs/{job_id}` | 轮询拆分进度 |
| `GET /api/export/budget/monthly` | 最新拆分结果 `{matrix, checks:{row_failures,total_gap}, mode, warnings[], generated_at}` |
| `GET /api/export/budget/monthly/download` | 下载最终文件（追加月度 Sheet 另存；stage≠ready 返回 409） |
| 现有 `POST /api/export/budget/jobs` + `/download` | **保留不动** = 跳过拆分导出旧版路径 |

错误语义：未导入财报 400；AI 未配置不报错（回退规则，`source/mode` 标 `rule`）；恒等校验失败属系统缺陷路径，任务 failed 提示重试。

### 2.3 拆分引擎（新 `core/CO_monthly_split_WB-CO-TR-20260820.py`，纯标准库）

**数据契约**

```python
@dataclass
class MonthlyRow:
    row: int; subject: str; expense_name: str
    annual: float          # 第一稿年度预算（元）
    months: list[float]    # 长度 12，元级
    shape: str             # uniform|front_load|back_load|peak|lump|custom
    shape_note: str        # 依据（"年终奖·春节月集中" 等）

@dataclass
class SplitResult:
    rows: list[MonthlyRow]
    month_totals: list[float]  # 12 列合计
    grand_total: float         # = Σannual（恒等）
    mode: str                  # ai|rule
    warnings: list[str]        # 数字白名单告警等
```

**数学规则（恒等硬门槛）**

1. AI/规则只产出每行形状或 12 个权重 `w[i] ≥ 0`；引擎归一化 Σw=1 后 `months[i] = round(annual × w[i])`（四舍五入到元）。
2. **尾差归位**：`diff = annual − Σmonths` 加到 `argmax(w)` 月（非零行），保证 `Σmonths == round(annual)` 精确相等；annual=0 行 12 个月全 0。
3. 整表校验：逐行 Σmonths==round(annual)、`Σ(month_totals) == Σ(round(annual))`；任一失败 → 任务 failed（AI 路径带错误反馈重试 ≤3 次）。

**AI 交互契约**（`CO_ai` 新增两函数，走 `ai_engine.chat_result`，thinking disabled，max_tokens ≥ 4096）

- `generate_monthly_questions(plan_snapshot, hints)` → JSON 数组 4~6 题（题数、每题有 default、type ∈ single|text 校验不过 → 回退规则题库）。
- `generate_monthly_weights(plan_snapshot, answers, hints)` → `{"rows":[{"row":N,"shape":"...","weights":[12 个小数],"note":"..."}]}`；行数不全或权重非法（负数/Σw 偏离 1 超 0.05/NaN）→ 失败重试。提示词硬规则：只允许引用输入年度金额与科目名；说明文字不得出现金额数字（出现 → warnings 白名单告警，不改数）。

**规则兜底（离线可用，AI 重试终态）**

| 行识别（费用项目关键词） | 形状 |
|---|---|
| 工资/薪酬/社保/公积金/五险/房租/租赁/物业/折旧/摊销/利息/通讯/网络 | uniform（答案声明「不平摊」时整行仍 uniform 并记 warning） |
| 年终奖/奖金/提成（依答案「年终奖 N 个月·春节前」） | peak：春节月（预算年度春节日期取 1 或 2 月）权重 ≈ N/(12+N)，其余均摊 |
| 广宣/推广/广告/营销（依答案「旺季前置 K 月」） | front_load / back_load：旺季窗口 K 月前置抬升（窗口权重 ×1.8，窗口外按剩余摊） |
| 答案给出的一次性支出（费用项匹配 + 月份 + 金额） | lump：指定月全额（金额仅校验 ≤ 年度，不改年度值） |
| 其余未识别行 | uniform |

规则题库（回退源，6 题全带默认项）：收入季节性、薪酬节奏、固定费用平摊、广宣投放、一次性大额支出（月份+金额+费用项）、预算起始月。

### 2.4 Excel「月度执行计划」Sheet

在第一稿工作簿末尾追加，不改任何现有 Sheet（`read_template` 只校验指定 Sheet 名与 84 行结构，天然兼容追加）。

| 列 | 内容 | 形式 |
|---|---|---|
| A/B/C | 行号 / 科目名称 / 费用项目 | 数值 / 文本 |
| D | 年度预算金额（=快照 annual，与费用预算表 G 同源） | 数值 |
| E~P | 1~12 月金额 | 数值（引擎写入） |
| Q | 月度合计 | 公式 `=SUM(Ei:Pi)` 可复算 |
| R | 校验（合计−年度） | 公式 `=Qi-Di`，全 0，非 0 条件标红 |
| 末两行 | 月度总计行（E~P SUM）+ 说明行（ai/rule、生成时间、恒等结论） | 公式 + 文本 |

样式沿用纸墨台账（表头加粗+发丝边框，禁花哨填充）；文件名 `{企业}{年段}费用预算三表（含月度拆分）.xlsx`。

### 2.5 前端设计（ExportPage ② 段）

- ② 段内部竖向四步骤（沿用现有 panel/field 体系，不动 ① 段与分割线）：①费用编制建议（不变）→ ②预算第一稿（摘要卡，无下载）→ ③月度拆分问答（单选组+文本域+「全部按推荐项」+「跳过月度拆分（导出旧版）」次级链接）→ ④拆分结果与导出（进度条复用 budgetProgress 状态模式、月度透视预览+行级 ✓、warnings 提示条、主按钮）。
- 挂载时 `GET monthly/state` 按 stage 恢复；勾选变化 → 警示条。
- API 客户端新增 7 函数：startBudgetDraft / getMonthlyState / genMonthlyQuestions / submitMonthlyAnswers / startMonthlySplit / pollMonthlySplit / downloadMonthlyBudget。
- **e2e 契约**：`diagnosis_flow.spec.ts:78`「导出测算模型」卡不动；预算 spec 改走新步骤序列；新增跳过路径用例。
- 回调全部 useCallback（防无限重渲染，项目既有坑）。

## 3. 模块 B 设计

### 3.1 服务与目录结构

```
collab_board/
├─ board_backend/                 # FastAPI（独立，不 import core/ 与 web_backend/）
│  ├─ CO_app_WB-CO-TR-20260820.py     # create_app()：/api 路由 + StaticFiles 托管前端 + /api/health
│  ├─ CO_db_WB-CO-TR-20260820.py      # psycopg 连接池 + schema 初始化 + 迁移
│  ├─ CO_auth_WB-CO-TR-20260820.py    # 注册/登录/JWT/限流/当前用户
│  ├─ CO_rooms_WB-CO-TR-20260820.py   # 房间 CRUD/邀请码/成员管理/权限隔离依赖
│  ├─ CO_tasks_WB-CO-TR-20260820.py   # 任务 CRUD/看板聚合(轮询)/统计/批量创建
│  ├─ CO_template_WB-CO-TR-20260820.py# 模板下载/导入解析/导出（openpyxl）
│  └─ requirements-board.txt          # fastapi uvicorn psycopg[binary] bcrypt pyjwt openpyxl python-multipart
├─ board_frontend/                # React+Vite 轻量 SPA
├─ Dockerfile                     # python:3.12-slim → uvicorn 0.0.0.0:8080
├─ docker-compose.yml             # db(postgres:16-alpine, 数据卷, 仅内网) + app(8080 对外)
├─ nginx/board.conf               # 域名期 443 反代（IP 直连期不启用）
└─ deploy/                        # backup.sh（pg_dump 留 7 份）+ 发布/回滚脚本
```

新增依赖 psycopg/bcrypt/pyjwt/python-multipart 均不在禁用清单；登记 guardian `EXPECTED_MODULES` 与 requirements 说明。**该服务不配置任何出站请求**；未来若需出站仅允许 http/https 且校验 host 拒绝环回/私网。

### 3.2 数据模型（PostgreSQL 16，DDL 由 CO_db 启动幂等执行）

```sql
CREATE TABLE IF NOT EXISTS users (
  id BIGSERIAL PRIMARY KEY, username VARCHAR(32) UNIQUE NOT NULL,
  display_name VARCHAR(64) NOT NULL, password_hash TEXT NOT NULL,   -- bcrypt cost 12
  color VARCHAR(7) NOT NULL,                                        -- #RRGGBB，注册时 8 色板轮询
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), last_login_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS rooms (
  id BIGSERIAL PRIMARY KEY, name VARCHAR(64) NOT NULL,
  owner_id BIGINT NOT NULL REFERENCES users(id),
  invite_code VARCHAR(12) UNIQUE NOT NULL,     -- 8 位 Crockford base32，owner 可重置
  version BIGINT NOT NULL DEFAULT 0,           -- 任务变更 +1（轮询增量判据）
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS room_members (
  room_id BIGINT NOT NULL REFERENCES rooms(id), user_id BIGINT NOT NULL REFERENCES users(id),
  role VARCHAR(10) NOT NULL CHECK (role IN ('owner','member')),
  joined_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (room_id, user_id)
);
CREATE TABLE IF NOT EXISTS tasks (
  id BIGSERIAL PRIMARY KEY, room_id BIGINT NOT NULL REFERENCES rooms(id),
  title VARCHAR(200) NOT NULL, detail TEXT NOT NULL DEFAULT '',
  status VARCHAR(10) NOT NULL DEFAULT 'todo' CHECK (status IN ('todo','doing','done')),
  assignee_id BIGINT REFERENCES users(id),     -- NULL=待分配
  creator_id BIGINT NOT NULL REFERENCES users(id),
  due_date DATE, priority VARCHAR(6) NOT NULL DEFAULT 'mid' CHECK (priority IN ('high','mid','low')),
  month_tag VARCHAR(7),                        -- 'YYYY-MM'，对接月度预算计划
  sort_no BIGINT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ, completed_by BIGINT
);
CREATE INDEX IF NOT EXISTS idx_tasks_room ON tasks(room_id, status);
CREATE TABLE IF NOT EXISTS task_events (      -- 操作流水（审计/联调排查）
  id BIGSERIAL PRIMARY KEY, room_id BIGINT NOT NULL, task_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL, action VARCHAR(20) NOT NULL,   -- create|update|status|delete|import
  payload JSONB NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

不变式：任务写操作在单事务内完成「改 tasks + rooms.version+=1 + 插 task_event」。

### 3.3 API 设计（前缀 /api，Bearer JWT；除 auth/health/template 下载外均需登录）

| 分组 | 接口 | 说明 |
|---|---|---|
| auth | `POST /auth/register` `{username,password,display_name?}` | 用户名 3~32（字母数字_-）、密码 ≥8；返回 `{token,user}`；分配颜色 |
| auth | `POST /auth/login` | JWT HS256 7 天（secret=env JWT_SECRET）；失败统一「用户名或密码错误」 |
| auth | `GET /me` / `PATCH /me` | 查看/修改 display_name、color |
| rooms | `POST /rooms` / `GET /rooms` | 建房（owner+邀请码）/ 我加入的房间（含角色、完成度摘要） |
| rooms | `POST /rooms/{rid}/join` `{invite_code}` | 凭码加入（码错 4 次/小时锁定） |
| rooms | `POST /rooms/{rid}/invite/refresh` | 仅 owner，旧码失效 |
| rooms | `GET /rooms/{rid}/members` / `DELETE .../members/{uid}` | 成员列表 / 移除（仅 owner；不动历史任务） |
| tasks | `GET /rooms/{rid}/board?version=n` | 聚合快照 `{version,tasks[],members[],stats{total,done,doing,overdue,due_today}}`；version 相同返回 `{unchanged:true}` |
| tasks | `POST .../tasks` / `PATCH .../tasks/{tid}` / `DELETE .../tasks/{tid}` | CRUD；status→done 记 completed_at/by；LWW |
| tasks | `POST .../tasks/batch` | 批量创建 ≤200 条（P2 本地端对接预留） |
| tasks | `GET .../stats` | 按成员统计（创建/完成/逾期） |
| template | `GET /template.xlsx` | 空模板下载 |
| template | `POST /rooms/{rid}/import.xlsx` (multipart) | 解析批量建任务，逐行 `{row,status,reason}` |
| template | `GET /rooms/{rid}/export.xlsx` | 当前看板导出（+创建人/完成时间两列） |

路径参数 `rid` 一律经依赖注入校验 membership，非成员 403。登录限流：内存令牌桶 10 次/分/IP，超限 429。

### 3.4 同步机制

- 客户端轮询 `GET /board?version=`，间隔 5 秒（`visibilitychange` 隐藏时暂停）；version 变化拉全量（v1 ≤1000 任务全量足够）。
- 编辑冲突 LWW + 服务端 updated_at/操作人展示；task_events 可追溯。
- v2 升级路径：SSE 推送 rooms.version（接口已按版本号设计，前端仅替换轮询源）。

### 3.5 看板 UI（board_frontend，三页）

1. **登录/注册页**：用户名+密码；注册后直接进房间列表。
2. **房间列表页**：房间卡（名称/角色/完成率环/成员色点列）；创建房间、凭码加入。
3. **看板页**：顶栏（房间名·完成度环（总/完成/逾期/今日到期）·成员颜色图例·owner 邀请码管理）；工具条（看板|总列表切换、筛选负责人/状态/月份、新建待办）；看板视图三列卡片（创建人色条左缘 3px+标题+负责人色点·昵称+截止日逾期红字+优先级角标+月份标签，点击开编辑抽屉）；总列表视图单页表格+底部合计行；移动端三列改横向标签页、新建/编辑全屏抽屉。
- 视觉延续纸墨台账（暖纸底、墨色、靛墨强调、方角、无动画）。
- 颜色规则：**卡片色条=创建人颜色**（creator 被移出房间后按离开时快照色显示）；8 色板：靛墨/赭石/黛绿/绛紫/秋香/棕褐/黛蓝/茹蓝，房间内按加入顺序去重分配。

### 3.6 跟踪进度表模板契约（openpyxl）

| 列 | 表头 | 校验/映射 |
|---|---|---|
| A | 任务名称* | 必填，≤100 字，超长截断提示 |
| B | 详细说明 | 自由文本 |
| C | 负责人（用户名） | 房间内不存在 → 导入成功但 assignee 空，反馈「待分配」 |
| D | 状态 | 待办/进行中/已完成 容错映射，非法 → todo |
| E | 截止日期 | YYYY-MM-DD 或 Excel 日期；非法 → 空 |
| F | 优先级 | 高/中/低 → high/mid/low，非法 → mid |
| G | 月份归属 | YYYY-MM 正则，非法 → 空 |
| H | 备注 | 自由文本 |
| I/J | （仅导出）创建人 / 完成时间 | 只读 |

导入上限 500 行/次；表头严格匹配（前 8 列名一致才受理，否则 400 提示下载官方模板）；往返一致：导出删 I/J 列可原样导回。

### 3.7 云端部署（Lighthouse）

- **规格建议**：2C2G / SSD 40G+ / Ubuntu 22.04，地域就近（免备案选香港/新加坡）。
- **形态**：Compose 单机——db（postgres:16-alpine，数据卷，仅容器内网）+ app（0.0.0.0:8080，depends_on db 健康检查）；IP 直连期 uvicorn 直接对外；上域名后宿主机前置 Nginx 443 反代终结 TLS，应用零改码。
- **部署顺序**：①SSH 准入（本机 ed25519 专用密钥；owner OrcaTerm 粘贴公钥命令；sshd_config 禁密码）②环境侦察（ss -tlnp / docker ps / df -h，8080 被占则换 8081+，绝不抢占）③装 Docker+Compose ④**控制台防火墙**放行 TCP 8080（轻量云特性：防火墙在控制台不在服务器内）⑤本地 build 前端后 scp 上传（服务器不装 Node）⑥`.env` chmod 600（DATABASE_URL/JWT_SECRET≥32 位随机/BOARD_ENV=prod）⑦`docker compose up -d --build` + health 探活 ⑧冒烟→SQL 清冒烟数据→建老板正式账户（凭据单独交付不入库）⑨crontab 每日 3:00 pg_dump 留 7 份 + 恢复演练 ⑩更新/回滚 SOP（build app && up -d app；回滚=上一镜像标签重新 up）。
- **域名+HTTPS 升级路径（可后补）**：A 记录→IP（大陆先 ICP 备案）→腾讯云免费证书（TrustAsia DV）→启用 nginx/board.conf→控制台放行 80/443、收回 8080。
- **安全基线**：SSH 密钥禁密码；PG 不映射公网；JWT_SECRET≥32 位随机；.env 600；不留调试端点；IP 直连过渡期建议来源 IP 白名单。

### 3.8 配合调试（交付必含）

双账户双设备联调覆盖：注册登录（含限流）→建房/加入/重置码→双端 5 秒同步→颜色区分→总列表完成度一致→模板往返→owner 监督视角与移除成员→手机浏览器→断网重连恢复。剧本详表见 tasks.md P7。

### 3.9 与本地端衔接（P2 预留，v1 只留接口）

本地「设置」页新增「协同看板」配置（看板地址+邀请码+老板 token，存 `.board_config.json`，gitignore）；导出测算模型页「同步执行清单到协同看板」按钮 → `POST {board}/api/rooms/{rid}/tasks/batch`。地址校验仅 http/https 且 host 非环回/私网；失败只提示不阻断导出。看板侧 v1 即实现 batch 接口，P2 零服务端改动。

## 4. 测试策略

**模块 A（本地 pytest）**
- `tests/CO_test_monthly_split_WB-CO-TR-20260820.py`：权重归一/尾差归位/恒等（0 行、大金额、PBT 随机 100 行固定种子）、规则形状表驱动、AI 权重解析容错（缺行/负权重/Σ 偏离→重试→回退）、题库结构校验、数字白名单告警。
- `tests/CO_test_monthly_api_WB-CO-TR-20260820.py`（TestClient）：draft 不下载、state 恢复各 stage、answers 校验、split 假 AI 成功/失败回退、download 409、跳过路径走旧端点、勾选指纹变化重置。
- Excel 往返：read_template 读回不报错 + Q/R 公式 openpyxl 复算为 0。
- 全量回归 + 前端构建 + Playwright e2e（34 用例基线不降）。

**模块 B（pytest，本地 PG）**
- auth 全分支/限流；rooms 隔离（非成员 403）/码重置；tasks CRUD+version 递增+unchanged 短路+stats 口径（逾期=due<今日且未完成）；template 头校验/导入反馈/往返一致；batch 上限。
- e2e：本地起服务 Playwright 走「注册→建房→加入→同步」最小剧本（云端联调按 3.8 剧本人工执行归档）。

## 5. 风险与对策

| 风险 | 对策 |
|---|---|
| AI 拆分质量差/幻觉金额 | AI 只出权重不出金额；shape_note 可解释；预览+重新拆分；失败回退规则 |
| 恒等校验边角（0 元行、极小金额） | 0 行直接全 0；round+尾差归位单测覆盖 |
| 现有 e2e/断言破坏 | 「导出测算模型」契约不动；预算 spec 同步改造；34 用例基线为门禁 |
| 无域名期明文 HTTP | 来源 IP 白名单 + 登录限流；尽快域名+HTTPS |
| 服务器被爆破/弱口令 | SSH 密钥禁密码；仅放行必要端口 |
| 服务器数据丢失 | 每日 pg_dump 留 7 份 + 恢复演练 |
| 邀请码滥用/爆破 | 加入 4 次/小时锁定 + 登录限流 + owner 重置码 |
| 与服务器已有服务冲突 | 部署前侦察，冲突换端口，绝不抢占 |
| 新依赖违反白名单 | 仅 psycopg/bcrypt/pyjwt/python-multipart，登记 guardian 与 requirements |
| Windows 主机开发差异 | 板端本地 uvicorn+本机 PG；测试命令双写（Scripts/ 与 bin/） |

## 6. 既有约束遵循清单

- 文件命名 `_WB-CO-TR-20260820` 后缀；`core/` 不反向依赖上层；web 层复用 core 不重写领域逻辑；collab_board 不 import core/web_backend。
- 离线优先（AI 全可选、规则兜底）；数字白名单只提示不改数；合规红线词不出现。
- 本地端仍仅绑 127.0.0.1:8765；财报数据不上云；`.board_config.json` 入 gitignore。
- AGENTS.md 同步更新（新模块、新表、新护栏）；提交前 guardian --quick + 全量 pytest + 前端构建。
