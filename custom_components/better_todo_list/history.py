"""Audit-log helpers for Better Todo List.

Every task keeps its own `history` list: one entry per change, recording
*who* changed *what*, from *what* value to *what* value, and *when*. This
file has two small jobs - build one of those entries, and append it to a
task's history list without letting that list grow forever - kept
separate from store.py so the "how do we log a change" logic is in one
obvious place.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .const import MAX_HISTORY_ENTRIES


def now_iso() -> str:
    """UTC timestamp in ISO-8601. Used for every timestamp this integration writes."""
    return datetime.now(timezone.utc).isoformat()


def make_entry(
    actor: str,
    action: str,
    field: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
) -> dict[str, Any]:
    """Build one audit-log entry. `actor` is a human-readable name (a HA
    user's name, "Automation", or "system" for integration-driven changes
    like area cleanup) - see websocket_api.py and __init__.py for how it's
    resolved from the caller's context."""
    return {
        "ts": now_iso(),
        "actor": actor,
        "action": action,
        "field": field,
        "old": old_value,
        "new": new_value,
    }


def append(history: list[dict[str, Any]], entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Append an entry, dropping the oldest ones past MAX_HISTORY_ENTRIES
    so a task edited thousands of times over the years doesn't grow its
    history list forever."""
    history.append(entry)
    if len(history) > MAX_HISTORY_ENTRIES:
        del history[: len(history) - MAX_HISTORY_ENTRIES]
    return history


# Fields that shouldn't generate their own "field changed" entry when
# diffing an update: they're internal bookkeeping, or they already get a
# more descriptive, dedicated history entry from a more specific call site
# (e.g. status changes are logged by async_complete_task/async_reopen_task
# with a proper "completed"/"reopened" action instead of a generic one).
_DIFF_IGNORED_FIELDS = {
    "id",
    "history",
    "updated_at",
    "created_at",
    "sort_order",
    "sub_tasks",
    "status",
    "completed_at",
}


def diff_and_log(
    history_list: list[dict[str, Any]],
    before: dict[str, Any],
    after: dict[str, Any],
    actor: str,
) -> list[dict[str, Any]]:
    """Compare two versions of a task and log one "updated" history entry
    per field that actually changed."""
    for field, new_value in after.items():
        if field in _DIFF_IGNORED_FIELDS:
            continue
        old_value = before.get(field)
        if old_value != new_value:
            append(history_list, make_entry(actor, "updated", field, old_value, new_value))
    return history_list
