# Better Todo List

A lightweight, room-aware todo list for Home Assistant, installable through
[HACS](https://hacs.xyz/) as a custom repository. It's a smaller, focused
alternative to [home-tasks](https://github.com/L3t4l3s/home-tasks) - same
core idea (rich tasks with priority, tags, subtasks, recurrence, and a
custom Lovelace card), but without AI image generation, external provider
sync (CalDAV/Google Tasks/Todoist/Bring), or voice dictation.

## Features

- Any number of lists (each list = one Home Assistant integration entry)
- Each task supports:
  - Notes/description
  - Due date and due time
  - Priority: Low / Medium / High
  - Tags
  - Subtasks (a task can't be marked complete until all of its subtasks are)
  - Recurrence: fixed interval (every N hours/days/weeks/months/years),
    weekly (every N weeks on chosen weekdays), monthly (a specific day, or
    an Nth/last weekday), or yearly (an anniversary date) - each with a
    start date, an optional end date, and/or a maximum number of repetitions
  - A **Room**, using Home Assistant's built-in Areas (Settings -> Areas).
    This is how you can have a "Scrub toilet" task independently for each
    of 3 bathrooms - create one task per Area.
  - A full audit history: every field change, who changed it, and when
- Also shows up as a native `todo.*` entity per list, so it works with
  Home Assistant's built-in Todo card, Assist voice control, and the
  Companion App - on top of the richer custom card

## Repository layout

```
hacs.json                                    HACS metadata for this repo
custom_components/better_todo_list/
  manifest.json         Integration metadata (domain, dependencies, version)
  const.py              Every shared constant/field name in one place
  config_flow.py         The "Add Integration" setup wizard (one entry = one list)
  store.py               The data model + all task mutations (the source of truth)
  recurrence.py           Pure recurrence math - runnable standalone, see below
  history.py              Builds/appends audit-log entries
  todo.py                  Bridges each list to HA's native todo.* entity
  websocket_api.py         The custom API the frontend card talks to
  diagnostics.py           Powers the built-in "Download Diagnostics" button
  services.yaml + __init__.py   better_todo_list.add_task/complete_task/reopen_task
  better-todo-list-card.js   The Lovelace card (no build step - plain JS)
```

Every file starts with a comment explaining its role, and most functions
have a short comment on *why* they're written the way they are - so if
something breaks, reading the relevant file top-to-bottom should make
sense even without prior Home Assistant integration experience.

## Installing

### Option A: HACS custom repository

1. Push this folder to your own GitHub repository.
2. In Home Assistant: HACS -> the three-dot menu (top right) -> Custom
   repositories -> paste your repo URL -> category "Integration".
3. Find "Better Todo List" in HACS and install it.
4. Restart Home Assistant.

### Option B: Manual install

1. Copy `custom_components/better_todo_list/` into your Home Assistant
   config's `custom_components/` folder (so you end up with
   `config/custom_components/better_todo_list/...`).
2. Restart Home Assistant.

### After installing (either option)

1. Settings -> Devices & Services -> Add Integration -> search "Better
   Todo List" -> give your first list a name (e.g. "Kitchen Chores").
   Repeat for each list you want.
2. Edit a dashboard -> Add Card -> search "Better Todo List" (or add a
   manual card, see below). The card's JS is registered automatically -
   there's no separate "Add Resource" step.

## Card configuration

```yaml
type: custom:better-todo-list-card
title: My Household Tasks    # optional card header
list_name: Kitchen Chores    # optional - omit to show every list at once
group_by: room                # "room" (default), "list", or "none"
show_completed: false           # optional, default false
```

`list_name` must exactly match the name you gave the list when you added
the integration (Settings -> Devices & Services -> Better Todo List).

## Rooms

Rooms use Home Assistant's built-in Areas (Settings -> Areas), not a
separate list you maintain inside this integration. Create your Areas
there first (e.g. "Master Bathroom", "Guest Bathroom"), then pick a Room
per task in the card's task editor. If you delete an Area in Home
Assistant later, this integration automatically clears that Room from any
tasks that referenced it (see the area-registry listener in `__init__.py`)
so nothing is left pointing at a room that no longer exists.

## Automations

Three services are available for automations/scripts (Developer Tools ->
Actions), documented in-app via `services.yaml`:

- `better_todo_list.add_task`
- `better_todo_list.complete_task`
- `better_todo_list.reopen_task`

Each targets a list via its `todo.*` entity. Everything else (subtasks,
recurrence editing, tags, room, editing existing fields) is meant to be
done from the card - it's not typically something you'd want to trigger
from an automation.

## Known limitations (by design, to keep this lightweight)

- No AI-generated content, image attachments, or external provider sync
  (CalDAV/Google Tasks/Todoist/Bring) - the whole point of this project.
- No manual drag-and-drop reordering in the card (tasks sort automatically
  by priority/due date instead).
- No live multi-client sync: if you have the same dashboard open on two
  devices, changes made on one won't appear on the other until you refresh
  the card (the refresh button in the toolbar) or reload the page.
- Home Assistant's native `todo.*` entity schema has no room for priority,
  tags, room, subtasks, or recurrence - those fields only show up in the
  custom card, not in HA's built-in Todo card or Assist voice responses.

## Debugging

If something isn't working, here are three ways to get more information -
feel free to paste any of this back when asking for help:

1. **Debug logs.** Add this to `configuration.yaml` and restart (or use
   Settings -> System -> Logs -> the "Load Full Logs" / logger UI):

   ```yaml
   logger:
     logs:
       custom_components.better_todo_list: debug
   ```

   Nearly every function in this integration logs what it's doing at
   DEBUG level - task creation/updates, recurrence calculations, incoming
   WebSocket commands, and Area cleanup.

2. **Diagnostics download.** Settings -> Devices & Services -> Better Todo
   List -> pick a list -> three-dot menu -> Download Diagnostics. This
   gives you a JSON file with that list's exact stored data - the fastest
   way to show me what a task actually looks like versus what you expect.

3. **Browser DevTools.** With the dashboard open, press F12 -> Network ->
   filter to "WS" -> click the live WebSocket connection -> you'll see
   every `better_todo_list/*` command the card sends and the response it
   gets back. The Console tab will show any JavaScript errors from the
   card itself.

## Developing

`recurrence.py` has zero Home Assistant dependencies and a small built-in
self-test, so you can sanity-check recurrence math changes without a
running Home Assistant instance:

```
python custom_components/better_todo_list/recurrence.py
```
