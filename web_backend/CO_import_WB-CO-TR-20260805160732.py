"""利润宝 · 财报导入 API 路由。

复用 core.parser / core.finance / core.industry 的全部计算口径，本层只做
HTTP 适配：上传文件 → 写本地工作区 → 解析 → 指标计算 → 会话摘要。
"""

from __future__ import annotations

import importlib
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from core import case_manifest as case_mod
from core import finance as fin_mod
from core import industry as ind_mod
from core import parser as parser_mod
from core import pipeline as pipeline_mod
from core.interactive import FEASIBILITY_THRESHOLD

# 会话模块文件名含智能体标识连字符，须用 importlib 加载
session = importlib.import_module("web_backend.CO_session_WB-CO-TR-20260805160732")
workspace_db = importlib.import_module("web_backend.CO_db_WB-CO-TR-20260805160732")

router = APIRouter(prefix="/api", tags=["import"])
logger = logging.getLogger(__name__)
_workspace_lifecycle_lock = threading.RLock()

# 导入结果中的指标键（与 core.finance.compute_year_indicators 输出对齐）
INDICATOR_KEYS = [
    "增值税税负率", "所得税税负率", "综合税负率",
    "毛利率", "净利率", "销售费用率", "管理费用率",
    "研发费用率", "财务费用率", "营业收入", "利润总额",
]

# 支持格式：Excel（xlsx）/ CSV / Word（docx）/ PowerPoint（pptx）/ PDF
_EXT_ALLOWED = {".xlsx", ".csv", ".docx", ".pptx", ".pdf"}
_EXT_DISPLAY = ".xlsx / .csv / .docx / .pptx / .pdf"
_FILE_TOO_LARGE = 500 * 1024 * 1024  # 500MB
_HASH_CHUNK_SIZE = 1024 * 1024  # 1 MiB
_IMPORT_BATCH_NAME = re.compile(r"import-[^/]+")


@contextmanager
def workspace_lifecycle_guard():
    """串联 job 启动、会话发布和批次退休判定。"""
    with _workspace_lifecycle_lock:
        yield


def _reject_oversize(upload: UploadFile) -> None:
    size = 0
    while chunk := upload.file.read(65536):
        size += len(chunk)
        if size > _FILE_TOO_LARGE:
            raise HTTPException(status_code=413, detail="文件超过 500MB 上限")
    upload.file.seek(0)


def _save_upload(upload: UploadFile, workdir: Path) -> Path:
    """将上传文件写入本地工作区，返回落盘路径。

    浏览器文件夹上传（webkitdirectory）的文件名可能带相对路径前缀
    （如「三年财报/2021年审计报告.xlsx」），需提取纯文件名并防止路径穿越。
    """
    raw_name = (upload.filename or "").replace("\\", "/")
    # 取最末段作为纯文件名，忽略目录前缀
    safe_name = raw_name.rsplit("/", 1)[-1] or "upload"
    ext = Path(safe_name).suffix.lower()
    if ext not in _EXT_ALLOWED:
        raise HTTPException(status_code=400, detail=f"不支持的格式：{ext or '(无后缀)'}，仅支持 {_EXT_DISPLAY}")
    _reject_oversize(upload)
    # 同一批次中也隔离每个文件，保留显示名称同时避免同名上传相互覆盖。
    storage_dir = workdir / uuid.uuid4().hex
    storage_dir.mkdir(parents=True, exist_ok=False)
    dest = storage_dir / safe_name
    try:
        with open(dest, "wb") as fh:
            while chunk := upload.file.read(65536):
                fh.write(chunk)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"文件写入失败：{e}")
    return dest


def _workdir() -> Path:
    """返回当前运行时工作区，并确保目录存在。"""
    configured = os.environ.get("LIRUNBAO_WORKSPACE_PATH", "").strip()
    base = (
        Path(configured).resolve()
        if configured
        else Path(__file__).resolve().parent / "workspaces"
    )
    base.mkdir(parents=True, exist_ok=True)
    return base


def _source_files(
    saved: list[Path],
    previews: list[dict],
    parsed_years: dict[str, list[int]],
) -> list[dict]:
    """构建导入文件的可追溯快照，不保存或输出文件正文。"""
    snapshots = []
    for index, path in enumerate(saved):
        name = path.name
        report_year = _report_year(path, parsed_years.get(str(path), []))
        preview = previews[index] if index < len(previews) else {}
        snapshots.append(
            {
                "path": str(path.resolve()),
                "name": name,
                "sha256": _file_sha256(path),
                "size": path.stat().st_size,
                "report_year": report_year,
                "page_count": _page_count(preview),
            }
        )
    return snapshots


def _file_sha256(path: Path) -> str:
    """以固定大小分块计算文件哈希，避免把大文件整体载入内存。"""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _managed_import_batch(source_path: object, workspace: Path) -> Path | None:
    """从快照路径解析受管批次；外部、根文件和非批次路径均返回 None。"""
    if not isinstance(source_path, (str, os.PathLike)) or not source_path:
        return None
    try:
        root = workspace.resolve()
        source = Path(source_path).resolve()
        relative = source.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None
    if len(relative.parts) < 2:
        return None
    batch = root / relative.parts[0]
    return _validated_import_batch(batch, root)


def _validated_import_batch(batch: Path, workspace: Path) -> Path | None:
    """只接受受管工作区直属的真实 import-* 目录。"""
    if ".." in batch.parts:
        return None
    try:
        root = workspace.resolve()
        candidate = batch.resolve()
    except (OSError, RuntimeError):
        return None
    if (
        batch.is_symlink()
        or candidate.parent != root
        or _IMPORT_BATCH_NAME.fullmatch(candidate.name) is None
        or not candidate.is_dir()
    ):
        return None
    return candidate


