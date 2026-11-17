import json
from pathlib import Path

import pytest

from app.services import project_preferences as prefs, project_service, runtime_paths

RAW = b'{"hidden_folders":["hidden"],"pinned_folders":["pinned"]}'


def test_first_install_and_persistent_location(monkeypatch, tmp_path):
    monkeypatch.setenv('RIDIAN_DATA_DIR', str(tmp_path))
    assert runtime_paths.data_dir() == tmp_path
    destination = tmp_path / 'hidden_projects.json'
    prefs.migrate(tmp_path / 'absent', destination)
    assert not destination.exists()


def test_legacy_copy_is_verified_source_retained_and_idempotent(tmp_path):
    source, destination = tmp_path / 'legacy.json', tmp_path / 'data/prefs.json'
    source.write_bytes(RAW)
    prefs.migrate(source, destination)
    prefs.migrate(source, destination)
    assert source.read_bytes() == destination.read_bytes() == RAW


def test_conflict_preserves_both(tmp_path):
    source, destination = tmp_path / 'old', tmp_path / 'new'
    source.write_bytes(RAW)
    destination.write_bytes(b'keep existing')
    with pytest.raises(ValueError, match='conflict'):
        prefs.migrate(source, destination)
    assert destination.read_bytes() == b'keep existing'
    assert source.read_bytes() == RAW


def test_invalid_source_never_creates_destination(tmp_path):
    source, destination = tmp_path / 'old', tmp_path / 'new'
    source.write_bytes(b'{broken')
    with pytest.raises(ValueError):
        prefs.migrate(source, destination)
    assert not destination.exists()
    assert source.read_bytes() == b'{broken'


def test_failed_verification_rolls_back_only_new_copy(monkeypatch, tmp_path):
    source, destination = tmp_path / 'old', tmp_path / 'new'
    source.write_bytes(RAW)
    read = Path.read_bytes
    monkeypatch.setattr(Path, 'read_bytes', lambda p: b'bad copy' if p == destination else read(p))
    with pytest.raises(OSError, match='verification'):
        prefs.migrate(source, destination)
    assert not destination.exists()
    assert source.read_bytes() == RAW


def test_upgrade_removal_and_later_changes_preserve_preferences(monkeypatch, tmp_path):
    source, destination = tmp_path / 'install/hidden_projects.json', tmp_path / 'persistent/hidden_projects.json'
    source.parent.mkdir()
    source.write_bytes(RAW)
    monkeypatch.setattr(project_service, 'LEGACY_HIDDEN_PATH', source)
    monkeypatch.setattr(project_service, 'HIDDEN_PATH', destination)
    assert project_service._load_prefs() == ({'hidden'}, {'pinned'})
    project_service._save_prefs({'updated'}, {'still-pinned'})
    # Simulate installer replacing the bundle and rollback to retained old file.
    source.unlink()
    assert project_service._load_prefs() == ({'updated'}, {'still-pinned'})
    source.write_bytes(RAW)
    assert project_service._load_prefs() == ({'updated'}, {'still-pinned'})


def test_existing_destination_is_authoritative(monkeypatch, tmp_path):
    source, destination = tmp_path / 'legacy', tmp_path / 'persistent'
    source.write_bytes(b'bad legacy')
    destination.write_bytes(RAW)
    monkeypatch.setattr(project_service, 'LEGACY_HIDDEN_PATH', source)
    monkeypatch.setattr(project_service, 'HIDDEN_PATH', destination)
    assert project_service._load_prefs() == ({'hidden'}, {'pinned'})
    assert source.read_bytes() == b'bad legacy'


def test_opt_in_legacy_migration_includes_preferences(tmp_path):
    source, destination = tmp_path / 'legacy', tmp_path / 'persistent'
    source.mkdir()
    (source / 'hidden_projects.json').write_bytes(RAW)
    assert 'hidden_projects.json' in runtime_paths.migrate_legacy_state(source, destination)
    assert (destination / 'hidden_projects.json').read_bytes() == RAW


@pytest.mark.skipif(__import__('sys').platform != 'win32', reason='Actual installer preflight requires Windows PowerShell')
@pytest.mark.parametrize('case', ['first', 'legacy', 'existing', 'same', 'conflict', 'invalid', 'upgrade'])
def test_installer_preflight(case, tmp_path):
    import subprocess
    script = Path(__file__).resolve().parents[3] / 'desktop/installer/preserve-preferences.ps1'
    install = tmp_path / 'old-install'
    source = install / 'resources/backend/_internal/hidden_projects.json'
    destination = tmp_path / 'persistent/hidden_projects.json'
    source.parent.mkdir(parents=True)
    if case not in ('first', 'existing'):
        source.write_bytes(b'invalid' if case == 'invalid' else RAW)
    if case in ('existing', 'same', 'conflict'):
        destination.parent.mkdir()
        destination.write_bytes(b'keep' if case == 'conflict' else RAW)
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                             '-File', str(script), '-InstallDir', str(install), '-Destination', str(destination)],
                            capture_output=True, timeout=30)
    assert (result.returncode == 0) == (case not in ('conflict', 'invalid'))
    if case == 'first' or case == 'invalid':
        assert not destination.exists()
    elif case == 'conflict':
        assert destination.read_bytes() == b'keep'
    else:
        assert destination.read_bytes() == RAW
    if source.exists():
        assert source.read_bytes() == (b'invalid' if case == 'invalid' else RAW)
    if case == 'upgrade':
        import shutil
        shutil.rmtree(install)
        assert destination.read_bytes() == RAW
