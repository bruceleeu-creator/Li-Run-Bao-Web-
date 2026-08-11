// 利润宝 · Web 外壳类型（本阶段为外壳数据契约，后续逐项替换为真实 API 类型）

/** 七个工作区页面枚举，防止导航与后续 API 路由漂移。 */
export type Workspace =
  | "overview"
  | "import"
  | "budget"
  | "diagnosis"
  | "interaction"
  | "export"
  | "settings";

export interface MetricCard {
  label: string;
  value: string;
  hint?: string;
  accent?: "yellow" | "orange" | "neutral";
}

export interface WorkflowStep {
  key: Workspace;
  label: string;
}

export interface ExportDeliverable {
  id: string;
  name: string;
  format: "Word" | "PDF" | "Excel";
  enabled: boolean;
  note?: string;
}

// ── 后端 API 契约 ──────────────────────────────────────────────────────

export interface IndicatorValue {
  value: number;
  note: string;
  estimate: boolean;
}

export interface SessionSummary {
  company_name: string;
  industry: string;
  years: number[];
  matched: number;
  unmatched: string[];
  warnings: string[];
  latest_year: number | null;
}

export type IndicatorMap = Record<string, IndicatorValue>;

export interface PreviewSection {
  title: string;
  grid: string[][];
}

export interface PreviewImage {
  page: number;
  data: string; // data:image/png;base64,...
}

export interface FilePreview {
  name: string;
  kind: string;
  /** PDF 类型：scan（扫描件，需配置 AI 才能导入）/ text / mixed；非 PDF 无此字段 */
  pdf_type?: string;
  sections: PreviewSection[];
  images: PreviewImage[];
  notes: string[];
}

/** 导入管线 data_quality（与后端 pipeline 契约一致） */
export interface DataQuality {
  confidence?: "high" | "medium" | "low" | string;
  text_layer?: boolean;
  ocr_used?: boolean;
  matched_cells?: number;
  require_confirm?: boolean;
  export_blocked?: boolean;
  reconciliation?: {
    ok?: boolean;
    hard_fail?: boolean;
    error_count?: number;
    warning_count?: number;
    errors?: string[];
    warnings?: string[];
  };
  expense_anomalies?: Array<{ type?: string; message?: string; severity?: string }>;
  parse_notes?: string[];
}

/** PolicySnapshot 简版（导入/会话共用） */
export interface PolicySnapshot {
  industry_key?: string;
  e2?: number;
  e3?: number;
  e4?: number;
  e2_industry_contribution?: number;
  e3_company_contribution?: number;
  e4_income_tax_rate?: number;
  e3_basis?: string;
  e4_source?: string;
  near_zero_selling?: boolean;
  fee_growth_mode?: string;
  revenue_volatile?: boolean;
}

export interface ImportResponse {
  summary: SessionSummary;
  indicators: IndicatorMap[];
  years: number[];
  previews: FilePreview[];
  data_quality?: DataQuality;
  policy?: PolicySnapshot;
  reconciliation?: DataQuality["reconciliation"];
  cit_synthesis?: Record<string, unknown>;
  expense_anomalies?: DataQuality["expense_anomalies"];
  case_id?: string | null;
  message?: string;
}

export interface PreviewResponse {
  files: FilePreview[];
}

export interface SessionResponse {
  session: SessionSummary | null;
  indicators: IndicatorMap[];
  years: number[];
  data_quality?: DataQuality;
  policy?: PolicySnapshot;
}

export interface IndustriesResponse {
  /** 新契约：带说明的行业项 [{name, desc}] */
  industries: IndustryItem[];
  /** 向后兼容：纯名称列表 */
  names: string[];
  default: string;
}

// ── 行业推荐 ──────────────────────────────────────────────────────────

export interface IndustryItem {
  name: string;
  desc: string;
}

export interface IndustryRecommendResponse {
  industry: string;
  reason: string;
  source: "ai" | "rule";
  fallback: boolean;
}

// ── 第一轮诊断 ────────────────────────────────────────────────────────

export interface DiagnosisOption {
  label: string;
  name: string;
  description: string;
  target_value: number;
  tax_rate: number;
  est_saving: number;
  cost_saving: number;
  tax_saving: number;
  tax_impact: number;
  feasibility: string;
  risk_level: string;
  action_note: string;
  deduction_rate: number;
}

export interface DiagnosisFinding {
  id: string;
  title: string;
  category: string;
  severity: string;
  fact: string;
  benchmark: string;
  suggestion: string;
  current_value: number;
  target_value: number;
  unit: string;
  status: string;
  /** 该发现的 A/B/C 选项是否由 AI 增强 */
  ai_enhanced: boolean;
  /** rule | ai | rule+ai */
  source?: string;
  options: DiagnosisOption[];
}

export interface DiagnosisResponse {
  company_name: string;
  industry: string;
  industry_fallback: boolean;
  vat_estimate_note: string;
  ai_used: boolean;
  /** DeepSeek 新补充的发现条数（可选，旧缓存可能无此字段） */
  ai_discover_count?: number;
  /** 诊断阶段 AI 状态说明 */
  ai_message?: string;
  years: number[];
  findings: DiagnosisFinding[];
}

export interface DiagnosisGetResponse {
  diagnosis: DiagnosisResponse | null;
}

// ── A/B/C 互动 ────────────────────────────────────────────────────────

export interface InteractionDecision {
  finding_id: string;
  finding_title: string;
  option_label: string;
  option_name: string;
  current_value: number;
  target_value: number;
  est_saving: number;
  risk_level: string;
  strategy_note: string;
  trend: string;
  change_amount: number;
  change_pct: string;
  action_detail: string;
  cautions: string;
}

export interface Draft2Entry {
  finding_id: string;
  finding_title: string;
  option_label: string;
  option_name: string;
  trend: string;
  current_value: number;
  target_value: number;
  change_amount: number;
  change_pct: string;
  est_saving: number;
  action_detail: string;
  cautions: string;
  risk_level: string;
  cost_saving: number;
  tax_saving: number;
  tax_impact: number;
  tax_rate: number;
  deduction_rate: number;
}

export interface InteractionState {
  state: "IDLE" | "FINDING_LOOP" | "DRAFT2" | "CONFIRMATION" | "FINAL";
  current_index: number | null;
  total: number;
  current_finding: DiagnosisFinding | null;
  decisions: InteractionDecision[];
  draft2: Draft2Entry[];
  feasibility_score: number;
  feasibility_breakdown: string[];
  is_export_unlocked: boolean;
  user_confirmed: boolean;
  ai_fallback_message: string;
}
