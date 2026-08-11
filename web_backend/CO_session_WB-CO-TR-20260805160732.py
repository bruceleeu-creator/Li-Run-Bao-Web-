"""利润宝 · Web 会话状态（内存 + SQLite 持久化）。

管理当前导入的 FinancialData 与 OCR 文本。写入时同步持久化到 SQLite
（web_backend/workspaces/app.db），服务重启/前端刷新后可从数据库恢复，
根治「切换工作区/刷新后数据丢失」问题。
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
import threading
from typing import Optional

from core import parser as parser_mod
from core.models import FinancialData

# DB 模块文件名含智能体标识连字符，须用 importlib 加载
db = importlib.import_module("web_backend.CO_db_WB-CO-TR-20260805160732")

_lock = threading.Lock()
_data: Optional[FinancialData] = None
_ocr_texts: list[str] = []  # 导入时各文件的 OCR 文本（供 AI 识别指标）
_source_files: list[dict] = []
_saved_previews: list[dict] = []  # 最近一次导入时保存的文件预览（切页/刷新后仍可查看）
_session_version = ""
_generation = 0


def _to_dict(data: FinancialData) -> dict:
    """FinancialData → 可 JSON 序列化 dict（整数键转字符串）。"""
    raw = dataclasses.asdict(data)
    return json.loads(json.dumps(raw, ensure_ascii=False, default=str))


def _version_for(data: FinancialData, source_files: list[dict]) -> str:
    """对完整冻结输入求稳定摘要，任何事实或来源元数据变化都会换版本。"""
    canonical = {
        "financial_data": _to_dict(data),
        "sources": sorted(
            [
                {
                    "path": str(source.get("path") or ""),
                    "name": str(source.get("name") or source.get("source_file") or ""),
                    "sha256": str(source.get("sha256") or ""),
                    "size": int(source.get("size") or 0),
                    "report_year": int(source.get("report_year") or 0),
                    "page_count": int(source.get("page_count") or 0),
                }
                for source in source_files
                if isinstance(source, dict)
            ],
            key=lambda source: (
                source["report_year"], source["name"], source["path"], source["sha256"]
            ),
        ),
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _persist() -> None:
    """把当前内存态写入 SQLite。"""
    with _lock:
        if _data is None:
            db.clear_session_db()
            return
        session_data = {
            "company_name": _data.company_name,
            "industry": _data.industry,
            "years": _data.years,
            "indicators": [],
            "data_json": _to_dict(_data),
        }
        db.save_session(
            session_data,
            _ocr_texts,
            _source_files,
            _saved_previews,
            _session_version,
        )


def set_data(data: FinancialData) -> None:
    """写入当前会话数据（线程安全），并持久化到 SQLite。"""
    global _data, _session_version, _generation
    with _lock:
        _data = data
        _session_version = _version_for(data, _source_files)
        _generation += 1
    _persist()


def set_ocr_texts(texts: list[str]) -> None:
    """保存导入文件的 OCR 文本片段，并持久化。"""
    global _ocr_texts, _generation
    with _lock:
        _ocr_texts = [t for t in texts if t.strip()]
        _generation += 1
    _persist()


def set_source_files(files: list[dict]) -> None:
    """保存当前导入源文件的可追溯快照，并同步写入持久化会话。"""
    global _source_files, _session_version, _generation
    with _lock:
        _source_files = [dict(item) for item in files]
        _session_version = _version_for(_data, _source_files) if _data else ""
        _generation += 1
    _persist()


def get_source_files() -> list[dict]:
    with _lock:
        return [dict(item) for item in _source_files]


def get_saved_previews() -> list[dict]:
    with _lock:
        return [dict(item) for item in _saved_previews]


def get_version() -> str:
    with _lock:
        return _session_version


def replace(
    data: FinancialData,
    ocr_texts: list[str],
    source_files: list[dict],
    saved_previews: list | None = None,
) -> None:
    """替换单一当前会话，并持久化一份完整一致的快照。"""
    global _data, _ocr_texts, _source_files, _saved_previews, _session_version, _generation
    next_ocr_texts = [text for text in ocr_texts if text.strip()]
    next_source_files = [dict(item) for item in source_files]
    next_saved_previews = [dict(item) for item in saved_previews or []]
    next_version = _version_for(data, next_source_files)
    session_data = {
        "company_name": data.company_name,
        "industry": data.industry,
        "years": data.years,
        "indicators": [],
        "data_json": _to_dict(data),
    }
    with _lock:
        db.save_session(
            session_data,
            next_ocr_texts,
            next_source_files,
            next_saved_previews,
            next_version,
        )
        # 会话替换：旧诊断与互动会话一律失效，防止旧结果被新会话复用
        db.clear_diagnosis_db()
        db.clear_interaction_db()
        _data = data
        _ocr_texts = next_ocr_texts
        _source_files = next_source_files
        _saved_previews = next_saved_previews
        _session_version = next_version
        _generation += 1


def get_ocr_texts() -> list[str]:
    with _lock:
        return list(_ocr_texts)


def get_data() -> Optional[FinancialData]:
    with _lock:
        return _data


def get_policy() -> dict:
    """当前会话 PolicySnapshot dict（pipeline 写入 parsed_meta.policy）。"""
    with _lock:
        data = _data
    if data is None:
        return {}
    raw = (data.parsed_meta or {}).get("policy")
    return dict(raw) if isinstance(raw, dict) else {}


def get_data_quality() -> dict:
    """当前会话 data_quality。"""
    with _lock:
        data = _data
    if data is None:
        return {}
    raw = (data.parsed_meta or {}).get("data_quality")
    return dict(raw) if isinstance(raw, dict) else {}


def clear() -> None:
    """DB 提交成功后才清空内存，确保发布顺序与 replace 一致。"""
    global _data, _ocr_texts, _source_files, _saved_previews, _session_version, _generation
    with _lock:
        db.clear_session_db()
        db.clear_diagnosis_db()
        db.clear_interaction_db()
        _data = None
        _ocr_texts = []
        _source_files = []
        _saved_previews = []
        _session_version = ""
        _generation += 1


def has_data() -> bool:
    with _lock:
        return _data is not None


def restore_from_db() -> None:
    """从 SQLite 恢复会话（若内存为空且数据库有数据）。"""
    global _data, _ocr_texts, _source_files, _saved_previews, _session_version, _generation
    with _lock:
        if _data is not None:
            return
        restore_generation = _generation
        stored = db.load_session()
    if stored is None:
        return
    try:
        raw = stored["data_json"]
        data = parser_mod.parse_financial_dict(raw)
        data.company_name = stored.get("company_name") or data.company_name
        data.industry = stored.get("industry") or data.industry
        with _lock:
            if _data is not None or _generation != restore_generation:
                return
            _data = data
            _ocr_texts = stored.get("ocr_texts") or []
            _source_files = stored.get("source_files") or []
            _saved_previews = stored.get("saved_previews") or []
            _session_version = stored.get("session_version") or _version_for(data, _source_files)
            _generation += 1
    except Exception:
        # 数据库内容损坏时不阻塞启动
        with _lock:
            if _data is not None or _generation != restore_generation:
                return
            _data = None
            _ocr_texts = []
            _source_files = []
            _saved_previews = []
            _session_version = ""
            _generation += 1


def summary() -> Optional[dict]:
    """返回会话摘要（无数据返回 None）。金额单位：元。"""
    with _lock:
        data = _data
    if data is None:
        return None
    meta = data.parsed_meta or {}
    matched = meta.get("matched", 0) or 0
    # DeepSeek/合并路径若未写 matched，按已解析科目数回退，避免界面显示 0 误导
    if not matched:
        n = 0
        for table in (
            getattr(data, "income_statement", None) or {},
            getattr(data, "balance_sheet", None) or {},
        ):
            for _acc, yv in table.items():
                if yv:
                    n += 1
        matched = n
    return {
        "company_name": data.company_name,
        "industry": data.industry,
        "years": data.years,
        "matched": matched,
        "unmatched": list(meta.get("unmatched", [])),
        "warnings": list(meta.get("warnings", [])),
        "latest_year": data.latest_year(),
    }
