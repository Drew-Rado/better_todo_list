# Better Todo List

A lightweight, room-aware todo list for Home Assistant, installable through [HACS](https://hacs.xyz/) as a custom repository. It's a smaller, focused alternative to [home-tasks](https://github.com/L3t4l3s/home-tasks) with the same goal of being "better" than Home Assistant's built-in todo lists.  Its the same core idea as [home-tasks](https://github.com/L3t4l3s/home-tasks) by [**L3t4l3s**](https://github.com/L3t4l3s) (rich tasks with priority, tags, subtasks, recurrence, and a custom Lovelace card), but without AI image generation, external provider sync (CalDAV/Google Tasks/Todoist/Bring), or voice dictation as I didnt need those and wanted a slightly different room-based UI.

## Features

- Any number of lists (each list = one Home Assistant integration entry)
- Each task supports:
  - Notes/description
  - Due date and due time
  - Priority: Low / Medium / High
  - Level of effort: Low / Medium / High
  - Tags
  - Subtasks (a task can't be marked complete until all of its subtasks are)
  - Repeating: fixed interval (every N hours/days/weeks/months/years), weekly (every N weeks on chosen weekdays), monthly (a specific day, or an Nth/last weekday), or yearly (an anniversary date) - each with a start date, an optional end date, and/or a maximum number of repetitions, plus a separate **due rule** (see "Repeating tasks" below)
  - A **Room**, using Home Assistant's built-in Areas (Settings -> Areas). This is how you can have a "Scrub toilet" task independently for each of 3 bathrooms - create one task per Area.
  - **Assigned to**: one or more people (Settings -> People), who get the task's reminders as push notifications
  - A **reminder**: when it's due, or 1/3 hours, 1/2 days, or 1 week before
  - A full audit history: every field change, who changed it, and when
- Group by room, floor, list, priority, or effort; sort by floor, priority, effort, due date, tag, or title; and filter by any combination of priority, effort, floor, room, person, tag, and due date
- Also shows up as a native `todo.*` entity per list, so it works with Home Assistant's built-in Todo card, Assist voice control, and the Companion App - on top of the richer custom card
- Live multi-client sync: if two household members have the same dashboard open at once, a change made on one shows up on the other automatically, no manual refresh needed

## Repository layout

```
hacs.json                                    HACS metadata for this repo
custom_components/better_todo_list/
  manifest.json         Integration metadata (domain, dependencies, version)
  const.py              Every shared constant/field name in one place
  config_flow.py         The "Add Integration" setup wizard (one entry = one list)
  store.py               The data model + all task mutations (the source of truth)
  recurrence.py           Pure recurrence math - runnable standalone, see below
  scheduler.py            Midnight cycle rollover + reminder notifications
  history.py              Builds/appends audit-log entries
  todo.py                  Bridges each list to HA's native todo.* entity
  websocket_api.py         The custom API the frontend card talks to
  diagnostics.py           Powers the built-in "Download Diagnostics" button
  services.yaml + __init__.py   better_todo_list.add_task/complete_task/reopen_task
  better-todo-list-card.js   The Lovelace card (no build step - plain JS)
```

Every file starts with a comment explaining its role, and most functions have a short comment on *why* they're written the way they are - so if something breaks, reading the relevant file top-to-bottom should make sense even without prior Home Assistant integration experience.

## Installing

### Option A: HACS custom repository

1. Push this folder to your own GitHub repository.
2. In Home Assistant: HACS -> the three-dot menu (top right) -> Custom repositories -> paste your repo URL -> category "Integration".
3. Find "Better Todo List" in HACS and install it.
4. Restart Home Assistant.

### Option B: Manual install

1. Copy `custom_components/better_todo_list/` into your Home Assistant config's `custom_components/` folder (so you end up with `config/custom_components/better_todo_list/...`).
2. Restart Home Assistant.

### After installing (either option)

1. Settings -> Devices & Services -> Add Integration -> search "Better Todo List" -> give your first list a name (e.g. "Kitchen Chores"). Repeat for each list you want.
2. Edit a dashboard -> Add Card -> **Manual** -> paste the YAML below (see "Card configuration"). The card's JS is registered automatically - there's no separate "Add Resource" step.

Searching "Better Todo List" in the "By card" tab instead of using Manual will very likely get stuck on a permanent loading spinner - as of Home Assistant 2026.6, that search tab tries to render a live preview thumbnail of every matching card, and that particular preview mechanism doesn't reliably work for custom cards that load over the network (confirmed via extensive testing: our card's script loads and registers correctly, and it renders perfectly in the Manual/YAML config editor's own preview - just not in that specific search grid). This looks like a rough edge in that (two-month-old) HA core feature itself, not anything wrong with the card, but there's no known fix from the integration side - use Manual, it's the same card either way.

## Card configuration

```yaml
type: custom:better-todo-list-card
title: My Household Tasks    # optional card header
list_name: Kitchen Chores    # optional - omit to show every list at once
group_by: room                # "room" (default), "floor", "list", "priority", "effort", or "none"
sort_by: floor                # "default" (priority, then due date), "floor", "priority", "effort", "due", "tag", or "title"
sort_reverse: false             # optional, default false
show_completed: false           # optional, default false
filters:                        # optional starting filters - e.g. quick wins:
  effort: [low]
  priority: [high, medium]
```

`list_name` must exactly match the name you gave the list when you added the integration (Settings -> Devices & Services -> Better Todo List).

Everything except `title` and `list_name` can also be changed from the card's toolbar, and the card remembers your last choices in that browser. Changing these options in the YAML resets that remembered view, so your edits take effect.

## Grouping, sorting, and filtering

- **Group by** picks the section headings. **Sort** orders the tasks inside each section, and the ⇅ button reverses it. Tasks with no value for the sort (e.g. no priority) always go last.
- Sorting never breaks a grouping apart. When the sort is about the same thing as the grouping, it orders the sections too. **Group by room + Sort by floor** adds floor headings, with each floor's rooms (and their tasks) underneath.
- Floors and rooms appear in the order Home Assistant shows them under Settings -> Areas, labels & zones, which is top floor first unless you've rearranged them there. Assign each Area to a Floor there for floor sorting and grouping to work.
- **Filter** opens a panel of chips. Within a row, any selected chip matches. Across rows, every row with a selection must match. So Effort: Low + Priority: High, Medium shows low-effort tasks that are high *or* medium priority.
- **Tag** sorting is alphabetical, by each task's alphabetically-first tag.

## Repeating tasks

A repeating task has two separate settings:

- **The repeat pattern** (e.g. weekly on Monday, or monthly on the 1st) decides when it **appears** on the list.
- **Due** decides when each appearance is due: the day it appears, the day before it repeats, the last day of the month, a chosen weekday, or N days after it appears.

So "weekly on Monday, due the day before it repeats" appears every Monday and is due that Sunday. "Monthly on the 1st, due the last day of the month" populates your monthly chores on the 1st and gives you the whole month.

- **Checking a repeating task off** hides it until its next appearance, so a long weekly list shrinks as you work through it.
- **When the next appearance arrives** (just after midnight), the task comes back with its new due date and its subtasks unchecked.
- **If it wasn't done by then**, it's reset anyway and its history records the missed cycle.
- **Before its first appearance**, a task is hidden too. Turn on **Show completed** to see (and edit) hidden repeating tasks; each shows when it reappears.
- **The task editor previews** the next few appear/due dates as you change the settings. If a new task wouldn't appear until next week or month, it offers to start the current cycle right away (e.g. a weekly-Monday chore set up on a Tuesday).

Tasks that repeat **every N hours** work as before: completing one reschedules it immediately, with no due rule.

**Upgrading from 0.3.x:** existing repeating tasks keep their dates with a "due the day it appears" rule. Ones you'd already completed for this cycle are hidden until their next date. To change the due rule for a whole list at once, run the `better_todo_list.set_due_rule` action (Developer Tools -> Actions) instead of editing every task. For example, pick your Weekly Chores list and "The day before it repeats".

## Reminders

Pick people under **Assigned to** and a **Reminder** time in the task editor. When the reminder is due, each assigned person gets a push notification on every phone where they're logged in to the Home Assistant Companion App.

- Reminders due at the same time for the same person are combined into one notification (e.g. "3 tasks due soon"), so a big chore list doesn't send a flood of them.
- A single-task notification has a **Mark done** button.
- Tasks with a due date but no due time are treated as due at the list's **reminder time** (default 9:00). Change it under Settings -> Devices & Services -> Better Todo List -> Configure. So "1 day before" on a task due Sunday arrives Saturday at 9:00.
- A repeating task's reminder never arrives before that cycle appears. A "1 week before" reminder on a chore that appears Monday and is due Sunday arrives Monday at the reminder time.
- A person only gets push notifications if they have a Home Assistant user (Settings -> People -> the person -> "Allow person to login") and are logged in on the Companion App. The task editor marks people without the app.
- Every reminder also fires a `better_todo_list_reminder` event (with the task's title, due date, and assignees) for your own automations, e.g. to announce it on a speaker.

## Rooms

Rooms use Home Assistant's built-in Areas (Settings -> Areas), not a separate list you maintain inside this integration. Create your Areas there first (e.g. "Master Bathroom", "Guest Bathroom"), then pick a Room per task in the card's task editor. If you delete an Area in Home Assistant later, this integration automatically clears that Room from any tasks that referenced it (see the area-registry listener in `__init__.py`) so nothing is left pointing at a room that no longer exists. Deleting a person likewise removes them from every task's "Assigned to".

## Automations

Four services are available for automations/scripts (Developer Tools -> Actions), documented in-app via `services.yaml`:

- `better_todo_list.add_task`
- `better_todo_list.complete_task`
- `better_todo_list.reopen_task`
- `better_todo_list.set_due_rule` - change the due rule of every repeating task in a list at once

Each targets a list via its `todo.*` entity. Everything else (subtasks, recurrence editing, tags, room, editing existing fields) is meant to be done from the card - it's not typically something you'd want to trigger from an automation.

## Known limitations (by design, to keep this lightweight)

- No AI-generated content, image attachments, or external provider sync (CalDAV/Google Tasks/Todoist/Bring) - the whole point of this project its just extra bloat I wasn't using.
- No manual drag-and-drop reordering in the card (tasks sort automatically by the chosen sort instead).
- Home Assistant's native `todo.*` entity schema has no room for priority, effort, tags, room, assignees, subtasks, or recurrence - those fields only show up in the custom card, not in HA's built-in Todo card or Assist voice responses. Repeating tasks waiting for their next cycle are left out of the native entity entirely, so its "clear completed" button can't delete them.
- If you fully remove this integration, it leaves behind one entry in Settings -> Dashboards -> Resources (pointing at the card's JS file). It's harmless if left in place - the worst case is a broken URL that nothing references - but you can delete it by hand from that same page if you want it gone.

## Debugging

If something isn't working, here are three ways to get more information - feel free to paste any of this back when asking for help:

1. **Debug logs.** Add this to `configuration.yaml` and restart (or use Settings -> System -> Logs -> the "Load Full Logs" / logger UI):

   ```yaml
   logger:
     logs:
       custom_components.better_todo_list: debug
   ```

   Nearly every function in this integration logs what it's doing at DEBUG level - task creation/updates, recurrence calculations, new cycles starting, reminders sent (and which notify services they went to), incoming WebSocket commands, and Area cleanup.

2. **Diagnostics download.** Settings -> Devices & Services -> Better Todo List -> pick a list -> three-dot menu -> Download Diagnostics. This gives you a JSON file with that list's exact stored data - the fastest way to show me what a task actually looks like versus what you expect.

3. **Browser DevTools.** With the dashboard open, press F12 -> Network -> filter to "WS" -> click the live WebSocket connection -> you'll see every `better_todo_list/*` command the card sends and the response it gets back. The Console tab will show any JavaScript errors from the card itself.

## Developing

`recurrence.py` has zero Home Assistant dependencies and a small built-in self-test, so you can sanity-check recurrence math changes without a running Home Assistant instance:

```
python custom_components/better_todo_list/recurrence.py
```
