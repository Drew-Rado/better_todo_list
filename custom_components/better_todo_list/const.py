"""Shared constants for the Better Todo List integration.

Every "magic string" used by more than one file lives here - the config
flow, the data store, the WebSocket API, the services, and the frontend
card all need to agree on field names. If you ever want to add a new task
field, this is the first file to touch, followed by store.py.
"""

DOMAIN = "better_todo_list"

# --- Config entry / options flow keys ---------------------------------------------
# Each config entry represents exactly one list (see config_flow.py).
CONF_LIST_NAME = "list_name"
# Time of day ("HH:MM") reminders fire for tasks that have a due date but no
# due time - set per list in its options (the "Configure" button).
CONF_REMINDER_TIME = "reminder_time"
DEFAULT_REMINDER_TIME = "09:00"

# --- Storage --------------------------------------------------------------------
# Bumping STORAGE_VERSION makes store.py's _TaskStore._async_migrate_func()
# upgrade old data on load. Version history:
#   1 - original schema
#   2 - effort, assignees, reminders, and the appear/due cycle fields
#       (cycle_start / next_cycle_start, recurrence.due_rule)
STORAGE_VERSION = 2
STORAGE_KEY_PREFIX = DOMAIN

# --- Task status ------------------------------------------------------------------
STATUS_NEEDS_ACTION = "needs_action"
STATUS_COMPLETED = "completed"

# --- Priority -----------------------------------------------------------------
PRIORITY_LOW = "low"
PRIORITY_MEDIUM = "medium"
PRIORITY_HIGH = "high"
PRIORITIES = (PRIORITY_LOW, PRIORITY_MEDIUM, PRIORITY_HIGH)

# --- Level of effort (same three values as priority, but a separate field) ----------
EFFORTS = (PRIORITY_LOW, PRIORITY_MEDIUM, PRIORITY_HIGH)

# --- Recurrence types --------------------------------------------------------------
RECURRENCE_INTERVAL = "interval"
RECURRENCE_WEEKLY = "weekly"
RECURRENCE_MONTHLY_DAY = "monthly_day"
RECURRENCE_MONTHLY_WEEKDAY = "monthly_weekday"
RECURRENCE_YEARLY = "yearly"
RECURRENCE_TYPES = (
    RECURRENCE_INTERVAL,
    RECURRENCE_WEEKLY,
    RECURRENCE_MONTHLY_DAY,
    RECURRENCE_MONTHLY_WEEKDAY,
    RECURRENCE_YEARLY,
)

# Units usable with RECURRENCE_INTERVAL ("every N <unit>").
INTERVAL_UNITS = ("hours", "days", "weeks", "months", "years")

# "1st"..."4th" week of the month, or "last" (e.g. "last Wednesday").
NTH_WEEK_VALUES = ("1", "2", "3", "4", "last")

# Recurrence end conditions - a recurrence can be open-ended, end on a
# specific date, or end after a maximum number of completions.
RECURRENCE_END_NONE = "none"
RECURRENCE_END_DATE = "date"
RECURRENCE_END_COUNT = "count"
RECURRENCE_END_TYPES = (RECURRENCE_END_NONE, RECURRENCE_END_DATE, RECURRENCE_END_COUNT)

# When a repeating task is due, relative to the day it appears on the list.
# The repeat pattern above decides when each cycle *appears*; the due rule
# decides that cycle's due date (see recurrence.due_date_for_cycle).
DUE_RULE_SAME_DAY = "same_day"          # due the day it appears
DUE_RULE_DAYS_AFTER = "days_after"      # due N days after it appears ("due_days")
DUE_RULE_WEEKDAY = "weekday"            # due on the next given weekday ("due_weekday")
DUE_RULE_END_OF_MONTH = "end_of_month"  # due the last day of the month it appears in
DUE_RULE_BEFORE_NEXT = "before_next"    # due the day before the next cycle appears
DUE_RULES = (
    DUE_RULE_SAME_DAY,
    DUE_RULE_DAYS_AFTER,
    DUE_RULE_WEEKDAY,
    DUE_RULE_END_OF_MONTH,
    DUE_RULE_BEFORE_NEXT,
)

# --- Reminders -------------------------------------------------------------------
# A task's "reminder" is how many minutes before it's due to notify its
# assignees (0 = at the due time). Tasks with only a due date (no time) are
# treated as due at the list's CONF_REMINDER_TIME for this purpose.
REMINDER_OFFSETS_MINUTES = (0, 60, 180, 1440, 2880, 10080)
# A reminder that couldn't be sent on time (e.g. Home Assistant was
# restarting) is still sent if it's at most this late; older ones are
# dropped rather than arriving hours after the fact.
REMINDER_GRACE_MINUTES = 120

EVENT_REMINDER = f"{DOMAIN}_reminder"
# Prefix of the "Mark done" button identifier on reminder notifications;
# the full identifier is f"{prefix}|{entry_id}|{task_id}".
NOTIFICATION_ACTION_COMPLETE = "BETTER_TODO_LIST_DONE"

# --- History / audit log -----------------------------------------------------------
# Oldest entries are dropped once a task's history exceeds this length, so a
# task that's edited thousands of times over the years doesn't grow forever.
MAX_HISTORY_ENTRIES = 100

HISTORY_ACTION_CREATED = "created"
HISTORY_ACTION_UPDATED = "updated"
HISTORY_ACTION_COMPLETED = "completed"
HISTORY_ACTION_REOPENED = "reopened"
HISTORY_ACTION_RECURRED = "recurred"
HISTORY_ACTION_MISSED = "missed"
HISTORY_ACTION_REMINDED = "reminded"
HISTORY_ACTION_DELETED = "deleted"

# --- Field length limits (defense against accidentally pasting huge blobs) --------
MAX_TITLE_LENGTH = 200
MAX_NOTES_LENGTH = 4000
MAX_TAG_LENGTH = 40
MAX_TAGS = 20
MAX_SUBTASK_TITLE_LENGTH = 200

# --- Platforms this integration forwards each config entry to ---------------------
PLATFORMS = ["todo"]

# --- Service (automation-facing) names and field names -----------------------------
SERVICE_ADD_TASK = "add_task"
SERVICE_COMPLETE_TASK = "complete_task"
SERVICE_REOPEN_TASK = "reopen_task"
SERVICE_SET_DUE_RULE = "set_due_rule"

ATTR_TASK_ID = "task_id"
ATTR_TITLE = "title"
ATTR_NOTES = "notes"
ATTR_DUE_DATE = "due_date"
ATTR_DUE_TIME = "due_time"
ATTR_PRIORITY = "priority"
ATTR_EFFORT = "effort"
ATTR_TAGS = "tags"
ATTR_AREA_ID = "area_id"
ATTR_ASSIGNEES = "assignees"
ATTR_DUE_RULE = "due_rule"
ATTR_DUE_DAYS = "due_days"
ATTR_DUE_WEEKDAY = "due_weekday"

# --- Frontend -----------------------------------------------------------------
CARD_TAG = "better-todo-list-card"
FRONTEND_SCRIPT_URL = f"/{DOMAIN}_frontend/{CARD_TAG}.js"
