from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (
    Attachment,
    AuditEvent,
    BalanceSnapshot,
    Client,
    Company,
    ExportRecord,
    FC,
    FeePlan,
    Invoice,
    InvoiceLine,
    InvoiceSequence,
    Platform,
    QuarterlySettlement,
    SettlementAccountLine,
    SubAccount,
    TransactionRecord,
)
from ..money import money_string, to_cents
from ..services.calculation import is_quarter_end
from ..schemas import (
    AccountCreate,
    AccountUpdate,
    BalanceSnapshotCreate,
    ClientCreate,
    ClientUpdate,
    CompanyCreate,
    FCCreate,
    FeePlanCreate,
    PlatformCreate,
    TransactionCreate,
)


router = APIRouter(prefix="/api", tags=["master-data"])


def _commit(db: Session, message: str = "资料重复或关联不正确") -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=message) from exc


def _delete_master_data(
    db: Session,
    *,
    item,
    entity_name: str,
    entity_type: str,
    reference_queries: list[tuple[str, object]],
) -> dict:
    references = [
        label for label, query in reference_queries if db.scalar(query) is not None
    ]
    history_entity_types = {
        "COMPANY": ("COMPANY",),
        "FC": ("FC",),
        "PLATFORM": ("PLATFORM",),
        "FEE_PLAN": ("FEE_PLAN", "FEEPLAN"),
    }[entity_type]
    for label, model in (
        ("审计记录", AuditEvent),
        ("附件记录", Attachment),
        ("导出记录", ExportRecord),
    ):
        normalized_history_type = func.replace(
            func.replace(func.upper(func.trim(model.entity_type)), "-", "_"),
            " ",
            "_",
        )
        if db.scalar(
            select(model.id)
            .where(
                normalized_history_type.in_(history_entity_types),
                model.entity_id == item.id,
            )
            .limit(1)
        ) is not None:
            references.append(label)
    if references:
        raise HTTPException(
            status_code=409,
            detail=f"{entity_name}已被以下资料引用，不能删除：{'、'.join(references)}",
        )

    item_id = item.id
    item_name = item.name
    item_code = item.code
    try:
        result = db.execute(delete(type(item)).where(type(item).id == item_id))
        if result.rowcount != 1:
            db.rollback()
            raise HTTPException(status_code=404, detail=f"{entity_name}不存在")
        db.add(
            AuditEvent(
                action="MASTER_DATA_DELETED",
                entity_type="MASTER_DATA",
                entity_id=None,
                details_json={
                    "deleted_entity_type": entity_type,
                    "deleted_entity_id": item_id,
                    "name": item_name,
                    "code": item_code,
                },
            )
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"{entity_name}已被其他资料或历史记录引用，不能删除",
        ) from exc
    return {"status": "deleted", "id": item_id}


@router.get("/companies")
def list_companies(db: Session = Depends(get_db)) -> list[dict]:
    items = db.scalars(select(Company).order_by(Company.name)).all()
    return [
        {
            "id": item.id,
            "name": item.name,
            "code": item.code,
            "address": item.address,
            "contact": item.contact,
            "bank_information": item.bank_information,
            "cheque_information": item.cheque_information,
            "payment_terms_days": item.payment_terms_days,
            "active": item.active,
        }
        for item in items
    ]


@router.post("/companies", status_code=201)
def create_company(payload: CompanyCreate, db: Session = Depends(get_db)) -> dict:
    item = Company(**payload.model_dump())
    db.add(item)
    _commit(db, "Company Name或Company Code已经存在")
    db.refresh(item)
    return {"id": item.id, **payload.model_dump()}


@router.delete("/companies/{company_id}")
def delete_company(company_id: int, db: Session = Depends(get_db)) -> dict:
    item = db.get(Company, company_id)
    if not item:
        raise HTTPException(status_code=404, detail="Company不存在")
    return _delete_master_data(
        db,
        item=item,
        entity_name="Company",
        entity_type="COMPANY",
        reference_queries=[
            ("FC", select(FC.id).where(FC.company_id == company_id).limit(1)),
            ("Fee Plan", select(FeePlan.id).where(FeePlan.company_id == company_id).limit(1)),
            ("Client", select(Client.id).where(Client.company_id == company_id).limit(1)),
            (
                "Settlement",
                select(QuarterlySettlement.id)
                .where(QuarterlySettlement.company_id == company_id)
                .limit(1),
            ),
            ("Invoice", select(Invoice.id).where(Invoice.company_id == company_id).limit(1)),
            (
                "Invoice编号序列",
                select(InvoiceSequence.id).where(InvoiceSequence.company_id == company_id).limit(1),
            ),
        ],
    )


