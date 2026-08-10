# 利润宝 · 项目守护者（Project Guardian）

## 这是什么

一个集成了 **Git Hook 自动检查** 和 **本地自检脚本** 的质量保障系统，在你开发过程中自动发现：
- 文档与代码不一致
- Python 语法错误
- 架构依赖方向违规
- 合规红线表述
- 技术选型偏离
- 项目记忆过期

## 工作方式

### 1. 提交前自动检查（pre-commit Hook）

每次执行 `git commit` 时会自动运行快速检查，发现问题会阻止提交：

```bash
git commit -m "feat: add xxx"
# → 自动运行 project_guardian.py --quick
# → 全部通过才允许提交
```

如需跳过检查：`git commit --no-verify` 或 `git commit -n`

### 2. 手动运行完整检查

```bash
bash .hooks/check.sh          # 完整检查
bash .hooks/check.sh --quick  # 快速模式（仅语法+架构）
bash .hooks/check.sh --list   # 查看所有检查项
python3 .hooks/project_guardian.py  # 同上
```

## 退出码含义

| 退出码 | 含义 |
|--------|------|
| 0 | 全部通过 |
| 1 | 通过但有警告 |
| 2 | 存在必须修复的错误 |

## 检查项清单

1. **模块完整性** — 开发方案已声明的模块是否都存在
2. **Python 语法** — 逐文件 AST 解析
3. **架构依赖** — 层间依赖方向（core 不能导入 gui/main）
4. **合规红线** — 检测违禁表述（自动识别"严禁/禁止"语境）
5. **ADR 遵循** — 技术选型偏离检查
6. **AGENTS.md 时效性** — 提醒更新项目记忆
7. **导出完整性** — core/__init__.py 是否导出核心模块

## 维护

- 扩展检查项：编辑 `.hooks/project_guardian.py`，新增方法
- 修改白名单：调整 `FORBIDDEN_PATTERNS` 或 `LAYER_DEP_RULES`
- 更新模块清单：修改 `EXPECTED_MODULES`
- 文件路径均使用相对项目根目录的路径
