from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import (
    AuditEvent,
    BalanceSnapshot,
    Invoice,
    Payment,
    QuarterlySettlement,
    StatementImport,
    TransactionRecord,
)
from app.routes import statements
from app.routes.ai_assistant import FINANCIAL_REQUEST_HEADER
from app.schemas import StatementHoldingInput
from app.services.entity_ids import allocate_entity_id
from app.services.codex_app_server import (
    FIXED_AI_MODEL,
    CodexAuthenticationError,
    CodexIntegrationError,
    CodexModelReroutedError,
    CodexModelUnavailableError,
    CodexProtocolError,
    CodexQuotaExceededError,
    CodexRecognitionError,
    CodexTimeoutError,
    CodexUnavailableError,
    CodexUnsafeConfigurationError,
    CodexWrongAuthenticationError,
)


FINANCIAL_MODELS = (
    BalanceSnapshot,
    TransactionRecord,
    QuarterlySettlement,
    Invoice,
    Payment,
)
AI_REQUEST_HEADERS = {FINANCIAL_REQUEST_HEADER: "1"}


def _confirmation_client_id(client: TestClient, name: str = "SAMPLE CLIENT") -> int:
    # These independent recognition scenarios share a database and a sample
    # name. Explicitly select their customer instead of relying on the removed
    # behavior that silently created another customer for every new account.
    response = client.post("/api/clients", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _ocr_values(*, total_balance: str = "9736.57") -> dict:
    return {
        "document_type": "empf_account_page",
        "document_details": {},
        "client_name": "SAMPLE CLIENT",
        "account_number": "24681357",
        "scheme_name": "BCT (MPF) Pro Choice",
        "trustee": "Bank Consortium Trust Company Limited",
        "currency": "HKD",
        "as_of_date": "2026-05-20",
        "total_balance": total_balance,
        "lifetime_net_contributions": "9735.04",
        "lifetime_gain_loss": "1.53",
        "holdings": [],
    }


def _ai_values(*, total_balance: str = "9736.57") -> dict:
    return {
        "document_type": "empf_account_page",
        **_ocr_values(total_balance=total_balance),
        "uncertain_fields": [],
        "warnings": [],
    }


def _create_statement(*, status: str = "NEEDS_REVIEW") -> int:
    token = uuid4().hex
    path = get_settings().data_root / "statement_imports" / f"ai-api-{token}.jpg"
    # The AI client is replaced by a deterministic fake in these API tests;
    # only a safe in-root source path is needed to exercise the route guard.
    source_bytes = b"test statement image placeholder " + token.encode("ascii")
    path.write_bytes(source_bytes)
    with SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        item = StatementImport(
            id=allocate_entity_id(db, StatementImport),
            original_name=path.name,
            stored_path=str(path),
            sha256=hashlib.sha256(source_bytes).hexdigest(),
            mime_type="image/jpeg",
            status=status,
            parser_version="1.1",
            extracted_json=_ocr_values(),
            confidence_json={"overall": 0.91},
            warnings_json=[],
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item.id


def _financial_counts() -> dict[str, int]:
    with SessionLocal() as db:
        return {
            model.__tablename__: int(db.scalar(select(func.count()).select_from(model)) or 0)
            for model in FINANCIAL_MODELS
        }


class FakeRecognitionClient:
    def __init__(
        self,
        result: dict | None = None,
        error: CodexIntegrationError | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[Path] = []

    def recognize_statement(self, source: Path) -> dict:
        self.calls.append(source)
        if self.error:
            raise self.error
        assert self.result is not None
        return self.result


def _install_fake(monkeypatch: pytest.MonkeyPatch, fake: FakeRecognitionClient) -> None:
    monkeypatch.setattr(statements, "get_codex_app_server", lambda: fake)


def test_sensitive_ai_posts_require_local_request_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(app) as client:
        import_id = _create_statement()
        fake = FakeRecognitionClient(_ai_values())
        _install_fake(monkeypatch, fake)

        assert client.post("/api/ai-assistant/login").status_code == 403
        assert client.post("/api/ai-assistant/logout").status_code == 403
        assert client.post(f"/api/statement-imports/{import_id}/ai-recognize").status_code == 403
        assert fake.calls == []


def test_conflict_is_saved_for_human_review_without_financial_posting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(app, headers=AI_REQUEST_HEADERS) as client:
        import_id = _create_statement()
        before = _financial_counts()
        fake = FakeRecognitionClient(_ai_values(total_balance="9738.57"))
        _install_fake(monkeypatch, fake)

        response = client.post(f"/api/statement-imports/{import_id}/ai-recognize")

        assert response.status_code == 200, response.text
        body = response.json()
        review = body["ai_recognition"]
        assert body["status"] == "NEEDS_REVIEW"
        assert body["reviewed"] is None
        assert body["confirmed_snapshot_id"] is None
        assert body["ai_model"] == FIXED_AI_MODEL
        assert body["ai_status"] == "CONFLICT"
        assert review["model"] == FIXED_AI_MODEL
        assert review["model_escalation"] == "DISABLED"
        assert review["conflict_requires_human_review"] is True
        assert review["requires_financial_confirmation"] is True
        assert review["automatic_prefill_allowed"] is False
        assert [item["field"] for item in review["conflicts"]] == ["total_balance"]
        assert len(fake.calls) == 1
        assert _financial_counts() == before

        with SessionLocal() as db:
            item = db.get(StatementImport, import_id)
            assert item is not None
            assert item.extracted_json == _ocr_values()
            assert item.reviewed_json is None
            assert item.confirmed_account_id is None
            assert item.confirmed_snapshot_id is None
            audit = db.scalar(
                select(AuditEvent)
                .where(
                    AuditEvent.action == "STATEMENT_AI_RECOGNIZED",
                    AuditEvent.entity_id == import_id,
                )
                .order_by(AuditEvent.id.desc())
            )
            assert audit is not None
            assert audit.details_json["model"] == FIXED_AI_MODEL
            assert audit.details_json["model_escalation"] == "DISABLED"
            assert audit.details_json["financial_data_mutated"] is False


def test_full_agreement_still_requires_separate_finance_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(app, headers=AI_REQUEST_HEADERS) as client:
        import_id = _create_statement()
        before = _financial_counts()
        fake = FakeRecognitionClient(_ai_values())
        _install_fake(monkeypatch, fake)

        response = client.post(f"/api/statement-imports/{import_id}/ai-recognize")

        assert response.status_code == 200, response.text
        body = response.json()
        review = body["ai_recognition"]
        assert body["status"] == "NEEDS_REVIEW"
        assert body["ai_status"] == "AGREED"
        assert review["automatic_prefill_allowed"] is True
        assert review["requires_financial_confirmation"] is True
        assert review["conflict_requires_human_review"] is False
        assert body["reviewed"] is None
        assert body["confirmed_snapshot_id"] is None
        assert _financial_counts() == before


def test_ai_recognize_is_idempotent_after_first_audited_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(app, headers=AI_REQUEST_HEADERS) as client:
        import_id = _create_statement()
        first = FakeRecognitionClient(_ai_values())
        _install_fake(monkeypatch, first)
        first_response = client.post(f"/api/statement-imports/{import_id}/ai-recognize")
        assert first_response.status_code == 200
        assert len(first.calls) == 1

        duplicate = FakeRecognitionClient(error=AssertionError("model must not run twice"))
        _install_fake(monkeypatch, duplicate)
        second_response = client.post(f"/api/statement-imports/{import_id}/ai-recognize")

        assert second_response.status_code == 200
        assert second_response.json()["idempotent"] is True
        assert second_response.json()["ai_recognition"] == first_response.json()["ai_recognition"]
        assert duplicate.calls == []


def test_concurrent_ai_recognition_calls_model_only_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(app):
        import_id = _create_statement()

        class SlowRecognitionClient(FakeRecognitionClient):
            def __init__(self) -> None:
                super().__init__(_ai_values())
                self.call_lock = threading.Lock()

            def recognize_statement(self, source: Path) -> dict:
                with self.call_lock:
                    self.calls.append(source)
                time.sleep(0.15)
                assert self.result is not None
                return self.result

        fake = SlowRecognitionClient()
        _install_fake(monkeypatch, fake)
        barrier = threading.Barrier(2)

        def invoke() -> dict:
            with SessionLocal() as db:
                barrier.wait(timeout=2)
                return statements.recognize_statement_with_ai(import_id, db)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: invoke(), range(2)))

        assert len(fake.calls) == 1
        assert sorted(bool(result.get("idempotent")) for result in results) == [False, True]


def test_reparse_is_blocked_after_ai_result_to_preserve_comparison_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(app, headers=AI_REQUEST_HEADERS) as client:
        import_id = _create_statement()
        fake = FakeRecognitionClient(_ai_values())
        _install_fake(monkeypatch, fake)
        assert client.post(f"/api/statement-imports/{import_id}/ai-recognize").status_code == 200

        monkeypatch.setattr(
            statements,
            "parse_empf_statement",
            lambda _path: (_ for _ in ()).throw(AssertionError("OCR must not re-run")),
        )
        response = client.post(f"/api/statement-imports/{import_id}/reparse")

        assert response.status_code == 409
        assert "审计基线" in response.json()["detail"]


def test_holding_conflict_requires_explicit_finance_acknowledgement_before_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(app, headers=AI_REQUEST_HEADERS) as client:
        import_id = _create_statement()
        account_number = f"ACK{uuid4().hex[:12]}"
        scheme_name = f"AI Review Scheme {uuid4().hex[:8]}"
        ocr_holding = {
            "fund_name": "BCT (Pro) MPF Conservative Fund",
            "market_value": "9736.57",
            "investment_gain_loss": "1.53",
            "portfolio_percent": "100.00",
            "units": "7697.50929",
            "unit_price": "1.2649",
            "mandatory_contributions": None,
            "voluntary_contributions": None,
            "balance_as_of": "2026-05-20",
        }
        with SessionLocal() as db:
            item = db.get(StatementImport, import_id)
            assert item is not None
            item.extracted_json = {
                **_ocr_values(),
                "account_number": account_number,
                "scheme_name": scheme_name,
                "holdings": [ocr_holding],
            }
            db.commit()

        ai_holding = {**ocr_holding, "unit_price": "1.2650"}
        fake = FakeRecognitionClient(
            {
                **_ai_values(),
                "account_number": account_number,
                "scheme_name": scheme_name,
                "holdings": [ai_holding],
            }
        )
        _install_fake(monkeypatch, fake)
        ai_response = client.post(f"/api/statement-imports/{import_id}/ai-recognize")
        assert ai_response.status_code == 200
        assert ai_response.json()["ai_status"] == "CONFLICT"
        assert ai_response.json()["ai_recognition"]["conflicts"][0]["field"] == "holdings[0].unit_price"

        confirm_payload = {
            "client_name": "SAMPLE CLIENT",
            "client_id": _confirmation_client_id(client),
            "account_number": account_number,
            "scheme_name": scheme_name,
            "trustee": "Bank Consortium Trust Company Limited",
            "as_of_date": "2026-05-20",
            "total_balance": "9736.57",
        }
        before = _financial_counts()
        rejected = client.post(
            f"/api/statement-imports/{import_id}/confirm", json=confirm_payload
        )
        assert rejected.status_code == 409
        assert "逐项人工核对" in rejected.json()["detail"]
        assert _financial_counts() == before

        accepted = client.post(
            f"/api/statement-imports/{import_id}/confirm",
            json={
                **confirm_payload,
                "ai_conflicts_reviewed": True,
                "holdings": [ai_holding],
            },
        )
        assert accepted.status_code == 200, accepted.text
        with SessionLocal() as db:
            statement = db.get(StatementImport, import_id)
            assert statement is not None
            assert statement.reviewed_json["holdings"] == [ai_holding]
            snapshot = db.get(BalanceSnapshot, statement.confirmed_snapshot_id)
            assert snapshot is not None
            assert snapshot.holdings_json == [ai_holding]
            audit = db.scalar(
                select(AuditEvent)
                .where(
                    AuditEvent.action == "STATEMENT_CONFIRMED",
                    AuditEvent.entity_id == import_id,
                )
                .order_by(AuditEvent.id.desc())
            )
            assert audit is not None
            assert audit.details_json["ai_status"] == "CONFLICT"
            assert audit.details_json["ai_review_acknowledged"] is True
            assert audit.details_json["changes"]["holdings"] == {
                "source": "SOL_SELECTED",
                "recognized_count": 1,
                "luna_count": 1,
                "confirmed_count": 1,
                "changed": True,
            }


def test_confirmed_local_holdings_are_canonically_audited_as_selected() -> None:
    with TestClient(app, headers=AI_REQUEST_HEADERS) as client:
        import_id = _create_statement()
        account_number = f"LOCAL{uuid4().hex[:10]}"
        scheme_name = f"Local Holding Scheme {uuid4().hex[:8]}"
        local_holding = {
            "fund_name": "BCT Local Fund",
            "market_value": "9736.57",
            "investment_gain_loss": "1.53",
            "portfolio_percent": "100.00",
            "units": "7697.50929",
            "unit_price": "1.2649",
            # Local OCR does not emit these optional keys. The same Pydantic
            # schema adds nulls before source/changed comparisons.
        }
        with SessionLocal() as db:
            item = db.get(StatementImport, import_id)
            assert item is not None
            item.extracted_json = {
                **_ocr_values(),
                "account_number": account_number,
                "scheme_name": scheme_name,
                "holdings": [local_holding],
            }
            db.commit()

        response = client.post(
            f"/api/statement-imports/{import_id}/confirm",
            json={
                "client_name": "SAMPLE CLIENT",
                "client_id": _confirmation_client_id(client),
                "account_number": account_number,
                "scheme_name": scheme_name,
                "trustee": "Bank Consortium Trust Company Limited",
                "as_of_date": "2026-05-20",
                "total_balance": "9736.57",
                "holdings": [local_holding],
            },
        )

        assert response.status_code == 200, response.text
        with SessionLocal() as db:
            audit = db.scalar(
                select(AuditEvent)
                .where(
                    AuditEvent.action == "STATEMENT_CONFIRMED",
                    AuditEvent.entity_id == import_id,
                )
                .order_by(AuditEvent.id.desc())
            )
            assert audit is not None
            assert audit.details_json["changes"]["holdings"] == {
                "source": "LOCAL_OCR_SELECTED",
                "recognized_count": 1,
                "luna_count": 0,
                "confirmed_count": 1,
                "changed": False,
            }


@pytest.mark.parametrize("selection", ["omitted", "null", "empty", "partial"])
def test_holdings_cannot_be_lost_through_generic_acknowledgement(
    monkeypatch: pytest.MonkeyPatch, selection: str,
) -> None:
    with TestClient(app, headers=AI_REQUEST_HEADERS) as client:
        import_id = _create_statement()
        account_number = f"HOLD{uuid4().hex[:12]}"
        scheme_name = f"Synthetic Holdings {uuid4().hex[:12]}"
        ai_values = {**_ai_values(), "account_number": account_number, "scheme_name": scheme_name, "holdings": [
            StatementHoldingInput(fund_name="SYNTHETIC FUND A", market_value="6000.00").model_dump(),
            StatementHoldingInput(fund_name="SYNTHETIC FUND B", market_value="3736.57").model_dump(),
        ]}
        fake = FakeRecognitionClient(ai_values)
        _install_fake(monkeypatch, fake)
        assert client.post(f"/api/statement-imports/{import_id}/ai-recognize").status_code == 200
        payload = {
            **_ocr_values(), "account_number": account_number, "scheme_name": scheme_name,
            "client_id": _confirmation_client_id(client),
            "ai_conflicts_reviewed": True,
        }
        payload.pop("holdings")
        if selection != "omitted":
            payload["holdings"] = {"null": None, "empty": [], "partial": ai_values["holdings"][:1]}[selection]
        before = _financial_counts()
        rejected = client.post(f"/api/statement-imports/{import_id}/confirm", json=payload)
        assert rejected.status_code == 409, rejected.text
        assert "持仓差异原因" in rejected.json()["detail"]
        assert _financial_counts() == before
        with SessionLocal() as db:
            item = db.get(StatementImport, import_id)
            assert item.status == "NEEDS_REVIEW"
            assert item.confirmed_snapshot_id is None
            assert item.reviewed_json is None

        # A subsequent explicit selection saves both rows; the blocked attempt
        # neither creates a snapshot nor changes the single audited AI result.
        accepted = client.post(f"/api/statement-imports/{import_id}/confirm", json={
            **payload, "holdings": ai_values["holdings"],
        })
        assert accepted.status_code == 200, accepted.text
        with SessionLocal() as db:
            item = db.get(StatementImport, import_id)
            assert db.get(BalanceSnapshot, item.confirmed_snapshot_id).holdings_json == ai_values["holdings"]
            assert item.revision_log_json[-1]["changes"]["holdings"]["source"] == "SOL_SELECTED"
        assert len(fake.calls) == 1


@pytest.mark.parametrize("reason", [None, "  ", "  x  ", "原件已核对，另一来源为重复识别"])
def test_rejecting_false_positive_holdings_requires_specific_audited_reason(reason: str | None) -> None:
    with TestClient(app, headers=AI_REQUEST_HEADERS) as client:
        import_id = _create_statement()
        with SessionLocal() as db:
            item = db.get(StatementImport, import_id)
            item.extracted_json = {**item.extracted_json, "holdings": [{"fund_name": "SYNTHETIC DUPLICATE"}]}
            db.commit()
        payload = {
            **_ocr_values(), "account_number": f"MANUAL{uuid4().hex[:12]}",
            "client_id": _confirmation_client_id(client),
            "scheme_name": f"Synthetic Manual {uuid4().hex[:12]}",
            "holdings": [], "holdings_difference_reason": reason,
        }
        response = client.post(f"/api/statement-imports/{import_id}/confirm", json=payload)
        if reason is None or len(reason.strip()) < 2:
            assert response.status_code == (409 if reason is None else 422), response.text
            return
        assert response.status_code == 200, response.text
        with SessionLocal() as db:
            item = db.get(StatementImport, import_id)
            assert item.reviewed_json["holdings_difference_reason"] == reason
            assert db.get(BalanceSnapshot, item.confirmed_snapshot_id).holdings_json == []
            audit = db.scalar(select(AuditEvent).where(
                AuditEvent.action == "STATEMENT_CONFIRMED", AuditEvent.entity_id == import_id,
            ))
            assert audit.details_json["changes"]["holdings"]["difference_reason"] == reason


def test_non_balance_document_cannot_create_balance_snapshot() -> None:
    with TestClient(app, headers=AI_REQUEST_HEADERS) as client:
        import_id = _create_statement()
        with SessionLocal() as db:
            item = db.get(StatementImport, import_id)
            assert item is not None
            item.extracted_json = {
                "document_type": "contribution_record",
                "document_details": {"billing_amount": "5000.00"},
                "account_number": "87654321",
                "holdings": [],
            }
            item.ai_recognition_json = {
                "status": "CONFLICT",
                "values": _ai_values(),
                "conflicts": [{"field": "document_type"}],
            }
            item.ai_status = "CONFLICT"
            db.commit()

        before = _financial_counts()
        response = client.post(
            f"/api/statement-imports/{import_id}/confirm",
            json={
                "client_name": "SAMPLE MEMBER",
                "account_number": "87654321",
                "as_of_date": "2026-03-31",
                "total_balance": "5000.00",
                "ai_conflicts_reviewed": True,
                "luna_document_type_reviewed": True,
            },
        )

        assert response.status_code == 409
        assert "禁止生成余额快照" in response.json()["detail"]
        assert _financial_counts() == before


def test_unknown_local_type_can_use_sol_balance_type_only_after_explicit_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(app, headers=AI_REQUEST_HEADERS) as client:
        import_id = _create_statement()
        account_number = f"SOL{uuid4().hex[:10]}"
        scheme_name = f"Sol Type Review {uuid4().hex[:8]}"
        with SessionLocal() as db:
            item = db.get(StatementImport, import_id)
            assert item is not None
            item.extracted_json = {
                **_ocr_values(),
                "document_type": "unknown",
                "account_number": account_number,
                "scheme_name": scheme_name,
            }
            db.commit()

        fake = FakeRecognitionClient(
            {
                **_ai_values(),
                "account_number": account_number,
                "scheme_name": scheme_name,
            }
        )
        _install_fake(monkeypatch, fake)
        recognized = client.post(f"/api/statement-imports/{import_id}/ai-recognize")
        assert recognized.status_code == 200, recognized.text
        assert recognized.json()["ai_status"] == "CONFLICT"

        payload = {
            "client_name": "SAMPLE CLIENT",
            "client_id": _confirmation_client_id(client),
            "account_number": account_number,
            "scheme_name": scheme_name,
            "trustee": "Bank Consortium Trust Company Limited",
            "as_of_date": "2026-05-20",
            "total_balance": "9736.57",
            "ai_conflicts_reviewed": True,
        }
        rejected = client.post(f"/api/statement-imports/{import_id}/confirm", json=payload)
        assert rejected.status_code == 409
        assert "采用Sol余额页分类" in rejected.json()["detail"]

        accepted = client.post(
            f"/api/statement-imports/{import_id}/confirm",
            json={**payload, "luna_document_type_reviewed": True},
        )
        assert accepted.status_code == 200, accepted.text
        with SessionLocal() as db:
            statement = db.get(StatementImport, import_id)
            assert statement is not None
            assert statement.reviewed_json["document_type"] == "empf_account_page"
            audit = db.scalar(
                select(AuditEvent)
                .where(
                    AuditEvent.action == "STATEMENT_CONFIRMED",
                    AuditEvent.entity_id == import_id,
                )
                .order_by(AuditEvent.id.desc())
            )
            assert audit is not None
            assert audit.details_json["document_type_source"] == "SOL_HUMAN_CONFIRMED"


def test_unconfirmed_import_delete_removes_record_source_and_ai_result() -> None:
    with TestClient(app, headers=AI_REQUEST_HEADERS) as client:
        import_id = _create_statement()
        with SessionLocal() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            item = db.get(StatementImport, import_id)
            assert item is not None
            source_path = Path(item.stored_path)
            item.ai_recognition_json = {"status": "CONFLICT", "values": _ai_values()}
            item.ai_status = "CONFLICT"
            dependent_token = uuid4().hex
            dependent_bytes = f"semantic-{dependent_token}".encode()
            dependent_path = source_path.parent / f"semantic-{dependent_token}.jpg"
            dependent_path.write_bytes(dependent_bytes)
            dependent = StatementImport(
                id=allocate_entity_id(db, StatementImport),
                original_name=f"semantic-{dependent_token}.jpg",
                stored_path=str(dependent_path),
                sha256=hashlib.sha256(dependent_bytes).hexdigest(),
                mime_type="image/jpeg",
                status="NEEDS_REVIEW",
                parser_version="1.1",
                extracted_json=_ocr_values(),
                duplicate_of_id=item.id,
            )
            db.add(dependent)
            db.commit()
            db.refresh(dependent)
            dependent_id = dependent.id
        assert source_path.exists()

        blocked = client.delete(f"/api/statement-imports/{import_id}")
        assert blocked.status_code == 409
        assert f"后继重复记录 #{dependent_id}" in blocked.json()["detail"]

        dependent_deleted = client.delete(f"/api/statement-imports/{dependent_id}")
        assert dependent_deleted.status_code == 200, dependent_deleted.text
        response = client.delete(f"/api/statement-imports/{import_id}")

        assert response.status_code == 200, response.text
        assert response.json() == {
            "deleted": True,
            "import_id": import_id,
            "source_file_deleted": True,
        }
        assert not source_path.exists()
        with SessionLocal() as db:
            assert db.get(StatementImport, import_id) is None
            assert db.get(StatementImport, dependent_id) is None
            audit = db.scalar(
                select(AuditEvent)
                .where(
                    AuditEvent.action == "STATEMENT_IMPORT_DELETED",
                    AuditEvent.entity_id.is_(None),
                    AuditEvent.details_json["deleted_import_id"].as_integer() == import_id,
                )
                .order_by(AuditEvent.id.desc())
            )
            assert audit is not None
            assert audit.details_json["had_ai_recognition"] is True


def test_import_delete_requires_local_write_marker_and_preserves_source() -> None:
    import_id = _create_statement()
    with SessionLocal() as db:
        item = db.get(StatementImport, import_id)
        assert item is not None
        source_path = Path(item.stored_path)

    with TestClient(app) as client:
        response = client.delete(f"/api/statement-imports/{import_id}")

    assert response.status_code == 403
    assert source_path.exists()
    with SessionLocal() as db:
        assert db.get(StatementImport, import_id) is not None


def test_confirmed_import_delete_without_reason_is_rejected_and_keeps_source() -> None:
    with TestClient(app, headers=AI_REQUEST_HEADERS) as client:
        import_id = _create_statement(status="CONFIRMED")
        with SessionLocal() as db:
            item = db.get(StatementImport, import_id)
            assert item is not None
            source_path = Path(item.stored_path)

        response = client.delete(f"/api/statement-imports/{import_id}")

        assert response.status_code == 422
        assert "必须填写原因" in response.json()["detail"]
        assert source_path.exists()
        with SessionLocal() as db:
            assert db.get(StatementImport, import_id) is not None


def test_confirmed_import_rejects_ai_before_any_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(app, headers=AI_REQUEST_HEADERS) as client:
        import_id = _create_statement(status="CONFIRMED")
        fake = FakeRecognitionClient(_ai_values())
        _install_fake(monkeypatch, fake)

        response = client.post(f"/api/statement-imports/{import_id}/ai-recognize")

        assert response.status_code == 409
        assert fake.calls == []


@pytest.mark.parametrize(
    ("error_factory", "expected_status", "expected_code"),
    [
        (lambda: CodexAuthenticationError("请登录ChatGPT"), 401, "CHATGPT_AUTH_REQUIRED"),
        (
            lambda: CodexWrongAuthenticationError("拒绝API Key认证"),
            409,
            "CHATGPT_AUTH_MODE_REQUIRED",
        ),
        (lambda: CodexUnavailableError("Codex不可用"), 503, "CODEX_UNAVAILABLE"),
        (
            lambda: CodexUnsafeConfigurationError("Codex安全配置异常"),
            503,
            "CODEX_UNSAFE_CONFIGURATION",
        ),
        (lambda: CodexModelUnavailableError("Sol不可用"), 503, "SOL_UNAVAILABLE"),
        (
            lambda: CodexQuotaExceededError("ChatGPT额度已用完"),
            503,
            "CHATGPT_QUOTA_EXHAUSTED",
        ),
        (lambda: CodexTimeoutError("Sol超时"), 504, "CODEX_TIMEOUT"),
        (lambda: CodexProtocolError("协议错误"), 502, "CODEX_PROTOCOL_ERROR"),
        (
            lambda: CodexModelReroutedError("拒绝升级模型"),
            502,
            "AI_MODEL_REROUTED",
        ),
        (lambda: CodexRecognitionError("结构化结果无效"), 502, "AI_RECOGNITION_FAILED"),
    ],
)
def test_ai_failures_return_manual_review_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
    error_factory: Callable[[], CodexIntegrationError],
    expected_status: int,
    expected_code: str,
) -> None:
    with TestClient(app, headers=AI_REQUEST_HEADERS) as client:
        import_id = _create_statement()
        before = _financial_counts()
        fake = FakeRecognitionClient(error=error_factory())
        _install_fake(monkeypatch, fake)

        response = client.post(f"/api/statement-imports/{import_id}/ai-recognize")

        assert response.status_code == expected_status, response.text
        detail = response.json()["detail"]
        assert detail["code"] == expected_code
        assert detail["manual_review_required"] is True
        assert len(fake.calls) == 1
        assert _financial_counts() == before
        with SessionLocal() as db:
            item = db.get(StatementImport, import_id)
            assert item is not None
            assert item.ai_recognition_json is None
            assert item.ai_status is None
            assert item.ai_model is None
            assert item.reviewed_json is None
            assert item.confirmed_snapshot_id is None
