from __future__ import annotations

import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import mean

import pymupdf as fitz
import pytesseract
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError
from pytesseract import Output

from ..config import application_root, get_settings, installation_root
from .ocr_runtime import OcrRuntimeError, check_tesseract, prepare_windows_runtime


OCR_PARSER_VERSION = "2.0"
DOCUMENT_TYPE_LABELS = {
    "empf_account_page": "eMPF账户余额页面",
    "contribution_record": "eMPF供款记录详情",
    "contribution_asset_transfer_record": "供款/资产转入记录",
    "unknown": "未知或不支持的文件",
}


@dataclass
class ParsedStatement:
    document_type: str = "unknown"
    document_details: dict = field(default_factory=dict)
    validation_checks: list[dict] = field(default_factory=list)
    client_name: str | None = None
    account_number: str | None = None
    scheme_name: str | None = None
    trustee: str | None = None
    currency: str = "HKD"
    as_of_date: date | None = None
    total_balance: str | None = None
    lifetime_net_contributions: str | None = None
    lifetime_gain_loss: str | None = None
    holdings: list[dict] = field(default_factory=list)
    confidence: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    raw_text: str = ""

    def extracted_dict(self) -> dict:
        result = asdict(self)
        result.pop("confidence", None)
        result.pop("warnings", None)
        result.pop("raw_text", None)
        if self.as_of_date:
            result["as_of_date"] = self.as_of_date.isoformat()
        return result


