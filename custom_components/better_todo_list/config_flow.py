"""Config flow for Better Todo List.

Home Assistant's config flow is the setup wizard under Settings -> Devices
& Services -> Add Integration. In this integration, **one config entry
represents one list** - so "adding the integration" a second time is how
you create a second list (e.g. "Kitchen Chores", "Weekly Errands", one
per bathroom, etc). There is no YAML configuration; everything happens
through this UI flow and the frontend card.

DEBUGGING TIP: if the "Add Integration" dialog doesn't show Better Todo
List, or the form errors out, check Settings -> System -> Logs and search
for "better_todo_list" - and see the README for how to turn on DEBUG
level logging for much more detail.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_LIST_NAME, DOMAIN, MAX_TITLE_LENGTH


class BetterTodoListConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle creation of a new Better Todo List entry (= one list)."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Only step: ask for a name for the new list."""
        errors: dict[str, str] = {}

        if user_input is not None:
            list_name = user_input[CONF_LIST_NAME].strip()

            if not list_name:
                errors["base"] = "name_required"
            elif len(list_name) > MAX_TITLE_LENGTH:
                errors["base"] = "name_too_long"
            elif self._list_name_taken(list_name):
                # Duplicate titles are technically legal in HA, but two
                # lists both called "Chores" would make the list picker in
                # the card confusing, so we guard against it here.
                errors["base"] = "name_exists"

            if not errors:
                return self.async_create_entry(
                    title=list_name, data={CONF_LIST_NAME: list_name}
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_LIST_NAME): str}),
            errors=errors,
        )

    def _list_name_taken(self, list_name: str) -> bool:
        return any(
            entry.title.strip().lower() == list_name.lower()
            for entry in self._async_current_entries()
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "BetterTodoListOptionsFlow":
        """Tell HA to use our options flow for the gear icon on this entry.

        No config_entry is passed to the constructor - HA sets
        `self.config_entry` on the returned instance automatically (see
        the note on BetterTodoListOptionsFlow).
        """
        return BetterTodoListOptionsFlow()


class BetterTodoListOptionsFlow(config_entries.OptionsFlow):
    """Lets you rename a list after creation via its entry's "Configure" button.

    Deliberately has no __init__: Home Assistant deprecated (and, as of
    HA 2025.12, removed) integrations setting `self.config_entry`
    themselves - the base OptionsFlow class now provides it automatically,
    so we just read `self.config_entry` directly. See
    https://developers.home-assistant.io/blog/2024/11/12/options-flow/
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            new_name = user_input[CONF_LIST_NAME].strip()
            if not new_name:
                errors["base"] = "name_required"
            elif len(new_name) > MAX_TITLE_LENGTH:
                errors["base"] = "name_too_long"
            else:
                # We store the list name as both the entry title (what you
                # see in Settings -> Devices & Services) and in entry.data
                # (what the rest of the integration reads), so update both.
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    title=new_name,
                    data={**self.config_entry.data, CONF_LIST_NAME: new_name},
                )
                # Returning an empty entry closes the options dialog. The
                # entry_update_listener registered in __init__.py reloads
                # the config entry so the todo.* entity picks up the new name.
                return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_LIST_NAME, default=self.config_entry.title
                    ): str
                }
            ),
            errors=errors,
        )
