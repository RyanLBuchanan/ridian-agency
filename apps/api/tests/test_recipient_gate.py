"""draft_gmail provenance gate — Ridian never drafts to an address it invented.

Uses the testable _draft_gmail core with gmail_service.create_draft mocked, so
we can assert a real draft is NOT created for an unverified recipient.
"""
import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from app.services import gmail_service
from app.services import operator_tools as t
from app.services.operator_context import OperatorContext


def _ctx(tmp_path, record):
    async def _emit(_ev):
        return None
    for key in ("steps", "tools_used", "artifacts", "errors"):
        record.setdefault(key, [])
    return OperatorContext(folder=Path(tmp_path), record=record, emit=_emit)


# --------------------------------------------------------------------------
# pure helpers
# --------------------------------------------------------------------------

def test_extract_emails():
    got = t.extract_emails("ping sarah@x.com and BOB@Y.ORG, also sarah@x.com again")
    assert got == ["sarah@x.com", "bob@y.org"]


def test_address_only_handles_display_name():
    assert t._address_only("Sarah <sarah@x.com>") == "sarah@x.com"
    assert t._address_only("sarah@x.com") == "sarah@x.com"
    assert t._address_only("Sarah at the Chamber") == ""


def test_recipient_is_known_contact(monkeypatch, tmp_path):
    monkeypatch.setattr(t.memory_service, "list_contacts",
                        lambda: [{"name": "Sarah", "email": "sarah@realchamber.org"}])
    op = _ctx(tmp_path, {})
    assert t._recipient_is_known(op, "sarah@realchamber.org") is True
    assert t._recipient_is_known(op, "SARAH@RealChamber.org") is True   # case-insensitive
    assert t._recipient_is_known(op, "sarah@chamber.com") is False       # fabricated


def test_recipient_is_known_user_typed(monkeypatch, tmp_path):
    monkeypatch.setattr(t.memory_service, "list_contacts", lambda: [])
    op = _ctx(tmp_path, {"user_provided_emails": ["typed@me.com"]})
    assert t._recipient_is_known(op, "typed@me.com") is True
    assert t._recipient_is_known(op, "other@me.com") is False


# --------------------------------------------------------------------------
# the gate inside _draft_gmail — create_draft must NOT be called when unverified
# --------------------------------------------------------------------------

def test_draft_refuses_fabricated_and_does_not_create(monkeypatch, tmp_path):
    monkeypatch.setattr(t.memory_service, "list_contacts", lambda: [])   # no contacts
    created = MagicMock()
    monkeypatch.setattr(gmail_service, "create_draft", created)
    op = _ctx(tmp_path, {})   # nothing typed by the operator

    res = asyncio.run(t._draft_gmail(op, "sarah@chamber.com", "Hi", "Body"))

    assert res.get("reason") == "recipient_unverified"
    created.assert_not_called()                            # NO real draft created
    assert len(op.record.get("needs_input", [])) == 1      # asked BEFORE drafting
    assert op.record.get("awaiting_input") is True


def test_draft_allows_known_contact(monkeypatch, tmp_path):
    monkeypatch.setattr(t.memory_service, "list_contacts",
                        lambda: [{"name": "Sarah", "email": "sarah@realchamber.org"}])
    created = MagicMock(return_value={
        "draft_id": "d123456789", "compose_url": "https://mail.google.com/x",
        "to": "sarah@realchamber.org",
    })
    monkeypatch.setattr(gmail_service, "create_draft", created)
    op = _ctx(tmp_path, {})

    res = asyncio.run(t._draft_gmail(op, "sarah@realchamber.org", "Hi", "Body"))

    created.assert_called_once()
    assert res.get("draft_id") == "d123456789"


def test_resume_typed_address_then_drafts(monkeypatch, tmp_path):
    """Resume path: the operator supplies the address in the answer, it's
    captured into user_provided_emails, and the retried draft succeeds."""
    monkeypatch.setattr(t.memory_service, "list_contacts", lambda: [])
    created = MagicMock(return_value={
        "draft_id": "d987654321", "compose_url": "https://mail.google.com/y",
        "to": "sarah@coastalchamber.org",
    })
    monkeypatch.setattr(gmail_service, "create_draft", created)
    op = _ctx(tmp_path, {})

    # First attempt with a guessed address is refused; no draft.
    first = asyncio.run(t._draft_gmail(op, "sarah@chamber.com", "Hi", "Body"))
    assert first.get("reason") == "recipient_unverified"
    created.assert_not_called()

    # continue_operation captures the typed address from the operator's answer:
    for e in t.extract_emails("Her email is sarah@coastalchamber.org"):
        op.record.setdefault("user_provided_emails", []).append(e)

    # The retried draft to the now-verified address succeeds.
    second = asyncio.run(t._draft_gmail(op, "sarah@coastalchamber.org", "Hi", "Body"))
    created.assert_called_once()
    assert second.get("draft_id") == "d987654321"
