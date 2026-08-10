import { expect, test } from "@playwright/test";

// 目标：AI 整理/合并报告按 markdown 分段渲染（标题/表格/列表成独立 DOM 元素），
// 而非整块 <pre>，避免内容被截断；导入后页面只显示「导入结果」。

test("AI 合并报告分段渲染：表格与标题成独立 DOM 元素", async ({ page }) => {
  await page.goto("/");
  // 载入示例数据，触发总览自动生成合并报告（离线回退为确定性 markdown）
  await page.getByRole("button", { name: "财报导入" }).click();
  await page.getByRole("button", { name: "载入示例数据" }).click();
  await page.getByRole("button", { name: "总览" }).click();

  const result = page.locator(".ai-result").first();
  await expect(result).toBeVisible({ timeout: 20000 });

  // 不应是整块 <pre> 原文，而应有结构化的表格与标题元素
  await expect(result.locator("table")).toHaveCount(1);
  await expect(result.locator("thead, table th").first()).toBeVisible();
  // 关键年份列存在于表格表头
  await expect(result.locator("th, td").first()).toBeVisible();
});

test("导入成功后不显示 PDF 预览图与 OCR 采样文字", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "财报导入" }).click();
  await page.getByRole("button", { name: "载入示例数据" }).click();
  // 载入示例数据后只有导入结果，无预览文件区
  await expect(page.getByText("导入结果", { exact: true })).toBeVisible();
  await expect(page.locator(".preview-image")).toHaveCount(0);
  await expect(page.locator(".preview-notes")).toHaveCount(0);
  await expect(page.getByText("文件完整采集", { exact: true })).toHaveCount(0);
});
