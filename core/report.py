"""利润宝 · Word/PDF 报告生成（S6）。

结构：封面 → 综合分析（指标表）→ 诊断发现 → 第二稿（决策表）→ 完整方案
（预计节税汇总）→ 合规声明 → 附录（增值税估算口径说明）。

- Word 用 python-docx；PDF 用 ReportLab，注册 CJK 字体避免中文乱码。
- 图表用 Matplotlib，自动探测中文字体（macOS PingFang SC / Linux Noto）。
- 导出失败抛 ReportError，调用方应保留内存方案并提示用户。
- 所有建议限于合法税务筹划；增值税税负率显著标注估算口径。
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import List, Optional

from . import finance as fin
from . import industry as ind_mod
from .diagnostic import COMPLIANCE_NOTE, DiagnosisResult, Finding
from .interactive import Draft2Entry, Session


class ReportError(Exception):
    """报告生成错误。"""


# ── 中文字体探测（Matplotlib） ────────────────────────────────────────────
_CJK_FONT_CANDIDATES = [
    "PingFang SC", "Heiti SC", "STHeiti", "SimHei", "Microsoft YaHei",
    "Noto Sans CJK SC", "Noto Sans SC", "WenQuanYi Zen Hei", "Arial Unicode MS",
]
_resolved_font: Optional[str] = None


def _resolve_cjk_font() -> Optional[str]:
    """探测系统中文字体，返回可用字体名；无则返回 None。"""
    global _resolved_font
    if _resolved_font is not None:
        return _resolved_font
    try:
        import matplotlib
        matplotlib.use("Agg")  # 非交互后端
        from matplotlib import font_manager
        available = {f.name for f in font_manager.fontManager.ttflist}
        for name in _CJK_FONT_CANDIDATES:
            if name in available:
                _resolved_font = name
                return name
    except Exception:
        pass
    _resolved_font = ""  # 标记已尝试，避免重复探测
    return None or _resolved_font or None


def _setup_matplotlib_font():
    """配置 Matplotlib 中文字体；缺失时给出明确日志而非崩溃。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        font = _resolve_cjk_font()
        if font:
            plt.rcParams["font.sans-serif"] = [font, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
        return font
    except Exception:
        return None


def _fmt_money(v: float) -> str:
    return f"{v:,.0f} 元"


def _fmt_pct(v: float) -> str:
    return f"{v:.2f}%"


# ── 报告内容构建 ──────────────────────────────────────────────────────────

def _build_indicator_rows(sess: Session) -> List[List[str]]:
    """构造各年指标表行（年份 / 营收 / 增值税税负率 / 所得税税负率 / 毛利率 / 净利率）。"""
    rows: List[List[str]] = []
    for yr in sess.data.years:
        ind = fin.compute_year_indicators(sess.data, yr)
        vat = ind["增值税税负率"]
        rows.append([
            str(yr),
            _fmt_money(ind.get("营业收入", 0.0) or 0.0),
            f"{vat['value']}%（{vat['note']}）",
            _fmt_pct(ind["所得税税负率"]["value"]),
            _fmt_pct(ind["毛利率"]["value"]),
            _fmt_pct(ind["净利率"]["value"]),
        ])
    return rows


def _render_chart_to_png(sess: Session, tmp_dir: str) -> Optional[str]:
    """渲染营收与增值税税负率双轴图；返回 PNG 路径，失败返回 None。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        _setup_matplotlib_font()
        years = list(sess.data.years)
        if not years:
            return None
        revenues = []
        vat_rates = []
        for yr in years:
            ind = fin.compute_year_indicators(sess.data, yr)
            revenues.append(ind.get("营业收入", 0.0) or 0.0)
            vat_rates.append(ind["增值税税负率"]["value"])
        fig, ax1 = plt.subplots(figsize=(6, 3.2), dpi=120)
        color1 = "#2563eb"
        ax1.bar([str(y) for y in years], revenues, color=color1, alpha=0.7, label="营业收入")
        ax1.set_ylabel("营业收入（元）", color=color1)
        ax1.tick_params(axis="y", labelcolor=color1)
        ax2 = ax1.twinx()
        color2 = "#dc2626"
        ax2.plot([str(y) for y in years], vat_rates, color=color2, marker="o", linewidth=2, label="增值税税负率")
        ax2.set_ylabel("增值税税负率（%）", color=color2)
        ax2.tick_params(axis="y", labelcolor=color2)
        # 行业下限参考线
        benchmark, _ = ind_mod.get_benchmark(sess.data.industry)
        vat_min = benchmark["vat_tax_rate"]["min"]
        ax2.axhline(y=vat_min, color=color2, linestyle="--", alpha=0.4, label=f"行业下限 {vat_min:.1f}%")
        plt.title("营业收入与增值税税负率趋势（税负率为估算口径）")
        fig.tight_layout()
        path = os.path.join(tmp_dir, "trend.png")
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        return path
    except Exception:
        return None


# ── Word 报告 ─────────────────────────────────────────────────────────────

def export_word(sess: Session, path: str, ai_fallback: str = "") -> str:
    """生成 Word 报告（.docx）。返回路径；失败抛 ReportError。"""
    try:
        from docx import Document
        from docx.shared import Pt, Cm, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH
    except ImportError as e:
        raise ReportError("未安装 python-docx，无法生成 Word 报告。") from e

    try:
        doc = Document()
        # 设置默认中文字体
        style = doc.styles["Normal"]
        style.font.name = "Songti SC"
        style.font.size = Pt(10.5)
        try:
            from docx.oxml.ns import qn
            style.element.rPr.rFonts.set(qn("w:eastAsia"), "Songti SC")
        except Exception:
            pass

        # 封面
        title = doc.add_heading("利润宝 · 企业财税优化方案", level=0)
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run(f"企业：{sess.data.company_name}\n行业：{sess.data.industry}\n期间：{sess.data.years}\n生成日期：{datetime.now().strftime('%Y-%m-%d')}")

        # 一、综合分析
        doc.add_heading("一、综合分析", level=1)
        doc.add_paragraph(f"企业名称：{sess.data.company_name}")
        doc.add_paragraph(f"所属行业：{sess.data.industry}" + ("（未匹配，已回退制造业基准）" if sess.diagnosis.industry_fallback else ""))
        doc.add_paragraph(f"分析年度：{', '.join(str(y) for y in sess.data.years)}")
        # 指标表
        doc.add_heading("年度核心指标", level=2)
        rows = _build_indicator_rows(sess)
        table = doc.add_table(rows=1 + len(rows), cols=6)
        table.style = "Light Grid Accent 1"
        hdr = table.rows[0].cells
        for i, h in enumerate(["年度", "营业收入", "增值税税负率", "所得税税负率", "毛利率", "净利率"]):
            hdr[i].text = h
        for ri, row in enumerate(rows):
            for ci, val in enumerate(row):
                table.rows[ri + 1].cells[ci].text = val
        # 增值税估算口径显著标注
        note_p = doc.add_paragraph()
        run = note_p.add_run(f"注：增值税税负率为 {fin.VAT_ESTIMATE_NOTE}，实际以申报数据为准。")
        run.bold = True
        run.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)

        # 图表
        tmp_dir = os.path.dirname(path) or "."
        chart_path = _render_chart_to_png(sess, tmp_dir)
        if chart_path and os.path.exists(chart_path):
            doc.add_picture(chart_path, width=Cm(15))
            cap = doc.add_paragraph("图：营业收入与增值税税负率趋势")
            cap.alignment = WD_ALIGN_PARAGRAPH.CENTER

        # 二、诊断发现
        doc.add_heading("二、第一轮诊断发现", level=1)
        if sess.diagnosis.findings:
            for f in sess.diagnosis.findings:
                doc.add_heading(f"{f.title}（严重度：{f.severity}）", level=2)
                doc.add_paragraph(f"事实：{f.fact}")
                doc.add_paragraph(f"行业对标：{f.benchmark}")
                doc.add_paragraph(f"初稿建议：{f.suggestion}")
                doc.add_paragraph("可选方案：")
                for opt in f.options:
                    doc.add_paragraph(
                        f"  {opt.label}. {opt.name}\n"
                        f"     描述：{opt.description}\n"
                        f"     目标值：{opt.target_value:,.2f}；预计节税：{_fmt_money(opt.est_saving)}；"
                        f"可行性：{opt.feasibility}；风险：{opt.risk_level}",
                        style="List Bullet",
                    )
        else:
            doc.add_paragraph("未发现明显异常，主要指标处于行业合理区间。")

        # 三、第二稿（互动决策）
        doc.add_heading("三、第二稿（互动决策与增值测算）", level=1)
        if sess.draft2:
            t2 = doc.add_table(rows=1 + len(sess.draft2), cols=6)
            t2.style = "Light Grid Accent 1"
            hdr2 = t2.rows[0].cells
            for i, h in enumerate(["发现", "选项", "当前值", "目标值", "变动幅度", "预计节税"]):
                hdr2[i].text = h
            for ri, e in enumerate(sess.draft2):
                cells = t2.rows[ri + 1].cells
                cells[0].text = e.finding_title
                cells[1].text = f"{e.option_label}. {e.option_name}"
                cells[2].text = f"{e.current_value:,.2f}"
                cells[3].text = f"{e.target_value:,.2f}"
                cells[4].text = e.change_pct
                cells[5].text = _fmt_money(e.est_saving)
            # 操作细节与注意事项
            doc.add_heading("操作细节与注意事项", level=2)
            for e in sess.draft2:
                doc.add_heading(e.finding_title, level=3)
                doc.add_paragraph(f"选项：{e.option_label}. {e.option_name}")
                doc.add_paragraph(f"趋势（环比同比）：{e.trend}")
                doc.add_paragraph(f"操作细节：{e.action_detail}")
                doc.add_paragraph(f"注意事项：{e.cautions}")
        else:
            doc.add_paragraph("无决策记录。")

        # 四、完整方案（预计节税汇总）
        doc.add_heading("四、完整方案与预计节税汇总", level=1)
        doc.add_paragraph(f"总预计节税：{_fmt_money(sess.total_est_saving)}")
        doc.add_paragraph(f"落地性评分：{sess.feasibility_score:.2f}%")
        if sess.feasibility_breakdown:
            doc.add_paragraph("扣分明细：")
            for b in sess.feasibility_breakdown:
                doc.add_paragraph(b, style="List Bullet")
        doc.add_paragraph(f"状态：{sess.state}")
        if sess.strategy_notes:
            doc.add_heading("战略意图记录", level=2)
            for n in sess.strategy_notes:
                doc.add_paragraph(n, style="List Bullet")

        # 五、合规声明
        doc.add_heading("五、合规声明", level=1)
        p = doc.add_paragraph(COMPLIANCE_NOTE)
        p.runs[0].bold = True
        doc.add_paragraph(
            "本工具所有优化建议均属于合法税务筹划范畴，包括但不限于：研发费用加计扣除、"
            "小微企业优惠、高新技术企业优惠、限额内费用据实扣除、业务模式优化等。"
        )

        # 六、增值税估算口径说明
        doc.add_heading("六、增值税税负率估算口径说明", level=1)
        doc.add_paragraph(
            "本报告所列增值税税负率为估算值，非真实应纳税额。"
            "估算公式：估算增值税 = 税金及附加 ÷ 12%（增值税附加税费占增值税比例的经验值），"
            "税负率 = 估算增值税 ÷ 营业收入 × 100%。"
            "如能直连开票/申报系统可取真实应纳税额，则应替换为本口径。"
            "实际税务申报与稽查以企业增值税申报表为准。"
        )

        # 附录
        if ai_fallback:
            doc.add_heading("附录：AI 增强状态", level=1)
            doc.add_paragraph(ai_fallback)

        doc.save(path)
        return path
    except ReportError:
        raise
    except Exception as e:
        raise ReportError(f"Word 报告生成失败：{type(e).__name__}: {e}") from e


# ── PDF 报告（ReportLab + CJK 字体） ──────────────────────────────────────

# 系统中文字体候选路径（TrueType），用于 ReportLab 嵌入，避免 Poppler 下 CID 字体空白
_CJK_TTF_CANDIDATES = [
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Songti.ttc",
    # Linux (Noto / WenQuanYi)
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/wenquanyi/wqy-microhei/wqy-microhei.ttc",
    "/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc",
]


def _register_pdf_cjk_font():
    """注册 ReportLab CJK 字体；返回字体名。失败抛 ReportError。

    优先嵌入系统 TrueType 字体（PingFang/Noto）以确保跨查看器（含 Poppler）字形正确；
    若系统无 CJK TrueType，回退到 ReportLab 内置 STSong-Light CID 字体（部分查看器
    可能渲染异常，会在返回字体名后缀加 '_CID' 标记）。
    """
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    except ImportError as e:
        raise ReportError("未安装 reportlab，无法生成 PDF 报告。") from e

    # 1) 优先尝试嵌入系统 TrueType 字体
    for ttf_path in _CJK_TTF_CANDIDATES:
        if not os.path.exists(ttf_path):
            continue
        try:
            # PingFang/Noto 是 ttc 集合，subfontIndex=0 取第一字重
            pdfmetrics.registerFont(TTFont("ProfitBaoCJK", ttf_path, subfontIndex=0))
            return "ProfitBaoCJK"
        except Exception:
            continue

    # 2) 回退到 ReportLab 内置 CID 字体（Poppler 下可能空白，但不会崩溃）
    font_name = "STSong-Light"
    try:
        pdfmetrics.registerFont(UnicodeCIDFont(font_name))
    except Exception:
        pass
    return font_name


def _clean_pdf_text(text: str) -> str:
    """清理 PDF 文本中的不兼容字符。

    - `&nbsp;` → 普通空格
    - `&amp;` / `&lt;` / `&gt;` → 实体转换
    - 项目符号 `•` → `*`（部分 CJK 字体无此字形）
    - 全角空格 `　` → 普通空格
    """
    if not text:
        return text
    return (
        text.replace("&nbsp;", " ")
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("•", "*")
            .replace("　", " ")
    )


def export_pdf(sess: Session, path: str, ai_fallback: str = "") -> str:
    """生成 PDF 报告。返回路径；失败抛 ReportError。"""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak,
        )
    except ImportError as e:
        raise ReportError("未安装 reportlab，无法生成 PDF 报告。") from e

    cjk_font = _register_pdf_cjk_font()
    try:
        doc = SimpleDocTemplate(
            path, pagesize=A4,
            leftMargin=2 * cm, rightMargin=2 * cm,
            topMargin=2 * cm, bottomMargin=2 * cm,
            title="利润宝 · 企业财税优化方案",
        )
        styles = getSampleStyleSheet()
        h1 = ParagraphStyle("H1CJK", parent=styles["Heading1"], fontName=cjk_font, fontSize=16, leading=22, textColor=colors.HexColor("#1e3a8a"))
        h2 = ParagraphStyle("H2CJK", parent=styles["Heading2"], fontName=cjk_font, fontSize=13, leading=18, textColor=colors.HexColor("#1e40af"))
        body = ParagraphStyle("BodyCJK", parent=styles["BodyText"], fontName=cjk_font, fontSize=10, leading=15)
        note_style = ParagraphStyle("NoteCJK", parent=body, textColor=colors.HexColor("#b91c1c"), fontName=cjk_font)
        title_style = ParagraphStyle("TitleCJK", parent=styles["Title"], fontName=cjk_font, fontSize=22, leading=30, alignment=1, textColor=colors.HexColor("#1e3a8a"))

        story = []
        # 封面
        story.append(Paragraph("利润宝 · 企业财税优化方案", title_style))
        story.append(Spacer(1, 0.5 * cm))
        story.append(Paragraph(
            f"企业：{sess.data.company_name}<br/>行业：{sess.data.industry}<br/>"
            f"期间：{sess.data.years}<br/>生成日期：{datetime.now().strftime('%Y-%m-%d')}",
            body,
        ))
        story.append(Spacer(1, 0.5 * cm))

        # 一、综合分析
        story.append(Paragraph("一、综合分析", h1))
        story.append(Paragraph(f"企业名称：{sess.data.company_name}", body))
        fallback_text = "（未匹配，已回退制造业基准）" if sess.diagnosis.industry_fallback else ""
        story.append(Paragraph(f"所属行业：{sess.data.industry}{fallback_text}", body))
        story.append(Paragraph(f"分析年度：{', '.join(str(y) for y in sess.data.years)}", body))
        story.append(Paragraph("年度核心指标", h2))
        rows = _build_indicator_rows(sess)
        table_data = [["年度", "营业收入", "增值税税负率", "所得税税负率", "毛利率", "净利率"]] + rows
        t = Table(table_data, repeatRows=1)
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), cjk_font),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e40af")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ]))
        story.append(t)
        story.append(Spacer(1, 0.3 * cm))
        story.append(Paragraph(f"注：增值税税负率为 {fin.VAT_ESTIMATE_NOTE}，实际以申报数据为准。", note_style))
        story.append(Spacer(1, 0.3 * cm))

        # 图表
        tmp_dir = os.path.dirname(path) or "."
        chart_path = _render_chart_to_png(sess, tmp_dir)
        if chart_path and os.path.exists(chart_path):
            try:
                story.append(Image(chart_path, width=15 * cm, height=8 * cm))
                story.append(Paragraph("图：营业收入与增值税税负率趋势", body))
                story.append(Spacer(1, 0.3 * cm))
            except Exception:
                pass

        # 二、诊断发现
        story.append(PageBreak())
        story.append(Paragraph("二、第一轮诊断发现", h1))
        if sess.diagnosis.findings:
            for f in sess.diagnosis.findings:
                story.append(Paragraph(f"{f.title}（严重度：{f.severity}）", h2))
                story.append(Paragraph(f"事实：{f.fact}", body))
                story.append(Paragraph(f"行业对标：{f.benchmark}", body))
                story.append(Paragraph(f"初稿建议：{f.suggestion}", body))
                story.append(Paragraph("可选方案：", body))
                for opt in f.options:
                    # 清理 &nbsp; / •；用全角空格替代 HTML 实体，避免 ReportLab 解析异常
                    desc_text = _clean_pdf_text(opt.description)
                    story.append(Paragraph(
                        f"{opt.label}. {opt.name}<br/>"
                        f"  描述：{desc_text}<br/>"
                        f"  目标值：{opt.target_value:,.2f}；预计净影响：{_fmt_money(opt.est_saving)}；"
                        f"成本节约：{_fmt_money(opt.cost_saving)}；税收节约：{_fmt_money(opt.tax_saving)}；"
                        f"税负影响：{_fmt_money(opt.tax_impact)}；"
                        f"可行性：{opt.feasibility}；风险：{opt.risk_level}",
                        body,
                    ))
        else:
            story.append(Paragraph("未发现明显异常，主要指标处于行业合理区间。", body))

        # 三、第二稿
        story.append(Paragraph("三、第二稿（互动决策与增值测算）", h1))
        if sess.draft2:
            t2_data = [["发现", "选项", "当前值", "目标值", "变动幅度", "净影响"]]
            for e in sess.draft2:
                t2_data.append([
                    e.finding_title,
                    f"{e.option_label}. {e.option_name}",
                    f"{e.current_value:,.2f}",
                    f"{e.target_value:,.2f}",
                    e.change_pct,
                    _fmt_money(e.est_saving),
                ])
            t2 = Table(t2_data, repeatRows=1)
            t2.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, -1), cjk_font),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e40af")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]))
            story.append(t2)
            story.append(Spacer(1, 0.3 * cm))
            story.append(Paragraph("操作细节与注意事项", h2))
            for e in sess.draft2:
                story.append(Paragraph(e.finding_title, h2))
                story.append(Paragraph(f"选项：{e.option_label}. {e.option_name}", body))
                story.append(Paragraph(f"趋势（环比同比）：{e.trend}", body))
                story.append(Paragraph(f"操作细节：{_clean_pdf_text(e.action_detail)}", body))
                story.append(Paragraph(f"注意事项：{_clean_pdf_text(e.cautions)}", body))
        else:
            story.append(Paragraph("无决策记录。", body))

        # 四、完整方案
        story.append(Paragraph("四、完整方案与净影响汇总", h1))
        story.append(Paragraph(f"总净影响：{_fmt_money(sess.total_est_saving)}", body))
        story.append(Paragraph(f"落地性评分：{sess.feasibility_score:.2f}%", body))
        if sess.feasibility_breakdown:
            story.append(Paragraph("扣分明细：", body))
            for b in sess.feasibility_breakdown:
                story.append(Paragraph(f"- {b}", body))
        story.append(Paragraph(f"状态：{sess.state}", body))
        if sess.strategy_notes:
            story.append(Paragraph("战略意图记录", h2))
            for n in sess.strategy_notes:
                story.append(Paragraph(f"- {n}", body))

        # 五、合规声明
        story.append(Paragraph("五、合规声明", h1))
        story.append(Paragraph(COMPLIANCE_NOTE, ParagraphStyle("Bold", parent=body, fontName=cjk_font, textColor=colors.HexColor("#b91c1c"))))
        story.append(Paragraph(
            "本工具所有优化建议均属于合法税务筹划范畴，包括但不限于：研发费用加计扣除、"
            "小微企业优惠、高新技术企业优惠、限额内费用据实扣除、业务模式优化等。",
            body,
        ))

        # 六、增值税估算口径说明
        story.append(Paragraph("六、增值税税负率估算口径说明", h1))
        story.append(Paragraph(
            "本报告所列增值税税负率为估算值，非真实应纳税额。"
            "估算公式：估算增值税 = 税金及附加 ÷ 12%（增值税附加税费占增值税比例的经验值），"
            "税负率 = 估算增值税 ÷ 营业收入 × 100%。"
            "如能直连开票/申报系统可取真实应纳税额，则应替换为本口径。"
            "实际税务申报与稽查以企业增值税申报表为准。",
            body,
        ))

        if ai_fallback:
            story.append(Paragraph("附录：AI 增强状态", h1))
            story.append(Paragraph(ai_fallback, body))

        doc.build(story)
        return path
    except ReportError:
        raise
    except Exception as e:
        raise ReportError(f"PDF 报告生成失败：{type(e).__name__}: {e}") from e


# ── T6.5 模板版预算报告（Word / PDF） ──────────────────────────────────────

def _budget_indicator_rows(plan) -> List[List[str]]:
    """构建经营目标指标行（顶部输入 + 计算结果）。"""
    ti = plan.top_inputs
    tc = plan.top_computed
    return [
        ["指标", "数值", "说明"],
        ["预算营业收入（C2）", f"{ti.budget_revenue:,.2f} 元", "顶部输入"],
        ["预算营业成本（C3）", f"{ti.budget_cost:,.2f} 元", "顶部输入"],
        ["毛利（C4=C2-C3）", f"{tc.gross_profit:,.2f} 元", "公式计算"],
        ["毛利率（C5=C4/C2）", f"{tc.gross_margin*100:.2f}%", "公式计算"],
        ["上年度营业收入（C6）", f"{ti.last_year_revenue:,.2f} 元", "顶部输入"],
        ["收入增长率（C7=(C2-C6)/C6）", f"{tc.revenue_growth_rate*100:.2f}%", "公式计算"],
        ["上年度营业成本（C8）", f"{ti.last_year_cost:,.2f} 元", "顶部输入"],
        ["上年度毛利率（C9=(C6-C8)/C6）", f"{tc.last_year_gross_margin*100:.2f}%", "公式计算"],
        ["行业所得税贡献率（E2）", f"{ti.industry_contribution_rate*100:.4f}%", "模板默认（待核验）"],
        ["企业预算所得税贡献率（E3）", f"{ti.company_contribution_rate*100:.4f}%", "顶部输入"],
        ["所得税税率（E4）", f"{ti.income_tax_rate*100:.2f}%", "模板默认（待核验）"],
        ["应交所得税预算（E5=E3×C2）", f"{tc.income_tax_budget:,.2f} 元", "公式计算"],
        ["利润总额预算（E6=E5/E4）", f"{tc.profit_total_budget:,.2f} 元", "公式计算"],
        ["费用预算上限（E7=C4-E6）", f"{tc.expense_budget_cap:,.2f} 元", "公式计算"],
        ["实际已发生费用（E8=I98）", f"{tc.actual_expense_total:,.2f} 元", "明细联动"],
        ["费用差额（E9=E7-E8）", f"{tc.expense_diff:,.2f} 元", "正=剩余 负=超支"],
        ["未分配余额（G100=E7-G98）", f"{tc.unallocated_balance:,.2f} 元", "负=分配超上限"],
    ]


def _budget_summary_rows(plan) -> List[List[str]]:
    """构建预算执行汇总行。"""
    return [
        ["汇总项", "数值"],
        ["明细行数", f"{plan.total_lines} 行"],
        ["上年同期实际费用合计（D98）", f"{plan.last_year_total:,.2f} 元"],
        ["预算费用合计（G98）", f"{plan.allocated_total:,.2f} 元"],
        ["实际已发生费用合计（I98）", f"{plan.actual_total:,.2f} 元"],
        ["差额合计（J98）", f"{plan.diff_total:,.2f} 元"],
        ["超支项数", f"{plan.over_budget_count} 项"],
        ["临界项数", f"{plan.critical_count} 项"],
        ["待补录项数", f"{plan.pending_count} 项"],
        ["预算执行率", f"{(plan.actual_total/plan.allocated_total*100) if plan.allocated_total else 0:.1f}%"],
    ]


def export_budget_word(plan, path: str, session=None) -> str:
    """P0-4：生成模板版预算 Word 报告（含超支明细 + 诊断建议 + 负责人/期限 + 完整口径）。

    Args:
        plan: 预算计划
        path: 导出路径
        session: 可选诊断会话；有则写入诊断与优化建议章节，无则明确写「尚未完成诊断」

    Raises:
        ReportError: 校验未通过或生成失败；失败时不写入任何文件、不修改 plan
    """
    # P0-C（CO T7 重开）：导出前调用 validate_plan；失败时不写入文件、不修改 plan
    from .budget import validate_plan
    ok, errors = validate_plan(plan)
    if not ok:
        raise ReportError(
            "预算计划校验未通过，拒绝生成 Word：\n  - " + "\n  - ".join(errors)
            + "\n请先在「2. 模板工作台」修正输入后再导出。"
        )

    try:
        from docx import Document
        from docx.shared import Pt, Cm, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml.ns import qn
    except ImportError as e:
        raise ReportError("未安装 python-docx，无法生成 Word 报告。") from e

    try:
        doc = Document()
        # 默认中文字体
        style = doc.styles["Normal"]
        style.font.name = "Songti SC"
        style.font.size = Pt(10.5)
        try:
            style.element.rPr.rFonts.set(qn("w:eastAsia"), "Songti SC")
        except Exception:
            pass

        # 封面
        title = doc.add_heading("利润宝 · 经营目标与预算执行报告", level=0)
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        doc.add_paragraph(
            f"企业：{plan.company_name or '（未命名）'}　　行业：{plan.industry}　　年度：{plan.year or '—'}",
        )
        doc.add_paragraph()

        # 一、经营目标
        doc.add_heading("一、经营目标（顶部公式联动）", level=1)
        _add_word_table(doc, _budget_indicator_rows(plan))
        doc.add_paragraph(
            "注：行业所得税贡献率与所得税税率为模板默认值，须以企业实际适用政策核验。",
        )

        # 二、预算执行汇总
        doc.add_heading("二、预算执行汇总", level=1)
        _add_word_table(doc, _budget_summary_rows(plan))

        # 三、超支异常明细（P0-4 新增）
        doc.add_heading("三、超支异常明细", level=1)
        over_lines = [l for l in plan.lines if l.exec_status == "超支"]
        critical_lines = [l for l in plan.lines if l.exec_status == "临界"]
        doc.add_paragraph(
            f"超支项数：{plan.over_budget_count}　临界项数：{plan.critical_count}　待补录项数：{plan.pending_count}",
        )
        if over_lines:
            over_header = ["行号", "科目", "费用名称", "预算费用", "实际已发生", "差额", "执行率", "执行状态"]
            over_rows = [over_header]
            for l in over_lines:
                exec_rate = (l.actual_amount / l.budget_amount * 100) if l.budget_amount else 0
                over_rows.append([
                    str(l.row), l.subject, l.expense_name,
                    f"{l.budget_amount:,.0f}", f"{l.actual_amount:,.0f}",
                    f"{l.diff:,.0f}", f"{exec_rate:.1f}%", l.exec_status,
                ])
            _add_word_table(doc, over_rows)
            doc.add_paragraph(
                "处理建议：超支项应优先复核业务真实性与必要性；可在「3. 互动」环节通过 A/B/C 选项生成压降方案。",
            )
        else:
            doc.add_paragraph("无超支项。")

        # 四、费用明细（84 行）
        doc.add_heading("四、费用明细（84 行）", level=1)
        detail_header = ["行号", "科目", "费用名称", "上年实际", "预算费用", "实际已发生", "差额", "执行状态"]
        detail_rows = [detail_header]
        for l in plan.lines:
            detail_rows.append([
                str(l.row), l.subject, l.expense_name,
                f"{l.last_year_actual:,.0f}", f"{l.budget_amount:,.0f}",
                f"{l.actual_amount:,.0f}", f"{l.diff:,.0f}", l.exec_status,
            ])
        _add_word_table(doc, detail_rows)

        # 五、诊断与优化建议（P0-4 新增；含负责人/期限/成本节约/税收节约/税负影响/净影响分栏）
        doc.add_heading("五、诊断与优化建议", level=1)
        if session is not None and getattr(session, "draft2", None):
            doc.add_paragraph(
                f"总成本节约：{_fmt_money(sum(getattr(d, 'cost_saving', 0.0) or 0.0 for d in session.draft2))}　"
                f"总税收节约：{_fmt_money(sum(getattr(d, 'tax_saving', 0.0) or 0.0 for d in session.draft2))}　"
                f"总税负影响：{_fmt_money(sum(getattr(d, 'tax_impact', 0.0) or 0.0 for d in session.draft2))}　"
                # P0-B（CO T7 重开）：Draft2Entry 字段为 est_saving（非 net_impact）
                f"总净影响：{_fmt_money(sum(getattr(d, 'est_saving', 0.0) or 0.0 for d in session.draft2))}",
            )
            action_header = [
                "序号", "发现", "选项", "目标值",
                "成本节约(元)", "税收节约(元)", "税负影响(元)", "净影响(元)",
                "负责人", "期限", "执行状态",
            ]
            action_rows = [action_header]
            for idx, d in enumerate(session.draft2, start=1):
                action_rows.append([
                    str(idx),
                    getattr(d, "finding_title", "") or getattr(d, "finding_id", ""),
                    f"{getattr(d, 'option_label', '')}. {getattr(d, 'option_name', '')}",
                    f"{getattr(d, 'target_value', 0):,.2f}",
                    f"{getattr(d, 'cost_saving', 0.0) or 0.0:,.0f}",
                    f"{getattr(d, 'tax_saving', 0.0) or 0.0:,.0f}",
                    f"{getattr(d, 'tax_impact', 0.0) or 0.0:,.0f}",
                    # P0-B：Draft2Entry 字段为 est_saving
                    f"{getattr(d, 'est_saving', 0.0) or 0.0:,.0f}",
                    getattr(d, "owner", "") or "（待指派）",
                    getattr(d, "deadline", "") or "（待确定）",
                    getattr(d, "exec_status", "待执行") if hasattr(d, "exec_status") else "待执行",
                ])
            _add_word_table(doc, action_rows)
            # 操作细节与注意事项
            doc.add_heading("操作细节与注意事项", level=2)
            for d in session.draft2:
                doc.add_heading(getattr(d, "finding_title", ""), level=3)
                doc.add_paragraph(f"选项：{getattr(d, 'option_label', '')}. {getattr(d, 'option_name', '')}")
                doc.add_paragraph(f"目标值：{getattr(d, 'target_value', 0):,.2f}")
                if hasattr(d, "trend"):
                    doc.add_paragraph(f"趋势（环比同比）：{d.trend}")
                if hasattr(d, "action_detail"):
                    doc.add_paragraph(f"操作细节：{d.action_detail}")
                if hasattr(d, "cautions"):
                    doc.add_paragraph(f"注意事项：{d.cautions}")
        else:
            doc.add_paragraph(
                "尚未完成诊断；请先在「1. 导入」载入三年财报 → 「2. 诊断」执行第一轮 → "
                "「3. 互动」完成 A/B/C 决策，再生成本章节的优化建议。本工具不得伪造建议。",
            )

        # 六、完整计算口径（P0-4 新增）
        doc.add_heading("六、完整计算口径", level=1)
        doc.add_paragraph(
            "顶部公式：\n"
            "  C4 毛利 = C2 预算营业收入 − C3 预算营业成本\n"
            "  C5 毛利率 = IF(C2=0, 0, C4 / C2)\n"
            "  C7 收入增长率 = IF(C6=0, 0, (C2 − C6) / C6)\n"
            "  C9 上年度毛利率 = IF(C6=0, 0, (C6 − C8) / C6)\n"
            "  E5 应交所得税预算 = E3 企业所得税贡献率 × C2\n"
            "  E6 利润总额预算 = IF(E4=0, 0, E5 / E4)\n"
            "  E7 费用预算上限 = C4 − E6\n"
            "  E8 实际已发生费用 = I98（明细 I 列合计）\n"
            "  E9 费用差额 = E7 − E8（正=剩余，负=超支）\n"
            "  G100 未分配余额 = E7 − G98\n"
            "明细公式：\n"
            "  E 上年费用率 = IF($C$6=0, 0, D / $C$6)\n"
            "  F 参考金额 = D × (1 + $C$7)\n"
            "  H 预算费用率 = IF($C$2=0, 0, G / $C$2)\n"
            "  J 差额 = G − I\n"
            "执行状态判定：待补录（G=I=0）/ 正常（执行率<80%）/ 临界（80%-100%）/ 超支（>100%）",
        )

        # 七、合规声明
        doc.add_heading("七、合规声明", level=1)
        doc.add_paragraph(COMPLIANCE_NOTE)
        doc.add_paragraph(
            "增值税税负率为 " + fin.VAT_ESTIMATE_NOTE
            + "；公式：税金及附加 ÷ 12% ÷ 营业收入 × 100%。实际以申报数据为准。",
        )

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        doc.save(path)
        return path
    except ReportError:
        raise
    except Exception as e:
        raise ReportError(f"预算 Word 报告生成失败：{type(e).__name__}: {e}") from e


def _add_word_table(doc, rows):
    """向 Word 文档添加表格（首行为表头）。"""
    if not rows:
        return
    table = doc.add_table(rows=len(rows), cols=len(rows[0]))
    table.style = "Light Grid Accent 1"
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            cell = table.cell(i, j)
            cell.text = str(val)
            if i == 0:
                for p in cell.paragraphs:
                    for r in p.runs:
                        r.font.bold = True


def export_budget_pdf(plan, path: str, session=None) -> str:
    """P0-4：生成模板版预算 PDF 报告（含超支明细 + 诊断建议 + 负责人/期限 + 完整口径）。

    Args:
        plan: 预算计划
        path: 导出路径
        session: 可选诊断会话；有则写入诊断与优化建议章节，无则明确写「尚未完成诊断」

    Raises:
        ReportError: 校验未通过或生成失败；失败时不写入任何文件、不修改 plan
    """
    # P0-C（CO T7 重开）：导出前调用 validate_plan；失败时不写入文件、不修改 plan
    from .budget import validate_plan
    ok, errors = validate_plan(plan)
    if not ok:
        raise ReportError(
            "预算计划校验未通过，拒绝生成 PDF：\n  - " + "\n  - ".join(errors)
            + "\n请先在「2. 模板工作台」修正输入后再导出。"
        )

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
        )
    except ImportError as e:
        raise ReportError("未安装 reportlab，无法生成 PDF 报告。") from e

    cjk_font = _register_pdf_cjk_font()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        doc = SimpleDocTemplate(path, pagesize=A4,
                                leftMargin=1.8*cm, rightMargin=1.8*cm,
                                topMargin=1.8*cm, bottomMargin=1.8*cm)
        styles = getSampleStyleSheet()
        h1 = ParagraphStyle("H1", parent=styles["Heading1"],
                            fontName=cjk_font, fontSize=15, leading=20)
        h2 = ParagraphStyle("H2", parent=styles["Heading2"],
                            fontName=cjk_font, fontSize=12, leading=16)
        h3 = ParagraphStyle("H3", parent=styles["Heading3"],
                            fontName=cjk_font, fontSize=10.5, leading=14)
        body = ParagraphStyle("Body", parent=styles["Normal"],
                              fontName=cjk_font, fontSize=9.5, leading=14)
        note = ParagraphStyle("Note", parent=body, textColor=colors.red, fontSize=8.5)

        story = []
        # 封面
        story.append(Paragraph("利润宝 · 经营目标与预算执行报告", h1))
        story.append(Spacer(1, 6))
        story.append(Paragraph(
            _clean_pdf_text(f"企业：{plan.company_name or '（未命名）'}　行业：{plan.industry}　年度：{plan.year or '—'}"),
            body,
        ))
        story.append(Spacer(1, 12))

        _pdf_table_style = TableStyle([
            ("FONT", (0, 0), (-1, -1), cjk_font, 8),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E40AF")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ])

        # 一、经营目标
        story.append(Paragraph("一、经营目标（顶部公式联动）", h2))
        ind_rows = _budget_indicator_rows(plan)
        ind_data = [[_clean_pdf_text(c) for c in row] for row in ind_rows]
        story.append(Table(ind_data, repeatRows=1, style=_pdf_table_style))
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            _clean_pdf_text("注：行业所得税贡献率与所得税税率为模板默认值，须以企业实际适用政策核验。"),
            note,
        ))
        story.append(Spacer(1, 10))

        # 二、预算执行汇总
        story.append(Paragraph("二、预算执行汇总", h2))
        sum_rows = _budget_summary_rows(plan)
        sum_data = [[_clean_pdf_text(c) for c in row] for row in sum_rows]
        story.append(Table(sum_data, repeatRows=1, style=_pdf_table_style))
        story.append(Spacer(1, 10))

        # 三、超支异常明细（P0-4 新增）
        story.append(Paragraph("三、超支异常明细", h2))
        over_lines = [l for l in plan.lines if l.exec_status == "超支"]
        story.append(Paragraph(
            _clean_pdf_text(f"超支项数：{plan.over_budget_count}　临界项数：{plan.critical_count}　待补录项数：{plan.pending_count}"),
            body,
        ))
        if over_lines:
            over_header = ["行号", "科目", "费用名称", "预算费用", "实际已发生", "差额", "执行率", "执行状态"]
            over_data = [over_header]
            for l in over_lines:
                exec_rate = (l.actual_amount / l.budget_amount * 100) if l.budget_amount else 0
                over_data.append([
                    str(l.row), _clean_pdf_text(l.subject),
                    _clean_pdf_text(l.expense_name),
                    f"{l.budget_amount:,.0f}", f"{l.actual_amount:,.0f}",
                    f"{l.diff:,.0f}", f"{exec_rate:.1f}%",
                    _clean_pdf_text(l.exec_status),
                ])
            story.append(Table(over_data, repeatRows=1, style=_pdf_table_style))
            story.append(Spacer(1, 4))
            story.append(Paragraph(
                _clean_pdf_text("处理建议：超支项应优先复核业务真实性与必要性；可在「3. 互动」环节通过 A/B/C 选项生成压降方案。"),
                body,
            ))
        else:
            story.append(Paragraph("无超支项。", body))
        story.append(Spacer(1, 10))

        # 四、费用明细（84 行）
        story.append(Paragraph("四、费用明细（84 行）", h2))
        detail_header = ["行号", "科目", "费用名称", "上年实际", "预算", "实际", "差额", "状态"]
        detail_data = [detail_header]
        for l in plan.lines:
            detail_data.append([
                str(l.row), _clean_pdf_text(l.subject),
                _clean_pdf_text(l.expense_name),
                f"{l.last_year_actual:,.0f}", f"{l.budget_amount:,.0f}",
                f"{l.actual_amount:,.0f}", f"{l.diff:,.0f}",
                _clean_pdf_text(l.exec_status),
            ])
        story.append(Table(detail_data, repeatRows=1, style=TableStyle([
            ("FONT", (0, 0), (-1, -1), cjk_font, 7.5),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E40AF")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ])))
        story.append(Spacer(1, 10))

        # 五、诊断与优化建议（P0-4 新增）
        story.append(Paragraph("五、诊断与优化建议", h2))
        if session is not None and getattr(session, "draft2", None):
            total_cost = sum(getattr(d, "cost_saving", 0.0) or 0.0 for d in session.draft2)
            total_tax = sum(getattr(d, "tax_saving", 0.0) or 0.0 for d in session.draft2)
            total_impact = sum(getattr(d, "tax_impact", 0.0) or 0.0 for d in session.draft2)
            total_net = sum(getattr(d, "est_saving", 0.0) or 0.0 for d in session.draft2)  # P0-B：est_saving 非 net_impact
            story.append(Paragraph(
                _clean_pdf_text(f"总成本节约：{total_cost:,.0f} 元　总税收节约：{total_tax:,.0f} 元　"
                                f"总税负影响：{total_impact:,.0f} 元　总净影响：{total_net:,.0f} 元"),
                body,
            ))
            story.append(Spacer(1, 4))
            action_header = [
                "序号", "发现", "选项", "目标值",
                "成本节约", "税收节约", "税负影响", "净影响",
                "负责人", "期限", "执行状态",
            ]
            action_data = [action_header]
            for idx, d in enumerate(session.draft2, start=1):
                action_data.append([
                    str(idx),
                    _clean_pdf_text(getattr(d, "finding_title", "") or getattr(d, "finding_id", "")),
                    _clean_pdf_text(f"{getattr(d, 'option_label', '')}. {getattr(d, 'option_name', '')}"),
                    f"{getattr(d, 'target_value', 0):,.0f}",
                    f"{getattr(d, 'cost_saving', 0.0) or 0.0:,.0f}",
                    f"{getattr(d, 'tax_saving', 0.0) or 0.0:,.0f}",
                    f"{getattr(d, 'tax_impact', 0.0) or 0.0:,.0f}",
                    # P0-B：Draft2Entry 字段为 est_saving
                    f"{getattr(d, 'est_saving', 0.0) or 0.0:,.0f}",
                    _clean_pdf_text(getattr(d, "owner", "") or "（待指派）"),
                    _clean_pdf_text(getattr(d, "deadline", "") or "（待确定）"),
                    _clean_pdf_text(getattr(d, "exec_status", "待执行") if hasattr(d, "exec_status") else "待执行"),
                ])
            story.append(Table(action_data, repeatRows=1, style=TableStyle([
                ("FONT", (0, 0), (-1, -1), cjk_font, 7),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E40AF")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ])))
            story.append(Spacer(1, 6))
            story.append(Paragraph("操作细节与注意事项", h3))
            for d in session.draft2:
                story.append(Paragraph(
                    _clean_pdf_text(f"· {getattr(d, 'finding_title', '')}："
                                    f"选项 {getattr(d, 'option_label', '')}. {getattr(d, 'option_name', '')}；"
                                    f"目标值 {getattr(d, 'target_value', 0):,.2f}"),
                    body,
                ))
                if hasattr(d, "action_detail"):
                    story.append(Paragraph(_clean_pdf_text(f"  操作细节：{d.action_detail}"), body))
                if hasattr(d, "cautions"):
                    story.append(Paragraph(_clean_pdf_text(f"  注意事项：{d.cautions}"), body))
        else:
            story.append(Paragraph(
                _clean_pdf_text("尚未完成诊断；请先在「1. 导入」载入三年财报 → 「2. 诊断」执行第一轮 → "
                                "「3. 互动」完成 A/B/C 决策，再生成本章节的优化建议。本工具不得伪造建议。"),
                body,
            ))
        story.append(Spacer(1, 10))

        # 六、完整计算口径（P0-4 新增）
        story.append(Paragraph("六、完整计算口径", h2))
        formula_text = (
            "顶部公式：<br/>"
            "  C4 毛利 = C2 预算营业收入 - C3 预算营业成本<br/>"
            "  C5 毛利率 = IF(C2=0, 0, C4 / C2)<br/>"
            "  C7 收入增长率 = IF(C6=0, 0, (C2 - C6) / C6)<br/>"
            "  C9 上年度毛利率 = IF(C6=0, 0, (C6 - C8) / C6)<br/>"
            "  E5 应交所得税预算 = E3 企业所得税贡献率 × C2<br/>"
            "  E6 利润总额预算 = IF(E4=0, 0, E5 / E4)<br/>"
            "  E7 费用预算上限 = C4 - E6<br/>"
            "  E8 实际已发生费用 = I98（明细 I 列合计）<br/>"
            "  E9 费用差额 = E7 - E8（正=剩余，负=超支）<br/>"
            "  G100 未分配余额 = E7 - G98<br/>"
            "明细公式：<br/>"
            "  E 上年费用率 = IF($C$6=0, 0, D / $C$6)<br/>"
            "  F 参考金额 = D × (1 + $C$7)<br/>"
            "  H 预算费用率 = IF($C$2=0, 0, G / $C$2)<br/>"
            "  J 差额 = G - I<br/>"
            "执行状态判定：待补录（G=I=0）/ 正常（执行率&lt;80%）/ 临界（80%-100%）/ 超支（&gt;100%）"
        )
        story.append(Paragraph(formula_text, body))
        story.append(Spacer(1, 10))

        # 七、合规声明
        story.append(Paragraph("七、合规声明", h2))
        story.append(Paragraph(_clean_pdf_text(COMPLIANCE_NOTE), body))
        story.append(Paragraph(
            _clean_pdf_text("增值税税负率为 " + fin.VAT_ESTIMATE_NOTE
                            + "；实际以申报数据为准。"),
            note,
        ))

        doc.build(story)
        return path
    except ReportError:
        raise
    except Exception as e:
        raise ReportError(f"预算 PDF 报告生成失败：{type(e).__name__}: {e}") from e
