"""Non-destructive preference migration; never use installation storage for writes."""
import json
from pathlib import Path

from .runtime_paths import guard_real_state_write


def validate(raw: bytes) -> None:
    value = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(value, dict) or any(
        not isinstance(value.get(key), list)
        or any(not isinstance(item, str) for item in value[key])
        for key in ('hidden_folders', 'pinned_folders')
    ):
        raise ValueError('Invalid project preferences; preserve and recover manually')


def migrate(source: Path, destination: Path) -> None:
    """Copy exclusively, verify bytes, retain source. Conflicts block writes."""
    if source.resolve() == destination.resolve() or not source.exists():
        return
    raw = source.read_bytes()
    validate(raw)
    if destination.exists():
        if destination.read_bytes() != raw:
            raise ValueError('Project preference conflict; both files retained')
        return
    guard_real_state_write(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with destination.open('xb') as stream:
            created = True
            stream.write(raw)
            stream.flush()
            import os
            os.fsync(stream.fileno())
        if destination.read_bytes() != raw:
            raise OSError('Preference migration verification failed')
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise
