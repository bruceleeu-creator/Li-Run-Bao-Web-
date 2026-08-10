import { expect, test, type Page, type Route } from "@playwright/test";

const session = {
  session: {
    company_name: "云南艺康",
    industry: "制造业",
    years: [2022, 2023, 2024],
    latest_year: 2024,
    matched: 12,
    unmatched: [],
    warnings: [],
  },
  indicators: [],
  years: [2022, 2023, 2024],
};

type Job = {
  job_id: string;
  status: "queued" | "running" | "completed" | "failed";
  stage: string;
  progress: { current: number; total: number };
  message: string;
  markdown?: string;
  error?: string;
  error_code?: string;
  report_id?: number;
};

async function fulfillJson(route: Route, json: unknown, status = 200) {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(json) });
}

async function mockAppShell(page: Page, activeJob: Job | null = null) {
  await page.route("**/api/health", (route) => fulfillJson(route, { status: "ok", bind: "127.0.0.1" }));
  await page.route("**/api/session", (route) => fulfillJson(route, session));
  await page.route("**/api/industries", (route) => fulfillJson(route, { industries: ["制造业"] }));
  await page.route("**/api/ai/config", (route) =>
    fulfillJson(route, { base_url: "", model: "", configured: false }),
  );
  await page.route("**/api/ai/reports", (route) => fulfillJson(route, { reports: [] }));
  await page.route("**/api/ai/years-summary/jobs/active", (route) =>
    fulfillJson(route, { job: activeJob }),
  );
}

test("后台任务显示嵌套页进度，完成后只展示正式报告并停止轮询", async ({ page }) => {
  await mockAppShell(page);
  let polls = 0;
  await page.route("**/api/ai/years-summary/jobs", (route) =>
    fulfillJson(route, { job_id: "job-1", status: "queued" }),
  );
  await page.route("**/api/ai/years-summary/jobs/job-1", (route) => {
    polls += 1;
    const job: Job = polls === 1
      ? {
          job_id: "job-1",
          status: "running",
          stage: "read",
          progress: { current: 18, total: 122 },
          message: "正在读取 2023年审计报告.pdf 第 18/43 页",
          markdown: "# 不得展示的临时报告",
        }
      : {
          job_id: "job-1",
          status: "completed",
          stage: "validate",
          progress: { current: 122, total: 122 },
          message: "最终报告校验完成",
          markdown: "# 不得展示的任务副本",
          report_id: 41,
        };
    return fulfillJson(route, job);
  });
  await page.route("**/api/ai/reports/41", (route) =>
    fulfillJson(route, {
      id: 41,
      kind: "years_summary",
      title: "云南艺康 跨年合并报告",
      created_at: "2026-08-08 12:00:00",
      content: "# 云南艺康 2022—2024 跨年合并报告",
    }),
  );

  await page.goto("/");
  await page.getByRole("button", { name: "生成 AI 合并报告" }).click();
  await expect(page.getByText("18 / 122", { exact: true })).toBeVisible();
  const progress = page.getByRole("progressbar", { name: "AI 合并报告生成进度" });
  await expect(progress).toHaveAttribute("aria-valuenow", "18");
  await expect(progress).toHaveAttribute("aria-valuemax", "122");
  await expect(page.getByText("云南艺康 2022—2024 跨年合并报告", { exact: true })).toBeVisible();
  await expect(page.getByText("不得展示的临时报告", { exact: false })).toHaveCount(0);
  await expect(page.getByText("不得展示的任务副本", { exact: false })).toHaveCount(0);

  const terminalPolls = polls;
  await page.waitForTimeout(1_800);
  expect(polls).toBe(terminalPolls);
});

test("刷新后恢复 active 任务，未知总数和零进度均可访问", async ({ page }) => {
  const active: Job = {
    job_id: "job-active",
    status: "running",
    stage: "prepare",
    progress: { current: 0, total: 0 },
    message: "正在准备报告来源",
  };
  await mockAppShell(page, active);
  let starts = 0;
  let polls = 0;
  await page.route("**/api/ai/years-summary/jobs", (route) => {
    starts += 1;
    return fulfillJson(route, { job_id: "unexpected", status: "queued" });
  });
  await page.route("**/api/ai/years-summary/jobs/job-active", (route) => {
    polls += 1;
    return fulfillJson(route, {
      ...active,
      stage: "read",
      progress: { current: 0, total: 122 },
      message: "已确认 122 页，等待读取",
    });
  });

  await page.goto("/");
  await expect(page.getByText("0 / 总页数待确认", { exact: true })).toBeVisible();
  await expect(page.getByText("0 / 122", { exact: true })).toBeVisible();
  const progress = page.getByRole("progressbar", { name: "AI 合并报告生成进度" });
  await expect(progress).toHaveAttribute("aria-valuenow", "0");
  await expect(progress).toHaveAttribute("aria-valuemax", "122");
  expect(starts).toBe(0);
  expect(polls).toBeGreaterThan(0);
});

