import { expect, test } from "@playwright/test";

// 诊断流程 e2e：步骤条 → 导入 → 诊断 → 互动 → 导出解锁 + 上一步/下一步导航
// 使用真实本机后端（Playwright webServer 已启动 127.0.0.1:8765），示例数据离线可用。

test("步骤条在导入页可见，未导入时下一步被拦截", async ({ page }) => {
  // 清空后端会话，保证本测试的「未导入」前提（与其他 spec 共享后端）
  await page.request.post("http://127.0.0.1:8765/api/session/clear");
  await page.goto("/");
  // 步骤条精简为编号圆点（2026-08-18）：4 个步骤、文字走 title/aria
  await expect(page.locator(".workflow--compact .workflow__step")).toHaveCount(4);
  await expect(page.getByRole("button", { name: "第二稿与导出" })).toBeVisible();

  // 导入区就在「导入财报」初始页
  await expect(page.getByText("拖入文件夹或文件", { exact: true })).toBeVisible();
  // 未导入时点「下一步」被拦截并提示
  await page.getByRole("button", { name: "下一步 →" }).click();
  await expect(page.getByText("请先完成财报导入", { exact: false })).toBeVisible();
});

test("行业选择显示说明与 AI 推荐按钮，可一键应用推荐", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "导入财报" }).click();
  // 行业下拉默认制造业，说明可见
  await expect(page.getByText("行业（选择后用于行业对标诊断）", { exact: false })).toBeVisible();
  await expect(page.getByText("生产加工型企业", { exact: false })).toBeVisible();
  // AI 推荐按钮存在（企业名为空时禁用）
  const recBtn = page.getByRole("button", { name: "AI 推荐行业" });
  await expect(recBtn).toBeDisabled();
  // 填写企业名称后可用并触发规则推荐（E2E 环境无 AI 配置 → 规则路径）
  await page.getByPlaceholder(/自动识别|留空则用文件名/).fill("某某软件科技有限公司");
  await expect(recBtn).toBeEnabled();
  await recBtn.click();
  await expect(page.getByText("规则推荐", { exact: true })).toBeVisible();
  await expect(page.locator(".rec-card__body strong")).toHaveText("软件和信息技术服务业");
  // 一键应用
  await page.getByRole("button", { name: "应用", exact: true }).click();
  await expect(page.getByRole("button", { name: "已应用", exact: true })).toBeVisible();
  // 下拉已切换为推荐行业
  await expect(page.locator("select.input")).toHaveValue("软件和信息技术服务业");
});

test("完整流程：导入 → 诊断 → 互动 → 导出解锁", async ({ page }) => {
  // 「载入示例数据」按钮已移除：测试改走示例 API，重载页面获取会话
  await page.request.post("/api/import/sample");
  await page.goto("/");

  // 通过侧栏进入诊断页（自动执行诊断）
  await page.getByRole("button", { name: "诊断", exact: true }).click();
  // 诊断页自动执行诊断，等待出现真实发现
  await expect(page.getByText("研发费用缺失", { exact: false })).toBeVisible({ timeout: 15000 });
  await expect(page.getByText("业务招待费超限", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("规则引擎诊断", { exact: false })).toBeVisible();
  // 展开一条发现查看 A/B/C 选项
  await page.getByRole("button", { name: /展开选项/ }).first().click();
  await expect(page.getByText("净影响", { exact: false }).first()).toBeVisible();
  await expect(page.getByText("可行性：", { exact: false }).first()).toBeVisible();

  // 下一步：进入互动页（诊断已完成，不再被拦截）
  await page.getByRole("button", { name: "下一步 →" }).click();
  await expect(page.getByText("请选择落地路径", { exact: false })).toBeVisible({ timeout: 15000 });
  // 依次选 A 处理所有发现（数量随行业基准库发现规则变化，循环到无选项为止）
  for (let i = 0; i < 12; i++) {
    if ((await page.locator(".finding-option--selectable").count()) === 0) break;
    await expect(page.getByText("请选择落地路径", { exact: false })).toBeVisible();
    await page.locator(".finding-option--selectable").first().click();
    await page.waitForTimeout(400);
  }
  // 全部决策后进入第二稿/确认
  await expect(page.getByText("第二稿", { exact: false }).first()).toBeVisible({ timeout: 15000 });
  await expect(page.getByText("落地性评分", { exact: false })).toBeVisible();
  // 确认进入最终稿
  await page.getByRole("button", { name: /确认第二稿/ }).click();
  await expect(page.getByText("导出已解锁", { exact: false })).toBeVisible({ timeout: 15000 });

  // 下一步：进入导出页，导出按钮可点
  // 2026-08-19：导出页两段式重构后 Word/PDF 卡片需先生成①经营分析报告才渲染，
  // 这里断言无条件渲染的②测算模型卡（解锁后即 enabled）
  await page.getByRole("button", { name: "下一步 →" }).click();
  await expect(page.getByRole("button", { name: "导出测算模型" })).toBeVisible();
  await expect(page.locator(".export-card--enabled").first()).toBeVisible();
});

test("互动页未完成时下一步被拦截", async ({ page }) => {
  await page.request.post("/api/import/sample");
  await page.goto("/");
  // 直接进入互动页
  await page.getByRole("button", { name: "互动", exact: true }).click();
  await expect(page.getByText("请选择落地路径", { exact: false })).toBeVisible({ timeout: 15000 });
  // 未完成互动时点下一步被拦截
  await page.getByRole("button", { name: "下一步 →" }).click();
  await expect(page.getByText("请先完成 A/B/C 互动", { exact: false })).toBeVisible();
});
