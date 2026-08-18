"""经营预算分析（前世今生）：事实清单 / 合并 / 数字白名单 / API 链路。"""

from __future__ import annotations

import importlib
import os
import sys

import pytest
from fastapi.testclient import TestClient

from core import diagnostic as diag
from core import interactive as iv
from core import parser as pr
from core.narrative import build_stage_narrative
from data import make_sample

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

report_analysis = importlib.import_module("core.CO_report_analysis_WB-CO-TR-20260818")


@pytest.fixture
def sample_sess():
    data = pr.parse_financial_dict(make_sample.build_sample_data())
    diagnosis = diag.diagnose(data)
    sess = iv.start_session(data, diagnosis)
    while sess.state == iv.STATE_FINDING_LOOP and sess.current_finding:
        f = sess.current_finding
        iv.submit_decision(sess, f.id, "A")
    if sess.state == iv.STATE_CONFIRMATION:
        iv.confirm(sess, user_confirmed=True)
    return sess


def test_factsheet_structure(sample_sess):
    facts = report_analysis.build_analysis_factsheet(
        sample_sess.data, sample_sess.diagnosis, sample_sess.decisions
    )
    assert facts["company_name"]
    assert facts["years"]
    assert facts["stages"], "阶段故事（前世今生）必备"
    assert facts["year_rows"]
    assert facts["now_points"]


def test_merge_overrides_text_only(sample_sess):
    facts = report_analysis.build_analysis_factsheet(
        sample_sess.data, sample_sess.diagnosis, sample_sess.decisions
    )
    base = build_stage_narrative(sample_sess.data, sample_sess.diagnosis, sample_sess.decisions)
    stage_title = facts["stages"][0]["title"]
    point_title = facts["now_points"][0]["title"]
    metric_name = next(iter(facts["metric_judgments"]))
    analysis = {
        "one_liner": "一句话结论（DeepSeek）",
        "headline": "先说结论改写。",
        "stage_insight": "阶段启发改写",
        "stages": [
            {"title": stage_title, "summary": "阶段改写", "bullets": ["要点1", "要点2"]}
        ],
        "now_points": [{"title": point_title, "body": "现在要点改写"}],
        "now_judgments": {metric_name: "管理判断改写"},
        "future_actions": ["将来动作1", "将来动作2"],
    }
    merged = report_analysis.merge_narrative(base, analysis)

    assert merged.one_liner == "一句话结论（DeepSeek）"
    assert merged.headline == "先说结论改写。"
    # 文本字段按键覆盖
    assert any(s.summary == "阶段改写" for s in merged.stages)
    assert any(p.body == "现在要点改写" for p in merged.now_points)
    assert any(m.judgment == "管理判断改写" for m in merged.now_metrics)
    assert merged.future_actions == ["将来动作1", "将来动作2"]
    # 结构与确定性数字不动：阶段数量/跨年表/指标当前情况保持
    assert len(merged.stages) == len(base.stages)
    assert len(merged.year_rows) == len(base.year_rows)
    assert [m.value_text for m in merged.now_metrics] == [m.value_text for m in base.now_metrics]
    # 未匹配键的阶段保留规则文案
    for b, m in zip(base.stages, merged.stages):
        if m.summary != "阶段改写":
            assert m.summary == b.summary


def test_number_validation_flags_unknown(sample_sess):
    facts = report_analysis.build_analysis_factsheet(
        sample_sess.data, sample_sess.diagnosis, sample_sess.decisions
    )
    # 用事实清单里的真实数字 → 不告警
    ok_text = facts["stages"][0]["summary"] + " " + facts["year_rows"][0]["one_liner"]
    clean = {
        "one_liner": ok_text,
        "headline": ok_text,
        "stages": [{"title": facts["stages"][0]["title"], "summary": ok_text, "bullets": []}],
        "now_points": [],
        "future_actions": [],
        "now_judgments": {},
    }
    assert report_analysis.validate_analysis_numbers(clean, facts) == []

    # 编造数字（事实清单外的大数）→ 告警且不改写原文
    bad = dict(clean)
    bad["headline"] = "公司营收高达 98765432.11 万元，净利率 77.77%。"
    warnings = report_analysis.validate_analysis_numbers(bad, facts)
    assert warnings, "事实清单外数字应触发告警"
    assert "98765432.11" in warnings[0] or "77.77" in warnings[0]


def test_normalize_and_has_content():
    payload = report_analysis.normalize_analysis_payload(
        {
            "one_liner": " x ",
            "headline": "结论",
            "stages": [{"title": "现在", "summary": "s", "bullets": ["b"]}],
            "now_points": [{"title": "t", "body": "b"}],
            "now_judgments": {"毛利率": "偏高"},
            "future_actions": ["a1"],
            "summary": "总述",
        }
    )
    assert payload["one_liner"] == "x"
    assert report_analysis.has_analysis_content(payload)
    assert not report_analysis.has_analysis_content(
        report_analysis.normalize_analysis_payload({"stages": [], "future_actions": []})
    )


def test_analysis_endpoint_requires_ai(sample_sess, monkeypatch):
    """未配置 DeepSeek：POST /api/export/analysis 返回 503（不静默降级）。"""
    app_mod = importlib.import_module("web_backend.CO_app_WB-CO-TR-20260805160732")
    session = importlib.import_module("web_backend.CO_session_WB-CO-TR-20260805160732")
    interaction = importlib.import_module(
        "web_backend.CO_interaction_WB-CO-TR-20260805160732"
    )
    export_mod = importlib.import_module("web_backend.CO_export_WB-CO-TR-20260810")

    session.replace(
        sample_sess.data,
        ocr_texts=[],
        source_files=[],
        saved_previews=[],
    )
    with interaction._lock:
        interaction._sess = sample_sess
        interaction._sess_session_version = session.get_version()

    client = TestClient(app_mod.create_app())
    # 强制未配置 AI
    monkeypatch.setattr(export_mod.ai_mod, "_engine", lambda timeout=8.0: None)
    r = client.post("/api/export/analysis")
    assert r.status_code == 503
    client.close()


def test_word_export_with_merged_narrative(sample_sess, tmp_path):
    """合并叙事注入 Word：模板结构保留 + DeepSeek 文案出现。"""
    from core import report as report_mod

    base = build_stage_narrative(sample_sess.data, sample_sess.diagnosis, sample_sess.decisions)
    merged = report_analysis.merge_narrative(
        base,
        {"one_liner": "【DS】一句话结论", "headline": "【DS】先说结论"},
    )
    path = tmp_path / "merged.docx"
    report_mod.export_word(sample_sess, str(path), narrative=merged)
    assert path.exists() and path.stat().st_size > 2000

    from docx import Document

    doc = Document(str(path))
    joined = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    assert "【DS】先说结论" in joined
    assert "一、先说结论" in joined  # 原模板结构保留
    assert "八、落地清单" in joined
