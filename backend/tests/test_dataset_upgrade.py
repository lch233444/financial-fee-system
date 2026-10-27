from contextlib import closing
import sqlite3
from uuid import uuid4

from alembic import command
import pytest

from app import config, database
from app.services import backup
from app.services.dataset_upgrade import upgrade_registered_dataset
from test_datasets import workspace, headers
from test_deletion_guard_migration import _alembic_config


def old_registered(manager):
    identity = uuid4().hex
    settings = manager.settings_for(identity)
    settings.ensure_directories()
    with config.using_settings(settings):
        command.upgrade(_alembic_config(settings), 'd016f0ee1160')
    with closing(sqlite3.connect(settings.database_path)) as db:
        db.execute("INSERT INTO clients(id,name,status,created_at,updated_at) VALUES(1,'Keep old customer','DRAFT','2026-10-23','2026-10-23')")
        db.execute("UPDATE app_settings SET value='1' WHERE key='id_high_water.clients'")
        db.commit()
    (settings.data_root/'attachments'/'untouched.txt').write_bytes(b'original evidence')
    manager.entries.append({'id': identity, 'name': 'Registered old package'})
    manager.save(manager.entries, manager.active)
    return identity, settings


def test_switch_registered_12_package_backs_up_rehearses_and_preserves(workspace):
    client, manager, primary = workspace
    identity, target = old_registered(manager)
    original = target.database_path.read_bytes()
    response = client.post('/api/datasets/switch', headers=headers(manager), json={'id': identity})
    assert response.status_code == 200, response.text
    assert response.json()['settlement_cycle'] == 'QUARTERLY'
    assert client.get('/api/clients', headers=headers(manager)).json()[0]['name'] == 'Keep old customer'
    assert (target.data_root/'attachments'/'untouched.txt').read_bytes() == b'original evidence'
    packages = list((target.data_root/'backups').glob('*.zip'))
    assert len(packages) == 1
    with config.using_settings(target):
        manifest = backup.validate_backup(packages[0], target.data_root/'tmp'/'check-old', allow_upgrade=True)
    assert manifest['app_version'] == '1.2.0'
    assert not (target.data_root/'pending_restore.json').exists()
    with closing(sqlite3.connect(target.database_path)) as db:
        backup.validate_database_structure(db)
        assert db.execute("SELECT id,name,status,created_at,updated_at FROM clients").fetchall() == [(1,'Keep old customer','DRAFT','2026-10-23','2026-10-23')]
    # Switching again does not re-upgrade, duplicate the customer or back up again.
    response = client.post('/api/datasets/switch', headers=headers(manager), json={'id':'primary'})
    assert response.status_code == 200
    assert client.get('/api/clients', headers=headers(manager)).json() == []
    response = client.post('/api/datasets/switch', headers=headers(manager), json={'id': identity})
    assert response.status_code == 200
    assert list((target.data_root/'backups').glob('*.zip')) == packages


@pytest.mark.parametrize('failure_stage', ['migration', 'reexport'])
def test_failed_upgrade_rehearsal_keeps_original_and_active_package(workspace, monkeypatch, failure_stage):
    client, manager, primary = workspace
    identity, target = old_registered(manager)
    original = target.database_path.read_bytes()
    def fail(**kwargs):
        raise RuntimeError('synthetic isolated migration failure')
    if failure_stage == 'migration':
        monkeypatch.setattr(database, 'init_db', fail)
    else:
        original_backup = backup.create_backup
        def fail_export(*args, **kwargs):
            if kwargs.get('_source_app_version') is None:
                raise RuntimeError('synthetic isolated reexport failure')
            return original_backup(*args, **kwargs)
        monkeypatch.setattr(backup, 'create_backup', fail_export)
    with pytest.raises(RuntimeError, match='synthetic isolated'):
        upgrade_registered_dataset(target, manager.engine_for)
    assert target.database_path.read_bytes() == original
    assert manager.active == 'primary'
    assert not (target.data_root/'pending_restore.json').exists()
    assert len(list((target.data_root/'backups').glob('*.zip'))) == 1


def test_failed_inactive_package_replace_rolls_back_and_next_selection_resumes(workspace, monkeypatch):
    client, manager, _ = workspace
    identity, target = old_registered(manager)
    original = target.database_path.read_bytes()
    replace = backup._replace_restore_directory
    failed = False
    def fail_once(source, destination):
        nonlocal failed
        if destination == target.data_root/'attachments' and not failed:
            failed = True
            raise OSError('synthetic target replacement failure')
        return replace(source, destination)
    with monkeypatch.context() as patch:
        patch.setattr(backup, '_replace_restore_directory', fail_once)
        response = client.post('/api/datasets/switch', headers=headers(manager), json={'id':identity})
    assert response.status_code == 409
    assert manager.active == 'primary'
    assert target.database_path.read_bytes() == original
    assert (target.data_root/'pending_restore.json').exists()
    response = client.post('/api/datasets/switch', headers=headers(manager), json={'id':identity})
    assert response.status_code == 200, response.text
    assert client.get('/api/clients', headers=headers(manager)).json()[0]['name'] == 'Keep old customer'
    assert not (target.data_root/'pending_restore.json').exists()
