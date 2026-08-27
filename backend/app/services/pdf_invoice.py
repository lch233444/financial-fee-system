from __future__ import annotations

from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..models import Invoice, QuarterlySettlement
from ..money import money_string


BRAND = colors.HexColor("#173B57")
ACCENT = colors.HexColor("#E6A33A")
PALE = colors.HexColor("#F3F7FA")
TEXT = colors.HexColor("#1D2935")
MUTED = colors.HexColor("#657786")


def _register_chinese_font() -> str:
    candidates = [
        ("MicrosoftYaHei", Path(r"C:\Windows\Fonts\msyh.ttc")),
        ("MicrosoftYaHei", Path(r"C:\Windows\Fonts\msyh.ttf")),
        ("SimSun", Path(r"C:\Windows\Fonts\simsun.ttc")),
        ("SimHei", Path(r"C:\Windows\Fonts\simhei.ttf")),
    ]
    for name, path in candidates:
        if path.exists():
            try:
                if name not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(name, str(path)))
                return name
            except Exception:
                continue
    return "Helvetica"


def _money(cents: int) -> str:
    return f"HKD {int(cents) / 100:,.2f}"


def _labels(language: str) -> dict[str, str]:
    if language == "en":
        return {
            "title_invoice": "Quarterly Settlement & Service Fee Invoice",
            "title_statement": "Quarterly Settlement Statement",
            "invoice_no": "Invoice No.",
            "issue_date": "Issue Date",
            "due_date": "Payment Due Date",
            "client": "Client",
            "platform": "Platform",
            "fee_plan": "Fee Plan",
            "accounts": "Account(s)",
            "period": "Settlement Period",
            "calculation": "Settlement Calculation",
            "account_breakdown": "Account-level Service Fee Breakdown",
            "item": "Item",
            "amount": "Amount (HKD)",
            "beginning": "Beginning Balance",
            "contribution": "Contribution",
            "withdrawal": "Withdrawal",
            "net_contribution": "Net Contribution",
            "closing": "Closing Balance",
            "gain_loss": "Gain / Loss",
            "period_rate": "Period Rate of Return",
            "days": "Days (display only)",
            "original_hwm": "Original High Water Mark",
            "adjusted_hwm": "Adjusted High Water Mark",
            "above_hwm": "Chargeable Above Watermark",
            "fee_rate": "Service Fee Rate",
            "service_fee": "Service Fee Due",
            "next_hwm": "High Water Mark in Next Period",
            "net_short": "Net Contribution",
            "above_short": "Above HWM",
            "payment": "Payment Information",
            "bank": "Bank Transfer",
            "cheque": "Cheque",
            "note": "This document is generated from the finalized quarterly settlement record.",
            "page": "Page",
        }
    return {
        "title_invoice": "季度结算及服务费账单",
        "title_statement": "季度结算单",
        "invoice_no": "账单编号 Invoice No.",
        "issue_date": "出具日期 Issue Date",
        "due_date": "付款到期日 Due Date",
        "client": "客户 Client",
        "platform": "平台 Platform",
        "fee_plan": "收费计划 Fee Plan",
        "accounts": "账户 A/C",
        "period": "结算期间 Settlement Period",
        "calculation": "结算计算 Settlement Calculation",
        "account_breakdown": "账户级收费明细 Account-level Breakdown",
        "item": "项目 Item",
        "amount": "金额 Amount (HKD)",
        "beginning": "期初余额 Beginning",
        "contribution": "追加资金 Contribution",
        "withdrawal": "提款 Withdrawal",
        "net_contribution": "净资金变动 Net Contribution",
        "closing": "期末余额 Closing",
        "gain_loss": "本期盈亏 Gain / Loss",
        "period_rate": "期间收益率 Period Rate",
        "days": "天数 Days（仅展示）",
        "original_hwm": "原始高水位 Original HWM",
        "adjusted_hwm": "调整后高水位 Adjusted HWM",
        "above_hwm": "可收费超额 Above Watermark",
        "fee_rate": "服务费率 Fee Rate",
        "service_fee": "应付服务费 Service Fee",
        "next_hwm": "下期高水位 Next HWM",
        "net_short": "净资金 Net Contribution",
        "above_short": "超出HWM Above HWM",
        "payment": "付款信息 Payment Information",
        "bank": "银行转账 Bank Transfer",
        "cheque": "支票 Cheque",
        "note": "本文件根据已最终确认的季度结算记录自动生成。",
        "page": "页 Page",
    }


