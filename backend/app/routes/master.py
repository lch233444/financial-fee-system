from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
from ..services.settlement_period import transaction_locked_settlement_id
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
    InvoiceCorrection,
    Platform,
    QuarterlySettlement,
    SettlementAccountLine,
    StatementImport,
    SubAccount,
    TransactionRecord,
)
from ..money import money_string, to_cents
from ..services.calculation import is_quarter_end
from ..services.entity_ids import EntityIdAllocationError, allocate_entity_id
from ..services.client_identity import normalized_client_name
from ..schemas import (
    AccountCreate,
    AccountUpdate,
    BalanceSnapshotCreate,
    ClientCreate,
    ClientMergeRequest,
    ClientUpdate,
    CompanyCreate,
    FCCreate,
    FeePlanCreate,
    PlatformCreate,
    TransactionCreate,
    TransactionUpdate,
)


router = APIRouter(prefix="/api", tags=["master-data"])


def _commit(db: Session, message: str = "资料重复或关联不正确") -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=message) from exc


def _is_sqlite_busy(exc: OperationalError) -> bool:
    message = str(exc.orig).casefold()
    return (
        "database is locked" in message
        or "database table is locked" in message
        or "database is busy" in message
    )


def _begin_immediate(db: Session) -> None:
    try:
        db.execute(text("BEGIN IMMEDIATE"))
    except OperationalError as exc:
        db.rollback()
        if _is_sqlite_busy(exc):
            raise HTTPException(
                status_code=409,
                detail="数据库正在处理另一笔财务写入，请稍后重试",
            ) from exc
        raise


def _transaction_audit_values(item: TransactionRecord) -> dict:
    return {
        "account_id": item.account_id,
        "transaction_date": item.transaction_date.isoformat(),
        "transaction_type": item.transaction_type,
        "amount_cents": item.amount_cents,
        "remark": item.remark,
    }


def _next_entity_id(db: Session, model: type) -> int:
    try:
        return allocate_entity_id(db, model)
    except EntityIdAllocationError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="系统ID序号记录异常，已停止新增，请联系管理员检查数据库",
        ) from exc


def _normalized_entity_type(model):
    return func.replace(
        func.replace(func.upper(func.trim(model.entity_type)), "-", "_"),
        " ",
        "_",
    )


def _logical_reference_labels(
    db: Session,
    *,
    entity_id: int,
    entity_types: tuple[str, ...],
) -> list[str]:
    references: list[str] = []
    for label, model in (("附件记录", Attachment), ("导出记录", ExportRecord)):
        if db.scalar(
            select(model.id)
            .where(
                _normalized_entity_type(model).in_(entity_types),
                model.entity_id == entity_id,
            )
            .limit(1)
        ) is not None:
            references.append(label)
    return references


def _controlled_delete_commit_outcome(
    *,
    model,
    item_id: int,
    deleted_entity_type: str,
) -> str:
    """Prove whether an uncertain master-data delete commit took effect.

    A database driver can report an error after SQLite has already committed.
    Use a fresh read transaction so the API never reports a completed delete as
    failed, while ambiguous or conflicting evidence remains fail-closed.
    """

    try:
        with SessionLocal() as check_db:
            check_db.execute(text("BEGIN"))
            row_exists = check_db.get(model, item_id) is not None
            matching_audits = []
            for audit in check_db.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "MASTER_DATA_DELETED",
                    AuditEvent.entity_type == "MASTER_DATA",
                    AuditEvent.entity_id.is_(None),
                )
            ).all():
                details = audit.details_json
                if not isinstance(details, dict):
                    continue
                if (
                    details.get("deleted_entity_type") == deleted_entity_type
                    and type(details.get("deleted_entity_id")) is int
                    and details.get("deleted_entity_id") == item_id
                ):
                    matching_audits.append(audit)
            check_db.rollback()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"{deleted_entity_type}删除提交结果无法自动确认，"
                "请停止操作并检查数据库"
            ),
        ) from exc

    if not row_exists and len(matching_audits) == 1:
        return "COMMITTED"
    if row_exists and not matching_audits:
        return "NOT_COMMITTED"
    raise HTTPException(
        status_code=500,
        detail=(
            f"{deleted_entity_type}删除提交结果存在冲突证据，"
            "请停止操作并检查数据库"
        ),
    )


