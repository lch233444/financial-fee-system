from __future__ import annotations

from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFError, TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Flowable,
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..models import Invoice, QuarterlySettlement
from ..money import from_cents


BRAND = colors.HexColor("#173B57")
ACCENT = colors.HexColor("#E6A33A")
PALE = colors.HexColor("#F3F7FA")
TEXT = colors.HexColor("#1D2935")
MUTED = colors.HexColor("#657786")

INVOICE_NAVY = colors.HexColor("#102A43")
INVOICE_GOLD = colors.HexColor("#C7A35A")
INVOICE_PALE = colors.HexColor("#F7F5F0")
INVOICE_LINE = colors.HexColor("#D8E0E6")
INVOICE_TEXT = colors.HexColor("#1F2933")
INVOICE_MUTED = colors.HexColor("#657786")


class _InvoiceBrandMark(Flowable):
    """Small neutral geometric mark; it is decorative and not a company logo."""

    def __init__(self) -> None:
        super().__init__()
        self.width = 14 * mm
        self.height = 14 * mm

    def draw(self) -> None:
        canvas = self.canv
        canvas.saveState()
        canvas.setStrokeColor(INVOICE_GOLD)
        canvas.setLineWidth(1.4)
        canvas.translate(self.width / 2, self.height / 2)
        canvas.rotate(45)
        outer = 7.8 * mm
        inner = 4.2 * mm
        canvas.rect(-outer / 2, -outer / 2, outer, outer, stroke=1, fill=0)
        canvas.setLineWidth(0.8)
        canvas.rect(-inner / 2, -inner / 2, inner, inner, stroke=1, fill=0)
        canvas.restoreState()


def _numbered_invoice_canvas(font: str, footer_note: str):
    class NumberedInvoiceCanvas(Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._page_states: list[dict] = []

        def showPage(self) -> None:  # noqa: N802 - ReportLab API
            self._page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self) -> None:
            page_count = len(self._page_states)
            for state in self._page_states:
                self.__dict__.update(state)
                self._draw_invoice_footer(page_count)
                super().showPage()
            super().save()

        def _draw_invoice_footer(self, page_count: int) -> None:
            self.saveState()
            self.setStrokeColor(INVOICE_GOLD)
            self.setLineWidth(0.7)
            self.line(18 * mm, 14 * mm, 192 * mm, 14 * mm)
            self.setFont(font, 7)
            self.setFillColor(INVOICE_MUTED)
            self.drawString(18 * mm, 9 * mm, footer_note)
            self.drawRightString(
                192 * mm,
                9 * mm,
                f"Page {self._pageNumber} of {page_count}",
            )
            self.restoreState()

    return NumberedInvoiceCanvas


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
            except (OSError, TTFError):
                continue
    return "Helvetica"


def _money(cents: int) -> str:
    return f"HKD {from_cents(int(cents)):,.2f}"


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
            "quarter": "Billing Quarter",
            "calculation": "Settlement Calculation",
            "account_breakdown": "Account-level Service Fee Breakdown",
            "account_period": "Period",
            "closing_short": "Closing",
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
            "service_fee_short": "Service Fee",
            "total": "Total Service Fee Due",
            "payment": "Payment Information",
            "bank": "Bank Transfer",
            "cheque": "Cheque",
            "note": "This document is generated from the finalized quarterly settlement record.",
            "invoice_note": "This invoice is generated from frozen finalized settlement lines.",
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
        "quarter": "账单季度 Billing Quarter",
        "calculation": "结算计算 Settlement Calculation",
        "account_breakdown": "账户级收费明细 Account-level Breakdown",
        "account_period": "期间 Period",
        "closing_short": "期末 Closing",
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
        "net_short": "净资金 Net",
        "above_short": "超额 Above HWM",
        "service_fee_short": "服务费 Service Fee",
        "total": "应付服务费合计 Total",
        "payment": "付款信息 Payment Information",
        "bank": "银行转账 Bank Transfer",
        "cheque": "支票 Cheque",
        "note": "本文件根据已最终确认的季度结算记录自动生成。",
        "invoice_note": "本账单根据已冻结的Finalized Settlement账户明细生成。",
        "page": "页 Page",
    }