def _remove_managed_import_batch(batch: Path, workspace: Path) -> bool:
    """再次校验后删除单个受管批次；校验或删除失败时安全保留。"""
    candidate = _validated_import_batch(batch, workspace)
    if candidate is None:
        return False
    try:
        shutil.rmtree(candidate)
    except OSError:
        return False
    return True


def _active_report_jobs_exist() -> bool:
    job_mod = importlib.import_module(
        "web_backend.CO_ai_report_job_WB-CO-TR-20260807113737"
    )
    return bool(job_mod.has_active_jobs())


def _queue_retired_sources(
    old_sources: list[dict], workspace: Path, current_batch: Path | None, reason: str
) -> None:
    """在会话丢弃来源前持久登记安全候选，避免引用永久丢失。"""
    current = current_batch.resolve() if current_batch is not None else None
    candidates: set[Path] = set()
    for source in old_sources:
        if not isinstance(source, dict):
            continue
        candidate = _managed_import_batch(source.get("path"), workspace)
        if candidate is not None and candidate != current:
            candidates.add(candidate)
    for candidate in candidates:
        workspace_db.queue_workspace_retirement(
            str(workspace.resolve()), str(candidate), reason
        )


def _current_managed_batches(
    retirements: list[dict], additional_workspaces: tuple[Path, ...] = ()
) -> set[Path]:
    """以 SQLite 当前会话为事实源，保护仍被持久会话引用的批次。"""
    batches: set[Path] = set()
    stored = workspace_db.load_session()
    persisted_sources = stored.get("source_files", []) if stored is not None else []
    workspaces = {
        Path(retirement["workspace_path"]).resolve() for retirement in retirements
    }
    workspaces.update(workspace.resolve() for workspace in additional_workspaces)
    for source in persisted_sources:
        if not isinstance(source, dict):
            continue
        for workspace in workspaces:
            candidate = _managed_import_batch(source.get("path"), workspace)
            if candidate is not None:
                batches.add(candidate)
    return batches


def _write_cleanup_fallback_marker(
    batch: Path, workspace: Path, deletion_error: str, queue_error: Exception
) -> str:
    """退休 DB 不可用时写入工作区兜底标记，避免残留变成无追踪孤儿。"""
    marker_id = f"fallback-{uuid.uuid4().hex}"
    marker = workspace.resolve() / f".cleanup-pending-{marker_id}.json"
    payload = {
        "cleanup_id": marker_id,
        "workspace_path": str(workspace.resolve()),
        "batch_path": str(batch.resolve()),
        "reason": "failed_import_rollback",
        "last_error": deletion_error,
        "queue_error": f"{type(queue_error).__name__}: {queue_error}",
    }
    try:
        marker.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        logger.exception(
            "导入回滚双失败且兜底标记写入失败 cleanup_id=%s batch=%s",
            marker_id,
            batch,
        )
    return marker_id


def _drain_cleanup_fallbacks(
    workspace: Path, protected_batches: set[Path]
) -> int:
    """验证兜底标记并跳过 SQLite 当前会话仍引用的批次。"""
    removed = 0
    for marker in workspace.glob(".cleanup-pending-fallback-*.json"):
        try:
            if marker.is_symlink():
                raise ValueError("兜底标记不得为符号链接")
            payload = json.loads(marker.read_text(encoding="utf-8"))
            required = (
                "cleanup_id",
                "workspace_path",
                "batch_path",
                "reason",
                "last_error",
                "queue_error",
            )
            if not isinstance(payload, dict) or any(
                not isinstance(payload.get(field), str) or not payload[field]
                for field in required
            ):
                raise ValueError("兜底标记 schema 非法")
            if marker.name != f".cleanup-pending-{payload['cleanup_id']}.json":
                raise ValueError("兜底标记 cleanup_id 与文件名不一致")
            if payload["reason"] != "failed_import_rollback":
                raise ValueError("兜底标记 reason 非法")
            if Path(payload["workspace_path"]).resolve() != workspace.resolve():
                raise ValueError("兜底标记工作区不匹配")
            raw_batch = Path(payload["batch_path"])
            if not raw_batch.is_absolute():
                raise ValueError("兜底批次路径必须为绝对路径")
            if not raw_batch.exists() and not raw_batch.is_symlink():
                marker.unlink()
                continue
            candidate = _validated_import_batch(raw_batch, workspace)
            if candidate is None:
                raise ValueError("兜底批次未通过受管工作区安全校验")
            if candidate in protected_batches:
                logger.error(
                    "工作区兜底标记指向 SQLite 当前会话批次，保留待后续重试 marker=%s",
                    marker,
                )
                continue
            if _remove_managed_import_batch(candidate, workspace):
                marker.unlink()
                removed += 1
        except (
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            KeyError,
            json.JSONDecodeError,
        ):
            logger.exception("工作区兜底清理重试失败 marker=%s", marker)
    return removed


def _drain_workspace_retirements() -> int:
    """无活动 job 时回收不再被当前会话引用的退休批次。"""
    if _active_report_jobs_exist():
        return 0
    workspace = _workdir()
    retirements = workspace_db.list_workspace_retirements()
    current_batches = _current_managed_batches(
        retirements, additional_workspaces=(workspace,)
    )
    removed = _drain_cleanup_fallbacks(workspace, current_batches)
    for retirement in retirements:
        retirement_id = int(retirement["id"])
        workspace = Path(retirement["workspace_path"])
        raw_batch = Path(retirement["batch_path"])
        if not raw_batch.exists() and not raw_batch.is_symlink():
            workspace_db.delete_workspace_retirement(retirement_id)
            continue
        candidate = _validated_import_batch(raw_batch, workspace)
        if candidate is None:
            workspace_db.fail_workspace_retirement(
                retirement_id, "退休路径未通过受管工作区安全校验"
            )
            continue
        if candidate in current_batches:
            continue
        try:
            deleted = _remove_managed_import_batch(candidate, workspace)
        except Exception as exc:
            workspace_db.fail_workspace_retirement(
                retirement_id, f"{type(exc).__name__}: {exc}"
            )
            logger.exception("工作区退休批次删除异常 retirement_id=%s", retirement_id)
            continue
        if deleted:
            workspace_db.delete_workspace_retirement(retirement_id)
            removed += 1
        else:
            workspace_db.fail_workspace_retirement(
                retirement_id, "受管批次删除失败，等待下次重试"
            )
            logger.error("工作区退休批次删除失败 retirement_id=%s", retirement_id)
    return removed


