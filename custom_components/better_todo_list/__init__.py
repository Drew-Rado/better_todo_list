"""The Better Todo List integration.

This file wires everything together:

  * `async_setup`       - runs ONCE, regardless of how many lists you've
                           created. Registers the WebSocket API, the
                           frontend card (as a resource HA loads
                           automatically - no manual "Add Resource" step),
                           the `better_todo_list.*` services, and a
                           listener that clears a task's Room whenever you
                           delete the underlying HA Area, so tasks never
                           point at a room that no longer exists.
  * `async_setup_entry`  - runs once PER LIST (each list is its own config
                           entry - see config_flow.py). Creates that
                           list's Store, loads its saved tasks from disk,
                           and forwards setup to the `todo` platform so it
                           also shows up as a native todo.* entity.

DEBUGGING TIP: nearly every function in this integration logs what it's
doing at DEBUG level. To see it: Settings -> System -> Logs -> "Load Full
Logs", or add this to configuration.yaml and restart:

    logger:
      logs:
        custom_components.better_todo_list: debug

You can also go to Settings -> Devices & Services -> Better Todo List ->
(three dots on a list) -> Download Diagnostics for a one-click data dump
you can send back for troubleshooting (see diagnostics.py).
"""
from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType

from . import websocket_api
from .const import (
    ATTR_AREA_ID,
    ATTR_DUE_DATE,
    ATTR_DUE_TIME,
    ATTR_NOTES,
    ATTR_PRIORITY,
    ATTR_TAGS,
    ATTR_TASK_ID,
    ATTR_TITLE,
    CARD_TAG,
    DOMAIN,
    FRONTEND_SCRIPT_URL,
    PLATFORMS,
    PRIORITIES,
    SERVICE_ADD_TASK,
    SERVICE_COMPLETE_TASK,
    SERVICE_REOPEN_TASK,
)
from .store import BetterTodoListStore

_LOGGER = logging.getLogger(__name__)

# hass.data[DOMAIN] layout:
# {
#     "stores": {entry_id: BetterTodoListStore, ...},
# }

_ATTR_ENTITY_ID = "entity_id"

