"""Custom WebSocket API for Better Todo List.

Home Assistant's built-in `todo` entity schema (see todo.py) has no room
for priority, tags, room, subtasks, recurrence, or history - so the
custom Lovelace card (better-todo-list-card.js) talks to these
`better_todo_list/*` WebSocket commands instead, whenever it needs one of
those richer fields.

Every command here does the same three things: look up the right list's
Store (`_get_store`), ask it to do something (all the actual mutation
logic lives in store.py - this file just translates between
JSON-over-WebSocket and Python calls), and turn any HomeAssistantError
into a WebSocket error the card can display to you.

DEBUGGING TIP: open your browser's DevTools -> Network -> WS tab while
using the card to see these commands and their responses directly - that,
plus DEBUG-level logs (see README), covers most "why isn't this working"
questions.
"""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .store import BetterTodoListStore

_LOGGER = logging.getLogger(__name__)

# Optional task fields shared by create_task and update_task's schemas, so
# both stay in sync when a field is added.
_TASK_FIELDS_OPTIONAL = {
    vol.Optional("notes"): vol.Any(str, None),
    vol.Optional("due_date"): vol.Any(str, None),
    vol.Optional("due_time"): vol.Any(str, None),
    vol.Optional("priority"): vol.Any(str, None),
    vol.Optional("tags"): [cv.string],
    vol.Optional("area_id"): vol.Any(str, None),
    vol.Optional("recurrence"): vol.Any(dict, None),
}
_UPDATABLE_TASK_FIELDS = ("title", "notes", "due_date", "due_time", "priority", "tags", "area_id", "recurrence")


def _actor_name(connection: websocket_api.ActiveConnection) -> str:
    """Best-effort human-readable name of whoever is making this call, for
    the task's audit history (see history.py)."""
    user = connection.user
    if user is None:
        return "Unknown user"
    return user.name or user.id


def _get_store(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg_id: int, entry_id: str
) -> BetterTodoListStore | None:
    """Look up a list's Store, or send a WebSocket error and return None."""
    store = hass.data.get(DOMAIN, {}).get("stores", {}).get(entry_id)
    if store is None:
        connection.send_error(msg_id, "list_not_found", f"No Better Todo List with entry_id {entry_id}")
        return None
    return store


def _send_store_error(connection: websocket_api.ActiveConnection, msg_id: int, err: HomeAssistantError) -> None:
    _LOGGER.debug("WS command failed: %s", err)
    connection.send_error(msg_id, "better_todo_list_error", str(err))


# --- Lists / rooms ----------------------------------------------------------------


@websocket_api.websocket_command({vol.Required("type"): "better_todo_list/get_lists"})
@websocket_api.async_response
async def handle_get_lists(hass, connection, msg):
    """All configured lists, so the card knows what's available.

    Includes each list's native todo.* entity_id (looked up by unique_id,
    since the entity_id itself is name-derived and can change) so the card
    can detect when one of them changes - see _maybeSyncOnHassChange in
    better-todo-list-card.js, which compares this entity's state object
    across the `hass` updates Home Assistant's frontend already delivers
    to every card. todo.py calls async_write_ha_state() after every store
    mutation, from any client, which is what makes that state object
    change - so this piggybacks on Home Assistant's existing real-time
    entity updates instead of building a separate push mechanism.
    """
    stores: dict[str, BetterTodoListStore] = hass.data.get(DOMAIN, {}).get("stores", {})
    registry = er.async_get(hass)
    lists = [
        {
            "entry_id": entry.entry_id,
            "name": entry.title,
            "entity_id": registry.async_get_entity_id("todo", DOMAIN, entry.entry_id),
        }
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.entry_id in stores
    ]
    connection.send_result(msg["id"], {"lists": lists})


@websocket_api.websocket_command({vol.Required("type"): "better_todo_list/get_areas"})
@websocket_api.async_response
async def handle_get_areas(hass, connection, msg):
    """All HA Areas, for the card's Room picker."""
    registry = ar.async_get(hass)
    areas = sorted(
        (
            {"area_id": area.id, "name": area.name, "icon": area.icon}
            for area in registry.async_list_areas()
        ),
        key=lambda a: a["name"].lower(),
    )
    connection.send_result(msg["id"], {"areas": areas})


# --- Tasks --------------------------------------------------------------------


@websocket_api.websocket_command(
    {vol.Required("type"): "better_todo_list/get_tasks", vol.Required("entry_id"): cv.string}
)
@websocket_api.async_response
async def handle_get_tasks(hass, connection, msg):
    store = _get_store(hass, connection, msg["id"], msg["entry_id"])
    if store is None:
        return
    connection.send_result(msg["id"], {"tasks": store.tasks})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "better_todo_list/create_task",
        vol.Required("entry_id"): cv.string,
        vol.Required("title"): cv.string,
        **_TASK_FIELDS_OPTIONAL,
    }
)
@websocket_api.async_response
async def handle_create_task(hass, connection, msg):
    store = _get_store(hass, connection, msg["id"], msg["entry_id"])
    if store is None:
        return
    try:
        task = await store.async_create_task(
            title=msg["title"],
            notes=msg.get("notes"),
            due_date=msg.get("due_date"),
            due_time=msg.get("due_time"),
            priority=msg.get("priority"),
            tags=msg.get("tags"),
            area_id=msg.get("area_id"),
            recurrence_spec=msg.get("recurrence"),
            actor=_actor_name(connection),
        )
    except HomeAssistantError as err:
        _send_store_error(connection, msg["id"], err)
        return
    connection.send_result(msg["id"], {"task": task})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "better_todo_list/update_task",
        vol.Required("entry_id"): cv.string,
        vol.Required("task_id"): cv.string,
        vol.Optional("title"): cv.string,
        **_TASK_FIELDS_OPTIONAL,
    }
)
@websocket_api.async_response
async def handle_update_task(hass, connection, msg):
    store = _get_store(hass, connection, msg["id"], msg["entry_id"])
    if store is None:
        return
    # Only fields the card actually sent are treated as "changed" - this is
    # what lets the card send e.g. just {"priority": "high"} without
    # clobbering the rest of the task.
    changes = {key: msg[key] for key in _UPDATABLE_TASK_FIELDS if key in msg}
    try:
        task = await store.async_update_task(msg["task_id"], changes, actor=_actor_name(connection))
    except HomeAssistantError as err:
        _send_store_error(connection, msg["id"], err)
        return
    connection.send_result(msg["id"], {"task": task})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "better_todo_list/delete_task",
        vol.Required("entry_id"): cv.string,
        vol.Required("task_id"): cv.string,
    }
)
@websocket_api.async_response
async def handle_delete_task(hass, connection, msg):
    store = _get_store(hass, connection, msg["id"], msg["entry_id"])
    if store is None:
        return
    try:
        await store.async_delete_task(msg["task_id"], actor=_actor_name(connection))
    except HomeAssistantError as err:
        _send_store_error(connection, msg["id"], err)
        return
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "better_todo_list/complete_task",
        vol.Required("entry_id"): cv.string,
        vol.Required("task_id"): cv.string,
    }
)
@websocket_api.async_response
async def handle_complete_task(hass, connection, msg):
    store = _get_store(hass, connection, msg["id"], msg["entry_id"])
    if store is None:
        return
    try:
        task = await store.async_complete_task(msg["task_id"], actor=_actor_name(connection))
    except HomeAssistantError as err:
        _send_store_error(connection, msg["id"], err)
        return
    connection.send_result(msg["id"], {"task": task})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "better_todo_list/reopen_task",
        vol.Required("entry_id"): cv.string,
        vol.Required("task_id"): cv.string,
    }
)
@websocket_api.async_response
async def handle_reopen_task(hass, connection, msg):
    store = _get_store(hass, connection, msg["id"], msg["entry_id"])
    if store is None:
        return
    try:
        task = await store.async_reopen_task(msg["task_id"], actor=_actor_name(connection))
    except HomeAssistantError as err:
        _send_store_error(connection, msg["id"], err)
        return
    connection.send_result(msg["id"], {"task": task})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "better_todo_list/reorder_tasks",
        vol.Required("entry_id"): cv.string,
        vol.Required("task_ids"): [cv.string],
    }
)
@websocket_api.async_response
async def handle_reorder_tasks(hass, connection, msg):
    store = _get_store(hass, connection, msg["id"], msg["entry_id"])
    if store is None:
        return
    await store.async_reorder_tasks(msg["task_ids"])
    connection.send_result(msg["id"], {})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "better_todo_list/get_task_history",
        vol.Required("entry_id"): cv.string,
        vol.Required("task_id"): cv.string,
    }
)
@websocket_api.async_response
async def handle_get_task_history(hass, connection, msg):
    store = _get_store(hass, connection, msg["id"], msg["entry_id"])
    if store is None:
        return
    try:
        task = store.get_task(msg["task_id"])
    except HomeAssistantError as err:
        _send_store_error(connection, msg["id"], err)
        return
    connection.send_result(msg["id"], {"history": task["history"]})