@router.get("/fcs")
def list_fcs(db: Session = Depends(get_db)) -> list[dict]:
    items = db.scalars(select(FC).order_by(FC.name)).all()
    return [
        {
            "id": item.id,
            "company_id": item.company_id,
            "company_name": item.company.name,
            "name": item.name,
            "code": item.code,
            "remark": item.remark,
            "active": item.active,
        }
        for item in items
    ]


@router.post("/fcs", status_code=201)
def create_fc(payload: FCCreate, db: Session = Depends(get_db)) -> dict:
    if not db.get(Company, payload.company_id):
        raise HTTPException(status_code=404, detail="Company不存在")
    item = FC(**payload.model_dump())
    db.add(item)
    _commit(db, "同一Company下的FC Code必须唯一")
    db.refresh(item)
    return {"id": item.id, **payload.model_dump()}


@router.delete("/fcs/{fc_id}")
def delete_fc(fc_id: int, db: Session = Depends(get_db)) -> dict:
    item = db.get(FC, fc_id)
    if not item:
        raise HTTPException(status_code=404, detail="FC不存在")
    return _delete_master_data(
        db,
        item=item,
        entity_name="FC",
        entity_type="FC",
        reference_queries=[
            ("Client", select(Client.id).where(Client.fc_id == fc_id).limit(1)),
            (
                "Settlement",
                select(QuarterlySettlement.id)
                .where(QuarterlySettlement.fc_id == fc_id)
                .limit(1),
            ),
            ("Invoice", select(Invoice.id).where(Invoice.fc_id == fc_id).limit(1)),
            (
                "Invoice编号序列",
                select(InvoiceSequence.id).where(InvoiceSequence.fc_id == fc_id).limit(1),
            ),
        ],
    )


@router.get("/platforms")
def list_platforms(db: Session = Depends(get_db)) -> list[dict]:
    items = db.scalars(select(Platform).order_by(Platform.name)).all()
    return [
        {
            "id": item.id,
            "name": item.name,
            "code": item.code,
            "trustee": item.trustee,
            "remark": item.remark,
            "active": item.active,
        }
        for item in items
    ]


@router.post("/platforms", status_code=201)
def create_platform(payload: PlatformCreate, db: Session = Depends(get_db)) -> dict:
    item = Platform(**payload.model_dump())
    db.add(item)
    _commit(db, "Platform Name或Platform Code已经存在")
    db.refresh(item)
    return {"id": item.id, **payload.model_dump()}


@router.delete("/platforms/{platform_id}")
def delete_platform(platform_id: int, db: Session = Depends(get_db)) -> dict:
    item = db.get(Platform, platform_id)
    if not item:
        raise HTTPException(status_code=404, detail="Platform不存在")
    return _delete_master_data(
        db,
        item=item,
        entity_name="Platform",
        entity_type="PLATFORM",
        reference_queries=[
            (
                "Sub Account",
                select(SubAccount.id).where(SubAccount.platform_id == platform_id).limit(1),
            ),
            (
                "Settlement",
                select(QuarterlySettlement.id)
                .where(QuarterlySettlement.platform_id == platform_id)
                .limit(1),
            ),
            (
                "Invoice收费行",
                select(InvoiceLine.id).where(InvoiceLine.platform_id == platform_id).limit(1),
            ),
        ],
    )


@router.get("/fee-plans")
def list_fee_plans(db: Session = Depends(get_db)) -> list[dict]:
    items = db.scalars(select(FeePlan).order_by(FeePlan.name)).all()
    return [
        {
            "id": item.id,
            "company_id": item.company_id,
            "company_name": item.company.name,
            "name": item.name,
            "code": item.code,
            "fee_rate_percent": item.fee_rate_bps / 100,
            "calculation_method": item.calculation_method,
            "active": item.active,
        }
        for item in items
    ]


