"""Diagnostics support for Better Todo List.

This wires into Home Assistant's built-in "Download Diagnostics" button:
Settings -> Devices & Services -> Better Todo List -> pick a list -> the
three-dot menu -> Download Diagnostics. That's the easiest way to hand me
a complete, structured snapshot of what a list looks like when something
isn't behaving as expected - just attach the downloaded JSON (or paste
its contents) to your message and I can see exactly what's stored.

The dump includes your task titles/notes/etc, since that IS the data
needed to debug a todo list. It stays on your machine as a downloaded
file unless you choose to share it - same as any other Home Assistant
diagnostics export.
"""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .store import BetterTodoListStore


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for one list (config entry)."""
    store: BetterTodoListStore | None = hass.data.get(DOMAIN, {}).get("stores", {}).get(
        entry.entry_id
    )
    return {
        "entry": {
            "entry_id": entry.entry_id,
            "title": entry.title,
            "data": dict(entry.data),
        },
        "store": store.as_diagnostics_dict() if store is not None else None,
    }
