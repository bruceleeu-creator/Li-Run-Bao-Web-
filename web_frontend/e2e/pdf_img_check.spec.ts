import { expect, test } from "@playwright/test";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

test("PDF 预览为文本型（不渲染大图、显示 OCR/文字采样）", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "财报导入" }).click();
  const pdfPath = path.resolve(__dirname, "../../demo_output/样例财报中文_WB-CO-TR-20260805.pdf");
  await page.setInputFiles('input[type="file"][accept]:not([webkitdirectory])', pdfPath);
  // 文件完整采集标题出现
  await expect(page.getByText("文件完整采集", { exact: true })).toBeVisible();
  // 预览含文字/OCR 采样（自动预览完成后出现）
  await expect(page.locator(".preview-notes")).toBeVisible({ timeout: 20000 });
  // 不应渲染任何 base64 大图
  const imgCount = await page.locator(".preview-image").count();
  expect(imgCount).toBe(0);
  await expect(page.locator("img[src^='data:image']")).toHaveCount(0);
  // 应有页数说明
  await expect(page.getByText(/^共 \d+ 页/)).toBeVisible();
});
