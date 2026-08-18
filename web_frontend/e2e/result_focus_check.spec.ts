import { expect, test } from "@playwright/test";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const yearlyFixture = path.resolve(__dirname, "../../web_backend/workspaces/2021年审计报告.xlsx");

// 目标：AI 整理/合并报告按 markdown 分段渲染（标题/表格/列表成独立 DOM 元素），
// 而非整块 <pre>，避免内容被截断；导入后页面只显示「导入结果」。

test("AI 合并报告分段渲染：表格与标题成独立 DOM 元素", async ({ page }) => {
  await page.request.post("/api/import/sample");
  await page.goto("/");
  // 示例按钮已移除：API 导入后手动触发生成（离线回退为确定性 markdown）
  await page.getByRole("button", { name: "生成 AI 合并报告" }).click();

  const result = page.locator(".ai-result").first();
  await expect(result).toBeVisible({ timeout: 20000 });

  // 不应是整块 <pre> 原文，而应有结构化的表格与标题元素
  await expect(result.locator("table")).toHaveCount(1);
  await expect(result.locator("thead, table th").first()).toBeVisible();
  // 关键年份列存在于表格表头
  await expect(result.locator("th, td").first()).toBeVisible();
});

test("导入成功后不显示 PDF 预览图与 OCR 采样文字", async ({ page }) => {
  await page.request.post("/api/import/sample");
  await page.goto("/");
  // 导入后只有导入结果，无预览文件区
  await expect(page.getByText("导入结果", { exact: true })).toBeVisible();
  await expect(page.locator(".preview-image")).toHaveCount(0);
  await expect(page.locator(".preview-notes")).toHaveCount(0);
  await expect(page.getByText("文件完整采集", { exact: true })).toHaveCount(0);
});

test("点击报告记录：预览报告并完整载入对应案例", async ({ page }) => {
  // 案例甲：导入示例并生成 AI 报告（离线确定性回退，报告带 session_version）
  await page.request.post("/api/import/sample");
  await page.goto("/");
  await page.getByRole("button", { name: "生成 AI 合并报告" }).click();
  await expect(page.locator(".ai-result").first()).toBeVisible({ timeout: 20000 });

  // 案例乙：上传另一份财报（当前会话切到乙）
  await page.setInputFiles('input[type="file"][accept]:not([webkitdirectory])', yearlyFixture);
  await page.getByRole("button", { name: "开始导入" }).click();
  await expect(page.getByText("导入成功", { exact: false })).toBeVisible({ timeout: 15000 });
  await expect(page.getByRole("banner").getByText("示例制造有限公司", { exact: true })).toHaveCount(0);

  // 点击案例甲的报告记录 → 应载入甲案例（顶栏企业名切回）并展示报告详情
  await page.locator(".report-item__title").first().click();
  await expect(
    page.getByRole("banner").getByText("示例制造有限公司", { exact: true }),
  ).toBeVisible({ timeout: 10000 });
  await expect(page.getByText("已载入该报告对应的案例", { exact: false })).toBeVisible();
  await expect(page.locator(".report-detail")).toBeVisible();
});
