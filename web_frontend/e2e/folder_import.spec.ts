import { expect, test } from "@playwright/test";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { copyFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const sourceFolder = path.resolve(__dirname, "../../web_backend/workspaces");
let folder = "";

test.beforeAll(() => {
  // Windows headless-shell 对多文件 setInputFiles + 中文临时路径会崩溃
  // （0xC0000409）：临时目录用 ASCII 前缀规避（文件名本身保持中文契约）
  folder = mkdtempSync(path.join(tmpdir(), "lrb-e2e-3y-"));
  for (const year of [2021, 2022, 2023]) {
    const name = `${year}年审计报告.xlsx`;
    copyFileSync(path.join(sourceFolder, name), path.join(folder, name));
  }
});

test.afterAll(() => {
  if (folder.startsWith(tmpdir())) rmSync(folder, { recursive: true, force: true });
});

test("文件夹导入三年审计报告并显示上传成功 toast", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "导入财报" }).click();
  // 选择文件夹（webkitdirectory input）
  await page.setInputFiles('input[webkitdirectory]', folder);
  await expect(page.getByText("已选择 3 个文件", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "开始导入" }).click();
  await expect(page.locator(".toast")).toBeVisible({ timeout: 5000 });
  await expect(page.locator(".toast").getByText("上传成功", { exact: false })).toBeVisible();
  await expect(page.getByText("2021 / 2022 / 2023", { exact: false }).first()).toBeVisible();
});

test("多选文件导入多个审计报告", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "导入财报" }).click();
  // 多选 input（非 webkitdirectory）
  await page.setInputFiles('input[type="file"][accept]:not([webkitdirectory])', [
    `${folder}/2021年审计报告.xlsx`,
    `${folder}/2022年审计报告.xlsx`,
    `${folder}/2023年审计报告.xlsx`,
  ]);
  await expect(page.getByText("已选择 3 个文件", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "开始导入" }).click();
  await expect(page.locator(".toast")).toBeVisible({ timeout: 5000 });
  await expect(page.getByText("2021 / 2022 / 2023", { exact: false }).first()).toBeVisible();
});
