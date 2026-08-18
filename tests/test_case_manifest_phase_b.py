"""Phase B：CaseManifest 注册与解析（无硬编码公司逻辑）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import case_manifest as cm

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "demo_output" / "cases"


def test_discover_yikang_manifest():
    ms = cm.load_all_manifests(CASES)
    ids = {m.id for m in ms}
    assert "audit_yikang_3y" in ids
    m = cm.get_manifest("audit_yikang_3y", CASES)
    assert m.company_name.startswith("云南艺康")
    assert m.industry == "建筑业"
    assert len(m.files) == 3


def test_alias_audit_3years_still_resolves():
    m = cm.get_manifest("audit_3years", CASES)
    assert m.id == "audit_yikang_3y"
    m2 = cm.get_manifest("yikang", CASES)
    assert m2.id == "audit_yikang_3y"


def test_resolve_files_from_test_dir():
    """PDF 在项目旁「测试文件」时仍可解析。"""
    m = cm.get_manifest("audit_yikang_3y", CASES)
    try:
        paths = cm.resolve_case_files(m)
    except cm.CaseManifestError as e:
        pytest.skip(f"测试 PDF 不在搜索路径：{e}")
    assert len(paths) == 3
    assert all(p.is_file() for p in paths)
    assert all(p.suffix.lower() == ".pdf" for p in paths)


def test_list_cases_public_shape():
    rows = cm.list_cases_public(CASES)
    assert any(r["id"] == "audit_yikang_3y" for r in rows)
    yk = next(r for r in rows if r["id"] == "audit_yikang_3y")
    assert "available" in yk
    assert "files" in yk
    assert "aliases" in yk
    assert "audit_3years" in yk["aliases"]


def test_unknown_case_raises():
    with pytest.raises(cm.CaseManifestError, match="未知案例"):
        cm.get_manifest("no_such_case_xyz", CASES)


def test_manifest_tax_default():
    m = cm.get_manifest("audit_yikang_3y", CASES)
    assert m.income_tax_nominal_rate == 0.15


def test_gold_path():
    m = cm.get_manifest("audit_yikang_3y", CASES)
    gp = cm.gold_path(m)
    assert gp is not None and gp.is_file()
    gold = json.loads(gp.read_text(encoding="utf-8"))
    assert "by_year" in gold or "company_name" in gold


def test_second_case_can_be_registered(tmp_path: Path):
    """证明新增案例只需目录 + manifest，无需改代码。"""
    root = tmp_path / "cases"
    d = root / "demo_factory_2y"
    d.mkdir(parents=True)
    # 假文件
    for name in ("a.xlsx", "b.xlsx"):
        (d / name).write_bytes(b"PK\x03\x04fake")
    (d / "manifest.json").write_text(
        json.dumps(
            {
                "id": "demo_factory_2y",
                "label": "演示工厂两年",
                "company_name": "演示工厂有限公司",
                "industry": "制造业",
                "files": ["a.xlsx", "b.xlsx"],
                "defaults": {"income_tax_nominal_rate": 0.25},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    m = cm.get_manifest("demo_factory_2y", root)
    paths = cm.resolve_case_files(m)
    assert len(paths) == 2
    assert m.income_tax_nominal_rate == 0.25
    public = cm.list_cases_public(root)
    assert len(public) == 1
    assert public[0]["available"] is True
