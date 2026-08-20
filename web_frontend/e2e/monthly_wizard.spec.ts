import { expect, test } from "@playwright/test";

// 月度拆分四步向导 e2e（模块 A 二段式，离线全流程：规则兜底，无需 AI 配置）
// 覆盖：向导按 stage 渲染、规则题库出题、默认作答、拆分恒等预览（✓）、
// 含月度拆分下载。跳过路径（旧端点原样保留）由 pytest CO_test_monthly_api 覆盖。

const BASE = "http://127.0.0.1:8765";

async function apiPost(path: string, body: unknown) {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path} -> HTTP ${res.status}: ${await res.text()}`);
  return res.json();
}

async function seedDraft() {
  // 起第一稿任务并等待完成（规则链路，无 AI 也可跑）
  const started = await apiPost("/api/export/budget/draft/jobs", { advice_items: [] });
  for (let i = 0; i < 120; i++) {
    const job = await (await fetch(`${BASE}/api/export/budget/draft/jobs/${started.job_id}`)).json();
    if (job.status === "completed") return job;
    if (job.status === "failed") throw new Error(`第一稿失败：${job.error}`);
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("第一稿超时");
}

test("无月度状态时向导不渲染，预算卡片提示先完成编制建议", async ({ page }) => {
  // 先清态再导入：全新会话版本 → monthly stage = none（本用例须在全流程用例之前跑）
  await page.request.post("/api/session/clear");
  await page.request.post("/api/import/sample");
  await page.goto("/");
  await page.getByRole("button", { name: "第二稿与导出" }).click();
  await expect(page.locator(".monthly-wizard")).toHaveCount(0);
  await expect(page.getByText("请先完成②费用编制建议并勾选", { exact: false })).toBeVisible();
});

test("月度拆分向导：第一稿 → 问答（规则题库）→ 拆分 → 含月度导出", async ({ page }) => {
  test.setTimeout(180_000);
  await page.request.post("/api/session/clear");
  await page.request.post("/api/import/sample");
  await seedDraft();
  await page.goto("/");

  // 进入导出页（示例数据未走互动解锁：预算三表不依赖解锁，向导照常渲染）
  await page.getByRole("button", { name: "第二稿与导出" }).click();

  // 第 1 步：第一稿摘要卡可见（stage=draft）
  await expect(page.getByText("月度拆分 · 从年度目标到逐月执行计划")).toBeVisible({ timeout: 15000 });
  await expect(page.getByText("年度营收")).toBeVisible();
  await expect(page.getByText("非空费用行")).toBeVisible();

  // 第 2 步：规则题库自动出题（E2E 无 AI 配置 → source=rule），单选含推荐项
  await expect(page.getByText("月度拆分问答")).toBeVisible({ timeout: 10000 });
  await expect(page.getByText("规则题库", { exact: true })).toBeVisible();
  await expect(page.locator(".monthly-option").first()).toBeVisible();

  // 全部按推荐项 → 提交答案
  await page.getByRole("button", { name: "全部按推荐项" }).click();
  await page.getByRole("button", { name: "提交答案，进入拆分" }).click();
  await expect(page.getByText("答案已提交", { exact: false })).toBeVisible({ timeout: 10000 });

  // 第 3 步：开始月度拆分 → 恒等预览（行级 ✓）
  await page.getByRole("button", { name: "开始月度拆分" }).click();
  await expect(page.getByText("拆分结果与导出")).toBeVisible({ timeout: 30000 });
  await expect(
    page.getByText("恒等校验：逐行 12 个月合计 = 年度预算", { exact: false }),
  ).toBeVisible({ timeout: 30000 });
  // 透视表至少一行 ✓ 且合计行存在
  await expect(page.locator(".monthly-table__ok").first()).toBeVisible({ timeout: 10000 });
  await expect(page.locator(".monthly-table__total")).toBeVisible();

  // 含月度拆分下载（等待浏览器下载事件）
  const downloadPromise = page.waitForEvent("download", { timeout: 60000 });
  await page.getByRole("button", { name: "导出费用预算三表（含月度拆分）" }).click();
  const download = await downloadPromise;
  expect((await download.suggestedFilename()) || "").toContain("含月度拆分");
});