def _best_effort_drain_workspace_retirements(context: str) -> int:
    """隔离提交后的退休扫描故障，已提交操作仍返回其真实结果。"""
    try:
        return _drain_workspace_retirements()
    except Exception:
        logger.exception("已提交会话的工作区退休扫描失败 context=%s", context)
        return 0


def retry_workspace_cleanup() -> int:
    """重试持久退休队列；活动 job 存在时保守延迟。"""
    with workspace_lifecycle_guard():
        return _drain_workspace_retirements()


def _rollback_new_batch(batch: Path, workspace: Path) -> HTTPException | None:
    """优先物理回滚；只有删除失败才登记退休 DB 或持久兜底标记。"""
    try:
        deleted = _remove_managed_import_batch(batch, workspace)
    except Exception as exc:
        deleted = False
        error = f"{type(exc).__name__}: {exc}"
    else:
        error = "受管批次删除失败，等待下次重试"
    if deleted:
        return None
    try:
        retirement_id = workspace_db.queue_workspace_retirement(
            str(workspace.resolve()), str(batch.resolve()), "failed_import_rollback"
        )
    except Exception as queue_error:
        marker_id = _write_cleanup_fallback_marker(
            batch, workspace, error, queue_error
        )
        logger.exception(
            "导入回滚删除与退休登记均失败 cleanup_id=%s batch=%s",
            marker_id,
            batch,
        )
        return HTTPException(
            status_code=500,
            detail=(
                "导入失败且临时批次清理失败，已写入兜底重试标记"
                f"（cleanup_id={marker_id}）"
            ),
        )
    try:
        workspace_db.fail_workspace_retirement(retirement_id, error)
    except Exception:
        logger.exception("导入回滚失败状态更新异常 retirement_id=%s", retirement_id)
    logger.error("导入回滚清理失败 retirement_id=%s", retirement_id)
    return HTTPException(
        status_code=500,
        detail=f"导入失败且临时批次清理失败，已登记重试（cleanup_id={retirement_id}）",
    )


def _report_year(path: Path, years: list[int]) -> int | None:
    """优先使用文件解析到的报告年份，文件名仅作无歧义兜底。"""
    parsed = sorted({int(year) for year in years if 1900 <= int(year) <= 2100})
    if parsed:
        return parsed[-1]
    filename_years = set(re.findall(r"(?<!\d)(20\d{2})(?!\d)", path.name))
    return int(filename_years.pop()) if len(filename_years) == 1 else None


def _page_count(preview: dict) -> int:
    """从 PDF 预览说明读取页数；其他格式或读取失败时记为 0。"""
    for note in preview.get("notes", []):
        match = re.search(r"共\s*(\d+)\s*页", str(note))
        if match:
            return int(match.group(1))
    return 0


def _handle_parse_error(e: Exception) -> HTTPException:
    """将 core.parser 错误映射为 4xx/5xx。ParserError 属用户输入问题 → 400。"""
    if isinstance(e, parser_mod.ParserError):
        return HTTPException(status_code=400, detail=str(e))
    return HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


def _year_from_filename(path: str) -> int | None:
    m = re.search(r"(20\d{2})", os.path.basename(path) or "")
    return int(m.group(1)) if m else None


def _align_year_to_filename(data, path: str):
    """多份年报导入时：文件名年份优先，修正模型误判的唯一年份。

    通用规则，适用于任意企业「2022年审计报告.pdf」命名；
    不依赖艺康或其它特定客户。
    """
    y_from_name = _year_from_filename(path)
    if not y_from_name or not data:
        return data
    years = list(data.years or [])
    if years == [y_from_name]:
        return data
    if len(years) == 1 and years[0] != y_from_name:
        old_y = years[0]
        for table_name in ("income_statement", "balance_sheet", "account_balances"):
            table = getattr(data, table_name) or {}
            new_table: dict = {}
            for acc, yv in table.items():
                if not yv:
                    continue
                new_table[acc] = {
                    (y_from_name if int(yr) == old_y else int(yr)): val
                    for yr, val in yv.items()
                }
            setattr(data, table_name, new_table)
        data.years = [y_from_name]
        meta = dict(data.parsed_meta or {})
        meta["year_aligned_from_filename"] = y_from_name
        data.parsed_meta = meta
    elif not years:
        # 无年份：把现有金额归到文件名年
        for table_name in ("income_statement", "balance_sheet", "account_balances"):
            table = getattr(data, table_name) or {}
            new_table: dict = {}
            for acc, yv in table.items():
                if not yv:
                    continue
                pick = None
                for v in reversed(list(yv.values())):
                    if v is not None:
                        pick = v
                        break
                if pick is not None:
                    new_table[acc] = {y_from_name: pick}
            setattr(data, table_name, new_table)
        data.years = [y_from_name]
    return data


