import { expect, test } from "@playwright/test";

test("模板工作台显示审计报告提取的指标", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "财报导入" }).click();
  await page.getByText("载入示例数据", { exact: true }).click();
  await expect(page.getByText("示例数据已载入", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "模板工作台" }).click();
  await expect(page.getByText("结构化提取", { exact: false }).first()).toBeVisible();
  // 输入框 value 断言
  const inputs = page.locator(".budget-top input");
  const values = await inputs.evaluateAll((els) => els.map((el) => (el as HTMLInputElement).value));
  expect(values).toContain("15200000");
  expect(values).toContain("11930000");
  expect(values).toContain("13500000");
  expect(values).toContain("10530000");
  // 企业信息显示
  await expect(page.getByText("示例制造有限公司", { exact: false }).first()).toBeVisible();
});
