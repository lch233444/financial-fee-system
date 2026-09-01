from __future__ import annotations

import re


DELETE_GUARD_REQUIRED_FRAGMENTS = {
    "trg_client_delete_no_cascade": (
        "beforedeleteonclients",
        "fromsub_accountsasaccountwhereaccount.client_id=old.id",
        "fromquarterly_settlementsassettlementwheresettlement.client_id=old.id",
        "frominvoicesasinvoicewhereinvoice.client_id=old.id",
        "fromattachmentsasattachment",
        "='client'andattachment.entity_id=old.id",
        "fromexport_recordsasexport_record",
        "='client'andexport_record.entity_id=old.id",
        "raise(abort,'client_delete_would_cascade')",
    ),
    "trg_account_delete_no_cascade": (
        "beforedeleteonsub_accounts",
        "fromtransactionsastransaction_record",
        "transaction_record.account_id=old.id",
        "frombalance_snapshotsassnapshotwheresnapshot.account_id=old.id",
        "fromstatement_importsasstatement",
        "statement.confirmed_account_id=old.id",
        "fromsettlement_account_linesaslinewhereline.account_id=old.id",
        "fromattachmentsasattachment",
        "in('account','sub_account')andattachment.entity_id=old.id",
        "fromexport_recordsasexport_record",
        "in('account','sub_account')andexport_record.entity_id=old.id",
        "raise(abort,'account_delete_would_cascade')",
    ),
    "trg_statement_import_delete_no_snapshot": (
        "beforedeleteonstatement_imports",
        "frombalance_snapshotsassnapshot",
        "snapshot.statement_import_id=old.id",
        "joinsettlement_account_linesasline",
        "fromstatement_importsasdependent",
        "dependent.duplicate_of_id=old.id",
        "fromattachmentsasattachment",
        "='statement_import'andattachment.entity_id=old.id",
        "fromexport_recordsasexport_record",
        "='statement_import'andexport_record.entity_id=old.id",
        "raise(abort,'statement_import_delete_has_snapshot')",
    ),
    "trg_snapshot_delete_no_confirmed_import": (
        "beforedeleteonbalance_snapshots",
        "fromstatement_importsasstatement",
        "statement.confirmed_snapshot_id=old.id",
        "fromsettlement_account_linesasline",
        "fromattachmentsasattachment",
        "='snapshot'andattachment.entity_id=old.id",
        "fromexport_recordsasexport_record",
        "='snapshot'andexport_record.entity_id=old.id",
        "raise(abort,'snapshot_delete_has_confirmed_import')",
    ),
}


def normalized_trigger_sql(sql: str) -> str:
    return re.sub(r"\s+", "", sql.casefold())


def delete_guard_trigger_sql_is_current(trigger_sql: dict[str, str]) -> bool:
    for trigger_name, required_fragments in DELETE_GUARD_REQUIRED_FRAGMENTS.items():
        sql = trigger_sql.get(trigger_name, "")
        if not sql or "--" in sql or "/*" in sql or "*/" in sql:
            return False
        normalized = normalized_trigger_sql(sql)
        if any(fragment not in normalized for fragment in required_fragments):
            return False
    return True
