"""One-time, offline conversion of an explicitly authorized TEST restore copy.

Run only on an isolated restored data directory. Formal cutover uses a verified
complete data package made from this converted copy, preserving the old backup.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, joinedload
from app.models import Invoice
from app.services.backup import _validate_sqlite_database
from app.services.invoice_archive import invoice_archive_paths
from app.services.pdf_invoice import generate_invoice_pdf
from app.services.release_100_contract import RELEASE_100_REVISION


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _rows(connection):
    return {name: connection.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall()
            for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()}


def convert_test_copy(data_root: Path, *, acknowledged_test_copy: bool, renderer=generate_invoice_pdf):
    if not acknowledged_test_copy:
        raise ValueError('必须明确确认这是获得授权的隔离测试资料副本')
    data_root = data_root.resolve(strict=True)
    database = data_root / 'database/financial_system.sqlite3'
    if data_root.is_symlink() or database.is_symlink():
        raise ValueError('测试转换路径不能为链接')
    _validate_sqlite_database(database)
    sql = sqlite3.connect(database, timeout=10)
    sql.execute('PRAGMA foreign_keys=ON')
    generated = []
    try:
        sql.execute('BEGIN IMMEDIATE')
        if sql.execute('SELECT version_num FROM alembic_version').fetchone() != (RELEASE_100_REVISION,):
            raise ValueError('测试副本必须先完整升级到1.0结构')
        if sql.execute("SELECT 1 FROM invoices WHERE lifecycle_status='ISSUING'").fetchone():
            raise ValueError('存在出具中账单，须先恢复后转换')
        before = _rows(sql)
        dates = {}
        invoices = sql.execute('SELECT id,invoice_number,issue_date FROM invoices WHERE invoice_number IS NOT NULL ORDER BY issue_date,id').fetchall()
        for _, number, issue_date in invoices:
            if not issue_date:
                raise ValueError('已编号测试账单缺少出具日期')
            dates[number] = date.fromisoformat(issue_date).isoformat()
        for number, in sql.execute('SELECT invoice_number FROM invoice_issue_attempts ORDER BY id').fetchall():
            if number in dates or re.fullmatch(r'\d{9}', number, re.ASCII):
                continue
            match = re.search(r'-(\d{8})-\d+$', number)
            if not match:
                raise ValueError('历史预留编号没有可核实出具日期，停止转换')
            raw = match.group(1)
            dates[number] = date(int(raw[:4]), int(raw[4:6]), int(raw[6:])).isoformat()
        counters = dict(sql.execute('SELECT issue_month,last_number FROM invoice_monthly_sequences'))
        all_numbers = {row[0] for row in sql.execute('SELECT invoice_number FROM invoices UNION SELECT invoice_number FROM invoice_issue_attempts') if row[0]}
        for number in all_numbers:
            if re.fullmatch(r'\d{9}', number, re.ASCII):
                date(int(number[:4]), int(number[4:6]), 1)
                counters[number[:6]] = max(counters.get(number[:6], 0), int(number[6:]))
        mapping = {}
        for number, day in sorted(dates.items(), key=lambda pair: pair[1]):
            if re.fullmatch(r'\d{9}', number, re.ASCII):
                continue
            month = day[:7].replace('-', '')
            counter = counters.get(month, 0) + 1
            if counter > 999:
                raise ValueError('该月份三位流水容量不足，停止转换')
            mapping[number] = f'{month}{counter:03d}'
            counters[month] = counter
        if not mapping:
            sql.rollback()
            return {'converted_invoices': 0, 'converted_reservations': 0, 'mapping': {}}
        trigger_name = 'trg_invoice_issue_metadata_guard'
        original_triggers = dict(sql.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'").fetchall())
        # Only this restored test copy permits the acknowledged number rewrite.
        # The guard is restored in the same transaction before it can commit.
        sql.execute(f'DROP TRIGGER {trigger_name}')
        engine = create_engine(f'sqlite:///{database.as_posix()}')
        converted_invoices = 0
        try:
            with Session(engine) as db:
                for invoice_id, old_number, _ in invoices:
                    if old_number not in mapping:
                        continue
                    item = db.scalar(select(Invoice).options(joinedload(Invoice.company), joinedload(Invoice.payee_company), joinedload(Invoice.client)).where(Invoice.id == invoice_id))
                    notice = SimpleNamespace(company=item.company, payee_company=item.payee_company, client=item.client,
                        invoice_number=mapping[old_number], issue_date=item.issue_date, due_date=item.due_date, amount_cents=item.amount_cents)
                    paths = invoice_archive_paths(notice.invoice_number, data_root / 'output/pdf')
                    for language, path in paths.items():
                        if not path.parent.resolve().is_relative_to(data_root) or path.exists():
                            raise ValueError('新PDF目标不在测试副本内或已存在，停止覆盖')
                        # Register before rendering so an interrupted renderer's partial file is removed.
                        generated.append(path)
                        renderer(invoice=notice, language=language, output_path=path)
                        sql.execute("INSERT INTO export_records(export_type,entity_type,entity_id,stored_path,sha256,language,created_at,updated_at) VALUES ('PDF_INVOICE','INVOICE',?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", (invoice_id,str(path),digest(path),language))
                    sql.execute('UPDATE invoices SET invoice_number=?,pdf_paths_json=? WHERE id=?',
                        (notice.invoice_number,json.dumps({lang:str(path) for lang,path in paths.items()}),invoice_id))
                    converted_invoices += 1
        finally:
            engine.dispose()
        attempts = 0
        for old, new in mapping.items():
            attempts += sql.execute('UPDATE invoice_issue_attempts SET invoice_number=? WHERE invoice_number=?', (new,old)).rowcount
        for month, last in counters.items():
            sql.execute('INSERT INTO invoice_monthly_sequences(issue_month,last_number) VALUES (?,?) ON CONFLICT(issue_month) DO UPDATE SET last_number=excluded.last_number', (month,last))
        sql.execute(original_triggers[trigger_name])
        if dict(sql.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'").fetchall()) != original_triggers:
            raise ValueError('测试转换后保护结构改变，停止提交')
        after = _rows(sql)
        changed_tables = {'invoices','invoice_issue_attempts','invoice_monthly_sequences','export_records'}
        for table, rows in before.items():
            if table not in changed_tables and after[table] != rows:
                raise ValueError(f'测试转换意外改变了{table}，停止提交')
        for table, allowed in [('invoices', {'invoice_number','pdf_paths_json'}), ('invoice_issue_attempts', {'invoice_number'})]:
            columns = [row[1] for row in sql.execute(f'PRAGMA table_info("{table}")')]
            indexes = [index for index,name in enumerate(columns) if name not in allowed]
            project = lambda rows: [tuple(row[index] for index in indexes) for row in rows]
            if project(before[table]) != project(after[table]):
                raise ValueError('测试转换改变了收费、状态、日期或台账关系，停止提交')
        if sql.execute('PRAGMA foreign_key_check').fetchall() or sql.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('测试转换后数据库完整性异常')
        report = {'converted_invoices': converted_invoices, 'converted_reservations': attempts, 'mapping': mapping,
            'financial_rows_unchanged': True, 'triggers_restored': True,
            'original_archives_preserved': True, 'new_pdf_files': len(generated)}
        sql.execute("INSERT INTO audit_events(created_at,action,entity_type,details_json) VALUES (CURRENT_TIMESTAMP,'TEST_INVOICE_NUMBER_CONVERSION','SYSTEM',?)", (json.dumps(report,ensure_ascii=False),))
        sql.commit()
    except BaseException:
        sql.rollback()
        for path in generated:
            path.unlink(missing_ok=True)
        raise
    finally:
        sql.close()
    _validate_sqlite_database(database)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test-copy', required=True, type=Path)
    parser.add_argument('--acknowledge-test-copy', action='store_true')
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    result = convert_test_copy(args.test_copy, acknowledged_test_copy=args.acknowledge_test_copy)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key,value in result.items() if key != 'mapping'}, ensure_ascii=False))