def generate_invoice_pdf(
    *,
    invoice: Invoice,
    output_path: Path,
    language: str = "zh",
) -> Path:
    """Render an Invoice only from its frozen ownership and InvoiceLine rows."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    company = invoice.company
    client = invoice.client
    fee_plan = invoice.fee_plan
    dynamic_text = "\n".join(
        str(value or "")
        for value in (
            company.name,
            company.address,
            company.contact,
            company.bank_information,
            company.cheque_information,
            client.name,
            fee_plan.name,
            invoice.invoice_number,
            *(line.platform_name_snapshot for line in invoice.lines),
            *(line.account_number_snapshot for line in invoice.lines),
        )
    )
    font = (
        _register_chinese_font()
        if language != "en" or not dynamic_text.isascii()
        else "Helvetica"
    )
    bold_font = "Helvetica-Bold" if font == "Helvetica" else font
    styles = getSampleStyleSheet()
    company_style = ParagraphStyle(
        "invoice-company",
        parent=styles["Heading1"],
        fontName=bold_font,
        fontSize=14,
        leading=18,
        textColor=INVOICE_NAVY,
        alignment=TA_LEFT,
        wordWrap="CJK",
        splitLongWords=True,
    )
    document_title_style = ParagraphStyle(
        "invoice-document-title",
        parent=styles["Title"],
        fontName=bold_font,
        fontSize=23,
        leading=25,
        textColor=INVOICE_NAVY,
        alignment=TA_RIGHT,
    )
    document_subtitle_style = ParagraphStyle(
        "invoice-document-subtitle",
        parent=styles["BodyText"],
        fontName=bold_font,
        fontSize=9,
        leading=12,
        textColor=INVOICE_GOLD,
        alignment=TA_RIGHT,
        spaceBefore=1.5 * mm,
    )
    section_style = ParagraphStyle(
        "invoice-section",
        parent=styles["Heading2"],
        fontName=bold_font,
        fontSize=10,
        leading=13,
        textColor=INVOICE_NAVY,
        spaceAfter=2 * mm,
    )
    body_style = ParagraphStyle(
        "invoice-body",
        parent=styles["BodyText"],
        fontName=font,
        fontSize=8.5,
        leading=12,
        textColor=INVOICE_TEXT,
        wordWrap="CJK",
        splitLongWords=True,
    )
    small_style = ParagraphStyle(
        "invoice-small",
        parent=body_style,
        fontSize=7.2,
        leading=9.5,
        textColor=INVOICE_MUTED,
    )
    label_style = ParagraphStyle(
        "invoice-label",
        parent=small_style,
        fontName=bold_font,
        textColor=INVOICE_MUTED,
    )
    value_style = ParagraphStyle(
        "invoice-value",
        parent=body_style,
        fontName=bold_font,
        textColor=INVOICE_NAVY,
    )
    invoice_number_style = ParagraphStyle(
        "invoice-number-value",
        parent=value_style,
        fontSize=7.2,
        leading=9.2,
        wordWrap="CJK",
        splitLongWords=True,
    )
    table_header_style = ParagraphStyle(
        "invoice-table-header",
        parent=small_style,
        fontName=bold_font,
        fontSize=7.3,
        leading=9.2,
        textColor=colors.white,
        alignment=TA_LEFT,
    )
    amount_header_style = ParagraphStyle(
        "invoice-amount-header",
        parent=table_header_style,
        alignment=TA_RIGHT,
    )
    amount_style = ParagraphStyle(
        "invoice-amount",
        parent=body_style,
        alignment=TA_RIGHT,
    )
    total_label_style = ParagraphStyle(
        "invoice-total-label",
        parent=body_style,
        fontName=bold_font,
        textColor=INVOICE_NAVY,
        alignment=TA_RIGHT,
    )
    total_amount_style = ParagraphStyle(
        "invoice-total-amount",
        parent=body_style,
        fontName=bold_font,
        fontSize=15,
        leading=18,
        textColor=INVOICE_NAVY,
        alignment=TA_RIGHT,
    )

    is_english = language == "en"
    copy = {
        "title": "INVOICE",
        "subtitle": "SERVICE FEE" if is_english else "服務費賬單",
        "bill_to": "BILL TO" if is_english else "客戶 / BILL TO",
        "sub_accounts": "Sub Account(s)" if is_english else "子賬戶 / SUB ACCOUNT",
        "invoice_no": "Invoice No." if is_english else "賬單編號 / INVOICE NO.",
        "issue_date": "Issue Date" if is_english else "出具日期 / ISSUE DATE",
        "due_date": "Due Date" if is_english else "付款到期日 / DUE DATE",
        "billing_period": "Billing Period" if is_english else "賬單季度 / BILLING PERIOD",
        "currency": "Currency" if is_english else "貨幣 / CURRENCY",
        "account": "SUB ACCOUNT" if is_english else "子賬戶 / SUB ACCOUNT",
        "period": "SETTLEMENT PERIOD" if is_english else "結算期間 / PERIOD",
        "description": "SERVICE DESCRIPTION" if is_english else "服務說明 / DESCRIPTION",
        "amount": "AMOUNT (HKD)" if is_english else "金額 (HKD) / AMOUNT",
        "service_fee": "Service Fee" if is_english else "服務費 / SERVICE FEE",
        "subtotal": "SUBTOTAL" if is_english else "小計 / SUBTOTAL",
        "total_due": "TOTAL DUE" if is_english else "應付總額\nTOTAL DUE",
        "payment": "PAYMENT INFORMATION" if is_english else "付款資料 / PAYMENT INFORMATION",
        "bank": "BANK TRANSFER" if is_english else "銀行轉賬 / BANK TRANSFER",
        "cheque": "CROSSED CHEQUE" if is_english else "劃線支票 / CROSSED CHEQUE",
        "reference": (
            "Please quote the invoice number with payment."
            if is_english
            else "付款時請註明賬單編號。"
        ),
        "footer": (
            "Generated from finalized settlement records."
            if is_english
            else "本賬單根據已確認的季度結算記錄生成。"
        ),
    }

    def p(value: object, style: ParagraphStyle = body_style) -> Paragraph:
        return Paragraph(escape(str(value or "-")).replace("\n", "<br/>"), style)

    def display_date(value) -> str:
        if value is None:
            return "-"
        return value.strftime("%d %b %Y") if is_english else value.strftime("%d/%m/%Y")

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=14 * mm,
        bottomMargin=20 * mm,
        title=copy["title"],
        author=company.name,
    )
    story: list = []

    company_details = " | ".join(filter(None, [company.address, company.contact]))
    header = Table(
        [
            [
                _InvoiceBrandMark(),
                (
                    [p(company.name, company_style), p(company_details, small_style)]
                    if company_details
                    else p(company.name, company_style)
                ),
                [
                    Paragraph(copy["title"], document_title_style),
                    Paragraph(copy["subtitle"], document_subtitle_style),
                ],
            ]
        ],
        colWidths=[17 * mm, 105 * mm, 52 * mm],
    )
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("ALIGN", (2, 0), (2, 0), "RIGHT"),
            ]
        )
    )
    story.extend(
        [
            header,
            Spacer(1, 3 * mm),
            HRFlowable(width="100%", thickness=0.9, color=INVOICE_GOLD),
            Spacer(1, 8 * mm),
        ]
    )

    account_numbers = ", ".join(
        line.account_number_snapshot
        for line in sorted(invoice.lines, key=lambda value: (value.display_order, value.id))
    )
    bill_rows = [
        [Paragraph(copy["bill_to"], section_style)],
        [HRFlowable(width="100%", thickness=0.4, color=INVOICE_LINE)],
        [p(client.name, value_style)],
        [p(f"{copy['sub_accounts']}: {account_numbers or '-'}", body_style)],
    ]
    if client.contact:
        bill_rows.append([p(client.contact, small_style)])
    bill_card = Table(bill_rows, colWidths=[72 * mm])
    bill_card.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("BOX", (0, 0), (-1, -1), 0.6, INVOICE_LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )

    meta_rows = [
        [
            p(copy["invoice_no"], label_style),
            p(invoice.invoice_number or "-", invoice_number_style),
        ],
        [p(copy["issue_date"], label_style), p(display_date(invoice.issue_date), value_style)],
        [p(copy["due_date"], label_style), p(display_date(invoice.due_date), value_style)],
        [
            p(copy["billing_period"], label_style),
            p(f"{invoice.year} Q{invoice.quarter}", value_style),
        ],
        [p(copy["currency"], label_style), p("HKD", value_style)],
    ]
    meta_card = Table(meta_rows, colWidths=[35 * mm, 57 * mm])
    meta_card.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LINEBELOW", (0, 0), (-1, -2), 0.35, INVOICE_LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 5.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5.5),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ]
        )
    )
    summary = Table([[bill_card, meta_card]], colWidths=[78 * mm, 96 * mm])
    summary.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("RIGHTPADDING", (0, 0), (0, 0), 6),
                ("LEFTPADDING", (1, 0), (1, 0), 6),
                ("RIGHTPADDING", (1, 0), (1, 0), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.extend([summary, Spacer(1, 8 * mm)])

    invoice_rows = [
        [
            Paragraph(copy["account"], table_header_style),
            Paragraph(copy["period"], table_header_style),
            Paragraph(copy["description"], table_header_style),
            Paragraph(copy["amount"], amount_header_style),
        ]
    ]
    for line in sorted(invoice.lines, key=lambda value: (value.display_order, value.id)):
        if line.start_date and line.closing_date:
            period = f"{line.start_date:%d/%m/%Y} - {line.closing_date:%d/%m/%Y}"
        else:
            period = "-"
        invoice_rows.append(
            [
                p(line.account_number_snapshot),
                p(period),
                p(f"{fee_plan.name}\n{copy['service_fee']}"),
                p(f"{from_cents(int(line.service_fee_cents)):,.2f}", amount_style),
            ]
        )
    invoice_table = Table(
        invoice_rows,
        colWidths=[43 * mm, 44 * mm, 53 * mm, 34 * mm],
        repeatRows=1,
    )
    invoice_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), font),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BACKGROUND", (0, 0), (-1, 0), INVOICE_NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ALIGN", (3, 1), (3, -1), "RIGHT"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, INVOICE_PALE]),
                ("BOX", (0, 0), (-1, -1), 0.5, INVOICE_LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, INVOICE_LINE),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.extend([invoice_table, Spacer(1, 5 * mm)])

    totals = Table(
        [
            [p(copy["subtotal"], total_label_style), p(_money(invoice.amount_cents), amount_style)],
            [p(copy["total_due"], total_label_style), p(_money(invoice.amount_cents), total_amount_style)],
        ],
        colWidths=[34 * mm, 48 * mm],
        hAlign="RIGHT",
    )
    totals.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 1), (-1, 1), 0.9, INVOICE_GOLD),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend([totals, Spacer(1, 8 * mm)])

    bank_text = company.bank_information or "-"
    cheque_text = company.cheque_information or "-"
    bank_block = [
        Paragraph(copy["bank"], section_style),
        p(bank_text),
    ]
    cheque_block = [
        Paragraph(copy["cheque"], section_style),
        p(cheque_text),
        Spacer(1, 2 * mm),
        p(copy["reference"], small_style),
    ]
    payment_table = Table([[bank_block, cheque_block]], colWidths=[87 * mm, 87 * mm])
    payment_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), INVOICE_PALE),
                ("BOX", (0, 0), (-1, -1), 0.5, INVOICE_LINE),
                ("LINEBEFORE", (1, 0), (1, 0), 0.5, INVOICE_LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
            ]
        )
    )
    payment_heading = Table(
        [[Paragraph(copy["payment"], section_style)]],
        colWidths=[174 * mm],
    )
    payment_heading.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, -1), 0.7, INVOICE_GOLD),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(KeepTogether([payment_heading, Spacer(1, 2 * mm), payment_table]))
    doc.build(
        story,
        canvasmaker=_numbered_invoice_canvas(font, copy["footer"]),
    )
    return output_path


def generate_settlement_pdf(
    *,
    settlement: QuarterlySettlement,
    output_path: Path,
    language: str = "zh",
    invoice: Invoice | None = None,
) -> Path:
    if invoice is not None:
        return generate_invoice_pdf(invoice=invoice, output_path=output_path, language=language)
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

    company = settlement.company
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
                Paragraph(escape(company.name) if company else "Company", title_style),
                Paragraph(labels["title_invoice"] if is_invoice else labels["title_statement"], title_style),
            ]
        ],
        colWidths=[75 * mm, 99 * mm],
    )
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (1, 0), (1, 0), "RIGHT")]))
    story.extend([header, Spacer(1, 1.5 * mm)])
    if company and (company.address or company.contact):
        story.append(
            Paragraph(escape(" | ".join(filter(None, [company.address, company.contact]))), small_style)
        )
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
        account_rows = [[labels["accounts"], labels["account_period"], labels["closing_short"], labels["net_short"], labels["above_short"], labels["service_fee_short"]]]
        for line in settlement.account_lines:
            account_rows.append(
                [
                    line.account.account_number,
                    f"{line.start_date:%d/%m/%Y} - {line.closing_date:%d/%m/%Y}",
                    _money(line.closing_cents),
                    _money(line.net_contribution_cents or 0),
                    _money(line.chargeable_above_hwm_cents or 0),
                    _money(line.service_fee_cents or 0),
                ]
            )
        account_table = Table(
            account_rows,
            colWidths=[29 * mm, 42 * mm, 25 * mm, 24 * mm, 27 * mm, 27 * mm],
            repeatRows=1,
        )
        account_table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), font),
                    ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                    ("FONTSIZE", (0, 0), (-1, 0), 6.5),
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
            payment_rows.append(
                [labels["bank"], Paragraph(escape(company.bank_information).replace("\n", "<br/>"), normal_style)]
            )
        if company.cheque_information:
            payment_rows.append(
                [labels["cheque"], Paragraph(escape(company.cheque_information).replace("\n", "<br/>"), normal_style)]
            )
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
