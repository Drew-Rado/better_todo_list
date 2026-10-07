"""The data model and single source of truth for one Better Todo List list.

Every list (= one config entry, see config_flow.py) gets one
`BetterTodoListStore` instance. It owns that list's tasks, persists them
to disk with Home Assistant's `Store` helper, and is the *only* place
that's allowed to mutate task data. Both the native todo.* entity
(todo.py) and the custom card's WebSocket commands (websocket_api.py)
call methods here instead of touching saved data directly - that way
validation, audit-log entries, and recurrence handling only need to be
written once and can't drift out of sync between the two.

--- The task dict shape ---

    {
        "id": "<uuid hex>",
        "title": str,
        "notes": str | None,
        "status": "needs_action" | "completed",
        "completed_at": "<ISO timestamp>" | None,
        "due_date": "YYYY-MM-DD" | None,
        "due_time": "HH:MM" | None,
        "priority": "low" | "medium" | "high" | None,
        "effort": "low" | "medium" | "high" | None,
        "tags": [str, ...],
        "area_id": "<HA area_id>" | None,     # this is the task's "Room"
        "assignees": ["person.<id>", ...],    # "Assigned to" - who gets reminders
        "reminder": <minutes before due> | None,
        "reminder_sent_for": str | None,      # see reminders.reminder_key()
        "sub_tasks": [
            {"id": "<uuid hex>", "title": str, "status": ..., "sort_order": int},
            ...
        ],
        "recurrence": {...} | None,            # see recurrence.py for the shape
        "cycle_start": "YYYY-MM-DD" | None,      # day the current cycle appeared
        "next_cycle_start": "YYYY-MM-DD" | None, # day the next cycle appears
        "sort_order": int,
        "history": [{"ts", "actor", "action", "field", "old", "new"}, ...],
        "created_at": "<ISO timestamp>",
        "updated_at": "<ISO timestamp>",
    }

--- Repeating tasks ---

A repeating task (every recurrence type except "interval: hours") is one
task that goes through "cycles": the repeat pattern decides the day each
cycle *appears*, and the recurrence's due rule decides when it's due (see
recurrence.py). Completing it just marks it completed - it stays hidden
until its next cycle appears, at which point async_advance_cycles() (run
just after midnight, and at startup) reopens it with the new due date and
resets its subtasks. If it wasn't completed by then, it's reset anyway and
a "missed" entry is added to its history. A task that is waiting for its
next (or first) cycle is hidden from the card and the native todo entity -
see is_waiting().

DEBUGGING TIP: every method below logs at DEBUG level what it did. Turn on
debug logging for `custom_components.better_todo_list` (see the README) to
watch these in Settings -> System -> Logs while you reproduce a problem.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, time
from typing import Any, Callable

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import history, recurrence
from .const import (
    DUE_RULE_DAYS_AFTER,
    DUE_RULE_SAME_DAY,
    DUE_RULE_WEEKDAY,
    EFFORTS,
    HISTORY_ACTION_COMPLETED,
    HISTORY_ACTION_CREATED,
    HISTORY_ACTION_MISSED,
    HISTORY_ACTION_RECURRED,
    HISTORY_ACTION_REMINDED,
    HISTORY_ACTION_REOPENED,
    HISTORY_ACTION_UPDATED,
    MAX_NOTES_LENGTH,
    MAX_SUBTASK_TITLE_LENGTH,
    MAX_TAGS,
    MAX_TAG_LENGTH,
    MAX_TITLE_LENGTH,
    PRIORITIES,
    RECURRENCE_END_TYPES,
    RECURRENCE_TYPES,
    REMINDER_OFFSETS_MINUTES,
    STATUS_COMPLETED,
    STATUS_NEEDS_ACTION,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

# Recurrence keys that describe *when it's due* rather than *when it repeats*,
# plus bookkeeping - changing only these doesn't restart the task's cycle.
_DUE_RULE_KEYS = ("due_rule", "due_days", "due_weekday")
_NON_PATTERN_KEYS = (*_DUE_RULE_KEYS, "occurrences_count")


def _str_to_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _str_to_time(value: str | None) -> time | None:
    return time.fromisoformat(value) if value else None


def _date_to_str(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _today() -> date:
    """Today in Home Assistant's configured time zone."""
    return dt_util.now().date()


