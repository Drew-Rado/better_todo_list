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
 * This file avoids that by keeping three SEPARATE, independently-rendered
 * regions instead of one big one:
 *   #toolbar-root  - the search box / filters (rendered once)
 *   #groups-root   - the task list itself (re-rendered after data changes)
 *   #dialog-root   - the add/edit task popup (only rendered while open)
 * As long as you're typing in a field, nothing re-renders that field's
 * container until you submit or trigger a structural change (like
 * switching the recurrence type). See _onSubmit/_openDialog/_closeDialog.
 *
 * DEBUGGING TIP: open your browser's DevTools (F12) -> Console tab for any
 * JS errors, and the Network -> WS tab to watch the actual
 * better_todo_list/* messages and responses live while you use the card.
 */

// --- Constants ----------------------------------------------------------------

// 0 = Monday .. 6 = Sunday, matching Python's `date.weekday()` used by
// recurrence.py on the backend - keep this ordering in sync with that file.
const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

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

const GROUP_LABELS = { room: "Group by room", list: "Group by list", none: "No grouping" };

const PRIORITY_META = {
  low: { label: "Low", color: "#4caf50" },
  medium: { label: "Medium", color: "#ff9800" },
  high: { label: "High", color: "#f44336" },
};

const PRIORITY_SORT_ORDER = { high: 0, medium: 1, low: 2 };