def _commit_controlled_delete(
    db: Session,
    *,
    model,
    item_id: int,
    entity_name: str,
    deleted_entity_type: str,
) -> None:
    try:
        db.commit()
    except SQLAlchemyError as exc:
        try:
            db.rollback()
        except Exception:
            # The fresh-session proof below is authoritative for the persisted
            # outcome even when the request Session cannot roll back cleanly.
            pass
        outcome = _controlled_delete_commit_outcome(
            model=model,
            item_id=item_id,
            deleted_entity_type=deleted_entity_type,
        )
        if outcome == "COMMITTED":
            return
        if isinstance(exc, IntegrityError):
            raise HTTPException(
                status_code=409,
                detail=f"{entity_name}已被其他资料引用，不能删除",
            ) from exc
        if isinstance(exc, OperationalError) and _is_sqlite_busy(exc):
            raise HTTPException(
                status_code=409,
                detail="数据库正在处理另一笔财务写入，请稍后重试",
            ) from exc
        raise


def _execute_controlled_delete(db: Session, *, model, item_id: int, entity_name: str):
    try:
        return db.execute(delete(model).where(model.id == item_id))
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"{entity_name}已被其他资料引用，不能删除",
        ) from exc
    except OperationalError as exc:
        db.rollback()
        if _is_sqlite_busy(exc):
            raise HTTPException(
                status_code=409,
                detail="数据库正在处理另一笔财务写入，请稍后重试",
            ) from exc
        raise


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
            ("Invoice", select(Invoice.id).where((Invoice.company_id == company_id) | (Invoice.payee_company_id == company_id)).limit(1)),
            ("Invoice收款公司更正", select(InvoiceCorrection.id).where(InvoiceCorrection.target_company_id == company_id).limit(1)),
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
    _begin_immediate(db)
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
    item = Client(id=_next_entity_id(db, Client), **payload.model_dump())
    db.add(item)
    _commit(db)
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


