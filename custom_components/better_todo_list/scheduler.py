"""Background jobs for Better Todo List.

Two timers, both set up once from __init__.py's async_setup():

  * Just after midnight (and once per list at startup, to catch up on
    anything missed while Home Assistant was off): start the next cycle of
    every repeating task whose next cycle has appeared - see
    store.async_advance_cycles().
  * Every minute: send any due reminders. A task with a reminder, a due
    date, and at least one person in "Assigned to" notifies those people's
    phones (the Home Assistant Companion App) once per due date. Every
    reminder due in the same minute for the same person is combined into
    one notification, so a list of 30 chores all due Sunday sends one
    notification, not 30.

A reminder reaches a person through every Companion App device logged in
as that person's Home Assistant user (person -> user -> mobile_app
devices). People without a linked user or without the app just don't get
push notifications - a warning is logged - but every reminder also fires a
`better_todo_list_reminder` event you can use in your own automations
(e.g. to announce it on a speaker instead).

Single-task notifications get a "Mark done" button; tapping it completes
the task (see _async_handle_notification_action).

DEBUGGING TIP: turn on debug logging (see the README) to see each reminder
as it's sent, and which notify services it went to.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
import logging
from typing import Any

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .const import (
    CONF_REMINDER_TIME,
    DEFAULT_REMINDER_TIME,
    DOMAIN,
    EVENT_REMINDER,
    NOTIFICATION_ACTION_COMPLETE,
    REMINDER_GRACE_MINUTES,
    STATUS_COMPLETED,
    STATUS_NEEDS_ACTION,
)
from .store import BetterTodoListStore, is_waiting

_LOGGER = logging.getLogger(__name__)

_MOBILE_APP_DOMAIN = "mobile_app"
_EVENT_NOTIFICATION_ACTION = "mobile_app_notification_action"
_MAX_LINES_PER_NOTIFICATION = 8


# --- When is a reminder due? (pure functions) ------------------------------------


def reminder_key(task: dict[str, Any]) -> str:
    """Identifies "this reminder for this due date". Stored in the task's
    reminder_sent_for once sent; changing the due date, due time, or
    reminder setting (or a new cycle starting) makes it due again."""
    return f"{task.get('due_date')}|{task.get('due_time') or ''}|{task.get('reminder')}"


def reminder_fire_time(task: dict[str, Any], reminder_time: time, tz: Any) -> datetime | None:
    """When this task's reminder should go out, or None if it has none.

    A task with only a due date counts as due at `reminder_time` on that
    date. A repeating task's reminder never fires before its cycle
    appeared (it waits until `reminder_time` that day instead) - so a
    weekly chore that appears Monday and is due Sunday with a "1 week
    before" reminder notifies Monday morning, not at midnight.
    """
    if task.get("reminder") is None or not task.get("due_date"):
        return None
    due_time = time.fromisoformat(task["due_time"]) if task.get("due_time") else reminder_time
    due_moment = datetime.combine(date.fromisoformat(task["due_date"]), due_time, tz)
    fire = due_moment - timedelta(minutes=int(task["reminder"]))
    if task.get("cycle_start"):
        appeared = datetime.combine(date.fromisoformat(task["cycle_start"]), reminder_time, tz)
        fire = max(fire, min(appeared, due_moment))
    return fire


def reminder_is_due(task: dict[str, Any], now: datetime, reminder_time: time) -> bool:
    if (
        task["status"] != STATUS_NEEDS_ACTION
        or not task.get("assignees")
        or is_waiting(task)
        or task.get("reminder_sent_for") == reminder_key(task)
    ):
        return False
    fire = reminder_fire_time(task, reminder_time, now.tzinfo)
    return fire is not None and fire <= now < fire + timedelta(minutes=REMINDER_GRACE_MINUTES)


def describe_due(task: dict[str, Any], today: date) -> str:
    """"today", "tomorrow at 18:00", "Sun", "Oct 20" - short, for a notification."""
    due = date.fromisoformat(task["due_date"])
    days = (due - today).days
    if days < 0:
        label = "overdue"
    elif days == 0:
        label = "today"
    elif days == 1:
        label = "tomorrow"
    elif days < 7:
        label = due.strftime("%A")
    else:
        label = f"{due.strftime('%b')} {due.day}"
    if task.get("due_time") and days >= 0:
        label += f" at {task['due_time']}"
    return label


# --- Who gets notified, and how --------------------------------------------------


def _person_name(hass: HomeAssistant, person_entity_id: str) -> str:
    state = hass.states.get(person_entity_id)
    return state.name if state else person_entity_id


def notify_services_for_person(hass: HomeAssistant, person_entity_id: str) -> list[str]:
    """The notify.mobile_app_* services of every Companion App device
    logged in as this person's Home Assistant user."""
    state = hass.states.get(person_entity_id)
    user_id = state.attributes.get("user_id") if state else None
    if not user_id:
        return []
    services = []
    for entry in hass.config_entries.async_entries(_MOBILE_APP_DOMAIN):
        if entry.data.get("user_id") != user_id or not entry.data.get("device_name"):
            continue
        service = slugify(f"mobile_app_{entry.data['device_name']}")
        if hass.services.has_service("notify", service):
            services.append(service)
    return services