// --- Small pure helper functions ------------------------------------------------

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function formatDateHuman(isoDate) {
  const [y, m, d] = isoDate.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
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
  if (entry.action === "recurred") return `Rescheduled to next occurrence (${entry.new || "?"})`;
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

// --- CSS ------------------------------------------------------------------------
// Uses Home Assistant's theme CSS variables (--primary-color etc.) so the
// card matches the user's light/dark theme automatically instead of
// hardcoding colors.

const CARD_CSS = `
  :host { display: block; }
  ha-card { padding: 8px 0 12px; }
  .toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 0 16px 8px; }
  .toolbar input[type="search"] { flex: 1 1 140px; min-width: 100px; padding: 6px 8px; border-radius: 6px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); }
  .toolbar select { padding: 6px 8px; border-radius: 6px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); }
  .toolbar label.show-completed { display: flex; align-items: center; gap: 4px; font-size: 0.9em; color: var(--secondary-text-color); white-space: nowrap; }
  .toolbar button { border: none; border-radius: 6px; background: var(--primary-color); color: var(--text-primary-color, #fff); padding: 6px 12px; cursor: pointer; font-size: 0.9em; }
  .toolbar button.icon-btn { background: transparent; color: var(--secondary-text-color); padding: 4px 8px; font-size: 1.1em; }

  .empty-state, .error-state { padding: 24px 16px; text-align: center; color: var(--secondary-text-color); }
  .error-state { color: var(--error-color, #db4437); }

  .group { margin: 8px 0; }
  .group-header { display: flex; align-items: center; gap: 8px; padding: 4px 16px; font-weight: 500; color: var(--secondary-text-color); text-transform: uppercase; font-size: 0.78em; letter-spacing: .04em; }
  .group-header .count { background: var(--divider-color); border-radius: 10px; padding: 0 6px; font-size: 0.9em; }

  .task-row { padding: 8px 16px; border-bottom: 1px solid var(--divider-color); }
  .task-row:last-child { border-bottom: none; }
  .task-row.completed .task-title { text-decoration: line-through; color: var(--secondary-text-color); }
  .task-row-main { display: flex; align-items: flex-start; gap: 10px; }
  .task-check { margin-top: 3px; width: 18px; height: 18px; flex: none; }
  .task-main { flex: 1; min-width: 0; cursor: pointer; }
  .task-title-row { display: flex; align-items: center; gap: 6px; }
  .task-title { font-size: 1em; color: var(--primary-text-color); word-break: break-word; }
  .prio-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
  .recur-icon { --mdc-icon-size: 16px; color: var(--secondary-text-color); }
  .task-meta { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 2px; font-size: 0.82em; color: var(--secondary-text-color); align-items: center; }
  .task-meta .due.overdue { color: var(--error-color, #db4437); font-weight: 500; }
  .chip { background: var(--divider-color); border-radius: 10px; padding: 1px 8px; }
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
  .field-row > label { flex: 1; }
  fieldset.recurrence-fieldset { border: 1px solid var(--divider-color); border-radius: 8px; padding: 10px; display: flex; flex-direction: column; gap: 10px; }
  fieldset.recurrence-fieldset legend { padding: 0 4px; font-size: 0.9em; color: var(--primary-text-color); }
  .weekday-picker { display: flex; flex-wrap: wrap; gap: 6px; }
  .weekday-chip { flex-direction: row !important; align-items: center; gap: 4px !important; border: 1px solid var(--divider-color); border-radius: 14px; padding: 3px 8px; font-size: 0.85em; }
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
    this._entryIds = [];
    this._tasksByEntry = {};
    this._groupBy = "room";
    this._showCompleted = false;
    this._searchText = "";
    this._loaded = false;
    this._dialogState = null;
    this._searchDebounceTimer = null;

    this.shadowRoot.innerHTML = `
      <style>${CARD_CSS}</style>
      <ha-card>
        <div id="toolbar-root"></div>
        <div id="groups-root"><div class="empty-state">Loading...</div></div>
      </ha-card>
      <div id="dialog-root"></div>
    `;
    this._toolbarRoot = this.shadowRoot.getElementById("toolbar-root");
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
    if (config.group_by) this._groupBy = config.group_by;
    if (config.show_completed) this._showCompleted = true;

    const cardEl = this.shadowRoot.querySelector("ha-card");
    if (cardEl) cardEl.header = config.title || "";
    if (this._toolbarRoot) this._renderToolbar();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._loaded && this.isConnected) {
      this._loaded = true;
      this._loadAll();
    }
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

  // --- Data loading ---------------------------------------------------------------

  async _callWS(msg) {
    if (!this._hass) throw new Error("Home Assistant connection isn't ready yet.");
    return this._hass.callWS(msg);
  }

  async _loadAll() {
    try {
      const [{ lists }, { areas }] = await Promise.all([
        this._callWS({ type: "better_todo_list/get_lists" }),
        this._callWS({ type: "better_todo_list/get_areas" }),
      ]);
      this._lists = lists;
      this._areas = areas;
      this._entryIds = this._resolveEntryIds(lists);
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
  }

  _listName(entryId) {
    const entry = this._lists.find((l) => l.entry_id === entryId);
    return entry ? entry.name : entryId;
  }

  _areaName(areaId) {
    const area = this._areas.find((a) => a.area_id === areaId);
    return area ? area.name : null;
  }

  _findTask(entryId, taskId) {
    return (this._tasksByEntry[entryId] || []).find((t) => t.id === taskId) || null;
  }

  // --- Toolbar ---------------------------------------------------------------

  _renderToolbar() {
    const groupOptions = Object.keys(GROUP_LABELS)
      .map((g) => `<option value="${g}" ${this._groupBy === g ? "selected" : ""}>${GROUP_LABELS[g]}</option>`)
      .join("");
    this._toolbarRoot.innerHTML = `
      <div class="toolbar">
        <input type="search" id="search" placeholder="Search tasks..." value="${escapeHtml(this._searchText)}">
        <label class="show-completed">
          <input type="checkbox" id="show-completed" ${this._showCompleted ? "checked" : ""}>
          Show completed
        </label>
        <select id="group-by">${groupOptions}</select>
        <button type="button" class="icon-btn" data-action="refresh" title="Refresh">&#8635;</button>
        <button type="button" data-action="add-task">+ Add task</button>
      </div>
    `;
  }

  // --- Task list -------------------------------------------------------------

  _flattenVisibleTasks() {
    const search = (this._searchText || "").trim().toLowerCase();
    const rows = [];
    for (const entryId of this._entryIds) {
      for (const task of this._tasksByEntry[entryId] || []) {
        if (!this._showCompleted && task.status === "completed") continue;
        if (search) {
          const haystack = [task.title, task.notes, ...(task.tags || [])].filter(Boolean).join(" ").toLowerCase();
          if (!haystack.includes(search)) continue;
        }
        rows.push({ entryId, task });
      }
    }
    rows.sort((a, b) => compareTasks(a.task, b.task));
    return rows;
  }

  _groupKey(row) {
    if (this._groupBy === "list") {
      return { id: row.entryId, label: this._listName(row.entryId) };
    }
    if (this._groupBy === "room") {
      const areaId = row.task.area_id;
      if (!areaId) return { id: "__no_room__", label: "No room" };
      return { id: areaId, label: this._areaName(areaId) || "Unknown room" };
    }
    return { id: "__all__", label: "All tasks" };
  }

  _groupRows(rows) {
    const groups = new Map();
    for (const row of rows) {
      const key = this._groupKey(row);
      if (!groups.has(key.id)) groups.set(key.id, { label: key.label, rows: [] });
      groups.get(key.id).rows.push(row);
    }
    return [...groups.values()].sort((a, b) => a.label.localeCompare(b.label));
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
      this._groupsRoot.innerHTML = `<div class="empty-state">No tasks to show.</div>`;
      return;
    }

    const groups = this._groupRows(rows);
    this._groupsRoot.innerHTML = groups
      .map(
        (g) => `
        <div class="group">
          <div class="group-header">${escapeHtml(g.label)} <span class="count">${g.rows.length}</span></div>
          <div class="task-list">
            ${g.rows.map((r) => this._taskRowHtml(r.entryId, r.task)).join("")}
          </div>
        </div>`
      )
      .join("");
  }

  _taskRowHtml(entryId, task) {
    const overdue = isOverdue(task);
    const subLabel = subtaskProgressLabel(task);
    const dueLabel = formatDue(task.due_date, task.due_time);
    const tagsHtml = (task.tags || []).map((t) => `<span class="chip">${escapeHtml(t)}</span>`).join("");
    const prio = task.priority ? PRIORITY_META[task.priority] : null;
    const notes = (task.notes || "").trim();

    return `
      <div class="task-row ${task.status === "completed" ? "completed" : ""}">
        <div class="task-row-main">
          <input type="checkbox" class="task-check" data-role="toggle-task" data-task-id="${task.id}" data-entry-id="${entryId}" ${task.status === "completed" ? "checked" : ""}>
          <div class="task-main" data-action="open" data-task-id="${task.id}" data-entry-id="${entryId}">
            <div class="task-title-row">
              ${prio ? `<span class="prio-dot" style="background:${prio.color}" title="${prio.label} priority"></span>` : ""}
              <span class="task-title">${escapeHtml(task.title)}</span>
              ${task.recurrence ? `<ha-icon icon="mdi:repeat" class="recur-icon" title="Repeats"></ha-icon>` : ""}
            </div>
            <div class="task-meta">
              ${dueLabel ? `<span class="due ${overdue ? "overdue" : ""}">${escapeHtml(dueLabel)}</span>` : ""}
              ${subLabel ? `<span class="subprogress">${subLabel}</span>` : ""}
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
      this._renderGroups();
      return;
    }
    if (target.id === "group-by") {
      this._groupBy = target.value;
      this._renderGroups();
      return;
    }
    if (target.dataset && target.dataset.role === "toggle-task") {
      const { taskId, entryId } = target.dataset;
      const wasChecked = target.checked;
      try {
        const command = wasChecked ? "better_todo_list/complete_task" : "better_todo_list/reopen_task";
        await this._callWS({ type: command, entry_id: entryId, task_id: taskId });
        await this._refreshTasks();
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
      return;
    }
    if (target.id === "recurrence-type") {
      this._refreshRecurrenceTypeFields();
      return;
    }
    if (target.id === "recurrence-end-type") {
      this._refreshRecurrenceEndFields();
    }
  }

  _onInput(e) {
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

    try {
      if (this._dialogState.mode === "create") {
        const { task } = await this._callWS({
          type: "better_todo_list/create_task",
          entry_id: payload.entry_id,
          title: payload.title,
          notes: payload.notes,
          due_date: payload.due_date,
          due_time: payload.due_time,
          priority: payload.priority,
          tags: payload.tags,
          area_id: payload.area_id,
          recurrence: payload.recurrence,
        });
        for (const title of payload.newSubtasks) {
          await this._callWS({ type: "better_todo_list/add_sub_task", entry_id: payload.entry_id, task_id: task.id, title });
        }
      } else {
        await this._callWS({
          type: "better_todo_list/update_task",
          entry_id: this._dialogState.entryId,
          task_id: this._dialogState.taskId,
          title: payload.title,
          notes: payload.notes,
          due_date: payload.due_date,
          due_time: payload.due_time,
          priority: payload.priority,
          tags: payload.tags,
          area_id: payload.area_id,
          recurrence: payload.recurrence,
        });
      }
      this._closeDialog();
      await this._refreshTasks();
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
  }

  _closeDialog() {
    const dialogEl = this._dialogRoot.querySelector("dialog");
    if (dialogEl && dialogEl.open) dialogEl.close();
    this._dialogRoot.innerHTML = "";
    this._dialogState = null;
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
    return [...history]
      .reverse()
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

    const areaOptions = this._areas
      .map((a) => `<option value="${a.area_id}" ${task && task.area_id === a.area_id ? "selected" : ""}>${escapeHtml(a.name)}</option>`)
      .join("");

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
            <label>Due date <input type="date" name="due_date" value="${task && task.due_date ? task.due_date : ""}"></label>
            <label>Due time <input type="time" name="due_time" value="${task && task.due_time ? task.due_time : ""}"></label>
          </div>

          <div class="field-row">
            <label>Priority
              <select name="priority">
                <option value="">None</option>
                <option value="low" ${task && task.priority === "low" ? "selected" : ""}>Low</option>
                <option value="medium" ${task && task.priority === "medium" ? "selected" : ""}>Medium</option>
                <option value="high" ${task && task.priority === "high" ? "selected" : ""}>High</option>
              </select>
            </label>
            <label>Room
              <select name="area_id">
                <option value="">No room</option>
                ${areaOptions}
              </select>
            </label>
          </div>

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

    return `
      <label>Repeat type
        <select id="recurrence-type" name="recurrence_type">
          ${RECURRENCE_TYPE_OPTIONS.map(([v, label]) => `<option value="${v}" ${type === v ? "selected" : ""}>${label}</option>`).join("")}
        </select>
      </label>
      <div id="recurrence-type-fields">${this._recurrenceTypeFieldsHtml(type, r)}</div>
      <label>Begin <input type="date" name="recurrence_start_date" value="${startDate}"></label>
      <label>Ends
        <select id="recurrence-end-type" name="recurrence_end_type">
          <option value="none" ${endType === "none" ? "selected" : ""}>Never</option>
          <option value="date" ${endType === "date" ? "selected" : ""}>On date</option>
          <option value="count" ${endType === "count" ? "selected" : ""}>After a number of times</option>
        </select>
      </label>
      <div id="recurrence-end-fields">${this._recurrenceEndFieldsHtml(endType, r)}</div>
    `;
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

  _refreshRecurrenceTypeFields() {
    const form = this._dialogRoot.querySelector("#task-form");
    const type = form.querySelector("#recurrence-type").value;
    form.querySelector("#recurrence-type-fields").innerHTML = this._recurrenceTypeFieldsHtml(type, {});
  }

  _refreshRecurrenceEndFields() {
    const form = this._dialogRoot.querySelector("#task-form");
    const endType = form.querySelector("#recurrence-end-type").value;
    form.querySelector("#recurrence-end-fields").innerHTML = this._recurrenceEndFieldsHtml(endType, {});
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
    const tags = (form.querySelector("[name=tags]").value || "")
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
    const area_id = form.querySelector("[name=area_id]").value || null;

    const entryIdField = form.querySelector("[name=entry_id]");
    const entry_id = entryIdField ? entryIdField.value : this._dialogState.entryId;

    const recurrence = this._recurrenceFromForm(form);

    const newSubtasksField = form.querySelector("[name=new_subtasks]");
    const newSubtasks = newSubtasksField
      ? newSubtasksField.value.split("\n").map((s) => s.trim()).filter(Boolean)
      : [];

    return { title, notes, due_date, due_time, priority, tags, area_id, entry_id, recurrence, newSubtasks };
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

customElements.define("better-todo-list-card", BetterTodoListCard);

// Registers the card with HA's Lovelace card picker UI so it shows up
// with a name/description instead of just its raw tag name.
window.customCards = window.customCards || [];
window.customCards.push({
  // NOTE: this "type" must carry the "custom:" prefix (unlike the tag name
  // passed to customElements.define above) - without it, HA's card picker
  // dialog silently won't list the card, even though `type:
  // custom:better-todo-list-card` still works fine typed directly into a
  // dashboard's YAML.
  type: "custom:better-todo-list-card",
  name: "Better Todo List",
  description: "A room-aware todo list with priorities, tags, subtasks, and recurrence.",
});