@router.post("/clients/{client_id}/merge")
def merge_client(client_id: int, payload: ClientMergeRequest, db: Session = Depends(get_db)) -> dict:
    """Merge explicitly confirmed duplicate identities before any settlement.

    Account IDs, plans, snapshots, imports and their history remain intact.
    A deletion audit also prevents the retired customer ID from being reused.
    """
    _begin_immediate(db)
    if client_id == payload.target_client_id:
        raise HTTPException(status_code=400, detail="不能将客户合并到自己")
    source, target = db.get(Client, client_id), db.get(Client, payload.target_client_id)
    if source is None or target is None:
        raise HTTPException(status_code=404, detail="来源或保留客户不存在，请刷新后核对")
    if normalized_client_name(source.name) != normalized_client_name(target.name):
        raise HTTPException(status_code=409, detail="客户姓名不一致，不能作为重复客户合并")
    if "CLOSED" in (source.status, target.status) or (source.status == "ACTIVE" and target.status != "ACTIVE"):
        raise HTTPException(status_code=409, detail="请保留有效客户档案；已关闭客户不能在此合并")
    fields = ("company_id", "fc_id", "management_start_date", "contact")
    conflicts = [field for field in fields if getattr(source, field) is not None
                 and getattr(target, field) is not None and getattr(source, field) != getattr(target, field)]
    if conflicts:
        raise HTTPException(status_code=409, detail=f"客户资料冲突，不能直接合并：{'、'.join(conflicts)}")
    ids = (source.id, target.id)
    if (db.scalar(select(QuarterlySettlement.id).where(QuarterlySettlement.client_id.in_(ids)).limit(1))
        or db.scalar(select(Invoice.id).where(Invoice.client_id.in_(ids)).limit(1))
        or db.scalar(select(SettlementAccountLine.id).join(SubAccount, SettlementAccountLine.account_id == SubAccount.id)
                     .where(SubAccount.client_id.in_(ids)).limit(1))):
        raise HTTPException(status_code=409, detail="客户已存在结算或Invoice记录，不能直接合并或改写历史归属")
    if _logical_reference_labels(db, entity_id=source.id, entity_types=("CLIENT",)):
        raise HTTPException(status_code=409, detail="来源客户有直接关联的附件或导出记录，不能直接合并")

    def values(client: Client) -> dict:
        return {column.name: (value.isoformat() if hasattr(value, "isoformat") else value)
                for column in Client.__table__.columns
                for value in (getattr(client, column.name),)}

    source_values, target_before = values(source), values(target)
    accounts = db.scalars(select(SubAccount).where(SubAccount.client_id == source.id).order_by(SubAccount.id)).all()
    moved_ids = [account.id for account in accounts]
    company_id = target.company_id if target.company_id is not None else source.company_id
    if company_id is not None and any(account.fee_plan_id is not None
                                     and account.fee_plan.company_id != company_id for account in accounts):
        raise HTTPException(status_code=409, detail="子账户收费计划与保留客户的Company不一致，不能合并")
    for field in fields:
        if getattr(target, field) is None:
            setattr(target, field, getattr(source, field))
    for account in accounts:
        account.client_id = target.id
    db.flush()
    result = _execute_controlled_delete(db, model=Client, item_id=source.id, entity_name="Client")
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="来源客户状态发生变化，合并未完成")
    details = {"source_client": source_values, "target_before": target_before,
               "target_client_id": target.id, "moved_account_ids": moved_ids, "reason": payload.reason}
    db.add(AuditEvent(action="CLIENT_MERGED", entity_type="CLIENT", entity_id=target.id, details_json=details))
    db.add(AuditEvent(action="MASTER_DATA_DELETED", entity_type="MASTER_DATA", entity_id=None, details_json={
        "deleted_entity_type": "CLIENT", "deleted_entity_id": client_id,
        "name": source_values["name"], "previous_status": source_values["status"],
        "merged_into_client_id": target.id, "reason": payload.reason,
    }))
    _commit_controlled_delete(db, model=Client, item_id=client_id, entity_name="Client", deleted_entity_type="CLIENT")
    return {"status": "merged", "source_client_id": client_id, "target_client_id": target.id,
            "moved_account_ids": moved_ids}


