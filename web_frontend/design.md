# Design — 利润宝 Web（纸墨台账 Ledger Paper）

本文件是前端视觉系统的唯一锁定源。任何页面/组件改版前先读本文件；
系统需要演进时**修改本文件**，而不是在某个页面局部覆盖。
实施范围：`web_frontend/src/CO_app_WB-CO-TR-20260805160732.css`（2026-08-19 全量重写）。

## 硬边界（先于一切审美）

- **不改 DOM 结构、类名、文案、role**：e2e（9 spec / 34 用例）按文案 + 类名 +
  角色断言（`.toast`、`.history-card`、`.export-card--enabled`、`select.input`、
  `button.btn--ai`、`.workflow--compact .workflow__step`、`role=banner` 等）。
- 交互逻辑（React 状态、API 调用、轮询）一律不动；本系统只约束视觉层。
- 无新增 npm 依赖；字体全部走系统栈。

## Genre

editorial（台账/公文编辑风）。产品是给财税顾问用的专业工具，
纸面 + 墨色 + 发丝线的「案头文档」气质优先于任何装饰。

## Macrostructure family

- App 页（全部五个工作区）：**Workbench**——左侧纸面导航轨（216px）+
  右侧文档列（`.content` 最大 1220px 居中）。页面间共享同一骨架，
  仅在组件原型（面板、发现卡、选项卡、导出卡）上变化。
- 导入页例外：主列 + 320px 右栏（`.ws-grid`），右栏 sticky。

## Theme（OKLCH，纸墨台账）

| Token | 值 | 用途 |
| --- | --- | --- |
| `--paper` | `oklch(96.6% 0.006 84)` | 页面底：暖纸 |
| `--sheet` | `oklch(98.8% 0.003 85)` | 面板：桌面上的白纸 |
| `--sheet-2` | `oklch(97.4% 0.005 84)` | 面板内嵌块 |
| `--sheet-3` | `oklch(95.8% 0.007 84)` | 表头 / 输入底 |
| `--ink` | `oklch(25% 0.012 75)` | 正文墨色 |
| `--ink-2` / `--ink-dim` | `oklch(42%/47% …)` | 次级 / 弱文字 |
| `--rule` / `--rule-strong` | `oklch(88.5%/79% …)` | 发丝线两档 |
| `--accent` | `oklch(41% 0.082 252)` | 靛墨（蓝黑墨水）：交互/激活/AI 动作 |
| `--accent-strong` | `oklch(35% 0.085 252)` | 主按钮 hover |
| `--accent-ink` | `oklch(98.5% 0.003 85)` | 靛墨上的字 |
| `--seal` | `oklch(48% 0.155 28)` | 印章朱砂：仅品牌方块与 favicon |
| 语义色 | 见 CSS `:root` | 低=松绿 / 中=赭黄 / 高=朱砂（各配 ink/wash/line 三档） |

强调色占比约束：靛墨作为大面积填充只出现在主按钮、激活态与左侧墨条，
单视口占比 ≤ 5%；朱砂只允许出现在品牌位与高风险语义。

## Typography

- Display（面板标题/品牌/区块标题）：`--font-display` = Songti SC → STSong →
  Noto Serif CJK SC → Source Han Serif SC → SimSun, serif；600；
  永远正体（禁斜体标题）。
- Body：`--font-body` = PingFang SC → Hiragino Sans GB → Microsoft YaHei。
- 数字（指标值/表格/金额）：`--mono` + `font-variant-numeric: tabular-nums`。
- 标题节奏：`.panel__title` 自带下缘发丝线；弹性标题行（`.diag-head`、
  `.preview-actions`）下缘线归容器。

## Spacing / Radius

- 4pt 命名刻度：`--space-2xs 4 / xs 8 / sm 12 / md 16 / lg 24 / xl 32 / 2xl 48`。
- 圆角只有两档：**2px**（行内元素）与 **3px**（面板）。禁止胶囊（999px）。

## Motion

- 立场：**motion-cut**。无进场动画、无位移悬浮、无发光。
- 仅保留：130ms 颜色/边框过渡（`--ease-out` / `--dur-1`）、
  功能性动效（进度条、加载旋转、toast 150ms 淡入）。
- `prefers-reduced-motion: reduce` 全局收敛（见 CSS 末段）。
- 焦点环 `:focus-visible` 即时出现、2px 靛墨、不参与过渡。

## 组件语音

- **按钮**：主=靛墨实底；AI 动作=靛墨描边（`btn--ai`）；ghost=发丝线；
  danger=朱砂描边。方角 2px。
- **面板**：白纸 + 发丝线，无阴影、无毛玻璃、无渐变。
- **发现卡**：左侧 3px 严重度墨条 + 描边档位；标签方角描边。
- **A/B/C 选项**：24px 方框衬线字母；净影响用松绿等宽数字。
- **状态条**：四语义（ok/warn/error/info）平涂淡彩 + 对应描边。
- **表格**：行发丝线、表头 `--sheet-3`、数字右对齐 tabular。

## 明令禁止（去 AI 味清单）

深色底、霓虹/荧光强调、径向光晕、毛玻璃 backdrop-filter、
胶囊圆角、translateY 悬浮、逐元素进场动画、uppercase 大间距眉标、
渐变分割线、彩色投影发光。以上一律不得回归。

## Exports · tokens.css

```css
:root {
  --color-paper:  oklch(96.6% 0.006 84);
  --color-sheet:  oklch(98.8% 0.003 85);
  --color-ink:    oklch(25% 0.012 75);
  --color-rule:   oklch(88.5% 0.009 82);
  --color-accent: oklch(41% 0.082 252);
  --color-focus:  oklch(41% 0.082 252);
  --font-display: "Songti SC", "Noto Serif CJK SC", serif;
  --font-body:    "PingFang SC", "Microsoft YaHei", sans-serif;
  --space-sm: 12px; --space-md: 16px; --space-lg: 24px; --space-xl: 32px;
  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --dur-1: 130ms;
}
```