def is_waiting(task: dict[str, Any]) -> bool:
    """Whether a repeating task is hidden until its next cycle appears:
    either it's completed and another cycle is coming, or its first cycle
    hasn't appeared yet. The card applies the same rule (isWaiting() in
    better-todo-list-card.js)."""
    if not task.get("next_cycle_start") or not recurrence.uses_cycles(task.get("recurrence")):
        return False
    return task.get("cycle_start") is None or task["status"] == STATUS_COMPLETED


def _pattern_of(spec: dict[str, Any] | None) -> dict[str, Any] | None:
    if spec is None:
        return None
    return {k: v for k, v in spec.items() if k not in _NON_PATTERN_KEYS}


def _due_rule_of(spec: dict[str, Any] | None) -> tuple[Any, ...] | None:
    if spec is None:
        return None
    rule = spec.get("due_rule", DUE_RULE_SAME_DAY)
    return (
        rule,
        spec.get("due_days") if rule == DUE_RULE_DAYS_AFTER else None,
        spec.get("due_weekday") if rule == DUE_RULE_WEEKDAY else None,
    )


def _apply_plan(task: dict[str, Any], plan: recurrence.CyclePlan) -> None:
    task["cycle_start"] = _date_to_str(plan.cycle_start)
    task["next_cycle_start"] = _date_to_str(plan.next_cycle_start)
    if plan.due_date is not None:
        task["due_date"] = plan.due_date.isoformat()
    task["recurrence"]["occurrences_count"] = plan.occurrences_count


def migrate_task_v1_to_v2(task: dict[str, Any], today: date) -> None:
    """Upgrade one task saved by version 1 (see const.STORAGE_VERSION).

    v1 repeating tasks were a single due date that rolled forward the
    moment they were completed. Each one keeps its repeat pattern with a
    "due the day it appears" rule, so its dates don't change; a due date
    still in the future is the next cycle (hidden until that day, since
    the previous one was already done), and one that's today or past is
    the current cycle.
    """
    task.setdefault("effort", None)
    task.setdefault("assignees", [])
    task.setdefault("reminder", None)
    task.setdefault("reminder_sent_for", None)
    task.setdefault("cycle_start", None)
    task.setdefault("next_cycle_start", None)

    spec = task.get("recurrence")
    if not recurrence.uses_cycles(spec):
        return
    spec.setdefault("due_rule", DUE_RULE_SAME_DAY)
    try:
        due = _str_to_date(task.get("due_date"))
        # v1 counted the pending occurrence too; v2 only counts appeared cycles.
        count = int(spec.get("occurrences_count", 1))
        if task["status"] == STATUS_COMPLETED:
            # v1 only left a repeating task completed once it had ended.
            task["cycle_start"] = _date_to_str(due)
        elif due is None:
            _apply_plan(task, recurrence.plan_cycle(spec, today))
        elif due > today:
            task["next_cycle_start"] = due.isoformat()
            spec["occurrences_count"] = max(0, count - 1)
        else:
            task["cycle_start"] = due.isoformat()
            if not recurrence.count_exhausted(spec, count):
                task["next_cycle_start"] = _date_to_str(recurrence.next_appearance_after(spec, due))
    except (recurrence.RecurrenceError, KeyError, ValueError) as err:
        _LOGGER.warning("Could not migrate recurrence of task %s, leaving it as-is: %s", task.get("id"), err)


class _TaskStore(Store):
    """Home Assistant's Store, plus our on-disk schema migrations."""

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: dict[str, Any]
    ) -> dict[str, Any]:
        if old_major_version < 2:
            today = _today()
            for task in old_data.get("tasks", []):
                migrate_task_v1_to_v2(task, today)
            _LOGGER.info("Migrated %d task(s) to storage version 2", len(old_data.get("tasks", [])))
        return old_data


