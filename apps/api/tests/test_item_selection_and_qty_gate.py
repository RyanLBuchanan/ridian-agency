"""v6.7 — the quantity cross-purpose gate (Part 1) + id-carrying catalog
selection (Part 2) + the interaction pins (Part 3).

Part 1 pins (the diagnosed hole): with a quantity question pending, typing
"Create a QuickBooks invoice for the Coastal Chamber: one line, AI
discovery session, $500." put 500 into the flat stated pool, where the
quantity gate would accept qty=500. Now dollars are never quantities:
quantities verify against the PLAIN (non-currency) pool only, absorbed by
the same code path intake and resume use.

Part 2 pins: catalog buttons carry the QuickBooks item id in a machine
token; _apply_item_selection_answer (sole writer) validates ids against
the OFFERED snapshot and refuses never-offered ids; the invoice gate
matches selections by ID EQUALITY — proven by two items with IDENTICAL
display names, which text matching cannot tell apart.

Part 3 pins: composer never carries the question as value or placeholder;
compose buttons may prefill (the one exception); pending strip + dismiss
exist; reopening a waiting run re-arms the pending question.
"""
import asyncio
import json
from pathlib import Path

import pytest

from app.services import operator_service, state_store
from app.services import operator_tools as t
from app.services.operator_context import OperatorContext, set_current_operator

_RENDERER = Path(__file__).resolve().parents[3] / "desktop" / "renderer"

# TWO ITEMS WITH THE SAME NAME, different ids and prices — the fixture that
# makes text matching provably insufficient.
_ITEMS = [
    {"id": "7", "name": "AI Discovery Session", "unit_price": 500.0, "type": "Service"},
    {"id": "8", "name": "AI Discovery Session", "unit_price": 750.0, "type": "Service"},
]
_CUSTOMERS = [{"id": "42", "name": "Coastal Chamber", "email": "info@coastal.test"}]


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(state_store, "STATE_DIR", tmp_path / "state")


@pytest.fixture()
def qb(monkeypatch):
    created = []
    monkeypatch.setattr(t.quickbooks_service, "list_customers", lambda: list(_CUSTOMERS))
    monkeypatch.setattr(t.quickbooks_service, "list_items", lambda: [dict(i) for i in _ITEMS])

    def fake_create(customer_id, lines, txn_date="", due_date=""):
        created.append({"customer_id": customer_id, "lines": lines})
        return {"id": "99", "doc_number": "1042", "customer": "Coastal Chamber",
                "total": 0.0, "email_status": "NotSet", "link": ""}

    monkeypatch.setattr(t.quickbooks_service, "create_invoice", fake_create)
    return created


def _op(tmp_path, command=""):
    async def _emit(_ev):
        return None
    record = {"id": "op_sel", "command": command, "steps": [], "tools_used": [],
              "artifacts": [], "errors": []}
    t.absorb_stated_numbers(record, command)
    op = OperatorContext(folder=tmp_path, record=record, emit=_emit)
    set_current_operator(op)
    return op


def _call(_tool_name, **kwargs):
    tool = next(x for x in t.PLANNER_TOOLS if x.name == _tool_name)
    raw = asyncio.run(tool.call(kwargs))
    return json.loads(raw) if isinstance(raw, str) else raw


def _answer(op, text):
    """The REAL resume plumbing for a typed answer: absorb numbers, run the
    selection applier — exactly what continue_operation does."""
    t.absorb_stated_numbers(op.record, text)
    return operator_service._apply_item_selection_answer(op, text)


# ==========================================================================
# PART 1 — dollars are never quantities
# ==========================================================================

def test_absorption_separates_plain_from_currency():
    rec: dict = {}
    t.absorb_stated_numbers(
        rec, "Create a QuickBooks invoice for the Coastal Chamber: one line, "
             "AI discovery session, $500.")
    assert rec["user_stated_numbers"] == [500.0]       # full pool: amounts ok
    assert rec["user_stated_plain_numbers"] == []      # NOTHING can be a qty
    t.absorb_stated_numbers(rec, "3 sessions at $250")
    assert rec["user_stated_plain_numbers"] == [3.0]   # bare 3 → qty-eligible
    assert 250.0 in rec["user_stated_numbers"]


