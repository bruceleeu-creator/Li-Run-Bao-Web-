"""利润宝 · 完整财报 AI 流水线协调器。

本模块只消费冻结输入和显式依赖，不读取 Web 当前会话，也不管理线程或任务终态。
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from core import parser
from core.ai_engine import (
    AICompletionError,
    AIEngineError,
    AIRequestError,
)


@dataclass(frozen=True)
class SourceFileSnapshot:
    path: str
    name: str
    sha256: str
    size: int
    report_year: int
    page_count: int


@dataclass(frozen=True)
class JobInputSnapshot:
    session_version: str
    company_name: str
    industry: str
    years: tuple[int, ...]
    financial_data_json: str
    sources: tuple[SourceFileSnapshot, ...]


@dataclass(frozen=True)
class PipelineResult:
    markdown: str
    report_type: Literal["ai_full", "rules_quick"]
    model: str
    attempted_model: str
    fallback: bool
    fallback_reason_code: str
    page_coverage: dict[str, tuple[int, int]]
    blank_pages: dict[str, tuple[int, ...]]
    conflict_count: int


@dataclass(frozen=True)
class ProgressUpdate:
    stage: str
    current: int
    total: int
    message: str


class PipelineError(Exception):
    """可安全持久化的流水线错误。"""

    def __init__(
        self,
        code: str,
        public_message: str,
        *,
        stage: str,
        source_file: str | None = None,
        page_range: tuple[int, int] | None = None,
        retryable: bool = False,
    ):
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message
        self.stage = stage
        self.source_file = source_file
        self.page_range = page_range
        self.retryable = retryable


@dataclass(frozen=True)
class PipelineDependencies:
    reader: Any
    ai: Any
    db: Any
    engine_factory: Callable[[], Any | None]
    workspace_root: Path

    @classmethod
    def default(
        cls,
        *,
        engine_factory: Callable[[], Any | None] | None = None,
        workspace_root: Path | None = None,
    ) -> "PipelineDependencies":
        reader = importlib.import_module(
            "core.CO_full_pdf_reader_WB-CO-TR-20260807113737"
        )
        ai = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")
        db = importlib.import_module("web_backend.CO_db_WB-CO-TR-20260805160732")
        if engine_factory is None:
            engine_factory = lambda: ai._engine(timeout=180.0)
        if workspace_root is None:
            configured = os.environ.get("LIRUNBAO_WORKSPACE_PATH", "").strip()
            workspace_root = (
                Path(configured)
                if configured
                else Path(__file__).resolve().parent / "workspaces"
            )
        return cls(reader, ai, db, engine_factory, Path(workspace_root))


@dataclass(frozen=True)
class LoadedSources:
    pages: dict[str, tuple[Any, ...]]
    page_coverage: dict[str, tuple[int, int]]
    blank_pages: dict[str, tuple[int, ...]]


_REQUEST_FALLBACK_CODES = frozenset(
    {"AI_TIMEOUT", "AI_CONNECTION_FAILED", "AI_HTTP_ERROR", "AI_SERVICE_UNAVAILABLE"}
)


def is_request_fallback_allowed(error: AIRequestError) -> bool:
    return bool(error.retryable and error.code in _REQUEST_FALLBACK_CODES)


def _safe_source_name(name: str) -> str:
    candidate = Path(name).name
    return candidate if candidate and candidate == name else "财报文件"


def _source_error(
    code: str,
    source: SourceFileSnapshot,
    message: str,
    *,
    page_range: tuple[int, int] | None = None,
) -> PipelineError:
    return PipelineError(
        code,
        message,
        stage="read",
        source_file=_safe_source_name(source.name),
        page_range=page_range,
    )


def _validated_source_path(
    source: SourceFileSnapshot, deps: PipelineDependencies
) -> Path:
    raw = Path(source.path)
    name = _safe_source_name(source.name)
    if not raw.is_absolute() or raw.is_symlink() or raw.suffix.lower() != ".pdf":
        raise _source_error("SOURCE_FILE_INVALID", source, f"来源文件 {name} 无效")
    resolved = raw.resolve()
    try:
        resolved.relative_to(deps.workspace_root.resolve())
    except ValueError as exc:
        raise _source_error(
            "SOURCE_FILE_OUTSIDE_WORKSPACE", source, f"来源文件 {name} 不在受管工作区"
        ) from exc
    if not resolved.is_file():
        raise _source_error("SOURCE_FILE_MISSING", source, f"来源文件 {name} 已不存在")
    if resolved.stat().st_size != source.size:
        raise _source_error("SOURCE_FILE_CHANGED", source, f"来源文件 {name} 已变化")
    if deps.reader.file_sha256(resolved) != source.sha256:
        raise _source_error("SOURCE_FILE_CHANGED", source, f"来源文件 {name} 已变化")
    return resolved


def _records_from_cache(rows: list[dict], reader: Any) -> tuple[Any, ...]:
    return tuple(
        reader.PDFPageRecord(
            int(row["page_no"]),
            int(row["total_pages"]),
            str(row["method"]),
            str(row["text"]),
            str(row["status"]),
        )
        for row in rows
    )


def _page_dict(page: Any) -> dict:
    return {
        "page_no": int(page.page_no),
        "total_pages": int(page.total_pages),
        "method": str(page.method),
        "status": str(page.status),
        "text": str(page.text),
    }


def _page_state_valid(page: Any) -> bool:
    """Accept only truthful terminal page-state combinations."""
    status = str(page.get("status") if isinstance(page, dict) else page.status)
    method = str(page.get("method") if isinstance(page, dict) else page.method)
    text = str(page.get("text") if isinstance(page, dict) else page.text)
    if status == "ok":
        return method in {"text", "ocr"} and bool(text.strip())
    if status == "blank":
        return method == "none" and text == ""
    if status == "failed":
        return method in {"text", "ocr", "none"}
    return False


def load_or_extract_pages(
    source: SourceFileSnapshot,
    on_page: Callable[[int, int, bool], None],
    deps: PipelineDependencies,
) -> tuple[Any, ...]:
    """验证来源后复用完整缓存或逐页提取；failed 页永不成为成功缓存。"""
    path = _validated_source_path(source, deps)
    cached = deps.db.load_cached_pages(source.sha256)
    cache_valid = bool(cached) and all(
        _page_state_valid(row)
        and row.get("status") != "failed"
        and int(row.get("total_pages") or 0) == source.page_count
        for row in cached
    )
    if cache_valid:
        pages = _records_from_cache(cached, deps.reader)
        for page in pages:
            on_page(page.page_no, source.page_count, True)
    else:
        progress_count = 0

        def checked_progress(local_current: int, local_total: int) -> None:
            nonlocal progress_count
            progress_count += 1
            if (
                int(local_total) != source.page_count
                or int(local_current) != progress_count
                or progress_count > source.page_count
            ):
                raise _source_error(
                    "PROGRESS_INVALID",
                    source,
                    f"来源文件 {_safe_source_name(source.name)} 读取进度无效",
                )
            on_page(progress_count, source.page_count, False)

        pages = tuple(
            deps.reader.extract_all_pages(
                path,
                on_progress=checked_progress,
            )
        )
        if progress_count != source.page_count:
            raise _source_error(
                "PROGRESS_INVALID",
                source,
                f"来源文件 {_safe_source_name(source.name)} 读取进度不完整",
            )
        if (
            len(pages) != source.page_count
            or any(page.total_pages != source.page_count for page in pages)
            or [page.page_no for page in pages]
            != list(range(1, source.page_count + 1))
        ):
            raise _source_error(
                "PAGE_COUNT_CHANGED", source, f"来源文件 {_safe_source_name(source.name)} 页数已变化"
            )
        failed = next((page for page in pages if page.status == "failed"), None)
        if failed is not None:
            raise _source_error(
                "PAGE_EXTRACTION_FAILED",
                source,
                f"来源文件 {_safe_source_name(source.name)} 第 {failed.page_no} 页读取失败",
                page_range=(failed.page_no, failed.page_no),
            )
        invalid = next((page for page in pages if not _page_state_valid(page)), None)
        if invalid is not None:
            raise _source_error(
                "PAGE_STATE_INVALID",
                source,
                f"来源文件 {_safe_source_name(source.name)} 第 {invalid.page_no} 页状态无效",
                page_range=(invalid.page_no, invalid.page_no),
            )
        deps.db.save_cached_pages(source.sha256, [_page_dict(page) for page in pages])
    failed = next((page for page in pages if page.status == "failed"), None)
    if failed is not None:
        raise _source_error(
            "PAGE_EXTRACTION_FAILED",
            source,
            f"来源文件 {_safe_source_name(source.name)} 第 {failed.page_no} 页读取失败",
            page_range=(failed.page_no, failed.page_no),
        )
    return pages


def load_sources_pages(
    snapshot: JobInputSnapshot,
    update: Callable[[ProgressUpdate], None],
    deps: PipelineDependencies,
) -> LoadedSources:
    """按稳定来源顺序读取，并把所有文件进度映射为单调全局序列。"""
    sources = sorted(snapshot.sources, key=lambda item: (item.report_year, item.name, item.sha256))
    total = sum(source.page_count for source in sources)
    if total <= 0:
        raise PipelineError("SOURCE_FILES_UNAVAILABLE", "没有可读取的财报来源", stage="read")
    current = 0
    pages_by_name: dict[str, tuple[Any, ...]] = {}
    coverage: dict[str, tuple[int, int]] = {}
    blanks: dict[str, tuple[int, ...]] = {}

    for source in sources:
        def on_page(local_current: int, local_total: int, cached: bool) -> None:
            nonlocal current
            current += 1
            update(
                ProgressUpdate(
                    "read",
                    current,
                    total,
                    f"{_safe_source_name(source.name)} 第 {local_current}/{source.page_count} 页",
                )
            )

        pages = load_or_extract_pages(source, on_page, deps)
        pages_by_name[source.name] = pages
        coverage[source.name] = (len(pages), source.page_count)
        blanks[source.name] = tuple(page.page_no for page in pages if page.status == "blank")
    return LoadedSources(pages_by_name, coverage, blanks)


def _chunk_for_pages(pages: tuple[Any, ...], reader_module: Any):
    return reader_module.PDFTextChunk(
        pages[0].page_no,
        pages[-1].page_no,
        pages[0].total_pages,
        "\n\n".join(reader_module._format_page(page) for page in pages),
    )


def extract_pages_with_split_retry(
    engine: Any,
    source: dict,
    pages: tuple[Any, ...],
    depth: int = 0,
    *,
    ai_module: Any | None = None,
    reader_module: Any | None = None,
) -> list[dict]:
    """length 时只沿页边界拆分，每条祖先链最多三轮。"""
    if ai_module is None:
        ai_module = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")
    if reader_module is None:
        reader_module = importlib.import_module(
            "core.CO_full_pdf_reader_WB-CO-TR-20260807113737"
        )
    if not pages:
        raise PipelineError("EMPTY_CHUNK", "财报页段为空", stage="extract")
    chunk = _chunk_for_pages(pages, reader_module)
    try:
        return [ai_module.extract_chunk_facts(engine, source, chunk)]
    except AICompletionError as exc:
        if exc.finish_reason != "length":
            raise PipelineError(
                "AI_COMPLETION_INCOMPLETE",
                "AI 分段提取未完整结束",
                stage="extract",
                source_file=_safe_source_name(str(source.get("name") or "")),
                page_range=(chunk.start_page, chunk.end_page),
            ) from exc
        if len(pages) == 1:
            raise PipelineError(
                "CHUNK_TRUNCATED_UNSPLITTABLE",
                f"{_safe_source_name(str(source.get('name') or ''))} 第 {chunk.start_page} 页输出被截断",
                stage="extract",
                source_file=_safe_source_name(str(source.get("name") or "")),
                page_range=(chunk.start_page, chunk.end_page),
            ) from exc
        if depth >= 3:
            raise PipelineError(
                "CHUNK_TRUNCATION_EXHAUSTED",
                f"{_safe_source_name(str(source.get('name') or ''))} 第 {chunk.start_page}-{chunk.end_page} 页多次截断",
                stage="extract",
                source_file=_safe_source_name(str(source.get("name") or "")),
                page_range=(chunk.start_page, chunk.end_page),
            ) from exc
        midpoint = len(pages) // 2
        left = extract_pages_with_split_retry(
            engine, source, pages[:midpoint], depth + 1,
            ai_module=ai_module, reader_module=reader_module,
        )
        right = extract_pages_with_split_retry(
            engine, source, pages[midpoint:], depth + 1,
            ai_module=ai_module, reader_module=reader_module,
        )
        return left + right


def generate_final_with_retry(engine: Any, payload: dict, ai_module: Any):
    """最终生成仅对结构化 length 完整重跑一次（16,384 → 32,768）。"""
    try:
        return ai_module.generate_final_report_result(engine, payload, 16_384)
    except AICompletionError as exc:
        if exc.finish_reason != "length":
            raise PipelineError(
                "FINAL_COMPLETION_INCOMPLETE", "AI 最终报告未完整结束", stage="generate"
            ) from exc
    try:
        return ai_module.generate_final_report_result(engine, payload, 32_768)
    except AICompletionError as exc:
        if exc.finish_reason == "length":
            raise PipelineError(
                "FINAL_REPORT_TRUNCATED", "AI 最终报告再次截断，未保存", stage="generate"
            ) from exc
        raise PipelineError(
            "FINAL_COMPLETION_INCOMPLETE", "AI 最终报告未完整结束", stage="generate"
        ) from exc


def _expected(snapshot: JobInputSnapshot, coverage: dict[str, tuple[int, int]]) -> dict:
    return {
        "company_name": snapshot.company_name,
        "years": list(snapshot.years),
        "page_coverage": {name: list(values) for name, values in coverage.items()},
    }


def _rules_result(
    snapshot: JobInputSnapshot,
    data: Any,
    reason_code: str,
    ai_module: Any,
    *,
    attempted_model: str = "",
    coverage: dict[str, tuple[int, int]] | None = None,
    blank_pages: dict[str, tuple[int, ...]] | None = None,
) -> PipelineResult:
    deterministic = ai_module.merge_years_deterministic(data)
    if not deterministic:
        raise PipelineError(
            "STRUCTURED_DATA_INSUFFICIENT", "结构化财务数据不足，无法生成规则版", stage="fallback"
        )
    markdown = ai_module.build_rules_report(data, reason_code)
    errors = ai_module.validate_rules_report(markdown, _expected(snapshot, {}))
    if errors:
        raise PipelineError(
            "RULES_REPORT_INVALID", "规则版报告校验失败，未保存", stage="fallback"
        )
    return PipelineResult(
        markdown,
        "rules_quick",
        "",
        attempted_model,
        True,
        reason_code,
        coverage or {},
        blank_pages or {},
        0,
    )


def _validate_source_metadata(snapshot: JobInputSnapshot) -> None:
    if not snapshot.sources:
        return
    names = [source.name for source in snapshot.sources]
    years = [source.report_year for source in snapshot.sources]
    hashes = [source.sha256 for source in snapshot.sources]
    invalid = any(
        not source.name
        or Path(source.name).name != source.name
        or source.report_year <= 0
        or source.page_count <= 0
        or source.size <= 0
        or len(source.sha256) != 64
        for source in snapshot.sources
    )
    if invalid or len(names) != len(set(names)) or len(years) != len(set(years)) or len(hashes) != len(set(hashes)):
        raise PipelineError(
            "SOURCE_METADATA_INVALID", "财报来源快照的文件、年度或页数信息无效", stage="prepare"
        )


def run_report_pipeline(
    snapshot: JobInputSnapshot,
    update: Callable[[ProgressUpdate], None],
    deps: PipelineDependencies | None = None,
) -> PipelineResult:
    """组装完整逐页 AI 路径，并仅对可用性错误诚实回退规则版。"""
    deps = deps or PipelineDependencies.default()
    try:
        raw = json.loads(snapshot.financial_data_json)
        data = parser.parse_financial_dict(raw)
    except Exception as exc:
        raise PipelineError(
            "SNAPSHOT_DATA_INVALID", "任务输入中的结构化财务数据无效", stage="prepare"
        ) from exc
    data.company_name = snapshot.company_name
    data.industry = snapshot.industry
    if sorted(data.years or []) != sorted(snapshot.years):
        raise PipelineError("SNAPSHOT_IDENTITY_INVALID", "任务输入年度不一致", stage="prepare")
    _validate_source_metadata(snapshot)

    engine = deps.engine_factory()
    if engine is None:
        return _rules_result(snapshot, data, "AI_NOT_CONFIGURED", deps.ai)
    attempted_model = str(getattr(engine, "model", "") or "")
    if not snapshot.sources:
        raise PipelineError(
            "SOURCE_FILES_UNAVAILABLE", "缺少完整财报来源，未生成报告", stage="prepare"
        )
    if {source.report_year for source in snapshot.sources} != set(snapshot.years):
        raise PipelineError(
            "SOURCE_FILES_INCOMPLETE", "财报来源年度不完整，未生成报告", stage="prepare"
        )

    loaded: LoadedSources | None = None
    try:
        loaded = load_sources_pages(snapshot, update, deps)
        documents: list[dict] = []
        total = sum(source.page_count for source in snapshot.sources)
        for source in sorted(snapshot.sources, key=lambda item: (item.report_year, item.name, item.sha256)):
            source_dict = dataclasses.asdict(source)
            source_dict["company_name"] = snapshot.company_name
            pages = loaded.pages[source.name]
            facts: list[dict] = []
            for chunk in deps.reader.chunk_pages(list(pages)):
                chunk_pages = tuple(
                    page for page in pages
                    if chunk.start_page <= page.page_no <= chunk.end_page
                )
                facts.extend(
                    extract_pages_with_split_retry(
                        engine, source_dict, chunk_pages,
                        ai_module=deps.ai, reader_module=deps.reader,
                    )
                )
            documents.append(deps.ai.merge_document_facts(source_dict, facts))
            update(ProgressUpdate("extract", total, total, f"{source.name} 事实提取完成"))

        deterministic = deps.ai.merge_years_deterministic(data)
        payload = deps.ai.reconcile_with_deterministic(documents, deterministic)
        payload["company_name"] = snapshot.company_name
        expected = _expected(snapshot, loaded.page_coverage)
        expected["required_metrics"] = deps.ai.deterministic_metric_matrix(deterministic)
        expected["conflicts"] = payload.get("conflicts") or []
        errors = deps.ai.validate_reconciled_payload(payload, expected)
        if errors:
            raise PipelineError(
                "RECONCILED_PAYLOAD_INVALID", "对账载荷校验失败，未生成报告", stage="reconcile"
            )
        update(ProgressUpdate("reconcile", total, total, "确定性对账完成"))
        # page_coverage 以本地完整读取为准，写入 payload 供模型参考
        payload["page_coverage"] = {
            name: list(values) for name, values in loaded.page_coverage.items()
        }
        final = generate_final_with_retry(engine, payload, deps.ai)
        content = final.content
        errors = deps.ai.validate_final_report(content, expected)
        if errors:
            # AI 草稿常因表格格式/页覆盖措辞/数值千分位失败：硬化为可过校验结构
            update(
                ProgressUpdate(
                    "validate",
                    total,
                    total,
                    f"最终报告校验未过（{len(errors)} 项），正在结构化修复…",
                )
            )
            try:
                content = deps.ai.harden_final_report(
                    content,
                    expected,
                    deterministic_markdown=deterministic,
                )
            except Exception as exc:
                raise PipelineError(
                    "FINAL_REPORT_INVALID",
                    "最终报告校验失败，未保存：" + "；".join(errors[:6]),
                    stage="validate",
                ) from exc
            errors = deps.ai.validate_final_report(content, expected)
        if errors:
            # 仍失败则退回纯确定性组装（仍标 ai_full 的叙事段可能为空，但保证可保存）
            try:
                content = deps.ai.harden_final_report(
                    "",
                    expected,
                    deterministic_markdown=deterministic,
                )
                errors = deps.ai.validate_final_report(content, expected)
            except Exception:
                errors = errors  # keep original
        if errors:
            raise PipelineError(
                "FINAL_REPORT_INVALID",
                "最终报告校验失败，未保存：" + "；".join(errors[:6]),
                stage="validate",
            )
        update(ProgressUpdate("validate", total, total, "最终报告校验完成"))
        return PipelineResult(
            content,
            "ai_full",
            str(final.model or attempted_model),
            attempted_model,
            False,
            "",
            loaded.page_coverage,
            loaded.blank_pages,
            len(payload.get("conflicts") or []),
        )
    except AIRequestError as exc:
        if not is_request_fallback_allowed(exc):
            raise PipelineError(
                "AI_REQUEST_NOT_FALLBACKABLE",
                "AI 响应或客户端错误，未生成报告",
                stage="request",
            ) from exc
        return _rules_result(
            snapshot,
            data,
            exc.code,
            deps.ai,
            attempted_model=attempted_model,
            coverage=loaded.page_coverage if loaded else {},
            blank_pages=loaded.blank_pages if loaded else {},
        )
    except PipelineError:
        raise
    except AICompletionError as exc:
        raise PipelineError(
            "AI_COMPLETION_INCOMPLETE", "AI 输出未完整结束，未保存", stage="extract"
        ) from exc
    except AIEngineError as exc:
        raise PipelineError(
            "AI_FACT_INVALID", "AI 事实或报告格式无效，未保存", stage="extract"
        ) from exc
