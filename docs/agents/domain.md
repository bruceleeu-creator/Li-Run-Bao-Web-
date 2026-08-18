# Domain Docs

工程技能在探索代码库时如何消费本仓库的领域文档。

## 探索前先读这些

- 仓库根目录的 **`CONTEXT.md`**，或
- 若存在 **`CONTEXT-MAP.md`** —— 它指向每个上下文各自的 `CONTEXT.md`。逐个读取与主题相关的部分。
- **`docs/adr/`** —— 读取与你即将工作的领域相关的 ADR。多上下文仓库还需检查 `src/<context>/docs/adr/` 下上下文范围的决策。

如果以上文件都不存在，**静默继续**。不要标注缺失，也不要主动建议创建。`/domain-modeling` 技能（通过 `/grill-with-docs` 和 `/improve-codebase-architecture` 触发）会在术语或决策真正确定时懒创建它们。

## 文件结构

单上下文仓库（大多数仓库）：

```
/
├── CONTEXT.md
├── docs/adr/
│   ├── 0001-event-sourced-orders.md
│   └── 0002-postgres-for-write-model.md
└── src/
```

多上下文仓库（存在根级 `CONTEXT-MAP.md`）：

```
/
├── CONTEXT-MAP.md
├── docs/adr/                          ← 全系统决策
└── src/
    ├── ordering/
    │   ├── CONTEXT.md
    │   └── docs/adr/                  ← 上下文相关决策
    └── billing/
        ├── CONTEXT.md
        └── docs/adr/
```

## 使用术语表中的词汇

当输出中命名领域概念（issue 标题、重构提案、假设、测试名）时，使用 `CONTEXT.md` 中定义的术语。不要漂移到术语表明确规避的同义词。

如果所需概念尚未收录在术语表中，这是一个信号 —— 要么你在发明项目未使用的语言（请重新考虑），要么确实存在空白（记为 `/domain-modeling` 的待办）。

## 标记 ADR 冲突

如果输出与现有 ADR 相矛盾，请明确指出而非静默覆盖：

> _Contradicts ADR-0007 (event-sourced orders) — but worth reopening because…_
