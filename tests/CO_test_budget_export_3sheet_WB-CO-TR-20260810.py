"""三 Sheet 费用预算模板导出：结构与用户标准模板一致。"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest
from openpyxl import load_workbook

from core import parser as pr
from data import make_sample

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

budget_export = importlib.import_module("core.CO_budget_export_WB-CO-TR-20260810")
REF = Path("/Users/bruceleeu/Downloads/budget_3sheet_WB-CO-TR-20260726.xlsx")
if not REF.exists():
    REF = Path(ROOT) / "demo_output" / "budget_3sheet_WB-CO-TR-20260726.xlsx"


@pytest.fixture
def sample_data():
    return pr.parse_financial_dict(make_sample.build_sample_data())


def test_export_budget_3sheet_matches_template_labels(sample_data, tmp_path):
    out = tmp_path / "out.xlsx"
    path, meta = budget_export.export_budget_3sheet(sample_data, str(out))
    assert Path(path).exists()
    assert meta["sheets"] == [
        "费用预算表",
        "行业企业所得税贡献率参考",
        "诊断与行动清单",
    ]

    wb = load_workbook(path)
    ref = load_workbook(str(REF))
    assert wb.sheetnames == ref.sheetnames == meta["sheets"]

    ws, rs = wb["费用预算表"], ref["费用预算表"]
    for coord in ("A1", "A2", "A3", "A4", "A5", "A6", "A12", "A13", "D13", "G13", "I13", "J13"):
        assert ws[coord].value == rs[coord].value, coord
    # 公式列保留
    assert str(ws["C4"].value).startswith("=")
    assert str(ws["E14"].value).startswith("=")
    assert str(ws["J14"].value).startswith("=")
    # 顶部有填入
    assert float(ws["C2"].value) > 0


def test_export_api_budget_async(sample_data, tmp_path, monkeypatch):
    """异步任务：mock DeepSeek 提取后应 completed 并可下载。"""
    import time
    app_mod = importlib.import_module("web_backend.CO_app_WB-CO-TR-20260805160732")
    session = importlib.import_module("web_backend.CO_session_WB-CO-TR-20260805160732")
    job_mod = importlib.import_module("web_backend.CO_budget_export_job_WB-CO-TR-20260810")
    ai_mod = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")
    from fastapi.testclient import TestClient

    out = tmp_path / "exports"
    out.mkdir()
    monkeypatch.setattr(job_mod, "_EXPORT_DIR", out)

    def fake_top(ocr, structured_hints=None):
        h = structured_hints or {}
        return {
            "budget_revenue": float(h.get("budget_revenue") or 1),
            "budget_cost": float(h.get("budget_cost") or 1),
            "last_year_revenue": float(h.get("last_year_revenue") or 1),
            "last_year_cost": float(h.get("last_year_cost") or 1),
            "_period": {},
        }, ""

    def fake_lines(ocr, catalog, structured_facts=None, period_totals=None):
        return (
            [
                {
                    "row": 48,
                    "last_year_actual": 1000,
                    "budget_amount": 1000,
                    "actual_amount": 500,
                }
            ],
            "",
        )

    monkeypatch.setattr(ai_mod, "extract_budget_indicators", fake_top)
    monkeypatch.setattr(ai_mod, "extract_budget_expense_lines", fake_lines)

    client = TestClient(app_mod.create_app())
    session.replace(sample_data, ["OCR 利润表 营业收入"], [])
    r = client.post("/api/export/budget/jobs")
    assert r.status_code == 200, r.text
    jid = r.json()["job_id"]
    job = None
    for _ in range(80):
        job = client.get(f"/api/export/budget/jobs/{jid}").json()
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)
    assert job and job["status"] == "completed", job
    d = client.get(f"/api/export/budget/jobs/{jid}/download")
    assert d.status_code == 200
    assert len(d.content) > 3000
    client.close()
