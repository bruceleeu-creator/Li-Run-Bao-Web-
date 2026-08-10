# 利润宝 · macOS 安装使用指南

> 版本：v1.0.0  
> 智能体标识：WB-CO-TR-20260726  
> 适用：macOS 12+ · Python 3.11+  
> 分发：GitHub 私有仓库（不开源），客户财务数据只在本机处理  
> 注：Tk 桌面端已于 2026-08 移除，Web 为本项目唯一入口。

---

## 一、环境准备

### 1.1 系统要求

| 项 | 要求 | 推荐 |
|---|---|---|
| 操作系统 | macOS 12 Monterey+ | macOS 14 Sonoma+ |
| Python | 3.11 / 3.12 / 3.13 | Homebrew Python 3.13 |
| Node.js | 20+（仅首次构建前端需要） | 22 LTS |
| 磁盘 | ≥ 200 MB（含 .venv） | — |
| 网络 | 仅安装时需要 | 离线运行 |

### 1.2 安装 Homebrew Python（推荐）

打开「终端」App，执行：

```bash
# 如尚未安装 Homebrew
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 安装 Python 3.13
brew install python@3.13
```

> 本项目为 Web 应用，不需要安装 `python-tk@3.13`（Tk 桌面端已移除）。

---

## 二、首次安装

### 方式 A：双击脚本（推荐，适合财税/代账同事）

1. 解压发布包，得到目录 `利润宝-v1.0.0-macOS源码版_WB-CO-TR-20260726/`。
2. 进入 `scripts/` 目录。
3. **双击** `安装利润宝_WB-CO-TR-20260726.command`：
   - 首次双击时 macOS 可能弹出「无法打开，因为来自身份不明的开发者」。
   - 解决：右键点击文件 → 选择「打开」→ 在弹窗中再次点「打开」。
   - 也可在「系统设置 → 隐私与安全性」中点「仍要打开」。
4. 终端窗口自动出现安装进度：
   - 自动查找 Python（优先级：`LRB_PYTHON` → `python3.13` → `python3.12` → `python3.11` → `python3`）
   - 校验版本 ≥ 3.11
   - 在项目根创建 `.venv/`
   - 安装 `requirements.txt` 依赖
   - Web 后端健康自检（`/api/health`）
5. 看到 `✅ 安装完成` 即成功，按回车关闭窗口。

### 方式 B：终端命令（开发者）

```bash
cd 利润宝-v1.0.0-macOS源码版_WB-CO-TR-20260726
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
cd web_frontend && npm install && npm run build && cd ..   # 首次需构建前端
```

---

## 三、日常启动

### 双击启动

- 进入 `scripts/` 目录，**双击** `启动利润宝Web_WB-CO-TR-20260805160732.command`。
- 若 `.venv/` 缺失或未构建前端，脚本会提示先运行安装脚本/构建，不会启动失败的应用。
- 脚本自动在 `127.0.0.1:8765` 启动服务并打开浏览器。

### 终端启动

```bash
cd 利润宝-v1.0.0-macOS源码版_WB-CO-TR-20260726
.venv/bin/python -m web_backend.CO_run_WB-CO-TR-20260805160732
```

启动后浏览器访问 `http://127.0.0.1:8765`，包含：总览 / 财报导入 / 模板工作台 / 诊断 / 互动 / 第二稿与导出 / 设置 等工作区。

---

## 四、环境检查

双击 `scripts/环境检查_WB-CO-TR-20260726.command`，检查：

- Python 版本是否符合（≥ 3.11）
- `.venv/` 是否就绪
- 关键依赖（openpyxl/matplotlib/reportlab/python-docx/requests/fastapi/uvicorn/pytest）是否安装
- Web 后端 `/api/health` 是否正常

退出码：`0=全部通过`、`1=通过但有警告`、`2=错误（阻断运行）`。

---

## 五、质量检查

双击 `scripts/质量检查_WB-CO-TR-20260726.command`，依次运行：

1. 全量 pytest 回归（tests/）
2. 生成示例数据（make_sample.py）
3. Web 后端健康检查（/api/health）
4. `project_guardian.py --quick` 守护脚本快速检查
5. `check.sh` 项目自检

任一步失败会显示 `❌`，全部通过显示 `✅ 通过：N 失败：0`。

---

## 六、数据安全与合规

- **客户财务数据只在本机处理**，不触网上传。
- AI 引擎为**可选增强**：未配置 Base URL/Key/Model 时由规则引擎兜底，离线可跑通完整闭环。
- 配置文件 `.ai_config.json` 已在 `.gitignore` 中忽略，不会进入仓库（内含持久化的 API Key，勿外传）。
- 所有优化建议限于**合法税务筹划**范畴；严禁违规筹划。

---

## 七、常见问题

### Q1：双击 `.command` 提示「无法打开」

macOS Gatekeeper 拦截。**右键 → 打开 → 仍要打开** 即可。或在「系统设置 → 隐私与安全性」中点击「仍要打开」。

### Q2：安装脚本提示「未找到可用的 Python 3.11+」

按 §1.2 安装 Homebrew Python 3.13；或设置环境变量指向已有 Python：

```bash
export LRB_PYTHON=/path/to/python3.13
```

### Q3：启动后提示「未找到前端构建产物」

需先构建前端：进入 `web_frontend/` 执行 `npm install && npm run build`。或重新运行安装脚本（安装脚本会自动构建）。

### Q4：导入扫描件 PDF 时提示「请配置 AI」

2023/2024 年审计报告等扫描件（无文字层）需 DeepSeek 解析：在「设置」页或导入页「配置 AI」填入 Base URL / 模型 / API Key。配置持久化到本机 `.ai_config.json`，重启免重输。

### Q5：如何运行真实用户模板验收

仅开发者场景，本机已有真实用户模板时：

```bash
export LRB_REAL_TEMPLATE_PATH=/path/to/企业成本费用计划表（模板）.xlsx
.venv/bin/python -m pytest tests/test_t7_acceptance.py -q
```

未设置时该用例自动跳过，不影响其余测试覆盖。

---

## 八、构建发布包（开发者）

```bash
bash scripts/构建发布包_WB-CO-TR-20260726.sh
```

将在 `release/` 下生成：

- `利润宝-v1.0.0-macOS源码版_WB-CO-TR-20260726/`（脱敏发布目录）
- `利润宝-v1.0.0-macOS源码版_WB-CO-TR-20260726.zip`
- `利润宝-v1.0.0-macOS源码版_WB-CO-TR-20260726.zip.sha256`

构建完成后会自动扫描敏感字符串（本机路径/凭据/微信路径），命中即中止。

---

如遇本文档未覆盖的问题，请提交 Issue 到私有仓库或联系交付负责人。
