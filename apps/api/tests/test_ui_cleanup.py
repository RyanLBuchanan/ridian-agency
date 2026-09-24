"""0.9.18 UI cleanup — the backend halves (the renderer halves are the
"UI cleanup (real DOM)" section of check_settings_layout.js).

  2. A parked question renders once. On 2026-09-24 one invoice quantity
     (op_d5204a25df77) rendered as three "Ridian needs an answer" cards: the
     invoice gate asked twice in one turn (qty 1 "isn't a count you typed",
     then no qty "How many of…?") and the planner paraphrased it through
     request_missing_info. Now: one entry per pending item, the gate's
     latest wording, and the planner's paraphrase folds into it.
  3. Obligations can be edited (name, task text, cadence); a changed cadence
     re-bases the schedule the way creation does.
  5. "Needs a reply" never lists bulk mail: List-Unsubscribe, bulk/list/junk
     precedence, no-reply and marketing senders are counted under "Also in
     the inbox" instead.
  Also: every run gets its own output folder.
"""
import asyncio
import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import artifact_service, brief_service, inbox_service, obligations_service, state_store
from app.services import operator_tools as t
from app.services.operator_context import OperatorContext, set_current_operator

PC = ("127.0.0.1", 50000)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(state_store, "STATE_DIR", tmp_path / "state")


# ---------------------------------------------------------------------------
# 2. One card per pending item
# ---------------------------------------------------------------------------

_ITEMS = [{"id": "11", "name": "WRN Monthly Support Retainer", "unit_price": 1000.0, "type": "Service"}]
_CUSTOMERS = [{"id": "5", "name": "Greg Alexander", "email": "greg@wrn.test"},
              {"id": "6", "name": "Sandy Alvarez", "email": "sandy@gulf.test"}]


@pytest.fixture()
def qb(monkeypatch):
    monkeypatch.setattr(t.quickbooks_service, "list_customers", lambda: list(_CUSTOMERS))
    monkeypatch.setattr(t.quickbooks_service, "list_items", lambda: [dict(i) for i in _ITEMS])


def _op(tmp_path, command: str):
    events: list = []

    async def emit(ev):
        events.append(ev)
    record = {"id": "op_once0001", "command": command, "steps": [], "tools_used": [],
              "artifacts": [], "errors": []}
    t.absorb_stated_numbers(record, command)
    op = OperatorContext(folder=tmp_path, record=record, emit=emit)
    set_current_operator(op)
    return op, events


def _call(name, **kwargs):
    tool = next(x for x in t.PLANNER_TOOLS if x.name == name)
    raw = asyncio.run(tool.call(kwargs))
    return json.loads(raw) if isinstance(raw, str) else raw


RETAINER = "WRN Monthly Support Retainer"
PARAPHRASE = ("To invoice Greg Alexander for the WRN Monthly Support Retainer at $1,000 total, "
              "I just need you to confirm the quantity.")


def test_the_invoice_question_from_2026_09_24_renders_once(tmp_path, qb):
    op, events = _op(tmp_path, "Invoice Greg Alexander $1,000 for the WRN Monthly Support Retainer")
    first = _call("create_quickbooks_invoice", customer="Greg Alexander",
                  lines=[{"item_name": RETAINER, "qty": 1}])
    second = _call("create_quickbooks_invoice", customer="Greg Alexander", lines=[{"item_name": RETAINER}])
    third = _call("request_missing_info", question=PARAPHRASE, context_hint="QuickBooks invoice — Greg Alexander")
    assert first["reason"] == "line_value_unverified" and second["reason"] == "line_value_missing"
    needs = op.record["needs_input"]
    assert len(needs) == 1, [n["question"] for n in needs]
    need = needs[0]
    assert need["question"] == f"How many of '{RETAINER}' should I invoice?", "the gate's latest wording"
    assert need["item"] == "invoice:greg alexander"
    assert third["status"] == "already_asked" and third["id"] == need["id"] and third["question"] == need["question"]
    shown = [e["data"] for e in events if e["event"] == "needs_input"]
    assert [d["id"] for d in shown] == [need["id"], need["id"]], "one card, updated in place — never a second"
    assert shown[0]["question"].startswith(f"Confirm the quantity for '{RETAINER}'")
    assert shown[1]["question"] == need["question"]
    assert not any(e["event"] == "step" and e["data"]["name"] == "needs_input" for e in events), \
        "the folded paraphrase adds nothing to the timeline"
    assert op.record["awaiting_input"] is True


def test_each_pending_item_has_its_own_card_and_a_new_turn_asks_anew(tmp_path, qb):
    op, events = _op(tmp_path, "Invoice Greg Alexander and Sandy Alvarez for the retainer")
    _call("create_quickbooks_invoice", customer="Greg Alexander", lines=[{"item_name": RETAINER}])
    _call("create_quickbooks_invoice", customer="Sandy Alvarez", lines=[{"item_name": RETAINER}])
    assert [n["item"] for n in op.record["needs_input"]] == ["invoice:greg alexander", "invoice:sandy alvarez"]
    # The owner answers: the next turn's questions are new ones.
    op.record["needs_turn_start"] = len(op.record["needs_input"])
    _call("create_quickbooks_invoice", customer="Greg Alexander", lines=[{"item_name": RETAINER}])
    assert len(op.record["needs_input"]) == 3


