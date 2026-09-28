"""Add an approved prefix to ONE issued invoice in an isolated restored copy.

Never run this against the live root. Formal application belongs to the later
authorized release, after backup, isolated validation and PDF review.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from pypdf import PdfReader
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, joinedload
from app.models import Invoice
from app.services.backup import _validate_sqlite_database
from app.services.invoice_archive import invoice_archive_paths
from app.services.invoice_number import invoice_number_prefix
from app.services.pdf_invoice import generate_invoice_pdf


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def rows(sql):
    return {name: sql.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall()
            for name, in sql.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()}


def pdf_without_number(path, number):
    reader = PdfReader(path)
    text = ''.join(''.join(page.extract_text() or '' for page in reader.pages).split())
    number = ''.join(number.split())
    if not reader.pages or text.count(number) != 1:
        raise ValueError('PDF必须能读出唯一的对应账单编号，停止转换')
    return text.replace(number, '', 1)


def prefix_copy(data_root: Path, *, live_root: Path, invoice_id: int,
                expected_number: str, expected_company: str, expected_fc: str,
                english_name: str, acknowledged_copy: bool, renderer=generate_invoice_pdf):
    if not acknowledged_copy:
        raise ValueError('必须明确确认这是授权的隔离恢复副本')
    if data_root.is_symlink():
        raise ValueError('隔离副本不能为链接')
    data_root = data_root.resolve(strict=True)
    live_root = live_root.resolve()
    database = data_root / 'database/financial_system.sqlite3'
    live_database = live_root / 'database/financial_system.sqlite3'
    if data_root.is_relative_to(live_root) or live_root.is_relative_to(data_root) or database.is_symlink() or (live_database.exists() and database.samefile(live_database)):
        raise ValueError('只能转换独立恢复副本，禁止操作正式数据根')
    if not re.fullmatch(r'[0-9]{9}', expected_number) or expected_number[-3:] == '000':
        raise ValueError('只接受明确指定的原九位数字编号')
    new_number = f'{invoice_number_prefix(expected_company, english_name)}-{expected_number}'
    _validate_sqlite_database(database)
    sql = sqlite3.connect(database, timeout=10)
    sql.execute('PRAGMA foreign_keys=ON')
    generated = []
    try:
        sql.execute('BEGIN IMMEDIATE')
        before = rows(sql)
        if sql.execute("SELECT 1 FROM invoices WHERE lifecycle_status='ISSUING'").fetchone():
            raise ValueError('副本存在签发中账单，须先处理后转换')
        target = sql.execute('''SELECT i.invoice_number,i.lifecycle_status,i.fc_id,f.name,c.name,i.issue_date,i.pdf_paths_json
            FROM invoices i JOIN fcs f ON f.id=i.fc_id
            JOIN companies c ON c.id=coalesce(i.payee_company_id,i.company_id) WHERE i.id=?''', (invoice_id,)).fetchone()
        if not target or target[:2] != (expected_number, 'ISSUED') or target[3:5] != (expected_fc, expected_company):
            raise ValueError('指定账单编号、状态、公司或中介人已改变，停止转换')
        if not target[5] or date.fromisoformat(target[5]).strftime('%Y%m') != expected_number[:6]:
            raise ValueError('原数字编号与出具月份不符')
        if sql.execute('SELECT 1 FROM invoices WHERE invoice_number=? UNION SELECT 1 FROM invoice_issue_attempts WHERE invoice_number=?', (new_number,new_number)).fetchone():
            raise ValueError('完整编号已存在，停止覆盖')
        old_paths = json.loads(target[6] or '{}')
        if set(old_paths) != {'zh','en'}:
            raise ValueError('原中英文PDF不完整')
        old_hashes, old_text = {}, {}
        for language, stored in old_paths.items():
            path = Path(stored)
            path = (path if path.is_absolute() else data_root / path).resolve()
            if not path.is_relative_to(data_root / 'output/pdf') or not path.is_file():
                raise ValueError('原PDF不在副本受控目录或已缺失')
            record = sql.execute("SELECT sha256 FROM export_records WHERE entity_type='INVOICE' AND entity_id=? AND language=? AND stored_path=? ORDER BY id DESC LIMIT 1", (invoice_id,language,stored)).fetchone()
            old_hashes[path] = digest(path)
            if not record or record[0] != old_hashes[path]:
                raise ValueError('原PDF登记哈希不匹配')
            old_text[language] = pdf_without_number(path, expected_number)
        engine = create_engine(f'sqlite:///{database.as_posix()}')
        try:
            with Session(engine) as db:
                item = db.scalar(select(Invoice).options(joinedload(Invoice.company), joinedload(Invoice.payee_company), joinedload(Invoice.client)).where(Invoice.id == invoice_id))
                notice = SimpleNamespace(company=item.company, payee_company=item.payee_company, client=item.client,
                    invoice_number=new_number, issue_date=item.issue_date, due_date=item.due_date, amount_cents=item.amount_cents)
                paths = invoice_archive_paths(new_number, data_root / 'output/pdf')
                for language, path in paths.items():
                    if not path.parent.resolve().is_relative_to(data_root):
                        raise ValueError('新PDF路径越出副本')
                    with path.open('xb'):
                        generated.append(path)
                    renderer(invoice=notice, language=language, output_path=path)
                    if pdf_without_number(path, new_number) != old_text[language]:
                        raise ValueError('新PDF除编号外的内容与原件不同，停止转换')
                    sql.execute("INSERT INTO export_records(export_type,entity_type,entity_id,stored_path,sha256,language,created_at,updated_at) VALUES ('PDF_INVOICE','INVOICE',?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", (invoice_id,str(path),digest(path),language))
        finally:
            engine.dispose()
        original_triggers = dict(sql.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
        guard = 'trg_invoice_issue_metadata_guard'
        # Only this acknowledged offline copy can rewrite the protected number;
        # restore the exact guard in the same transaction before committing.
        sql.execute(f'DROP TRIGGER {guard}')
        sql.execute('UPDATE invoices SET invoice_number=?,pdf_paths_json=? WHERE id=?', (new_number,json.dumps({k:str(v) for k,v in paths.items()}),invoice_id))
        sql.execute('UPDATE fcs SET name=? WHERE id=?', (english_name.strip(),target[2]))
        sql.execute(original_triggers[guard])
        if dict(sql.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'")) != original_triggers:
            raise ValueError('保护结构发生变化')
        after = rows(sql)
        for table in before:
            if table not in {'invoices','fcs','export_records'} and after[table] != before[table]:
                raise ValueError(f'意外改变{table}')
        for table, selected_id, allowed in [('invoices',invoice_id,{'invoice_number','pdf_paths_json'}), ('fcs',target[2],{'name'})]:
            columns = [row[1] for row in sql.execute(f'PRAGMA table_info("{table}")')]
            identity = columns.index('id')
            for old, current in zip(before[table], after[table], strict=True):
                if old[identity] != selected_id:
                    if old != current:
                        raise ValueError('非指定记录被修改')
                elif any(old[i] != current[i] for i,name in enumerate(columns) if name not in allowed):
                    raise ValueError('编号转换意外修改财务字段')
        if after['export_records'][:-2] != before['export_records'] or len(after['export_records']) != len(before['export_records']) + 2:
            raise ValueError('历史导出索引被修改')
        if any(digest(path) != sha for path,sha in old_hashes.items()):
            raise ValueError('历史PDF被修改')
        if sql.execute('PRAGMA foreign_key_check').fetchall() or sql.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('转换后数据库完整性异常')
        report = {'invoice_id':invoice_id,'old_number':expected_number,'new_number':new_number,
                  'fc_id':target[2],'old_fc_name':expected_fc,'english_name':english_name.strip(),
                  'new_pdf_files':2,'financial_rows_unchanged':True,'other_invoices_unchanged':True,
                  'old_archives_and_attempts_preserved':True,'monthly_sequences_unchanged':True,'triggers_restored':True}
        sql.execute("INSERT INTO audit_events(created_at,action,entity_type,entity_id,details_json) VALUES(CURRENT_TIMESTAMP,'INVOICE_NUMBER_PREFIX_APPLIED','INVOICE',?,?)", (invoice_id,json.dumps(report,ensure_ascii=False)))
        sql.commit()
    except BaseException:
        sql.rollback()
        # A commit error can be ambiguous. Delete only files proved unregistered
        # after rollback; if this query fails, keep the files for inspection.
        for path in generated:
            if not sql.execute('SELECT 1 FROM export_records WHERE stored_path=?', (str(path),)).fetchone():
                path.unlink(missing_ok=True)
        raise
    finally:
        sql.close()
    _validate_sqlite_database(database)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restored-copy', type=Path, required=True)
    parser.add_argument('--live-root', type=Path, required=True)
    parser.add_argument('--invoice-id', type=int, required=True)
    parser.add_argument('--expected-number', required=True)
    parser.add_argument('--expected-company', required=True)
    parser.add_argument('--expected-fc', required=True)
    parser.add_argument('--english-name', required=True)
    parser.add_argument('--acknowledge-isolated-copy', action='store_true')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    report = prefix_copy(args.restored_copy, live_root=args.live_root, invoice_id=args.invoice_id,
        expected_number=args.expected_number, expected_company=args.expected_company,
        expected_fc=args.expected_fc, english_name=args.english_name,
        acknowledged_copy=args.acknowledge_isolated_copy)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Converted one invoice in the isolated copy; report saved.')
