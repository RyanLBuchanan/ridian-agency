"""Dashboard aggregator for Ridian Command Center.

Combines memory counts, open follow-ups, recent runs, and a deterministic
list of suggested next actions into a single payload for the desktop
Dashboard view.

No model calls. No state mutation. Pure read-aggregate.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from . import memory_service, project_service


def _today_str() -> str:
    """Human-readable date for the dashboard header."""
    return datetime.now().strftime("%A, %B %-d, %Y") if hasattr(datetime.now(), "strftime") else datetime.now().strftime("%A, %B %d, %Y")


def _today_safe() -> str:
    # Windows strftime doesn't support %-d. Use %#d on Windows, fall back to %d.
    now = datetime.now()
    try:
        return now.strftime("%A, %B %#d, %Y")
    except ValueError:
        try:
            return now.strftime("%A, %B %-d, %Y")
        except ValueError:
            return now.strftime("%A, %B %d, %Y")


def _suggested_next_actions(
    *,
    memory_counts: dict,
    recent_runs: list[dict],
    open_follow_ups: list[dict],
    brand: dict,
) -> list[dict]:
    """Deterministic suggestion logic — no model calls.

    Each action is ``{ id, label, hint, target }`` where ``target`` is a
    soft hint the renderer can map to a view or modal.
    """
    actions: list[dict] = []

    if memory_counts.get("contacts", 0) == 0:
        actions.append({
            "id": "add-first-contact",
            "label": "Add your first Ridian contact.",
            "hint": "Memory is empty. Start with one person you want to follow up with.",
            "target": "memory:contacts",
        })

    brand_empty = all(
        not (brand.get(k, {}).get("voice") or "").strip() for k in ("ridian", "open_gulf", "buns")
    )
    if brand_empty:
        actions.append({
            "id": "add-brand-voice",
            "label": "Add Ridian or Open Gulf brand voice notes.",
            "hint": "Define voice, audience, and tone for sharper agent output.",
            "target": "memory:brand",
        })

    if open_follow_ups:
        n = len(open_follow_ups)
        actions.append({
            "id": "review-follow-ups",
            "label": f"Review {n} open follow-up{'s' if n != 1 else ''}.",
            "hint": "Mark anything complete or update the next step.",
            "target": "memory:follow-ups",
        })

    if recent_runs:
        latest = recent_runs[0]
        if latest.get("workflow") == "social":
            actions.append({
                "id": "review-recent-social",
                "label": "Review or upload your most recent social content.",
                "hint": latest.get("name", ""),
                "target": "run:" + latest.get("artifact_folder", ""),
            })
        else:
            actions.append({
                "id": "review-recent-business",
                "label": "Review your most recent business workflow.",
                "hint": latest.get("name", ""),
                "target": "run:" + latest.get("artifact_folder", ""),
            })
    else:
        actions.append({
            "id": "start-new-workflow",
            "label": "Start a new workflow.",
            "hint": "Open the wizard to create research, content, or a proposal.",
            "target": "wizard",
        })

    if memory_counts.get("facts", 0) == 0 and memory_counts.get("contacts", 0) > 0:
        actions.append({
            "id": "add-first-fact",
            "label": "Log a company fact so agents can use it later.",
            "hint": "Example: 'Q4 focus is AI workflow consulting for chambers.'",
            "target": "memory:facts",
        })

    # Agentic Advances daily-brief nudge
    last_agentic = _most_recent_run(recent_runs, "agentic")
    if not last_agentic or _hours_since(last_agentic.get("mtime_iso", "")) >= 24:
        actions.append({
            "id": "generate-agentic-brief",
            "label": "Generate today's Agentic Advances Brief.",
            "hint": "Daily scan of significant agentic AI advances relevant to Ridian.",
            "target": "workflow:agentic",
        })

    # NotebookLM review nudge — only if a recent package exists
    last_nlm = _most_recent_run(recent_runs, "notebooklm")
    if last_nlm and _hours_since(last_nlm.get("mtime_iso", "")) <= 72:
        actions.append({
            "id": "review-latest-notebooklm",
            "label": "Review latest NotebookLM package.",
            "hint": last_nlm.get("name", ""),
            "target": "run:" + last_nlm.get("artifact_folder", ""),
        })

    return actions


def _most_recent_run(runs: list[dict], workflow: str) -> dict | None:
    for r in runs:
        if r.get("workflow") == workflow:
            return r
    return None


def _hours_since(mtime_iso: str) -> float:
    if not mtime_iso:
        return float("inf")
    try:
        when = datetime.fromisoformat(mtime_iso)
    except ValueError:
        return float("inf")
    return (datetime.now() - when).total_seconds() / 3600.0


def build_dashboard(recent_limit: int = 6) -> dict:
    """Build the full Dashboard payload."""
    counts = memory_service.memory_summary()
    open_follow_ups = memory_service.list_open_follow_ups()
    brand = memory_service.get_brand()

    # Derived stat: how many of the 3 brand sections have a voice set.
    counts["brand_voices"] = sum(
        1 for k in ("ridian", "open_gulf", "buns")
        if (brand.get(k, {}).get("voice") or "").strip()
    )

    try:
        recent_runs = project_service.list_recent_projects(limit=recent_limit)
    except Exception:
        recent_runs = []

    actions = _suggested_next_actions(
        memory_counts=counts,
        recent_runs=recent_runs,
        open_follow_ups=open_follow_ups,
        brand=brand,
    )

    return {
        "today": _today_safe(),
        "memory_counts": counts,
        "open_follow_ups": open_follow_ups,
        "recent_runs": recent_runs,
        "suggested_next_actions": actions,
    }
