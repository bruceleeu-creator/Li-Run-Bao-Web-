// 利润宝 · 本机后端 API 客户端
import type {
  DiagnosisGetResponse,
  DiagnosisResponse,
  ImportResponse,
  IndustriesResponse,
  IndustryRecommendResponse,
  InteractionState,
  PreviewResponse,
  SessionResponse,
} from "./CO_types_WB-CO-TR-20260805160732";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // 非 JSON 响应，保留默认信息
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export async function fetchHealth(): Promise<{ status: string; bind: string }> {
  return request("/api/health");
}

export async function fetchSession(): Promise<SessionResponse> {
  return request("/api/session");
}

export async function clearSession(): Promise<SessionResponse> {
  return request("/api/session/clear", { method: "POST" });
}

export async function fetchIndustries(): Promise<IndustriesResponse> {
  return request("/api/industries");
}

export async function recommendIndustry(companyName: string, overview = ""): Promise<IndustryRecommendResponse> {
  return request("/api/industries/recommend", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ company_name: companyName, overview }),
  });
}

export interface IdentifyResponse {
  company_name: string;
  industry: string;
  reason: string;
  source: "ai" | "rule";
  fallback: boolean;
}

export async function identifyCompany(files: { name: string; text: string }[]): Promise<IdentifyResponse> {
  return request("/api/import/identify", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ files }),
  });
}

// ── 第一轮诊断 ────────────────────────────────────────────────────────

export async function runDiagnosis(): Promise<DiagnosisResponse> {
  return request("/api/diagnosis/run", { method: "POST" });
}

export async function fetchDiagnosis(): Promise<DiagnosisGetResponse> {
  return request("/api/diagnosis");
}

export async function clearDiagnosis(): Promise<{ cleared: boolean }> {
  return request("/api/diagnosis/clear", { method: "POST" });
}

// ── A/B/C 互动 ────────────────────────────────────────────────────────

export async function startInteraction(): Promise<InteractionState> {
  return request("/api/interaction/start", { method: "POST" });
}

export async function fetchInteractionState(): Promise<InteractionState> {
  return request("/api/interaction/state");
}

export async function submitDecision(
  findingId: string,
  optionLabel: string,
  strategyNote = "",
): Promise<InteractionState> {
  return request("/api/interaction/decide", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ finding_id: findingId, option_label: optionLabel, strategy_note: strategyNote }),
  });
}

export async function confirmInteraction(userConfirmed = true): Promise<InteractionState> {
  return request("/api/interaction/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_confirmed: userConfirmed }),
  });
}

/** 一键补全剩余 A/B/C 并确认，解锁导出（不重跑诊断）。 */
export async function fastForwardInteraction(
  optionLabel: "A" | "B" | "C" = "A",
): Promise<InteractionState & { fast_forward?: boolean; auto_decisions?: number; already_unlocked?: boolean }> {
  return request("/api/interaction/fast-forward", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      option_label: optionLabel,
      strategy_note: "一键补全（沿用已有诊断，快速解锁导出）",
      confirm: true,
    }),
  });
}

export async function importSample(): Promise<ImportResponse> {
  return request("/api/import/sample", { method: "POST" });
}

/** 一键载入案例包（manifest 驱动；id 或 alias，如 audit_yikang_3y / audit_3years） */
export async function importCasePack(caseId = "audit_yikang_3y"): Promise<ImportResponse & { message?: string; case_id?: string }> {
  return request(`/api/import/case/${encodeURIComponent(caseId)}`, { method: "POST" });
}

export async function listImportCases(): Promise<{
  cases: Array<{
    id: string;
    label: string;
    description: string;
    files: string[];
    company_name: string;
    industry: string;
    available: boolean;
    location: string;
  }>;
}> {
  return request("/api/import/cases");
}

export async function importFiles(
  files: File[],
  companyName: string,
  industry: string,
): Promise<ImportResponse> {
  const form = new FormData();
  for (const f of files) form.append("files", f);
  form.append("company_name", companyName);
  form.append("industry", industry);
  return request("/api/import", { method: "POST", body: form });
}

export async function previewFiles(files: File[]): Promise<PreviewResponse> {
  const form = new FormData();
  for (const f of files) form.append("files", f);
  return request("/api/preview", { method: "POST", body: form });
}

export async function fetchSavedPreviews(): Promise<PreviewResponse> {
  return request("/api/import/saved-previews");
}

export interface AIConfigResponse {
  base_url: string;
  model: string;
  configured: boolean;
  error?: string;
}

export async function fetchAIConfig(): Promise<AIConfigResponse> {
  return request("/api/ai/config");
}

