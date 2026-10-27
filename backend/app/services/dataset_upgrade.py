"""Back up and rehearse an existing 1.2 package before its additive upgrade."""
from contextlib import closing
import sqlite3
from uuid import uuid4

from .. import database
from ..config import using_settings
from . import backup
from .zero_fee_invoice_contract import ZERO_FEE_REVISION


def upgrade_registered_dataset(settings, engine_factory) -> None:
    if not settings.database_path.is_file():
        return  # A genuinely new, unregistered root is initialized normally.
    with closing(sqlite3.connect(settings.database_path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='alembic_version'").fetchone():
            return  # The established legacy bootstrap performs its own checks.
        revision = db.execute('SELECT version_num FROM alembic_version').fetchone()[0]
    if revision != ZERO_FEE_REVISION:
        return
    with using_settings(settings):
        original = backup.create_backup(_source_app_version='1.2.0')
        isolated_root = settings.data_root / 'tmp' / ('cycle_upgrade_' + uuid4().hex[:8])
        manifest = backup.validate_backup(original, isolated_root, allow_upgrade=True)
        backup._rebase_data_package_database(isolated_root, isolated_root, manifest, allow_upgrade=True)
        isolated_settings = settings.model_copy(update={'data_root': isolated_root})
        isolated_settings.ensure_directories()
        isolated_engine = engine_factory(isolated_settings)
        try:
            with using_settings(isolated_settings):
                database.init_db(target_settings=isolated_settings, target_engine=isolated_engine)
                upgraded = backup.create_backup()
        finally:
            isolated_engine.dispose()
        # No live component changes before both original and migrated packages
        # pass strict validation. Native restore supplies rollback and recovery.
        backup.stage_restore(upgraded)
        backup.apply_pending_restore()