def _parse_one(path: str, company_name: str, industry: str):
    """解析单个文件（供 pipeline.parse_one 回调；含 DeepSeek PDF 路径）。

    PDF（含扫描件）：优先走 DeepSeek 全权解析。已配置 AI 时 DeepSeek 必须
    成功，失败则抛明确错误，绝不静默回退到乱码的 pdfplumber 表格提取；
    未配置 AI 时回退 parse_smart（供带表格的普通 PDF），扫描件会明确报错。
    其它格式（xlsx/csv/docx/pptx）走 core.parser.parse_smart。
    """
    if path.lower().endswith(".pdf"):
        ai = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")
        cred = ai.get_credentials()
        if cred.get("api_key"):
            ds = importlib.import_module("core.CO_deepseek_parse_WB-CO-TR-20260806140818")
            try:
                data = ds.parse_pdf_with_deepseek(
                    path,
                    api_key=cred["api_key"],
                    base_url=cred["base_url"],
                    model=cred["model"],
                    company_name=company_name,
                    industry=industry or "制造业",
                )
                return _align_year_to_filename(data, path)
            except parser_mod.ParserError:
                raise
            except Exception as e:
                # DeepSeek 调用失败：明确报错，避免产出乱码数据
                raise parser_mod.ParserError(
                    f"扫描件 PDF 解析失败（DeepSeek）：{type(e).__name__}: {e}"
                ) from e
        # 未配置 AI：回退本地解析。带表格的普通 PDF 可解析，扫描件会抛 ParserError
        data = parser_mod.parse_smart(
            path, company_name=company_name, industry=industry or "制造业",
        )
        return _align_year_to_filename(data, path)
    return parser_mod.parse_smart(
        path, company_name=company_name, industry=industry or "制造业",
    )


def _collect_ocr_texts(previews: list) -> list[str]:
    ocr_texts: list[str] = []
    for pv in previews:
        fname = pv.get("name", "")
        file_notes = [n for n in pv.get("notes", []) if "OCR" in n]
        if file_notes:
            ocr_texts.append(f"【文件 {fname}】\n" + "\n".join(file_notes))
    return ocr_texts


def _snapshot_import_history(source_files: list, previews: list) -> None:
    """导入成功后把会话快照存档为一条导入历史（供卡片快速载入）。

    与最近一条 session_version 相同时由 DB 层去重跳过；任何失败仅记日志，
    不阻塞导入主流程。新导入即新案例：激活该行并把进度快照清零。
    """
    try:
        stored = workspace_db.load_session()
        if stored is None:
            return
        new_id = workspace_db.save_import_history(
            company_name=stored.get("company_name") or "",
            industry=stored.get("industry") or "",
            years=stored.get("years") or [],
            file_count=len(source_files or []),
            session_version=stored.get("session_version") or "",
            data_json=stored.get("data_json") or {},
            ocr_texts=stored.get("ocr_texts") or [],
            source_files=source_files or [],
            saved_previews=previews or [],
        )
        # 去重复用旧行（new_id=None）时激活最新一行；新行直接激活
        workspace_db.set_active_import_history(new_id)
        # 新会话无诊断/互动进度：覆盖旧行上可能存在的快照
        workspace_db.snapshot_case_progress()
    except Exception:
        logger.warning("导入历史存档失败（不阻塞导入）", exc_info=True)


def _publish_bundle(
    bundle: pipeline_mod.CaseBundle,
    *,
    previews: list,
    source_files: list,
    message: str | None = None,
) -> dict:
    """会话发布 + 统一 import 响应。"""
    data = bundle.financial_data
    indicators = [_indicator_row(data, yr) for yr in (data.years or [])]
    session.replace(
        data,
        bundle.ocr_texts,
        source_files,
        saved_previews=previews,
    )
    _snapshot_import_history(source_files, previews)
    summ = session.summary()
    return bundle.to_import_response(
        indicators=indicators,
        previews=previews,
        summary=summ,
        message=message,
    )


def _indicator_row(data, year: int) -> dict:
    """计算单年指标，返回前端友好的 {key: {value, note, estimate}}。"""
    ind = fin_mod.compute_year_indicators(data, year)
    out: dict = {}
    for key in INDICATOR_KEYS:
        val = ind.get(key)
        if isinstance(val, dict):
            out[key] = {
                "value": val.get("value"),
                "note": val.get("note", ""),
                "estimate": val.get("estimate", False),
            }
        else:
            out[key] = {"value": val, "note": "", "estimate": False}
    return out


@router.get("/industries")
def list_industries() -> dict:
    """可选行业下拉（含说明；names 保持向后兼容）。"""
    items = ind_mod.list_industries_with_desc()
    return {
        "industries": items,
        "names": ind_mod.list_industries(),
        "default": ind_mod.DEFAULT_INDUSTRY,
    }


class IndustryRecommendIn(BaseModel):
    company_name: str = ""
    overview: str = ""  # 可选财务概览文本


class IdentifyIn(BaseModel):
    """AI 识别企业名称与行业：files 为 [{name, text}]，text 为预览采集文本。"""
    files: list[dict] = []


