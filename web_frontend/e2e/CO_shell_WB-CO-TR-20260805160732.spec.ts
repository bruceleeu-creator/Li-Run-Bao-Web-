import { expect, test } from "@playwright/test";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

test("显示工作区导航和本机状态", async ({ page }) => {
  await page.goto("/");
  // 2026-08-18 合并：总览+财报导入 → 「导入财报」；模板工作台已移除
  await expect(page.getByRole("button", { name: "导入财报" })).toBeVisible();
  await expect(page.getByRole("button", { name: "诊断" })).toBeVisible();
  await expect(page.getByRole("button", { name: "互动" })).toBeVisible();
  await expect(page.getByRole("button", { name: "第二稿与导出" })).toBeVisible();
  await expect(page.getByRole("button", { name: "设置" })).toBeVisible();
  await expect(page.getByText("仅本机运行", { exact: true })).toBeVisible();
  // 合并页：经营概况 + 财报导入区 + 右侧导入记录卡片同屏
  await expect(page.getByText("经营概况", { exact: true })).toBeVisible();
  await expect(page.getByText("导入记录", { exact: true })).toBeVisible();
  await expect(page.getByText("报告记录", { exact: true })).toBeVisible();
});

test("示例接口导入后显示真实指标", async ({ page }) => {
  // 「载入示例数据」按钮已移除：测试改走示例 API，重载页面获取会话
  await page.request.post("/api/import/sample");
  await page.goto("/");
  await expect(page.getByText("导入结果", { exact: true })).toBeVisible();
  await expect(page.getByRole("main").getByText("示例制造有限公司", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("毛利率", { exact: true }).first()).toBeVisible();
  // 增值税税负率估算标注
  await expect(page.getByText("估算", { exact: true }).first()).toBeVisible();

  // 顶栏与会话信息应显示真实企业名与指标（同页即可见）
  await expect(page.getByRole("banner").getByText("示例制造有限公司", { exact: true })).toBeVisible();
  await expect(page.getByText("增值税税负率", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("估算值（基于税金及附加反推）", { exact: true }).first()).toBeVisible();
  // 导入成功后生成导入记录卡片
  await expect(page.locator(".history-card").first()).toBeVisible();
});

test("上传 docx 财报可预览并导入", async ({ page }) => {
  await page.goto("/");
  // 选择 docx 文件（单文件模式）
  const docxPath = path.resolve(__dirname, "../../demo_output/样例财报_WB-CO-TR-20260805.docx");
  await page.setInputFiles('input[type="file"][accept]:not([webkitdirectory])', docxPath);
  // 等待预览出现
  await expect(page.getByText("文件完整采集", { exact: true })).toBeVisible();
  await expect(page.getByText("样例财报", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("营业收入", { exact: true }).first()).toBeVisible();
  // 点击导入（提交按钮已更名「开始导入」避免与导航同名）
  await page.getByRole("button", { name: "开始导入" }).click();
  await expect(page.getByText("导入成功", { exact: false })).toBeVisible();
  // 企业名未填时默认用文件名（导入结果表出现）
  await expect(page.getByText("导入结果", { exact: true })).toBeVisible();
});