test("失败只显示后端安全错误，不显示 partial markdown，并保留重新生成", async ({ page }) => {
  await mockAppShell(page);
  await page.route("**/api/ai/years-summary/jobs", (route) =>
    fulfillJson(route, { job_id: "job-failed", status: "queued" }),
  );
  await page.route("**/api/ai/years-summary/jobs/job-failed", (route) =>
    fulfillJson(route, {
      job_id: "job-failed",
      status: "failed",
      stage: "read",
      progress: { current: 18, total: 122 },
      message: "读取失败",
      error_code: "SOURCE_PAGE_FAILED",
      error: "2023年审计报告.pdf 第 18 页读取失败，请重新生成",
      markdown: "# 不完整内容禁止展示",
    }),
  );

  await page.goto("/");
  await page.getByRole("button", { name: "生成 AI 合并报告" }).click();
  await expect(page.getByRole("alert")).toContainText("2023年审计报告.pdf 第 18 页读取失败，请重新生成");
  await expect(page.getByText("不完整内容禁止展示", { exact: false })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "重新生成 AI 报告" })).toBeEnabled();
});

test("新会话启动新任务后，旧任务的迟到响应不能覆盖当前报告", async ({ page }) => {
  await mockAppShell(page);
  let startNo = 0;
  let releaseOld: (() => void) | undefined;
  await page.route("**/api/ai/years-summary/jobs", (route) => {
    startNo += 1;
    return fulfillJson(route, { job_id: `job-${startNo}`, status: "queued" });
  });
  await page.route("**/api/ai/years-summary/jobs/job-1", async (route) => {
    await new Promise<void>((resolve) => {
      releaseOld = resolve;
    });
    await fulfillJson(route, {
      job_id: "job-1",
      status: "completed",
      stage: "validate",
      progress: { current: 122, total: 122 },
      message: "旧任务完成",
      report_id: 11,
    });
  });
  await page.route("**/api/ai/years-summary/jobs/job-2", (route) =>
    fulfillJson(route, {
      job_id: "job-2",
      status: "completed",
      stage: "validate",
      progress: { current: 3, total: 3 },
      message: "新任务完成",
      report_id: 22,
    }),
  );
  await page.route("**/api/ai/reports/11", (route) =>
    fulfillJson(route, { id: 11, kind: "years_summary", title: "旧", created_at: "", content: "# 旧会话报告" }),
  );
  await page.route("**/api/ai/reports/22", (route) =>
    fulfillJson(route, { id: 22, kind: "years_summary", title: "新", created_at: "", content: "# 新会话报告" }),
  );
  await page.route("**/api/import/sample", (route) =>
    fulfillJson(route, {
      summary: { ...session.session, company_name: "新会话企业" },
      indicators: [],
      years: [2022, 2023, 2024],
    }),
  );

  await page.goto("/");
  await page.getByRole("button", { name: "生成 AI 合并报告" }).click();
  await page.getByRole("button", { name: "财报导入" }).click();
  await page.getByRole("button", { name: "载入示例数据" }).click();
  await page.getByRole("button", { name: "总览" }).click();
  await expect(page.getByText("新会话报告", { exact: true })).toBeVisible();
  releaseOld?.();
  await page.waitForTimeout(300);
  await expect(page.getByText("旧会话报告", { exact: true })).toHaveCount(0);
  await expect(page.getByText("新会话报告", { exact: true })).toBeVisible();
});

test("真实 Chromium 加载总览时控制台无 error", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  await mockAppShell(page);
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  expect(consoleErrors).toEqual([]);
});

test("首次空会话导入后，新任务不会被刷新恢复检查取消", async ({ page }) => {
  await mockAppShell(page);
  await page.route("**/api/session", (route) =>
    fulfillJson(route, { session: null, indicators: [], years: [] }),
  );
  await page.route("**/api/import/sample", (route) =>
    fulfillJson(route, { summary: session.session, indicators: [], years: session.years }),
  );
  await page.route("**/api/ai/years-summary/jobs", (route) =>
    fulfillJson(route, { job_id: "job-first-import", status: "queued" }),
  );
  await page.route("**/api/ai/years-summary/jobs/job-first-import", (route) =>
    fulfillJson(route, {
      job_id: "job-first-import",
      status: "completed",
      stage: "validate",
      progress: { current: 3, total: 3 },
      message: "完成",
      report_id: 66,
    }),
  );
  await page.route("**/api/ai/reports/66", (route) =>
    fulfillJson(route, { id: 66, kind: "years_summary", title: "首次导入", created_at: "", content: "# 首次导入报告" }),
  );

  await page.goto("/");
  await page.getByRole("button", { name: "财报导入" }).click();
  await page.getByRole("button", { name: "载入示例数据" }).click();
  await page.getByRole("button", { name: "总览" }).click();
  await expect(page.getByText("首次导入报告", { exact: true })).toBeVisible();
});

