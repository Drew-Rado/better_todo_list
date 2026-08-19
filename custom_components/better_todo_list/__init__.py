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
from homeassistant.loader import async_get_integration

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

    WHY cache_headers=True + a "?v=<version>" suffix: this used to be
    cache_headers=False (no caching at all), which sounded safer but
    actually caused the "Custom element not found: better-todo-list-card"
    error intermittently - with caching disabled, *every* dashboard load
    (not just the first one ever) has to make a fresh network request for
    this file before the card can register itself, and if Lovelace tries
    to render the card before that request finishes (more likely on a
    slower/mobile connection), it gives up with that error instead of
    waiting. Letting the browser cache the file normally means only the
    very first load after each update pays that cost. Tagging the URL with
    the integration's version means an update still forces a fresh fetch
    automatically - no more manual hard-refreshing needed after upgrading.
    """
    frontend_dir = Path(__file__).parent
    js_path = str(frontend_dir / f"{CARD_TAG}.js")

    await hass.http.async_register_static_paths(
        [StaticPathConfig(FRONTEND_SCRIPT_URL, js_path, cache_headers=True)]
    )

    integration = await async_get_integration(hass, DOMAIN)
    versioned_url = f"{FRONTEND_SCRIPT_URL}?v={integration.version}"
    add_extra_js_url(hass, versioned_url)
    await _async_register_lovelace_resource(hass, versioned_url)
    _LOGGER.debug("Registered frontend card at %s -> %s", versioned_url, js_path)


async def _async_register_lovelace_resource(hass: HomeAssistant, versioned_url: str) -> None:
    """Best-effort: ALSO register as a proper Lovelace "resource" (the kind
    listed under Settings -> Dashboards -> Resources), on top of the
    add_extra_js_url call above.

    WHY: add_extra_js_url works everywhere (including YAML-mode dashboards)
    but doesn't make Lovelace *wait* for the script before it starts
    rendering cards - which can lose a race against Home Assistant's
    hardcoded ~2 second "give up waiting for a custom element to register"
    timeout, especially with the 2026.6+ "Add Card" dialog's live preview
    thumbnails (which try to render every matching card, including ours,
    the moment you search). A properly registered resource IS awaited by
    Lovelace's own dashboard bootstrap, closing that race by construction
    instead of just hoping the network fetch finishes in time.

    This only works for storage-mode dashboards (the default) - YAML-mode
    dashboards manage their own resources list and can't be written to at
    runtime, so we just skip for those; add_extra_js_url above is already
    sufficient there (the card still loads, it just can't win that
    specific race in the picker's live preview). Any failure here is
    caught and logged, never raised - this is purely additive, and
    add_extra_js_url is what this integration has always relied on, so
    nothing breaks if this fails for an unforeseen reason.

    SAFETY NOTE: writing to this collection before it has loaded its
    existing data from disk used to be able to silently wipe out every
    OTHER resource you had registered (Home Assistant core issue #165767) -
    fixed in core PR #165773. manifest.json requires Home Assistant
    2026.6.0+ specifically so that fix is guaranteed present; we also
    explicitly call async_get_info() first below (which forces the load)
    as defense in depth, on top of the fix itself.

    This writes into the same storage Settings -> Dashboards -> Resources
    reads and edits - if you ever want to inspect or remove this entry by
    hand, that's where to look for it.
    """
    lovelace_data = hass.data.get("lovelace")
    if lovelace_data is None or getattr(lovelace_data, "resource_mode", None) != "storage":
        _LOGGER.debug("Skipping Lovelace resource registration (not in storage mode)")
        return

    resources = lovelace_data.resources
    try:
        await resources.async_get_info()  # forces existing resources to load first - see SAFETY NOTE above

        existing = [item for item in resources.async_items() if item["url"].startswith(FRONTEND_SCRIPT_URL)]
        for item in existing:
            if item["url"] == versioned_url:
                return  # already correctly registered, nothing to do
            await resources.async_delete_item(item["id"])

        await resources.async_create_item({"res_type": "module", "url": versioned_url})
        _LOGGER.debug("Registered %s as a Lovelace resource", versioned_url)
    except Exception as err:  # noqa: BLE001 - best-effort only, must never break setup
        _LOGGER.debug(
            "Could not register as a Lovelace resource (non-fatal, add_extra_js_url still covers it): %s", err
        )


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