export async function saveAIConfig(cfg: {
  base_url: string;
  model: string;
  api_key: string;
}): Promise<AIConfigResponse> {
  return request("/api/ai/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cfg),
  });
}

export async function clearAIConfig(): Promise<AIConfigResponse> {
  return request("/api/ai/clear", { method: "POST" });
}

export async function summarizePreview(content: string): Promise<{ markdown: string }> {
  return request("/api/ai/summarize", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
}

export type AIReportJobStatus = "queued" | "running" | "completed" | "failed";

/** Task 5B 公开任务快照。私有输入、文件路径和 owner token 不属于前端契约。 */
export interface AIReportJob {
  job_id: string;
  session_version: string;
  status: AIReportJobStatus;
  stage: string;
  progress: { current: number; total: number };
  message: string;
  markdown?: string;
  error?: string;
  error_code?: string;
  report_id?: number;
  report_type?: "ai_full" | "rules_quick" | "";
  model?: string;
  attempted_model?: string;
  fallback?: boolean;
  fallback_reason_code?: string;
}

export interface AIReportJobStart {
  job_id: string;
  status: AIReportJobStatus;
}

export async function startYearsSummaryJob(): Promise<AIReportJobStart> {
  return request("/api/ai/years-summary/jobs", { method: "POST" });
}

export async function fetchActiveYearsSummaryJob(): Promise<AIReportJob | null> {
  const response = await request<{ job: AIReportJob | null }>("/api/ai/years-summary/jobs/active");
  return response.job;
}

export async function fetchYearsSummaryJob(jobId: string): Promise<AIReportJob> {
  return request(`/api/ai/years-summary/jobs/${encodeURIComponent(jobId)}`);
}

export interface AIReportItem {
  id: number;
  kind: string;
  title: string;
  created_at: string;
}

export interface AIReportDetail extends AIReportItem {
  content: string;
}

export async function fetchReports(): Promise<{ reports: AIReportItem[] }> {
  return request("/api/ai/reports");
}

export async function fetchReport(id: number): Promise<AIReportDetail> {
  return request(`/api/ai/reports/${id}`);
}

export async function deleteReport(id: number): Promise<{ deleted: number }> {
  return request(`/api/ai/reports/${id}`, { method: "DELETE" });
}

// ── 导出（艺康体 Word / PDF / Excel） ─────────────────────────────────

export interface ExportStatus {
  ready: boolean;
  unlocked: boolean;
  reason: string;
  company_name: string;
  years: number[];
  findings: number;
  decisions: number;
  feasibility_score?: number;
  state?: string;
  total_est_saving?: number;
  data_quality?: import("./CO_types_WB-CO-TR-20260805160732").DataQuality;
  policy?: import("./CO_types_WB-CO-TR-20260805160732").PolicySnapshot;
  require_confirm?: boolean;
}

export async function fetchExportStatus(): Promise<ExportStatus> {
  return request("/api/export/status");
}

function triggerBlobDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function filenameFromDisposition(cd: string, fallback: string): string {
  const mStar = /filename\*=UTF-8''([^;]+)/i.exec(cd);
  const mPlain = /filename="?([^";]+)"?/i.exec(cd);
  if (mStar) {
    try {
      return decodeURIComponent(mStar[1]);
    } catch {
      return mStar[1];
    }
  }
  if (mPlain) return mPlain[1];
  return fallback;
}

/** 触发文件下载：POST 二进制流 + Content-Disposition 文件名。 */
export async function downloadExport(
  kind: "word" | "pdf" | "excel" | "budget",
): Promise<void> {
  // 预算三表：走异步任务（DeepSeek 多阶段，可能 1–3 分钟）
  if (kind === "budget") {
    await downloadBudgetExportAsync();
    return;
  }
  const res = await fetch(`/api/export/${kind}`, { method: "POST" });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // ignore
    }
    throw new Error(detail);
  }
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") || "";
  const fallback =
    kind === "word"
      ? "经营业绩分析与建议.docx"
      : kind === "pdf"
        ? "经营业绩分析与建议.pdf"
        : "成本优化测算模型.xlsx";
  triggerBlobDownload(blob, filenameFromDisposition(cd, fallback));
}

export interface BudgetExportJob {
  job_id: string;
  status: "queued" | "running" | "completed" | "failed" | string;
  stage: string;
  progress: number;
  message: string;
  error?: string;
  filename?: string;
  download_ready?: boolean;
  meta?: {
    top_method?: string;
    line_method?: string;
    filled_lines?: number;
    advice_applied?: boolean;
    advice_selected?: number;
    notes?: string[];
  };
}