@router.post("/fee-plans", status_code=201)
def create_fee_plan(payload: FeePlanCreate, db: Session = Depends(get_db)) -> dict:
    if not db.get(Company, payload.company_id):
        raise HTTPException(status_code=404, detail="Company不存在")
    data = payload.model_dump(exclude={"fee_rate_percent"})
    data["code"] = data["code"].strip().upper()
    fee_rate_bps = int((payload.fee_rate_percent * 100).to_integral_exact())
    item = FeePlan(**data, fee_rate_bps=fee_rate_bps)
    db.add(item)
    _commit(db, "同一Company下的Fee Plan Code必须唯一")
    db.refresh(item)
    return {
        "id": item.id,
        **payload.model_dump(exclude={"fee_rate_percent"}),
        "fee_rate_percent": item.fee_rate_bps / 100,
    }


@router.delete("/fee-plans/{fee_plan_id}")
def delete_fee_plan(fee_plan_id: int, db: Session = Depends(get_db)) -> dict:
    item = db.get(FeePlan, fee_plan_id)
    if not item:
        raise HTTPException(status_code=404, detail="Fee Plan不存在")
    return _delete_master_data(
        db,
        item=item,
        entity_name="Fee Plan",
        entity_type="FEE_PLAN",
        reference_queries=[
            (
                "Sub Account",
                select(SubAccount.id).where(SubAccount.fee_plan_id == fee_plan_id).limit(1),
            ),
            (
                "Settlement",
                select(QuarterlySettlement.id)
                .where(QuarterlySettlement.fee_plan_id == fee_plan_id)
                .limit(1),
            ),
            ("Invoice", select(Invoice.id).where(Invoice.fee_plan_id == fee_plan_id).limit(1)),
        ],
    )


@router.get("/clients")
def list_clients(db: Session = Depends(get_db)) -> list[dict]:
    items = db.scalars(select(Client).order_by(Client.name)).all()
    return [
        {
            "id": item.id,
            "company_id": item.company_id,
            "company_name": item.company.name if item.company else None,
            "fc_id": item.fc_id,
            "fc_name": item.fc.name if item.fc else None,
            "name": item.name,
            "contact": item.contact,
            "management_start_date": item.management_start_date.isoformat() if item.management_start_date else None,
            "remark": item.remark,
            "status": item.status,
        }
        for item in items
    ]


