"""Frozen-vs-dev path contract (v4.2) + provenance-preserving migration.

The approved contract: branch on sys.frozen ONLY. Dev resolves every path
exactly as before this feature existed; frozen resolves writable state to
%APPDATA%/Ridian Operator. Migration is byte-copy — written_by/source_op
provenance survives BYTE-IDENTICAL, no re-stamp, no inference.
"""
import json
import sys
from pathlib import Path

from app.services import runtime_paths


def test_dev_paths_are_exactly_the_historical_ones():
    """Not frozen (the test process) -> apps/api, byte-for-byte the old base."""
    assert not runtime_paths.is_frozen()
    api_dir = Path(runtime_paths.__file__).resolve().parent.parent.parent
    assert runtime_paths.data_dir() == api_dir
    assert runtime_paths.resource_base() == api_dir


def test_frozen_data_dir_is_appdata(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    d = runtime_paths.data_dir()
    assert d == tmp_path / "Ridian Operator"
    assert d.is_dir()                     # created on first use


def test_frozen_resource_base_is_bundle_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert runtime_paths.resource_base() == tmp_path


# --------------------------------------------------------------------------
# Migration — provenance survives byte-identical
# --------------------------------------------------------------------------

_MEMORY_FIXTURE = [
    {"id": "c_1", "name": "Sarah Chen", "written_by": "save_memory",
     "source_op": "op_abc123def456"},
    {"id": "c_2", "name": "Marcus Delacroix", "written_by": "commit",
     "source_op": "op_999888777666"},
    {"id": "c_3", "name": "Legacy Row", "written_by": "unknown", "source_op": ""},
]


def _legacy_tree(root: Path) -> Path:
    src = root / "legacy_api"
    (src / "state").mkdir(parents=True)
    (src / "state" / "contacts.json").write_text(
        json.dumps(_MEMORY_FIXTURE, indent=2), encoding="utf-8")
    (src / "local_settings.json").write_text('{"operator_name": "Ryan"}',
                                             encoding="utf-8")
    (src / "quickbooks_token.json").write_text('{"refresh_token": "SECRET"}',
                                               encoding="utf-8")
    return src


def test_migration_preserves_provenance_byte_identical(tmp_path):
    src = _legacy_tree(tmp_path)
    dst = tmp_path / "appdata" / "Ridian Operator"
    copied = runtime_paths.migrate_legacy_state(src, dst)
    assert "state" in copied and "quickbooks_token.json" in copied
    # BYTE-level equality — the strongest possible "no re-stamp" assertion.
    assert ((dst / "state" / "contacts.json").read_bytes()
            == (src / "state" / "contacts.json").read_bytes())
    migrated = json.loads((dst / "state" / "contacts.json").read_text(encoding="utf-8"))
    assert [(r["written_by"], r["source_op"]) for r in migrated] == \
           [(r["written_by"], r["source_op"]) for r in _MEMORY_FIXTURE]


def test_migration_never_overwrites_existing_destination(tmp_path):
    src = _legacy_tree(tmp_path)
    dst = tmp_path / "appdata" / "Ridian Operator"
    dst.mkdir(parents=True)
    (dst / "local_settings.json").write_text('{"operator_name": "KEEP ME"}',
                                             encoding="utf-8")
    runtime_paths.migrate_legacy_state(src, dst)
    assert "KEEP ME" in (dst / "local_settings.json").read_text(encoding="utf-8")


def test_migration_noop_when_env_unset(monkeypatch):
    """The clean-machine case: frozen, no RIDIAN_MIGRATE_FROM -> nothing."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv("RIDIAN_MIGRATE_FROM", raising=False)
    assert runtime_paths.maybe_migrate_on_first_run() == []
