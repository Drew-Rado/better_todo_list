"""Bridges each Better Todo List "list" to Home Assistant's native `todo`
entity platform.

This is what makes a list show up as `todo.<your_list_name>` so it also
works in HA's built-in Todo Lovelace card, Assist voice control ("add
milk to my shopping list"), and the Companion App's todo widget - on top
of the richer custom card described in websocket_api.py and
better-todo-list-card.js.

Home Assistant's built-in TodoItem only understands summary/status/due/
description - it has no concept of priority, tags, room, subtasks, or
recurrence, so those fields are only visible/editable through the custom
card. Every write, from either side, goes through store.py so both stay
in sync (see store.py's `add_listener`).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time
from typing import Any

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, STATUS_COMPLETED
from .store import BetterTodoListStore

_LOGGER = logging.getLogger(__name__)

_SUPPORTED_FEATURES = (
    TodoListEntityFeature.CREATE_TODO_ITEM
    | TodoListEntityFeature.UPDATE_TODO_ITEM
    | TodoListEntityFeature.DELETE_TODO_ITEM
    | TodoListEntityFeature.MOVE_TODO_ITEM
    | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
    | TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM
    | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the native todo.* entity for this list."""
    store: BetterTodoListStore = hass.data[DOMAIN]["stores"][entry.entry_id]
    async_add_entities([BetterTodoListTodoListEntity(store, entry)])


class BetterTodoListTodoListEntity(TodoListEntity):
    """A native HA todo list entity backed by a BetterTodoListStore."""

    _attr_should_poll = False
    _attr_supported_features = _SUPPORTED_FEATURES

    def __init__(self, store: BetterTodoListStore, entry: ConfigEntry) -> None:
        self._store = store
        self._attr_name = entry.title
        self._attr_unique_id = entry.entry_id
        self._remove_listener: callable | None = None

    async def async_added_to_hass(self) -> None:
        """Subscribe to store changes so we refresh state no matter which
        side (native todo service call or the custom card) wrote them."""
        await super().async_added_to_hass()
        self._remove_listener = self._store.add_listener(self._handle_store_changed)

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_listener is not None:
            self._remove_listener()
        await super().async_will_remove_from_hass()

    def _handle_store_changed(self) -> None:
        self.async_write_ha_state()

    @property
    def todo_items(self) -> list[TodoItem]:
        return [_task_to_todo_item(task) for task in self._store.tasks]

    async def async_create_todo_item(self, item: TodoItem) -> None:
        due_date, due_time = _split_due(item.due)
        await self._store.async_create_task(
            title=item.summary or "",
            notes=item.description,
            due_date=due_date,
            due_time=due_time,
            actor="Home Assistant todo",
        )

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """HA's `todo` component always calls this with a *complete* item
        (it merges your change into the existing item before calling us -
        see homeassistant/components/todo/__init__.py's
        `_async_update_todo_item`), so we can safely overwrite every field
        unconditionally, the same way HA's own `local_todo` integration
        does. The one thing we must NOT do unconditionally is call
        complete/reopen - since `item.status` reflects the *current*
        status even when only the title changed, blindly calling
        async_complete_task() on every edit would re-run (and re-log) a
        completion, and would even re-trigger recurrence, every time you
        just renamed an already-completed task.
        """
        if item.uid is None:
            raise ValueError("Cannot update a todo item without a uid")

        current = self._store.get_task(item.uid)
        due_date, due_time = _split_due(item.due)
        await self._store.async_update_task(
            item.uid,
            {
                "title": item.summary or "",
                "notes": item.description,
                "due_date": due_date,
                "due_time": due_time,
            },
            actor="Home Assistant todo",
        )

        new_status = (
            STATUS_COMPLETED if item.status == TodoItemStatus.COMPLETED else "needs_action"
        )
        if new_status != current["status"]:
            if new_status == STATUS_COMPLETED:
                await self._store.async_complete_task(item.uid, actor="Home Assistant todo")
            else:
                await self._store.async_reopen_task(item.uid, actor="Home Assistant todo")

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        for uid in uids:
            await self._store.async_delete_task(uid, actor="Home Assistant todo")

    async def async_move_todo_item(self, uid: str, previous_uid: str | None = None) -> None:
        ordered_ids = [t["id"] for t in self._store.tasks]
        ordered_ids.remove(uid)
        if previous_uid is None:
            ordered_ids.insert(0, uid)
        else:
            ordered_ids.insert(ordered_ids.index(previous_uid) + 1, uid)
        await self._store.async_reorder_tasks(ordered_ids)


def _split_due(due: date | datetime | None) -> tuple[str | None, str | None]:
    """Split HA's combined `due` (a date or datetime) into our separate
    due_date/due_time strings."""
    if due is None:
        return None, None
    if isinstance(due, datetime):
        return due.date().isoformat(), due.time().isoformat(timespec="minutes")
    return due.isoformat(), None


def _task_to_todo_item(task: dict[str, Any]) -> TodoItem:
    due: date | datetime | None = None
    if task["due_date"]:
        due_date = date.fromisoformat(task["due_date"])
        if task["due_time"]:
            due = datetime.combine(due_date, time.fromisoformat(task["due_time"]))
        else:
            due = due_date

    return TodoItem(
        uid=task["id"],
        summary=task["title"],
        status=(
            TodoItemStatus.COMPLETED
            if task["status"] == STATUS_COMPLETED
            else TodoItemStatus.NEEDS_ACTION
        ),
        due=due,
        description=task["notes"],
    )