export async function startBudgetExportJob(
  adviceItems?: BudgetAdviceItem[],
): Promise<BudgetExportJob> {
  const items = (adviceItems || [])
    .filter((it) => it.selected !== false)
    .map((it) => ({
      row: it.row,
      reference_amount: it.reference_amount,
      budget_amount: it.budget_amount,
      has_last_year: it.has_last_year,
      last_year_actual: it.last_year_actual,
      selected: true,
      write_last_year: false,
      subject: it.subject,
      expense_name: it.expense_name,
      invoice_name: it.invoice_name,
      reason: it.reason,
    }));
  return request("/api/export/budget/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ advice_items: items }),
  });
}

export async function fetchBudgetExportJob(jobId: string): Promise<BudgetExportJob> {
  return request(`/api/export/budget/jobs/${jobId}`);
}

export async function fetchActiveBudgetExportJob(): Promise<BudgetExportJob | null> {
  const body = await request<{ job: BudgetExportJob | null }>("/api/export/budget/jobs/active");
  return body.job;
}

/** 费用编制建议项 */
export interface BudgetAdviceItem {
  row: number;
  subject: string;
  expense_name: string;
  invoice_name: string;
  has_last_year: boolean;
  last_year_actual: number;
  reference_amount: number;
  budget_amount: number;
  budget_ratio: number;
  budget_ratio_pct: number;
  priority: "high" | "mid" | "low" | string;
  reason: string;
  source: string;
  selected: boolean;
  write_last_year: boolean;
}

export interface BudgetAdviceResponse {
  company_name: string;
  industry: string;
  year: number;
  budget_revenue: number;
  expense_budget_cap: number;
  allocated_before: number;
  residual: number;
  zero_lines: number;
  suggestions: BudgetAdviceItem[];
  algorithm_notes: string[];
  ai_used: boolean;
  ai_summary: string;
  subject_mix: Record<string, number>;
  suggestion_count: number;
  selected_budget_total: number;
  ai_error?: string;
  plan_meta?: Record<string, unknown>;
}

/** 费用编制建议：后端强制 DeepSeek 全量介入（use_ai 固定 true） */
export async function generateBudgetAdvice(_useAi = true): Promise<BudgetAdviceResponse> {
  return request("/api/budget/advice", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ use_ai: true }),
  });
}

/**
 * 异步生成费用预算三表并下载。
 * 推荐：先 generateBudgetAdvice，再传入勾选的 adviceItems，导出时自动填入。
 */
export async function downloadBudgetExportAsync(
  onProgress?: (job: BudgetExportJob) => void,
  adviceItems?: BudgetAdviceItem[],
): Promise<BudgetExportJob> {
  const started = await startBudgetExportJob(adviceItems);
  let job = started;
  onProgress?.(job);
  const deadline = Date.now() + 8 * 60 * 1000;
  while (Date.now() < deadline) {
    if (job.status === "completed" && job.download_ready) {
      const res = await fetch(`/api/export/budget/jobs/${job.job_id}/download`);
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const body = await res.json();
          if (typeof body?.detail === "string") detail = body.detail;
        } catch {
          /* ignore */
        }
        throw new Error(detail);
      }
      const blob = await res.blob();
      const cd = res.headers.get("Content-Disposition") || "";
      triggerBlobDownload(
        blob,
        filenameFromDisposition(cd, job.filename || "费用预算三表.xlsx"),
      );
      return job;
    }
    if (job.status === "failed") {
      throw new Error(job.error || job.message || "预算三表生成失败");
    }
    await new Promise((r) => setTimeout(r, 1200));
    job = await fetchBudgetExportJob(job.job_id);
    onProgress?.(job);
  }
  throw new Error("预算三表生成超时，请稍后在导出页重试");
}

/** @deprecated 使用 downloadBudgetExportAsync(onProgress, items) 即可自动填入 */
export async function applyBudgetAdviceAndDownload(
  items: BudgetAdviceItem[],
): Promise<void> {
  await downloadBudgetExportAsync(undefined, items);
}

export interface BudgetTopInputs {
  budget_revenue: number;
  budget_cost: number;
  last_year_revenue: number;
  last_year_cost: number;
  industry_contribution_rate: number;
  company_contribution_rate: number;
  income_tax_rate: number;
}

export interface BudgetPlanResponse {
  plan: {
    company_name: string;
    industry: string;
    year: number;
    top_inputs: BudgetTopInputs;
  };
  source_note: string;
  method: "ai" | "fallback";
}

export async function fetchBudgetFromSession(): Promise<BudgetPlanResponse> {
  return request("/api/budget/from-session", { method: "POST" });
}
