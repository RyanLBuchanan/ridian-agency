"""read_url must be wired into the planner registry and the open/upload
allowlists so its source.md artifact is usable end to end."""
from app.services import operator_tools as t
from app.services.export_service import ALLOWED_OPEN_FILENAMES
from app.services.google_drive_service import UPLOAD_ALLOWED_FILENAMES


def test_read_url_registered():
    names = [x.name for x in t.PLANNER_TOOLS]
    assert "read_url" in names


def test_read_url_in_capability_summary():
    assert "read_url" in t.tool_capability_summary()


def test_source_md_is_openable_and_uploadable():
    assert "source.md" in ALLOWED_OPEN_FILENAMES
    assert "source.md" in UPLOAD_ALLOWED_FILENAMES
