// 利润宝 · 本机后端 API 客户端
import type {
  DiagnosisGetResponse,
  DiagnosisResponse,
  ImportHistoryItem,
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

// ── 导入历史（卡片快速载入）──────────────────────────────────────────

export async function fetchImportHistory(): Promise<{ history: ImportHistoryItem[] }> {
  return request("/api/import/history");
}

/** 载入一条导入历史到当前会话（响应契约与 /import 一致） */
export async function loadImportHistory(id: number): Promise<ImportResponse> {
  return request(`/api/import/history/${id}/load`, { method: "POST" });
}

export async function deleteImportHistory(id: number): Promise<{ deleted: number }> {
  return request(`/api/import/history/${id}`, { method: "DELETE" });
}

export interface AIConfigResponse {
  base_url: string;
  model: string;
  configured: boolean;
  /** 内存 Key 的脱敏提示（如 sk-***abcd）；未配置为空 */
  key_hint?: string;
  /** 内存 Key 空闲存活期（秒），超时自动清除 */
  key_ttl_seconds?: number;
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

/** 页面关闭/刷新时通知后端立即清除内存 Key（sendBeacon 尽力送达，TTL 兜底） */
export function clearAIKeyBeacon(): void {
  if (typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function") {
    navigator.sendBeacon("/api/ai/key/clear");
    return;
  }
  void fetch("/api/ai/key/clear", { method: "POST", keepalive: true }).catch(() => undefined);
}

/** 前端心跳：页面仍打开时延长后端内存 Key 存活（失败静默，不影响主流程） */
export async function keepaliveAI(): Promise<AIConfigResponse | null> {
  try {
    return await request("/api/ai/keepalive", { method: "POST" });
  } catch {
    return null;
  }
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
  /** 报告所属会话版本：用于反查对应导入记录（点击报告时载入该案例） */
  session_version?: string;
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
  analysis_ready?: boolean;
}

export async function fetchExportStatus(): Promise<ExportStatus> {
  return request("/api/export/status");
}

// ── 经营预算分析（前世今生 · DeepSeek 先行，Word/PDF 后导） ─────────────

export interface ReportAnalysisStage {
  title: string;
  summary: string;
  bullets: string[];
}

export interface ReportAnalysisPoint {
  title: string;
  body: string;
}

export interface ReportAnalysisResponse {
  company_name: string;
  years: number[];
  one_liner: string;
  headline: string;
  stage_insight: string;
  stages: ReportAnalysisStage[];
  now_points: ReportAnalysisPoint[];
  now_judgments: Record<string, string>;
  future_actions: string[];
  ai_summary: string;
  number_warnings: string[];
  mode: string;
}

/** 生成经营预算分析：DeepSeek 把事实清单改写为前世今生文案（数字白名单校验） */
export async function generateReportAnalysis(): Promise<ReportAnalysisResponse> {
  return request("/api/export/analysis", { method: "POST" });
}

/** 最近一次经营预算分析（同会话）；无则 404 */
export async function fetchReportAnalysisLast(): Promise<ReportAnalysisResponse | null> {
  try {
    return await request("/api/export/analysis/last");
  } catch {
    return null;
  }
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

// ── 月度拆分（模块 A 二段式：第一稿 → 问答 → 拆分 → 导出） ────────────────
// 数字安全：AI 只出题/出权重，金额全部由确定性引擎计算并恒等校验（Σ月=年）。

export interface MonthlySummary {
  revenue: number;
  expense_total: number;
  fee_rate: number;
  filled_lines: number;
  advice_applied: number;
}

export interface MonthlyPlanRow {
  row: number;
  subject: string;
  expense_name: string;
  annual: number;
}

export interface MonthlyQuestion {
  id: string;
  type: "single" | "text" | string;
  title: string;
  options: string[];
  default: string;
  placeholder: string;
}

export interface MonthlyMatrixRow {
  row: number;
  subject: string;
  expense_name: string;
  annual: number;
  months: number[];
  shape: string;
  shape_note: string;
}

export interface MonthlySplitResult {
  matrix: MonthlyMatrixRow[];
  month_totals: number[];
  grand_total: number;
  mode: "ai" | "rule" | string;
  warnings: string[];
  checks: { row_failures: number; total_gap: number };
  generated_at?: string;
  stage?: string;
}

export interface MonthlyState {
  stage:
    | "none" | "draft" | "questions" | "answered" | "splitting" | "ready"
    | "failed" | "skipped" | string;
  session_version?: string;
  advice_fingerprint?: string;
  plan_snapshot?: { rows?: MonthlyPlanRow[]; top_summary?: { budget_revenue?: number } } | null;
  draft_meta?: MonthlySummary | null;
  question_source?: string;
  questions?: MonthlyQuestion[] | null;
  answers?: { id: string; value: string }[] | null;
  split_mode?: string;
  split_result?: MonthlySplitResult | null;
  summary?: MonthlySummary;
}

export interface BudgetDraftJob extends BudgetExportJob {
  monthly_stage?: string;
  summary?: MonthlySummary | null;
  advice_fingerprint?: string;
}

export interface MonthlySplitJob {
  job_id: string;
  status: "queued" | "running" | "completed" | "failed" | string;
  progress: number;
  message: string;
  error?: string;
}

/** 启动第一稿任务：复用预算管线，产出快照不触发下载。 */
export async function startBudgetDraft(
  adviceItems?: BudgetAdviceItem[],
): Promise<BudgetDraftJob> {
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
  return request("/api/export/budget/draft/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ advice_items: items }),
  });
}

export async function fetchBudgetDraft(jobId: string): Promise<BudgetDraftJob> {
  return request(`/api/export/budget/draft/jobs/${jobId}`);
}

export async function getMonthlyState(): Promise<MonthlyState> {
  return request("/api/export/budget/monthly/state");
}

export async function genMonthlyQuestions(): Promise<{
  questions: MonthlyQuestion[];
  source: "ai" | "rule" | string;
}> {
  return request("/api/export/budget/monthly/questions", { method: "POST" });
}

export async function submitMonthlyAnswers(
  answers: { id: string; value: string }[],
): Promise<{ answers: { id: string; value: string }[]; stage: string }> {
  return request("/api/export/budget/monthly/answers", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answers }),
  });
}

