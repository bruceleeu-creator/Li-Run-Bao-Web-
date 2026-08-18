"""利润宝 · 三份真实审计报告 PDF 的扫描件识别与导入路径测试。

覆盖：
- 文本型预览：三份真实 PDF 不再渲染图片，返回 pdf_type 与 OCR 采样文本
- 未配置 AI 时导入扫描件报错文案引导配置 AI
- 配置 AI 时扫描件走 DeepSeek 解析路径（monkeypatch，不真触网）

三份 PDF 使用项目 `web_backend/workspaces/` 内的正式本地样本。
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORTS = REPO_ROOT / "web_backend" / "workspaces"
PDF_PATHS = {
    2022: str(REPORTS / "2022年审计报告.pdf"),
    2023: str(REPORTS / "2023年审计报告.pdf"),
    2024: str(REPORTS / "2024年审计报告.pdf"),
}


def test_real_pdf_fixtures_are_project_portable():
    """真实 PDF 固件必须随项目可发现，不能依赖某个用户的绝对目录。"""
    for path_string in PDF_PATHS.values():
        path = Path(path_string).resolve()
        assert path.is_relative_to(REPO_ROOT)
        assert path.is_file()

needs_pdfs = pytest.mark.skipif(
    any(not Path(path).exists() for path in PDF_PATHS.values()),
    reason="三份真实审计报告 PDF 缺失",
)


def test_looks_garbled_detects_known_garbled_layers():
    """子集化字体/生僻字形乱码层必须被识别，不能直接喂给 AI。"""
    from core import parser as p

    assert p._looks_garbled("中庆审⒛22O39号 CertiⅡed 灬")
    assert p._looks_garbled(
        "嘿 剞 羽 斟 释 〓 寮 濠 巛 ㈧ 00000^0∽ ^`α 寸 ∽ 卜 一∽ 0^∽ 0^o寸 "
        "Φ ∽ 0 〓 00^0寸 ^冖 Φ 寸 ∽ ∽ ∽ 〓 ∞ ^∽ ∽ ∞ ∞ ㈧ 寸 ∞ ㈧ .冖 ∽ ∽"
    )
    assert p._looks_garbled(
        "呔 剞 羽 腓 铎 〓 姣 铷 怔 刈 登 t 姐 ˇ 寸 Θ ° 卜 0〓 d.ˇ ^ˇ 对 寸 ˇ "
        "一 卜 ㈧ 0〓 .0∞ 〓 ∽ ∞ 一 〓 d卜 .∞ ^冖 ∞ ∞ ㈧"
    )


def test_looks_garbled_accepts_dense_financial_tables():
    """正常财报数字密集页（数字多、字符重复）不得误判为乱码。"""
    from core import parser as p

    assert not p._looks_garbled(
        "现金流量表 编制单位：云南 金额单位：元 本年累计金额 上年累计金额 "
        "一、经营活动产生 销售产成品、商品、 258,164,18561 366,778,41237 "
        "支付的各项税费 支付给职工以及为职工支付的现金 178,938,420.37 "
        "经营活动产生的现金流量净额 18,530,966.08"
    )
    assert not p._looks_garbled(
        "云南艺康装饰工程有限公司 财务报表附注 2022年度报告 金额单位为人民币元 "
        "无形资产 处置 损失 经营活动产生的现金流量净额 1,086,530.00 "
        "项目 本年金额 上年金额 利息支出 财务费用"
    )


app_mod = importlib.import_module("web_backend.CO_app_WB-CO-TR-20260805160732")
session_mod = importlib.import_module("web_backend.CO_session_WB-CO-TR-20260805160732")

CLIENT = TestClient(app_mod.create_app())


@pytest.fixture(autouse=True)
def _reset():
    session_mod.clear()
    # 确保未配置 AI（Key 仅内存，测试内不落盘）
    ai_mod = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")
    ai_mod.clear_config()
    yield
    session_mod.clear()


def _open(y: int) -> tuple[str, bytes]:
    with open(PDF_PATHS[y], "rb") as fh:
        return (f"{y}年审计报告.pdf", fh.read(), "application/pdf")


@needs_pdfs
@pytest.mark.real_pdf
def test_preview_pdf_is_text_not_image():
    """三份真实 PDF 预览不再渲染图片，输出 pdf_type 与 OCR 采样。"""
    from core import parser as p

    for y, path in PDF_PATHS.items():
        r = p.preview_file(path)
        assert r["kind"] == "pdf"
        assert r["images"] == [], f"{y} 预览不应含图片"
        assert "pdf_type" in r, f"{y} 应标注 pdf_type"
        assert r["notes"], f"{y} 预览应含文本/OCR 采样"
        assert r["notes"][0].startswith("共 "), f"{y} 首条 note 应为页数说明"
        assert any("第 " in n for n in r["notes"])


@needs_pdfs
@pytest.mark.real_pdf
def test_scan_pdfs_are_classified_scan():
    """2023/2024 为扫描件（无文本层）；2022 有文本层（乱码）。"""
    from core import parser as p

    assert p.preview_file(PDF_PATHS[2023])["pdf_type"] == "scan"
    assert p.preview_file(PDF_PATHS[2024])["pdf_type"] == "scan"
    assert p.preview_file(PDF_PATHS[2022])["pdf_type"] in ("text", "mixed")


@needs_pdfs
@pytest.mark.real_pdf
def test_preview_2022_garbled_page_4_falls_back_to_ocr():
    """2022 第 4 页文本层乱码：预览必须回退 OCR，不得把乱码字形喂给 AI。"""
    from core import parser as p

    r = p.preview_file(PDF_PATHS[2022])
    page4 = next((n for n in r["notes"] if n.startswith("[第 4 页")), None)
    assert page4 is not None, "2022 预览应包含第 4 页"
    assert "OCR" in page4, "第 4 页乱码文本层应回退 OCR"
    assert "00000^0" not in page4, "第 4 页不得再携带乱码字形"


@needs_pdfs
@pytest.mark.real_pdf
def test_import_scans_without_ai_fails_with_guidance():
    """未配置 AI 时导入三份扫描件，报错文案须引导配置 AI（不静默失败）。"""
    files = [("files", _open(y)) for y in (2022, 2023, 2024)]
    r = CLIENT.post("/api/import", files=files, data={"company_name": "", "industry": "制造业"})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "配置 AI" in detail
    assert "扫描件" in detail


@needs_pdfs
@pytest.mark.real_pdf
def test_parse_one_routes_scans_to_deepseek(monkeypatch):
    """配置 AI 后，_parse_one 对扫描件调用 DeepSeek 解析（monkeypatch 不真触网）。"""
    import_mod = importlib.import_module("web_backend.CO_import_WB-CO-TR-20260805160732")
    ai_mod = importlib.import_module("web_backend.CO_ai_WB-CO-TR-20260805160732")

    calls: list[str] = []

    monkeypatch.setattr(
        ai_mod,
        "get_credentials",
        lambda: {"base_url": "https://x", "model": "m", "api_key": "k"},
    )

    from core.models import FinancialData

    def fake_parse(path, **kw):
        calls.append(path)
        return FinancialData(
            company_name="云南艺康装饰工程有限公司",
            industry="建筑业",
            years=[2024],
            income_statement={"营业收入": {2024: 283347223.63}},
            balance_sheet={"资产总额": {2024: 186968496.25}},
            account_balances={},
            parsed_meta={"source": "deepseek", "warnings": []},
        )

    ds_mod = importlib.import_module("core.CO_deepseek_parse_WB-CO-TR-20260806140818")
    monkeypatch.setattr(ds_mod, "parse_pdf_with_deepseek", fake_parse)

    data = import_mod._parse_one(PDF_PATHS[2024], "", "制造业")
    assert calls, "扫描件应调用 DeepSeek 解析"
    assert data.years == [2024]
    assert data.income_statement["营业收入"] == {2024: 283347223.63}