@router.post("/import/identify")
def identify_company(body: IdentifyIn) -> dict:
    """AI/规则双路径识别企业名称与行业。

    - AI 可用：综合文件名 + 预览文本识别 {company_name, industry, reason}；
      解析失败回退规则。
    - AI 不可用/失败：规则提取——企业名从文件名去年份/后缀；行业关键词匹配。
    返回 {company_name, industry, reason, source, fallback}。
    """
    files = body.files or []
    names = [str(f.get("name", "")).strip() for f in files if f.get("name")]
    text_chunks = [str(f.get("text", "")).strip() for f in files if f.get("text")]
    combined_text = "\n".join(text_chunks)[:6000]  # 控制输入长度

    def _rule_extract() -> tuple[str, str, str]:
        """规则提取：返回 (企业名, 行业, 理由)。"""
        company = ""
        for name in names:
            stem = re.sub(r"\.(pdf|xlsx|csv|docx|pptx)$", "", name, flags=re.I)
            # 先整体删除复合后缀词（避免先删年份后残留「度」「报」）
            stem = re.sub(
                r"(19|20)\d{2}年度审计报告|(19|20)\d{2}年度财务报告|(19|20)\d{2}年度报告|"
                r"(19|20)\d{2}审计报告|(19|20)\d{2}财务报告|(19|20)\d{2}年报|(19|20)\d{2}财报",
                "", stem,
            )
            # 再删剩余年份
            stem = re.sub(r"(19|20)\d{2}年?", "", stem)
            # 删除剩余后缀词（先长后短）
            stem = re.sub(r"年度审计报告|年度财务报告|年度报告|审计报告|财务报告|合并报告|年报|财报|报告", "", stem)
            stem = stem.replace("年度", "")
            stem = stem.replace("（", "").replace("）", "").replace("(", "").replace(")", "")
            stem = stem.strip(" -_—·.")
            if stem and not re.fullmatch(r"[\d\W]+", stem):
                company = stem
                break
        if not company:
            # 纯年份/通用后缀文件名无法提取企业名：留空让用户填写（或 AI 从内容识别）
            company = ""
        industry, ind_reason = ind_mod.recommend_by_rule(company)
        if not company:
            return "", industry, "未能从文件名识别企业名称，请手动填写；" + ind_reason
        return company, industry, f"从文件名「{company}」提取企业名称；" + ind_reason

    ai_mod = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")
    engine = ai_mod._engine(timeout=30.0)
    if engine is not None and engine.is_available() and (names or combined_text):
        names_txt = "、".join(names) if names else "（无文件名）"
        industry_names = "、".join(ind_mod.list_industries())
        system_prompt = (
            "你是企业财报解析助手。根据给定审计报告的文件名与内容文本，"
            "识别出：1) 企业名称（报告中反复出现的正式全称或简称）；"
            "2) 所属行业（必须从给定行业列表中选择一个）。"
            f"可选行业：{industry_names}\n"
            "只返回 JSON：{\"company_name\": \"企业名\", \"industry\": \"行业名\", \"reason\": \"一句话依据\"}。"
            "无法确定企业名称时 company_name 返回空字符串。"
        )
        user_prompt = (
            f"文件名：{names_txt}\n"
            f"文档内容（前 {min(len(combined_text), 6000)} 字符）：\n{combined_text}"
        )
        try:
            content = engine.chat(user_prompt, system_prompt=system_prompt, max_tokens=400)
            import json as _json
            start = content.find("{")
            end = content.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("AI 返回非 JSON")
            obj = _json.loads(content[start:end + 1])
            company = str(obj.get("company_name", "")).strip()
            industry = str(obj.get("industry", "")).strip()
            reason = str(obj.get("reason", "")).strip()
            if industry not in ind_mod.list_industries():
                industry = ""
            if not company and not industry:
                raise ValueError("AI 未识别出任何信息")
            return {
                "company_name": company,
                "industry": industry or ind_mod.DEFAULT_INDUSTRY,
                "reason": reason or "AI 根据文档内容识别",
                "source": "ai",
                "fallback": False,
            }
        except Exception as e:
            logger.info("AI 企业识别失败，回退规则：%s", type(e).__name__)
            company, industry, reason = _rule_extract()
            return {
                "company_name": company,
                "industry": industry,
                "reason": f"{reason}（AI 识别暂不可用，已按规则匹配）",
                "source": "rule",
                "fallback": True,
            }
    company, industry, reason = _rule_extract()
    return {
        "company_name": company,
        "industry": industry,
        "reason": reason,
        "source": "rule",
        "fallback": False,
    }


@router.post("/industries/recommend")
def recommend_industry(body: IndustryRecommendIn) -> dict:
    """AI/规则双路径推荐行业。

    AI 可用时优先：提示词要求返回 {industry, reason}；解析失败回退规则。
    AI 不可用或未配置：规则关键词匹配（兜底默认制造业），返回 fallback 标识。
    """
    ai_mod = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")
    engine = ai_mod._engine(timeout=15.0)
    if engine is not None and engine.is_available():
        names = "、".join(ind_mod.list_industries())
        system_prompt = (
            "你是企业行业分类助手。根据企业名称与财务概览，从给定行业列表中"
            "选出最匹配的一个，并给出简短理由。"
            "只返回 JSON：{\"industry\": \"行业名\", \"reason\": \"理由\"}。"
            "行业名必须是列表中存在的名称。"
        )
        user_prompt = (
            f"可选行业：{names}\n企业名称：{body.company_name or '（未提供）'}\n"
            f"财务概览：{body.overview or '（未提供）'}"
        )
        try:
            content = engine.chat(user_prompt, system_prompt=system_prompt, max_tokens=300)
            import json as _json
            start = content.find("{")
            end = content.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("AI 返回非 JSON")
            obj = _json.loads(content[start:end + 1])
            industry = str(obj.get("industry", "")).strip()
            reason = str(obj.get("reason", "")).strip()
            if industry in ind_mod.list_industries():
                return {
                    "industry": industry,
                    "reason": reason or f"AI 推荐 {industry}",
                    "source": "ai",
                    "fallback": False,
                }
            raise ValueError(f"AI 返回行业不在列表中：{industry}")
        except Exception as e:
            logger.info("AI 行业推荐失败，回退规则：%s", type(e).__name__)
            industry, reason = ind_mod.recommend_by_rule(body.company_name, body.overview)
            return {
                "industry": industry,
                "reason": f"{reason}（AI 推荐暂不可用，已按规则匹配）",
                "source": "rule",
                "fallback": True,
            }
    industry, reason = ind_mod.recommend_by_rule(body.company_name, body.overview)
    return {
        "industry": industry,
        "reason": reason,
        "source": "rule",
        "fallback": False,
    }


