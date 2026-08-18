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
        "tags": [str, ...],
        "area_id": "<HA area_id>" | None,     # this is the task's "Room"
        "sub_tasks": [
            {"id": "<uuid hex>", "title": str, "status": ..., "sort_order": int},
            ...
        ],
        "recurrence": {...} | None,            # see recurrence.py for the shape
        "sort_order": int,
        "history": [{"ts", "actor", "action", "field", "old", "new"}, ...],
        "created_at": "<ISO timestamp>",
        "updated_at": "<ISO timestamp>",
    }

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

from . import history, recurrence
from .const import (
    HISTORY_ACTION_COMPLETED,
    HISTORY_ACTION_CREATED,
    HISTORY_ACTION_RECURRED,
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
    STATUS_COMPLETED,
    STATUS_NEEDS_ACTION,
    STORAGE_KEY_PREFIX,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)


def _str_to_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _str_to_time(value: str | None) -> time | None:
    return time.fromisoformat(value) if value else None


class BetterTodoListStore:
    """Owns and persists the tasks for a single list."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self._store: Store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY_PREFIX}_{entry_id}")
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

    def _validate_recurrence(self, recurrence_spec: dict[str, Any] | None) -> dict[str, Any] | None:
        if recurrence_spec is None:
            return None
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
        # Per-type field checks (interval_value, weekdays, day_of_month...)
        # are deliberately left to recurrence.py: it already validates its
        # own inputs the moment it computes an occurrence, so duplicating
        # those checks here would just be two places to keep in sync.
        recurrence_spec.setdefault("occurrences_count", 1)
        return recurrence_spec

    def _maybe_seed_due_date_from_recurrence(self, task: dict[str, Any]) -> None:
        """If a task has recurrence enabled but no due_date yet (freshly
        made recurring, or created without picking a date), compute and
        fill in the first occurrence so the recurrence has a date to
        anchor to - see recurrence.first_occurrence()."""
        if task.get("recurrence") is None or task.get("due_date") is not None:
            return
        first_date, first_time = recurrence.first_occurrence(
            task["recurrence"], _str_to_time(task.get("due_time"))
        )
        task["due_date"] = first_date.isoformat()
        task["due_time"] = first_time.isoformat() if first_time else task.get("due_time")

    # --- Task CRUD -----------------------------------------------------------------

    async def async_create_task(
        self,
        *,
        title: str,
        notes: str | None = None,
        due_date: str | None = None,
        due_time: str | None = None,
        priority: str | None = None,
        tags: list[str] | None = None,
        area_id: str | None = None,
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
            "tags": self._validate_tags(tags),
            "area_id": self._validate_area_id(area_id),
            "sub_tasks": [],
            "recurrence": self._validate_recurrence(recurrence_spec),
            "sort_order": self._next_sort_order(),
            "history": [],
            "created_at": now,
            "updated_at": now,
        }
        self._maybe_seed_due_date_from_recurrence(task)
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
        before = dict(task)

        if "title" in changes:
            task["title"] = self._validate_title(changes["title"])
        if "notes" in changes:
            task["notes"] = self._validate_notes(changes["notes"])
        if "due_date" in changes:
            task["due_date"] = self._validate_due_date(changes["due_date"])
        if "due_time" in changes:
            task["due_time"] = self._validate_due_time(changes["due_time"])
        if "priority" in changes:
            task["priority"] = self._validate_priority(changes["priority"])
        if "tags" in changes:
            task["tags"] = self._validate_tags(changes["tags"])
        if "area_id" in changes:
            task["area_id"] = self._validate_area_id(changes["area_id"])
        if "recurrence" in changes:
            task["recurrence"] = self._validate_recurrence(changes["recurrence"])

        self._maybe_seed_due_date_from_recurrence(task)
        task["updated_at"] = history.now_iso()
        task["history"] = history.diff_and_log(task["history"], before, task, actor)

        await self._async_persist()
        _LOGGER.debug("Updated task %s: fields=%s", task_id, list(changes.keys()))
        return task

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
        complete" per the spec). If the task recurs, immediately computes
        and applies the next occurrence instead of leaving it completed.
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
        if recurrence_spec is not None:
            self._reschedule_recurring_task(task, recurrence_spec, actor)

        await self._async_persist()
        _LOGGER.debug("Completed task %s (%r)", task_id, task["title"])
        return task

    def _reschedule_recurring_task(
        self, task: dict[str, Any], recurrence_spec: dict[str, Any], actor: str
    ) -> None:
        """After marking a recurring task completed, roll it forward to
        its next occurrence (or leave it completed if the recurrence has
        ended). Mutates `task` in place; the caller persists."""
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
        task["due_time"] = next_time.isoformat() if next_time else None
        task["history"] = history.append(
            task["history"],
            history.make_entry(actor, HISTORY_ACTION_RECURRED, "due_date", None, task["due_date"]),
        )
        _LOGGER.debug("Rescheduled recurring task %s to %s", task["id"], task["due_date"])

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

    # --- Room (HA Area) cleanup --------------------------------------------------

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
