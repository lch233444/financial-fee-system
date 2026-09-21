"""Copy synthetic rows into a real migration-created historical schema."""
import sqlite3


def copy_synthetic_rows(source_path, target_path):
    with sqlite3.connect(source_path) as source, sqlite3.connect(target_path) as target:
        triggers = target.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'").fetchall()
        target.execute("BEGIN IMMEDIATE")
        for name, _ in triggers:
            target.execute(f'DROP TRIGGER "{name}"')
        tables = [row[0] for row in target.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('alembic_version','sqlite_sequence')")]
        for table in tables:
            target.execute(f'DELETE FROM "{table}"')
            target_names = [row[1] for row in target.execute(f'PRAGMA table_info("{table}")')]
            source_names = [row[1] for row in source.execute(f'PRAGMA table_info("{table}")')]
            if not source_names:
                continue
            for row in source.execute(f'SELECT * FROM "{table}"'):
                values = dict(zip(source_names, row))
                # The current synthetic masters never had codes. Older schemas
                # require fixture-only codes; no historical customer data is used.
                if table in {'fcs', 'fee_plans'} and 'code' in target_names:
                    values['code'] = f'FIXTURE{values["id"]}'
                names = [name for name in target_names if name in values]
                projection = ','.join(f'"{name}"' for name in names)
                target.execute(f'INSERT INTO "{table}" ({projection}) VALUES ({",".join("?" for _ in names)})', [values[name] for name in names])
        for _, sql in triggers:
            target.execute(sql)


def project_removed_meeting_fields(before, after):
    for table in ('fcs', 'fee_plans'):
        before[table] = [row[:3] + row[4:] for row in before[table]]
    after['attachments'] = [row[:-1] for row in after['attachments']]
