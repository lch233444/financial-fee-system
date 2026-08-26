from __future__ import annotations

from app.services.statement_parser import _classify_document, _extract, _transaction_document


def test_empf_text_mapping_keeps_lifetime_values_separate() -> None:
    text = """
    Member Account No.: 24681357 | Name: SAMPLE CLIENT
    Scheme: BCT (MPF) Pro Choice (Member Account No.: 24681357)
    Trustee Bank Consortium Trust Company Limited Scheme Name BCT (MPF) Pro Choice
    Total Balance (HKD) $ 9,736.57 As of 20/05/2026
    Net Contributions & Transfer-in Amount (HKD) $ 9,735.04
    Investment gain (loss) + $ 1.53
    My Current Holdings by Contribution Type (as of 20/05/2026)
    Fund Name Market Value (HKD) Mandatory Contributions(HKD) Voluntary Contributions(HKD)
    BCT (Pro) MPF Conservative Fund $ 9,736.57 $0.00 $0.00
    My Current Holdings Overview (as of 20/05/2026)
    BCT (Pro) MPF Conservative Fund $ 9,736.57 $ 1.53 100.00% 7697.50929 $ 1.2649 20/05/2026
    """
    parsed = _extract(text, 0.97)
    assert parsed.account_number == "24681357"
    assert parsed.client_name == "SAMPLE CLIENT"
    assert parsed.scheme_name == "BCT (MPF) Pro Choice"
    assert parsed.total_balance == "9736.57"
    assert parsed.lifetime_net_contributions == "9735.04"
    assert parsed.lifetime_gain_loss == "1.53"
    assert parsed.as_of_date.isoformat() == "2026-05-20"
    assert parsed.holdings[0]["mandatory_contributions"] == "0.00"
    assert parsed.holdings[0]["voluntary_contributions"] == "0.00"
    assert any("不会自动写入" in warning for warning in parsed.warnings)


def test_manulife_full_text_fallback_extracts_summary_and_fund_rows() -> None:
    text = """
    Special Voluntary Contribution Account
    Member Account No.: 12345678 | Name: TEST MEMBER
    Scheme: Manulife Global Select (MPF) Scheme (Member Account No.: 12345678)
    Trustee Manulife Provident Funds Trust Company Limited Scheme Name Manulife Global Select (MPF) Scheme
    Total Balance (HKD) $ 1,200,000.00 As of 31/12/2025
    Net Contributions & Transfer-in Amount (HKD) $ 1,000,000.00
    Investment gain (loss) $ 200,000.00
    My Current Holdings by Contribution Type (as of 31/12/2025)
    Fund Name Market Value (HKD)
    Manulife MPF Conservative Fund $ 900,000.00
    Manulife MPF Age 65 Plus Fund $ 300,000.00
    My Current Holdings Overview (as of 31/12/2025)
    """
    parsed = _extract(
        text,
        0.96,
        regions={
            "total": ("unrelated crop", 0.91),
            "net": ("unrelated crop", 0.91),
            "gain": ("unrelated crop", 0.91),
        },
    )
    assert parsed.document_type == "empf_account_page"
    assert parsed.total_balance == "1200000.00"
    assert parsed.lifetime_net_contributions == "1000000.00"
    assert parsed.lifetime_gain_loss == "200000.00"
    assert parsed.trustee == "Manulife Provident Funds Trust Company Limited"
    assert parsed.as_of_date.isoformat() == "2025-12-31"
    assert [holding["market_value"] for holding in parsed.holdings] == [
        "900000.00",
        "300000.00",
    ]
    assert {check["status"] for check in parsed.validation_checks} == {"PASSED"}
    assert not any("持仓市值合计" in warning for warning in parsed.warnings)


def test_document_classifier_routes_contribution_documents_away_from_balance() -> None:
    billing_text = """
    Contribution Record Details
    Member Account No.: 87654321
    Billing Information
    Billing Amount (HKD) $ 5,000.00
    Fund Transaction History
    """
    transfer_text = """
    Contribution / asset transfer-in record
    Account type: MPF Regular Employee Account
    Account number: 12345678-99999999
    Member name: SAMPLE MEMBER
    Employer name: SAMPLE LIMITED
    Print date: 15 Jan, 2026
    """
    assert _classify_document(billing_text) == "contribution_record"
    assert _classify_document(transfer_text) == "contribution_asset_transfer_record"
    parsed = _transaction_document(transfer_text, 0.99, "contribution_asset_transfer_record")
    assert parsed.account_number == "12345678-99999999"
    assert parsed.client_name == "SAMPLE MEMBER"
    assert parsed.document_details["employer_name"] == "SAMPLE LIMITED"
    assert parsed.total_balance is None
    assert any("已阻止生成余额快照" in warning for warning in parsed.warnings)
