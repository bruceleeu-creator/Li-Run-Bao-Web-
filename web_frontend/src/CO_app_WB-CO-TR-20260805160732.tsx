import { useCallback, useEffect, useRef, useState } from "react";
import {
  clearAIConfig,
  clearSession,
  confirmInteraction,
  deleteReport,
  deleteImportHistory,
  fetchAIConfig,
  fetchActiveYearsSummaryJob,
  fetchDiagnosis,
  fetchHealth,
  fetchImportHistory,
  fetchIndustries,
  fetchInteractionState,
  fetchReport,
  fetchReports,
  fetchSavedPreviews,
  fetchSession,
  fetchYearsSummaryJob,
  importFiles,
  identifyCompany,
  loadImportHistory,
  previewFiles,
  recommendIndustry,
  runDiagnosis,
  downloadExport,
  downloadBudgetExportAsync,
  generateBudgetAdvice,
  fetchExportStatus,
  saveAIConfig,
  startInteraction,
  startYearsSummaryJob,
  submitDecision,
  summarizePreview,
  type AIReportDetail,
  type AIReportItem,
  type AIReportJob,
  type BudgetAdviceItem,
  type BudgetAdviceResponse,
} from "./CO_api_WB-CO-TR-20260805160732";
import type {
  DataQuality,
  DiagnosisResponse,
  ExportDeliverable,
  FilePreview,
  ImportHistoryItem,
  IndicatorMap,
  ImportResponse,
  IndustryItem,
  IndustryRecommendResponse,
  InteractionState,
  NumericAudit,
  PolicySnapshot,
  SessionResponse,
  WorkflowStep,
  Workspace,
} from "./CO_types_WB-CO-TR-20260805160732";

/** 数据质量 / 税率政策条（导入与会话共用） */
function QualityPolicyBanner({
  quality,
  policy,
}: {
  quality?: DataQuality | null;
  policy?: PolicySnapshot | null;
}) {
  if (!quality && !policy) return null;
  const conf = quality?.confidence || "—";
  const confLabel =
    conf === "high" ? "高" : conf === "medium" ? "中" : conf === "low" ? "低" : String(conf);
  const rec = quality?.reconciliation;
  const e4 = policy?.e4 ?? policy?.e4_income_tax_rate;
  const e3 = policy?.e3 ?? policy?.e3_company_contribution;
  const e2 = policy?.e2 ?? policy?.e2_industry_contribution;
  const pct = (v?: number) =>
    v == null || Number.isNaN(v) ? "—" : `${(Number(v) * 100).toFixed(2)}%`;
  const warnCount =
    (rec?.warning_count ?? rec?.warnings?.length ?? 0) +
    (quality?.expense_anomalies?.length ?? 0);
  const errCount = rec?.error_count ?? rec?.errors?.length ?? 0;
  const tone =
    conf === "low" || rec?.hard_fail || errCount > 0
      ? "status--error"
      : conf === "medium" || warnCount > 0
        ? "status--warn"
        : "status--ok";
  return (
    <div className={`status ${tone}`} style={{ marginTop: 8, marginBottom: 8 }} role="status">
      <strong>数据质量</strong>
      {" · 置信度 "}
      <strong>{confLabel}</strong>
      {rec?.ok === false || rec?.hard_fail
        ? " · 勾稽异常"
        : errCount > 0
          ? ` · 勾稽错误 ${errCount}`
          : " · 勾稽通过"}
      {warnCount > 0 ? ` · 警告 ${warnCount}` : ""}
      {quality?.require_confirm ? " · 建议人工核验后再导出" : ""}
      {quality?.numeric_grade ? ` · 数字质检 ${quality.numeric_grade}（${quality.numeric_score ?? "—"} 分）` : ""}
      {(e2 != null || e3 != null || e4 != null) && (
        <>
          <br />
          <strong>政策</strong>
          {` · E2 ${pct(e2)} · E3 ${pct(e3)}（${policy?.e3_basis || "—"}） · E4 ${pct(e4)}（${policy?.e4_source || "—"}）`}
          {policy?.industry_key ? ` · 行业 ${policy.industry_key}` : ""}
          {policy?.near_zero_selling ? " · 无销售费用型" : ""}
        </>
      )}
    </div>
  );
}

// ── 外壳数据（流程进度仍为模拟；指标与导入为真实 API 数据）──