test("清空会话在途期间禁止启动任务，清空后旧 job 与 report 迟到响应不能回写", async ({ page }) => {
  await mockAppShell(page);
  let startPosts = 0;
  let releaseClear: (() => void) | undefined;
  let releaseDetail: (() => void) | undefined;
  await page.route("**/api/session/clear", async (route) => {
    await new Promise<void>((resolve) => {
      releaseClear = resolve;
    });
    await fulfillJson(route, { session: null, indicators: [], years: [] });
  });
  await page.route("**/api/ai/years-summary/jobs", (route) => {
    startPosts += 1;
    return fulfillJson(route, { job_id: "job-during-clear", status: "queued" });
  });
  await page.route("**/api/ai/years-summary/jobs/job-during-clear", async (route) => {
    await new Promise<void>((resolve) => {
      releaseDetail = resolve;
    });
    await fulfillJson(route, {
      job_id: "job-during-clear",
      status: "completed",
      stage: "validate",
      progress: { current: 122, total: 122 },
      message: "旧任务完成",
      report_id: 77,
    });
  });
  await page.route("**/api/ai/reports/77", (route) =>
    fulfillJson(route, { id: 77, kind: "years_summary", title: "旧", created_at: "", content: "# 清空后不应出现的旧报告" }),
  );

  await page.goto("/");
  await page.getByRole("button", { name: "生成 AI 合并报告" }).click();
  await expect.poll(() => Boolean(releaseDetail)).toBe(true);
  await page.getByRole("button", { name: "财报导入" }).click();
  await page.getByRole("button", { name: "重新导入" }).click();
  await expect.poll(() => Boolean(releaseClear)).toBe(true);
  await page.getByRole("button", { name: "总览" }).click();
  const generate = page.locator("button.btn--ai").first();
  await expect(generate).toBeDisabled();
  await generate.evaluate((button: HTMLButtonElement) => button.click());
  expect(startPosts).toBe(1);

  releaseClear?.();
  await expect(page.getByText("尚未导入财报", { exact: false }).first()).toBeVisible();
  releaseDetail?.();
  await page.waitForTimeout(300);
  await expect(page.getByText("清空后不应出现的旧报告", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("progressbar", { name: "AI 合并报告生成进度" })).toHaveCount(0);
  await expect(page.getByRole("alert")).toHaveCount(0);
});

for (const activeStatus of ["queued", "running"] as const) {
  for (const detailStatus of [404, 410] as const) {
  test(`刷新恢复 ${activeStatus} 后 detail ${detailStatus} 进入可重试失败态且不再轮询`, async ({ page }) => {
    const active: Job = {
      job_id: `job-missing-${activeStatus}`,
      status: activeStatus,
      stage: activeStatus === "queued" ? "" : "read",
      progress: { current: activeStatus === "queued" ? 0 : 18, total: activeStatus === "queued" ? 0 : 122 },
      message: activeStatus === "queued" ? "等待开始" : "正在读取第 18 页",
    };
    await mockAppShell(page, active);
    let details = 0;
    await page.route(`**/api/ai/years-summary/jobs/${active.job_id}`, (route) => {
      details += 1;
      return fulfillJson(route, { detail: "任务不存在" }, detailStatus);
    });

    await page.goto("/");
    await expect(page.getByRole("progressbar", { name: "AI 合并报告生成进度" })).toBeVisible();
    await expect(page.getByRole("alert")).toContainText("任务不存在");
    await expect(page.getByRole("progressbar", { name: "AI 合并报告生成进度" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "重新生成 AI 报告" })).toBeEnabled();
    const terminalDetails = details;
    await page.waitForTimeout(1_800);
    expect(details).toBe(terminalDetails);
  });
  }
}

for (const reportFailure of ["404", "network", "missing-id"] as const) {
  test(`completed 正式报告 ${reportFailure} 显示明确可重试失败且不展示 job markdown`, async ({ page }) => {
    await mockAppShell(page);
    const reportId = reportFailure === "missing-id" ? undefined : 88;
    await page.route("**/api/ai/years-summary/jobs", (route) =>
      fulfillJson(route, { job_id: `job-report-${reportFailure}`, status: "queued" }),
    );
    await page.route(`**/api/ai/years-summary/jobs/job-report-${reportFailure}`, (route) =>
      fulfillJson(route, {
        job_id: `job-report-${reportFailure}`,
        status: "completed",
        stage: "validate",
        progress: { current: 122, total: 122 },
        message: "任务完成",
        markdown: "# 禁止展示的 job 副本",
        report_id: reportId,
      }),
    );
    if (reportFailure === "404") {
      await page.route("**/api/ai/reports/88", (route) =>
        fulfillJson(route, { detail: "报告不存在" }, 404),
      );
    } else if (reportFailure === "network") {
      await page.route("**/api/ai/reports/88", (route) => route.abort("failed"));
    }

    await page.goto("/");
    await page.getByRole("button", { name: "生成 AI 合并报告" }).click();
    await expect(page.getByRole("alert")).toContainText("正式报告不可用，请重新生成");
    await expect(page.getByRole("button", { name: "重新生成 AI 报告" })).toBeEnabled();
    await expect(page.getByText("禁止展示的 job 副本", { exact: false })).toHaveCount(0);
  });
}

test("新任务 start 成功后首次 detail 传输失败进入安全可重试状态", async ({ page }) => {
  await mockAppShell(page);
  await page.route("**/api/ai/years-summary/jobs", (route) =>
    fulfillJson(route, { job_id: "job-detail-transport", status: "queued" }),
  );
  await page.route("**/api/ai/years-summary/jobs/job-detail-transport", (route) =>
    route.abort("failed"),
  );

  await page.goto("/");
  await page.getByRole("button", { name: "生成 AI 合并报告" }).click();
  await expect(page.getByRole("alert")).toContainText("任务状态读取失败，请重新生成");
  await expect(page.getByRole("alert")).not.toContainText("Failed to fetch");
  await expect(page.getByRole("button", { name: "重新生成 AI 报告" })).toBeEnabled();
});

test("同一事件循环同步双击只发一个 start POST 并沿单一任务完成", async ({ page }) => {
  await mockAppShell(page);
  let starts = 0;
  let releaseStart: (() => void) | undefined;
  const startGate = new Promise<void>((resolve) => {
    releaseStart = resolve;
  });
  await page.route("**/api/ai/years-summary/jobs", async (route) => {
    starts += 1;
    await startGate;
    await fulfillJson(route, { job_id: "job-double", status: "queued" });
  });
  await page.route("**/api/ai/years-summary/jobs/job-double", (route) =>
    fulfillJson(route, {
      job_id: "job-double",
      status: "completed",
      stage: "validate",
      progress: { current: 122, total: 122 },
      message: "完成",
      report_id: 99,
    }),
  );
  await page.route("**/api/ai/reports/99", (route) =>
    fulfillJson(route, { id: 99, kind: "years_summary", title: "双击", created_at: "", content: "# 单一任务报告" }),
  );

  await page.goto("/");
  await page.getByRole("button", { name: "生成 AI 合并报告" }).evaluate((button: HTMLButtonElement) => {
    button.click();
    button.click();
  });
  await expect.poll(() => starts).toBeGreaterThan(0);
  await page.waitForTimeout(100);
  expect(starts).toBe(1);
  releaseStart?.();
  await expect(page.getByText("单一任务报告", { exact: true })).toBeVisible();
});

test("失败重试与成功后重新生成可连续启动，且没有未处理页面异常", async ({ page }) => {
  await mockAppShell(page);
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  let starts = 0;
  await page.route("**/api/ai/years-summary/jobs", (route) => {
    starts += 1;
    return fulfillJson(route, { job_id: `job-chain-${starts}`, status: "queued" });
  });
  await page.route("**/api/ai/years-summary/jobs/job-chain-1", (route) =>
    fulfillJson(route, {
      job_id: "job-chain-1",
      status: "failed",
      stage: "read",
      progress: { current: 1, total: 3 },
      message: "失败",
      error: "安全失败，可重新生成",
    }),
  );
  for (const no of [2, 3]) {
    await page.route(`**/api/ai/years-summary/jobs/job-chain-${no}`, (route) =>
      fulfillJson(route, {
        job_id: `job-chain-${no}`,
        status: "completed",
        stage: "validate",
        progress: { current: 3, total: 3 },
        message: "完成",
        report_id: 100 + no,
      }),
    );
    await page.route(`**/api/ai/reports/${100 + no}`, (route) =>
      fulfillJson(route, {
        id: 100 + no,
        kind: "years_summary",
        title: `连续任务 ${no}`,
        created_at: "",
        content: `# 连续任务报告 ${no}`,
      }),
    );
  }

  await page.goto("/");
  await page.getByRole("button", { name: "生成 AI 合并报告" }).click();
  await expect(page.getByRole("alert")).toContainText("安全失败，可重新生成");
  await page.getByRole("button", { name: "重新生成 AI 报告" }).click();
  await expect(page.getByText("连续任务报告 2", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "重新生成 AI 报告" }).click();
  await expect(page.getByText("连续任务报告 3", { exact: true })).toBeVisible();
  expect(starts).toBe(3);
  expect(pageErrors).toEqual([]);
});