def test_the_planners_own_question_is_one_card_when_no_gate_is_asking(tmp_path, qb):
    op, events = _op(tmp_path, "Draft a follow-up to Greg")
    a = _call("request_missing_info", question="Which Greg — Greg Alexander or Greg Lane?")
    b = _call("request_missing_info", question="Which Greg do you mean: Greg Alexander or Greg Lane?")
    assert a["status"] == b["status"] == "awaiting_user" and a["id"] == b["id"]
    needs = op.record["needs_input"]
    assert len(needs) == 1 and needs[0]["question"] == "Which Greg do you mean: Greg Alexander or Greg Lane?"
    # A gate asking after the planner is a different item: its own card.
    _call("create_quickbooks_invoice", customer="Greg Alexander", lines=[{"item_name": RETAINER}])
    assert len(op.record["needs_input"]) == 2


def test_after_an_answer_the_same_item_asked_again_is_a_new_card(monkeypatch):
    """The real run and resume: a question answered stays in the thread as
    answered; the gate asking about the same item on the next turn is a new
    card, never an in-place edit of the answered one."""
    from anthropic.types.beta import BetaTextBlock
    from app.services import operator_service
    from app.services.operator_context import current_operator
    from test_parked_runs import _collector, planner

    def gate(question):
        async def turn():
            await current_operator().emit_needs_input(question=question, item="invoice:greg alexander")
            return ([BetaTextBlock(type="text", text="Waiting on you.")], None)
        return turn

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-only-not-a-real-key")
    monkeypatch.setattr(operator_service, "apply_to_environment", lambda: None)
    planner(monkeypatch, [gate("How many should I invoice?"), gate("Confirm the rate: $1,000 each?")])
    events, emit = _collector()

    async def scenario():
        first = await operator_service.run_operation(command="Invoice Greg Alexander for the retainer", emit=emit)
        return await operator_service.continue_operation(operation_id=first["id"], answer="2", emit=emit)

    parked = asyncio.run(scenario())
    assert [n["question"] for n in parked["needs_input"]] == [
        "How many should I invoice?", "Confirm the rate: $1,000 each?"]
    assert parked["needs_input"][0]["id"] != parked["needs_input"][1]["id"]


def test_every_gate_names_its_pending_item():
    """Every needs-input question a tool raises carries an item key."""
    import inspect
    source = inspect.getsource(t)
    calls = source.split("emit_needs_input(")[1:]
    assert len(calls) >= 14
    for call in calls:
        head = call.split("\n    )", 1)[0].split("\n        )", 1)[0][:900]
        assert "item=" in head, head[:200]


# ---------------------------------------------------------------------------
# 3. Obligations: Edit (name, task text, cadence) beside Delete
# ---------------------------------------------------------------------------

def test_editing_an_obligation_keeps_its_state_unless_the_cadence_changes():
    sept1 = dt.date(2026, 9, 1)
    ob = obligations_service.add_obligation(
        {"name": "WRN retainer", "task": "Invoice Greg $1,000", "cadence": {"kind": "monthly_day", "day": 1}},
        written_by="manual", today=sept1)
    oct5 = dt.date(2026, 10, 5)
    assert obligations_service.due_status(ob, today=oct5)["status"] == "overdue"
    # Name and task only: the obligation stays overdue — nothing is forgiven.
    ob2 = obligations_service.update_obligation(
        ob["id"], {"name": "WRN Monthly Support Retainer", "task": "Invoice Greg Alexander $1,000, Net 15"}, today=oct5)
    assert (ob2["name"], ob2["task"]) == ("WRN Monthly Support Retainer", "Invoice Greg Alexander $1,000, Net 15")
    assert ob2["cadence"] == {"kind": "monthly_day", "day": 1}
    assert obligations_service.due_status(ob2, today=oct5)["status"] == "overdue"
    # A new cadence re-bases the schedule, as creation does: caught up today.
    ob3 = obligations_service.update_obligation(ob["id"], {"cadence": {"kind": "weekly", "weekday": 0}}, today=oct5)
    assert ob3["cadence"] == {"kind": "weekly", "weekday": 0}
    assert obligations_service.due_status(ob3, today=oct5) is None
    assert obligations_service.due_status(ob3, today=dt.date(2026, 10, 12))["status"] == "due_today"
    # A one-time date in the past stays owed.
    ob4 = obligations_service.update_obligation(ob["id"], {"cadence": {"kind": "once", "date": "2026-10-02"}}, today=oct5)
    assert obligations_service.due_status(ob4, today=oct5)["status"] == "overdue"
    with pytest.raises(obligations_service.ObligationError):
        obligations_service.update_obligation(ob["id"], {"cadence": {"kind": "fortnightly"}}, today=oct5)
    assert obligations_service.list_obligations()[0]["cadence"] == {"kind": "once", "date": "2026-10-02"}


