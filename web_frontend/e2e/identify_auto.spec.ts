import { expect, test } from "@playwright/test";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { copyFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const sourceFolder = path.resolve(__dirname, "../../web_backend/workspaces");
let folder = "";

test.beforeAll(() => {
  folder = mkdtempSync(path.join(tmpdir(), "利润宝识别-"));
  // 用带企业名的文件名（模拟真实用户文件）
  copyFileSync(
    path.join(sourceFolder, "2021年审计报告.xlsx"),
    path.join(folder, "云南艺康装饰工程有限公司 2021年度审计报告.xlsx"),
  );
  copyFileSync(
    path.join(sourceFolder, "2022年审计报告.xlsx"),
    path.join(folder, "云南艺康装饰工程有限公司 2022年度审计报告.xlsx"),
  );
});

test.afterAll(() => {
  if (folder.startsWith(tmpdir())) rmSync(folder, { recursive: true, force: true });
});

test("选择文件后自动识别企业名称与行业并填入表单", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "财报导入" }).click();
  // 多选文件（非 webkitdirectory）
  await page.setInputFiles('input[type="file"][accept]:not([webkitdirectory])', [
    `${folder}/云南艺康装饰工程有限公司 2021年度审计报告.xlsx`,
    `${folder}/云南艺康装饰工程有限公司 2022年度审计报告.xlsx`,
  ]);
  await expect(page.getByText("已选择 2 个文件", { exact: false })).toBeVisible();
  // 自动识别（规则路径：E2E 环境 AI 未配置）：企业名称自动填入
  await expect(page.getByPlaceholder("选择文件后自动识别，可手动修改")).toHaveValue("云南艺康装饰工程有限公司", { timeout: 15000 });
  // 行业自动填入建筑业（名称含「工程」）
  await expect(page.locator("select.input")).toHaveValue("建筑业", { timeout: 5000 });
  // 识别提示可见
  await expect(page.getByText("规则识别", { exact: true })).toBeVisible();
  // 导入后结果正确
  await page.getByRole("button", { name: "导入财报" }).click();
  await expect(page.locator(".toast")).toBeVisible({ timeout: 8000 });
  await expect(page.getByText("云南艺康装饰工程有限公司", { exact: true }).first()).toBeVisible();
});
