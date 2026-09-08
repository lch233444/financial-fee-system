from sqlalchemy.orm import Session

from ..models import Invoice, QuarterlySettlement


def settlements_follow_original(db: Session, original: Invoice, settlements: list[QuarterlySettlement], *, same_sources: bool = False) -> bool:
    """Cover every old source with its replacement; allow additional, distinct platforms."""
    originals = {source.settlement_id: db.get(QuarterlySettlement, source.settlement_id) for source in original.sources}
    if not originals or any(item is None for item in originals.values()):
        return False
    if same_sources:
        return (
            len(settlements) == len(originals)
            and {item.id for item in settlements} == set(originals)
            and all(item.status == "FINALIZED" for item in settlements)
        )
    original_platforms = {item.platform_id for item in originals.values()}
    matched_ids: set[int] = set()
    seen_platforms: set[int] = set()
    group = (original.client_id, original.year, original.quarter, original.fee_plan_id)
    for settlement in settlements:
        if (
            settlement.status != "FINALIZED"
            or (settlement.client_id, settlement.year, settlement.quarter, settlement.fee_plan_id) != group
            or settlement.platform_id in seen_platforms
        ):
            return False
        seen_platforms.add(settlement.platform_id)
        if settlement.platform_id not in original_platforms:
            continue
        current = settlement
        visited = {current.id}
        while current.replaces_settlement_id is not None:
            previous_id = current.replaces_settlement_id
            if previous_id in visited:
                return False
            visited.add(previous_id)
            if previous_id in originals:
                if previous_id in matched_ids:
                    return False
                matched_ids.add(previous_id)
                break
            current = db.get(QuarterlySettlement, previous_id)
            if current is None:
                return False
        else:
            return False
    return matched_ids == set(originals)
