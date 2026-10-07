/**
 * Better Todo List - custom Lovelace card.
 *
 * This is a plain "vanilla" Web Component: no build step, no npm, no
 * framework (not even Lit) - just a <script type="module"> that Home
 * Assistant loads automatically (see __init__.py's _async_register_frontend).
 * That means you can open this file in any text editor, change something,
 * refresh your browser (hard refresh: Ctrl+Shift+R, since browsers cache
 * JS aggressively), and see the result immediately - no compiling.
 *
 * --- How this file talks to the backend ---
 * Everything goes over `hass.callWS({...})`, Home Assistant's WebSocket
 * API. Every command below is one implemented in websocket_api.py -
 * search for the matching `better_todo_list/xxx` string there if you want
 * to see what happens on the Python side of any given action.
 *
 * --- Why so much manual DOM code instead of a framework? ---
 * Frameworks like Lit/React solve "how do I update the DOM without losing
 * focus/cursor position while the user is typing". Without one, the classic
 * bug is: re-render the whole card -> the <input> you were typing in gets
 * destroyed and recreated -> you lose focus and your cursor jumps around.
 * This file avoids that by keeping SEPARATE, independently-rendered
 * regions instead of one big one:
 *   #toolbar-root  - the search box / group / sort / filter controls
 *   #filter-root   - the filter chips panel (re-rendered when a chip is toggled)
 *   #groups-root   - the task list itself (re-rendered after data changes)
 *   #dialog-root   - the add/edit task popup (only rendered while open)
 * As long as you're typing in a field, nothing re-renders that field's
 * container until you submit or trigger a structural change (like
 * switching the recurrence type). See _onSubmit/_openDialog/_closeDialog.
 *
 * --- Grouping, sorting and filtering ---
 * "Group by" decides the section headers, "Sort" orders the tasks inside
 * each section, and the filter chips narrow down which tasks show at all
 * (any chip within a row matches, and every row with a chip selected must
 * match). Sorting never breaks a grouping apart - but when the sort is
 * about the same thing as the grouping (e.g. group by room + sort by floor),
 * it also orders the sections, and group-by-room + sort-by-floor adds floor
 * headings above the rooms. Floors and rooms follow the order Home
 * Assistant shows them in under Settings -> Areas, labels & zones. Your
 * last view is remembered per card in this browser (localStorage).
 *
 * DEBUGGING TIP: open your browser's DevTools (F12) -> Console tab for any
 * JS errors, and the Network -> WS tab to watch the actual
 * better_todo_list/* messages and responses live while you use the card.
 */

// --- Constants ----------------------------------------------------------------

// 0 = Monday .. 6 = Sunday, matching Python's `date.weekday()` used by
// recurrence.py on the backend - keep this ordering in sync with that file.
const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

const NTH_WEEK_OPTIONS = [
  ["1", "1st"],
  ["2", "2nd"],
  ["3", "3rd"],
  ["4", "4th"],
  ["last", "Last"],
];

const RECURRENCE_TYPE_OPTIONS = [
  ["interval", "Fixed interval (every N hours/days/weeks/months/years)"],
  ["weekly", "Weekly (every N weeks, on chosen weekdays)"],
  ["monthly_day", "Monthly, by day of month"],
  ["monthly_weekday", "Monthly, by weekday (e.g. 2nd Saturday)"],
  ["yearly", "Yearly anniversary"],
];

// Keep in sync with const.DUE_RULES on the backend.
const DUE_RULE_OPTIONS = [
  ["same_day", "The day it appears"],
  ["before_next", "The day before it repeats"],
  ["end_of_month", "The last day of the month"],
  ["weekday", "On a weekday..."],
  ["days_after", "A number of days after it appears..."],
];

// Keep in sync with const.REMINDER_OFFSETS_MINUTES on the backend.
const REMINDER_OPTIONS = [
  ["", "No reminder"],
  ["0", "When it's due"],
  ["60", "1 hour before"],
  ["180", "3 hours before"],
  ["1440", "1 day before"],
  ["2880", "2 days before"],
  ["10080", "1 week before"],
];

const GROUP_LABELS = {
  room: "Group by room",
  floor: "Group by floor",
  list: "Group by list",
  priority: "Group by priority",
  effort: "Group by effort",
  none: "No grouping",
};

const SORT_LABELS = {
  default: "Sort: priority, then due",
  due: "Sort: due date",
  priority: "Sort: priority",
  effort: "Sort: effort",
  floor: "Sort: floor",
  tag: "Sort: tag",
  title: "Sort: title",
};

const PRIORITY_META = {
  low: { label: "Low", color: "#4caf50" },
  medium: { label: "Medium", color: "#ff9800" },
  high: { label: "High", color: "#f44336" },
};

const EFFORT_META = {
  low: { label: "Low effort", icon: "mdi:gauge-low" },
  medium: { label: "Medium effort", icon: "mdi:gauge" },
  high: { label: "High effort", icon: "mdi:gauge-full" },
};

// "First" in each order; the reverse button flips them. Tasks without a
// value always go last either way.
const PRIORITY_SORT_ORDER = { high: 0, medium: 1, low: 2 };
const EFFORT_SORT_ORDER = { low: 0, medium: 1, high: 2 };

const FILTER_KEYS = ["priority", "effort", "floor", "room", "assignee", "tag", "due"];

const DUE_FILTER_OPTIONS = [
  ["overdue", "Overdue"],
  ["today", "Today"],
  ["week", "Next 7 days"],
  ["later", "Later"],
  ["none", "No due date"],
];

const NOTICE_DURATION_MS = 6000;
const NO_FLOOR_ORDER = 1e6;

// --- Small pure helper functions ------------------------------------------------

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function isoDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function todayIso() {
  return isoDate(new Date());
}

function addDaysIso(iso, days) {
  const [y, m, d] = iso.split("-").map(Number);
  return isoDate(new Date(y, m - 1, d + days));
}