// 轻量 markdown 分段渲染：把 AI 报告的 markdown 按标题/表格/列表/段落
// 拆成独立 DOM 块，避免整块 <pre> 导致内容过长被截断。
function MarkdownBlock({ text }: { text: string }) {
  const lines = (text || "").split("\n");
  const blocks: React.ReactNode[] = [];
  let i = 0;
  let key = 0;

  const parseTable = (start: number): { rows: string[][]; next: number } | null => {
    // 表头行、分隔行、数据行，连续扫描
    const rows: string[][] = [];
    let j = start;
    while (j < lines.length && lines[j].trim().startsWith("|")) {
      const cells = lines[j]
        .trim()
        .replace(/^\||\|$/g, "")
        .split("|")
        .map((c) => c.trim());
      rows.push(cells);
      j += 1;
    }
    if (rows.length >= 2) return { rows, next: j };
    return null;
  };

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();
    // 空行跳过
    if (!trimmed) {
      i += 1;
      continue;
    }
    // 表格
    if (trimmed.startsWith("|")) {
      const t = parseTable(i);
      if (t) {
        const header = t.rows[0];
        const bodyRows = t.rows.filter((r, idx) => idx >= 1 && !r.every((c) => /^[-:]+$/.test(c)));
        blocks.push(
          <div className="md-table-wrap" key={key++}>
            <table className="md-table">
              {header ? (
                <thead>
                  <tr>{header.map((h, hi) => <th key={hi}>{h}</th>)}</tr>
                </thead>
              ) : null}
              <tbody>
                {bodyRows.map((r, ri) => (
                  <tr key={ri}>{r.map((c, ci) => <td key={ci}>{c}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>,
        );
        i = t.next;
        continue;
      }
    }
    // 标题
    const hMatch = /^(#{1,4})\s+(.*)$/.exec(trimmed);
    if (hMatch) {
      const level = Math.min(hMatch[1].length, 4);
      const H = `h${level + 1}` as "h2" | "h3" | "h4" | "h5";
      blocks.push(<H key={key++} className={`md-heading md-heading--${level}`}>{hMatch[2]}</H>);
      i += 1;
      continue;
    }
    // 引用（> 数据来源等）
    if (trimmed.startsWith(">")) {
      blocks.push(
        <p key={key++} className="md-quote">
          {trimmed.replace(/^>\s?/, "")}
        </p>,
      );
      i += 1;
      continue;
    }
    // 列表（- 或 1.）
    if (/^[-*]\s+/.test(trimmed) || /^\d+[.、]\s+/.test(trimmed)) {
      const items: string[] = [];
      while (i < lines.length && (/^[-*]\s+/.test(lines[i].trim()) || /^\d+[.、]\s+/.test(lines[i].trim()))) {
        items.push(lines[i].trim().replace(/^[-*]\s+/, "").replace(/^\d+[.、]\s+/, ""));
        i += 1;
      }
      blocks.push(
        <ul key={key++} className="md-list">
          {items.map((it, idx) => <li key={idx}>{it}</li>)}
        </ul>,
      );
      continue;
    }
    // 普通段落（连续多行合并为一个段落）
    const para: string[] = [line];
    i += 1;
    while (i < lines.length && lines[i].trim() && !lines[i].trim().startsWith("|") && !/^#{1,4}\s/.test(lines[i].trim())) {
      para.push(lines[i]);
      i += 1;
    }
    blocks.push(<p key={key++} className="md-para">{para.join(" ")}</p>);
  }

  return <div className="md-block">{blocks}</div>;
}

const WORKFLOW: readonly WorkflowStep[] = [
  { key: "overview", label: "导入财报" },
  { key: "diagnosis", label: "第一轮诊断" },
  { key: "interaction", label: "A/B/C 互动" },
  { key: "export", label: "第二稿与导出" },
] as const;

/** 主流程页面序列：上一步/下一步导航按此顺序推进 */
const FLOW_PAGES: readonly Workspace[] = ["overview", "diagnosis", "interaction", "export"] as const;

const NAV_ITEMS: readonly { key: Workspace; label: string; group: string }[] = [
  { key: "overview", label: "导入财报", group: "工作区" },
  { key: "diagnosis", label: "诊断", group: "工作区" },
  { key: "interaction", label: "互动", group: "工作区" },
  { key: "export", label: "第二稿与导出", group: "工作区" },
  { key: "settings", label: "设置", group: "系统" },
];

const EXPORT_ITEMS: readonly ExportDeliverable[] = [
  { id: "w", name: "导出 Word 报告", format: "Word", enabled: false, note: "完成诊断与互动后可用" },
  { id: "p", name: "导出 PDF 报告", format: "PDF", enabled: false, note: "完成诊断与互动后可用" },
  { id: "e", name: "导出测算模型", format: "Excel", enabled: false, note: "完成诊断与互动后可用" },
  {
    id: "b",
    name: "导出费用预算三表",
    format: "Excel",
    enabled: false,
    note: "先编制建议 → 导出时自动填入",
  },
] as const;

// ── 后端健康状态（仅本机）──

function useBackendHealth(): { status: "loading" | "ok" | "offline"; bind: string } {
  const [state, setState] = useState<{ status: "loading" | "ok" | "offline"; bind: string }>({
    status: "loading",
    bind: "127.0.0.1",
  });

  useEffect(() => {
    let cancelled = false;
    fetchHealth()
      .then((body) => {
        if (!cancelled) setState({ status: "ok", bind: body.bind ?? "127.0.0.1" });
      })
      .catch(() => {
        if (!cancelled) setState({ status: "offline", bind: "127.0.0.1" });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}

// ── 指标展示辅助 ──────────────────────────────────────────────────────

const INDICATOR_META: Record<string, { label: string; accent: "yellow" | "orange" | "neutral" }> = {
  增值税税负率: { label: "增值税税负率", accent: "orange" },
  所得税税负率: { label: "所得税税负率", accent: "neutral" },
  综合税负率: { label: "综合税负率", accent: "neutral" },
  毛利率: { label: "毛利率", accent: "yellow" },
  净利率: { label: "净利率", accent: "yellow" },
  销售费用率: { label: "销售费用率", accent: "neutral" },
  管理费用率: { label: "管理费用率", accent: "neutral" },
  研发费用率: { label: "研发费用率", accent: "neutral" },
  财务费用率: { label: "财务费用率", accent: "neutral" },
  营业收入: { label: "营业收入", accent: "yellow" },
  利润总额: { label: "利润总额", accent: "neutral" },
};

function formatMoney(v: number): string {
  const wan = v / 10000;
  return `¥ ${wan.toLocaleString("zh-CN", { maximumFractionDigits: 1 })} 万`;
}

function isNumeric(cell: string): boolean {
  // 数字（含千分位、正负号、小数、百分号、货币符号）；空串/纯文字不算
  const s = cell.trim();
  if (!s) return false;
  return /^[-+]?[¥￥$]?[\d][\d,]*(\.[\d]+)?%?$/.test(s);
}

/** 占比/费率展示：小比例加长小数，避免千元/数亿营收显示成 0.00% */
function formatPercent(v: number): string {
  const n = Number(v);
  if (!Number.isFinite(n) || n === 0) return "0%";
  const a = Math.abs(n);
  const digits = a < 0.001 ? 4 : a < 0.01 ? 4 : a < 0.1 ? 3 : 2;
  return `${n.toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}%`;
}

/** budget_ratio_pct 已是百分点（0.0008 表示 0.0008%） */
function formatBudgetRatioPct(pct: number): string {
  return formatPercent(Number(pct) || 0);
}

function MetricCardView({ name, ind }: { name: string; ind: { value: number; note: string; estimate: boolean } }) {
  const meta = INDICATOR_META[name] ?? { label: name, accent: "neutral" as const };
  const display = name === "营业收入" || name === "利润总额" ? formatMoney(ind.value) : formatPercent(ind.value);
  return (
    <div className={`metric-card metric-card--${meta.accent}`}>
      <span className="metric-card__label">{meta.label}</span>
      <strong className="metric-card__value">{ind.value != null ? display : "—"}</strong>
      {ind.estimate || ind.note ? (
        <span className="metric-card__hint">{ind.note || (ind.estimate ? "估算值（基于税金及附加反推）" : "")}</span>
      ) : null}
    </div>
  );
}

// ── 子组件 ────────────────────────────────────────────────────────────

function Sidebar({
  current,
  onNavigate,
}: {
  current: Workspace;
  onNavigate: (w: Workspace) => void;
}) {
  const groups = ["工作区", "系统"] as const;
  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <span className="sidebar__brand-mark">利</span>
        <div className="sidebar__brand-text">
          <strong>利润宝</strong>
          <span>企业财税优化顾问</span>
        </div>
      </div>
      {groups.map((group) => (
        <nav key={group} className="sidebar__group" aria-label={group}>
          <h2 className="sidebar__group-title">{group}</h2>
          {NAV_ITEMS.filter((item) => item.group === group).map((item) => (
            <button
              key={item.key}
              type="button"
              className={`sidebar__item${current === item.key ? " is-active" : ""}`}
              onClick={() => onNavigate(item.key)}
            >
              {item.label}
            </button>
          ))}
        </nav>
      ))}
      <div className="sidebar__footer">
        <span className="sidebar__dot" aria-hidden="true" />
        仅本机运行
      </div>
    </aside>
  );
}

function Topbar({
  session,
  health,
  current,
}: {
  session: SessionResponse;
  health: { status: "loading" | "ok" | "offline"; bind: string };
  current: Workspace;
}) {
  const enterprise = session.session?.company_name || "（未导入）";
  const dataState = session.session ? `已导入 ${session.years.join(" / ")}` : "未导入数据";
  const stepLabel = NAV_ITEMS.find((n) => n.key === current)?.label ?? "导入财报";
  const healthLabel =
    health.status === "ok"
      ? `后端 ${health.bind} 已连接`
      : health.status === "loading"
        ? "正在连接本机后端…"
        : "后端未启动（预览外壳）";
  return (
    <header className="topbar">
      <div className="topbar__enterprise">
        <span className="topbar__label">企业</span>
        <strong>{enterprise}</strong>
      </div>
      <div className="topbar__meta">
        <span className="topbar__chip">数据状态：{dataState}</span>
        <span className="topbar__chip">步骤：{stepLabel}</span>
        <span className="topbar__chip topbar__chip--health" title={healthLabel}>
          {healthLabel}
        </span>
      </div>
      <div className="topbar__notice">所有建议均限于合法税务筹划</div>
    </header>
  );
}

function WorkflowRail({
  current,
  session,
  diagnosisDone,
  interactionDone,
  exportUnlocked,
}: {
  current: Workspace;
  session: SessionResponse;
  diagnosisDone: boolean;
  interactionDone: boolean;
  exportUnlocked: boolean;
}) {
  const currentIdx = WORKFLOW.findIndex((s) => s.key === current);
  // 完成状态由真实数据推导：导入=有会话；诊断=有结果；互动=已进入第二稿；导出=已解锁
  const doneMap: Record<string, boolean> = {
    overview: Boolean(session.session),
    diagnosis: diagnosisDone,
    interaction: interactionDone,
    export: exportUnlocked,
  };
  // 精简样式：只保留编号/✓ 圆点，文字放 title/aria 供悬停与读屏
  return (
    <ol className="workflow workflow--compact" aria-label="主流程步骤">
      {WORKFLOW.map((step, i) => {
        const done = doneMap[step.key] ?? false;
        const state = done ? "done" : i < currentIdx ? "blocked" : i === currentIdx ? "active" : "idle";
        return (
          <li
            key={step.key}
            className={`workflow__step workflow__step--${state}`}
            title={step.label}
            aria-label={`第 ${i + 1} 步 ${step.label}${done ? "（已完成）" : ""}`}
          >
            <span className="workflow__index">{done ? "✓" : i + 1}</span>
          </li>
        );
      })}
    </ol>
  );
}

/** 上一步/下一步导航：按 FLOW_PAGES 顺序推进，带前置校验 */
function StepNav({
  current,
  session,
  diagnosisDone,
  interactionDone,
  exportUnlocked,
  onNavigate,
}: {
  current: Workspace;
  session: SessionResponse;
  diagnosisDone: boolean;
  interactionDone: boolean;
  exportUnlocked: boolean;
  onNavigate: (w: Workspace) => void;
}) {
  const idx = FLOW_PAGES.indexOf(current as (typeof FLOW_PAGES)[number]);
  if (idx < 0) return null; // 非主流程页面不显示导航
  const prev = idx > 0 ? FLOW_PAGES[idx - 1] : null;
  const next = idx < FLOW_PAGES.length - 1 ? FLOW_PAGES[idx + 1] : null;
  const [blockMsg, setBlockMsg] = useState("");

  const goNext = () => {
    if (!next) return;
    if (current === "overview" && !session.session) {
      setBlockMsg("请先完成财报导入（拖入文件或文件夹）");
      return;
    }
    if (current === "diagnosis" && !diagnosisDone) {
      setBlockMsg("请先完成第一轮诊断（进入诊断页自动执行）");
      return;
    }
    if (current === "interaction" && !interactionDone) {
      setBlockMsg("请先完成 A/B/C 互动（处理完所有发现）");
      return;
    }
    setBlockMsg("");
    onNavigate(next);
  };

  return (
    <div className="step-nav">
      <div className="step-nav__inner">
        {prev ? (
          <button type="button" className="btn btn--ghost" onClick={() => onNavigate(prev)}>
            ← 上一步
          </button>
        ) : <span />}
        {blockMsg ? <span className="step-nav__block" role="alert">{blockMsg}</span> : null}
        {next ? (
          <button type="button" className="btn btn--primary" onClick={goNext}>
            下一步 →
          </button>
        ) : <span />}
      </div>
      {current === "export" && !exportUnlocked ? (
        <p className="step-nav__hint">完成互动并确认后即可解锁导出。</p>
      ) : null}
    </div>
  );
}

/** 数字质检面板（core/numeric_audit 报告：恒等式 + 错位归因 + OCR 字面） */
function NumericAuditPanel({ audit }: { audit?: NumericAudit | null }) {
  if (!audit || !audit.engine) return null;
  const score = audit.score ?? 100;
  const grade = audit.grade || "—";
  const tone =
    grade === "高" ? "status--ok" : grade === "中" ? "status--warn" : "status--error";
  const findings = audit.findings || [];
  const identities = audit.identities || [];
  const fmtNum = (v?: number | null) =>
    v == null ? "—" : Number(v).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  return (
    <section className="panel">
      <h2 className="panel__title">数字质检</h2>
      <div className={`status ${tone}`} role="status">
        <strong>评分 {score}</strong>
        {" · 等级 "}
        <strong>{grade}</strong>
        {audit.summary ? ` —— ${audit.summary}` : ""}
      </div>
      {identities.length > 0 ? (
        <table className="indicator-table na-table">
          <thead>
            <tr>
              <th>年度</th>
              <th>恒等式规则</th>
              <th>报告值</th>
              <th>推算值</th>
              <th>差额</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            {identities.map((it) => (
              <tr key={it.id}>
                <td>{it.year ?? "—"}</td>
                <td className="na-rule">{it.rule}</td>
                <td>{fmtNum(it.value)}</td>
                <td>{fmtNum(it.expected)}</td>
                <td>{fmtNum(it.gap)}</td>
                <td>{it.status === "pass" ? "✓" : it.status === "warn" ? "⚠ 提示" : "✕ 异常"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {findings.length > 0 ? (
        <ul className="warning-list">
          {findings.map((f) => (
            <li key={f.id} className="na-finding">
              <span className={`na-sev na-sev--${f.severity}`}>
                {f.severity === "high" ? "高风险" : f.severity === "medium" ? "中风险" : "提示"}
              </span>
              <span className="na-finding__msg">
                {f.message}
                {f.suggestion ? <span className="na-suggestion">→ {f.suggestion}</span> : null}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="panel__note">未发现数字异常。</p>
      )}
      <p className="panel__note">
        质检为确定性核对（会计恒等式、小数点错位归因、逐年跳变、OCR 原文字面），
        只提示不自动改数；高风险发现会要求导出前人工核验。
      </p>
    </section>
  );
}

/**
 * 「导入财报」工作区：原总览 + 财报导入合并页（2026-08-18）。
 * 主列：经营概况 → 财报导入区 → AI 合并报告；
 * 右栏：导入记录卡片（点击快速载入）→ 报告记录。
 */
function ImportWorkspacePage({
  session,
  industries,
  onImported,
  onClear,
  aiConfigured,
  aiConfig,
  configBusy,
  configError,
  reportBusy,
  reportError,
  reportJob,
  reportContent,
  reportBlocked,
  onGenerateReport,
  onSaveAIConfig,
  onClearAIConfig,
}: {
  session: SessionResponse;
  industries: IndustryItem[];
  onImported: (
    resp: ImportResponse,
    opts?: { autoReport?: boolean; restored?: ImportResponse["restored"] },
  ) => void;
  onClear: () => void;
  aiConfigured: boolean;
  aiConfig: { base_url: string; model: string; api_key: string };
  configBusy: boolean;
  configError: string;
  reportBusy: boolean;
  reportError: string;
  reportJob: AIReportJob | null;
  reportContent: string;
  reportBlocked: boolean;
  onGenerateReport: () => void;
  onSaveAIConfig: (cfg: { base_url: string; model: string; api_key: string }) => void;
  onClearAIConfig: () => void;
}) {
  const latest = session.indicators[session.indicators.length - 1];
  const cards = latest
    ? ["增值税税负率", "毛利率", "净利率", "营业收入"].map((k) => latest[k]).filter(Boolean)
    : [];
  // 报告记录管理（从 DB 拉取，刷新/切换后仍保留）
  const [reports, setReports] = useState<AIReportItem[]>([]);
  const [selectedReport, setSelectedReport] = useState<AIReportDetail | null>(null);
  const [reportsError, setReportsError] = useState("");
  // 点击报告后的案例载入提示（已载入 / 仅预览）
  const [reportCaseHint, setReportCaseHint] = useState("");
  // 导入记录卡片：每次导入自动生成，点击快速载入
  const [history, setHistory] = useState<ImportHistoryItem[]>([]);
  const [historyError, setHistoryError] = useState("");
  const [loadingId, setLoadingId] = useState<number | null>(null);

  useEffect(() => {
    fetchReports()
      .then((r) => setReports(r.reports))
      .catch((e) => setReportsError(e instanceof Error ? e.message : String(e)));
  }, []);

  // 导入 / 历史载入 / 清空后刷新卡片（session 摘要对象引用每次都会更换）
  useEffect(() => {
    fetchImportHistory()
      .then((r) => setHistory(r.history))
      .catch((e) => setHistoryError(e instanceof Error ? e.message : String(e)));
  }, [session.session]);

  const onViewReport = async (id: number) => {
    try {
      const detail = await fetchReport(id);
      setSelectedReport(detail);
      setReportsError("");
      // 点击报告记录 = 预览 + 载入对应案例：按 session_version 反查导入记录，
      // 命中则完整载入（财务/诊断/互动进度 + 解锁态）；未命中（记录已删）仅预览
      const version = detail.session_version || "";
      const entry = version ? history.find((h) => h.session_version === version) : null;
      if (entry) {
        setReportCaseHint("已载入该报告对应的案例（经营概况与进度随之上方更新）。");
        await onLoadHistory(entry.id);
      } else {
        setReportCaseHint("该报告对应的导入记录不存在（可能已删除），仅预览报告内容。");
      }
    } catch (e) {
      setReportsError(e instanceof Error ? e.message : String(e));
    }
  };

  const onDeleteReport = async (id: number) => {
    try {
      await deleteReport(id);
      setReports((rs) => rs.filter((r) => r.id !== id));
      if (selectedReport?.id === id) setSelectedReport(null);
    } catch (e) {
      setReportsError(e instanceof Error ? e.message : String(e));
    }
  };

  /** 点击卡片完整载入该案例：财务+诊断+互动进度+已生成报告一并恢复 */
  const onLoadHistory = async (id: number) => {
    setLoadingId(id);
    setHistoryError("");
    try {
      const resp = await loadImportHistory(id);
      onImported(resp, { autoReport: false, restored: resp.restored });
    } catch (e) {
      setHistoryError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingId(null);
    }
  };

  const onRemoveHistory = async (id: number) => {
    try {
      await deleteImportHistory(id);
      setHistory((h) => h.filter((e) => e.id !== id));
    } catch (e) {
      setHistoryError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="ws-grid">
      <div className="ws-grid__main">
        <section className="panel">
          <h2 className="panel__title">经营概况</h2>
          {session.session && latest ? (
            <>
              <MetricGrid
                items={cards.map((ind) => {
                  const name = Object.keys(latest).find((k) => latest[k] === ind)!;
                  return { name, ind };
                })}
              />
              <p className="panel__note">
                数据截至 {session.session.latest_year} 年；企业 {session.session.company_name} · {session.session.industry}
                {session.session.warnings.length > 0
                  ? ` · 解析警告 ${session.session.warnings.length} 条`
                  : ""}
              </p>
            </>
          ) : session.session ? (
            <p className="panel__note">
              已导入 {session.session.company_name} · {session.session.industry}；
              指标年份识别中，AI 合并报告见下方。
            </p>
          ) : (
            <p className="panel__note">尚未导入财报。可在下方导入区上传 Excel/CSV/PDF 财报文件。</p>
          )}
          {session.session ? <QualityPolicyBanner quality={session.data_quality} policy={session.policy} /> : null}
        </section>

        <NumericAuditPanel audit={session.numeric_audit} />

        <ImportSection
          session={session}
          industries={industries}
          onImported={onImported}
          onClear={onClear}
          aiConfigured={aiConfigured}
          aiConfig={aiConfig}
          aiBusy={configBusy}
          aiError={configError}
          aiJob={reportJob}
          aiReport={reportContent}
          onSaveAIConfig={onSaveAIConfig}
          onClearAIConfig={onClearAIConfig}
        />

        <section className="panel">
          <h2 className="panel__title">AI 合并报告</h2>
          <div className="ai-actions">
            <button type="button" className="btn btn--ai" disabled={reportBusy || reportBlocked || !session.session} onClick={onGenerateReport}>
              {reportBusy ? "AI 合并中…" : reportContent || reportJob?.status === "failed" ? "重新生成 AI 报告" : "生成 AI 合并报告"}
            </button>
            <span className={`ai-config__status${aiConfigured ? " is-on" : ""}`}>
              {aiConfigured ? "● AI 已配置" : "○ 未配置（可在设置中配置）"}
            </span>
          </div>
          {reportError ? <div className="status status--error" role="alert">{reportError}</div> : null}
          {reportJob && (reportJob.status === "queued" || reportJob.status === "running") ? (
            <div className="ai-progress" aria-live="polite">
              <div className="ai-progress__head">
                <strong>{reportJob.message || (reportJob.status === "queued" ? "报告任务排队中" : "正在生成跨年合并报告")}</strong>
                <span>
                  {reportJob.progress.total > 0
                    ? `${reportJob.progress.current} / ${reportJob.progress.total}`
                    : `${reportJob.progress.current} / 总页数待确认`}
                </span>
              </div>
              <div
                className={`ai-progress__track${reportJob.progress.total > 0 ? "" : " is-indeterminate"}`}
                role="progressbar"
                aria-label="AI 合并报告生成进度"
                aria-valuetext={
                  reportJob.progress.total > 0
                    ? `${reportJob.progress.current} / ${reportJob.progress.total}`
                    : "总页数待确认"
                }
                {...(reportJob.progress.total > 0
                  ? { "aria-valuemin": 0, "aria-valuemax": reportJob.progress.total, "aria-valuenow": reportJob.progress.current }
                  : {})}
              >
                <span
                  className="ai-progress__bar"
                  style={{ width: reportJob.progress.total > 0 ? `${(reportJob.progress.current / reportJob.progress.total) * 100}%` : "35%" }}
                />
              </div>
            </div>
          ) : null}
          {reportContent ? (
            <div className="ai-result">
              <h4 className="ai-result__title">跨年合并报告</h4>
              <MarkdownBlock text={reportContent} />
            </div>
          ) : null}
        </section>
      </div>

      <aside className="ws-grid__side">
        <section className="panel">
          <h2 className="panel__title">导入记录</h2>
          {historyError ? <div className="status status--error">{historyError}</div> : null}
          {history.length > 0 ? (
            <div className="history-list">
              {history.map((h) => (
                <div key={h.id} className={`history-card${loadingId === h.id ? " is-loading" : ""}`}>
                  <button
                    type="button"
                    className="history-card__main"
                    onClick={() => void onLoadHistory(h.id)}
                    title="点击快速载入该次导入的数据"
                  >
                    <strong className="history-card__name">{h.company_name || "未知企业"}</strong>
                    <span className="history-card__meta">
                      {(h.years || []).join(" / ") || "年份—"} · {h.industry || "—"} · {h.file_count} 份文件
                    </span>
                    <span className="history-card__time">
                      {loadingId === h.id ? "载入中…" : (h.created_at || "").replace("T", " ").slice(0, 16)}
                    </span>
                  </button>
                  <button type="button" className="btn btn--danger btn--sm" onClick={() => void onRemoveHistory(h.id)}>
                    删除
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <p className="panel__note">暂无导入记录。每次导入财报后会在这里生成卡片。</p>
          )}
          <p className="panel__note">
            点击卡片完整载入该案例：经营概况与导入结果自动显示，已做的诊断/互动进度一并恢复，
            已生成过的 AI 报告直接展示（不再重新整理）。
          </p>
        </section>

        <section className="panel">
          <h2 className="panel__title">报告记录</h2>
          {reportsError ? <div className="status status--error">{reportsError}</div> : null}
          {selectedReport ? (
            <div className="report-detail">
              <div className="report-detail__head">
                <h4>{selectedReport.title}</h4>
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={() => {
                    setSelectedReport(null);
                    setReportCaseHint("");
                  }}
                >
                  返回列表
                </button>
              </div>
              {reportCaseHint ? <p className="panel__note">{reportCaseHint}</p> : null}
              <MarkdownBlock text={selectedReport.content} />
            </div>
          ) : reports.length > 0 ? (
            <div className="report-list">
              {reports.map((r) => (
                <div key={r.id} className="report-item">
                  <button type="button" className="report-item__title" onClick={() => void onViewReport(r.id)}>
                    {r.title}
                  </button>
                  <span className="report-item__kind">{r.kind === "years_summary" ? "跨年合并" : "财报整理"}</span>
                  <button type="button" className="btn btn--danger btn--sm" onClick={() => void onDeleteReport(r.id)}>
                    删除
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <p className="panel__note">暂无报告记录。导入并生成 AI 报告后会自动保存在这里；点击报告可预览并载入其对应案例。</p>
          )}
        </section>
      </aside>
    </div>
  );
}

function MetricGrid({ items }: { items: { name: string; ind: IndicatorMap[string] }[] }) {
  return (
    <div className="metric-grid">
      {items.map(({ name, ind }) => (
        <MetricCardView key={name} name={name} ind={ind} />
      ))}
    </div>
  );
}

// 全局 AI 配置面板：设置页主入口 + 导入页快捷入口共用。
// 状态由 App 顶层持有（aiConfigured/aiConfig），保存/清空后通过 onChanged 回传。
const DEFAULT_BASE_URL = "https://api.deepseek.com";
const DEFAULT_MODEL = "deepseek-v4-flash";

function AiConfigPanel({
  configured,
  config,
  busy,
  error,
  onSave,
  onClear,
}: {
  configured: boolean;
  config: { base_url: string; model: string; api_key: string };
  busy: boolean;
  error: string;
  onSave: (cfg: { base_url: string; model: string; api_key: string }) => void;
  onClear: () => void;
}) {
  const [cfg, setCfg] = useState(config);
  // Base URL 为空时默认预填官方地址，用户只需填 API Key 即可保存
  const effectiveBaseUrl = cfg.base_url.trim() || DEFAULT_BASE_URL;
  const effectiveModel = cfg.model.trim() || DEFAULT_MODEL;
  // 保存必须三字段齐全；已有 Key 时允许不重复输入（后端保留旧 Key）
  const canSave = Boolean(
    effectiveBaseUrl && effectiveModel && (cfg.api_key.trim() || configured),
  );
  const missingKeyHint = !configured && !cfg.api_key.trim();

  useEffect(() => {
    setCfg(config);
  }, [config]);

  return (
    <div className="ai-config">
      <div className="ai-config__row">
        <label className="field">
          <span className="field__label">Base URL</span>
          <input
            className="input"
            value={cfg.base_url}
            placeholder={DEFAULT_BASE_URL}
            onChange={(e) => setCfg((c) => ({ ...c, base_url: e.target.value }))}
          />
        </label>
        <label className="field">
          <span className="field__label">模型</span>
          <input
            className="input"
            value={cfg.model}
            placeholder={DEFAULT_MODEL}
            onChange={(e) => setCfg((c) => ({ ...c, model: e.target.value }))}
          />
        </label>
        <label className="field">
          <span className="field__label">API Key（已持久化，重启免重输）</span>
          <input
            className="input"
            type="password"
            value={cfg.api_key}
            placeholder="sk-..."
            onChange={(e) => setCfg((c) => ({ ...c, api_key: e.target.value }))}
          />
        </label>
      </div>
      <div className="ai-config__actions">
        <button
          type="button"
          className="btn btn--primary btn--sm"
          disabled={busy || !canSave}
          onClick={() =>
            onSave({
              base_url: effectiveBaseUrl,
              model: effectiveModel,
              api_key: cfg.api_key.trim(),
            })
          }
        >
          {busy ? "保存中…" : "保存配置"}
        </button>
        <button type="button" className="btn btn--ghost btn--sm" disabled={busy} onClick={onClear}>
          清空（恢复离线）
        </button>
        <span className={`ai-config__status${configured ? " is-on" : ""}`}>
          {configured ? "● AI 已配置" : "○ 未配置"}
        </span>
      </div>
      {!canSave && !busy ? (
        <p className="ai-config__hint">
          保存前请填写 Base URL、模型和 API Key（{missingKeyHint ? "API Key 当前为空，" : ""}
          Base URL 与模型留空时将使用官方默认值）。
        </p>
      ) : null}
      {error ? <div className="status status--error">{error}</div> : null}
    </div>
  );
}

/** 财报导入区（「导入财报」页主列中段；拖拽/文件夹/多文件 + 预览 + AI 整理） */
function ImportSection({
  session,
  industries,
  onImported,
  onClear,
  aiConfigured,
  aiConfig,
  aiBusy,
  aiError,
  aiJob,
  aiReport,
  onSaveAIConfig,
  onClearAIConfig,
}: {
  session: SessionResponse;
  industries: IndustryItem[];
  onImported: (resp: ImportResponse) => void;
  onClear: () => void;
  aiConfigured: boolean;
  aiConfig: { base_url: string; model: string; api_key: string };
  aiBusy: boolean;
  aiError: string;
  /** 导入后自动启动的 AI 总结任务（逐页分段，不会截断） */
  aiJob: AIReportJob | null;
  aiReport: string;
  onSaveAIConfig: (cfg: { base_url: string; model: string; api_key: string }) => void;
  onClearAIConfig: () => void;
}) {
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [toast, setToast] = useState("");
  const [companyName, setCompanyName] = useState("");
  const [industry, setIndustry] = useState("制造业");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [previews, setPreviews] = useState<FilePreview[] | null>(null);
  // 最近一次导入时后端已保存的预览（切页/刷新后仍可查看）
  const [savedPreviews, setSavedPreviews] = useState<FilePreview[] | null>(null);
  const [showSavedPreview, setShowSavedPreview] = useState(false);
  const [previewErr, setPreviewErr] = useState("");
  const [aiMarkdown, setAiMarkdown] = useState("");
  const [aiLocalBusy, setAiLocalBusy] = useState(false);
  // AI 整理分阶段提示（多文件时逐年分析 → 最后合并）
  const [aiStage, setAiStage] = useState("");
  const [aiLocalError, setAiLocalError] = useState("");
  const [showAiConfig, setShowAiConfig] = useState(false);
  // 行业推荐（AI/规则双路径）
  const [recResult, setRecResult] = useState<IndustryRecommendResponse | null>(null);
  const [recBusy, setRecBusy] = useState(false);
  // 自动识别企业名称+行业（选择文件后触发）
  const [identifyInfo, setIdentifyInfo] = useState<{ reason: string; source: "ai" | "rule" } | null>(null);
  const [identifyBusy, setIdentifyBusy] = useState(false);
  const industryDesc =
    industries.find((i) => i.name === industry)?.desc ??
    (industry ? "" : "");

  const acceptAttr = ".xlsx,.csv,.docx,.pptx,.pdf";

  useEffect(() => {
    let cancelled = false;
    fetchSavedPreviews()
      .then((r) => {
        if (!cancelled) setSavedPreviews(r.files.length ? r.files : null);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const onSummarize = async () => {
    if (!previews || previews.length === 0) return;
    // 把预览内容拼成文本喂给 AI（PDF 用 OCR 文本；其他格式用表格文本）
    // 2026-08-09：完整传入全部页/全部行，不再截断前 10 条（保证分析完整）
    const parts: string[] = [];
    for (const pv of previews) {
      parts.push(`文件：${pv.name}`);
      if (pv.kind === "pdf") {
        // PDF：完整采集全部页文本（文本层 + OCR），DeepSeek 读不到图片
        if (pv.notes.length) {
          parts.push(pv.notes.join("\n"));
        }
      } else {
        for (const sec of pv.sections) {
          parts.push(`[${sec.title}]`);
          for (const row of sec.grid.slice(0, 40)) {
            parts.push(row.join(" | "));
          }
        }
        if (pv.notes.length) {
          parts.push(pv.notes.join("\n"));
        }
      }
    }
    setAiLocalBusy(true);
    setAiLocalError("");
    setAiMarkdown("");
    // 多文件时提示按年独立分析（后端分阶段处理：每份提炼 → 合并）
    const fileCount = previews.length;
    setAiStage(
      fileCount > 1
        ? `正在按 ${fileCount} 份文件独立分析（每年单独分析，最后合并对比）…`
        : "正在分析…",
    );
    try {
      const resp = await summarizePreview(parts.join("\n"));
      setAiMarkdown(resp.markdown);
      // 整理完成即自动导入：写入会话并生成「导入记录」卡片（整理结果由后端
      // 自动保存到「报告记录」，点击导入记录卡片即可恢复经营概况）
      if (selectedFiles.length > 0) {
        void runImport(selectedFiles);
      }
    } catch (e) {
      setAiLocalError(e instanceof Error ? e.message : String(e));
      // 未配置时提示去配置
      if (e instanceof Error && e.message.includes("未配置")) {
        setShowAiConfig(true);
      }
    } finally {
      setAiStage("");
      setAiLocalBusy(false);
    }
  };

  const onRecommend = async () => {
    if (!companyName.trim()) return;
    setRecBusy(true);
    try {
      const result = await recommendIndustry(companyName);
      setRecResult(result);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRecBusy(false);
    }
  };

  // 选完文件后自动识别企业名称与行业（AI 优先，规则回退），自动填入表单
  const autoIdentify = async (files: File[], previewList: FilePreview[] | null) => {
    if (!files.length) return;
    setIdentifyBusy(true);
    setIdentifyInfo(null);
    try {
      // 传给后端：文件名 + 预览文本（PDF 用 notes 文本，其他用表格文本摘要）
      const payload = files.map((f) => {
        const pv = previewList?.find((p) => p.name === f.name);
        let text = "";
        if (pv) {
          if (pv.notes.length) text = pv.notes.join("\n").slice(0, 3000);
          else text = (pv.sections || []).slice(0, 3).map((s) => s.grid.map((r) => r.join(" | ")).join("\n")).join("\n").slice(0, 3000);
        }
        return { name: f.name, text };
      });
      const result = await identifyCompany(payload);
      if (result.company_name) setCompanyName(result.company_name);
      if (result.industry) setIndustry(result.industry);
      setIdentifyInfo({ reason: result.reason, source: result.source });
      setError("");
    } catch (e) {
      // 识别失败不阻塞：用户仍可手动填写
      setIdentifyInfo(null);
    } finally {
      setIdentifyBusy(false);
    }
  };

  const runImport = async (files: File[]) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const resp = await importFiles(files, companyName, industry);
      onImported(resp);
      // 导入成功后隐藏文件预览区，只保留导入结果
      setPreviews(null);
      setSavedPreviews(resp.previews && resp.previews.length ? resp.previews : null);
      setShowSavedPreview(false);
      setSelectedFiles([]);
      setPreviewErr("");
      const yearsText = resp.summary.years.join(" / ");
      const merged = resp.summary.years.length > 1 ? ` · 共 ${resp.summary.years.length} 年合并` : "";
      setNotice(`导入成功：${resp.summary.company_name} · ${yearsText} 年${merged}`);
      // 右上角上传成功 toast
      setToast(`上传成功：${resp.summary.company_name}${merged}`);
      window.setTimeout(() => setToast(""), 3500);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const collectFolderFiles = (fileList: FileList | null): File[] => {
    if (!fileList) return [];
    return Array.from(fileList).filter((f) =>
      [".xlsx", ".csv", ".docx", ".pptx", ".pdf"].some((e) => f.name.toLowerCase().endsWith(e)),
    );
  };

  const onFolderPick = (fileList: FileList | null) => {
    const files = collectFolderFiles(fileList);
    setSelectedFiles(files);
    setPreviews(null);
    setShowSavedPreview(false);
    setPreviewErr("");
    setAiMarkdown("");
    setAiLocalError("");
    setError("");
    if (files.length === 0) {
      setError("未找到支持的财报文件（xlsx/csv/docx/pptx/pdf）");
      return;
    }
    setNotice(`已选择 ${files.length} 个文件`);
    // 选完文件后自动预览
    setBusy(true);
    previewFiles(files)
      .then((r) => {
        setPreviews(r.files);
        // 预览完成后自动识别企业名称与行业并填入
        void autoIdentify(files, r.files);
      })
      .catch((e) => setPreviewErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setBusy(false));
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const files = collectFolderFiles(e.dataTransfer.files);
    if (files.length === 0) {
      setError("未识别到支持的财报文件（可拖入文件或文件夹）");
      return;
    }
    setSelectedFiles(files);
    setPreviews(null);
    setShowSavedPreview(false);
    setError("");
    // 拖入后先识别企业名称与行业（不阻塞导入；识别结果填入表单供确认）
    void autoIdentify(files, null);
    void runImport(files);
  };

  const onImport = () => {
    if (selectedFiles.length === 0) {
      setError("请先选择文件");
      return;
    }
    void runImport(selectedFiles);
  };

  return (
    <>
      {toast ? (
        <div className="toast" role="status">
          <span className="toast__icon">✓</span>
          <span className="toast__text">{toast}</span>
        </div>
      ) : null}
      <section className="panel">
        <h2 className="panel__title">财报导入</h2>
        <div
          className={`dropzone-folder${dragOver ? " is-dragover" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
        >
          <strong>拖入文件夹或文件</strong>
          <span>支持将含多年审计报告的文件或文件夹拖到此处，一次导入多份并自动合并</span>
          <div className="dropzone-folder__actions">
            <label className="btn btn--primary btn--sm folder-pick-btn">
              选择文件夹
              <input
                type="file"
                multiple
                {...({ webkitdirectory: "" } as Record<string, string>)}
                style={{ display: "none" }}
                onChange={(e) => onFolderPick(e.target.files)}
              />
            </label>
            <label className="btn btn--ghost btn--sm folder-pick-btn">
              选择多个文件
              <input
                type="file"
                multiple
                accept={acceptAttr}
                style={{ display: "none" }}
                onChange={(e) => onFolderPick(e.target.files)}
              />
            </label>
          </div>
          {selectedFiles.length > 0 ? (
            <div className="file-tags">
              {selectedFiles.map((f) => (
                <span key={f.name} className="file-tag">
                  {f.name}
                </span>
              ))}
            </div>
          ) : null}
        </div>

        <div className="meta-form">
          <label className="field">
            <span className="field__label">
              企业名称
              {identifyBusy ? <span className="identify-tag">AI 识别中…</span> : null}
            </span>
            <input
              className="input"
              value={companyName}
              onChange={(e) => setCompanyName(e.target.value)}
              placeholder="选择文件后自动识别，可手动修改"
            />
          </label>
          <div className="field">
            <span className="field__label">行业（选择后用于行业对标诊断）</span>
            <div className="industry-pick">
              <select className="input" value={industry} onChange={(e) => setIndustry(e.target.value)}>
                {(industries.length ? industries : [{ name: "制造业", desc: "" }, { name: "批发零售业", desc: "" }, { name: "服务业", desc: "" }]).map((ind) => (
                  <option key={ind.name} value={ind.name}>
                    {ind.name}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                disabled={recBusy || !companyName.trim()}
                onClick={() => void onRecommend()}
                title="根据企业名称推荐行业（AI 可用时由 AI 判断，否则按规则匹配）"
              >
                {recBusy ? "推荐中…" : "AI 推荐行业"}
              </button>
            </div>
            {industryDesc ? <p className="industry-pick__desc">{industryDesc}</p> : null}
          {identifyInfo ? (
            <div className="identify-card">
              <span className={`rec-card__source${identifyInfo.source === "ai" ? " is-ai" : ""}`}>
                {identifyInfo.source === "ai" ? "AI 识别" : "规则识别"}
              </span>
              <span className="identify-card__reason">{identifyInfo.reason}</span>
            </div>
          ) : null}
            {recResult ? (
              <div className="rec-card">
                <span className={`rec-card__source${recResult.source === "ai" ? " is-ai" : ""}`}>
                  {recResult.source === "ai" ? "AI 推荐" : "规则推荐"}
                  {recResult.fallback ? "（AI 暂不可用）" : ""}
                </span>
                <span className="rec-card__body">
                  <strong>{recResult.industry}</strong>
                  <span>{recResult.reason}</span>
                </span>
                <button
                  type="button"
                  className="btn btn--primary btn--sm"
                  disabled={recResult.industry === industry}
                  onClick={() => setIndustry(recResult.industry)}
                >
                  {recResult.industry === industry ? "已应用" : "应用"}
                </button>
              </div>
            ) : null}
          </div>
        </div>

        <div className="action-row">
          <button type="button" className="btn btn--primary" disabled={busy} onClick={onImport}>
            {busy ? "处理中…" : "开始导入"}
          </button>
          {session.session ? (
            <button type="button" className="btn btn--danger" disabled={busy} onClick={onClear}>
              重新导入
            </button>
          ) : null}
        </div>

        {error ? <div className="status status--error">{error}</div> : null}
        {notice ? <div className="status status--ok">{notice}</div> : null}
      </section>

      {savedPreviews && savedPreviews.length > 0 ? (
        <section className="panel saved-preview-bar">
          <div className="saved-preview-bar__row">
            <span className="saved-preview-bar__status">
              ✓ 预览已保存（本机 · 切换页面不丢失）
            </span>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => {
                if (showSavedPreview && previews) {
                  setPreviews(null);
                  setShowSavedPreview(false);
                } else {
                  setPreviews(savedPreviews);
                  setShowSavedPreview(true);
                }
              }}
            >
              {showSavedPreview && previews
                ? "收起预览"
                : `查看已保存预览（${savedPreviews.length} 份）`}
            </button>
          </div>
        </section>
      ) : null}

      {selectedFiles.length > 0 || previews ? (
        <section className="panel">
          <div className="preview-actions">
            <h2 className="panel__title">文件完整采集</h2>
            <div className="ai-actions">
              <button
                type="button"
                className="btn btn--ai"
                disabled={aiLocalBusy || !previews}
                onClick={() => void onSummarize()}
                title="整理完成会自动导入并写入导入记录；整理结果保存到报告记录"
              >
                {aiLocalBusy ? "AI 整理中…" : "AI 整理并导入"}
              </button>
              {aiStage ? (
                <span className="ai-stage" role="status">{aiStage}</span>
              ) : null}
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => setShowAiConfig((v) => !v)}
              >
                {aiConfigured ? "AI 已配置" : "配置 AI"}
              </button>
            </div>
          </div>

          {showAiConfig ? (
            <AiConfigPanel
              configured={aiConfigured}
              config={aiConfig}
              busy={aiBusy}
              error={aiError}
              onSave={onSaveAIConfig}
              onClear={onClearAIConfig}
            />
          ) : null}

          {aiError ? <div className="status status--error">{aiError}</div> : null}
          {aiLocalError ? <div className="status status--error">{aiLocalError}</div> : null}
          {aiMarkdown ? (
            <div className="ai-result">
              <h4 className="ai-result__title">AI 整理结果</h4>
              <MarkdownBlock text={aiMarkdown} />
            </div>
          ) : null}
          {selectedFiles.length > 0 ? (
            <div className="file-tags">
              {selectedFiles.map((f) => (
                <span key={f.name} className="file-tag">
                  {f.name}
                </span>
              ))}
            </div>
          ) : null}
          {previewErr ? <div className="status status--error">{previewErr}</div> : null}
          {previews ? (
            <div className="preview-list">
              {previews.map((pv) => (
                <div key={pv.name} className="preview-file">
                  <h3 className="preview-file__name">
                    {pv.name}
                    <span className="preview-file__kind">{pv.kind.toUpperCase()}</span>
                    {pv.kind === "pdf" ? (
                      <span className="preview-file__complete">完整采集 ✓</span>
                    ) : null}
                  </h3>
                  {pv.kind === "pdf" && pv.pdf_type === "scan" && !aiConfigured ? (
                    <div className="status status--warn">
                      检测到扫描件 PDF（无文字层），导入前请先「配置 AI」，否则解析将失败。
                    </div>
                  ) : null}
                  {pv.notes.length > 0 ? (
                    <div className="preview-notes">
                      {pv.notes.map((n, i) => (
                        <p key={i}>{n}</p>
                      ))}
                    </div>
                  ) : null}
                  {pv.sections.map((sec, si) => (
                    <div key={si} className="preview-table-wrap">
                      <h4 className="preview-table__title">
                        {sec.title}
                        <span className="preview-table__rows">共 {sec.grid.length} 行 · 完整显示</span>
                      </h4>
                      <table className="preview-table">
                        <tbody>
                          {sec.grid.map((row, ri) => (
                            <tr key={ri}>
                              {row.map((cell, ci) => (
                                <td key={ci} className={isNumeric(cell) ? "is-num" : ""}>{cell}</td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          ) : null}
        </section>
      ) : null}

      {session.session ? (
        <section className="panel">
          <h2 className="panel__title">导入结果</h2>
          <div className="result-meta">
            <span>
              企业：<strong>{session.session.company_name}</strong>
            </span>
            <span>
              行业：<strong>{session.session.industry}</strong>
            </span>
            <span>
              年份：<strong>{session.session.years.join(" / ")}</strong>
            </span>
            <span>
              同义词归并：<strong>{session.session.matched}</strong> 项
              {session.session.unmatched.length > 0 ? `，未匹配 ${session.session.unmatched.length} 项` : ""}
            </span>
          </div>
          {session.session.warnings.length > 0 ? (
            <ul className="warning-list">
              {session.session.warnings.map((w, i) => (
                <li key={i}>⚠ {w}</li>
              ))}
            </ul>
          ) : null}
          <table className="indicator-table">
            <thead>
              <tr>
                <th>指标</th>
                {session.years.map((y) => (
                  <th key={y}>{y}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {["增值税税负率", "所得税税负率", "毛利率", "净利率", "研发费用率", "营业收入"].map((key) => (
                <tr key={key}>
                  <td>
                    {INDICATOR_META[key]?.label ?? key}
                    {session.indicators[0]?.[key]?.estimate ? <span className="est-tag">估算</span> : null}
                  </td>
                  {session.indicators.map((row, i) => {
                    const ind = row[key];
                    if (!ind) return <td key={i}>—</td>;
                    const display = key === "营业收入" ? formatMoney(ind.value) : formatPercent(ind.value);
                    return <td key={i}>{ind.value != null ? display : "—"}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          {/* 导入后自动 AI 总结：进度与结果统一展示在下方「AI 合并报告」，此处仅留状态指引 */}
          <div className="ai-summary-block">
            <h3 className="ai-summary-title">AI 总结预览</h3>
            {aiJob && (aiJob.status === "queued" || aiJob.status === "running") ? (
              <div className="status status--info" role="status">
                {aiJob.status === "queued" ? "AI 总结任务排队中" : "AI 总结进行中"}：
                逐页分段分析（每年独立 → 合并对比），进度见下方「AI 合并报告」。
              </div>
            ) : aiJob?.status === "failed" ? (
              <div className="status status--warn" role="status">
                AI 总结失败：错误详情与重新生成入口见下方「AI 合并报告」。
              </div>
            ) : aiReport ? (
              <p className="panel__note">✓ AI 总结已生成，完整报告见下方「AI 合并报告」。</p>
            ) : aiConfigured ? (
              <p className="panel__note">AI 总结已生成，见下方「AI 合并报告」。</p>
            ) : (
              <p className="panel__note">
                ○ 未配置 AI：当前显示确定性指标（上方表格）。配置 AI 后导入将自动生成逐页分段总结预览。
              </p>
            )}
          </div>
        </section>
      ) : null}
    </>
  );
}
// 风险等级 → 语义色 class：低风险绿色在上、中风险橙色居中、高风险红色在下
const SEVERITY_CLASS: Record<string, string> = {
  低: "finding--sev-low",
  中: "finding--sev-med",
  高: "finding--sev-high",
};
const SEVERITY_RANK: Record<string, number> = { 低: 0, 中: 1, 高: 2 };
const severityClassOf = (severity: string) => SEVERITY_CLASS[severity] ?? "finding--sev-low";
const sortFindings = <T extends { severity: string }>(items: T[]) =>
  [...items].sort(
    (a, b) => (SEVERITY_RANK[a.severity] ?? 9) - (SEVERITY_RANK[b.severity] ?? 9),
  );

function DiagnosisPage({
  session,
  onDiagnosisDone,
}: {
  session: SessionResponse;
  onDiagnosisDone: (done: boolean, meta?: { resetDownstream?: boolean }) => void;
}) {
  const [diagnosis, setDiagnosis] = useState<DiagnosisResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);

  const applyDiagnosis = useCallback(
    (fresh: DiagnosisResponse, meta?: { resetDownstream?: boolean }) => {
      setDiagnosis(fresh);
      onDiagnosisDone(fresh.findings.length > 0, meta);
    },
    [onDiagnosisDone],
  );

  /** 首次进入：有缓存则展示缓存；无缓存则自动跑一轮诊断。 */
  const loadDiagnosis = useCallback(async () => {
    if (!session.session) {
      setDiagnosis(null);
      onDiagnosisDone(false);
      return;
    }
    setBusy(true);
    setError("");
    try {
      const saved = await fetchDiagnosis();
      if (saved.diagnosis) {
        applyDiagnosis(saved.diagnosis);
      } else {
        // 无已保存诊断：自动执行第一轮诊断（规则 + DeepSeek）
        const fresh = await runDiagnosis();
        applyDiagnosis(fresh);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      onDiagnosisDone(false);
    } finally {
      setBusy(false);
    }
  }, [session.session, onDiagnosisDone, applyDiagnosis]);

  useEffect(() => {
    void loadDiagnosis();
  }, [loadDiagnosis]);

  /** 重新诊断：强制 POST /api/diagnosis/run，不走缓存；下游互动/导出状态一并重置。 */
  const onRerun = () => {
    if (!session.session || busy) return;
    setBusy(true);
    setError("");
    void (async () => {
      try {
        const fresh = await runDiagnosis();
        applyDiagnosis(fresh, { resetDownstream: true });
        setExpanded(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    })();
  };

  const fmtMoney = (v: number) => `¥ ${(v / 10000).toLocaleString("zh-CN", { maximumFractionDigits: 1 })} 万`;
  const fmtSaving = (v: number) =>
    v === 0 ? "¥ 0" : `¥ ${(v / 10000).toLocaleString("zh-CN", { maximumFractionDigits: 1 })} 万`;

  const severityLabel: Record<string, string> = { 高: "高风险", 中: "中风险", 低: "低风险" };
  const highRiskCount = diagnosis ? diagnosis.findings.filter((f) => f.severity === "高").length : 0;
  const aiEnhancedCount = diagnosis ? diagnosis.findings.filter((f) => f.ai_enhanced).length : 0;

  return (
    <>
      <section className="panel">
        <div className="diag-head">
          <h2 className="panel__title">第一轮诊断</h2>
          <div className="ai-actions">
            <button type="button" className="btn btn--ai" disabled={busy || !session.session} onClick={onRerun}>
              {busy ? "诊断分析中…" : "重新诊断"}
            </button>
          </div>
        </div>
        {!session.session ? (
          <div className="diag-state">
            <span className="diag-state__icon" aria-hidden="true">◎</span>
            <p className="panel__note">尚未导入财报。请先在「导入财报」页上传财报文件，再进行诊断。</p>
          </div>
        ) : busy ? (
          <div className="placeholder diag-state">
            <span className="diag-state__icon diag-state__icon--spin" aria-hidden="true">◌</span>
            <strong>AI 正在介入诊断</strong>
            <span>规则引擎计算指标 → 行业对标 → 生成 A/B/C 选项（AI 可用时增强）</span>
          </div>
        ) : error ? (
          <div className="status status--error" role="alert">{error}</div>
        ) : diagnosis ? (
          <>
            <div className={`diag-ai-status${diagnosis.ai_used ? " is-on" : ""}`}>
              {diagnosis.ai_used
                ? `● AI 已介入${
                    typeof diagnosis.ai_discover_count === "number" && diagnosis.ai_discover_count > 0
                      ? `（DeepSeek 补充 ${diagnosis.ai_discover_count} 条）`
                      : "（选项/发现已增强）"
                  }`
                : "○ 规则引擎诊断（AI 未配置，选项为规则生成）"}
              {diagnosis.ai_message ? (
                <div className="panel__note" style={{ marginTop: 6 }}>{diagnosis.ai_message}</div>
              ) : null}
            </div>

            <div className="diag-summary">
              <div className="diag-metric">
                <span className="diag-metric__label">发现</span>
                <div className="diag-metric__value-row">
                  <strong className="diag-metric__value">{diagnosis.findings.length}</strong>
                  <span className="diag-metric__unit">条</span>
                </div>
              </div>
              <div className="diag-metric diag-metric--risk">
                <span className="diag-metric__label">高风险</span>
                <div className="diag-metric__value-row">
                  <strong className="diag-metric__value">{highRiskCount}</strong>
                  <span className="diag-metric__unit">条</span>
                </div>
              </div>
              <div className="diag-metric diag-metric--ai">
                <span className="diag-metric__label">AI 增强建议</span>
                <div className="diag-metric__value-row">
                  <strong className="diag-metric__value">{aiEnhancedCount}</strong>
                  <span className="diag-metric__unit">条</span>
                </div>
              </div>
              <div className="diag-metric diag-metric--mode">
                <span className="diag-metric__label">诊断方式</span>
                <div className="diag-metric__value-row">
                  <strong className="diag-metric__value diag-metric__value--mode">
                    {diagnosis.ai_used ? "AI 增强" : "规则引擎"}
                  </strong>
                </div>
              </div>
            </div>

            <div className="diag-meta-chips">
              <span className="meta-chip"><span className="meta-chip__label">企业</span><strong>{diagnosis.company_name}</strong></span>
              <span className="meta-chip"><span className="meta-chip__label">行业</span><strong>{diagnosis.industry}</strong></span>
              <span className="meta-chip"><span className="meta-chip__label">年份</span><strong>{(diagnosis.years || []).join(" / ") || "—"}</strong></span>
            </div>
            {diagnosis.industry_fallback ? (
              <div className="diag-warn">⚠ 行业基准回退到制造业（未知行业）</div>
            ) : null}
            {diagnosis.vat_estimate_note ? (
              <div className="diag-vat-note">⚠ {diagnosis.vat_estimate_note}</div>
            ) : null}

            <div className="diag-risk-legend" aria-label="风险等级图例">
              <span className="diag-risk-legend__item diag-risk-legend__item--low">低风险 · 绿 · 上方</span>
              <span className="diag-risk-legend__item diag-risk-legend__item--med">中风险 · 橙 · 中间</span>
              <span className="diag-risk-legend__item diag-risk-legend__item--high">高风险 · 红 · 下方</span>
            </div>
            <div className="finding-list">
              {sortFindings(diagnosis.findings).map((f) => (
                <article key={f.id} className={`finding ${severityClassOf(f.severity)}`}>
                  <div className="finding__head">
                    <span className="finding__tag">
                      {severityLabel[f.severity] ?? f.severity} · {f.category}
                    </span>
                    {f.ai_enhanced ? <span className="est-tag">AI 增强</span> : null}
                    <button
                      type="button"
                      className="finding__toggle"
                      onClick={() => setExpanded((prev) => (prev === f.id ? null : f.id))}
                    >
                      {expanded === f.id ? "收起 ▴" : "展开选项 ▾"}
                    </button>
                  </div>
                  <h3>{f.title}</h3>
                  <p className="finding__field"><span className="finding__field-label">事实</span>{f.fact}</p>
                  <p className="finding__field"><span className="finding__field-label">行业对标</span>{f.benchmark}</p>
                  <p className="finding__field"><span className="finding__field-label">建议</span>{f.suggestion}</p>
                  {expanded === f.id ? (
                    <div className="finding-options">
                      {f.options.map((o) => (
                        <div key={o.label} className="finding-option">
                          <div className="finding-option__head">
                            <span className="finding-option__label">{o.label}</span>
                            <strong>{o.name}</strong>
                            <span className="finding-option__saving">净影响 {fmtSaving(o.est_saving)}</span>
                          </div>
                          <p>{o.description}</p>
                          <div className="finding-option__meta">
                            <span className="finding-option__meta-item">可行性：{o.feasibility}</span>
                            <span className="finding-option__meta-item">风险：{o.risk_level}</span>
                            <span className="finding-option__meta-item">目标值：{o.target_value ? (f.unit === "元" ? fmtMoney(o.target_value) : `${o.target_value}%`) : "—"}</span>
                          </div>
                          {o.action_note ? <p className="finding-option__action">操作：{o.action_note}</p> : null}
                        </div>
                      ))}
                    </div>
                  ) : null}
                </article>
              ))}
            </div>
            {diagnosis.findings.length === 0 ? (
              <div className="diag-state diag-state--ok">
                <span className="diag-state__icon" aria-hidden="true">✓</span>
                <p className="panel__note">未发现明显异常。可点击「重新诊断」或直接进入 A/B/C 互动。</p>
              </div>
            ) : null}
          </>
        ) : null}
      </section>
    </>
  );
}

function InteractionPage({
  session,
  onInteractionChange,
}: {
  session: SessionResponse;
  onInteractionChange: (state: InteractionState) => void;
}) {
  const [state, setState] = useState<InteractionState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [strategyNote, setStrategyNote] = useState("");

  const loadState = useCallback(async () => {
    if (!session.session) {
      setState(null);
      return;
    }
    setBusy(true);
    setError("");
    try {
      const st = await fetchInteractionState();
      if (st.state === "IDLE") {
        const started = await startInteraction();
        setState(started);
        onInteractionChange(started);
      } else {
        setState(st);
        onInteractionChange(st);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [session.session, onInteractionChange]);

  useEffect(() => {
    void loadState();
  }, [loadState]);

  const onChoose = async (findingId: string, label: string) => {
    setBusy(true);
    setError("");
    try {
      const next = await submitDecision(findingId, label, strategyNote);
      setStrategyNote("");
      setState(next);
      onInteractionChange(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const onConfirm = async () => {
    setBusy(true);
    setError("");
    try {
      const next = await confirmInteraction(true);
      setState(next);
      onInteractionChange(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const fmtMoney = (v: number) => `¥ ${(v / 10000).toLocaleString("zh-CN", { maximumFractionDigits: 1 })} 万`;

  if (!session.session) {
    return (
      <section className="panel">
        <h2 className="panel__title">A/B/C 互动</h2>
        <p className="panel__note">尚未导入财报。请先完成财报导入与第一轮诊断，再进行 A/B/C 互动。</p>
      </section>
    );
  }

  if (busy && !state) {
    return (
      <section className="panel">
        <h2 className="panel__title">A/B/C 互动</h2>
        <div className="placeholder">
          <strong>正在准备互动会话</strong>
          <span>加载诊断结果与 A/B/C 选项…</span>
        </div>
      </section>
    );
  }

  if (error && !state) {
    return (
      <section className="panel">
        <h2 className="panel__title">A/B/C 互动</h2>
        <div className="status status--error" role="alert">{error}</div>
        <button type="button" className="btn btn--primary" onClick={() => void loadState()}>
          重试
        </button>
      </section>
    );
  }

  const cur = state?.current_finding ?? null;

  return (
    <>
      <section className="panel">
        <h2 className="panel__title">A/B/C 互动</h2>
        {error ? <div className="status status--error" role="alert">{error}</div> : null}
        {state?.ai_fallback_message ? <p className="panel__note">{state.ai_fallback_message}</p> : null}

        {state?.state === "FINDING_LOOP" && cur ? (
          <>
            <div className="interaction-progress">
              发现进度：{state.current_index != null ? state.current_index + 1 : "?"} / {state.total}
            </div>
            <article className={`finding ${severityClassOf(cur.severity)}`}>
              <div className="finding__head">
                <span className="finding__tag">
                  {{ 低: "低风险", 中: "中风险", 高: "高风险" }[cur.severity] ?? cur.severity}
                  {" · "}
                  {cur.category}
                  {" · "}
                  第 {state.current_index != null ? state.current_index + 1 : "?"} 条
                </span>
              </div>
              <h3>{cur.title}</h3>
              <p><strong>事实：</strong>{cur.fact}</p>
              <p><strong>行业对标：</strong>{cur.benchmark}</p>
              <div className="choice-card">
                <h3>请选择落地路径（A / B / C）</h3>
                <div className="choice-row">
                  <span>A · 默认落地路径</span>
                  <span>B · 稳妥路径</span>
                  <span>C · 激进路径</span>
                </div>
              </div>
              <div className="finding-options">
                {cur.options.map((o) => (
                  <button
                    key={o.label}
                    type="button"
                    className={`finding-option finding-option--selectable${busy ? " is-disabled" : ""}`}
                    disabled={busy}
                    onClick={() => void onChoose(cur.id, o.label)}
                  >
                    <div className="finding-option__head">
                      <span className="finding-option__label">{o.label}</span>
                      <strong>{o.name}</strong>
                      <span className="finding-option__saving">净影响 {fmtMoney(o.est_saving)}</span>
                    </div>
                    <p>{o.description}</p>
                    <div className="finding-option__meta">
                      <span>可行性：{o.feasibility}</span>
                      <span>风险：{o.risk_level}</span>
                      <span>成本节约：{fmtMoney(o.cost_saving)}</span>
                      <span>税收节约：{fmtMoney(o.tax_saving)}</span>
                      <span>税负影响：{fmtMoney(o.tax_impact)}</span>
                    </div>
                    {o.action_note ? <p className="finding-option__action">操作：{o.action_note}</p> : null}
                  </button>
                ))}
              </div>
              <label className="field interaction-note-field">
                <span className="field__label">战略意图备注（可选）</span>
                <input
                  className="input"
                  value={strategyNote}
                  onChange={(e) => setStrategyNote(e.target.value)}
                  placeholder="如：本年度优先保证现金流，选择保守方案"
                />
              </label>
            </article>
          </>
        ) : null}

        {state?.state === "CONFIRMATION" || state?.state === "DRAFT2" || state?.state === "FINAL" ? (
          <>
            <div className={`ai-config__status${state?.is_export_unlocked ? " is-on" : ""}`}>
              {state?.is_export_unlocked ? "● 导出已解锁（FINAL）" : "○ 已生成第二稿，等待确认（落地性 " + state?.feasibility_score + "%）"}
            </div>
            <div className="result-meta diag-meta">
              <span>落地性评分：<strong>{state?.feasibility_score}%</strong></span>
              <span>决策：<strong>{state?.decisions.length}</strong> 条</span>
            </div>
            {state?.feasibility_breakdown && state.feasibility_breakdown.length > 0 ? (
              <ul className="warning-list">
                {state.feasibility_breakdown.map((b, i) => <li key={i}>⚠ {b}</li>)}
              </ul>
            ) : null}
            <h3 className="draft2-title">第二稿（每轮决策的落地细节）</h3>
            {state?.draft2.map((e) => (
              <article key={e.finding_id} className="draft2-entry">
                <div className="draft2-entry__head">
                  <strong>{e.finding_title}</strong>
                  <span className="est-tag">{e.option_label} · {e.option_name}</span>
                  <span className="draft2-entry__saving">净影响 {fmtMoney(e.est_saving)}</span>
                </div>
                <p><strong>趋势：</strong>{e.trend}</p>
                <p><strong>变动：</strong>当前 {e.current_value?.toLocaleString("zh-CN")} → 目标 {e.target_value?.toLocaleString("zh-CN")}（{e.change_pct}）</p>
                <p><strong>操作细节：</strong>{e.action_detail}</p>
                <p className="draft2-entry__cautions"><strong>注意事项：</strong>{e.cautions}</p>
                <div className="finding-option__meta">
                  <span>成本节约：{fmtMoney(e.cost_saving)}</span>
                  <span>税收节约：{fmtMoney(e.tax_saving)}</span>
                  <span>税负影响：{fmtMoney(e.tax_impact)}</span>
                  <span>风险：{e.risk_level}</span>
                </div>
              </article>
            ))}
            {state?.state !== "FINAL" ? (
              <div className="action-row">
                <button type="button" className="btn btn--primary" disabled={busy} onClick={() => void onConfirm()}>
                  确认第二稿，进入最终稿（解锁导出）
                </button>
              </div>
            ) : (
              <p className="panel__note">已确认，进入最终稿。可前往「第二稿与导出」页。</p>
            )}
          </>
        ) : null}
      </section>
    </>
  );
}

function ExportPage({ session, unlocked }: { session: SessionResponse; unlocked: boolean }) {
  const [busyKind, setBusyKind] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [statusNote, setStatusNote] = useState("");
  const [budgetProgress, setBudgetProgress] = useState("");
  // 流程：先 DeepSeek 编制建议 → 再导出三表并自动填入
  const [advice, setAdvice] = useState<BudgetAdviceResponse | null>(null);
  const [adviceItems, setAdviceItems] = useState<BudgetAdviceItem[]>([]);
  const [adviceBusy, setAdviceBusy] = useState(false);
  const [adviceNote, setAdviceNote] = useState("");
  const [requireConfirm, setRequireConfirm] = useState(false);
  const [qualityAck, setQualityAck] = useState(false);

  useEffect(() => {
    if (!session.session) return;
    void fetchExportStatus()
      .then((s) => {
        if (s.ready) {
          setStatusNote(
            `企业 ${s.company_name || "—"} · ${s.findings} 条发现 · ${s.decisions} 条决策`
            + (s.unlocked ? " · 已解锁" : ` · ${s.reason || "未解锁"}`),
          );
          const need =
            Boolean(s.require_confirm) ||
            s.data_quality?.confidence === "low" ||
            Boolean(session.data_quality?.require_confirm);
          setRequireConfirm(need);
          if (!need) setQualityAck(true);
        }
      })
      .catch(() => {
        /* ignore */
      });
  }, [session.session, session.data_quality, unlocked]);

  const loadAdvice = async () => {
    setAdviceBusy(true);
    setError("");
    setAdviceNote("DeepSeek 全量编制中（按销售/管理/财务等科目分批）…");
    try {
      const resp = await generateBudgetAdvice(true);
      setAdvice(resp);
      setAdviceItems(resp.suggestions.map((s) => ({ ...s, selected: s.selected !== false })));
      setAdviceNote(
        resp.ai_summary
          ? `建议已就绪，请勾选后点「导出费用预算三表」自动填入。${resp.ai_summary}`
          : `DeepSeek 已给出 ${resp.suggestion_count || resp.suggestions?.length || 0} 条建议，勾选后即可导出填入。`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setAdviceNote("需要已配置 DeepSeek。请到「设置」填写 API Key 后重试。");
    } finally {
      setAdviceBusy(false);
    }
  };

  const selectedCount = adviceItems.filter((i) => i.selected).length;
  const selectedSum = adviceItems
    .filter((i) => i.selected)
    .reduce((a, b) => a + (Number(b.budget_amount) || 0), 0);

  const onExport = (kind: "word" | "pdf" | "excel" | "budget") => {
    // 预算三表：导入后即可导出；其余需解锁
    if (kind !== "budget" && !unlocked) return;
    if (busyKind) return;
    if (requireConfirm && !qualityAck) {
      setError("数据置信度偏低或勾稽有警告：请勾选「已人工核验关键金额」后再导出。");
      return;
    }
    if (kind === "budget") {
      const picked = adviceItems.filter((i) => i.selected);
      if (!advice || picked.length === 0) {
        setError("请先完成「费用编制建议」并勾选要填入的费用项，再导出预算三表。");
        // 滚到建议区
        document.getElementById("budget-advice")?.scrollIntoView({ behavior: "smooth", block: "start" });
        return;
      }
    }
    setBusyKind(kind);
    setError("");
    setBudgetProgress("");
    void (async () => {
      try {
        if (kind === "budget") {
          const picked = adviceItems.filter((i) => i.selected);
          setBudgetProgress(`将把 ${picked.length} 条编制建议自动填入后导出…`);
          await downloadBudgetExportAsync((job) => {
            const pct = typeof job.progress === "number" ? `${job.progress}%` : "";
            const filled = job.meta?.filled_lines != null ? ` · 已填 ${job.meta.filled_lines}/84 行` : "";
            const adv =
              job.meta?.advice_applied
                ? ` · 建议已填入 ${job.meta.advice_selected ?? picked.length} 项`
                : "";
            setBudgetProgress(`${job.stage || ""} ${pct} ${job.message || ""}${filled}${adv}`.trim());
          }, picked);
          setAdviceNote(
            `已导出费用预算三表：DeepSeek 建议 ${picked.length} 项已自动写入参考金额/预算/占比。`,
          );
        } else {
          await downloadExport(kind);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusyKind(null);
      }
    })();
  };

  const kindOf = (id: string): "word" | "pdf" | "excel" | "budget" | null => {
    if (id === "w") return "word";
    if (id === "p") return "pdf";
    if (id === "e") return "excel";
    if (id === "b") return "budget";
    return null;
  };

  const toggleItem = (row: number) => {
    setAdviceItems((list) =>
      list.map((it) => (it.row === row ? { ...it, selected: !it.selected } : it)),
    );
  };

  const selectAll = (on: boolean) => {
    setAdviceItems((list) => list.map((it) => ({ ...it, selected: on })));
  };

  /** 一键：用当前勾选直接导出三表（等同点导出卡片） */
  const onExportWithAdvice = () => {
    onExport("budget");
  };

  const fmtYuan = (n: number) =>
    Number(n || 0).toLocaleString("zh-CN", { maximumFractionDigits: 0 });

  const budgetReady = Boolean(advice && selectedCount > 0);

  return (
    <section className="panel">
      <h2 className="panel__title">第二稿与导出</h2>
      <p className="panel__note">
        Word 采用「艺康体」小白版结构：先说结论 → 最以前/中间/现在 → 关键指标白话 → 将来建议 → 月度看板 → 落地清单。
      </p>
      {!session.session ? (
        <p className="panel__note">尚未导入财报。请先完成导入、诊断与互动，再导出。</p>
      ) : (
        <>
          <QualityPolicyBanner quality={session.data_quality} policy={session.policy} />
          {requireConfirm ? (
            <label className="field" style={{ display: "flex", gap: 8, alignItems: "flex-start", marginBottom: 12 }}>
              <input
                type="checkbox"
                checked={qualityAck}
                onChange={(e) => setQualityAck(e.target.checked)}
              />
              <span className="field__label">
                已人工核验关键金额（营收/成本/净利/所得税）；数据置信度偏低或勾稽有警告时必须勾选后才能导出
              </span>
            </label>
          ) : null}
          {unlocked ? (
            <div className="ai-config__status is-on">● 报告/测算导出已解锁（互动已确认）</div>
          ) : (
            <div className="status status--warn">
              导出尚未解锁：请到「互动」页完成所有发现的决策并确认（或落地性 ≥95%），再回到本页导出。
            </div>
          )}
          {statusNote ? <p className="panel__note">{statusNote}</p> : null}
          {budgetProgress ? (
            <div className="status status--info" role="status">
              费用预算三表：{budgetProgress}
            </div>
          ) : null}
          {error ? <div className="status status--error" role="alert">{error}</div> : null}

          {/* ① 费用编制建议 — 必须在导出预算三表之前 */}
          <div className="advice-panel" id="budget-advice">
            <h3 className="advice-panel__title">① 费用编制建议 · DeepSeek 全量（先做这一步）</h3>
            <p className="panel__note">
              <strong>分工：</strong>DeepSeek 在本页完成「开支预算 / 未来预期 / 参考与预算金额」分析填入
              （无上年不编造上年）；导出时<strong>不再二次调用 DeepSeek</strong>。
              毛利率、费用占比等由 <strong>WB 建模</strong>计算。
              <strong>编制时强制执行：</strong>
              ①历史费用占营收对标；②金税四期合规下评估收入上涨后费用可筹划范围；
              ③费用增幅匹配营收增速、落在行业区间，杜绝涉税风险（金额有硬顶）。
            </p>
            <div className="ai-actions">
              <button
                type="button"
                className="btn btn--ai"
                disabled={adviceBusy || busyKind !== null || !session.session}
                onClick={() => void loadAdvice()}
              >
                {adviceBusy ? "DeepSeek 编制中…" : advice ? "重新生成编制建议" : "生成费用编制建议"}
              </button>
              {adviceItems.length > 0 ? (
                <>
                  <button type="button" className="btn btn--ghost" disabled={adviceBusy} onClick={() => selectAll(true)}>
                    全选
                  </button>
                  <button type="button" className="btn btn--ghost" disabled={adviceBusy} onClick={() => selectAll(false)}>
                    全不选
                  </button>
                  <button
                    type="button"
                    className="btn btn--primary"
                    disabled={adviceBusy || busyKind !== null || selectedCount === 0}
                    onClick={onExportWithAdvice}
                  >
                    {busyKind === "budget"
                      ? "导出填入中…"
                      : `② 导出费用预算三表（自动填入 ${selectedCount} 项）`}
                  </button>
                </>
              ) : null}
            </div>
            {adviceNote ? <p className="panel__note">{adviceNote}</p> : null}
            {advice ? (
              <>
                <div className="diag-summary" style={{ marginBottom: 12 }}>
                  <div className="diag-metric">
                    <span className="diag-metric__label">费用上限 E7</span>
                    <strong className="diag-metric__value">{fmtYuan(advice.expense_budget_cap)}</strong>
                  </div>
                  <div className="diag-metric">
                    <span className="diag-metric__label">已分配</span>
                    <strong className="diag-metric__value">{fmtYuan(advice.allocated_before)}</strong>
                  </div>
                  <div className="diag-metric">
                    <span className="diag-metric__label">可补 residual</span>
                    <strong className="diag-metric__value">{fmtYuan(advice.residual)}</strong>
                  </div>
                  <div className="diag-metric">
                    <span className="diag-metric__label">空白行</span>
                    <strong className="diag-metric__value">{advice.zero_lines}</strong>
                  </div>
                  <div className="diag-metric">
                    <span className="diag-metric__label">勾选预算合计</span>
                    <strong className="diag-metric__value">{fmtYuan(selectedSum)}</strong>
                  </div>
                </div>
                {advice.ai_summary ? (
                  <div className="status status--info" role="status">
                    DeepSeek：{advice.ai_summary}
                  </div>
                ) : null}
                <div className="advice-list">
                  {adviceItems.map((it) => (
                    <label key={it.row} className={`advice-item advice-item--${it.priority}`}>
                      <input
                        type="checkbox"
                        checked={it.selected}
                        onChange={() => toggleItem(it.row)}
                      />
                      <div className="advice-item__body">
                        <div className="advice-item__head">
                          <strong>
                            R{it.row} · {it.subject} / {it.expense_name}
                          </strong>
                          <span className="advice-item__tag">{it.invoice_name}</span>
                          <span className="advice-item__prio">{it.priority}</span>
                          {!it.has_last_year ? (
                            <span className="advice-item__tag advice-item__tag--warn">无上年·只写参考/预算</span>
                          ) : (
                            <span className="advice-item__tag">有上年</span>
                          )}
                        </div>
                        <div className="advice-item__nums">
                          <span>参考 F {fmtYuan(it.reference_amount)} 元</span>
                          <span>预算 G {fmtYuan(it.budget_amount)} 元</span>
                          <span>占比 H {formatBudgetRatioPct(Number(it.budget_ratio_pct || 0))}</span>
                        </div>
                        <p className="advice-item__reason">{it.reason}</p>
                      </div>
                    </label>
                  ))}
                </div>
                {advice.algorithm_notes?.length ? (
                  <details className="advice-notes">
                    <summary>算法说明</summary>
                    <ul>
                      {advice.algorithm_notes.map((n, i) => (
                        <li key={i}>{n}</li>
                      ))}
                    </ul>
                  </details>
                ) : null}
              </>
            ) : (
              <p className="panel__note">
                请先点「生成费用编制建议」。完成勾选后，再导出费用预算三表，内容会自动填入。
              </p>
            )}
          </div>

          {/* ② 其他交付物 + 预算三表卡片 */}
          <h3 className="panel__title" style={{ marginTop: 20, fontSize: 15 }}>
            ② 导出交付物
          </h3>
          <p className="panel__note">
            费用预算三表：须先完成上方编制建议；导出只写入金额并套用公式模型（占比/毛利表内算）。
            {budgetReady
              ? ` 当前已勾选 ${selectedCount} 项，可直接导出。`
              : " 当前尚未勾选建议项。"}
          </p>
          <div className="export-grid">
            {EXPORT_ITEMS.map((item) => {
              const kind = kindOf(item.id);
              const busy = kind !== null && busyKind === kind;
              const enabled =
                kind === "budget" ? budgetReady && !adviceBusy : unlocked;
              return (
                <button
                  key={item.id}
                  type="button"
                  className={enabled ? "export-card export-card--enabled" : "export-card"}
                  disabled={busy || !kind || !enabled || adviceBusy}
                  onClick={() => kind && onExport(kind)}
                >
                  <span className="export-card__format">{item.format}</span>
                  <strong>
                    {busy
                      ? kind === "budget"
                        ? "填入建议并导出中…"
                        : "生成中…"
                      : item.name}
                  </strong>
                  <span>
                    {kind === "word"
                      ? "经营业绩分析与建议（小白版）"
                      : kind === "pdf"
                        ? "同结构 PDF 交付稿"
                        : kind === "excel"
                          ? "成本优化测算模型（可逐月跟踪）"
                          : kind === "budget"
                            ? budgetReady
                              ? `自动填入已勾选 ${selectedCount} 条 DeepSeek 建议`
                              : "请先完成①费用编制建议并勾选"
                            : item.note ?? ""}
                  </span>
                </button>
              );
            })}
          </div>
        </>
      )}
    </section>
  );
}

function SettingsPage({
  aiConfigured,
  aiConfig,
  aiBusy,
  aiError,
  onSaveAIConfig,
  onClearAIConfig,
}: {
  aiConfigured: boolean;
  aiConfig: { base_url: string; model: string; api_key: string };
  aiBusy: boolean;
  aiError: string;
  onSaveAIConfig: (cfg: { base_url: string; model: string; api_key: string }) => void;
  onClearAIConfig: () => void;
}) {
  return (
    <section className="panel">
      <h2 className="panel__title">设置</h2>
      <div className="mini-panel">
        <h3>AI 可选增强（全局配置，一次配置处处可用）</h3>
        <p className="mini-panel__row">
          默认离线：未配置大模型时由规则引擎兜底。配置后用于扫描件 PDF 导入解析、AI 整理、跨年合并报告、模板工作台指标识别。
        </p>
        <p className="mini-panel__row">配置持久化到本机 .ai_config.json，重启免重输。</p>
      </div>
      <AiConfigPanel
        configured={aiConfigured}
        config={aiConfig}
        busy={aiBusy}
        error={aiError}
        onSave={onSaveAIConfig}
        onClear={onClearAIConfig}
      />
    </section>
  );
}

// ── 应用外壳 ──────────────────────────────────────────────────────────

export function App() {
  const [current, setCurrent] = useState<Workspace>("overview");
  const health = useBackendHealth();
  const [session, setSession] = useState<SessionResponse>({ session: null, indicators: [], years: [] });
  const [sessionLoaded, setSessionLoaded] = useState(false);
  const [industries, setIndustries] = useState<IndustryItem[]>([]);
  // 全局 AI 配置状态：设置页与导入页共用，配一次处处可用
  const [aiConfigured, setAiConfigured] = useState(false);
  const [aiConfig, setAiConfig] = useState({ base_url: "", model: "deepseek-v4-flash", api_key: "" });
  const [aiCfgBusy, setAiCfgBusy] = useState(false);
  const [aiCfgError, setAiCfgError] = useState("");
  // AI 合并报告全局 state：切换工作区后仍保留，不重新请求
  const [aiReport, setAiReport] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  const [aiError, setAiError] = useState("");
  const [aiJob, setAiJob] = useState<AIReportJob | null>(null);
  const [sessionClearing, setSessionClearing] = useState(false);
  const reportPollTimer = useRef<number | null>(null);
  const reportRequestGeneration = useRef(0);
  const initialRecoveryStarted = useRef(false);
  const sessionClearingRef = useRef(false);
  const reportAdmissionRef = useRef(false);
  // 「重新导入」后强制重挂载导入页，清空已选文件/表单等内部 state
  const [importResetKey, setImportResetKey] = useState(0);
  // 全局诊断与互动状态：驱动步骤条完成态与导航前置校验
  const [diagnosisDone, setDiagnosisDone] = useState(false);
  const [interactionDone, setInteractionDone] = useState(false);
  const [exportUnlocked, setExportUnlocked] = useState(false);

  useEffect(() => {
    fetchSession()
      .then((body) => {
        setSession(body);
        setSessionLoaded(true);
      })
      .catch(() => {
        setSession({ session: null, indicators: [], years: [] });
        setSessionLoaded(true);
      });
    fetchIndustries()
      .then((r) => setIndustries(r.industries))
      .catch(() => undefined);
    fetchAIConfig()
      .then((cfg) => {
        setAiConfigured(cfg.configured);
        setAiConfig((c) => ({ ...c, base_url: cfg.base_url, model: cfg.model || "deepseek-v4-flash" }));
      })
      .catch(() => undefined);
    // 启动时恢复诊断/互动完成态（后端持久化，刷新不丢）
    fetchDiagnosis()
      .then((r) => setDiagnosisDone(Boolean(r.diagnosis && r.diagnosis.findings.length > 0)))
      .catch(() => undefined);
    fetchInteractionState()
      .then((st) => {
        setInteractionDone(st.state === "DRAFT2" || st.state === "CONFIRMATION" || st.state === "FINAL");
        setExportUnlocked(st.is_export_unlocked);
      })
      .catch(() => undefined);
  }, []);

  const handleSaveAIConfig = async (cfg: { base_url: string; model: string; api_key: string }) => {
    setAiCfgBusy(true);
    setAiCfgError("");
    try {
      const saved = await saveAIConfig(cfg);
      setAiConfigured(saved.configured);
      setAiConfig({ base_url: saved.base_url, model: saved.model, api_key: cfg.api_key });
      setAiCfgError(saved.configured ? "" : saved.error || "配置未完成，请检查 Base URL / 模型 / API Key");
    } catch (e) {
      setAiCfgError(e instanceof Error ? e.message : String(e));
    } finally {
      setAiCfgBusy(false);
    }
  };

  const handleClearAIConfig = async () => {
    setAiCfgBusy(true);
    setAiCfgError("");
    try {
      const cleared = await clearAIConfig();
      setAiConfigured(cleared.configured);
      setAiConfig({ base_url: "", model: "deepseek-v4-flash", api_key: "" });
    } catch (e) {
      setAiCfgError(e instanceof Error ? e.message : String(e));
    } finally {
      setAiCfgBusy(false);
    }
  };

  const invalidateReportRequest = useCallback(() => {
    reportRequestGeneration.current += 1;
    if (reportPollTimer.current !== null) {
      window.clearTimeout(reportPollTimer.current);
      reportPollTimer.current = null;
    }
    return reportRequestGeneration.current;
  }, []);

  const followReportJob = useCallback((jobId: string, generation: number, initial?: AIReportJob) => {
    const failPolling = (error: unknown) => {
      if (generation !== reportRequestGeneration.current) return;
      reportPollTimer.current = null;
      setAiBusy(false);
      reportAdmissionRef.current = false;
      setAiReport("");
      setAiJob((currentJob) =>
        currentJob?.job_id === jobId
          ? { ...currentJob, status: "failed" }
          : {
              job_id: jobId,
              session_version: "",
              status: "failed",
              stage: "",
              progress: { current: 0, total: 0 },
              message: "",
            },
      );
      setAiError(
        error instanceof TypeError
          ? "任务状态读取失败，请重新生成"
          : error instanceof Error
            ? error.message
            : String(error),
      );
    };
    const failFormalReport = () => {
      if (generation !== reportRequestGeneration.current) return;
      setAiBusy(false);
      reportAdmissionRef.current = false;
      setAiReport("");
      setAiJob((currentJob) =>
        currentJob?.job_id === jobId ? { ...currentJob, status: "failed" } : currentJob,
      );
      setAiError("任务已完成，但正式报告不可用，请重新生成");
    };
    const consume = async (job: AIReportJob) => {
      if (generation !== reportRequestGeneration.current) return;
      setAiJob(job);
      if (job.status === "failed") {
        reportPollTimer.current = null;
        reportAdmissionRef.current = false;
        setAiBusy(false);
        setAiReport("");
        setAiError(job.error || "报告任务失败，请重新生成");
        return;
      }
      if (job.status === "completed") {
        reportPollTimer.current = null;
        setAiBusy(false);
        if (!job.report_id) {
          failFormalReport();
          return;
        }
        try {
          const report = await fetchReport(job.report_id);
          if (generation !== reportRequestGeneration.current) return;
          setAiReport(report.content);
          setAiError("");
          reportAdmissionRef.current = false;
        } catch (error) {
          failFormalReport();
        }
        return;
      }
      setAiBusy(true);
      setAiError("");
      reportPollTimer.current = window.setTimeout(async () => {
        reportPollTimer.current = null;
        try {
          const next = await fetchYearsSummaryJob(jobId);
          await consume(next);
        } catch (error) {
          failPolling(error);
        }
      }, 1_500);
    };
    if (initial) void consume(initial);
    else {
      void fetchYearsSummaryJob(jobId)
        .then(consume)
        .catch(failPolling);
    }
  }, []);

  const handleGenerateReport = useCallback(async () => {
    if (sessionClearingRef.current || reportAdmissionRef.current) return;
    reportAdmissionRef.current = true;
    const generation = invalidateReportRequest();
    setAiBusy(true);
    setAiError("");
    setAiReport("");
    setAiJob(null);
    try {
      const started = await startYearsSummaryJob();
      if (generation !== reportRequestGeneration.current) return;
      followReportJob(started.job_id, generation);
    } catch (e) {
      if (generation !== reportRequestGeneration.current) return;
      reportAdmissionRef.current = false;
      setAiJob({
        job_id: "start-failed",
        session_version: "",
        status: "failed",
        stage: "",
        progress: { current: 0, total: 0 },
        message: "",
      });
      setAiError("报告任务启动失败，请重新生成");
      setAiBusy(false);
    }
  }, [followReportJob, invalidateReportRequest]);

  const handleImported = (
    resp: ImportResponse,
    opts?: { autoReport?: boolean; restored?: ImportResponse["restored"] },
  ) => {
    reportAdmissionRef.current = false;
    invalidateReportRequest();
    setSession({
      session: resp.summary,
      indicators: resp.indicators,
      years: resp.years,
      data_quality: resp.data_quality,
      policy: resp.policy,
    });
    setAiReport("");
    setAiError("");
    setAiJob(null);
    // 重新导入后：诊断/互动完成态一律重置（后端已清空旧结果）
    setDiagnosisDone(false);
    setInteractionDone(false);
    setExportUnlocked(false);
    if (opts?.restored) {
      // 历史载入：按该案例保存的进度恢复全局状态，各页面随之联动
      setDiagnosisDone(Boolean(opts.restored.diagnosis_done));
      setInteractionDone(Boolean(opts.restored.interaction_done));
      setExportUnlocked(Boolean(opts.restored.export_unlocked));
      if (opts.restored.report_id) {
        // 已生成过 AI 合并报告：直接展示，不重新整理
        void fetchReport(opts.restored.report_id)
          .then((report) => {
            setAiReport(report.content);
            setAiError("");
          })
          .catch(() => undefined); // 报告已被删除等场景静默跳过
      }
      return;
    }
    // 历史卡片载入不自动重算 AI 报告（该版本报告可能已存在于报告记录）
    if (opts?.autoReport !== false) void handleGenerateReport();
  };

  // 刷新时只恢复当前 active 任务；没有 active 时等待用户明确生成。
  useEffect(() => {
    if (!sessionLoaded || initialRecoveryStarted.current) return;
    initialRecoveryStarted.current = true;
    if (!session.session) return;
    const generation = invalidateReportRequest();
    fetchActiveYearsSummaryJob()
      .then((active) => {
        if (generation !== reportRequestGeneration.current || !active) return;
        reportAdmissionRef.current = true;
        followReportJob(active.job_id, generation, active);
      })
      .catch(() => undefined);
  }, [followReportJob, invalidateReportRequest, session.session, sessionLoaded]);

  useEffect(() => () => {
    reportAdmissionRef.current = false;
    invalidateReportRequest();
  }, [invalidateReportRequest]);

  const handleClear = async () => {
    sessionClearingRef.current = true;
    reportAdmissionRef.current = false;
    setSessionClearing(true);
    invalidateReportRequest();
    try {
      await clearSession();
    } catch {
      /* 清空失败仍重置前端 */
    }
    // 清空请求等待期间可能仍有旧网络响应；完成/失败后再次推进 epoch。
    invalidateReportRequest();
    setSession({ session: null, indicators: [], years: [], data_quality: {}, policy: {} });
    setAiReport("");
    setAiError("");
    setAiJob(null);
    setAiBusy(false);
    setDiagnosisDone(false);
    setInteractionDone(false);
    setExportUnlocked(false);
    sessionClearingRef.current = false;
    reportAdmissionRef.current = false;
    setSessionClearing(false);
    // 强制重挂载导入页，清空已选文件/表单，避免旧状态残留
    setImportResetKey((k) => k + 1);
  };

  // 稳定的流程状态回调（避免子页面 useCallback 依赖变化导致无限循环）
  const handleDiagnosisDone = useCallback((done: boolean, meta?: { resetDownstream?: boolean }) => {
    setDiagnosisDone(done);
    // 重新诊断后旧互动/导出状态作废
    if (meta?.resetDownstream) {
      setInteractionDone(false);
      setExportUnlocked(false);
    }
  }, []);

  const handleInteractionChange = useCallback((state: InteractionState) => {
    setInteractionDone(state.state === "DRAFT2" || state.state === "CONFIRMATION" || state.state === "FINAL");
    setExportUnlocked(state.is_export_unlocked);
  }, []);

  const renderPage = () => {
    switch (current) {
      case "overview":
        return (
          <ImportWorkspacePage
            key={importResetKey}
            session={session}
            industries={industries}
            onImported={handleImported}
            onClear={() => void handleClear()}
            aiConfigured={aiConfigured}
            aiConfig={aiConfig}
            configBusy={aiCfgBusy}
            configError={aiCfgError}
            reportBusy={aiBusy}
            reportError={aiError}
            reportJob={aiJob}
            reportContent={aiReport}
            reportBlocked={sessionClearing}
            onGenerateReport={() => void handleGenerateReport()}
            onSaveAIConfig={(cfg) => void handleSaveAIConfig(cfg)}
            onClearAIConfig={() => void handleClearAIConfig()}
          />
        );
      case "diagnosis":
        return (
          <DiagnosisPage
            session={session}
            onDiagnosisDone={handleDiagnosisDone}
          />
        );
      case "interaction":
        return (
          <InteractionPage
            session={session}
            onInteractionChange={handleInteractionChange}
          />
        );
      case "export":
        return <ExportPage session={session} unlocked={exportUnlocked} />;
      case "settings":
        return <SettingsPage aiConfigured={aiConfigured} aiConfig={aiConfig} aiBusy={aiCfgBusy} aiError={aiCfgError} onSaveAIConfig={(cfg) => void handleSaveAIConfig(cfg)} onClearAIConfig={() => void handleClearAIConfig()} />;
    }
  };

  return (
    <div className="shell">
      <Sidebar current={current} onNavigate={setCurrent} />
      <div className="main">
        <Topbar session={session} health={health} current={current} />
        <main className="content">
          <WorkflowRail
            current={current}
            session={session}
            diagnosisDone={diagnosisDone}
            interactionDone={interactionDone}
            exportUnlocked={exportUnlocked}
          />
          {renderPage()}
          <StepNav
            current={current}
            session={session}
            diagnosisDone={diagnosisDone}
            interactionDone={interactionDone}
            exportUnlocked={exportUnlocked}
            onNavigate={setCurrent}
          />
        </main>
      </div>
    </div>
  );
}