def _notification_for(
    items: list[tuple[str, dict[str, Any]]], today: date
) -> dict[str, Any]:
    """Build one notification (title, message, data) for one person's
    reminders. `items` is [(entry_id, task), ...]."""
    if len(items) == 1:
        entry_id, task = items[0]
        payload: dict[str, Any] = {
            "title": task["title"],
            "message": f"Due {describe_due(task, today)}",
        }
        if all(s["status"] == STATUS_COMPLETED for s in task.get("sub_tasks", [])):
            payload["data"] = {
                "actions": [
                    {
                        "action": f"{NOTIFICATION_ACTION_COMPLETE}|{entry_id}|{task['id']}",
                        "title": "Mark done",
                    }
                ]
            }
        return payload

    ordered = sorted(items, key=lambda item: (item[1]["due_date"], item[1].get("due_time") or "", item[1]["title"]))
    lines = [f"• {task['title']} ({describe_due(task, today)})" for _, task in ordered[:_MAX_LINES_PER_NOTIFICATION]]
    if len(ordered) > _MAX_LINES_PER_NOTIFICATION:
        lines.append(f"…and {len(ordered) - _MAX_LINES_PER_NOTIFICATION} more")
    return {"title": f"{len(items)} tasks due soon", "message": "\n".join(lines)}


# --- The timers ------------------------------------------------------------------


def _stores(hass: HomeAssistant) -> dict[str, BetterTodoListStore]:
    return hass.data.get(DOMAIN, {}).get("stores", {})


def _reminder_time_for(hass: HomeAssistant, entry_id: str) -> time:
    entry = hass.config_entries.async_get_entry(entry_id)
    value = entry.options.get(CONF_REMINDER_TIME) if entry else None
    try:
        return time.fromisoformat(value or DEFAULT_REMINDER_TIME)
    except ValueError:
        return time.fromisoformat(DEFAULT_REMINDER_TIME)


async def async_advance_all_cycles(hass: HomeAssistant) -> None:
    for store in list(_stores(hass).values()):
        await store.async_advance_cycles()


async def async_send_due_reminders(hass: HomeAssistant, now: datetime | None = None) -> None:
    """Send every reminder that's due right now (see module docstring)."""
    now = now or dt_util.now()
    today = now.date()

    per_person: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    due_tasks: list[tuple[BetterTodoListStore, dict[str, Any]]] = []
    for entry_id, store in list(_stores(hass).items()):
        reminder_time = _reminder_time_for(hass, entry_id)
        for task in store.tasks:
            if not reminder_is_due(task, now, reminder_time):
                continue
            due_tasks.append((store, task))
            for person in task["assignees"]:
                per_person.setdefault(person, []).append((entry_id, task))

    if not due_tasks:
        return

    for person, items in per_person.items():
        services = notify_services_for_person(hass, person)
        if not services:
            _LOGGER.warning(
                "Can't send a task reminder to %s: no Companion App device found for them "
                "(the person needs a linked Home Assistant user, logged in on the app)",
                person,
            )
            continue
        payload = _notification_for(items, today)
        for service in services:
            try:
                await hass.services.async_call("notify", service, payload, blocking=True)
            except HomeAssistantError as err:
                _LOGGER.warning("Sending a task reminder via notify.%s failed: %s", service, err)
            else:
                _LOGGER.debug("Sent reminder for %d task(s) to %s via notify.%s", len(items), person, service)

    sent_by_store: dict[BetterTodoListStore, dict[str, tuple[str, list[str]]]] = {}
    for store, task in due_tasks:
        names = [_person_name(hass, p) for p in task["assignees"]]
        sent_by_store.setdefault(store, {})[task["id"]] = (reminder_key(task), names)
        hass.bus.async_fire(
            EVENT_REMINDER,
            {
                "entry_id": store.entry_id,
                "task_id": task["id"],
                "title": task["title"],
                "due_date": task["due_date"],
                "due_time": task.get("due_time"),
                "assignees": list(task["assignees"]),
            },
        )
    for store, sent in sent_by_store.items():
        await store.async_mark_reminded(sent)


async def _async_handle_notification_action(hass: HomeAssistant, event: Event) -> None:
    """The "Mark done" button on a reminder notification."""
    action = str(event.data.get("action", ""))
    prefix, _, rest = action.partition("|")
    if prefix != NOTIFICATION_ACTION_COMPLETE:
        return
    entry_id, _, task_id = rest.partition("|")
    store = _stores(hass).get(entry_id)
    if store is None:
        _LOGGER.warning("'Mark done' tapped for a task in a list that no longer exists")
        return

    actor = "Notification"
    if event.context.user_id and (user := await hass.auth.async_get_user(event.context.user_id)):
        actor = f"{user.name} (notification)"
    try:
        task = store.get_task(task_id)
        if task["status"] != STATUS_COMPLETED:
            await store.async_complete_task(task_id, actor=actor)
    except HomeAssistantError as err:
        _LOGGER.warning("Could not complete a task from its notification: %s", err)


@callback
def async_setup_scheduler(hass: HomeAssistant) -> None:
    """Start the midnight and every-minute timers, and listen for "Mark
    done" taps. Called once from __init__.py's async_setup()."""

    async def _midnight(now: datetime) -> None:
        await async_advance_all_cycles(hass)

    async def _every_minute(now: datetime) -> None:
        await async_send_due_reminders(hass)

    async def _notification_action(event: Event) -> None:
        await _async_handle_notification_action(hass, event)

    cancels = [
        async_track_time_change(hass, _midnight, hour=0, minute=0, second=5),
        async_track_time_change(hass, _every_minute, second=10),
        hass.bus.async_listen(_EVENT_NOTIFICATION_ACTION, _notification_action),
    ]

    @callback
    def _stop(event: Event) -> None:
        for cancel in cancels:
            cancel()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop)