def test_the_diagnosed_attack_dollar_figure_cannot_become_quantity(qb, tmp_path):
    """THE Part-1 pin. Pending qty question; the operator types a new
    request containing $500; the planner (mis)routes 500 into the qty slot.
    Before v6.7 the flat pool accepted it. Now it PARKS."""
    op = _op(tmp_path, "Invoice the Coastal Chamber for an AI Discovery Session")
    _answer(op, "Create a QuickBooks invoice for the Coastal Chamber: one "
                "line, AI discovery session, $500.")
    assert 500.0 in op.record["user_stated_numbers"]   # the old gate's input
    out = _call("create_quickbooks_invoice", customer="Coastal Chamber",
                lines=[{"item_id": "8", "qty": 500}])
    # (id 8 unselected parks first — select it so ONLY the qty gate decides)
    op.record["selected_catalog_items"] = {"8": {"name": "AI Discovery Session",
                                                 "unit_price": 750.0}}
    out = _call("create_quickbooks_invoice", customer="Coastal Chamber",
                lines=[{"item_id": "8", "qty": 500}])
    assert out.get("reason") == "line_value_unverified"
    assert "dollar amounts" in op.record["needs_input"][-1]["question"]
    assert qb == []                                    # nothing created
    # The SAME $500 legitimately sanctions an AMOUNT on a free-form line.
    ok = _call("create_quickbooks_invoice", customer="Coastal Chamber",
               lines=[{"description": "AI discovery session", "amount": 500}])
    assert ok.get("reason") == "invoice_plan_pending"  # provenance passed


def test_bare_count_still_sanctions_quantity(qb, tmp_path):
    op = _op(tmp_path, "Invoice Coastal Chamber for 3 discovery sessions")
    op.record["selected_catalog_items"] = {"7": dict(name="AI Discovery Session",
                                                     unit_price=500.0)}
    out = _call("create_quickbooks_invoice", customer="Coastal Chamber",
                lines=[{"item_id": "7", "qty": 3}])
    assert out.get("reason") == "invoice_plan_pending"  # 3 was typed bare


# ==========================================================================
# PART 2 — id-carrying selection
# ==========================================================================

def test_ambiguous_item_ask_offers_buttons_with_id_tokens(qb, tmp_path):
    op = _op(tmp_path, "Invoice Coastal Chamber for an AI Discovery Session")
    out = _call("create_quickbooks_invoice", customer="Coastal Chamber",
                lines=[{"item_name": "AI Discovery Session", "qty": 2}])
    assert out.get("reason") == "item_unresolved"      # two same-named items
    need = op.record["needs_input"][-1]
    prefills = [o["prefill"] for o in need["options"]]
    assert "[[qbo-item:7]] AI Discovery Session × " in prefills
    assert "[[qbo-item:8]] AI Discovery Session × " in prefills
    assert all(o["action"] == "compose" for o in need["options"])   # never submits
    labels = [o["label"] for o in need["options"]]
    assert "AI Discovery Session — $500.0" in labels   # name AND price shown
    assert op.record["offered_catalog_items"]["8"]["unit_price"] == 750.0
    assert need["buttons_only"] is False               # free text stays open


def test_click_token_carries_the_id_and_never_offered_refuses(qb, tmp_path):
    op = _op(tmp_path, "Invoice Coastal Chamber")
    _call("create_quickbooks_invoice", customer="Coastal Chamber",
          lines=[{"item_name": "AI Discovery Session", "qty": 2}])
    note = _answer(op, "[[qbo-item:8]] AI Discovery Session × 3")
    assert "SELECTED catalog item id 8" in note
    assert op.record["selected_catalog_items"] == {
        "8": {"name": "AI Discovery Session", "unit_price": 750.0}}
    # Never-offered id: REFUSED — not recorded, said plainly.
    note2 = _answer(op, "[[qbo-item:999]] Mystery Item × 1")
    assert "REFUSED" in note2 and "999" in note2
    assert "999" not in op.record["selected_catalog_items"]


