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

# --- Storage --------------------------------------------------------------------
# Bumping STORAGE_VERSION lets Store.async_migrate_func() detect old data on
# load and upgrade it, should the task schema ever change in a future version.
STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = DOMAIN

# --- Task status ------------------------------------------------------------------
STATUS_NEEDS_ACTION = "needs_action"
STATUS_COMPLETED = "completed"

# --- Priority -----------------------------------------------------------------
PRIORITY_LOW = "low"
PRIORITY_MEDIUM = "medium"
PRIORITY_HIGH = "high"
PRIORITIES = (PRIORITY_LOW, PRIORITY_MEDIUM, PRIORITY_HIGH)

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

# --- History / audit log -----------------------------------------------------------
# Oldest entries are dropped once a task's history exceeds this length, so a
# task that's edited thousands of times over the years doesn't grow forever.
MAX_HISTORY_ENTRIES = 100

HISTORY_ACTION_CREATED = "created"
HISTORY_ACTION_UPDATED = "updated"
HISTORY_ACTION_COMPLETED = "completed"
HISTORY_ACTION_REOPENED = "reopened"
HISTORY_ACTION_RECURRED = "recurred"
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

ATTR_TASK_ID = "task_id"
ATTR_TITLE = "title"
ATTR_NOTES = "notes"
ATTR_DUE_DATE = "due_date"
ATTR_DUE_TIME = "due_time"
ATTR_PRIORITY = "priority"
ATTR_TAGS = "tags"
ATTR_AREA_ID = "area_id"

# --- Frontend -----------------------------------------------------------------
CARD_TAG = "better-todo-list-card"
FRONTEND_SCRIPT_URL = f"/{DOMAIN}_frontend/{CARD_TAG}.js"
