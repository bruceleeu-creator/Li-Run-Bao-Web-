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

export async function importSample(): Promise<ImportResponse> {
  return request("/api/import/sample", { method: "POST" });
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