@router.post("/clients", status_code=201)
def create_client(payload: ClientCreate, db: Session = Depends(get_db)) -> dict:
    if payload.company_id and not db.get(Company, payload.company_id):
        raise HTTPException(status_code=404, detail="Company不存在")
    if payload.fc_id:
        fc = db.get(FC, payload.fc_id)
        if not fc or (payload.company_id and fc.company_id != payload.company_id):
            raise HTTPException(status_code=400, detail="FC与Company不匹配")
    if payload.status == "ACTIVE" and (
        not payload.company_id or not payload.fc_id or not payload.management_start_date
    ):
        raise HTTPException(status_code=400, detail="Active Client必须补全Company、FC和Management Start Date")
    item = Client(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return {"id": item.id, **payload.model_dump(mode="json")}


@router.patch("/clients/{client_id}")
def update_client(client_id: int, payload: ClientUpdate, db: Session = Depends(get_db)) -> dict:
    item = db.get(Client, client_id)
    if not item:
        raise HTTPException(status_code=404, detail="Client不存在")
    changes = payload.model_dump(exclude_unset=True)
    company_id = changes.get("company_id", item.company_id)
    fc_id = changes.get("fc_id", item.fc_id)
    if fc_id:
        fc = db.get(FC, fc_id)
        if not fc or (company_id and fc.company_id != company_id):
            raise HTTPException(status_code=400, detail="FC与Company不匹配")
    effective_status = changes.get("status", item.status)
    effective_start = changes.get("management_start_date", item.management_start_date)
    if effective_status == "ACTIVE" and (not company_id or not fc_id or not effective_start):
        raise HTTPException(status_code=400, detail="Active Client必须补全Company、FC和Management Start Date")
    for key, value in changes.items():
        setattr(item, key, value)
    db.commit()
    return {"id": item.id, **payload.model_dump(exclude_unset=True, mode="json")}


@router.get("/accounts")
def list_accounts(client_id: int | None = None, db: Session = Depends(get_db)) -> list[dict]:
    query = select(SubAccount).order_by(SubAccount.account_number)
    if client_id:
        query = query.where(SubAccount.client_id == client_id)
    items = db.scalars(query).all()
    return [
        {
            "id": item.id,
            "client_id": item.client_id,
            "client_name": item.client.name,
            "platform_id": item.platform_id,
            "platform_name": item.platform.name if item.platform else None,
            "fee_plan_id": item.fee_plan_id,
            "fee_plan_name": item.fee_plan.name if item.fee_plan else None,
            "account_number": item.account_number,
            "scheme_name": item.scheme_name,
            "currency": item.currency,
            "start_date": item.start_date.isoformat() if item.start_date else None,
            "end_date": item.end_date.isoformat() if item.end_date else None,
            "status": item.status,
            "remark": item.remark,
        }
        for item in items
    ]


@router.post("/accounts", status_code=201)
def create_account(payload: AccountCreate, db: Session = Depends(get_db)) -> dict:
    client = db.get(Client, payload.client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client不存在")
    if payload.platform_id and not db.get(Platform, payload.platform_id):
        raise HTTPException(status_code=404, detail="Platform不存在")
    plan = db.get(FeePlan, payload.fee_plan_id) if payload.fee_plan_id else None
    if payload.fee_plan_id and not plan:
        raise HTTPException(status_code=404, detail="Fee Plan不存在")
    if plan and client.company_id and plan.company_id != client.company_id:
        raise HTTPException(status_code=400, detail="Fee Plan与Client所属Company不一致")
    if payload.status == "ACTIVE" and (not payload.platform_id or not payload.fee_plan_id):
        raise HTTPException(status_code=400, detail="Active Sub Account必须补全Platform和Fee Plan")
    if payload.status == "ACTIVE" and client.status != "ACTIVE":
        raise HTTPException(status_code=400, detail="Client必须先补全并设为Active")
    if payload.start_date and payload.end_date and payload.end_date < payload.start_date:
        raise HTTPException(status_code=400, detail="账户结束日期不能早于开始日期")
    item = SubAccount(**payload.model_dump(), currency="HKD")
    db.add(item)
    _commit(db, "同一Platform下的Account Number必须唯一")
    db.refresh(item)
    return {"id": item.id, **payload.model_dump(mode="json"), "currency": "HKD"}


@router.patch("/accounts/{account_id}")
def update_account(account_id: int, payload: AccountUpdate, db: Session = Depends(get_db)) -> dict:
    item = db.get(SubAccount, account_id)
    if not item:
        raise HTTPException(status_code=404, detail="Sub Account不存在")
    changes = payload.model_dump(exclude_unset=True)
    platform_id = changes.get("platform_id", item.platform_id)
    fee_plan_id = changes.get("fee_plan_id", item.fee_plan_id)
    start_date = changes.get("start_date", item.start_date)
    end_date = changes.get("end_date", item.end_date)
    status = changes.get("status", item.status)
    if platform_id and not db.get(Platform, platform_id):
        raise HTTPException(status_code=404, detail="Platform不存在")
    plan = db.get(FeePlan, fee_plan_id) if fee_plan_id else None
    if fee_plan_id and not plan:
        raise HTTPException(status_code=404, detail="Fee Plan不存在")
    if plan and item.client.company_id and plan.company_id != item.client.company_id:
        raise HTTPException(status_code=400, detail="Fee Plan与Client所属Company不一致")
    if status == "ACTIVE" and (not platform_id or not fee_plan_id):
        raise HTTPException(status_code=400, detail="Active Sub Account必须补全Platform和Fee Plan")
    if status == "ACTIVE" and item.client.status != "ACTIVE":
        raise HTTPException(status_code=400, detail="Client必须先补全并设为Active")
    if start_date and end_date and end_date < start_date:
        raise HTTPException(status_code=400, detail="账户结束日期不能早于开始日期")
    for key, value in changes.items():
        setattr(item, key, value)
    _commit(db)
    return {"id": item.id, **payload.model_dump(exclude_unset=True, mode="json")}


@router.get("/transactions")
def list_transactions(account_id: int | None = None, db: Session = Depends(get_db)) -> list[dict]:
    query = select(TransactionRecord).order_by(TransactionRecord.transaction_date.desc(), TransactionRecord.id.desc())
    if account_id:
        query = query.where(TransactionRecord.account_id == account_id)
    items = db.scalars(query).all()
    result = []
    for item in items:
        attachment_count = db.scalar(
            select(func.count(Attachment.id)).where(
                Attachment.entity_type == "TRANSACTION", Attachment.entity_id == item.id
            )
        ) or 0
        evidence_count = int(attachment_count) + (1 if item.attachment_id is not None else 0)
        result.append({
            "id": item.id,
            "account_id": item.account_id,
            "account_number": item.account.account_number,
            "transaction_date": item.transaction_date.isoformat(),
            "transaction_type": item.transaction_type,
            "amount": money_string(item.amount_cents),
            "remark": item.remark,
            "evidence_count": evidence_count,
            "evidence_complete": evidence_count > 0,
        })
    return result


@router.post("/transactions", status_code=201)
def create_transaction(payload: TransactionCreate, db: Session = Depends(get_db)) -> dict:
    account = db.get(SubAccount, payload.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Sub Account不存在")
    if account.start_date and payload.transaction_date < account.start_date:
        raise HTTPException(status_code=400, detail="交易日期不能早于账户开始管理日期")
    locked_settlement_id = db.scalar(
        select(QuarterlySettlement.id)
        .join(SettlementAccountLine, SettlementAccountLine.settlement_id == QuarterlySettlement.id)
        .where(
            SettlementAccountLine.account_id == payload.account_id,
            QuarterlySettlement.status == "FINALIZED",
            SettlementAccountLine.start_date <= payload.transaction_date,
            SettlementAccountLine.closing_date >= payload.transaction_date,
        )
        .limit(1)
    )
    if locked_settlement_id is not None:
        raise HTTPException(
            status_code=409,
            detail=f"交易日期已落入Finalized Settlement #{locked_settlement_id}，请先按顺序作废下游结算后再调整",
        )
    item = TransactionRecord(
        account_id=payload.account_id,
        transaction_date=payload.transaction_date,
        transaction_type=payload.transaction_type,
        amount_cents=to_cents(payload.amount),
        remark=payload.remark,
    )
    db.add(item)
    _commit(db, "交易日期已落入Finalized Settlement，不能补录")
    db.refresh(item)
    return {
        "id": item.id,
        **payload.model_dump(exclude={"amount"}, mode="json"),
        "amount": money_string(item.amount_cents),
    }


@router.get("/balance-snapshots")
def list_balance_snapshots(account_id: int | None = None, db: Session = Depends(get_db)) -> list[dict]:
    query = select(BalanceSnapshot).order_by(BalanceSnapshot.as_of_date.desc())
    if account_id:
        query = query.where(BalanceSnapshot.account_id == account_id)
    items = db.scalars(query).all()
    result = []
    for item in items:
        attachment_count = db.scalar(
            select(func.count(Attachment.id)).where(
                Attachment.entity_type == "SNAPSHOT", Attachment.entity_id == item.id
            )
        ) or 0
        evidence_count = int(attachment_count) + (1 if item.statement_import_id is not None else 0)
        result.append({
            "id": item.id,
            "account_id": item.account_id,
            "account_number": item.account.account_number,
            "as_of_date": item.as_of_date.isoformat(),
            "total_balance": money_string(item.total_balance_cents),
            "currency": item.currency,
            "source_type": item.source_type,
            "eligible_for_closing": item.eligible_for_closing,
            "remark": item.remark,
            "evidence_count": evidence_count,
            "evidence_complete": evidence_count > 0,
        })
    return result


@router.post("/balance-snapshots", status_code=201)
def create_balance_snapshot(payload: BalanceSnapshotCreate, db: Session = Depends(get_db)) -> dict:
    account = db.get(SubAccount, payload.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Sub Account不存在")
    closing_eligible = is_quarter_end(payload.as_of_date) or account.end_date == payload.as_of_date
    if payload.eligible_for_closing and not closing_eligible:
        raise HTTPException(status_code=400, detail="非季末且非实际退出日的余额只能保存为普通快照")
    item = BalanceSnapshot(
        account_id=payload.account_id,
        as_of_date=payload.as_of_date,
        total_balance_cents=to_cents(payload.total_balance),
        eligible_for_closing=payload.eligible_for_closing and closing_eligible,
        source_type="MANUAL",
        remark=payload.remark,
    )
    db.add(item)
    _commit(db, "该账户在同一天已经有余额快照")
    db.refresh(item)
    return {
        "id": item.id,
        **payload.model_dump(exclude={"total_balance", "eligible_for_closing"}, mode="json"),
        "total_balance": money_string(item.total_balance_cents),
        "eligible_for_closing": item.eligible_for_closing,
        "currency": "HKD",
    }