@router.post("/import")
def import_financials(
    files: list[UploadFile] = File(...),
    company_name: str = Form(""),
    industry: str = Form("制造业"),
) -> dict:
    """导入财报。

    files 支持两种形态：
    - 单个文件（xlsx/csv/docx/pptx/pdf，含 利润表/资产负债表/科目余额表），文件名随意
    - 多个文件：每个文件是一整年的完整审计报告（如 2021/2022/2023 年报告），
      自动按 科目+年份 合并成多年数据集（merge_years）
    返回会话摘要、按年指标与各文件预览。
    """
    if not files:
        raise HTTPException(status_code=400, detail="请至少选择一个文件")

    workspace = _workdir()
    with workspace_lifecycle_guard():
        captured_version = session.get_version()
        captured_sources = session.get_source_files()
    workdir = workspace / f"import-{uuid.uuid4().hex}"
    workdir.mkdir(parents=True, exist_ok=False)
    saved: list[Path] = []
    published = False
    try:
        for f in files:
            saved.append(_save_upload(f, workdir))

        previews = [_safe_preview(str(p)) for p in saved]
        ocr_texts = _collect_ocr_texts(previews)
        # 先解析拿 years 供 source 元数据（pipeline 内再 parse 一次会重复 AI 调用）
        # → 改为：pipeline 外预解析，from_data 组装，避免双重 DeepSeek
        parsed_list = []
        parsed_years: dict[str, list[int]] = {}
        errors: list[str] = []
        for p in saved:
            try:
                one = _parse_one(str(p), company_name or "", industry or "制造业")
                parsed_list.append(one)
                parsed_years[str(p)] = list(one.years or [])
            except parser_mod.ParserError as e:
                errors.append(f"{os.path.basename(str(p))}: {e}")
            except Exception as e:
                errors.append(f"{os.path.basename(str(p))}: {type(e).__name__}: {e}")
        if errors:
            raise parser_mod.ParserError("以下文件解析失败：" + "；".join(errors))
        if not parsed_list:
            data = parser_mod.make_empty_data(
                company_name=company_name or os.path.basename(saved[0]),
                industry=industry or "制造业",
            )
        elif len(parsed_list) == 1:
            data = parsed_list[0]
        else:
            data = parser_mod.merge_years(*parsed_list)
        if not data.company_name:
            data.company_name = company_name or os.path.basename(saved[0])
        if not data.industry:
            data.industry = industry or "制造业"

        source_files = _source_files(saved, previews, parsed_years)
        bundle = pipeline_mod.run_case_pipeline_from_data(
            data,
            options=pipeline_mod.PipelineOptions(
                company_name=company_name or data.company_name or "",
                industry=industry or data.industry or "制造业",
                case_id=None,
            ),
            ocr_texts=ocr_texts,
            sources=source_files,
        )
        with workspace_lifecycle_guard():
            if (
                session.get_version() != captured_version
                or session.get_source_files() != captured_sources
            ):
                raise HTTPException(
                    status_code=409,
                    detail="会话已被另一导入请求更新，请重试",
                )
            _queue_retired_sources(
                captured_sources, workspace, workdir, "session_replaced"
            )
            response = _publish_bundle(
                bundle, previews=previews, source_files=source_files
            )
            published = True
            _best_effort_drain_workspace_retirements("import")
    except parser_mod.ParserError as e:
        with workspace_lifecycle_guard():
            cleanup_error = None if published else _rollback_new_batch(workdir, workspace)
        if cleanup_error is not None:
            raise cleanup_error
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        with workspace_lifecycle_guard():
            cleanup_error = None if published else _rollback_new_batch(workdir, workspace)
        if cleanup_error is not None:
            raise cleanup_error
        raise
    except Exception as e:
        with workspace_lifecycle_guard():
            cleanup_error = None if published else _rollback_new_batch(workdir, workspace)
        if cleanup_error is not None:
            raise cleanup_error
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")

    return response


@router.post("/import/sample")
def import_sample() -> dict:
    """载入内置示例数据（离线）— 与上传/案例同一 pipeline。"""
    from data.make_sample import build_sample_data

    data = parser_mod.parse_financial_dict(build_sample_data())
    bundle = pipeline_mod.run_case_pipeline_from_data(
        data,
        options=pipeline_mod.PipelineOptions(
            company_name=data.company_name or "",
            industry=data.industry or "制造业",
            case_id="sample",
        ),
        ocr_texts=[],
        sources=[],
    )
    workspace = _workdir()
    with workspace_lifecycle_guard():
        old_sources = session.get_source_files()
        _queue_retired_sources(old_sources, workspace, None, "sample_replaced")
        response = _publish_bundle(bundle, previews=[], source_files=[])
        _best_effort_drain_workspace_retirements("sample")
    return response


# ── 标准案例包：扫描 demo_output/cases/*/manifest.json（Phase B）──────────

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


@router.get("/import/cases")
def list_import_cases() -> dict:
    """列出可一键载入的案例包（manifest 驱动，无硬编码公司逻辑）。"""
    cases = case_mod.list_cases_public()
    return {
        "cases": cases,
        "hint": (
            "新增案例：在 demo_output/cases/<id>/ 放置 manifest.json 与文件即可。"
            "任意企业日常请用 POST /api/import 多文件上传，不必建案例包。"
        ),
    }