def _configure_tesseract() -> str | None:
    settings = get_settings()
    candidates = [
        settings.tesseract_cmd,
        installation_root() / "Tesseract-OCR" / "tesseract.exe",
        application_root() / "Tesseract-OCR" / "tesseract.exe",
        application_root() / "tools" / "Tesseract-OCR" / "tesseract.exe",
        shutil.which("tesseract"),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            executable = Path(candidate)
            if os.name == "nt":
                # Keep native binaries on the business-data drive, outside
                # the database and full data package, using an ASCII path.
                cache_root = settings.ocr_cache_root or (
                    Path(settings.data_root.resolve().anchor) / "FinancialFeeSystem-OCR"
                )
                executable = prepare_windows_runtime(executable, cache_root)
                check_tesseract(executable)
                # pytesseract splits config with posix=False on Windows and
                # retains quotes around --tessdata-dir. Use the native runtime's
                # environment setting instead, including for paths with spaces.
                os.environ["TESSDATA_PREFIX"] = str(executable.parent / "tessdata")
            pytesseract.pytesseract.tesseract_cmd = str(executable)
            return str(executable)
    return None


def _open_document(path: Path) -> Image.Image:
    if path.suffix.lower() == ".pdf":
        doc = fitz.open(path)
        if not doc.page_count:
            raise ValueError("PDF没有可读取页面")
        page = doc.load_page(0)
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        doc.close()
        return image
    with Image.open(path) as image:
        return image.convert("RGB")


def _open_pdf_pages(path: Path, max_pages: int = 20) -> tuple[list[Image.Image], bool]:
    document = fitz.open(path)
    try:
        if not document.page_count:
            raise ValueError("PDF没有可读取页面")
        pages: list[Image.Image] = []
        for page_number in range(min(document.page_count, max_pages)):
            page = document.load_page(page_number)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            pages.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
        return pages, document.page_count > max_pages
    finally:
        document.close()


def _pdf_text_layer(path: Path, max_pages: int = 100) -> tuple[str, bool]:
    """Extract a native PDF text layer before considering raster OCR."""

    document = fitz.open(path)
    try:
        if not document.page_count:
            raise ValueError("PDF没有可读取页面")
        text = "\n".join(
            document.load_page(page_number).get_text("text") or ""
            for page_number in range(min(document.page_count, max_pages))
        )
        return text, document.page_count > max_pages
    finally:
        document.close()


def _preprocess(image: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(image)
    gray = ImageEnhance.Contrast(gray).enhance(1.45)
    return gray.filter(ImageFilter.SHARPEN)


def _ocr(image: Image.Image, psm: int = 11) -> tuple[str, float]:
    language = "eng"
    try:
        available = set(pytesseract.get_languages())
        requested = [name for name in ("eng", "chi_tra", "chi_sim") if name in available]
        if requested:
            language = "+".join(requested)
    except (OSError, pytesseract.TesseractError):
        language = "eng"
    config = f"--oem 3 --psm {psm}"
    data = pytesseract.image_to_data(image, lang=language, config=config, output_type=Output.DICT, timeout=30)
    words: list[str] = []
    confidences: list[float] = []
    for word, confidence in zip(data.get("text", []), data.get("conf", []), strict=False):
        word = str(word).strip()
        try:
            numeric_confidence = float(confidence)
        except (TypeError, ValueError):
            numeric_confidence = -1
        if word:
            words.append(word)
            if numeric_confidence >= 0:
                confidences.append(numeric_confidence / 100)
    text = pytesseract.image_to_string(image, lang=language, config=config, timeout=30)
    return text or " ".join(words), mean(confidences) if confidences else 0.0


def _money(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", value.replace(",", ""))
    if not cleaned or cleaned in {"-", "."}:
        return None
    try:
        return f"{float(cleaned):.2f}"
    except ValueError:
        return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in (
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y-%m-%d",
        "%d %b, %Y",
        "%d %B, %Y",
    ):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _first(patterns: list[str], text: str, flags: int = re.IGNORECASE | re.DOTALL) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            return match.group(1).strip(" |:\n\r\t")
    return None


def _classify_document(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).casefold()
    if re.search(r"contribution\s*/\s*asset\s+transfer-in\s+record", normalized):
        return "contribution_asset_transfer_record"
    if "contribution record details" in normalized and (
        "billing information" in normalized or "fund transaction history" in normalized
    ):
        return "contribution_record"
    account_page_markers = (
        "total balance",
        "my current holdings",
        "net contributions",
        "investment gain",
    )
    if sum(marker in normalized for marker in account_page_markers) >= 3:
        return "empf_account_page"
    return "unknown"


def _document_details(text: str, document_type: str) -> dict:
    flattened = re.sub(r"\s+", " ", text)
    details: dict[str, str] = {}

    def capture(key: str, patterns: list[str]) -> None:
        value = _first(patterns, flattened)
        if value:
            details[key] = re.sub(r"\s+", " ", value).strip()

    capture("account_type", [r"Account\s+type\s*:\s*(.+?)(?=\s+Account\s+number|\s+Update\s+Personal)"])
    capture("employer_name", [r"Employer\s+name\s*:\s*(.+?)(?=\s+Print\s+date)"])
    capture("print_date", [r"Print\s+date\s*:\s*(\d{1,2}\s+[A-Za-z]{3,9},\s*\d{4})"])
    capture(
        "contribution_period",
        [r"Contribution\s+Period\s+.*?(\d{1,2}/\d{1,2}/\d{4}\s*-\s*\d{1,2}/\d{1,2}/\d{4})"],
    )
    capture("bill_number", [r"Bill\s+No\.\s*([A-Z0-9-]{8,})"])
    capture("bill_issue_date", [r"Bill\s+Issue\s+Date.*?(\d{1,2}/\d{1,2}/\d{4})"])
    capture("billing_amount", [r"Billing\s+Amount\s*\(HKD\).*?\$?\s*([0-9][0-9, ]*(?:\.\s*\d{2})?)"])
    capture("submission_reference", [r"Submission\s+Reference\s+No\.\s*([A-Z0-9-]{8,})"])
    grand_total_tail = _first([r"Grand\s+total\s+(.+?)(?:\s+\d+\s*/\s*\d+\s*$|$)"], flattened)
    if grand_total_tail:
        amounts = re.findall(r"[0-9][0-9, ]*\.\s*\d{2}", grand_total_tail)
        if amounts:
            normalized_total = _money(amounts[-1])
            if normalized_total:
                details["grand_total"] = normalized_total
    if details.get("billing_amount"):
        normalized_billing = _money(details["billing_amount"])
        if normalized_billing:
            details["billing_amount"] = normalized_billing
    if document_type == "contribution_record":
        contribution_header = re.search(
            r"Contribution\s+Period\s+Company\s+Name\s+Employer\s+Account\s+No\.\s+"
            r"(\d{1,2}/\d{1,2}/\d{4}\s*-\s*\d{1,2}/\d{1,2}/\d{4})\s+"
            r"(.+?)\s+(\d{6,})\s+Billing\s+Information",
            flattened,
            re.IGNORECASE,
        )
        if contribution_header:
            details["contribution_period"] = contribution_header.group(1)
            details["company_name"] = contribution_header.group(2).strip()
            details["employer_account_number"] = contribution_header.group(3)
        bill_number = _first([r"\b(BLD[A-Z0-9-]{8,})\b"], flattened)
        if bill_number:
            details["bill_number"] = bill_number
        if re.search(r"\bFully\s+Paid\b", flattened, re.IGNORECASE):
            details["contribution_status"] = "Fully Paid"
        if re.search(
            r"Bill\s+Type\s+Contribution\s+Frequency\s+Billing\s+Amount.*?"
            r"Lump\s+Sum\s+Lump\s+Sum",
            flattened,
            re.IGNORECASE,
        ):
            details["bill_type"] = "Lump Sum"
            details["contribution_frequency"] = "Lump Sum"
        settled = _money(
            _first(
                [r"Settled\s+Special\s+Voluntary\s+Contributions\s*\(HKD\).*?\$?\s*([0-9][0-9, ]*(?:\.\s*\d{2})?)"],
                flattened,
            )
        )
        outstanding = _money(
            _first(
                [r"Outstanding\s+Special\s+Voluntary\s+Contributions\s*\(HKD\).*?\$?\s*([0-9][0-9, ]*(?:\.\s*\d{2})?)"],
                flattened,
            )
        )
        if settled:
            details["settled_amount"] = settled
        if outstanding:
            details["outstanding_amount"] = outstanding
        fund_transaction = re.search(
            r"\b([A-Z]{3,8})\s+((?:BCT\s*\(Pro\)\s*MPF|Manulife\s+MPF).+?Fund)\s+"
            r"(\d{1,2}/\d{1,2}/\d{4})\s+([0-9]+(?:\.\d+)?)\s+"
            r"([0-9]+(?:\.\d+)?)\s+\$?\s*([0-9][0-9, ]*(?:\.\s*\d{2})?)",
            flattened,
            re.IGNORECASE,
        )
        if fund_transaction:
            details.update(
                {
                    "fund_code": fund_transaction.group(1).upper(),
                    "fund_name": re.sub(r"\s+", " ", fund_transaction.group(2)).strip(),
                    "fund_allocation_date": fund_transaction.group(3),
                    "fund_units": fund_transaction.group(4),
                    "fund_price": fund_transaction.group(5),
                    "fund_total_amount": _money(fund_transaction.group(6)),
                }
            )
    if document_type == "contribution_asset_transfer_record":
        transaction_count = len(re.findall(r"\bRegular\s+contribution\b", flattened, re.IGNORECASE))
        details["transaction_count"] = str(transaction_count)
    return details


def _transaction_document(text: str, confidence: float, document_type: str) -> ParsedStatement:
    flattened = re.sub(r"\s+", " ", text)
    account_number = _first(
        [
            r"Account\s+number\s*:\s*([A-Za-z0-9-]{5,})",
            r"Member\s+Account\s+No\.?\s*:\s*([A-Za-z0-9-]{5,})",
        ],
        flattened,
    )
    client_name = _first(
        [r"Member\s+name\s*:\s*([A-Za-z][A-Za-z .'-]{1,100}?)(?=\s+Employer\s+name)"],
        flattened,
    )
    scheme_name = _first(
        [r"(BCT\s*\(MPF\)\s*Pro\s*Choice)", r"Scheme\s*:\s*(.+?)(?=\s+Member|\s+Trustee)"],
        flattened,
    )
    trustee = _first(
        [
            r"(Bank\s+Consortium\s+Trust\s+Company\s+Limited)",
            r"(Manulife\s+Provident\s+Funds\s+Trust\s+Company\s+Limited)",
        ],
        flattened,
    )
    parsed = ParsedStatement(
        document_type=document_type,
        document_details=_document_details(text, document_type),
        client_name=client_name,
        account_number=account_number,
        scheme_name=scheme_name,
        trustee=trustee,
        raw_text=text,
    )
    parsed.confidence = {
        "client_name": round(confidence if client_name else 0.0, 3),
        "account_number": round(confidence if account_number else 0.0, 3),
        "as_of_date": 0.0,
        "total_balance": 0.0,
    }
    parsed.warnings = [
        "该文件是供款/交易记录，不是账户余额页面；已阻止生成余额快照。",
        "请在资金与余额模块按原始凭证人工复核后登记交易。",
    ]
    if confidence < 0.85:
        parsed.warnings.append("文档文字识别置信度低于85%，请核对交易凭证原件")
    return parsed


def _fund_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _extract_holdings(
    text: str,
    total_balance: str | None,
    lifetime_gain: str | None,
    as_of_date: date | None,
) -> list[dict]:
    flattened = re.sub(r"\s+", " ", text)
    contribution_section = _first(
        [r"My\s+Current\s+Holdings\s+by\s+Contribution\s+Type.*?(.*?)(?=My\s+Current\s+Holdings\s+Overview|Investment\s+Mandate|$)"],
        flattened,
    ) or ""
    summary_section = flattened.split("My Current Holdings by Contribution Type", 1)[0]
    fund_pattern = re.compile(
        r"((?:Manulife\s+MPF|BCT\s*\(Pro\)\s*MPF)\s+[A-Za-z0-9()&/,'+\-\s]{1,110}?\s+Fund(?:\s*\(DIS\))?)"
        r"\s+\$?\s*(-?\s*[0-9][0-9,\s]*\.\s*\d{2})",
        re.IGNORECASE,
    )
    by_fund: dict[str, dict] = {}
    # Tiled OCR preserves table rows much better than a globally flattened
    # document. Parse complete line items first so adjacent fund names cannot
    # be joined into one synthetic holding.
    sources = [line.strip() for line in text.splitlines() if line.strip()]
    sources.extend((summary_section, contribution_section))
    for source in sources:
        for match in fund_pattern.finditer(source):
            fund_name = re.sub(r"\s+", " ", match.group(1)).strip()
            if fund_name.casefold().count("manulife mpf") > 1:
                continue
            if fund_name.casefold().count("bct (pro) mpf") > 1:
                continue
            market_value = _money(match.group(2))
            if not market_value:
                continue
            key = _fund_key(fund_name)
            by_fund.setdefault(
                key,
                {
                    "fund_name": fund_name,
                    "market_value": market_value,
                    "investment_gain_loss": None,
                    "portfolio_percent": None,
                    "units": None,
                    "unit_price": None,
                    "mandatory_contributions": None,
                    "voluntary_contributions": None,
                    "balance_as_of": as_of_date.isoformat() if as_of_date else None,
                },
            )

    # The compact BCT one-fund row is reliably ordered and provides the full
    # holdings detail. Preserve that higher-fidelity extraction.
    bct_name = _first([r"(BCT\s*\(Pro\)\s*MPF\s*Conservative\s*Fund)"], flattened)
    if bct_name and total_balance:
        detail_match = re.search(
            r"100(?:\.0+)?%\s+([0-9]+(?:\.[0-9]+)?)\s+\$?\s*([0-9]+(?:\.[0-9]+)?)",
            flattened,
            re.IGNORECASE,
        )
        contribution_match = re.search(
            r"BCT\s*\(Pro\)\s*MPF\s*Conservative\s*Fund\s+"
            r"\$?\s*([0-9][0-9,\s]*\.\s*\d{2})\s+"
            r"\$?\s*([0-9][0-9,\s]*\.\s*\d{2})\s+"
            r"\$?\s*([0-9][0-9,\s]*\.\s*\d{2})",
            contribution_section,
            re.IGNORECASE,
        )
        row = by_fund.setdefault(
            _fund_key(bct_name),
            {
                "fund_name": bct_name,
                "market_value": total_balance,
                "investment_gain_loss": lifetime_gain,
                "portfolio_percent": "100.00",
                "units": None,
                "unit_price": None,
                "mandatory_contributions": None,
                "voluntary_contributions": None,
                "balance_as_of": as_of_date.isoformat() if as_of_date else None,
            },
        )
        row.update(
            {
                "market_value": total_balance,
                "investment_gain_loss": lifetime_gain,
                "portfolio_percent": "100.00",
                "units": detail_match.group(1) if detail_match else None,
                "unit_price": detail_match.group(2) if detail_match else None,
                "mandatory_contributions": (
                    _money(contribution_match.group(2)) if contribution_match else None
                ),
                "voluntary_contributions": (
                    _money(contribution_match.group(3)) if contribution_match else None
                ),
            }
        )
    return list(by_fund.values())


def _extract(
    text: str,
    confidence: float,
    *,
    regions: dict[str, tuple[str, float]] | None = None,
) -> ParsedStatement:
    regions = regions or {}
    header_text, header_confidence = regions.get("header", (text, confidence))
    info_text, _ = regions.get("info", (text, confidence))
    total_text, total_confidence = regions.get("total", (text, confidence))
    net_text, _ = regions.get("net", (text, confidence))
    gain_text, _ = regions.get("gain", (text, confidence))
    holding_text, _ = regions.get("holding", (text, confidence))
    compact = re.sub(r"[ \t]+", " ", text)
    flattened = re.sub(r"\s+", " ", text)
    header_flattened = re.sub(r"\s+", " ", header_text)
    info_flattened = re.sub(r"\s+", " ", info_text)
    total_flattened = re.sub(r"\s+", " ", total_text)
    net_flattened = re.sub(r"\s+", " ", net_text)
    gain_flattened = re.sub(r"\s+", " ", gain_text)
    holding_flattened = re.sub(r"\s+", " ", holding_text)
    money_pattern = r"([0-9][0-9,\s]*\.\s*\d{2})"
    account_number = _first(
        [r"Member\s+Account\s+No\.?\s*[:：]?\s*(\d{5,})", r"會員帳戶號碼\s*[:：]?\s*(\d{5,})"],
        header_flattened,
    )
    client_name = _first(
        [
            r"Name\s*[:：]\s*([A-Za-z][A-Za-z .'-]{1,80}?)(?=\s*[^\w\s]{0,4}\s*(?:Scheme|Account|$))",
            r"Member\s+Account\s+No\.?\s*[:：]?\s*\d+\s*[|Il]?\s*Name\s*[:：]\s*([A-Za-z][A-Za-z .'-]{1,80}?)(?=\s+(?:Scheme|Account|眼|$))",
            r"Name\s*[:：]\s*([A-Za-z][A-Za-z .'-]{1,80}?)(?=\s+(?:Scheme|Account|$))",
        ],
        header_flattened,
    )
    scheme_name = _first(
        [
            r"Scheme\s*[:：]\s*(.+?)(?=\s*\(Member\s+Account)",
            r"Scheme\s+Name\s+(.+?)(?=\s+(?:Total\s+Balance|Net\s+Contributions|Investment\s+gain))",
        ],
        header_flattened + " " + info_flattened,
    )
    trustee = _first(
        [
            r"(Bank\s+Consortium\s+Trust\s+Company\s+Limited)",
            r"(Manulife\s+Provident\s+Funds\s+Trust\s+Company\s+Limited)",
            r"Trustee\s+(.+?)(?=\s+Scheme\s+Name)",
        ],
        info_flattened,
    )
    # The trustee line is rendered in very small type on long eMPF screenshots
    # and commonly loses its final word in OCR. The scheme name is stable and
    # provides an unambiguous fallback for the two supported scheme families.
    scheme_identity = f"{scheme_name or ''} {flattened}".casefold()
    if not trustee and "manulife global select (mpf) scheme" in scheme_identity:
        trustee = "Manulife Provident Funds Trust Company Limited"
    if not trustee and "bct (mpf) pro choice" in scheme_identity:
        trustee = "Bank Consortium Trust Company Limited"
    total_balance = _money(
        _first(
            [
                rf"Total\s+Balance\s*\(HKD\).*?\$?\s*{money_pattern}",
                rf"Market\s+Value\s*\(HKD\).*?\$?\s*{money_pattern}",
            ],
            total_flattened + " " + flattened,
        )
    )
    lifetime_net = _money(
        _first(
            [rf"Net\s+Contributions\s*&?\s*Transfer-in\s+Amount\s*\(HKD\).*?\$?\s*{money_pattern}"],
            net_flattened + " " + flattened,
        )
    )
    lifetime_gain = _money(
        _first(
            [rf"Investment\s+gain\s*\(loss\).*?[▲△+\-]?\s*\$?\s*{money_pattern}"],
            gain_flattened + " " + flattened,
        )
    )
    as_of_raw = _first(
        [
            r"As\s+of\s+(\d{1,2}/\d{1,2}/\d{4})",
            r"as\s+of\s+(\d{1,2}/\d{1,2}/\d{4})",
            r"Balance\s+as\s+of\s+(\d{1,2}/\d{1,2}/\d{4})",
        ],
        total_flattened + " " + holding_flattened + " " + flattened,
    )
    as_of_date = _parse_date(as_of_raw)

    # The visual arrow before gain/loss is often read as punctuation. The
    # statement identity Total Balance = Net Contributions + Gain/Loss gives
    # the reliable sign without treating either lifetime value as cash flow.
    if total_balance and lifetime_net and lifetime_gain:
        reconciled_gain = round(float(total_balance) - float(lifetime_net), 2)
        if abs(abs(reconciled_gain) - abs(float(lifetime_gain))) <= 0.02:
            lifetime_gain = f"{reconciled_gain:.2f}"

    holdings = _extract_holdings(text, total_balance, lifetime_gain, as_of_date)

    parsed = ParsedStatement(
        document_type="empf_account_page",
        client_name=client_name,
        account_number=account_number,
        scheme_name=scheme_name,
        trustee=trustee,
        as_of_date=as_of_date,
        total_balance=total_balance,
        lifetime_net_contributions=lifetime_net,
        lifetime_gain_loss=lifetime_gain,
        holdings=holdings,
        raw_text=compact,
    )
    required = {
        "client_name": client_name,
        "account_number": account_number,
        "as_of_date": as_of_date,
        "total_balance": total_balance,
    }
    field_confidences = {
        "client_name": header_confidence,
        "account_number": header_confidence,
        "as_of_date": total_confidence,
        "total_balance": total_confidence,
    }
    parsed.confidence = {
        name: round(field_confidences[name] if value else 0.0, 3)
        for name, value in required.items()
    }
    for name, value in required.items():
        if not value:
            parsed.warnings.append(f"关键字段{name}未能识别，确认前必须人工补充")
        elif field_confidences[name] < 0.85:
            parsed.warnings.append(f"关键字段{name}置信度低于85%，请人工核对")
    if confidence < 0.85:
        parsed.warnings.append("OCR整体置信度低于85%，请逐项核对")
    if as_of_date and (as_of_date.month, as_of_date.day) not in {
        (3, 31),
        (6, 30),
        (9, 30),
        (12, 31),
    }:
        parsed.warnings.append("余额日期并非季末；除非该日为实际退出日，否则只能保存为余额快照")
    if lifetime_net and lifetime_gain and total_balance:
        expected = round(float(lifetime_net) + float(lifetime_gain), 2)
        difference = abs(expected - float(total_balance))
        parsed.validation_checks.append(
            {
                "check": "total_equals_lifetime_net_plus_gain_loss",
                "status": "PASSED" if difference <= 0.02 else "FAILED",
                "difference": f"{difference:.2f}",
            }
        )
        if difference > 0.02:
            parsed.warnings.append("累计净供款加累计盈亏与总余额不一致，仅作为参考数据保存")
    holding_values: list[Decimal] = []
    for holding in holdings:
        try:
            holding_values.append(Decimal(str(holding.get("market_value"))))
        except (InvalidOperation, TypeError, ValueError):
            holding_values = []
            break
    if total_balance and holdings and len(holding_values) == len(holdings):
        difference = abs(Decimal(total_balance) - sum(holding_values, Decimal("0")))
        holding_tolerance = Decimal("0.01")
        parsed.validation_checks.append(
            {
                "check": "total_equals_sum_of_holding_market_values",
                "status": "PASSED" if difference <= holding_tolerance else "FAILED",
                "difference": f"{difference:.2f}",
            }
        )
        if difference > holding_tolerance:
            parsed.warnings.append(
                f"持仓市值合计与总余额相差HKD {difference:.2f}，持仓表必须人工复核"
            )
    parsed.warnings.append("累计净供款及累计盈亏不会自动写入季度Contribution或Gain/Loss")
    return parsed


def _ocr_page_text(image: Image.Image) -> tuple[str, float]:
    """OCR a page and add overlapping bands for unusually long screenshots."""

    text, confidence = _ocr(image, psm=11)
    width, height = image.size
    if height <= width * 2.2:
        return text, confidence
    tile_height = max(int(width * 1.6), 1200)
    overlap = max(int(width * 0.12), 100)
    step = max(tile_height - overlap, 1)
    tiled_text: list[str] = []
    tiled_confidences: list[float] = []
    for top in range(0, height, step):
        bottom = min(top + tile_height, height)
        tile_text, tile_confidence = _ocr(image.crop((0, top, width, bottom)), psm=6)
        if tile_text.strip():
            tiled_text.append(tile_text)
        if tile_confidence > 0:
            tiled_confidences.append(tile_confidence)
        if bottom >= height:
            break
    combined = "\n".join([text, *tiled_text])
    scores = [score for score in [confidence, *tiled_confidences] if score > 0]
    return combined, mean(scores) if scores else 0.0


def _unsupported_document(text: str, confidence: float) -> ParsedStatement:
    parsed = ParsedStatement(document_type="unknown", raw_text=text)
    parsed.confidence = {
        "client_name": 0.0,
        "account_number": 0.0,
        "as_of_date": 0.0,
        "total_balance": 0.0,
    }
    parsed.warnings = [
        "无法确认该文件是受支持的账户余额页面或供款记录，已禁止生成余额快照。",
        "请人工检查原文件类型及关键字段。",
    ]
    if confidence < 0.85:
        parsed.warnings.append("文档文字识别置信度低于85%，请核对原件")
    return parsed


def parse_empf_statement(path: Path) -> ParsedStatement:
    try:
        is_pdf = path.suffix.casefold() == ".pdf"
        if is_pdf:
            native_text, native_truncated = _pdf_text_layer(path)
            if len(re.sub(r"\s+", "", native_text)) >= 80:
                document_type = _classify_document(native_text)
                if document_type in {
                    "contribution_record",
                    "contribution_asset_transfer_record",
                }:
                    parsed = _transaction_document(native_text, 0.995, document_type)
                elif document_type == "empf_account_page":
                    parsed = _extract(native_text, 0.995)
                else:
                    parsed = _unsupported_document(native_text, 0.995)
                if native_truncated:
                    parsed.warnings.append("PDF超过100页；本次只读取前100页文字，必须人工核对其余页面")
                return parsed

        if not _configure_tesseract():
            parsed = ParsedStatement()
            parsed.warnings = [
                "未检测到Tesseract OCR。文件已安全保存，可在安装OCR后重新识别或人工录入字段。",
                "关键字段未识别时禁止确认入账。",
            ]
            return parsed

        if is_pdf:
            raw_pages, truncated = _open_pdf_pages(path)
        else:
            raw_pages, truncated = [_open_document(path)], False
        pages = [_preprocess(page) for page in raw_pages]
        page_results = [_ocr_page_text(page) for page in pages]
        text = "\n\n--- PDF PAGE ---\n\n".join(value[0] for value in page_results)
        scores = [value[1] for value in page_results if value[1] > 0]
        confidence = mean(scores) if scores else 0.0
        document_type = _classify_document(text)
        if document_type in {"contribution_record", "contribution_asset_transfer_record"}:
            parsed = _transaction_document(text, confidence, document_type)
        elif document_type == "unknown":
            parsed = _unsupported_document(text, confidence)
        else:
            first_page = pages[0]
            width, height = first_page.size
            region_boxes = {
                "header": (0, 0, width, int(height * 0.15)),
                "info": (0, int(height * 0.06), width, int(height * 0.22)),
                "total": (int(width * 0.02), int(height * 0.08), int(width * 0.38), int(height * 0.32)),
                "net": (int(width * 0.30), int(height * 0.08), int(width * 0.72), int(height * 0.32)),
                "gain": (int(width * 0.62), int(height * 0.08), int(width * 0.99), int(height * 0.32)),
                "holding": (0, int(height * 0.20), width, int(height * 0.88)),
            }
            regions = {
                name: _ocr(first_page.crop(box), psm=6)
                for name, box in region_boxes.items()
            }
            parsed = _extract(text, confidence, regions=regions)
        if truncated:
            parsed.warnings.append("PDF超过20页；扫描OCR只处理前20页，必须人工核对其余页面")
        return parsed
    except OcrRuntimeError as exc:
        return ParsedStatement(warnings=[str(exc), "关键字段未识别时禁止确认入账；本次未生成余额快照。"])
    except (OSError, RuntimeError, ValueError, UnidentifiedImageError, pytesseract.TesseractError):
        parsed = ParsedStatement()
        parsed.warnings = [
            "文件无法正常识别。原文件已安全保存，请检查格式后重新识别或人工录入。",
            "关键字段未识别时禁止确认入账。",
            "累计净供款及累计盈亏不会自动写入季度Contribution或Gain/Loss",
        ]
        return parsed