def test_same_named_items_resolved_by_id_equality_end_to_end(qb, tmp_path):
    """THE test that matters: two items share a display name, so any
    name-matching fallback would PARK as ambiguous (or guess). Only the
    clicked ID can put id 8 — at id 8's price — on the invoice."""
    op = _op(tmp_path, "Invoice Coastal Chamber for an AI Discovery Session")
    first = _call("create_quickbooks_invoice", customer="Coastal Chamber",
                  lines=[{"item_name": "AI Discovery Session", "qty": 2}])
    assert first.get("reason") == "item_unresolved"
    _answer(op, "[[qbo-item:8]] AI Discovery Session × 3")

    staged = _call("create_quickbooks_invoice", customer="Coastal Chamber",
                   lines=[{"item_id": "8", "qty": 3}])
    assert staged.get("reason") == "invoice_plan_pending"
    operator_service._apply_invoice_answer(op, t.INVOICE_PROCEED)
    done = _call("create_quickbooks_invoice", customer="Coastal Chamber",
                 lines=[{"item_id": "8", "qty": 3}])
    assert done["doc_number"] == "1042"
    line = qb[0]["lines"][0]
    assert line["item_id"] == "8"                      # the CLICKED id
    assert line["unit_price"] == 750.0                 # id 8's price, not id 7's
    assert line["qty"] == 3


def test_unselected_id_parks_even_if_it_was_offered(qb, tmp_path):
    op = _op(tmp_path, "Invoice Coastal Chamber")
    _call("create_quickbooks_invoice", customer="Coastal Chamber",
          lines=[{"item_name": "AI Discovery Session", "qty": 2}])
    _answer(op, "[[qbo-item:8]] AI Discovery Session × 3")
    out = _call("create_quickbooks_invoice", customer="Coastal Chamber",
                lines=[{"item_id": "7", "qty": 3}])    # offered, NOT selected
    assert out.get("reason") == "item_unverified"
    assert qb == []


def test_quantity_still_gated_after_a_click(qb, tmp_path):
    """Clicking selects the service; it never supplies a count. Missing qty
    parks; a currency-typed number parks; only a bare typed count passes."""
    op = _op(tmp_path, "Invoice Coastal Chamber")
    _call("create_quickbooks_invoice", customer="Coastal Chamber",
          lines=[{"item_name": "AI Discovery Session", "qty": 2}])
    _answer(op, "[[qbo-item:8]] AI Discovery Session × ")   # click, no qty yet
    out = _call("create_quickbooks_invoice", customer="Coastal Chamber",
                lines=[{"item_id": "8"}])
    assert out.get("reason") == "line_value_missing"
    assert "quantity needed" in op.record["needs_input"][-1]["task_summary"]
    out = _call("create_quickbooks_invoice", customer="Coastal Chamber",
                lines=[{"item_id": "8", "qty": 4}])
    assert out.get("reason") == "line_value_unverified"     # 4 never typed
    assert qb == []


# ==========================================================================
# PART 3 — interaction pins
# ==========================================================================

def test_composer_is_never_prefilled_with_the_question():
    app_js = (_RENDERER / "app.js").read_text(encoding="utf-8")
    assert "Answer: ${q}" not in app_js                # the question-as-placeholder
    assert "'Type your answer…'" in app_js
    # No code path assigns the question text into the composer VALUE; the
    # ONE sanctioned prefill is the compose option's machine token.
    assert "command.value = need.question" not in app_js
    assert "opt.prefill" in app_js


def test_pending_strip_dismiss_and_reopen_are_wired():
    html = (_RENDERER / "index.html").read_text(encoding="utf-8")
    for el in ("operator-pending-strip", "operator-pending-cancel",
               "operator-pending-send-answer", "operator-pending-new-task"):
        assert el in html, el
    app_js = (_RENDERER / "app.js").read_text(encoding="utf-8")
    assert "/dismiss" in app_js                        # cancel really cancels
    assert "_opLooksLikeNewTask" in app_js             # answer vs new task
    # Reopening a waiting run re-arms the LAST question interactively.
    rehydrate = app_js.split("const stillWaiting", 1)[1][:600]
    assert "_opSetAnswerMode" in rehydrate


def test_dismiss_operation_drops_session_and_marks_cancelled(tmp_path, monkeypatch):
    state_store.save("operations", [
        {"id": "op_x", "status": "awaiting_input", "awaiting_input": True,
         "command": "Invoice someone", "steps": []}])
    operator_service._SESSIONS["op_x"] = object()
    out = operator_service.dismiss_operation("op_x")
    assert out["cancelled"] is True
    assert "op_x" not in operator_service._SESSIONS
    op = state_store.load_list("operations")[0]
    assert op["status"] == "cancelled" and op["awaiting_input"] is False
    assert op["steps"][-1]["name"] == "cancelled"