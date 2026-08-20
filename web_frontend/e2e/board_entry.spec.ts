import { expect, test } from "@playwright/test";

test("协同看板板块：导航点击进入嵌入 iframe（云端看板入口）", async ({ page }) => {
  await page.goto("/");
  // 导航出现「协同看板」（2026-08-20 新增板块）
  const nav = page.getByRole("button", { name: "协同看板" });
  await expect(nav).toBeVisible();
  await nav.click();
  // 板块页渲染说明 + 指向云端看板的 iframe（外网地址；不等待其内容加载）
  await expect(page.getByRole("heading", { name: "协同看板" })).toBeVisible();
  const frame = page.locator("iframe.board-frame__iframe");
  await expect(frame).toBeVisible();
  await expect(frame).toHaveAttribute("src", /49\.232\.160\.7/);
  // 主流程上一步/下一步不在看板页显示
  await expect(page.locator(".step-nav")).toHaveCount(0);
});