@router.post("/import/case/{case_id}")
def import_case_pack(case_id: str) -> dict:
    """一键载入案例包（manifest → 与 /api/import 相同 pipeline）。

    case_id 支持 manifest.id 或 aliases（如 audit_3years → audit_yikang_3y）。
    任意企业审计：请用正常「财报导入」上传，不依赖案例包。
    """
    try:
        manifest = case_mod.get_manifest(case_id)
        paths = case_mod.resolve_case_files(manifest)
    except case_mod.CaseManifestError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    company_name = manifest.company_name or ""
    industry = manifest.industry or "制造业"
    tax_hint = manifest.income_tax_nominal_rate
    canonical_id = manifest.id

    workspace = _workdir()
    with workspace_lifecycle_guard():
        captured_version = session.get_version()
        captured_sources = session.get_source_files()
    workdir = workspace / f"import-{uuid.uuid4().hex}"
    workdir.mkdir(parents=True, exist_ok=False)
    saved: list[Path] = []
    published = False
    try:
        for src in paths:
            dest = workdir / src.name
            shutil.copy2(src, dest)
            saved.append(dest)

        previews = [_safe_preview(str(p)) for p in saved]
        ocr_texts = _collect_ocr_texts(previews)
        parsed: list = []
        errors: list[str] = []
        parsed_years: dict[str, list[int]] = {}
        for p in saved:
            try:
                one = _parse_one(str(p), company_name, industry)
                if not one.company_name:
                    one.company_name = company_name
                if not one.industry:
                    one.industry = industry
                parsed.append(one)
                parsed_years[str(p)] = list(one.years or [])
            except parser_mod.ParserError as e:
                errors.append(f"{p.name}: {e}")
            except Exception as e:
                errors.append(f"{p.name}: {type(e).__name__}: {e}")
        if errors:
            raise parser_mod.ParserError("案例解析失败：" + "；".join(errors))
        if not parsed:
            raise parser_mod.ParserError("案例解析结果为空")

        data = parser_mod.merge_years(*parsed) if len(parsed) > 1 else parsed[0]
        if not data.company_name:
            data.company_name = company_name
        if not data.industry:
            data.industry = industry

        source_files = _source_files(saved, previews, parsed_years)
        bundle = pipeline_mod.run_case_pipeline_from_data(
            data,
            options=pipeline_mod.PipelineOptions(
                company_name=company_name,
                industry=industry,
                case_id=canonical_id,
                income_tax_nominal_rate=tax_hint,
            ),
            ocr_texts=ocr_texts,
            sources=source_files,
        )
        with workspace_lifecycle_guard():
            if (
                session.get_version() != captured_version
                or session.get_source_files() != captured_sources
            ):
                raise HTTPException(status_code=409, detail="会话已被另一导入请求更新，请重试")
            _queue_retired_sources(
                captured_sources, workspace, workdir, "session_replaced"
            )
            response = _publish_bundle(
                bundle,
                previews=previews,
                source_files=source_files,
                message=None,
            )
            published = True
            summ = response.get("summary") or {}
            dq = response.get("data_quality") or {}
            years_s = "/".join(str(y) for y in (bundle.financial_data.years or []))
            response["message"] = (
                f"已载入案例 {canonical_id}：{bundle.financial_data.company_name} · "
                f"{years_s} · 命中科目 {summ.get('matched', 0)}"
                + (f" · 数据置信度 {dq.get('confidence', '—')}" if dq else "")
            )
            response["case_id"] = canonical_id
            response["case_label"] = manifest.label
            _best_effort_drain_workspace_retirements("import_case")
        return response
    except parser_mod.ParserError as e:
        with workspace_lifecycle_guard():
            cleanup_error = None if published else _rollback_new_batch(workdir, workspace)
        if cleanup_error is not None:
            raise cleanup_error
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        with workspace_lifecycle_guard():
            cleanup_error = None if published else _rollback_new_batch(workdir, workspace)
        if cleanup_error is not None:
            raise cleanup_error
        raise
    except Exception as e:
        with workspace_lifecycle_guard():
            cleanup_error = None if published else _rollback_new_batch(workdir, workspace)
        if cleanup_error is not None:
            raise cleanup_error
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@router.get("/import/saved-previews")
def saved_previews() -> dict:
    """返回最近一次导入时保存的文件预览（切页/刷新后仍可查看）。"""
    session.restore_from_db()
    return {"files": session.get_saved_previews()}


@router.get("/session")
def get_session() -> dict:
    """返回当前会话摘要与按年指标；无数据时返回空。"""
    # 内存为空时尝试从 SQLite 恢复（刷新/重启后数据仍保留）
    session.restore_from_db()
    summ = session.summary()
    if summ is None:
        return {
            "session": None,
            "indicators": [],
            "years": [],
            "data_quality": {},
            "policy": {},
            "numeric_audit": {},
        }
    data = session.get_data()
    indicators = [_indicator_row(data, yr) for yr in data.years] if data else []
    return {
        "session": summ,
        "indicators": indicators,
        "years": data.years if data else [],
        "data_quality": session.get_data_quality(),
        "policy": session.get_policy(),
        "numeric_audit": session.get_numeric_audit(),
    }


@router.post("/session/clear")
def clear_session() -> dict:
    """清空当前会话（重新导入用）。清除 FinancialData 与 OCR 文本缓存。"""
    workspace = _workdir()
    with workspace_lifecycle_guard():
        old_sources = session.get_source_files()
        _queue_retired_sources(old_sources, workspace, None, "session_cleared")
        session.clear()
        _best_effort_drain_workspace_retirements("clear")
    return {"session": None, "indicators": [], "years": []}


@router.post("/preview")
def preview_upload(files: list[UploadFile] = File(...)) -> dict:
    """预览上传文件内容（不写入会话）。返回各文件的表格与文本片段。"""
    if not files:
        raise HTTPException(status_code=400, detail="请至少选择一个文件")
    workspace = _workdir()
    with tempfile.TemporaryDirectory(prefix="preview-", dir=workspace) as temporary:
        workdir = Path(temporary)
        saved: list[Path] = []
        for f in files:
            saved.append(_save_upload(f, workdir))
        previews = [_safe_preview(str(p)) for p in saved]
    return {"files": previews}


# ── 导入历史（卡片快速载入）────────────────────────────────────────────


@router.get("/import/history")
def get_import_history() -> dict:
    """导入历史列表（轻量字段，新→旧）。"""
    return {"history": workspace_db.list_import_history()}