def generate_settlement_pdf(
    *,
    settlement: QuarterlySettlement,
    output_path: Path,
    language: str = "zh",
    invoice: Invoice | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    labels = _labels(language)
    font = "Helvetica" if language == "en" else _register_chinese_font()
    bold_font = "Helvetica-Bold" if font == "Helvetica" else font
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "title",
        parent=styles["Title"],
        fontName=bold_font,
        fontSize=18,
        leading=24,
        textColor=BRAND,
        alignment=TA_LEFT,
        spaceAfter=5 * mm,
    )
    heading_style = ParagraphStyle(
        "heading",
        parent=styles["Heading2"],
        fontName=bold_font,
        fontSize=11,
        leading=15,
        textColor=BRAND,
        spaceBefore=4 * mm,
        spaceAfter=2 * mm,
    )
    normal_style = ParagraphStyle(
        "normal-custom",
        parent=styles["BodyText"],
        fontName=font,
        fontSize=9,
        leading=13,
        textColor=TEXT,
    )
    small_style = ParagraphStyle(
        "small",
        parent=normal_style,
        fontSize=7.5,
        leading=10,
        textColor=MUTED,
    )

    company = settlement.client.company
    account_numbers = ", ".join(line.account.account_number for line in settlement.account_lines)
    is_invoice = invoice is not None and settlement.service_fee_cents > 0

    def footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#D8E1E8"))
        canvas.line(18 * mm, 14 * mm, 192 * mm, 14 * mm)
        canvas.setFont(font, 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 9 * mm, labels["note"])
        canvas.drawRightString(192 * mm, 9 * mm, f"{labels['page']} {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=20 * mm,
        title=labels["title_invoice"] if is_invoice else labels["title_statement"],
        author=company.name if company else "Financial Fee System",
    )
    story: list = []

    header = Table(
        [
            [
                Paragraph(company.name if company else "Company", title_style),
                Paragraph(labels["title_invoice"] if is_invoice else labels["title_statement"], title_style),
            ]
        ],
        colWidths=[75 * mm, 99 * mm],
    )
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (1, 0), (1, 0), "RIGHT")]))
    story.extend([header, Spacer(1, 1.5 * mm)])
    if company and (company.address or company.contact):
        story.append(Paragraph(" | ".join(filter(None, [company.address, company.contact])), small_style))
    story.append(Spacer(1, 4 * mm))

    meta_rows = [
        [labels["client"], settlement.client.name, labels["platform"], settlement.platform.name],
        [labels["fee_plan"], settlement.fee_plan.name, labels["accounts"], account_numbers],
        [
            labels["period"],
            f"{settlement.start_date:%d/%m/%Y} - {settlement.closing_date:%d/%m/%Y}",
            labels["invoice_no"] if is_invoice else "",
            invoice.invoice_number if is_invoice else "",
        ],
    ]
    if is_invoice:
        meta_rows.append(
            [labels["issue_date"], f"{invoice.issue_date:%d/%m/%Y}", labels["due_date"], f"{invoice.due_date:%d/%m/%Y}"]
        )
    meta = Table(meta_rows, colWidths=[39 * mm, 48 * mm, 39 * mm, 48 * mm])
    meta.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), font),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("BACKGROUND", (0, 0), (0, -1), PALE),
                ("BACKGROUND", (2, 0), (2, -1), PALE),
                ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
                ("TEXTCOLOR", (2, 0), (2, -1), MUTED),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D8E1E8")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(meta)

    if settlement.calculation_mode == "ACCOUNT_HWM":
        account_rows = [[labels["accounts"], labels["closing"], labels["net_short"], labels["above_short"], labels["service_fee"]]]
        for line in settlement.account_lines:
            account_rows.append(
                [
                    line.account.account_number,
                    _money(line.closing_cents),
                    _money(line.net_contribution_cents or 0),
                    _money(line.chargeable_above_hwm_cents or 0),
                    _money(line.service_fee_cents or 0),
                ]
            )
        account_table = Table(account_rows, colWidths=[42 * mm, 33 * mm, 33 * mm, 33 * mm, 33 * mm], repeatRows=1)
        account_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), font),
                    ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                    ("BACKGROUND", (0, 0), (-1, 0), BRAND),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCD8E0")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E2E8ED")),
                    ("TOPPADDING", (0, 0), (-1, -1), 4.5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
                ]
            )
        )
        story.extend([Paragraph(labels["account_breakdown"], heading_style), account_table])

    story.append(Paragraph(labels["calculation"], heading_style))
    rate = "N/A" if settlement.period_rate_ppm is None else f"{settlement.period_rate_ppm / 10_000:.2f}%"
    fee_rate = f"{settlement.fee_rate_bps / 100:.2f}%"
    calculation_rows = [
        [labels["item"], labels["amount"]],
        [labels["beginning"], _money(settlement.beginning_cents)],
        [labels["contribution"], _money(settlement.contribution_cents)],
        [labels["withdrawal"], _money(settlement.withdrawal_cents)],
        [labels["net_contribution"], _money(settlement.net_contribution_cents)],
        [labels["closing"], _money(settlement.closing_cents)],
        [labels["gain_loss"], _money(settlement.gain_loss_cents)],
        [labels["period_rate"], rate],
        [labels["days"], str(settlement.days)],
        [labels["original_hwm"], _money(settlement.original_hwm_cents)],
        [labels["adjusted_hwm"], _money(settlement.adjusted_hwm_cents)],
        [labels["above_hwm"], _money(settlement.chargeable_above_hwm_cents)],
        [labels["fee_rate"], fee_rate],
        [labels["service_fee"], _money(settlement.service_fee_cents)],
        [labels["next_hwm"], _money(settlement.next_hwm_cents)],
    ]
    calculations = Table(calculation_rows, colWidths=[105 * mm, 69 * mm], repeatRows=1)
    calculations.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), font),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 0), (-1, 0), BRAND),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ALIGN", (1, 1), (1, -1), "RIGHT"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
                ("LINEBELOW", (0, -2), (-1, -2), 0.8, ACCENT),
                ("FONTNAME", (0, -2), (-1, -2), bold_font),
                ("FONTNAME", (0, -1), (-1, -1), bold_font),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCD8E0")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E2E8ED")),
                ("TOPPADDING", (0, 0), (-1, -1), 5.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.append(calculations)

    if is_invoice:
        payment_blocks = [Paragraph(labels["payment"], heading_style)]
        payment_rows = []
        if company.bank_information:
            payment_rows.append([labels["bank"], Paragraph(company.bank_information.replace("\n", "<br/>"), normal_style)])
        if company.cheque_information:
            payment_rows.append([labels["cheque"], Paragraph(company.cheque_information.replace("\n", "<br/>"), normal_style)])
        if not payment_rows:
            payment_rows.append([labels["payment"], Paragraph("-", normal_style)])
        payment_table = Table(payment_rows, colWidths=[43 * mm, 131 * mm])
        payment_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), font),
                    ("BACKGROUND", (0, 0), (0, -1), PALE),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D8E1E8")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )
        payment_blocks.append(payment_table)
        story.append(KeepTogether(payment_blocks))

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return output_path