_ADD_TASK_SCHEMA = vol.Schema(
    {
        vol.Required(_ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_TITLE): cv.string,
        vol.Optional(ATTR_NOTES): cv.string,
        vol.Optional(ATTR_DUE_DATE): cv.string,
        vol.Optional(ATTR_DUE_TIME): cv.string,
        vol.Optional(ATTR_PRIORITY): vol.In(PRIORITIES),
        vol.Optional(ATTR_TAGS): [cv.string],
        vol.Optional(ATTR_AREA_ID): cv.string,
    }
)
_TASK_ID_SCHEMA = vol.Schema(
    {
        vol.Required(_ATTR_ENTITY_ID): cv.entity_id,
        vol.Required(ATTR_TASK_ID): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """One-time setup shared by every list (config entry)."""
    hass.data.setdefault(DOMAIN, {"stores": {}})

    websocket_api.async_register_commands(hass)
    await _async_register_frontend(hass)
    _async_register_services(hass)
    _async_register_area_cleanup(hass)

    _LOGGER.debug("Better Todo List: one-time global setup complete")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one list (one config entry)."""
    store = BetterTodoListStore(hass, entry.entry_id)
    await store.async_load()
    hass.data[DOMAIN]["stores"][entry.entry_id] = store

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    _LOGGER.debug("Set up list '%s' (entry_id=%s)", entry.title, entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Tear down one list."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN]["stores"].pop(entry.entry_id, None)
        _LOGGER.debug("Unloaded list '%s' (entry_id=%s)", entry.title, entry.entry_id)
    return unload_ok


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when it's renamed via the options flow."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the card's JS file and tell Lovelace to auto-load it.

    This is what lets you add `type: custom:better-todo-list-card` to a
    dashboard without ever visiting Settings -> Dashboards -> Resources -
    the integration registers the resource for you.
    """
    frontend_dir = Path(__file__).parent
    js_path = str(frontend_dir / f"{CARD_TAG}.js")

    await hass.http.async_register_static_paths(
        [StaticPathConfig(FRONTEND_SCRIPT_URL, js_path, cache_headers=False)]
    )
    add_extra_js_url(hass, FRONTEND_SCRIPT_URL)
    _LOGGER.debug("Registered frontend card at %s -> %s", FRONTEND_SCRIPT_URL, js_path)


def _resolve_store(hass: HomeAssistant, entity_id: str) -> BetterTodoListStore:
    """Look up which list's Store a `todo.*` entity_id belongs to, for the
    services below - they target a list the same way built-in HA todo
    services do, via `entity_id`."""
    registry = er.async_get(hass)
    entity_entry = registry.async_get(entity_id)
    if entity_entry is None or entity_entry.config_entry_id is None:
        raise HomeAssistantError(f"'{entity_id}' is not a Better Todo List entity")
    store = hass.data[DOMAIN]["stores"].get(entity_entry.config_entry_id)
    if store is None:
        raise HomeAssistantError(f"The list for '{entity_id}' is not currently loaded")
    return store


def _async_register_services(hass: HomeAssistant) -> None:
    """Register the small set of services usable from automations/scripts.

    These only cover the basics (create/complete/reopen) - everything
    else (subtasks, recurrence, tags, room, editing) is meant to be done
    from the card, since those aren't things you'd typically want to
    trigger from an automation.
    """

    async def handle_add_task(call: ServiceCall) -> None:
        store = _resolve_store(hass, call.data[_ATTR_ENTITY_ID])
        await store.async_create_task(
            title=call.data[ATTR_TITLE],
            notes=call.data.get(ATTR_NOTES),
            due_date=call.data.get(ATTR_DUE_DATE),
            due_time=call.data.get(ATTR_DUE_TIME),
            priority=call.data.get(ATTR_PRIORITY),
            tags=call.data.get(ATTR_TAGS),
            area_id=call.data.get(ATTR_AREA_ID),
            actor="Automation/script",
        )

    async def handle_complete_task(call: ServiceCall) -> None:
        store = _resolve_store(hass, call.data[_ATTR_ENTITY_ID])
        await store.async_complete_task(call.data[ATTR_TASK_ID], actor="Automation/script")

    async def handle_reopen_task(call: ServiceCall) -> None:
        store = _resolve_store(hass, call.data[_ATTR_ENTITY_ID])
        await store.async_reopen_task(call.data[ATTR_TASK_ID], actor="Automation/script")

    hass.services.async_register(DOMAIN, SERVICE_ADD_TASK, handle_add_task, schema=_ADD_TASK_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_COMPLETE_TASK, handle_complete_task, schema=_TASK_ID_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REOPEN_TASK, handle_reopen_task, schema=_TASK_ID_SCHEMA
    )


def _async_register_area_cleanup(hass: HomeAssistant) -> None:
    """Null out a task's Room whenever the underlying HA Area is deleted.

    Without this, deleting an Area in Settings -> Areas would silently
    leave tasks pointing at an area_id that no longer exists anywhere.
    """

    @callback
    def _handle_area_registry_updated(event: Event) -> None:
        if event.data.get("action") != "remove":
            return
        area_id = event.data.get("area_id")
        if not area_id:
            return
        hass.async_create_task(_async_clear_area_everywhere(hass, area_id))

    hass.bus.async_listen(ar.EVENT_AREA_REGISTRY_UPDATED, _handle_area_registry_updated)


async def _async_clear_area_everywhere(hass: HomeAssistant, area_id: str) -> None:
    stores: dict[str, BetterTodoListStore] = hass.data[DOMAIN]["stores"]
    for store in stores.values():
        await store.async_clear_area_references(area_id)