@router.post("/import/history/{entry_id}/load")
def load_import_history(entry_id: int) -> dict:
    """从导入历史完整载入案例：响应契约与 /import 一致 + restored 进度信息。

    载入不只恢复财务数据——同时恢复该案例保存的诊断结果、互动进度与
    导出解锁状态（进度快照在诊断/互动每次保存时同步），并带回该版本
    已生成的 AI 合并报告 id（前端直接展示，不重新整理）。
    载入不重复存档，也不触发 AI 报告重算。
    """
    entry = workspace_db.get_import_history_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="导入记录不存在或已删除")
    try:
        # 必须用忠实还原（保 parsed_meta 与年份结构），版本指纹才能与导入时一致
        data = session.from_persisted_dict(entry.get("data_json") or {})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导入记录数据损坏，无法载入：{e}")
    data.company_name = entry.get("company_name") or data.company_name
    data.industry = entry.get("industry") or data.industry
    previews = entry.get("saved_previews") or []
    source_files = entry.get("source_files") or []
    session.replace(
        data,
        entry.get("ocr_texts") or [],
        source_files,
        saved_previews=previews,
    )
    current_version = session.get_version()

    # 恢复该案例的诊断/互动进度（session.replace 已清空当前表，按快照写回）
    diag_snap = entry.get("diagnosis_snapshot")
    inter_snap = entry.get("interaction_snapshot")
    if isinstance(diag_snap, dict) and diag_snap.get("findings"):
        try:
            workspace_db.save_diagnosis(
                session_version=current_version,
                company_name=str(diag_snap.get("company_name") or data.company_name),
                industry=str(diag_snap.get("industry") or data.industry),
                industry_fallback=bool(diag_snap.get("industry_fallback")),
                vat_estimate_note=str(diag_snap.get("vat_estimate_note") or ""),
                ai_used=bool(diag_snap.get("ai_used")),
                findings=diag_snap.get("findings") or [],
            )
        except Exception:
            logger.warning("载入历史时恢复诊断进度失败（忽略）", exc_info=True)
    if isinstance(inter_snap, dict) and inter_snap.get("state"):
        try:
            workspace_db.save_interaction_session(
                session_version=current_version,
                state=str(inter_snap.get("state") or ""),
                decisions=inter_snap.get("decisions") or [],
                strategy_notes=inter_snap.get("strategy_notes") or [],
                draft2=inter_snap.get("draft2") or [],
                feasibility_score=float(inter_snap.get("feasibility_score") or 100.0),
                feasibility_breakdown=inter_snap.get("feasibility_breakdown") or [],
                ai_fallback_message=str(inter_snap.get("ai_fallback_message") or ""),
                user_confirmed=bool(inter_snap.get("user_confirmed")),
            )
        except Exception:
            logger.warning("载入历史时恢复互动进度失败（忽略）", exc_info=True)

    # 激活该历史行并同步快照（与恢复后的当前进度一致）
    workspace_db.set_active_import_history(entry_id)
    workspace_db.snapshot_case_progress()

    # 进度标志（与前端全局步骤状态、core.interactive 解锁口径一致：
    # 解锁 = FINAL 或 CONFIRMATION 且（用户已确认 或 落地性 ≥ 阈值））
    diag_row = workspace_db.load_diagnosis()
    inter_row = workspace_db.load_interaction_session()
    state = str((inter_row or {}).get("state") or "")
    user_confirmed = bool((inter_row or {}).get("user_confirmed"))
    feasibility = float((inter_row or {}).get("feasibility_score") or 0.0)
    diagnosis_done = bool(
        diag_row
        and diag_row.get("findings")
        and diag_row.get("session_version") == current_version
    )
    interaction_done = state in ("DRAFT2", "CONFIRMATION", "FINAL")
    export_unlocked = state == "FINAL" or (
        state == "CONFIRMATION"
        and (user_confirmed or feasibility >= FEASIBILITY_THRESHOLD)
    )
    report_id = workspace_db.find_report_for_version(current_version)

    indicators = [_indicator_row(data, yr) for yr in (data.years or [])]
    meta = data.parsed_meta or {}
    expense_pack = meta.get("expense_anomalies") or {}
    return {
        "summary": session.summary(),
        "indicators": indicators,
        "years": list(data.years or []),
        "previews": previews,
        "sources": source_files,
        "data_quality": session.get_data_quality(),
        "policy": session.get_policy(),
        "numeric_audit": session.get_numeric_audit(),
        "reconciliation": meta.get("reconciliation") or {},
        "cit_synthesis": meta.get("cit_synthesis") or {},
        "expense_anomalies": expense_pack.get("anomalies") or [],
        "case_id": None,
        "message": f"已载入导入记录：{entry.get('company_name') or '未知企业'}",
        "restored": {
            "diagnosis_done": diagnosis_done,
            "interaction_done": interaction_done,
            "export_unlocked": export_unlocked,
            "report_id": report_id,
        },
    }


@router.delete("/import/history/{entry_id}")
def remove_import_history(entry_id: int) -> dict:
    """删除一条导入历史记录。"""
    if not workspace_db.delete_import_history(entry_id):
        raise HTTPException(status_code=404, detail="导入记录不存在或已删除")
    return {"deleted": entry_id}


def _safe_preview(path: str) -> dict:
    """提取文件预览；失败时降级返回文件名与错误说明（不阻塞导入）。"""
    try:
        return parser_mod.preview_file(path)
    except Exception as e:
        return {
            "name": os.path.basename(path),
            "kind": "error",
            "sections": [],
            "notes": [f"预览失败：{e}"],
        }


def _classify_filename(stem: str) -> str:
    """按文件名关键字识别报表类型；无法识别返回 'unknown'。"""
    income_kw = ["利润", "损益", "收入"]
    balance_kw = ["资产", "负债", "余额", "资产负债"]
    ledger_kw = ["科目", "余额表", "总账"]
    lower = stem.lower()
    if any(k in lower for k in ledger_kw):
        return "ledger"
    if any(k in lower for k in income_kw):
        return "income"
    if any(k in lower for k in balance_kw):
        return "balance"
    return "unknown"