# --- Subtasks ------------------------------------------------------------------


@websocket_api.websocket_command(
    {
        vol.Required("type"): "better_todo_list/add_sub_task",
        vol.Required("entry_id"): cv.string,
        vol.Required("task_id"): cv.string,
        vol.Required("title"): cv.string,
    }
)
@websocket_api.async_response
async def handle_add_sub_task(hass, connection, msg):
    store = _get_store(hass, connection, msg["id"], msg["entry_id"])
    if store is None:
        return
    try:
        task = await store.async_add_sub_task(msg["task_id"], msg["title"], actor=_actor_name(connection))
    except HomeAssistantError as err:
        _send_store_error(connection, msg["id"], err)
        return
    connection.send_result(msg["id"], {"task": task})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "better_todo_list/update_sub_task",
        vol.Required("entry_id"): cv.string,
        vol.Required("task_id"): cv.string,
        vol.Required("sub_task_id"): cv.string,
        vol.Optional("title"): cv.string,
        vol.Optional("status"): cv.string,
    }
)
@websocket_api.async_response
async def handle_update_sub_task(hass, connection, msg):
    store = _get_store(hass, connection, msg["id"], msg["entry_id"])
    if store is None:
        return
    changes = {key: msg[key] for key in ("title", "status") if key in msg}
    try:
        task = await store.async_update_sub_task(
            msg["task_id"], msg["sub_task_id"], changes, actor=_actor_name(connection)
        )
    except HomeAssistantError as err:
        _send_store_error(connection, msg["id"], err)
        return
    connection.send_result(msg["id"], {"task": task})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "better_todo_list/delete_sub_task",
        vol.Required("entry_id"): cv.string,
        vol.Required("task_id"): cv.string,
        vol.Required("sub_task_id"): cv.string,
    }
)
@websocket_api.async_response
async def handle_delete_sub_task(hass, connection, msg):
    store = _get_store(hass, connection, msg["id"], msg["entry_id"])
    if store is None:
        return
    try:
        task = await store.async_delete_sub_task(
            msg["task_id"], msg["sub_task_id"], actor=_actor_name(connection)
        )
    except HomeAssistantError as err:
        _send_store_error(connection, msg["id"], err)
        return
    connection.send_result(msg["id"], {"task": task})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "better_todo_list/reorder_sub_tasks",
        vol.Required("entry_id"): cv.string,
        vol.Required("task_id"): cv.string,
        vol.Required("sub_task_ids"): [cv.string],
    }
)
@websocket_api.async_response
async def handle_reorder_sub_tasks(hass, connection, msg):
    store = _get_store(hass, connection, msg["id"], msg["entry_id"])
    if store is None:
        return
    try:
        task = await store.async_reorder_sub_tasks(msg["task_id"], msg["sub_task_ids"])
    except HomeAssistantError as err:
        _send_store_error(connection, msg["id"], err)
        return
    connection.send_result(msg["id"], {"task": task})


def async_register_commands(hass: HomeAssistant) -> None:
    """Register every better_todo_list/* WebSocket command. Called once
    from __init__.py's async_setup()."""
    for handler in (
        handle_get_lists,
        handle_get_areas,
        handle_get_tasks,
        handle_create_task,
        handle_update_task,
        handle_delete_task,
        handle_complete_task,
        handle_reopen_task,
        handle_reorder_tasks,
        handle_get_task_history,
        handle_add_sub_task,
        handle_update_sub_task,
        handle_delete_sub_task,
        handle_reorder_sub_tasks,
    ):
        websocket_api.async_register_command(hass, handler)