def test_the_edit_route_saves_from_the_pc():
    ob = obligations_service.add_obligation(
        {"name": "Sales tax", "task": "File the sales tax", "cadence": {"kind": "monthly_first_business_day"}},
        written_by="manual")
    pc = TestClient(app, client=PC)
    r = pc.post(f"/obligations/{ob['id']}/update",
                json={"name": "Sales tax (TX)", "task": "File the Texas sales tax", "cadence": {"kind": "monthly_day", "day": 20}})
    assert r.status_code == 200, r.text
    assert (r.json()["name"], r.json()["task"], r.json()["cadence"]) == (
        "Sales tax (TX)", "File the Texas sales tax", {"kind": "monthly_day", "day": 20})
    assert pc.post(f"/obligations/{ob['id']}/update", json={"cadence": {"kind": "monthly_day", "day": 40}}).status_code == 400
    assert pc.post("/obligations/obl_nosuch00000/update", json={"name": "x"}).status_code == 404


# ---------------------------------------------------------------------------
# 5. "Needs a reply" never lists bulk mail
# ---------------------------------------------------------------------------

ME = "ryan@ridian.test"


def _thread(tid, frm, subject, headers=(), when=dt.datetime(2026, 9, 24, 8, 0), earlier=None):
    def msg(sender, at, extra=()):
        hs = [{"name": "From", "value": sender}, {"name": "To", "value": ME},
              {"name": "Subject", "value": subject}, *({"name": k, "value": v} for k, v in extra)]
        return {"internalDate": str(int(at.timestamp() * 1000)), "snippet": "…", "labelIds": ["INBOX"],
                "payload": {"headers": hs}}
    messages = [msg(*earlier)] if earlier else []
    messages.append(msg(frm, when, headers))
    return inbox_service.normalize_thread({"id": tid, "messages": messages}, ME)


def _classified():
    threads = [
        _thread("person", "Sandy Alvarez <sandy@gulf.test>", "Discovery scope"),
        _thread("role", "Random Vendor <sales@vendor.test>", "Partnership?"),
        _thread("unsub", "Acme Weekly <hello@acme.test>", "This week at Acme",
                headers=[("List-Unsubscribe", "<mailto:unsub@acme.test>")]),
        _thread("bulk", "Updates <updates@tool.test>", "Your digest", headers=[("Precedence", "bulk")]),
        _thread("noreply", "GitHub <noreply@github.com>", "[repo] New issue"),
        _thread("donot", "Bank <do-not-reply@bank.test>", "Statement ready"),
        _thread("mkt", "Brand <marketing@brand.test>", "Fall sale"),
        _thread("mkthost", "Brand <hello@news.brand.test>", "New arrivals"),
        # A newsletter the person answered from: the LATEST message decides.
        _thread("replied", "Sandy Alvarez <sandy@gulf.test>", "Re: This week at Acme",
                earlier=("Acme Weekly <hello@acme.test>", dt.datetime(2026, 9, 23, 8, 0),
                         [("List-Unsubscribe", "<mailto:unsub@acme.test>")])),
    ]
    return inbox_service.classify(threads, now=dt.datetime(2026, 9, 24, 9, 0), contacts={})


def test_needs_a_reply_excludes_no_reply_and_marketing_senders():
    out = _classified()
    assert sorted(r["id"] for r in out["needs_reply"]) == ["person", "replied", "role"]
    also = {r["id"]: r["bulk"] for r in out["also_in_inbox"]}
    assert also == {"unsub": "list-unsubscribe", "bulk": "bulk precedence", "noreply": "no-reply sender",
                    "donot": "no-reply sender", "mkt": "marketing sender", "mkthost": "marketing sender"}
    assert out["checked"] == 9
    assert "List-Unsubscribe" in inbox_service._HEADERS and "Precedence" in inbox_service._HEADERS


def test_the_brief_counts_them_under_also_in_the_inbox(monkeypatch):
    out = _classified()
    monkeypatch.setattr(inbox_service, "triage", lambda **kw: out)
    section = brief_service.build_brief(today=dt.date(2026, 9, 24))["sections"]["needs_reply"]
    assert sorted(r["id"] for r in section["items"]) == ["person", "replied", "role"]
    assert section["also_in_inbox"]["count"] == 6
    assert {r["id"] for r in section["also_in_inbox"]["items"]} == {"unsub", "bulk", "noreply", "donot", "mkt", "mkthost"}


# ---------------------------------------------------------------------------
# Every run gets its own output folder
# ---------------------------------------------------------------------------

def test_two_runs_of_the_same_command_in_the_same_second_never_share_a_folder():
    folders = [artifact_service.create_run_folder("operator-Invoice Greg Alexander") for _ in range(3)]
    assert len({str(f) for f in folders}) == 3
    assert folders[1].name == folders[0].name + "-2" or folders[1].name != folders[0].name
