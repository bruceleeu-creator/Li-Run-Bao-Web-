import { expect, test } from "@playwright/test";

test("未填 API Key 时保存按钮禁用并提示缺失", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "设置" }).click();
  const saveBtn = page.getByRole("button", { name: "保存配置" });
  // 默认模型已预填，但 Base URL 与 API Key 为空 → 保存不可用
  await expect(saveBtn).toBeDisabled();
  await page.getByPlaceholder("https://api.deepseek.com").fill("https://api.deepseek.com");
  // 仍缺 API Key → 保持禁用并给出明确提示
  await expect(saveBtn).toBeDisabled();
  await expect(page.getByText(/API Key 当前为空/)).toBeVisible();
  // 补齐 API Key 后可保存
  await page.getByPlaceholder("sk-...").fill("sk-e2e-missing-key");
  await expect(saveBtn).toBeEnabled();
});

test("设置页保留已有模型，新表单默认 deepseek-v4-flash 并可保存", async ({ page }) => {
  await page.route("**/api/ai/config", async (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      await route.fulfill({ json: { base_url: body.base_url, model: body.model, configured: true } });
      return;
    }
    await route.fulfill({
      json: { base_url: "https://existing.example", model: "deepseek-chat", configured: true },
    });
  });
  await page.route("**/api/ai/clear", (route) =>
    route.fulfill({ json: { base_url: "", model: "", configured: false } }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "设置" }).click();
  // 设置页出现 AI 配置面板
  await expect(page.getByText("AI 可选增强", { exact: false })).toBeVisible();
  await expect(page.getByText("Base URL", { exact: true })).toBeVisible();
  await expect(page.getByText("模型", { exact: true })).toBeVisible();
  await expect(page.getByText("API Key", { exact: false })).toBeVisible();
  const model = page.getByPlaceholder("deepseek-v4-flash");
  await expect(model).toHaveValue("deepseek-chat");
  // 保存配置后状态变为已配置；已有值不会被默认值覆盖
  await page.getByPlaceholder("https://api.deepseek.com").fill("https://api.deepseek.com");
  await page.getByPlaceholder("sk-...").fill("sk-e2e-test-key");
  await page.getByRole("button", { name: "保存配置" }).click();
  await expect(page.getByText("AI 已配置", { exact: false }).first()).toBeVisible({ timeout: 5000 });
  // 清空恢复离线
  await page.getByRole("button", { name: "清空（恢复离线）" }).click();
  await expect(page.getByText("未配置", { exact: false }).first()).toBeVisible();
  await expect(model).toHaveValue("deepseek-v4-flash");
});