class BetterTodoListStore:
    """Owns and persists the tasks for a single list."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self._store: Store = _TaskStore(hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}_{entry_id}")
        self._tasks: dict[str, dict[str, Any]] = {}
        self._listeners: list[Callable[[], None]] = []

    async def async_load(self) -> None:
        """Load this list's tasks from disk. Called once during setup."""
        data = await self._store.async_load()
        if data:
            self._tasks = {t["id"]: t for t in data.get("tasks", [])}
        _LOGGER.debug("Loaded %d task(s) for entry %s", len(self._tasks), self.entry_id)

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a callback fired after any change is persisted.

        todo.py uses this to know when to call async_write_ha_state() on
        the native entity, so it stays in sync no matter which "side"
        (native todo service or custom card) made the change.
        Returns a function that unregisters the listener.
        """
        self._listeners.append(listener)

        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    @property
    def tasks(self) -> list[dict[str, Any]]:
        """All tasks in this list, sorted for display."""
        return sorted(self._tasks.values(), key=lambda t: t["sort_order"])

    def get_task(self, task_id: str) -> dict[str, Any]:
        try:
            return self._tasks[task_id]
        except KeyError as err:
            raise HomeAssistantError(f"Task {task_id} not found") from err

    def as_diagnostics_dict(self) -> dict[str, Any]:
        """Everything diagnostics.py needs for a "download diagnostics" dump."""
        return {"entry_id": self.entry_id, "task_count": len(self._tasks), "tasks": self.tasks}

    async def _async_persist(self) -> None:
        await self._store.async_save({"tasks": list(self._tasks.values())})
        for listener in list(self._listeners):
            listener()

    def _next_sort_order(self) -> int:
        if not self._tasks:
            return 0
        return max(t["sort_order"] for t in self._tasks.values()) + 1

    # --- Field validation --------------------------------------------------------
    # Centralized here so both async_create_task and async_update_task (and
    # therefore both the native todo bridge and the WebSocket API) enforce
    # the exact same rules.

    def _validate_title(self, title: str) -> str:
        title = (title or "").strip()
        if not title:
            raise HomeAssistantError("A task needs a title.")
        if len(title) > MAX_TITLE_LENGTH:
            raise HomeAssistantError(f"Title is too long (max {MAX_TITLE_LENGTH} characters).")
        return title

    def _validate_subtask_title(self, title: str) -> str:
        title = (title or "").strip()
        if not title:
            raise HomeAssistantError("A subtask needs a title.")
        if len(title) > MAX_SUBTASK_TITLE_LENGTH:
            raise HomeAssistantError(
                f"Subtask title is too long (max {MAX_SUBTASK_TITLE_LENGTH} characters)."
            )
        return title

    def _validate_notes(self, notes: str | None) -> str | None:
        if notes is None:
            return None
        notes = notes.strip()
        if len(notes) > MAX_NOTES_LENGTH:
            raise HomeAssistantError(f"Notes are too long (max {MAX_NOTES_LENGTH} characters).")
        return notes or None

    def _validate_priority(self, priority: str | None) -> str | None:
        if priority is None:
            return None
        if priority not in PRIORITIES:
            raise HomeAssistantError(f"Priority must be one of {PRIORITIES}.")
        return priority

    def _validate_effort(self, effort: str | None) -> str | None:
        if effort is None:
            return None
        if effort not in EFFORTS:
            raise HomeAssistantError(f"Effort must be one of {EFFORTS}.")
        return effort

    def _validate_tags(self, tags: list[str] | None) -> list[str]:
        if not tags:
            return []
        if len(tags) > MAX_TAGS:
            raise HomeAssistantError(f"Too many tags (max {MAX_TAGS}).")
        cleaned: list[str] = []
        seen: set[str] = set()
        for tag in tags:
            tag = (tag or "").strip()
            if not tag:
                continue
            if len(tag) > MAX_TAG_LENGTH:
                raise HomeAssistantError(f"Tag '{tag}' is too long (max {MAX_TAG_LENGTH} characters).")
            key = tag.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(tag)
        return cleaned

    def _validate_due_date(self, due_date: str | None) -> str | None:
        if due_date is None:
            return None
        try:
            date.fromisoformat(due_date)
        except ValueError as err:
            raise HomeAssistantError("due_date must be in YYYY-MM-DD format.") from err
        return due_date

    def _validate_due_time(self, due_time: str | None) -> str | None:
        if due_time is None:
            return None
        try:
            time.fromisoformat(due_time)
        except ValueError as err:
            raise HomeAssistantError("due_time must be in HH:MM format.") from err
        return due_time

    def _validate_area_id(self, area_id: str | None) -> str | None:
        if area_id is None:
            return None
        registry = ar.async_get(self.hass)
        if registry.async_get_area(area_id) is None:
            raise HomeAssistantError(f"Unknown area_id: {area_id}")
        return area_id

    def _validate_assignees(self, assignees: list[str] | None) -> list[str]:
        cleaned: list[str] = []
        for entity_id in assignees or []:
            if not entity_id.startswith("person.") or self.hass.states.get(entity_id) is None:
                raise HomeAssistantError(f"Unknown person: {entity_id}")
            if entity_id not in cleaned:
                cleaned.append(entity_id)
        return cleaned

    def _validate_reminder(self, reminder: int | None) -> int | None:
        if reminder is None:
            return None
        if reminder not in REMINDER_OFFSETS_MINUTES:
            raise HomeAssistantError(f"Reminder must be one of {REMINDER_OFFSETS_MINUTES} (minutes before due).")
        return reminder

    def _validate_recurrence(self, recurrence_spec: dict[str, Any] | None) -> dict[str, Any] | None:
        if recurrence_spec is None:
            return None
        recurrence_spec = dict(recurrence_spec)
        rtype = recurrence_spec.get("type")
        if rtype not in RECURRENCE_TYPES:
            raise HomeAssistantError(f"Unknown recurrence type: {rtype!r}")
        if not recurrence_spec.get("start_date"):
            raise HomeAssistantError("Recurrence needs a start date.")
        try:
            date.fromisoformat(recurrence_spec["start_date"])
        except ValueError as err:
            raise HomeAssistantError("Recurrence start_date must be in YYYY-MM-DD format.") from err
        end_type = recurrence_spec.get("end_type", "none")
        if end_type not in RECURRENCE_END_TYPES:
            raise HomeAssistantError(f"Unknown recurrence end_type: {end_type!r}")
        # occurrences_count is bookkeeping owned by this file - never
        # trust (or require) a caller-supplied one.
        recurrence_spec.pop("occurrences_count", None)
        if recurrence.uses_cycles(recurrence_spec):
            recurrence_spec.setdefault("due_rule", DUE_RULE_SAME_DAY)
        # Per-type field checks (interval_value, weekdays, day_of_month...)
        # live in recurrence.py - planning a cycle once here surfaces any
        # of them as a normal error now, instead of on some later midnight.
        try:
            recurrence.validate_due_rule(recurrence_spec)
            if recurrence.uses_cycles(recurrence_spec):
                recurrence.plan_cycle(recurrence_spec, _today())
            else:
                recurrence.first_occurrence(recurrence_spec, None)
        except (recurrence.RecurrenceError, KeyError, TypeError, ValueError) as err:
            raise HomeAssistantError(f"Invalid repeat settings: {err}") from err
        return recurrence_spec

    # --- Recurrence / cycles -------------------------------------------------------

    def _set_recurrence(self, task: dict[str, Any], new_spec: dict[str, Any] | None) -> None:
        """Apply a (validated) recurrence to a task: a new or changed repeat
        pattern re-plans the task's cycle from today; a change to only the
        due rule keeps the current cycle and just recomputes its due date."""
        old_spec = task.get("recurrence")

        if new_spec is None:
            task["recurrence"] = None
            task["cycle_start"] = None
            task["next_cycle_start"] = None
            return

        if old_spec is not None and _pattern_of(old_spec) == _pattern_of(new_spec):
            new_spec["occurrences_count"] = old_spec.get("occurrences_count", 1)
            task["recurrence"] = new_spec
            if _due_rule_of(old_spec) != _due_rule_of(new_spec) and recurrence.uses_cycles(new_spec):
                anchor = _str_to_date(task.get("cycle_start") or task.get("next_cycle_start"))
                if anchor is not None:
                    task["due_date"] = recurrence.due_date_for_cycle(new_spec, anchor).isoformat()
            return

        task["recurrence"] = new_spec
        if recurrence.uses_cycles(new_spec):
            _apply_plan(task, recurrence.plan_cycle(new_spec, _today()))
            return

        # "interval: hours" - the original roll-forward-on-completion model.
        new_spec["occurrences_count"] = 1
        task["cycle_start"] = None
        task["next_cycle_start"] = None
        if task.get("due_date") is None:
            first_date, first_time = recurrence.first_occurrence(new_spec, _str_to_time(task.get("due_time")))
            task["due_date"] = first_date.isoformat()
            task["due_time"] = first_time.isoformat(timespec="minutes") if first_time else task.get("due_time")

    def _advance_cycle(self, task: dict[str, Any], today: date) -> bool:
        """Start the task's next cycle if it has appeared. Mutates `task`;
        returns whether anything changed. The caller persists."""
        spec = task.get("recurrence")
        next_start = _str_to_date(task.get("next_cycle_start"))
        if not recurrence.uses_cycles(spec) or next_start is None:
            return False
        try:
            plan = recurrence.advance_cycle(
                spec, next_start, today, int(spec.get("occurrences_count", 0))
            )
        except recurrence.RecurrenceError as err:
            _LOGGER.error("Could not compute the next cycle for task %s: %s", task["id"], err)
            return False
        if plan is None:
            return False

        if task.get("cycle_start") is not None and task["status"] != STATUS_COMPLETED:
            task["history"] = history.append(
                task["history"],
                history.make_entry("system", HISTORY_ACTION_MISSED, "due_date", task.get("due_date"), None),
            )

        for sub in task["sub_tasks"]:
            sub["status"] = STATUS_NEEDS_ACTION
        task["status"] = STATUS_NEEDS_ACTION
        task["completed_at"] = None
        _apply_plan(task, plan)
        task["updated_at"] = history.now_iso()
        task["history"] = history.append(
            task["history"],
            history.make_entry("system", HISTORY_ACTION_RECURRED, "due_date", None, task["due_date"]),
        )
        _LOGGER.debug(
            "Task %s: new cycle appeared %s, due %s, next %s",
            task["id"], task["cycle_start"], task["due_date"], task["next_cycle_start"],
        )
        return True

    async def async_advance_cycles(self, today: date | None = None) -> int:
        """Start the next cycle of every repeating task whose next cycle
        has appeared. Run by scheduler.py just after midnight and at
        startup. Returns how many tasks changed."""
        today = today or _today()
        changed = sum(1 for task in self._tasks.values() if self._advance_cycle(task, today))
        if changed:
            await self._async_persist()
            _LOGGER.debug("Started a new cycle for %d task(s) in entry %s", changed, self.entry_id)
        return changed

    def _reschedule_hourly_task(
        self, task: dict[str, Any], recurrence_spec: dict[str, Any], actor: str
    ) -> None:
        """After marking an "interval: hours" task completed, roll it
        forward to its next occurrence (or leave it completed if the
        recurrence has ended). Mutates `task` in place; the caller persists."""
        last_due_date = _str_to_date(task["due_date"])
        if last_due_date is None:
            _LOGGER.warning(
                "Task %s has recurrence enabled but no due_date - cannot compute "
                "the next occurrence, leaving it completed.",
                task["id"],
            )
            return

        last_due_time = _str_to_time(task["due_time"])
        try:
            result = recurrence.compute_next_occurrence(recurrence_spec, last_due_date, last_due_time)
        except recurrence.RecurrenceError as err:
            _LOGGER.error("Could not compute next occurrence for task %s: %s", task["id"], err)
            return

        if result is None:
            _LOGGER.debug("Recurrence for task %s has ended; leaving it completed.", task["id"])
            return

        next_date, next_time = result
        recurrence_spec["occurrences_count"] = int(recurrence_spec.get("occurrences_count", 1)) + 1

        # Reset subtasks so they need to be done again on the next occurrence too.
        for sub in task["sub_tasks"]:
            sub["status"] = STATUS_NEEDS_ACTION

        task["status"] = STATUS_NEEDS_ACTION
        task["completed_at"] = None
        task["due_date"] = next_date.isoformat()
        task["due_time"] = next_time.isoformat(timespec="minutes") if next_time else None
        task["history"] = history.append(
            task["history"],
            history.make_entry(actor, HISTORY_ACTION_RECURRED, "due_date", None, task["due_date"]),
        )
        _LOGGER.debug("Rescheduled hourly task %s to %s %s", task["id"], task["due_date"], task["due_time"])

    # --- Task CRUD -----------------------------------------------------------------

    async def async_create_task(
        self,
        *,
        title: str,
        notes: str | None = None,
        due_date: str | None = None,
        due_time: str | None = None,
        priority: str | None = None,
        effort: str | None = None,
        tags: list[str] | None = None,
        area_id: str | None = None,
        assignees: list[str] | None = None,
        reminder: int | None = None,
        recurrence_spec: dict[str, Any] | None = None,
        actor: str = "unknown",
    ) -> dict[str, Any]:
        """Create a new task and persist it."""
        task_id = uuid.uuid4().hex
        now = history.now_iso()
        task: dict[str, Any] = {
            "id": task_id,
            "title": self._validate_title(title),
            "notes": self._validate_notes(notes),
            "status": STATUS_NEEDS_ACTION,
            "completed_at": None,
            "due_date": self._validate_due_date(due_date),
            "due_time": self._validate_due_time(due_time),
            "priority": self._validate_priority(priority),
            "effort": self._validate_effort(effort),
            "tags": self._validate_tags(tags),
            "area_id": self._validate_area_id(area_id),
            "assignees": self._validate_assignees(assignees),
            "reminder": self._validate_reminder(reminder),
            "reminder_sent_for": None,
            "sub_tasks": [],
            "recurrence": None,
            "cycle_start": None,
            "next_cycle_start": None,
            "sort_order": self._next_sort_order(),
            "history": [],
            "created_at": now,
            "updated_at": now,
        }
        self._set_recurrence(task, self._validate_recurrence(recurrence_spec))
        task["history"] = history.append(
            task["history"], history.make_entry(actor, HISTORY_ACTION_CREATED)
        )

        self._tasks[task_id] = task
        await self._async_persist()
        _LOGGER.debug("Created task %s (%r) in entry %s", task_id, task["title"], self.entry_id)
        return task

    async def async_update_task(
        self, task_id: str, changes: dict[str, Any], actor: str = "unknown"
    ) -> dict[str, Any]:
        """Patch one or more fields on a task. Only keys present in
        `changes` are touched. Every field that actually changes gets its
        own audit-log entry (see history.diff_and_log)."""
        task = self.get_task(task_id)
        # Validate everything before touching the task, so a bad field
        # can't leave it half-updated.
        validators: dict[str, Callable[[Any], Any]] = {
            "title": self._validate_title,
            "notes": self._validate_notes,
            "due_date": self._validate_due_date,
            "due_time": self._validate_due_time,
            "priority": self._validate_priority,
            "effort": self._validate_effort,
            "tags": self._validate_tags,
            "area_id": self._validate_area_id,
            "assignees": self._validate_assignees,
            "reminder": self._validate_reminder,
            "recurrence": self._validate_recurrence,
        }
        validated = {key: validators[key](value) for key, value in changes.items() if key in validators}

        before = dict(task)
        for key, value in validated.items():
            if key != "recurrence":
                task[key] = value
        if "recurrence" in validated:
            self._set_recurrence(task, validated["recurrence"])

        task["updated_at"] = history.now_iso()
        task["history"] = history.diff_and_log(task["history"], before, task, actor)

        await self._async_persist()
        _LOGGER.debug("Updated task %s: fields=%s", task_id, list(changes.keys()))
        return task

    async def async_set_due_rule(self, rule_fields: dict[str, Any], actor: str = "unknown") -> int:
        """Apply one due rule to every repeating task in this list (the
        better_todo_list.set_due_rule service). Returns how many changed."""
        changed = 0
        for task in self._tasks.values():
            spec = task.get("recurrence")
            if not recurrence.uses_cycles(spec):
                continue
            new_spec = {k: v for k, v in spec.items() if k not in _DUE_RULE_KEYS}
            new_spec.update(rule_fields)
            try:
                recurrence.validate_due_rule(new_spec)
            except recurrence.RecurrenceError as err:
                raise HomeAssistantError(str(err)) from err
            if _due_rule_of(spec) == _due_rule_of(new_spec):
                continue
            before = dict(task)
            self._set_recurrence(task, new_spec)
            task["updated_at"] = history.now_iso()
            task["history"] = history.diff_and_log(task["history"], before, task, actor)
            changed += 1
        if changed:
            await self._async_persist()
        _LOGGER.debug("Set due rule %s on %d task(s) in entry %s", rule_fields, changed, self.entry_id)
        return changed

    async def async_delete_task(self, task_id: str, actor: str = "unknown") -> None:
        task = self.get_task(task_id)
        del self._tasks[task_id]
        await self._async_persist()
        _LOGGER.debug(
            "Deleted task %s (%r) from entry %s (actor=%s)", task_id, task["title"], self.entry_id, actor
        )

    async def async_complete_task(self, task_id: str, actor: str = "unknown") -> dict[str, Any]:
        """Mark a task complete. Refuses if any subtask is still open (the
        subtasks are "things that must be done to mark the main task
        complete" per the spec). A repeating task then stays completed
        (and hidden) until its next cycle appears - except "interval:
        hours" ones, which are immediately rescheduled instead.
        """
        task = self.get_task(task_id)

        incomplete = [s for s in task["sub_tasks"] if s["status"] != STATUS_COMPLETED]
        if incomplete:
            raise HomeAssistantError(
                f"Cannot complete '{task['title']}': {len(incomplete)} subtask(s) still open."
            )

        now = history.now_iso()
        task["status"] = STATUS_COMPLETED
        task["completed_at"] = now
        task["updated_at"] = now
        task["history"] = history.append(task["history"], history.make_entry(actor, HISTORY_ACTION_COMPLETED))

        recurrence_spec = task.get("recurrence")
        if recurrence_spec is not None and not recurrence.uses_cycles(recurrence_spec):
            self._reschedule_hourly_task(task, recurrence_spec, actor)

        await self._async_persist()
        _LOGGER.debug("Completed task %s (%r)", task_id, task["title"])
        return task

    async def async_reopen_task(self, task_id: str, actor: str = "unknown") -> dict[str, Any]:
        task = self.get_task(task_id)
        task["status"] = STATUS_NEEDS_ACTION
        task["completed_at"] = None
        task["updated_at"] = history.now_iso()
        task["history"] = history.append(task["history"], history.make_entry(actor, HISTORY_ACTION_REOPENED))
        await self._async_persist()
        _LOGGER.debug("Reopened task %s (%r)", task_id, task["title"])
        return task

    async def async_reorder_tasks(self, ordered_task_ids: list[str]) -> None:
        for index, task_id in enumerate(ordered_task_ids):
            if task_id in self._tasks:
                self._tasks[task_id]["sort_order"] = index
        await self._async_persist()
        _LOGGER.debug("Reordered %d task(s) in entry %s", len(ordered_task_ids), self.entry_id)

    # --- Reminders ---------------------------------------------------------------

    async def async_mark_reminded(self, sent: dict[str, tuple[str, list[str]]]) -> None:
        """Record that reminders went out, so each is only sent once per
        due date. `sent` maps task_id -> (reminder key, names notified)."""
        for task_id, (key, names) in sent.items():
            task = self._tasks.get(task_id)
            if task is None:
                continue
            task["reminder_sent_for"] = key
            task["history"] = history.append(
                task["history"],
                history.make_entry("system", HISTORY_ACTION_REMINDED, "assignees", None, ", ".join(names) or None),
            )
        if sent:
            await self._async_persist()

    # --- Subtasks --------------------------------------------------------------

    def _get_sub_task(self, task: dict[str, Any], sub_task_id: str) -> dict[str, Any]:
        for sub in task["sub_tasks"]:
            if sub["id"] == sub_task_id:
                return sub
        raise HomeAssistantError(f"Subtask {sub_task_id} not found")

    async def async_add_sub_task(
        self, task_id: str, title: str, actor: str = "unknown"
    ) -> dict[str, Any]:
        task = self.get_task(task_id)
        title = self._validate_subtask_title(title)
        sub_task = {
            "id": uuid.uuid4().hex,
            "title": title,
            "status": STATUS_NEEDS_ACTION,
            "sort_order": len(task["sub_tasks"]),
        }
        task["sub_tasks"].append(sub_task)
        task["updated_at"] = history.now_iso()
        task["history"] = history.append(
            task["history"],
            history.make_entry(actor, HISTORY_ACTION_UPDATED, "sub_tasks", None, f"added '{title}'"),
        )
        await self._async_persist()
        return task

    async def async_update_sub_task(
        self, task_id: str, sub_task_id: str, changes: dict[str, Any], actor: str = "unknown"
    ) -> dict[str, Any]:
        task = self.get_task(task_id)
        sub = self._get_sub_task(task, sub_task_id)

        if "title" in changes:
            sub["title"] = self._validate_subtask_title(changes["title"])
        if "status" in changes:
            new_status = changes["status"]
            if new_status not in (STATUS_NEEDS_ACTION, STATUS_COMPLETED):
                raise HomeAssistantError(f"Invalid subtask status: {new_status!r}")
            sub["status"] = new_status

        task["updated_at"] = history.now_iso()
        task["history"] = history.append(
            task["history"],
            history.make_entry(actor, HISTORY_ACTION_UPDATED, "sub_tasks", None, f"updated '{sub['title']}'"),
        )
        await self._async_persist()
        return task

    async def async_delete_sub_task(
        self, task_id: str, sub_task_id: str, actor: str = "unknown"
    ) -> dict[str, Any]:
        task = self.get_task(task_id)
        sub = self._get_sub_task(task, sub_task_id)
        task["sub_tasks"] = [s for s in task["sub_tasks"] if s["id"] != sub_task_id]
        task["updated_at"] = history.now_iso()
        task["history"] = history.append(
            task["history"],
            history.make_entry(actor, HISTORY_ACTION_UPDATED, "sub_tasks", None, f"removed '{sub['title']}'"),
        )
        await self._async_persist()
        return task

    async def async_reorder_sub_tasks(
        self, task_id: str, ordered_sub_task_ids: list[str]
    ) -> dict[str, Any]:
        task = self.get_task(task_id)
        by_id = {s["id"]: s for s in task["sub_tasks"]}
        for index, sub_id in enumerate(ordered_sub_task_ids):
            if sub_id in by_id:
                by_id[sub_id]["sort_order"] = index
        task["sub_tasks"].sort(key=lambda s: s["sort_order"])
        task["updated_at"] = history.now_iso()
        await self._async_persist()
        return task

    # --- Room (HA Area) / person cleanup ------------------------------------------

    async def async_clear_area_references(self, area_id: str) -> None:
        """Called from __init__.py when an HA Area is deleted. Nulls out
        any task's Room that pointed at it, so tasks never keep a
        dangling reference to an area that no longer exists."""
        affected = [t for t in self._tasks.values() if t.get("area_id") == area_id]
        if not affected:
            return
        for task in affected:
            task["area_id"] = None
            task["updated_at"] = history.now_iso()
            task["history"] = history.append(
                task["history"],
                history.make_entry("system", HISTORY_ACTION_UPDATED, "area_id", area_id, None),
            )
        await self._async_persist()
        _LOGGER.debug(
            "Cleared deleted area %s from %d task(s) in entry %s", area_id, len(affected), self.entry_id
        )

    async def async_clear_person_references(self, entity_id: str) -> None:
        """Called from __init__.py when a person is deleted. Removes them
        from every task's "Assigned to"."""
        affected = [t for t in self._tasks.values() if entity_id in t.get("assignees", [])]
        if not affected:
            return
        for task in affected:
            old = list(task["assignees"])
            task["assignees"] = [a for a in old if a != entity_id]
            task["updated_at"] = history.now_iso()
            task["history"] = history.append(
                task["history"],
                history.make_entry("system", HISTORY_ACTION_UPDATED, "assignees", old, task["assignees"]),
            )
        await self._async_persist()