export async function startMonthlySplit(): Promise<MonthlySplitJob> {
  return request("/api/export/budget/monthly/split/jobs", { method: "POST" });
}

export async function fetchMonthlySplitJob(jobId: string): Promise<MonthlySplitJob> {
  return request(`/api/export/budget/monthly/split/jobs/${jobId}`);
}

export async function fetchMonthlyResult(): Promise<MonthlySplitResult> {
  return request("/api/export/budget/monthly");
}

/** 轮询拆分任务直到终态（不触发下载）。 */
export async function pollMonthlySplit(
  onProgress?: (job: MonthlySplitJob) => void,
): Promise<MonthlySplitJob> {
  let job = await startMonthlySplit();
  onProgress?.(job);
  const deadline = Date.now() + 3 * 60 * 1000;
  while (Date.now() < deadline) {
    if (job.status === "completed" || job.status === "failed") return job;
    await new Promise((r) => setTimeout(r, 1000));
    job = await fetchMonthlySplitJob(job.job_id);
    onProgress?.(job);
  }
  throw new Error("月度拆分超时，请重试");
}

/** 下载含月度拆分的最终文件（stage=ready 时可用）。 */
export async function downloadMonthlyBudget(): Promise<void> {
  const res = await fetch("/api/export/budget/monthly/download");
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
  triggerBlobDownload(blob, filenameFromDisposition(cd, "费用预算三表（含月度拆分）.xlsx"));
}
