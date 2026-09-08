from __future__ import annotations

from datetime import date
import re
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

from app.services.pdf_invoice import generate_invoice_pdf


def _notice(*, bank="Bank: Example Bank\nAccount: 000-123-456", cheque=None):
    # No settlement, lines or fee plan: customer rendering needs only these fields.
    return SimpleNamespace(
        company=SimpleNamespace(
            name="Example <Company> & Partners",
            address="18 Example Street", contact="billing@example.test",
            bank_information=bank, cheque_information=cheque,
        ),
        client=SimpleNamespace(name="Example <Client> & Family"),
        amount_cents=128050, due_date=date(2026, 9, 30),
    )


@pytest.mark.parametrize("language", ["zh", "en"])
def test_notice_uses_total_without_calculation_data(tmp_path, language) -> None:
    path = generate_invoice_pdf(
        invoice=_notice(), output_path=tmp_path / "notice.pdf", language=language,
    )
    pages = PdfReader(path).pages
    assert len(pages) == 1
    text = " ".join(pages[0].extract_text().split())
    assert "Example <Company> & Partners" in text
    assert "Example <Client> & Family" in text
    assert text.count("HKD 1,280.50") == 1
    assert "000-123-456" in text
    assert "Cheque" not in text and "支票" not in text


@pytest.mark.parametrize(
    "bank,cheque,expected,absent",
    [(None, "Payable to Example Company", "Cheque", "Bank transfer"),
     ("  ", None, "Please contact the company", "Bank transfer"),
     ("Bank: Example Bank", " ", "Bank transfer", "Cheque")],
)
def test_notice_only_offers_configured_payment_methods(tmp_path, bank, cheque, expected, absent) -> None:
    path = generate_invoice_pdf(
        invoice=_notice(bank=bank, cheque=cheque),
        output_path=tmp_path / "notice.pdf", language="en",
    )
    text = PdfReader(path).pages[0].extract_text()
    assert expected in text
    assert absent not in text


@pytest.mark.parametrize("language", ["zh", "en"])
def test_long_payment_instructions_flow_across_pages_without_lost_lines(tmp_path, language) -> None:
    instructions = [f"Transfer instruction {index:03}: Example bank details." for index in range(100)]
    notice = _notice(bank="\n".join(instructions), cheque="Payable to Example Company")
    notice.company.name = "香港示例財務顧問有限公司 " * 5
    notice.client.name = "Example Client " * 10
    path = generate_invoice_pdf(invoice=notice, output_path=tmp_path / "long.pdf", language=language)
    pages = PdfReader(path).pages
    assert len(pages) > 1
    # The first page must use its remaining space, not strand the payment
    # heading above an entire instruction block deferred to the next page.
    assert instructions[0] in " ".join(pages[0].extract_text().split())
    assert "Payable to Example Company" in pages[-1].extract_text()
    text = " ".join(" ".join(page.extract_text() for page in pages).split())
    for line in instructions:
        assert line in text
    assert "Payable to Example Company" in text
    assert text.count("HKD 1,280.50") == 1


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("long_field_first", [False, True])
def test_labelled_bank_details_preserve_long_values_and_unstructured_text(tmp_path, language, long_field_first) -> None:
    long_holder = "示例戶名" * 900
    bank_lines = ["Bank: Example <Bank> & Partners", f"Account name: {long_holder}"]
    if long_field_first:
        bank_lines.reverse()
    notice = _notice(
        bank=(
            "\n".join(bank_lines) + "\n"
            "Account number: 000-123-456\n"
            "https://example.test/pay?ref=A&B\n"
            "Reference: keep this free-text instruction."
        ),
        cheque="Payable to Example Company",
    )
    path = generate_invoice_pdf(invoice=notice, output_path=tmp_path / "bank.pdf", language=language)
    pages = PdfReader(path).pages
    text = " ".join(" ".join(page.extract_text() for page in pages).split())
    assert len(pages) > 1
    assert "示例戶名" in "".join(pages[0].extract_text().split())
    assert "Bank: Example <Bank> & Partners" in text
    # Continuation page numbers are separate from the bank field content.
    body_text = "".join(re.sub(r"(?m)^(?:Page|頁) \d+\s*$", "", page.extract_text()) for page in pages)
    assert long_holder in "".join(body_text.split())
    assert "Account number: 000-123-456" in text
    assert "https://example.test/pay?ref=A&B" in text
    assert "Reference: keep this free-text instruction." in text
    assert "Payable to Example Company" in pages[-1].extract_text()
    assert text.count("HKD 1,280.50") == 1