function formatDateHuman(isoDateStr) {
  const [y, m, d] = isoDateStr.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function formatDateShort(isoDateStr) {
  const [y, m, d] = isoDateStr.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

function formatDue(dueDate, dueTime) {
  if (!dueDate) return "";
  return dueTime ? `${formatDateHuman(dueDate)} ${dueTime}` : formatDateHuman(dueDate);
}

function isOverdue(task) {
  if (task.status === "completed" || !task.due_date) return false;
  const due = new Date(`${task.due_date}T${task.due_time || "23:59"}:00`);
  return due.getTime() < Date.now();
}

function usesCycles(recurrence) {
  return !!recurrence && !(recurrence.type === "interval" && recurrence.interval_unit === "hours");
}

// A repeating task that's hidden until its next cycle appears - the same
// rule as store.is_waiting() on the backend.
function isWaiting(task) {
  if (!task.next_cycle_start || !usesCycles(task.recurrence)) return false;
  return !task.cycle_start || task.status === "completed";
}

function dueBucket(task) {
  if (!task.due_date) return "none";
  if (isOverdue(task)) return "overdue";
  const today = todayIso();
  if (task.due_date === today) return "today";
  if (task.due_date <= addDaysIso(today, 7)) return "week";
  return "later";
}

function subtaskProgressLabel(task) {
  const subs = task.sub_tasks || [];
  if (!subs.length) return "";
  const done = subs.filter((s) => s.status === "completed").length;
  return `${done}/${subs.length} subtasks`;
}

function formatTimestamp(iso) {
  try {
    return new Date(iso).toLocaleString();
  } catch (err) {
    return iso;
  }
}

function formatHistoryValue(value) {
  if (value === null || value === undefined || value === "") return "(none)";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "(none)";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function historyDescription(entry) {
  if (entry.action === "created") return "Task created";
  if (entry.action === "completed") return "Marked complete";
  if (entry.action === "reopened") return "Reopened";
  if (entry.action === "recurred") return `New cycle started (due ${entry.new || "?"})`;
  if (entry.action === "missed") return `Not completed before the next cycle started (was due ${entry.old || "?"})`;
  if (entry.action === "reminded") return `Reminder sent${entry.new ? ` to ${entry.new}` : ""}`;
  if (entry.action === "updated" && entry.field) {
    return `Changed ${entry.field}: ${formatHistoryValue(entry.old)} -> ${formatHistoryValue(entry.new)}`;
  }
  return entry.action;
}

function compareTasks(a, b) {
  if (a.status !== b.status) return a.status === "completed" ? 1 : -1;
  const pa = PRIORITY_SORT_ORDER[a.priority] ?? 3;
  const pb = PRIORITY_SORT_ORDER[b.priority] ?? 3;
  if (pa !== pb) return pa - pb;
  const da = a.due_date || "9999-99-99";
  const db = b.due_date || "9999-99-99";
  if (da !== db) return da < db ? -1 : 1;
  return a.title.localeCompare(b.title);
}

// Compares two sort values of the same kind: numbers, strings, or arrays of those.
function compareValues(a, b) {
  if (Array.isArray(a)) {
    for (let i = 0; i < Math.max(a.length, b.length); i++) {
      const c = compareValues(a[i], b[i]);
      if (c) return c;
    }
    return 0;
  }
  if (typeof a === "string") return a.localeCompare(b);
  return a - b;
}

// --- CSS ------------------------------------------------------------------------
// Uses Home Assistant's theme CSS variables (--primary-color etc.) so the
// card matches the user's light/dark theme automatically instead of
// hardcoding colors.

const CARD_CSS = `
  :host { display: block; }
  ha-card { padding: 8px 0 12px; }
  .toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 0 16px 8px; }
  .toolbar input[type="search"] { flex: 1 1 140px; min-width: 100px; padding: 6px 8px; border-radius: 6px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); }
  .toolbar select { padding: 6px 8px; border-radius: 6px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); max-width: 100%; }
  .toolbar label.show-completed { display: flex; align-items: center; gap: 4px; font-size: 0.9em; color: var(--secondary-text-color); white-space: nowrap; }
  .toolbar button { border: none; border-radius: 6px; background: var(--primary-color); color: var(--text-primary-color, #fff); padding: 6px 12px; cursor: pointer; font-size: 0.9em; }
  .toolbar button.icon-btn { background: transparent; color: var(--secondary-text-color); padding: 4px 8px; font-size: 1.1em; }
  .toolbar button.toggle-btn { background: transparent; color: var(--secondary-text-color); border: 1px solid var(--divider-color); padding: 5px 10px; }
  .toolbar button.toggle-btn.on { border-color: var(--primary-color); color: var(--primary-color); }

  .filter-panel { display: flex; flex-direction: column; gap: 6px; padding: 0 16px 10px; }
  .filter-row { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
  .filter-row .filter-label { font-size: 0.78em; text-transform: uppercase; letter-spacing: .04em; color: var(--secondary-text-color); min-width: 72px; }
  .fchip { border: 1px solid var(--divider-color); border-radius: 14px; padding: 2px 10px; font: inherit; font-size: 0.82em; background: transparent; color: var(--primary-text-color); cursor: pointer; }
  .fchip.on { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
  .filter-actions { display: flex; justify-content: flex-end; }
  .link-btn { background: none; border: none; color: var(--primary-color); cursor: pointer; font: inherit; font-size: 0.85em; padding: 2px 0; }

  .notice { margin: 0 16px 8px; padding: 8px 12px; border-radius: 8px; background: var(--secondary-background-color, rgba(127,127,127,0.12)); color: var(--primary-text-color); font-size: 0.88em; }

  .empty-state, .error-state { padding: 24px 16px; text-align: center; color: var(--secondary-text-color); }
  .error-state { color: var(--error-color, #db4437); }

  .floor-header { display: flex; align-items: center; gap: 8px; padding: 12px 16px 2px; font-weight: 600; font-size: 0.92em; color: var(--primary-text-color); }
  .floor-header ha-icon { --mdc-icon-size: 18px; color: var(--secondary-text-color); }
  .group { margin: 8px 0; }
  .group.nested .group-header { padding-left: 28px; }
  .group-header { display: flex; align-items: center; gap: 8px; padding: 4px 16px; font-weight: 500; color: var(--secondary-text-color); text-transform: uppercase; font-size: 0.78em; letter-spacing: .04em; }
  .group-header .count { background: var(--divider-color); border-radius: 10px; padding: 0 6px; font-size: 0.9em; }

  .task-row { padding: 8px 16px; border-bottom: 1px solid var(--divider-color); }
  .task-row:last-child { border-bottom: none; }
  .task-row.completed .task-title { text-decoration: line-through; color: var(--secondary-text-color); }
  .task-row.waiting { opacity: 0.7; }
  .task-row-main { display: flex; align-items: flex-start; gap: 10px; }
  .task-check { margin-top: 3px; width: 18px; height: 18px; flex: none; }
  .task-main { flex: 1; min-width: 0; cursor: pointer; }
  .task-title-row { display: flex; align-items: center; gap: 6px; }
  .task-title { font-size: 1em; color: var(--primary-text-color); word-break: break-word; }
  .prio-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
  .recur-icon { --mdc-icon-size: 16px; color: var(--secondary-text-color); }
  .task-meta { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 2px; font-size: 0.82em; color: var(--secondary-text-color); align-items: center; }
  .task-meta .due.overdue { color: var(--error-color, #db4437); font-weight: 500; }
  .chip { background: var(--divider-color); border-radius: 10px; padding: 1px 8px; display: inline-flex; align-items: center; gap: 3px; }
  .chip ha-icon { --mdc-icon-size: 13px; }
  .task-notes { margin-top: 4px; font-size: 0.85em; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .subtasks-inline { display: flex; flex-direction: column; gap: 3px; margin: 6px 0 0 28px; padding-left: 8px; border-left: 2px solid var(--divider-color); }
  .subtask-row-inline { display: flex; align-items: center; gap: 6px; font-size: 0.88em; }
  .subtask-row-inline .subtask-check { width: 15px; height: 15px; flex: none; }
  .subtask-row-inline span { flex: 1; word-break: break-word; color: var(--primary-text-color); }
  .subtask-row-inline span.completed { text-decoration: line-through; color: var(--secondary-text-color); }
  .icon-btn { background: transparent; border: none; color: var(--secondary-text-color); cursor: pointer; font-size: 1em; padding: 4px; }

  dialog { border: none; border-radius: 12px; padding: 0; width: min(480px, 92vw); max-height: 88vh; background: var(--card-background-color, #fff); color: var(--primary-text-color); }
  dialog::backdrop { background: rgba(0,0,0,0.5); }
  #task-form { display: flex; flex-direction: column; gap: 10px; padding: 20px; overflow-y: auto; max-height: 88vh; box-sizing: border-box; }
  #task-form h2 { margin: 0 0 4px; font-size: 1.2em; }
  #task-form label { display: flex; flex-direction: column; gap: 4px; font-size: 0.85em; color: var(--secondary-text-color); }
  #task-form input, #task-form select, #task-form textarea { font: inherit; padding: 7px 8px; border-radius: 6px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); box-sizing: border-box; }
  #task-form textarea { resize: vertical; min-height: 44px; }
  .field-row { display: flex; gap: 10px; }
  .field-row > label { flex: 1; min-width: 0; }
  fieldset.recurrence-fieldset { border: 1px solid var(--divider-color); border-radius: 8px; padding: 10px; display: flex; flex-direction: column; gap: 10px; }
  fieldset.recurrence-fieldset legend { padding: 0 4px; font-size: 0.9em; color: var(--primary-text-color); }
  #recurrence-fields, #due-rule-block { display: flex; flex-direction: column; gap: 10px; }
  .recurrence-preview .link-btn { align-self: flex-start; text-align: left; }
  .weekday-picker, .chip-picker { display: flex; flex-wrap: wrap; gap: 6px; }
  .weekday-chip, .pick-chip { flex-direction: row !important; align-items: center; gap: 4px !important; border: 1px solid var(--divider-color); border-radius: 14px; padding: 3px 8px; font-size: 0.85em; }
  .recurrence-preview { display: flex; flex-direction: column; gap: 4px; font-size: 0.85em; color: var(--secondary-text-color); background: var(--secondary-background-color, rgba(127,127,127,0.08)); border-radius: 6px; padding: 8px; }
  .recurrence-preview strong { color: var(--primary-text-color); font-weight: 500; }
  .recurrence-preview .preview-error { color: var(--error-color, #db4437); }
  .hint { font-size: 0.8em; color: var(--secondary-text-color); font-weight: normal; }
  .section-label { font-size: 0.85em; color: var(--secondary-text-color); font-weight: 500; }
  .subtasks-block { display: flex; flex-direction: column; gap: 6px; border-top: 1px solid var(--divider-color); padding-top: 10px; }
  .subtask-row { display: flex; align-items: center; gap: 8px; font-size: 0.92em; }
  .subtask-row .completed { text-decoration: line-through; color: var(--secondary-text-color); }
  .subtask-row span:not(.icon-btn) { flex: 1; }
  .add-subtask-row { display: flex; gap: 6px; }
  .add-subtask-row input { flex: 1; }
  details#history-details { border-top: 1px solid var(--divider-color); padding-top: 8px; font-size: 0.85em; }
  details#history-details summary { cursor: pointer; font-weight: 500; color: var(--secondary-text-color); }
  .history-list { display: flex; flex-direction: column; gap: 4px; margin-top: 8px; max-height: 160px; overflow-y: auto; }
  .history-entry { display: flex; gap: 6px; flex-wrap: wrap; color: var(--secondary-text-color); }
  .history-ts { font-variant-numeric: tabular-nums; }
  .history-actor { font-weight: 500; }
  .dialog-error { color: var(--error-color, #db4437); font-size: 0.85em; min-height: 1em; }
  .dialog-actions { display: flex; align-items: center; gap: 8px; margin-top: 4px; }
  .dialog-actions .spacer { flex: 1; }
  .dialog-actions button { border: none; border-radius: 6px; padding: 8px 14px; cursor: pointer; font: inherit; }
  .dialog-actions button[type="submit"] { background: var(--primary-color); color: var(--text-primary-color, #fff); }
  .dialog-actions button[data-action="close-dialog"] { background: transparent; color: var(--secondary-text-color); }
  .dialog-actions button[data-action="delete-from-dialog"] { background: transparent; color: var(--error-color, #db4437); }
`;

// --- The card ---------------------------------------------------------------

class BetterTodoListCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });

    this._lists = [];
    this._areas = [];
    this._floors = [];
    this._people = [];
    this._entryIds = [];
    this._tasksByEntry = {};
    this._groupBy = "room";
    this._sortBy = "default";
    this._sortReverse = false;
    this._filters = this._emptyFilters();
    this._showCompleted = false;
    this._filterPanelOpen = false;
    this._searchText = "";
    this._loaded = false;
    this._dialogState = null;
    this._searchDebounceTimer = null;
    this._previewDebounceTimer = null;
    this._previewToken = 0;
    this._noticeTimer = null;
    this._watchedEntityIds = new Set();
    this._syncDebounceTimer = null;

    this.shadowRoot.innerHTML = `
      <style>${CARD_CSS}</style>
      <ha-card>
        <div id="toolbar-root"></div>
        <div id="filter-root"></div>
        <div id="notice-root"></div>
        <div id="groups-root"><div class="empty-state">Loading...</div></div>
      </ha-card>
      <div id="dialog-root"></div>
    `;
    this._toolbarRoot = this.shadowRoot.getElementById("toolbar-root");
    this._filterRoot = this.shadowRoot.getElementById("filter-root");
    this._noticeRoot = this.shadowRoot.getElementById("notice-root");
    this._groupsRoot = this.shadowRoot.getElementById("groups-root");
    this._dialogRoot = this.shadowRoot.getElementById("dialog-root");

    // One delegated listener per event type covers every button/input
    // this card will ever render, including ones added later - see the
    // comment at the top of the file for why this avoids re-attaching
    // listeners (and losing focus) on every re-render.
    this.shadowRoot.addEventListener("click", (e) => this._onClick(e));
    this.shadowRoot.addEventListener("change", (e) => this._onChange(e));
    this.shadowRoot.addEventListener("input", (e) => this._onInput(e));
    this.shadowRoot.addEventListener("submit", (e) => this._onSubmit(e));
  }

  // --- Lovelace card contract ---------------------------------------------------

  setConfig(config) {
    if (!config) throw new Error("Invalid configuration");
    this._config = config;
    this._applyView(this._configView());
    const saved = this._loadSavedView();
    if (saved) this._applyView(saved);

    const cardEl = this.shadowRoot.querySelector("ha-card");
    if (cardEl) cardEl.header = config.title || "";
    if (this._toolbarRoot) {
      this._renderToolbar();
      this._renderFilterPanel();
    }
  }

  set hass(hass) {
    const oldHass = this._hass;
    this._hass = hass;
    if (!this._loaded && this.isConnected) {
      this._loaded = true;
      this._loadAll();
      return;
    }
    this._maybeSyncOnHassChange(oldHass, hass);
  }

  // Live multi-client sync: Home Assistant's frontend re-invokes this
  // setter on EVERY entity state change system-wide (not just ours), so we
  // don't need a separate WebSocket subscription - we just need to notice
  // when it's one of our lists' todo.* entities that changed. HA's state
  // objects are immutable (a changed entity gets a new object; unchanged
  // ones keep the same reference), so a `!==` check is a cheap, reliable
  // way to detect "did this entity actually change" without deep-comparing
  // anything. todo.py writes a new state on every store mutation from any
  // client (see its `_handle_store_changed`), which is what makes this work.
  _maybeSyncOnHassChange(oldHass, hass) {
    if (!oldHass || !this._watchedEntityIds.size) return;
    for (const entityId of this._watchedEntityIds) {
      if (oldHass.states[entityId] !== hass.states[entityId]) {
        this._scheduleSyncRefresh();
        return;
      }
    }
  }

  _scheduleSyncRefresh() {
    clearTimeout(this._syncDebounceTimer);
    this._syncDebounceTimer = setTimeout(() => {
      this._refreshTasks().catch((err) => this._showError(err));
    }, 300);
  }

  get hass() {
    return this._hass;
  }

  connectedCallback() {
    if (!this._loaded && this._hass) {
      this._loaded = true;
      this._loadAll();
    }
  }

  getCardSize() {
    return 5;
  }

  static getStubConfig() {
    return { type: "custom:better-todo-list-card" };
  }

  // --- View state (group / sort / filters), remembered per card -----------------

  _emptyFilters() {
    return Object.fromEntries(FILTER_KEYS.map((k) => [k, new Set()]));
  }

  // The view the card's YAML config asks for - also what "reset" goes back to.
  _configView() {
    const c = this._config || {};
    return {
      groupBy: c.group_by || "room",
      sortBy: c.sort_by || "default",
      sortReverse: !!c.sort_reverse,
      showCompleted: !!c.show_completed,
      filters: c.filters || {},
    };
  }

  _applyView(view) {
    if (GROUP_LABELS[view.groupBy]) this._groupBy = view.groupBy;
    if (SORT_LABELS[view.sortBy]) this._sortBy = view.sortBy;
    this._sortReverse = !!view.sortReverse;
    this._showCompleted = !!view.showCompleted;
    this._filters = this._emptyFilters();
    for (const key of FILTER_KEYS) {
      const values = view.filters && view.filters[key];
      if (Array.isArray(values)) values.forEach((v) => this._filters[key].add(String(v)));
    }
  }

  _currentView() {
    return {
      groupBy: this._groupBy,
      sortBy: this._sortBy,
      sortReverse: this._sortReverse,
      showCompleted: this._showCompleted,
      filters: Object.fromEntries(FILTER_KEYS.map((k) => [k, [...this._filters[k]]])),
    };
  }

  // Saved views are tagged with the config they were made under, so
  // editing the card's YAML (e.g. a new group_by) takes effect instead of
  // being silently overridden by an old saved view.
  _viewStorageKey() {
    const c = this._config || {};
    return `better-todo-list-card:view:${c.list_name || "*"}:${c.title || ""}`;
  }

  _loadSavedView() {
    try {
      const raw = window.localStorage.getItem(this._viewStorageKey());
      if (!raw) return null;
      const saved = JSON.parse(raw);
      if (saved.configView !== JSON.stringify(this._configView())) return null;
      return saved.view;
    } catch (err) {
      return null;
    }
  }

  _saveView() {
    try {
      window.localStorage.setItem(
        this._viewStorageKey(),
        JSON.stringify({ configView: JSON.stringify(this._configView()), view: this._currentView() })
      );
    } catch (err) {
      // Storage unavailable (private browsing etc.) - the view just isn't remembered.
    }
  }

  _activeFilterCount() {
    return FILTER_KEYS.reduce((n, k) => n + this._filters[k].size, 0);
  }

  // --- Data loading ---------------------------------------------------------------

  async _callWS(msg) {
    if (!this._hass) throw new Error("Home Assistant connection isn't ready yet.");
    return this._hass.callWS(msg);
  }

  async _loadAll() {
    try {
      const [{ lists }, { areas, floors }, { people }] = await Promise.all([
        this._callWS({ type: "better_todo_list/get_lists" }),
        this._callWS({ type: "better_todo_list/get_areas" }),
        this._callWS({ type: "better_todo_list/get_people" }),
      ]);
      this._lists = lists;
      this._areas = areas;
      this._floors = floors || [];
      this._people = people || [];
      this._entryIds = this._resolveEntryIds(lists);
      this._watchedEntityIds = new Set(
        lists
          .filter((l) => this._entryIds.includes(l.entry_id) && l.entity_id)
          .map((l) => l.entity_id)
      );
      await this._refreshTasks();
    } catch (err) {
      this._showError(err);
    }
  }

  _resolveEntryIds(lists) {
    if (this._config && this._config.list_name) {
      const match = lists.find((l) => l.name === this._config.list_name);
      if (!match) {
        const known = lists.map((l) => l.name).join(", ") || "(none configured yet)";
        throw new Error(`No Better Todo List list named "${this._config.list_name}". Configured lists: ${known}`);
      }
      return [match.entry_id];
    }
    return lists.map((l) => l.entry_id);
  }

  async _refreshTasks() {
    const results = await Promise.all(
      this._entryIds.map((entryId) => this._callWS({ type: "better_todo_list/get_tasks", entry_id: entryId }))
    );
    this._tasksByEntry = {};
    this._entryIds.forEach((entryId, i) => {
      this._tasksByEntry[entryId] = results[i].tasks;
    });
    this._renderGroups();
    if (this._filterPanelOpen) this._renderFilterPanel();
  }

  _listName(entryId) {
    const entry = this._lists.find((l) => l.entry_id === entryId);
    return entry ? entry.name : entryId;
  }

  _area(areaId) {
    return this._areas.find((a) => a.area_id === areaId) || null;
  }

  _areaName(areaId) {
    const area = this._area(areaId);
    return area ? area.name : null;
  }

  _areaIndex(areaId) {
    const index = this._areas.findIndex((a) => a.area_id === areaId);
    return index === -1 ? NO_FLOOR_ORDER : index;
  }

  _floorOf(task) {
    const area = task.area_id ? this._area(task.area_id) : null;
    return area && area.floor_id ? this._floors.find((f) => f.floor_id === area.floor_id) || null : null;
  }

  _floorIndex(floorId) {
    const index = this._floors.findIndex((f) => f.floor_id === floorId);
    return index === -1 ? NO_FLOOR_ORDER : index;
  }

  _personName(entityId) {
    const person = this._people.find((p) => p.entity_id === entityId);
    return person ? person.name : entityId.replace(/^person\./, "");
  }

  _findTask(entryId, taskId) {
    return (this._tasksByEntry[entryId] || []).find((t) => t.id === taskId) || null;
  }

  _allTasks() {
    return this._entryIds.flatMap((entryId) => this._tasksByEntry[entryId] || []);
  }

  // --- Toolbar ---------------------------------------------------------------

  _renderToolbar() {
    const options = (labels, current) =>
      Object.keys(labels)
        .map((k) => `<option value="${k}" ${current === k ? "selected" : ""}>${labels[k]}</option>`)
        .join("");
    const filterCount = this._activeFilterCount();
    this._toolbarRoot.innerHTML = `
      <div class="toolbar">
        <input type="search" id="search" placeholder="Search tasks..." value="${escapeHtml(this._searchText)}">
        <label class="show-completed" title="Also shows repeating tasks that are waiting for their next cycle">
          <input type="checkbox" id="show-completed" ${this._showCompleted ? "checked" : ""}>
          Show completed
        </label>
        <select id="group-by" aria-label="Group by">${options(GROUP_LABELS, this._groupBy)}</select>
        <select id="sort-by" aria-label="Sort by">${options(SORT_LABELS, this._sortBy)}</select>
        <button type="button" class="toggle-btn ${this._sortReverse ? "on" : ""}" data-action="sort-reverse"
                title="Reverse the sort order" aria-pressed="${this._sortReverse}">&#8645;</button>
        <button type="button" class="toggle-btn ${filterCount ? "on" : ""}" data-action="toggle-filters"
                aria-expanded="${this._filterPanelOpen}">Filter${filterCount ? ` (${filterCount})` : ""}</button>
        <button type="button" class="icon-btn" data-action="refresh" title="Refresh">&#8635;</button>
        <button type="button" data-action="add-task">+ Add task</button>
      </div>
    `;
  }

  // Each filter row: [key, label, [[value, label], ...]]. Rooms and tags
  // only list ones actually used by a task (plus any already selected), so
  // a house with 30 Areas doesn't get 30 chips for rooms without tasks.
  _filterRows() {
    const tasks = this._allTasks();
    const usedAreas = new Set(tasks.map((t) => t.area_id).filter(Boolean));
    const usedTags = new Map();
    for (const t of tasks) for (const tag of t.tags || []) usedTags.set(tag.toLowerCase(), tag);
    for (const tag of this._filters.tag) if (!usedTags.has(tag)) usedTags.set(tag, tag);

    const rows = [
      ["priority", "Priority", [["high", "High"], ["medium", "Medium"], ["low", "Low"], ["none", "None"]]],
      ["effort", "Effort", [["low", "Low"], ["medium", "Medium"], ["high", "High"], ["none", "None"]]],
    ];
    if (this._floors.length) {
      rows.push(["floor", "Floor", [...this._floors.map((f) => [f.floor_id, f.name]), ["none", "No floor"]]]);
    }
    const rooms = this._areas
      .filter((a) => usedAreas.has(a.area_id) || this._filters.room.has(a.area_id))
      .map((a) => [a.area_id, a.name]);
    if (rooms.length) rows.push(["room", "Room", [...rooms, ["none", "No room"]]]);
    if (this._people.length) {
      rows.push(["assignee", "Assigned", [...this._people.map((p) => [p.entity_id, p.name]), ["none", "Unassigned"]]]);
    }
    if (usedTags.size) {
      const tagOptions = [...usedTags.entries()].sort((a, b) => a[1].localeCompare(b[1]));
      rows.push(["tag", "Tag", [...tagOptions, ["none", "No tags"]]]);
    }
    rows.push(["due", "Due", DUE_FILTER_OPTIONS]);
    return rows;
  }

  _renderFilterPanel() {
    if (!this._filterPanelOpen) {
      this._filterRoot.innerHTML = "";
      return;
    }
    const rowsHtml = this._filterRows()
      .map(([key, label, options]) => `
        <div class="filter-row">
          <span class="filter-label">${label}</span>
          ${options
            .map(([value, text]) => {
              const on = this._filters[key].has(value);
              return `<button type="button" class="fchip ${on ? "on" : ""}" data-action="filter-chip"
                        data-key="${key}" data-value="${escapeHtml(value)}" aria-pressed="${on}">${escapeHtml(text)}</button>`;
            })
            .join("")}
        </div>`)
      .join("");
    this._filterRoot.innerHTML = `
      <div class="filter-panel">
        ${rowsHtml}
        <div class="filter-actions">
          ${this._activeFilterCount() ? `<button type="button" class="link-btn" data-action="clear-filters">Clear filters</button>` : ""}
        </div>
      </div>`;
  }

  _onViewChanged() {
    this._saveView();
    this._renderToolbar();
    this._renderFilterPanel();
    this._renderGroups();
  }

  // --- Task list: filtering ------------------------------------------------------

  _filterValues(key, task) {
    switch (key) {
      case "priority":
        return [task.priority || "none"];
      case "effort":
        return [task.effort || "none"];
      case "floor": {
        const floor = this._floorOf(task);
        return [floor ? floor.floor_id : "none"];
      }
      case "room":
        return [task.area_id || "none"];
      case "assignee":
        return (task.assignees || []).length ? task.assignees : ["none"];
      case "tag":
        return (task.tags || []).length ? task.tags.map((t) => t.toLowerCase()) : ["none"];
      case "due":
        return [dueBucket(task)];
      default:
        return [];
    }
  }

  _passesFilters(task) {
    return FILTER_KEYS.every((key) => {
      const selected = this._filters[key];
      return !selected.size || this._filterValues(key, task).some((v) => selected.has(v));
    });
  }

  _flattenVisibleTasks() {
    const search = (this._searchText || "").trim().toLowerCase();
    const rows = [];
    for (const entryId of this._entryIds) {
      for (const task of this._tasksByEntry[entryId] || []) {
        if (!this._showCompleted && (task.status === "completed" || isWaiting(task))) continue;
        if (search) {
          const haystack = [task.title, task.notes, ...(task.tags || [])].filter(Boolean).join(" ").toLowerCase();
          if (!haystack.includes(search)) continue;
        }
        if (!this._passesFilters(task)) continue;
        rows.push({ entryId, task });
      }
    }
    rows.sort((a, b) => this._compareRows(a.task, b.task));
    return rows;
  }

  // --- Task list: sorting -------------------------------------------------------

  // The value a task sorts by for the current "Sort", or null if it has
  // none (those always sort last, whichever direction).
  _sortValue(task) {
    switch (this._sortBy) {
      case "due":
        return task.due_date ? `${task.due_date}T${task.due_time || "24:00"}` : null;
      case "priority":
        return task.priority ? PRIORITY_SORT_ORDER[task.priority] : null;
      case "effort":
        return task.effort ? EFFORT_SORT_ORDER[task.effort] : null;
      case "floor": {
        if (!task.area_id) return null;
        const floor = this._floorOf(task);
        return [floor ? this._floorIndex(floor.floor_id) : NO_FLOOR_ORDER, this._areaIndex(task.area_id)];
      }
      case "tag":
        return (task.tags || []).length ? task.tags.map((t) => t.toLowerCase()).sort().join("\u0000") : null;
      case "title":
        return task.title.toLowerCase();
      default:
        return null;
    }
  }

  _compareRows(a, b) {
    if (a.status !== b.status) return a.status === "completed" ? 1 : -1;
    const direction = this._sortReverse ? -1 : 1;
    if (this._sortBy === "default") return direction * compareTasks(a, b);
    const va = this._sortValue(a);
    const vb = this._sortValue(b);
    if (va === null && vb !== null) return 1;
    if (vb === null && va !== null) return -1;
    if (va !== null && vb !== null) {
      const c = compareValues(va, vb);
      if (c) return direction * c;
    }
    return compareTasks(a, b);
  }

  // --- Task list: grouping ------------------------------------------------------

  // Section key for a task under the current "Group by". `order` sorts the
  // sections; `floor` (group-by-room + sort-by-floor only) adds a floor
  // heading above each run of rooms on the same floor.
  _groupKey(row) {
    const task = row.task;
    // The sort also orders the sections when it's about the same thing.
    const flip = (sortKey) => (this._sortBy === sortKey && this._sortReverse ? -1 : 1);

    if (this._groupBy === "list") {
      return { id: row.entryId, label: this._listName(row.entryId), order: [0] };
    }
    if (this._groupBy === "room") {
      const areaId = task.area_id;
      const nestByFloor = this._sortBy === "floor";
      const floor = this._floorOf(task);
      const floorKey = floor
        ? { id: floor.floor_id, label: floor.name, icon: floor.icon }
        : { id: "__no_floor__", label: "No floor" };
      if (!areaId) {
        return { id: "__no_room__", label: "No room", order: [2, 0, 0], floor: nestByFloor ? floorKey : null };
      }
      const label = this._areaName(areaId) || "Unknown room";
      if (!nestByFloor) return { id: areaId, label, order: [0, this._areaIndex(areaId)] };
      return {
        id: areaId,
        label,
        order: [floor ? 0 : 1, floor ? flip("floor") * this._floorIndex(floor.floor_id) : 0, this._areaIndex(areaId)],
        floor: floorKey,
      };
    }
    if (this._groupBy === "floor") {
      const floor = this._floorOf(task);
      if (!floor) {
        return task.area_id
          ? { id: "__no_floor__", label: "No floor", order: [1, 0] }
          : { id: "__no_room__", label: "No room", order: [1, 1] };
      }
      return { id: floor.floor_id, label: floor.name, order: [0, flip("floor") * this._floorIndex(floor.floor_id)] };
    }
    if (this._groupBy === "priority") {
      if (!task.priority) return { id: "__none__", label: "No priority", order: [1, 0] };
      return {
        id: task.priority,
        label: `${PRIORITY_META[task.priority].label} priority`,
        order: [0, flip("priority") * PRIORITY_SORT_ORDER[task.priority]],
      };
    }
    if (this._groupBy === "effort") {
      if (!task.effort) return { id: "__none__", label: "No effort set", order: [1, 0] };
      return {
        id: task.effort,
        label: EFFORT_META[task.effort].label,
        order: [0, flip("effort") * EFFORT_SORT_ORDER[task.effort]],
      };
    }
    return { id: "__all__", label: "All tasks", order: [0] };
  }

  _groupRows(rows) {
    const groups = new Map();
    for (const row of rows) {
      const key = this._groupKey(row);
      if (!groups.has(key.id)) groups.set(key.id, { ...key, rows: [] });
      groups.get(key.id).rows.push(row);
    }
    return [...groups.values()].sort((a, b) => compareValues(a.order, b.order) || a.label.localeCompare(b.label));
  }

  _renderGroups() {
    if (!this._entryIds.length) {
      this._groupsRoot.innerHTML =
        `<div class="empty-state">No lists found yet. Add one via Settings &rarr; Devices &amp; Services &rarr; ` +
        `Add Integration &rarr; Better Todo List.</div>`;
      return;
    }

    const rows = this._flattenVisibleTasks();
    if (!rows.length) {
      const filtered = this._activeFilterCount() || (this._searchText || "").trim();
      this._groupsRoot.innerHTML = `<div class="empty-state">${filtered ? "No tasks match these filters." : "No tasks to show."}</div>`;
      return;
    }

    let currentFloor = null;
    this._groupsRoot.innerHTML = this._groupRows(rows)
      .map((g) => {
        let floorHeader = "";
        if (g.floor && g.floor.id !== currentFloor) {
          currentFloor = g.floor.id;
          floorHeader = `<div class="floor-header">${g.floor.icon ? `<ha-icon icon="${escapeHtml(g.floor.icon)}"></ha-icon>` : ""}${escapeHtml(g.floor.label)}</div>`;
        }
        return `${floorHeader}
        <div class="group ${g.floor ? "nested" : ""}">
          <div class="group-header">${escapeHtml(g.label)} <span class="count">${g.rows.length}</span></div>
          <div class="task-list">
            ${g.rows.map((r) => this._taskRowHtml(r.entryId, r.task)).join("")}
          </div>
        </div>`;
      })
      .join("");
  }

  _taskRowHtml(entryId, task) {
    const overdue = isOverdue(task);
    const waiting = isWaiting(task);
    const subLabel = subtaskProgressLabel(task);
    const dueLabel = formatDue(task.due_date, task.due_time);
    const tagsHtml = (task.tags || []).map((t) => `<span class="chip">${escapeHtml(t)}</span>`).join("");
    const prio = task.priority ? PRIORITY_META[task.priority] : null;
    const effort = task.effort ? EFFORT_META[task.effort] : null;
    const notes = (task.notes || "").trim();
    const roomName = task.area_id && this._groupBy !== "room" ? this._areaName(task.area_id) : null;
    const peopleHtml = (task.assignees || [])
      .map((p) => `<span class="chip"><ha-icon icon="mdi:account"></ha-icon>${escapeHtml(this._personName(p))}</span>`)
      .join("");
    const waitingLabel = waiting
      ? `<span class="chip"><ha-icon icon="mdi:calendar-clock"></ha-icon>${task.cycle_start ? "Reappears" : "Starts"} ${escapeHtml(formatDateShort(task.next_cycle_start))}</span>`
      : "";

    return `
      <div class="task-row ${task.status === "completed" ? "completed" : ""} ${waiting ? "waiting" : ""}">
        <div class="task-row-main">
          <input type="checkbox" class="task-check" data-role="toggle-task" data-task-id="${task.id}" data-entry-id="${entryId}" ${task.status === "completed" ? "checked" : ""}>
          <div class="task-main" data-action="open" data-task-id="${task.id}" data-entry-id="${entryId}">
            <div class="task-title-row">
              ${prio ? `<span class="prio-dot" style="background:${prio.color}" title="${prio.label} priority"></span>` : ""}
              <span class="task-title">${escapeHtml(task.title)}</span>
              ${task.recurrence ? `<ha-icon icon="mdi:repeat" class="recur-icon" title="Repeats"></ha-icon>` : ""}
              ${task.reminder !== null && task.reminder !== undefined && (task.assignees || []).length ? `<ha-icon icon="mdi:bell-outline" class="recur-icon" title="Reminder on"></ha-icon>` : ""}
            </div>
            <div class="task-meta">
              ${waitingLabel}
              ${dueLabel ? `<span class="due ${overdue ? "overdue" : ""}">${escapeHtml(dueLabel)}</span>` : ""}
              ${subLabel ? `<span class="subprogress">${subLabel}</span>` : ""}
              ${effort ? `<span class="chip"><ha-icon icon="${effort.icon}"></ha-icon>${effort.label}</span>` : ""}
              ${roomName ? `<span class="chip"><ha-icon icon="mdi:door"></ha-icon>${escapeHtml(roomName)}</span>` : ""}
              ${peopleHtml}
              ${tagsHtml}
            </div>
            ${notes ? `<div class="task-notes" title="${escapeHtml(notes)}">${escapeHtml(notes)}</div>` : ""}
          </div>
          <button type="button" class="icon-btn" data-action="delete" data-task-id="${task.id}" data-entry-id="${entryId}" title="Delete">
            <ha-icon icon="mdi:delete-outline"></ha-icon>
          </button>
        </div>
        ${this._inlineSubtasksHtml(entryId, task)}
      </div>`;
  }

  // Nested checklist shown directly under a task in the main list view, so
  // subtasks can be checked off without opening the edit dialog. Kept as a
  // sibling of .task-row-main (not nested inside .task-main) on purpose -
  // .task-main has data-action="open" covering its whole area, and a click
  // on a checkbox bubbles up through its ancestors, so if these rows lived
  // inside .task-main a subtask click would also pop open the edit dialog.
  _inlineSubtasksHtml(entryId, task) {
    const subs = task.sub_tasks || [];
    if (!subs.length) return "";

    const rows = subs
      .map(
        (s) => `
        <div class="subtask-row-inline">
          <input type="checkbox" class="subtask-check" data-role="toggle-subtask-inline"
                 data-sub-id="${s.id}" data-task-id="${task.id}" data-entry-id="${entryId}"
                 ${s.status === "completed" ? "checked" : ""}>
          <span class="${s.status === "completed" ? "completed" : ""}">${escapeHtml(s.title)}</span>
        </div>`
      )
      .join("");

    return `<div class="subtasks-inline">${rows}</div>`;
  }

  // --- Notices -------------------------------------------------------------------

  _showNotice(text) {
    clearTimeout(this._noticeTimer);
    this._noticeRoot.innerHTML = `<div class="notice" role="status">${escapeHtml(text)}</div>`;
    this._noticeTimer = setTimeout(() => {
      this._noticeRoot.innerHTML = "";
    }, NOTICE_DURATION_MS);
  }

  // After saving or completing: say where a task went if it just disappeared
  // from view because it's now waiting for its next cycle.
  _noticeIfHidden(task) {
    if (!task || this._showCompleted || !isWaiting(task)) return;
    const when = formatDateShort(task.next_cycle_start);
    this._showNotice(
      task.cycle_start
        ? `"${task.title}" is done for this cycle - it will reappear on ${when}.`
        : `"${task.title}" will first appear on ${when}. Turn on "Show completed" to see it before then.`
    );
  }

  // --- Delegated event handlers ------------------------------------------------

  async _onClick(e) {
    const el = e.target.closest("[data-action]");
    if (!el) return;

    try {
      switch (el.dataset.action) {
        case "refresh":
          await this._refreshTasks();
          break;
        case "add-task":
          if (!this._entryIds.length) {
            alert("Add a list first via Settings -> Devices & Services -> Add Integration -> Better Todo List.");
            return;
          }
          this._openDialog({ mode: "create" });
          break;
        case "sort-reverse":
          this._sortReverse = !this._sortReverse;
          this._onViewChanged();
          break;
        case "toggle-filters":
          this._filterPanelOpen = !this._filterPanelOpen;
          this._renderToolbar();
          this._renderFilterPanel();
          break;
        case "filter-chip": {
          const set = this._filters[el.dataset.key];
          if (set.has(el.dataset.value)) set.delete(el.dataset.value);
          else set.add(el.dataset.value);
          this._onViewChanged();
          break;
        }
        case "clear-filters":
          this._filters = this._emptyFilters();
          this._onViewChanged();
          break;
        case "open":
          this._openDialog({ mode: "edit", entryId: el.dataset.entryId, taskId: el.dataset.taskId });
          break;
        case "delete":
          if (!confirm("Delete this task?")) return;
          await this._callWS({ type: "better_todo_list/delete_task", entry_id: el.dataset.entryId, task_id: el.dataset.taskId });
          await this._refreshTasks();
          break;
        case "close-dialog":
          this._closeDialog();
          break;
        case "delete-from-dialog": {
          if (!confirm("Delete this task?")) return;
          const { entryId, taskId } = this._dialogState;
          await this._callWS({ type: "better_todo_list/delete_task", entry_id: entryId, task_id: taskId });
          this._closeDialog();
          await this._refreshTasks();
          break;
        }
        case "add-subtask":
          await this._addSubtaskFromDialog();
          break;
        case "delete-subtask":
          await this._deleteSubtaskFromDialog(el.dataset.subId);
          break;
        case "use-earlier-start": {
          const begin = this._dialogRoot.querySelector("[name=recurrence_start_date]");
          if (begin) begin.value = el.dataset.date;
          this._refreshRecurrencePreview();
          break;
        }
        default:
          break;
      }
    } catch (err) {
      this._showToastError(err);
    }
  }

  async _onChange(e) {
    const target = e.target;

    if (target.id === "show-completed") {
      this._showCompleted = target.checked;
      this._onViewChanged();
      return;
    }
    if (target.id === "group-by") {
      this._groupBy = target.value;
      this._onViewChanged();
      return;
    }
    if (target.id === "sort-by") {
      this._sortBy = target.value;
      this._onViewChanged();
      return;
    }
    if (target.dataset && target.dataset.role === "toggle-task") {
      const { taskId, entryId } = target.dataset;
      const wasChecked = target.checked;
      try {
        const command = wasChecked ? "better_todo_list/complete_task" : "better_todo_list/reopen_task";
        const { task } = await this._callWS({ type: command, entry_id: entryId, task_id: taskId });
        await this._refreshTasks();
        if (wasChecked) this._noticeIfHidden(task);
      } catch (err) {
        target.checked = !wasChecked;
        this._showToastError(err);
      }
      return;
    }
    if (target.dataset && target.dataset.role === "toggle-subtask-inline") {
      const wasChecked = target.checked;
      const { entryId, taskId, subId } = target.dataset;
      try {
        const status = wasChecked ? "completed" : "needs_action";
        await this._callWS({
          type: "better_todo_list/update_sub_task",
          entry_id: entryId,
          task_id: taskId,
          sub_task_id: subId,
          status,
        });
        await this._refreshTasks();
      } catch (err) {
        target.checked = !wasChecked;
        this._showToastError(err);
      }
      return;
    }
    if (target.dataset && target.dataset.role === "toggle-subtask") {
      const wasChecked = target.checked;
      try {
        const { entryId, taskId } = this._dialogState;
        const status = wasChecked ? "completed" : "needs_action";
        const { task } = await this._callWS({
          type: "better_todo_list/update_sub_task",
          entry_id: entryId,
          task_id: taskId,
          sub_task_id: target.dataset.subId,
          status,
        });
        this._dialogState.task = task;
        this._refreshSubtasksSection();
      } catch (err) {
        target.checked = !wasChecked;
        this._showDialogError(err);
      }
      return;
    }
    if (target.id === "repeat-toggle") {
      const fieldsEl = this._dialogRoot.querySelector("#recurrence-fields");
      if (fieldsEl) fieldsEl.style.display = target.checked ? "" : "none";
      this._refreshRecurrencePreview();
      return;
    }
    if (target.id === "recurrence-type") {
      this._refreshRecurrenceTypeFields();
      this._refreshRecurrencePreview();
      return;
    }
    if (target.name === "interval_unit") {
      this._refreshRecurrenceTypeFields({
        interval_unit: target.value,
        interval_value: this._dialogRoot.querySelector("[name=interval_value]").value,
      });
      this._refreshRecurrencePreview();
      return;
    }
    if (target.id === "recurrence-end-type") {
      this._refreshRecurrenceEndFields();
      this._refreshRecurrencePreview();
      return;
    }
    if (target.id === "due-rule") {
      this._refreshDueRuleFields();
      this._refreshRecurrencePreview();
      return;
    }
    if (target.closest && target.closest("#recurrence-fields")) {
      this._refreshRecurrencePreview();
    }
  }

  _onInput(e) {
    if (e.target.closest && e.target.closest("#recurrence-fields")) {
      clearTimeout(this._previewDebounceTimer);
      this._previewDebounceTimer = setTimeout(() => this._refreshRecurrencePreview(), 300);
      return;
    }
    if (e.target.id !== "search") return;
    const value = e.target.value;
    clearTimeout(this._searchDebounceTimer);
    this._searchDebounceTimer = setTimeout(() => {
      this._searchText = value;
      this._renderGroups();
    }, 150);
  }

  async _onSubmit(e) {
    if (e.target.id !== "task-form") return;
    e.preventDefault();
    this._clearDialogError();

    const form = e.target;
    let payload;
    try {
      payload = this._collectDialogPayload(form);
    } catch (err) {
      this._showDialogError(err);
      return;
    }

    const fields = {
      title: payload.title,
      notes: payload.notes,
      due_date: payload.due_date,
      due_time: payload.due_time,
      priority: payload.priority,
      effort: payload.effort,
      tags: payload.tags,
      area_id: payload.area_id,
      assignees: payload.assignees,
      reminder: payload.reminder,
      recurrence: payload.recurrence,
    };
    try {
      let saved;
      if (this._dialogState.mode === "create") {
        ({ task: saved } = await this._callWS({ type: "better_todo_list/create_task", entry_id: payload.entry_id, ...fields }));
        for (const title of payload.newSubtasks) {
          await this._callWS({ type: "better_todo_list/add_sub_task", entry_id: payload.entry_id, task_id: saved.id, title });
        }
      } else {
        ({ task: saved } = await this._callWS({
          type: "better_todo_list/update_task",
          entry_id: this._dialogState.entryId,
          task_id: this._dialogState.taskId,
          ...fields,
        }));
      }
      this._closeDialog();
      await this._refreshTasks();
      this._noticeIfHidden(saved);
    } catch (err) {
      this._showDialogError(err);
    }
  }

  // --- Dialog: open/close ------------------------------------------------------

  _openDialog({ mode, entryId, taskId }) {
    let task = null;
    let resolvedEntryId = entryId;

    if (mode === "edit") {
      task = this._findTask(entryId, taskId);
      if (!task) return;
    } else if (!resolvedEntryId) {
      resolvedEntryId = this._entryIds[0];
    }

    this._dialogState = { mode, entryId: resolvedEntryId, taskId: task ? task.id : null, task };
    this._dialogRoot.innerHTML = this._dialogHtml(this._dialogState, task);

    const dialogEl = this._dialogRoot.querySelector("dialog");
    dialogEl.addEventListener("close", () => this._closeDialog());

    // The `toggle` event on <details> doesn't bubble, so it can't be
    // caught by the delegated listeners on shadowRoot - it needs a
    // listener attached directly to the element itself.
    const historyDetails = this._dialogRoot.querySelector("#history-details");
    if (historyDetails) {
      historyDetails.addEventListener("toggle", () => this._onHistoryToggle());
    }

    dialogEl.showModal();
    this._refreshRecurrencePreview();
  }

  _closeDialog() {
    const dialogEl = this._dialogRoot.querySelector("dialog");
    if (dialogEl && dialogEl.open) dialogEl.close();
    this._dialogRoot.innerHTML = "";
    this._dialogState = null;
    clearTimeout(this._previewDebounceTimer);
  }

  async _onHistoryToggle() {
    const details = this._dialogRoot.querySelector("#history-details");
    if (!details || !details.open || !this._dialogState) return;
    const listEl = this._dialogRoot.querySelector("#history-list");
    try {
      const { entryId, taskId } = this._dialogState;
      const { history } = await this._callWS({ type: "better_todo_list/get_task_history", entry_id: entryId, task_id: taskId });
      listEl.innerHTML = this._historyEntriesHtml(history);
    } catch (err) {
      listEl.textContent = `Could not load history: ${err.message || err}`;
    }
  }

  _historyEntriesHtml(history) {
    if (!history || !history.length) return `<div class="hint">No history yet.</div>`;
    const names = (value) => (Array.isArray(value) ? value.map((p) => this._personName(p)) : value);
    return [...history]
      .reverse()
      .map((h) => (h.field === "assignees" && h.action === "updated" ? { ...h, old: names(h.old), new: names(h.new) } : h))
      .map(
        (h) => `
        <div class="history-entry">
          <span class="history-ts">${escapeHtml(formatTimestamp(h.ts))}</span>
          <span class="history-actor">${escapeHtml(h.actor)}</span>
          <span class="history-desc">${escapeHtml(historyDescription(h))}</span>
        </div>`
      )
      .join("");
  }

  // --- Dialog: main HTML -------------------------------------------------------

  _dialogHtml(state, task) {
    const showListPicker = state.mode === "create" && this._entryIds.length > 1;
    const listField = showListPicker
      ? `<label>List
          <select name="entry_id">
            ${this._entryIds
              .map((id) => `<option value="${id}" ${id === state.entryId ? "selected" : ""}>${escapeHtml(this._listName(id))}</option>`)
              .join("")}
          </select>
        </label>`
      : `<input type="hidden" name="entry_id" value="${escapeHtml(state.entryId || "")}">`;

    const areaOptions = [...this._areas]
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((a) => `<option value="${a.area_id}" ${task && task.area_id === a.area_id ? "selected" : ""}>${escapeHtml(a.name)}</option>`)
      .join("");
    const levelOptions = (current) =>
      ["low", "medium", "high"]
        .map((v) => `<option value="${v}" ${current === v ? "selected" : ""}>${v[0].toUpperCase()}${v.slice(1)}</option>`)
        .join("");
    const reminderValue = task && task.reminder !== null && task.reminder !== undefined ? String(task.reminder) : "";
    const isCycleTask = task && usesCycles(task.recurrence);

    return `
      <dialog id="task-dialog">
        <form id="task-form" novalidate>
          <h2>${state.mode === "create" ? "New Task" : "Edit Task"}</h2>

          ${listField}

          <label>Title
            <input type="text" name="title" required maxlength="200" value="${escapeHtml(task ? task.title : "")}">
          </label>

          <label>Notes
            <textarea name="notes" maxlength="4000">${escapeHtml(task ? task.notes || "" : "")}</textarea>
          </label>

          <div class="field-row">
            <label>Due date${isCycleTask ? ` <span class="hint">(this cycle)</span>` : ""} <input type="date" name="due_date" value="${task && task.due_date ? task.due_date : ""}"></label>
            <label>Due time <input type="time" name="due_time" value="${task && task.due_time ? task.due_time : ""}"></label>
          </div>

          <div class="field-row">
            <label>Priority
              <select name="priority">
                <option value="">None</option>
                ${levelOptions(task && task.priority)}
              </select>
            </label>
            <label>Effort
              <select name="effort">
                <option value="">None</option>
                ${levelOptions(task && task.effort)}
              </select>
            </label>
          </div>

          <div class="field-row">
            <label>Room
              <select name="area_id">
                <option value="">No room</option>
                ${areaOptions}
              </select>
            </label>
            <label>Reminder
              <select name="reminder">
                ${REMINDER_OPTIONS.map(([v, label]) => `<option value="${v}" ${reminderValue === v ? "selected" : ""}>${label}</option>`).join("")}
              </select>
            </label>
          </div>

          ${this._assigneesFieldHtml(task)}

          <label>Tags <span class="hint">(comma-separated)</span>
            <input type="text" name="tags" value="${escapeHtml(task && task.tags ? task.tags.join(", ") : "")}">
          </label>

          <fieldset class="recurrence-fieldset">
            <legend>
              <label style="display:inline-flex;flex-direction:row;align-items:center;gap:6px;">
                <input type="checkbox" id="repeat-toggle" ${task && task.recurrence ? "checked" : ""}> Repeats
              </label>
            </legend>
            <div id="recurrence-fields" style="${task && task.recurrence ? "" : "display:none"}">
              ${this._recurrenceFieldsHtml(task ? task.recurrence : null, task)}
            </div>
          </fieldset>

          ${state.mode === "create" ? this._newSubtasksFieldHtml() : this._subtasksSectionHtml(task)}

          ${state.mode === "edit" ? this._historySectionHtml() : ""}

          <div class="dialog-error" id="dialog-error"></div>

          <div class="dialog-actions">
            ${state.mode === "edit" ? `<button type="button" data-action="delete-from-dialog">Delete</button>` : ""}
            <span class="spacer"></span>
            <button type="button" data-action="close-dialog">Cancel</button>
            <button type="submit">${state.mode === "create" ? "Add task" : "Save"}</button>
          </div>
        </form>
      </dialog>
    `;
  }

  _assigneesFieldHtml(task) {
    const assigned = new Set((task && task.assignees) || []);
    // Keep anyone already assigned visible even if they've since been removed as a person.
    const people = [...this._people];
    for (const id of assigned) {
      if (!people.some((p) => p.entity_id === id)) people.push({ entity_id: id, name: this._personName(id), can_notify: false });
    }
    if (!people.length) {
      return `<div class="hint">Add people under Settings &rarr; People to assign tasks and send them reminders.</div>`;
    }
    const chips = people
      .map(
        (p) => `
        <label class="pick-chip" title="${p.can_notify ? "" : "No Home Assistant app found for this person - they won't get push reminders"}">
          <input type="checkbox" name="assignees" value="${escapeHtml(p.entity_id)}" ${assigned.has(p.entity_id) ? "checked" : ""}>
          ${escapeHtml(p.name)}${p.can_notify ? "" : ` <span class="hint">(no app)</span>`}
        </label>`
      )
      .join("");
    return `
      <div>
        <div class="section-label">Assigned to <span class="hint">- they get this task's reminders on their phone</span></div>
        <div class="chip-picker">${chips}</div>
      </div>`;
  }

  _newSubtasksFieldHtml() {
    return `
      <label>Subtasks <span class="hint">(one per line, optional)</span>
        <textarea name="new_subtasks" placeholder="e.g.&#10;Buy soap&#10;Restock towels"></textarea>
      </label>`;
  }

  _subtasksSectionHtml(task) {
    const subs = (task && task.sub_tasks) || [];
    const doneCount = subs.filter((s) => s.status === "completed").length;
    const rowsHtml =
      subs
        .map(
          (s) => `
        <div class="subtask-row">
          <input type="checkbox" data-role="toggle-subtask" data-sub-id="${s.id}" ${s.status === "completed" ? "checked" : ""}>
          <span class="${s.status === "completed" ? "completed" : ""}">${escapeHtml(s.title)}</span>
          <button type="button" class="icon-btn" data-action="delete-subtask" data-sub-id="${s.id}" title="Remove">
            <ha-icon icon="mdi:close"></ha-icon>
          </button>
        </div>`
        )
        .join("") || `<div class="hint">No subtasks yet.</div>`;

    return `
      <div class="subtasks-block">
        <div class="section-label">Subtasks ${subs.length ? `(${doneCount}/${subs.length} done)` : ""}</div>
        <div id="subtasks-list">${rowsHtml}</div>
        <div class="add-subtask-row">
          <input type="text" id="new-subtask-title" placeholder="Add a subtask...">
          <button type="button" data-action="add-subtask">Add</button>
        </div>
      </div>`;
  }

  _historySectionHtml() {
    return `
      <details id="history-details">
        <summary>History</summary>
        <div id="history-list" class="history-list">Loading...</div>
      </details>`;
  }

  // --- Dialog: recurrence sub-forms ---------------------------------------------

  _recurrenceFieldsHtml(recurrence, task) {
    const r = recurrence || {};
    const type = r.type || "interval";
    const startDate = r.start_date || (task && task.due_date) || todayIso();
    const endType = r.end_type || "none";
    const hourly = type === "interval" && r.interval_unit === "hours";

    return `
      <label>Repeat type
        <select id="recurrence-type" name="recurrence_type">
          ${RECURRENCE_TYPE_OPTIONS.map(([v, label]) => `<option value="${v}" ${type === v ? "selected" : ""}>${label}</option>`).join("")}
        </select>
      </label>
      <div id="recurrence-type-fields">${this._recurrenceTypeFieldsHtml(type, r)}</div>
      <label>Begin <input type="date" name="recurrence_start_date" value="${startDate}"></label>
      <div id="due-rule-block" style="${hourly ? "display:none" : ""}">
        <label>Due
          <select id="due-rule" name="due_rule">
            ${DUE_RULE_OPTIONS.map(([v, label]) => `<option value="${v}" ${(r.due_rule || "same_day") === v ? "selected" : ""}>${label}</option>`).join("")}
          </select>
        </label>
        <div id="due-rule-fields">${this._dueRuleFieldsHtml(r.due_rule || "same_day", r)}</div>
      </div>
      <label>Ends
        <select id="recurrence-end-type" name="recurrence_end_type">
          <option value="none" ${endType === "none" ? "selected" : ""}>Never</option>
          <option value="date" ${endType === "date" ? "selected" : ""}>On date</option>
          <option value="count" ${endType === "count" ? "selected" : ""}>After a number of times</option>
        </select>
      </label>
      <div id="recurrence-end-fields">${this._recurrenceEndFieldsHtml(endType, r)}</div>
      <div id="recurrence-preview" class="recurrence-preview" aria-live="polite"></div>
    `;
  }

  _dueRuleFieldsHtml(rule, r) {
    if (rule === "days_after") {
      return `<label>Days after it appears <input type="number" min="0" max="366" name="due_days" value="${r.due_days ?? 6}"></label>`;
    }
    if (rule === "weekday") {
      const weekday = r.due_weekday ?? 6;
      return `
        <label>Due on
          <select name="due_weekday">
            ${WEEKDAY_NAMES.map((label, i) => `<option value="${i}" ${Number(weekday) === i ? "selected" : ""}>${label}</option>`).join("")}
          </select>
        </label>`;
    }
    return "";
  }

  _recurrenceTypeFieldsHtml(type, r) {
    if (type === "weekly") {
      const weekdays = new Set(r.weekdays || []);
      return `
        <label>Every <input type="number" min="1" name="weekly_interval" value="${r.weekly_interval || 1}"> week(s) on:</label>
        <div class="weekday-picker">
          ${WEEKDAY_LABELS.map(
            (label, i) => `
            <label class="weekday-chip">
              <input type="checkbox" name="weekdays" value="${i}" ${weekdays.has(i) ? "checked" : ""}> ${label}
            </label>`
          ).join("")}
        </div>`;
    }
    if (type === "monthly_day") {
      const day = r.day_of_month || 1;
      const dayOptions = Array.from({ length: 31 }, (_, i) => i + 1)
        .map((d) => `<option value="${d}" ${String(day) === String(d) ? "selected" : ""}>${d}</option>`)
        .join("");
      return `
        <div class="field-row">
          <label>Every <input type="number" min="1" name="monthly_interval" value="${r.monthly_interval || 1}"> month(s) on</label>
          <label>&nbsp;
            <select name="day_of_month">
              ${dayOptions}
              <option value="last" ${day === "last" ? "selected" : ""}>Last day</option>
            </select>
          </label>
        </div>`;
    }
    if (type === "monthly_weekday") {
      const nth = r.nth_week || "1";
      const weekday = r.weekday ?? 0;
      return `
        <div class="field-row">
          <label>Every <input type="number" min="1" name="monthly_interval" value="${r.monthly_interval || 1}"> month(s) on the</label>
          <label>&nbsp;
            <select name="nth_week">
              ${NTH_WEEK_OPTIONS.map(([v, label]) => `<option value="${v}" ${nth === v ? "selected" : ""}>${label}</option>`).join("")}
            </select>
          </label>
          <label>&nbsp;
            <select name="weekday">
              ${WEEKDAY_LABELS.map((label, i) => `<option value="${i}" ${Number(weekday) === i ? "selected" : ""}>${label}</option>`).join("")}
            </select>
          </label>
        </div>`;
    }
    if (type === "yearly") {
      const [aMonth, aDay] = (r.anniversary || "01-01").split("-").map(Number);
      return `
        <div class="field-row">
          <label>Every <input type="number" min="1" name="yearly_interval" value="${r.yearly_interval || 1}"> year(s) on</label>
          <label>Month <input type="number" min="1" max="12" name="anniversary_month" value="${aMonth}"></label>
          <label>Day <input type="number" min="1" max="31" name="anniversary_day" value="${aDay}"></label>
        </div>`;
    }
    // Default / "interval"
    const unit = r.interval_unit || "days";
    return `
      <div class="field-row">
        <label>Every <input type="number" min="1" name="interval_value" value="${r.interval_value || 1}"></label>
        <label>&nbsp;
          <select name="interval_unit">
            ${["hours", "days", "weeks", "months", "years"].map((u) => `<option value="${u}" ${unit === u ? "selected" : ""}>${u}</option>`).join("")}
          </select>
        </label>
      </div>
      ${unit === "hours" ? `<label>Start time <input type="time" name="recurrence_start_time" value="${r.start_time || ""}"></label>` : ""}
    `;
  }

  _recurrenceEndFieldsHtml(endType, r) {
    if (endType === "date") {
      return `<label>End date <input type="date" name="recurrence_end_date" value="${r.end_date || ""}"></label>`;
    }
    if (endType === "count") {
      return `<label>Max repetitions <input type="number" min="1" name="recurrence_max_occurrences" value="${r.max_occurrences || 1}"></label>`;
    }
    return "";
  }

  _refreshRecurrenceTypeFields(values = {}) {
    const form = this._dialogRoot.querySelector("#task-form");
    const type = form.querySelector("#recurrence-type").value;
    form.querySelector("#recurrence-type-fields").innerHTML = this._recurrenceTypeFieldsHtml(type, values);
    // "Every N hours" reschedules on completion instead of using cycles,
    // so it has no due rule.
    const hourly = type === "interval" && values.interval_unit === "hours";
    form.querySelector("#due-rule-block").style.display = hourly ? "none" : "";
  }

  _refreshRecurrenceEndFields() {
    const form = this._dialogRoot.querySelector("#task-form");
    const endType = form.querySelector("#recurrence-end-type").value;
    form.querySelector("#recurrence-end-fields").innerHTML = this._recurrenceEndFieldsHtml(endType, {});
  }

  _refreshDueRuleFields() {
    const form = this._dialogRoot.querySelector("#task-form");
    const rule = form.querySelector("#due-rule").value;
    form.querySelector("#due-rule-fields").innerHTML = this._dueRuleFieldsHtml(rule, {});
  }

  // Asks the backend what the current repeat + due settings will actually
  // do (see handle_preview_recurrence in websocket_api.py) and shows it
  // under the settings, so there are no surprises after saving.
  async _refreshRecurrencePreview() {
    const form = this._dialogRoot.querySelector("#task-form");
    const el = form && form.querySelector("#recurrence-preview");
    if (!el) return;
    const token = ++this._previewToken;

    let spec;
    try {
      spec = this._recurrenceFromForm(form);
    } catch (err) {
      el.innerHTML = `<span class="preview-error">${escapeHtml(err.message || err)}</span>`;
      return;
    }
    if (!spec) {
      el.innerHTML = "";
      return;
    }
    try {
      const res = await this._callWS({ type: "better_todo_list/preview_recurrence", recurrence: spec });
      if (token === this._previewToken) el.innerHTML = this._previewHtml(res);
    } catch (err) {
      if (token === this._previewToken) el.innerHTML = `<span class="preview-error">${escapeHtml(err.message || err)}</span>`;
    }
  }

  _previewHtml(res) {
    if (!res.uses_cycles) {
      return `<span>Each time you complete it, it's rescheduled for the next time right away.</span>`;
    }
    if (!res.cycles.length) return `<span>This repeat has already ended - the task won't come back.</span>`;
    const [first, ...rest] = res.cycles;
    const span = (c) => `${formatDateShort(c.appears)} &rarr; due ${formatDateShort(c.due)}`;
    const lines = [
      first.appears <= res.today
        ? `<span><strong>This cycle:</strong> appeared ${span(first)}</span>`
        : `<span><strong>First appears</strong> ${span(first)}</span>`,
    ];
    if (rest.length) lines.push(`<span>Then: ${rest.map(span).join(", ")}, ...</span>`);
    lines.push(`<span>Completed tasks are hidden until their next cycle appears. One that isn't done by then is reset for the new cycle.</span>`);
    if (res.earlier_start) {
      lines.push(
        `<button type="button" class="link-btn" data-action="use-earlier-start" data-date="${res.earlier_start}">` +
          `Start the current cycle now instead (Begin ${escapeHtml(formatDateShort(res.earlier_start))})</button>`
      );
    }
    return lines.join("");
  }

  _recurrenceFromForm(form) {
    const repeatToggle = form.querySelector("#repeat-toggle");
    if (!repeatToggle || !repeatToggle.checked) return null;

    const type = form.querySelector("[name=recurrence_type]").value;
    const startDate = form.querySelector("[name=recurrence_start_date]").value;
    if (!startDate) throw new Error("Recurrence needs a Begin date.");
    const endType = form.querySelector("[name=recurrence_end_type]").value;

    const recurrence = { type, start_date: startDate, end_type: endType };

    if (type === "weekly") {
      recurrence.weekly_interval = Number(form.querySelector("[name=weekly_interval]").value) || 1;
      recurrence.weekdays = Array.from(form.querySelectorAll("[name=weekdays]:checked")).map((el) => Number(el.value));
      if (!recurrence.weekdays.length) throw new Error("Pick at least one weekday for a weekly recurrence.");
    } else if (type === "monthly_day") {
      recurrence.monthly_interval = Number(form.querySelector("[name=monthly_interval]").value) || 1;
      const dayVal = form.querySelector("[name=day_of_month]").value;
      recurrence.day_of_month = dayVal === "last" ? "last" : Number(dayVal);
    } else if (type === "monthly_weekday") {
      recurrence.monthly_interval = Number(form.querySelector("[name=monthly_interval]").value) || 1;
      recurrence.nth_week = form.querySelector("[name=nth_week]").value;
      recurrence.weekday = Number(form.querySelector("[name=weekday]").value);
    } else if (type === "yearly") {
      recurrence.yearly_interval = Number(form.querySelector("[name=yearly_interval]").value) || 1;
      const month = String(form.querySelector("[name=anniversary_month]").value).padStart(2, "0");
      const day = String(form.querySelector("[name=anniversary_day]").value).padStart(2, "0");
      recurrence.anniversary = `${month}-${day}`;
    } else {
      // "interval" is the only remaining type - its fields were already
      // rendered by _recurrenceTypeFieldsHtml's default branch.
      recurrence.interval_value = Number(form.querySelector("[name=interval_value]").value) || 1;
      recurrence.interval_unit = form.querySelector("[name=interval_unit]").value;
      if (recurrence.interval_unit === "hours") {
        const startTimeField = form.querySelector("[name=recurrence_start_time]");
        recurrence.start_time = startTimeField && startTimeField.value ? startTimeField.value : null;
      }
    }

    if (usesCycles(recurrence)) {
      recurrence.due_rule = form.querySelector("[name=due_rule]").value;
      if (recurrence.due_rule === "days_after") {
        const days = form.querySelector("[name=due_days]").value;
        if (days === "" || Number(days) < 0) throw new Error("Enter how many days after it appears it's due.");
        recurrence.due_days = Number(days);
      } else if (recurrence.due_rule === "weekday") {
        recurrence.due_weekday = Number(form.querySelector("[name=due_weekday]").value);
      }
    }

    if (endType === "date") {
      recurrence.end_date = form.querySelector("[name=recurrence_end_date]").value || null;
      if (!recurrence.end_date) throw new Error("Pick an end date, or change 'Ends' to Never.");
    } else if (endType === "count") {
      recurrence.max_occurrences = Number(form.querySelector("[name=recurrence_max_occurrences]").value) || 1;
    }

    return recurrence;
  }

  // --- Dialog: subtasks (edit mode - each action is an immediate save) ---------

  async _addSubtaskFromDialog() {
    const input = this._dialogRoot.querySelector("#new-subtask-title");
    const title = input.value.trim();
    if (!title) return;
    const { entryId, taskId } = this._dialogState;
    const { task } = await this._callWS({ type: "better_todo_list/add_sub_task", entry_id: entryId, task_id: taskId, title });
    this._dialogState.task = task;
    this._refreshSubtasksSection();
  }

  async _deleteSubtaskFromDialog(subTaskId) {
    const { entryId, taskId } = this._dialogState;
    const { task } = await this._callWS({ type: "better_todo_list/delete_sub_task", entry_id: entryId, task_id: taskId, sub_task_id: subTaskId });
    this._dialogState.task = task;
    this._refreshSubtasksSection();
  }

  _refreshSubtasksSection() {
    const container = this._dialogRoot.querySelector(".subtasks-block");
    if (container) container.outerHTML = this._subtasksSectionHtml(this._dialogState.task);
  }

  // --- Dialog: collecting the form for submit -----------------------------------

  _collectDialogPayload(form) {
    const title = form.querySelector("[name=title]").value.trim();
    if (!title) throw new Error("Title is required.");

    const notes = form.querySelector("[name=notes]").value.trim() || null;
    const due_date = form.querySelector("[name=due_date]").value || null;
    const due_time = form.querySelector("[name=due_time]").value || null;
    const priority = form.querySelector("[name=priority]").value || null;
    const effort = form.querySelector("[name=effort]").value || null;
    const tags = (form.querySelector("[name=tags]").value || "")
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
    const area_id = form.querySelector("[name=area_id]").value || null;
    const assignees = Array.from(form.querySelectorAll("[name=assignees]:checked")).map((el) => el.value);
    const reminderValue = form.querySelector("[name=reminder]").value;
    const reminder = reminderValue === "" ? null : Number(reminderValue);

    const entryIdField = form.querySelector("[name=entry_id]");
    const entry_id = entryIdField ? entryIdField.value : this._dialogState.entryId;

    const recurrence = this._recurrenceFromForm(form);

    const newSubtasksField = form.querySelector("[name=new_subtasks]");
    const newSubtasks = newSubtasksField
      ? newSubtasksField.value.split("\n").map((s) => s.trim()).filter(Boolean)
      : [];

    return { title, notes, due_date, due_time, priority, effort, tags, area_id, assignees, reminder, entry_id, recurrence, newSubtasks };
  }

  // --- Error display -----------------------------------------------------------

  _showError(err) {
    console.error("[better-todo-list-card]", err);
    this._groupsRoot.innerHTML = `<div class="error-state">Error: ${escapeHtml(err.message || String(err))}</div>`;
  }

  _showToastError(err) {
    console.error("[better-todo-list-card]", err);
    alert(err.message || String(err));
  }

  _showDialogError(err) {
    console.error("[better-todo-list-card]", err);
    const el = this._dialogRoot.querySelector("#dialog-error");
    if (el) el.textContent = err.message || String(err);
    else alert(err.message || String(err));
  }

  _clearDialogError() {
    const el = this._dialogRoot.querySelector("#dialog-error");
    if (el) el.textContent = "";
  }
}

