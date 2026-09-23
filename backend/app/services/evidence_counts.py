from collections.abc import Iterable

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..models import Attachment, BalanceSnapshot


def snapshot_evidence_counts(db: Session, snapshot_ids: Iterable[int | None]) -> dict[int, int]:
    """Count current attachments plus the registered import source, in bounded batches.

    This is a display count, not the physical-file validation required to Finalize.
    Missing snapshot IDs deliberately have no entry and are displayed as zero.
    """
    ids = sorted({item for item in snapshot_ids if item is not None})
    counts: dict[int, int] = {}
    for start in range(0, len(ids), 500):
        rows = db.execute(
            select(BalanceSnapshot.id, BalanceSnapshot.statement_import_id, func.count(Attachment.id))
            .outerjoin(Attachment, and_(
                Attachment.entity_type == "SNAPSHOT",
                Attachment.entity_id == BalanceSnapshot.id,
                Attachment.superseded.is_(False),
            ))
            .where(BalanceSnapshot.id.in_(ids[start:start + 500]))
            .group_by(BalanceSnapshot.id, BalanceSnapshot.statement_import_id)
        )
        counts.update({snapshot_id: count + int(source_id is not None)
                       for snapshot_id, source_id, count in rows})
    return counts