@router.delete("/clients/{client_id}")
def delete_client(client_id: int, db: Session = Depends(get_db)) -> dict:
    _begin_immediate(db)
    item = db.get(Client, client_id)
    if not item:
        db.rollback()
        raise HTTPException(status_code=404, detail="Client不存在")

    references = [
        label
        for label, query in (
            ("Sub Account", select(SubAccount.id).where(SubAccount.client_id == client_id).limit(1)),
            (
                "Settlement",
                select(QuarterlySettlement.id)
                .where(QuarterlySettlement.client_id == client_id)
                .limit(1),
            ),
            ("Invoice", select(Invoice.id).where(Invoice.client_id == client_id).limit(1)),
        )
        if db.scalar(query) is not None
    ]
    references.extend(
        _logical_reference_labels(
            db,
            entity_id=client_id,
            entity_types=("CLIENT",),
        )
    )
    if references:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Client已被以下资料引用，不能删除：{'、'.join(references)}",
        )

    item_name = item.name
    item_status = item.status
    result = _execute_controlled_delete(
        db,
        model=Client,
        item_id=client_id,
        entity_name="Client",
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=404, detail="Client不存在")
    db.add(
        AuditEvent(
            action="MASTER_DATA_DELETED",
            entity_type="MASTER_DATA",
            entity_id=None,
            details_json={
                "deleted_entity_type": "CLIENT",
                "deleted_entity_id": client_id,
                "name": item_name,
                "previous_status": item_status,
            },
        )
    )
    _commit_controlled_delete(
        db,
        model=Client,
        item_id=client_id,
        entity_name="Client",
        deleted_entity_type="CLIENT",
    )
    return {"status": "deleted", "id": client_id}


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
    _begin_immediate(db)
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
    if payload.status == "ACTIVE" and (
        not payload.platform_id or not payload.fee_plan_id or not payload.start_date
    ):
        raise HTTPException(
            status_code=400,
            detail="Active Sub Account必须补全Platform、Fee Plan和开始管理日期",
        )
    if payload.status == "ACTIVE" and client.status != "ACTIVE":
        raise HTTPException(status_code=400, detail="Client必须先补全并设为Active")
    if payload.start_date and payload.end_date and payload.end_date < payload.start_date:
        raise HTTPException(status_code=400, detail="账户结束日期不能早于开始日期")
    item = SubAccount(
        id=_next_entity_id(db, SubAccount),
        **payload.model_dump(),
        currency="HKD",
    )
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
    if status == "ACTIVE" and (not platform_id or not fee_plan_id or not start_date):
        raise HTTPException(
            status_code=400,
            detail="Active Sub Account必须补全Platform、Fee Plan和开始管理日期",
        )
    if status == "ACTIVE" and item.client.status != "ACTIVE":
        raise HTTPException(status_code=400, detail="Client必须先补全并设为Active")
    if start_date and end_date and end_date < start_date:
        raise HTTPException(status_code=400, detail="账户结束日期不能早于开始日期")

    end_date_changed = "end_date" in changes and end_date != item.end_date
    snapshot_changes: list[tuple[BalanceSnapshot, bool, bool]] = []
    if end_date_changed:
        if end_date is not None:
            conflicting_lines = db.scalars(
                select(SettlementAccountLine)
                .join(
                    QuarterlySettlement,
                    QuarterlySettlement.id == SettlementAccountLine.settlement_id,
                )
                .where(
                    SettlementAccountLine.account_id == item.id,
                    QuarterlySettlement.status != "VOID",
                    SettlementAccountLine.closing_date > end_date,
                )
                .order_by(SettlementAccountLine.closing_date, SettlementAccountLine.id)
            ).all()
            if conflicting_lines:
                joined_periods = "、".join(
                    f"Settlement #{line.settlement_id}（Closing {line.closing_date.isoformat()}）"
                    for line in conflicting_lines
                )
                raise HTTPException(
                    status_code=409,
                    detail=f"结束日期早于未作废Settlement的账户Closing Date：{joined_periods}",
                )
        snapshots = db.scalars(
            select(BalanceSnapshot).where(BalanceSnapshot.account_id == item.id)
        ).all()
        for snapshot in snapshots:
            date_is_eligible = (
                is_quarter_end(snapshot.as_of_date) or snapshot.as_of_date == end_date
            )
            if snapshot.source_type == "STATEMENT_IMPORT":
                updated_eligibility = date_is_eligible
            else:
                # A false manual flag may be an explicit finance opt-out. Never
                # promote it automatically; only revoke a true flag that is no
                # longer a quarter-end or the account's actual end date.
                updated_eligibility = snapshot.eligible_for_closing and date_is_eligible
            if updated_eligibility != snapshot.eligible_for_closing:
                snapshot_changes.append(
                    (snapshot, snapshot.eligible_for_closing, updated_eligibility)
                )

        changed_snapshot_ids = {
            snapshot.id for snapshot, _old, _updated in snapshot_changes
        }
        if changed_snapshot_ids:
            referenced_lines = db.scalars(
                select(SettlementAccountLine).where(
                    or_(
                        SettlementAccountLine.beginning_snapshot_id.in_(changed_snapshot_ids),
                        SettlementAccountLine.closing_snapshot_id.in_(changed_snapshot_ids),
                    )
                )
            ).all()
            referenced_snapshot_ids = sorted(
                {
                    snapshot_id
                    for line in referenced_lines
                    for snapshot_id in (line.beginning_snapshot_id, line.closing_snapshot_id)
                    if snapshot_id in changed_snapshot_ids
                }
            )
            if referenced_snapshot_ids:
                joined_ids = "、".join(
                    f"#{snapshot_id}" for snapshot_id in referenced_snapshot_ids
                )
                raise HTTPException(
                    status_code=409,
                    detail=f"结束日期会改变已被Settlement引用的Snapshot Closing资格：{joined_ids}",
                )

        for snapshot, _old_eligibility, updated_eligibility in snapshot_changes:
            snapshot.eligible_for_closing = updated_eligibility
            if snapshot.source_type == "STATEMENT_IMPORT":
                snapshot.remark = (
                    "季末/退出日Closing候选"
                    if updated_eligibility
                    else "非季末余额快照，不可直接作为Closing"
                )

        db.add(
            AuditEvent(
                action="ACCOUNT_END_DATE_UPDATED",
                entity_type="ACCOUNT",
                entity_id=item.id,
                details_json={
                    "old_end_date": item.end_date.isoformat() if item.end_date else None,
                    "new_end_date": end_date.isoformat() if end_date else None,
                    "snapshot_eligibility_changes": [
                        {
                            "snapshot_id": snapshot.id,
                            "old_eligible_for_closing": old_eligibility,
                            "new_eligible_for_closing": updated_eligibility,
                        }
                        for snapshot, old_eligibility, updated_eligibility in snapshot_changes
                    ],
                },
            )
        )
    for key, value in changes.items():
        setattr(item, key, value)
    _commit(db)
    return {"id": item.id, **payload.model_dump(exclude_unset=True, mode="json")}


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db)) -> dict:
    _begin_immediate(db)
    item = db.get(SubAccount, account_id)
    if not item:
        db.rollback()
        raise HTTPException(status_code=404, detail="Sub Account不存在")

    references = [
        label
        for label, query in (
            (
                "资金流水",
                select(TransactionRecord.id)
                .where(TransactionRecord.account_id == account_id)
                .limit(1),
            ),
            (
                "余额快照",
                select(BalanceSnapshot.id)
                .where(BalanceSnapshot.account_id == account_id)
                .limit(1),
            ),
            (
                "已确认账单导入",
                select(StatementImport.id)
                .where(
                    StatementImport.confirmed_account_id == account_id,
                )
                .limit(1),
            ),
            (
                "Settlement账户明细",
                select(SettlementAccountLine.id)
                .where(SettlementAccountLine.account_id == account_id)
                .limit(1),
            ),
        )
        if db.scalar(query) is not None
    ]
    references.extend(
        _logical_reference_labels(
            db,
            entity_id=account_id,
            entity_types=("ACCOUNT", "SUB_ACCOUNT"),
        )
    )
    if references:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Sub Account已被以下资料引用，不能删除：{'、'.join(references)}",
        )

    audit_details = {
        "deleted_entity_type": "SUB_ACCOUNT",
        "deleted_entity_id": account_id,
        "client_id": item.client_id,
        "platform_id": item.platform_id,
        "fee_plan_id": item.fee_plan_id,
        "account_number": item.account_number,
        "previous_status": item.status,
    }
    result = _execute_controlled_delete(
        db,
        model=SubAccount,
        item_id=account_id,
        entity_name="Sub Account",
    )
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=404, detail="Sub Account不存在")
    db.add(
        AuditEvent(
            action="MASTER_DATA_DELETED",
            entity_type="MASTER_DATA",
            entity_id=None,
            details_json=audit_details,
        )
    )
    _commit_controlled_delete(
        db,
        model=SubAccount,
        item_id=account_id,
        entity_name="Sub Account",
        deleted_entity_type="SUB_ACCOUNT",
    )
    return {"status": "deleted", "id": account_id}


