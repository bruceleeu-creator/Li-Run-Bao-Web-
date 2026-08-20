import { expect, test } from "@playwright/test";

// 最小剧本：注册 A → 建房 → 注册 B（独立存储）→ 凭码加入 → A 建待办 → B 可见 → B 完成 → A 完成度变化
// 双账户模拟：B 用第二个浏览器上下文（独立 localStorage）。

test("注册 → 建房 → 凭码加入 → 共享看板 → 完成 → 完成度更新", async ({ browser }) => {
  const ctxA = await browser.newContext();
  const pageA = await ctxA.newPage();

  // A 注册
  await pageA.goto("/");
  await pageA.getByRole("button", { name: "没有账户？注册" }).click();
  await pageA.getByLabel(/用户名/).fill("e2e_boss");
  await pageA.getByLabel(/密码/).fill("e2e-password-123");
  await pageA.getByRole("button", { name: "注册并进入" }).click();
  await expect(pageA.getByText("我的房间")).toBeVisible({ timeout: 10000 });

  // A 建房并取邀请码（创建后直接进入看板页）
  await pageA.getByPlaceholder("新房间名称").fill("e2e 落地跟踪");
  await pageA.getByRole("button", { name: "创建房间" }).click();
  await expect(pageA.getByText("完成率", { exact: false })).toBeVisible({ timeout: 10000 });
  await pageA.getByRole("button", { name: "生成/重置邀请码" }).click();
  const codeText = await pageA.locator(".invite-code strong").textContent();
  expect(codeText).toBeTruthy();
  const inviteCode = (codeText || "").trim();
  // 回房间列表（B 加入后统一从列表进入）
  await pageA.getByRole("button", { name: "← 房间" }).click();
  await expect(pageA.getByText("e2e 落地跟踪")).toBeVisible({ timeout: 10000 });

  // B 注册（独立上下文）
  const ctxB = await browser.newContext();
  const pageB = await ctxB.newPage();
  await pageB.goto("/");
  await pageB.getByRole("button", { name: "没有账户？注册" }).click();
  await pageB.getByLabel(/用户名/).fill("e2e_staff");
  await pageB.getByLabel(/密码/).fill("e2e-password-123");
  await pageB.getByRole("button", { name: "注册并进入" }).click();
  await expect(pageB.getByText("我的房间")).toBeVisible({ timeout: 10000 });

  // B 凭码加入（加入后直接进入看板页）
  await pageB.getByPlaceholder("输入邀请码加入房间").fill(inviteCode);
  await pageB.getByRole("button", { name: "凭邀请码加入" }).click();
  await expect(pageB.getByText("完成率", { exact: false })).toBeVisible({ timeout: 10000 });

  // A 从列表进入房间
  await pageA.getByRole("button", { name: "e2e 落地跟踪" }).click();
  await expect(pageA.getByText("完成率", { exact: false })).toBeVisible({ timeout: 10000 });

  // A 建待办
  await pageA.getByRole("button", { name: "新建待办" }).click();
  await pageA.getByLabel(/标题/).fill("3 月广宣投放核销");
  await pageA.getByRole("button", { name: "保存", exact: true }).click();
  await expect(pageA.getByText("3 月广宣投放核销")).toBeVisible({ timeout: 10000 });

  // B 轮询拉取后看到同一任务（≤5 秒同步）
  await expect(pageB.getByText("3 月广宣投放核销")).toBeVisible({ timeout: 15000 });

  // B 完成任务 → A 侧完成度更新（轮询 ≤5 秒）
  await pageB.getByRole("button", { name: "完成", exact: true }).first().click();
  await expect(
    pageB.locator(".kanban__col").filter({ hasText: "已完成" }).getByText("3 月广宣投放核销"),
  ).toBeVisible({ timeout: 10000 });
  await expect
    .poll(async () => pageA.locator(".stats strong").first().textContent(), { timeout: 15000 })
    .toBe("100%");

  await ctxA.close();
  await ctxB.close();
});
