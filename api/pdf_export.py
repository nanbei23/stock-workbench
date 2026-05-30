"""
PDF报告导出模块
支持fpdf2生成PDF，或降级为纯文本导出
"""

import os
import logging
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import StreamingResponse, PlainTextResponse
from pydantic import BaseModel
from typing import Optional
import base64
import tempfile

from services import ai_report_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["PDF导出"])


# 报告字段映射 (column_name -> display_label)
REPORT_FIELDS = [
    ("final_decision", "最终决策"),
    ("market_report", "市场技术分析"),
    ("sentiment_report", "市场情绪分析"),
    ("news_report", "新闻舆情分析"),
    ("fundamentals_report", "基本面分析"),
    ("policy_report", "政策分析"),
    ("hot_money_report", "游资追踪"),
    ("lockup_report", "解禁监控"),
    ("investment_debate", "多空辩论"),
    ("risk_debate", "风控评估"),
    ("trader_plan", "交易员计划"),
    ("data_quality_summary", "数据质量门控"),
]


def _generate_pdf_content(report: dict) -> bytes:
    """Generate PDF using fpdf2 with Chinese font support"""
    from fpdf import FPDF

    # 查找中文字体
    font_path = None
    candidates = [
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        str(Path.home() / "stock-workbench" / "static" / "fonts" / "NotoSansSC-Regular.ttf"),
    ]
    for fp in candidates:
        if os.path.exists(fp):
            font_path = fp
            break

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)

    # 如果找到中文字体，注册使用
    if font_path:
        try:
            pdf.add_font("Chinese", "", font_path, uni=True)
            font_name = "Chinese"
        except Exception:
            font_name = "Helvetica"
    else:
        font_name = "Helvetica"

    code = report.get("code", "")
    signal = report.get("signal", "--")
    created = report.get("created_at", "")
    chart_image = report.get("_chart_image")  # optional base64 png

    pdf.add_page()
    pdf.set_font(font_name, size=18)
    pdf.cell(0, 12, txt=f"AI Analysis Report - {code}", ln=True, align="C")
    pdf.set_font(font_name, size=10)
    pdf.cell(0, 8, txt=f"Signal: {signal} | Created: {created}", ln=True, align="C")
    pdf.ln(5)
    # Embed chart image if provided (first page after cover)
    if chart_image:
        try:
            img_data = base64.b64decode(chart_image)
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                tmp.write(img_data)
                tmp_path = tmp.name
            pdf.image(tmp_path, x=10, w=190)
            pdf.ln(5)
            os.unlink(tmp_path)
        except Exception as e:
            logger.warning(f"Failed to embed chart image: {e}")

    # Summary section
    pdf.set_font(font_name, size=12)
    pdf.cell(0, 8, txt="Summary", ln=True)
    pdf.set_font(font_name, size=10)

    confidence = report.get("confidence")
    risk_score = report.get("risk_score")
    conf_str = f"{confidence * 100:.0f}%" if confidence else "--"
    risk_str = f"{risk_score * 100:.0f}%" if risk_score else "--"
    duration = report.get("duration_seconds")
    dur_str = f"{duration:.1f}s" if duration else "--"

    pdf.cell(0, 7, txt=f"  Signal: {signal}  |  Confidence: {conf_str}  |  Risk: {risk_str}  |  Duration: {dur_str}", ln=True)
    pdf.ln(5)

    # Each report section
    for col_name, label in REPORT_FIELDS:
        text = report.get(col_name)
        if not text:
            continue

        text = str(text).strip()
        if not text:
            continue

        # 截断过长内容
        if len(text) > 8000:
            text = text[:8000] + "\n...(truncated)"

        pdf.set_font(font_name, size=12)
        pdf.cell(0, 8, txt=label, ln=True)
        pdf.set_draw_color(200, 200, 200)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(2)
        pdf.set_font(font_name, size=9)

        # 处理特殊字符
        safe_text = text.encode('latin-1', errors='replace').decode('latin-1') if font_name == "Helvetica" else text

        try:
            pdf.multi_cell(0, 5, txt=safe_text)
        except Exception:
            # 降级处理
            pdf.multi_cell(0, 5, txt=text[:2000])
        pdf.ln(3)

    return pdf.output(dest="S").encode("latin-1")


def _generate_text_content(report: dict) -> str:
    """Generate plain text report"""
    code = report.get("code", "")
    signal = report.get("signal", "--")
    confidence = report.get("confidence")
    risk_score = report.get("risk_score")
    created = report.get("created_at", "")
    duration = report.get("duration_seconds")

    conf_str = f"{confidence * 100:.0f}%" if confidence else "--"
    risk_str = f"{risk_score * 100:.0f}%" if risk_score else "--"
    dur_str = f"{duration:.1f}s" if duration else "--"

    lines = [
        f"AI分析报告 - {code}",
        "=" * 60,
        f"信号: {signal}  |  置信度: {conf_str}  |  风险评分: {risk_str}  |  耗时: {dur_str}",
        f"创建时间: {created}",
        "",
    ]

    for col_name, label in REPORT_FIELDS:
        text = report.get(col_name)
        if not text:
            continue
        text = str(text).strip()
        if not text:
            continue
        lines.append(f"--- {label} ---")
        if len(text) > 8000:
            text = text[:8000] + "\n...(截断)"
        lines.append(text)
        lines.append("")

    return "\n".join(lines)


class ReportRequest(BaseModel):
    chart_image: Optional[str] = None


@router.post("/ai/report/{report_id}/pdf")
async def export_report_pdf_post(report_id: int, req: Optional[ReportRequest] = None):
    """导出分析报告为PDF，支持可选的图表截图"""
    return await _do_export(report_id, req.chart_image if req else None)


@router.get("/ai/report/{report_id}/pdf")
async def export_report_pdf(report_id: int):
    """导出分析报告为PDF"""
    return await _do_export(report_id, None)


async def _do_export(report_id: int, chart_image: Optional[str] = None):
    report = await ai_report_service.get_report(report_id)

    # Inject chart image if provided
    if chart_image:
        report["_chart_image"] = chart_image

    # 尝试生成PDF
    try:
        pdf_bytes = _generate_pdf_content(report)
        return StreamingResponse(
            iter([pdf_bytes]),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="report-{report_id}.pdf"',
                "Content-Length": str(len(pdf_bytes)),
            },
        )
    except ImportError:
        # fpdf2 未安装，降级为纯文本
        content = _generate_text_content(report)
        return PlainTextResponse(
            content,
            headers={"Content-Disposition": f'attachment; filename="report-{report_id}.txt"'},
        )
    except Exception as e:
        logger.error(f"PDF生成失败: {e}", exc_info=True)
        # 降级为纯文本
        content = _generate_text_content(report)
        return PlainTextResponse(
            content,
            headers={"Content-Disposition": f'attachment; filename="report-{report_id}.txt"'},
        )