@router.get("/transactions")
def list_transactions(account_id: int | None = None, db: Session = Depends(get_db)) -> list[dict]:
    query = select(TransactionRecord).order_by(TransactionRecord.transaction_date.desc(), TransactionRecord.id.desc())
    if account_id:
        query = query.where(TransactionRecord.account_id == account_id)
    items = db.scalars(query).all()
    result = []
    for item in items:
        account = item.account
        locked_settlement_id = transaction_locked_settlement_id(
            db,
            account_id=item.account_id,
            transaction_date=item.transaction_date,
        )
        attachment_count = db.scalar(
            select(func.count(Attachment.id)).where(
                Attachment.entity_type == "TRANSACTION", Attachment.entity_id == item.id
            )
        ) or 0
        evidence_count = int(attachment_count) + (1 if item.attachment_id is not None else 0)
        result.append({
            "id": item.id,
            "account_id": item.account_id,
            "account_number": account.account_number,
            "client_id": account.client_id,
            "client_name": account.client.name,
            "platform_id": account.platform_id,
            "platform_name": account.platform.name if account.platform else None,
            "fee_plan_id": account.fee_plan_id,
            "fee_plan_name": account.fee_plan.name if account.fee_plan else None,
            "scheme_name": account.scheme_name,
            "transaction_date": item.transaction_date.isoformat(),
            "transaction_type": item.transaction_type,
            "amount": money_string(item.amount_cents),
            "remark": item.remark,
            "evidence_count": evidence_count,
            "evidence_complete": evidence_count > 0,
            "correction_allowed": locked_settlement_id is None,
            "locked_settlement_id": locked_settlement_id,
        })
    return result