// Guarded against double-registration: this file can legitimately end up
// requested twice on the same page - once via Home Assistant's
// add_extra_js_url (an unconditional <script type="module"> on every
// page load) and again via this integration's Lovelace "resource" entry
// (loaded independently by Lovelace's own dashboard bootstrap - see
// _async_register_lovelace_resource in __init__.py for why both exist).
// customElements.define() throws if called twice for the same tag name,
// which would otherwise abort this whole script partway through on the
// second load and could disrupt the "Add Card" picker's card catalog.
if (!customElements.get("better-todo-list-card")) {
  customElements.define("better-todo-list-card", BetterTodoListCard);

  // Registers the card with HA's Lovelace card picker UI so it shows up
  // with a name/description instead of just its raw tag name.
  window.customCards = window.customCards || [];
  window.customCards.push({
    // NOTE: this "type" must carry the "custom:" prefix (unlike the tag
    // name passed to customElements.define above) - without it, HA's card
    // picker dialog silently won't list the card, even though `type:
    // custom:better-todo-list-card` still works fine typed directly into a
    // dashboard's YAML.
    type: "custom:better-todo-list-card",
    name: "Better Todo List",
    description: "A room-aware todo list with priorities, effort, tags, subtasks, recurrence, and reminders.",
  });
}
