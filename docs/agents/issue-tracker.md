# Issue tracker: Local Markdown

本仓库的 issue 与规格（也可理解为 PRD）以 Markdown 文件形式存放在 `.scratch/`。

## 约定

- 每个功能一个目录：`.scratch/<feature-slug>/`
- 规格文件：`.scratch/<feature-slug>/spec.md`
- 实现类 issue 每个工单一个文件，位于 `.scratch/<feature-slug>/issues/<NN>-<slug>.md`，从 `01` 开始编号 —— 不要合并成单个工单文件
- 分诊状态记录在每个 issue 文件顶部附近的 `Status:` 行（角色字符串见 `triage-labels.md`）
- 评论与对话历史以 `## Comments` 标题追加到文件末尾

## 当技能说 "publish to the issue tracker"

在 `.scratch/<feature-slug>/` 下新建文件（必要时创建目录）。

## 当技能说 "fetch the relevant ticket"

读取所引用路径下的文件。用户通常会直接传路径或 issue 编号。

## Wayfinding 操作

供 `/wayfinder` 使用。**map** 是一个文件，每个 **child** 对应一个工单文件。

- **Map**: `.scratch/<effort>/map.md` —— Notes / Decisions-so-far / Fog 正文。
- **Child ticket**: `.scratch/<effort>/issues/NN-<slug>.md`，从 `01` 开始编号，工单问题写在正文中。`Type:` 行记录工单类型（`research`/`prototype`/`grilling`/`task`）；`Status:` 行记录 `claimed`/`resolved`。
- **Blocking**: 顶部附近 `Blocked by: NN, NN` 行。只有当其列出的每个文件都是 `resolved` 时，该工单才解除阻塞。
- **Frontier**: 扫描 `.scratch/<effort>/issues/`，找出 open、未阻塞且未认领的文件；按编号优先。
- **Claim**: 先写 `Status: claimed` 并保存，再开始任何工作。
- **Resolve**: 在 `## Answer` 标题下追加答案，将 `Status: resolved`，然后在 `map.md` 的 Decisions-so-far 中追加一个上下文指针（gist + 链接）。