@router.post("/transactions", status_code=201)
def create_transaction(payload: TransactionCreate, db: Session = Depends(get_db)) -> dict:
    account = db.get(SubAccount, payload.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Sub Account不存在")
    if account.start_date and payload.transaction_date < account.start_date:
        raise HTTPException(status_code=400, detail="交易日期不能早于账户开始管理日期")
    locked_settlement_id = transaction_locked_settlement_id(
        db, account_id=payload.account_id, transaction_date=payload.transaction_date
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


@router.patch("/transactions/{transaction_id}")
def update_transaction(
    transaction_id: int,
    payload: TransactionUpdate,
    db: Session = Depends(get_db),
) -> dict:
    _begin_immediate(db)
    item = db.get(TransactionRecord, transaction_id)
    if item is None:
        db.rollback()
        raise HTTPException(status_code=404, detail="资金流水不存在")

    account = db.get(SubAccount, item.account_id)
    if account is None:
        db.rollback()
        raise HTTPException(status_code=409, detail="资金流水关联的Sub Account不存在")
    if account.start_date and payload.transaction_date < account.start_date:
        db.rollback()
        raise HTTPException(status_code=400, detail="资金生效日期不能早于账户开始管理日期")

    current_lock = transaction_locked_settlement_id(
        db,
        account_id=item.account_id,
        transaction_date=item.transaction_date,
    )
    if current_lock is not None:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"该资金流水已被Finalized Settlement #{current_lock}使用，不能直接更正；请先按顺序作废下游结算",
        )
    target_lock = transaction_locked_settlement_id(
        db,
        account_id=item.account_id,
        transaction_date=payload.transaction_date,
    )
    if target_lock is not None:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"更正后的日期落入Finalized Settlement #{target_lock}，不能直接更正；请先按顺序作废下游结算",
        )

    before = _transaction_audit_values(item)
    after = {
        "account_id": item.account_id,
        "transaction_date": payload.transaction_date.isoformat(),
        "transaction_type": payload.transaction_type,
        "amount_cents": to_cents(payload.amount),
        "remark": payload.remark,
    }
    if before == after:
        db.rollback()
        raise HTTPException(status_code=400, detail="更正后的资金流水与原记录相同")

    item.transaction_date = payload.transaction_date
    item.transaction_type = payload.transaction_type
    item.amount_cents = after["amount_cents"]
    item.remark = payload.remark
    db.add(
        AuditEvent(
            action="TRANSACTION_CORRECTED",
            entity_type="TRANSACTION",
            entity_id=item.id,
            details_json={
                "reason": payload.correction_reason,
                "before": before,
                "after": after,
            },
        )
    )
    _commit(db, "资金流水已进入Finalized结算期间，不能更正")
    db.refresh(item)

    attachment_count = db.scalar(
        select(func.count(Attachment.id)).where(
            Attachment.entity_type == "TRANSACTION",
            Attachment.entity_id == item.id,
        )
    ) or 0
    evidence_count = int(attachment_count) + (1 if item.attachment_id is not None else 0)
    return {
        "id": item.id,
        "account_id": item.account_id,
        "account_number": account.account_number,
        "client_id": account.client_id,
        "client_name": account.client.name,
        "platform_id": account.platform_id,
        "platform_name": account.platform.name if account.platform else None,
        "fee_plan_id": account.fee_plan_id,
        "fee_plan_name": account.fee_plan.name if account.fee_plan else None,
        "scheme_name": account.scheme_name,
        "transaction_date": item.transaction_date.isoformat(),
        "transaction_type": item.transaction_type,
        "amount": money_string(item.amount_cents),
        "remark": item.remark,
        "evidence_count": evidence_count,
        "evidence_complete": evidence_count > 0,
        "correction_allowed": True,
        "locked_settlement_id": None,
    }


