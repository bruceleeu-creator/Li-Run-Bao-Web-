import { expect, test } from "@playwright/test";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

test("显示七个工作区和本机状态", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("总览", { exact: true })).toBeVisible();
  await expect(page.getByText("模板工作台", { exact: true })).toBeVisible();
  await expect(page.getByText("仅本机运行", { exact: true })).toBeVisible();
});

test("载入示例数据后总览显示真实指标", async ({ page }) => {
  await page.goto("/");
  // 进入财报导入页（侧栏导航按钮）
  await page.getByRole("button", { name: "财报导入" }).click();
  await page.getByText("载入示例数据", { exact: true }).click();
  // 等待导入结果出现（成功状态条）
  await expect(page.getByText("示例数据已载入", { exact: false })).toBeVisible();
  await expect(page.getByRole("main").getByText("示例制造有限公司", { exact: true })).toBeVisible();
  await expect(page.getByText("毛利率", { exact: true }).first()).toBeVisible();
  // 增值税税负率估算标注
  await expect(page.getByText("估算", { exact: true }).first()).toBeVisible();

  // 返回总览：应显示真实企业名与指标
  await page.getByRole("button", { name: "总览" }).click();
  await expect(page.getByRole("banner").getByText("示例制造有限公司", { exact: true })).toBeVisible();
  await expect(page.getByText("增值税税负率", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("估算值（基于税金及附加反推）", { exact: true })).toBeVisible();
});

test("上传 docx 财报可预览并导入", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "财报导入" }).click();
  // 选择 docx 文件（单文件模式）
  const docxPath = path.resolve(__dirname, "../../demo_output/样例财报_WB-CO-TR-20260805.docx");
  await page.setInputFiles('input[type="file"][accept]:not([webkitdirectory])', docxPath);
  // 等待预览出现
  await expect(page.getByText("文件完整采集", { exact: true })).toBeVisible();
  await expect(page.getByText("样例财报", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("营业收入", { exact: true }).first()).toBeVisible();
  // 点击导入
  await page.getByRole("button", { name: "导入财报" }).click();
  await expect(page.getByText("导入成功", { exact: false })).toBeVisible();
  // 企业名未填时默认用文件名（导入结果表出现）
  await expect(page.getByText("导入结果", { exact: true })).toBeVisible();
});