@router.get("/balance-snapshots")
def list_balance_snapshots(account_id: int | None = None, db: Session = Depends(get_db)) -> list[dict]:
    query = select(BalanceSnapshot).order_by(BalanceSnapshot.as_of_date.desc())
    if account_id:
        query = query.where(BalanceSnapshot.account_id == account_id)
    items = db.scalars(query).all()
    result = []
    for item in items:
        account = item.account
        attachment_count = db.scalar(
            select(func.count(Attachment.id)).where(
                Attachment.entity_type == "SNAPSHOT", Attachment.entity_id == item.id
            )
        ) or 0
        evidence_count = int(attachment_count) + (1 if item.statement_import_id is not None else 0)
        result.append({
            "id": item.id,
            "account_id": item.account_id,
            "account_number": account.account_number,
            "client_id": account.client_id,
            "client_name": account.client.name,
            "platform_id": account.platform_id,
            "platform_name": account.platform.name if account.platform else None,
            "fee_plan_id": account.fee_plan_id,
            "fee_plan_name": account.fee_plan.name if account.fee_plan else None,
            "scheme_name": account.scheme_name,
            "as_of_date": item.as_of_date.isoformat(),
            "total_balance": money_string(item.total_balance_cents),
            "currency": item.currency,
            "source_type": item.source_type,
            "statement_import_id": item.statement_import_id,
            "holdings": item.holdings_json or [],
            "eligible_for_closing": item.eligible_for_closing,
            "remark": item.remark,
            "evidence_count": evidence_count,
            "evidence_complete": evidence_count > 0,
        })
    return result


@router.post("/balance-snapshots", status_code=201)
def create_balance_snapshot(payload: BalanceSnapshotCreate, db: Session = Depends(get_db)) -> dict:
    _begin_immediate(db)
    account = db.get(SubAccount, payload.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Sub Account不存在")
    closing_eligible = is_quarter_end(payload.as_of_date) or account.end_date == payload.as_of_date
    if payload.eligible_for_closing and not closing_eligible:
        raise HTTPException(status_code=400, detail="非季末且非实际退出日的余额只能保存为普通快照")
    item = BalanceSnapshot(
        id=_next_entity_id(db, BalanceSnapshot),
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
