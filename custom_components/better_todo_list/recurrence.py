"""Recurrence math for Better Todo List.

Every function in this file is a *pure* function - it only takes plain
Python values (dates, dicts, strings) in and returns plain values out.
There is no Home Assistant import here on purpose: that means you (or I,
when helping you debug) can run this file directly with plain Python to
sanity-check a recurrence rule without needing a running Home Assistant
instance. Try it:

    python custom_components/better_todo_list/recurrence.py

...which runs the small self-test block at the bottom and prints results.

--- The recurrence dict shape (also documented in store.py) ---

    {
        "type": "interval" | "weekly" | "monthly_day" | "monthly_weekday" | "yearly",

        # type == "interval"
        "interval_unit": "hours" | "days" | "weeks" | "months" | "years",
        "interval_value": <int, N>,

        # type == "weekly"
        "weekly_interval": <int, every N weeks>,
        "weekdays": [0, 2, 4],   # 0=Monday .. 6=Sunday, any combination

        # type == "monthly_day"
        "monthly_interval": <int, every N months>,
        "day_of_month": 1-31 or "last",

        # type == "monthly_weekday"
        "monthly_interval": <int, every N months>,
        "nth_week": "1" | "2" | "3" | "4" | "last",
        "weekday": 0-6,   # 0=Monday .. 6=Sunday

        # type == "yearly"
        "yearly_interval": <int, every N years>,
        "anniversary": "MM-DD",

        # Common to every type
        "start_date": "YYYY-MM-DD",       # when the pattern begins ("Begin")
        "end_type": "none" | "date" | "count",
        "end_date": "YYYY-MM-DD" | None,
        "max_occurrences": <int> | None,
        "occurrences_count": <int>,       # how many occurrences have happened so far

        # When each cycle is due (every type except "interval: hours")
        "due_rule": "same_day" | "days_after" | "weekday" | "end_of_month" | "before_next",
        "due_days": <int>,                # due_rule == "days_after"
        "due_weekday": 0-6,               # due_rule == "weekday"
    }

--- Cycles: "appears on" vs "due on" ---

For every type except "interval: hours", the pattern above decides when a
task *appears* on the list (a "cycle" starts), and `due_rule` decides when
that cycle is due. E.g. "weekly on Monday" + "before_next" appears every
Monday and is due the following Sunday; "monthly on the 1st" +
"end_of_month" appears on the 1st and is due on the last day of the month.
store.py hides a completed task until its next cycle appears, and resets an
unfinished one when that happens - see plan_cycle() and advance_cycle().

"interval: hours" keeps the original behavior instead: completing it
immediately reschedules it N hours later (compute_next_occurrence()).
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, NamedTuple, Optional

try:
    # Normal case: imported as part of the custom_components.better_todo_list
    # package (i.e. by Home Assistant, or `python -m custom_components...`).
    from .const import (
        DUE_RULE_BEFORE_NEXT,
        DUE_RULE_DAYS_AFTER,
        DUE_RULE_END_OF_MONTH,
        DUE_RULE_SAME_DAY,
        DUE_RULE_WEEKDAY,
        DUE_RULES,
        RECURRENCE_END_COUNT,
        RECURRENCE_END_DATE,
        RECURRENCE_INTERVAL,
        RECURRENCE_MONTHLY_DAY,
        RECURRENCE_MONTHLY_WEEKDAY,
        RECURRENCE_WEEKLY,
        RECURRENCE_YEARLY,
    )
except ImportError:
    # Running this file directly (`python recurrence.py`) - there's no
    # parent package for a relative import to resolve against. const.py has
    # no Home Assistant dependencies, so we can just import it as a
    # standalone module instead; Python already put this file's directory
    # on sys.path for us.
    from const import (  # type: ignore[no-redef]
        DUE_RULE_BEFORE_NEXT,
        DUE_RULE_DAYS_AFTER,
        DUE_RULE_END_OF_MONTH,
        DUE_RULE_SAME_DAY,
        DUE_RULE_WEEKDAY,
        DUE_RULES,
        RECURRENCE_END_COUNT,
        RECURRENCE_END_DATE,
        RECURRENCE_INTERVAL,
        RECURRENCE_MONTHLY_DAY,
        RECURRENCE_MONTHLY_WEEKDAY,
        RECURRENCE_WEEKLY,
        RECURRENCE_YEARLY,
    )

# Hard safety cap on how many days we'll step forward while searching for
# the next occurrence. A well-formed recurrence resolves in well under 400
# steps; this just guarantees we can never hang HA in an infinite loop if a
# recurrence spec somehow slips past validation (e.g. an "every 0 weeks" bug).
_MAX_DAY_STEPS = 20 * 365


class RecurrenceError(ValueError):
    """A recurrence spec is malformed (bad shape, out-of-range value, etc)."""


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _parse_time(value: str | None) -> time | None:
    if not value:
        return None
    return time.fromisoformat(value)


def _last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _months_between(start: date, candidate: date) -> int:
    """How many whole calendar months lie between two dates (can be negative)."""
    return (candidate.year - start.year) * 12 + (candidate.month - start.month)


def _nth_weekday_of_month(year: int, month: int, weekday: int, nth: str) -> date:
    """The date of the Nth (or last) given weekday in a month.

    weekday: 0=Monday .. 6=Sunday. nth: "1".."4" or "last". Every month has
    at least four of each weekday, so "1".."4" always exist; only "last"
    needs special handling for months with a 5th occurrence.
    """
    first_of_month = date(year, month, 1)
    first_occurrence_day = 1 + (weekday - first_of_month.weekday()) % 7
    if nth == "last":
        last_day = _last_day_of_month(year, month)
        day = first_occurrence_day
        while day + 7 <= last_day:
            day += 7
        return date(year, month, day)
    day = first_occurrence_day + (int(nth) - 1) * 7
    return date(year, month, day)


# --- One "matches this candidate date?" predicate builder per recurrence type -----


def _predicate_interval(recurrence: dict[str, Any], start_date: date) -> Callable[[date], bool]:
    unit = recurrence["interval_unit"]
    value = int(recurrence["interval_value"])
    if value < 1:
        raise RecurrenceError("interval_value must be >= 1")

    if unit == "days":
        return lambda d: (d - start_date).days % value == 0
    if unit == "weeks":
        return lambda d: (d - start_date).days % (value * 7) == 0
    if unit == "months":
        def _match_months(d: date) -> bool:
            months_since = _months_between(start_date, d)
            if months_since < 0 or months_since % value != 0:
                return False
            target_day = min(start_date.day, _last_day_of_month(d.year, d.month))
            return d.day == target_day
        return _match_months
    if unit == "years":
        def _match_years(d: date) -> bool:
            years_since = d.year - start_date.year
            if years_since < 0 or years_since % value != 0:
                return False
            target_day = min(start_date.day, _last_day_of_month(d.year, start_date.month))
            return d.month == start_date.month and d.day == target_day
        return _match_years
    raise RecurrenceError(f"Unknown interval_unit: {unit!r}")


def _predicate_weekly(recurrence: dict[str, Any], start_date: date) -> Callable[[date], bool]:
    interval = int(recurrence["weekly_interval"])
    weekdays = set(recurrence["weekdays"])
    if interval < 1:
        raise RecurrenceError("weekly_interval must be >= 1")
    if not weekdays:
        raise RecurrenceError("weekly recurrence needs at least one weekday selected")

    start_week = start_date - timedelta(days=start_date.weekday())

    def _match(d: date) -> bool:
        if d.weekday() not in weekdays:
            return False
        week_start = d - timedelta(days=d.weekday())
        weeks_since = (week_start - start_week).days // 7
        return weeks_since >= 0 and weeks_since % interval == 0

    return _match


def _predicate_monthly_day(recurrence: dict[str, Any], start_date: date) -> Callable[[date], bool]:
    interval = int(recurrence["monthly_interval"])
    day_of_month = recurrence["day_of_month"]
    if interval < 1:
        raise RecurrenceError("monthly_interval must be >= 1")

    def _match(d: date) -> bool:
        months_since = _months_between(start_date, d)
        if months_since < 0 or months_since % interval != 0:
            return False
        last_day = _last_day_of_month(d.year, d.month)
        # "31st" in a 30-day month clamps to that month's last day rather
        # than being skipped - simpler to reason about than silently
        # vanishing some months.
        target_day = last_day if day_of_month == "last" else min(int(day_of_month), last_day)
        return d.day == target_day

    return _match


def _predicate_monthly_weekday(recurrence: dict[str, Any], start_date: date) -> Callable[[date], bool]:
    interval = int(recurrence["monthly_interval"])
    nth_week = str(recurrence["nth_week"])
    weekday = int(recurrence["weekday"])
    if interval < 1:
        raise RecurrenceError("monthly_interval must be >= 1")

    def _match(d: date) -> bool:
        months_since = _months_between(start_date, d)
        if months_since < 0 or months_since % interval != 0:
            return False
        return d == _nth_weekday_of_month(d.year, d.month, weekday, nth_week)

    return _match


def _predicate_yearly(recurrence: dict[str, Any], start_date: date) -> Callable[[date], bool]:
    interval = int(recurrence["yearly_interval"])
    month_str, day_str = recurrence["anniversary"].split("-")
    month, day = int(month_str), int(day_str)
    if interval < 1:
        raise RecurrenceError("yearly_interval must be >= 1")

    def _match(d: date) -> bool:
        years_since = d.year - start_date.year
        if years_since < 0 or years_since % interval != 0:
            return False
        # Anniversaries on Feb 29 clamp to Feb 28 in non-leap years.
        target_day = min(day, _last_day_of_month(d.year, month))
        return d.month == month and d.day == target_day

    return _match


_PREDICATE_BUILDERS: dict[str, Callable[[dict[str, Any], date], Callable[[date], bool]]] = {
    RECURRENCE_INTERVAL: _predicate_interval,
    RECURRENCE_WEEKLY: _predicate_weekly,
    RECURRENCE_MONTHLY_DAY: _predicate_monthly_day,
    RECURRENCE_MONTHLY_WEEKDAY: _predicate_monthly_weekday,
    RECURRENCE_YEARLY: _predicate_yearly,
}


def _find_date_matching(predicate: Callable[[date], bool], search_from: date) -> date:
    for offset in range(_MAX_DAY_STEPS):
        candidate = search_from + timedelta(days=offset)
        if predicate(candidate):
            return candidate
    raise RecurrenceError(
        "Could not find a matching occurrence within "
        f"{_MAX_DAY_STEPS} days - check the recurrence settings for this task."
    )


def _build_predicate(recurrence: dict[str, Any]) -> tuple[Callable[[date], bool], date]:
    start_date = _parse_date(recurrence["start_date"])
    rtype = recurrence["type"]
    if rtype not in _PREDICATE_BUILDERS:
        raise RecurrenceError(f"Unknown recurrence type: {rtype!r}")
    return _PREDICATE_BUILDERS[rtype](recurrence, start_date), start_date


def _find_next_date(recurrence: dict[str, Any], on_or_after: date) -> date:
    predicate, start_date = _build_predicate(recurrence)
    return _find_date_matching(predicate, max(on_or_after, start_date))


def _is_hourly_interval(recurrence: dict[str, Any]) -> bool:
    return recurrence["type"] == RECURRENCE_INTERVAL and recurrence["interval_unit"] == "hours"


def uses_cycles(recurrence: dict[str, Any] | None) -> bool:
    """Whether this recurrence uses the appear/due cycle model (every type
    except "interval: hours" - see the module docstring)."""
    return recurrence is not None and not _is_hourly_interval(recurrence)


# --- Cycles (appear date + due date) ---------------------------------------------


class CyclePlan(NamedTuple):
    """Where a cycle-based task stands. `cycle_start` is the date the
    current cycle appeared (None = its first cycle hasn't appeared yet),
    `due_date` that cycle's due date (or the first cycle's, if it hasn't
    appeared yet), `next_cycle_start` when the next cycle appears (None =
    the recurrence has ended), and `occurrences_count` how many cycles have
    appeared so far."""

    cycle_start: Optional[date]
    due_date: Optional[date]
    next_cycle_start: Optional[date]
    occurrences_count: int


def _end_date(recurrence: dict[str, Any]) -> date | None:
    if recurrence.get("end_type") != RECURRENCE_END_DATE or not recurrence.get("end_date"):
        return None
    return _parse_date(recurrence["end_date"])


def count_exhausted(recurrence: dict[str, Any], started_count: int) -> bool:
    if recurrence.get("end_type") != RECURRENCE_END_COUNT:
        return False
    max_occurrences = recurrence.get("max_occurrences")
    return max_occurrences is not None and started_count >= int(max_occurrences)


def validate_due_rule(recurrence: dict[str, Any]) -> None:
    """Raise RecurrenceError if the due rule fields are malformed."""
    rule = recurrence.get("due_rule", DUE_RULE_SAME_DAY)
    if rule not in DUE_RULES:
        raise RecurrenceError(f"Unknown due_rule: {rule!r}")
    if rule == DUE_RULE_DAYS_AFTER:
        try:
            days = int(recurrence.get("due_days"))
        except (TypeError, ValueError) as err:
            raise RecurrenceError("due_days must be a whole number") from err
        if not 0 <= days <= 366:
            raise RecurrenceError("due_days must be between 0 and 366")
    if rule == DUE_RULE_WEEKDAY:
        try:
            weekday = int(recurrence.get("due_weekday"))
        except (TypeError, ValueError) as err:
            raise RecurrenceError("due_weekday must be 0 (Monday) to 6 (Sunday)") from err
        if not 0 <= weekday <= 6:
            raise RecurrenceError("due_weekday must be 0 (Monday) to 6 (Sunday)")


def first_appearance(recurrence: dict[str, Any]) -> date | None:
    """The first date the pattern matches on/after its start date, or None
    if that's already past the recurrence's end date."""
    first = _find_next_date(recurrence, _parse_date(recurrence["start_date"]))
    end = _end_date(recurrence)
    return None if end is not None and first > end else first


def next_appearance_after(recurrence: dict[str, Any], after: date) -> date | None:
    """The next date strictly after `after` the pattern matches, or None if
    that's past the recurrence's end date."""
    nxt = _find_next_date(recurrence, after + timedelta(days=1))
    end = _end_date(recurrence)
    return None if end is not None and nxt > end else nxt


def latest_appearance_on_or_before(recurrence: dict[str, Any], day: date) -> date | None:
    """The most recent date on/before `day` (and on/after the start date,
    and on/before any end date) the pattern matches, or None."""
    predicate, start_date = _build_predicate(recurrence)
    end = _end_date(recurrence)
    candidate = min(day, end) if end is not None else day
    for _ in range(_MAX_DAY_STEPS):
        if candidate < start_date:
            return None
        if predicate(candidate):
            return candidate
        candidate -= timedelta(days=1)
    return None


def due_date_for_cycle(recurrence: dict[str, Any], cycle_start: date) -> date:
    """The due date of the cycle that appears on `cycle_start`."""
    rule = recurrence.get("due_rule", DUE_RULE_SAME_DAY)
    if rule == DUE_RULE_DAYS_AFTER:
        return cycle_start + timedelta(days=int(recurrence.get("due_days", 0)))
    if rule == DUE_RULE_WEEKDAY:
        return cycle_start + timedelta(days=(int(recurrence["due_weekday"]) - cycle_start.weekday()) % 7)
    if rule == DUE_RULE_END_OF_MONTH:
        return cycle_start.replace(day=_last_day_of_month(cycle_start.year, cycle_start.month))
    if rule == DUE_RULE_BEFORE_NEXT:
        # Deliberately ignores the end date/count: the last cycle of a
        # recurrence still gets the same amount of time as every other one.
        nxt = _find_next_date(recurrence, cycle_start + timedelta(days=1))
        return max(cycle_start, nxt - timedelta(days=1))
    return cycle_start


def _next_cycle_start(recurrence: dict[str, Any], after: date, started_count: int) -> date | None:
    if count_exhausted(recurrence, started_count):
        return None
    return next_appearance_after(recurrence, after)


def plan_cycle(recurrence: dict[str, Any], today: date) -> CyclePlan:
    """Where a task with this (new or just-edited) recurrence stands today:
    in the cycle that most recently appeared, or - if the pattern hasn't
    matched yet since its start date - waiting for its first appearance."""
    current = latest_appearance_on_or_before(recurrence, today)
    if current is not None:
        return CyclePlan(
            cycle_start=current,
            due_date=due_date_for_cycle(recurrence, current),
            next_cycle_start=_next_cycle_start(recurrence, current, 1),
            occurrences_count=1,
        )
    first = first_appearance(recurrence)
    return CyclePlan(
        cycle_start=None,
        due_date=due_date_for_cycle(recurrence, first) if first else None,
        next_cycle_start=first,
        occurrences_count=0,
    )


def advance_cycle(
    recurrence: dict[str, Any], next_cycle_start: date, today: date, started_count: int
) -> CyclePlan | None:
    """If the next cycle has appeared by `today`, return the new cycle's
    plan; otherwise None. If several cycles were skipped (Home Assistant
    was off for a while), jumps straight to the most recent one."""
    if next_cycle_start > today:
        return None
    start = latest_appearance_on_or_before(recurrence, today)
    if start is None or start < next_cycle_start:
        start = next_cycle_start
    count = started_count + 1
    return CyclePlan(
        cycle_start=start,
        due_date=due_date_for_cycle(recurrence, start),
        next_cycle_start=_next_cycle_start(recurrence, start, count),
        occurrences_count=count,
    )


def _shift_months(day: date, months: int) -> date:
    total = day.year * 12 + (day.month - 1) + months
    year, month = divmod(total, 12)
    return date(year, month + 1, min(day.day, _last_day_of_month(year, month + 1)))


def _one_period_earlier(recurrence: dict[str, Any], start: date) -> date:
    """`start` moved back by exactly one repeat period - which keeps every
    "every N ..." pattern in the same phase (same weeks/months/years)."""
    rtype = recurrence["type"]
    if rtype == RECURRENCE_WEEKLY:
        return start - timedelta(weeks=int(recurrence["weekly_interval"]))
    if rtype in (RECURRENCE_MONTHLY_DAY, RECURRENCE_MONTHLY_WEEKDAY):
        return _shift_months(start, -int(recurrence["monthly_interval"]))
    if rtype == RECURRENCE_YEARLY:
        return _shift_months(start, -12 * int(recurrence["yearly_interval"]))
    value = int(recurrence["interval_value"])
    unit = recurrence["interval_unit"]
    if unit == "days":
        return start - timedelta(days=value)
    if unit == "weeks":
        return start - timedelta(weeks=value)
    if unit == "months":
        return _shift_months(start, -value)
    return _shift_months(start, -12 * value)


def earlier_start_suggestion(recurrence: dict[str, Any], today: date) -> date | None:
    """If this recurrence won't appear until a future date, the start date
    that would make the *current* period's cycle appear right away instead
    (e.g. a weekly-on-Monday task set up on a Tuesday: this past Monday).
    None if it already has a current cycle, or there's no such date."""
    if not uses_cycles(recurrence) or plan_cycle(recurrence, today).cycle_start is not None:
        return None
    start = _parse_date(recurrence["start_date"])
    shifted = {**recurrence, "start_date": _one_period_earlier(recurrence, start).isoformat()}
    candidate = latest_appearance_on_or_before(shifted, today)
    return candidate if candidate is not None and candidate < start else None


def preview_cycles(recurrence: dict[str, Any], today: date, count: int = 3) -> list[tuple[date, date]]:
    """The (appears, due) dates of the current/first cycle and the ones
    after it - shown in the card's task editor so you can see what a
    repeat + due setting will actually do before saving it."""
    plan = plan_cycle(recurrence, today)
    appear = plan.cycle_start or plan.next_cycle_start
    started = 1  # the first cycle listed has appeared, or will have by then
    cycles: list[tuple[date, date]] = []
    while appear is not None and len(cycles) < count:
        cycles.append((appear, due_date_for_cycle(recurrence, appear)))
        appear = _next_cycle_start(recurrence, appear, started)
        started += 1
    return cycles


def _find_next_hourly(
    recurrence: dict[str, Any], after: datetime
) -> tuple[date, time]:
    """The "hours" interval unit needs sub-day precision, so it's handled
    separately from the day-stepping logic every other type uses."""
    value = int(recurrence["interval_value"])
    if value < 1:
        raise RecurrenceError("interval_value must be >= 1")
    start_date = _parse_date(recurrence["start_date"])
    start_time = _parse_time(recurrence.get("start_time")) or time(0, 0)
    anchor = datetime.combine(start_date, start_time)

    if after < anchor:
        return anchor.date(), anchor.time()

    hours_elapsed = (after - anchor).total_seconds() / 3600
    steps = int(hours_elapsed // value) + 1
    next_dt = anchor + timedelta(hours=value * steps)
    return next_dt.date(), next_dt.time()


def first_occurrence(recurrence: dict[str, Any], due_time: time | None) -> tuple[date, Optional[time]]:
    """The first date/time a brand-new recurring task should be due.

    `due_time` is whatever time-of-day the user picked for the task (or
    None) - every recurrence type except "interval: hours" keeps that time
    unchanged across occurrences, so we just need the correct date here.
    """
    if _is_hourly_interval(recurrence):
        # The "hours" unit steps through times of day on its own (e.g.
        # every 6h -> 08:00, 14:00, 20:00, 02:00...), anchored on the
        # recurrence's own start_time - not the task's due_time, which
        # doesn't apply here the way it does for the other, once-a-day
        # recurrence types.
        start_date = _parse_date(recurrence["start_date"])
        start_time = _parse_time(recurrence.get("start_time")) or time(0, 0)
        anchor = datetime.combine(start_date, start_time)
        return anchor.date(), anchor.time()

    start_date = _parse_date(recurrence["start_date"])
    next_date = _find_next_date(recurrence, on_or_after=start_date)
    return next_date, due_time


def compute_next_occurrence(
    recurrence: dict[str, Any],
    last_due_date: date,
    last_due_time: time | None,
) -> Optional[tuple[date, Optional[time]]]:
    """Given the due date/time a recurring task was just completed on,
    return the (date, time) it should be rescheduled to, or `None` if the
    recurrence has reached its end date or max repetitions and should stop
    recurring (the task then simply stays completed).

    store.py only uses this for "interval: hours" recurrences - every other
    type uses the cycle functions above instead.

    This does NOT mutate `recurrence` - the caller (store.py) owns
    persistence and is responsible for saving the bumped
    `occurrences_count` if a next occurrence is returned.
    """
    end_type = recurrence.get("end_type", "none")
    occurrences_so_far = int(recurrence.get("occurrences_count", 1))
    next_count = occurrences_so_far + 1

    if end_type == RECURRENCE_END_COUNT:
        max_occurrences = recurrence.get("max_occurrences")
        if max_occurrences is not None and next_count > int(max_occurrences):
            return None

    if _is_hourly_interval(recurrence):
        after = datetime.combine(last_due_date, last_due_time or time(0, 0))
        next_date, next_time = _find_next_hourly(recurrence, after)
    else:
        next_date = _find_next_date(recurrence, on_or_after=last_due_date + timedelta(days=1))
        next_time = last_due_time

    if end_type == RECURRENCE_END_DATE:
        end_date = recurrence.get("end_date")
        if end_date and next_date > _parse_date(end_date):
            return None

    return next_date, next_time


def _self_test() -> None:  # pragma: no cover - manual debugging helper
    """A few sanity checks you can run directly: `python recurrence.py`."""

    def check(label: str, actual: Any, expected: Any) -> None:
        status = "OK" if actual == expected else "FAIL"
        print(f"[{status}] {label}: got {actual!r}, expected {expected!r}")

    # Every 3 days, starting 2026-01-01 -> next after completing on 2026-01-01
    r = {
        "type": "interval",
        "interval_unit": "days",
        "interval_value": 3,
        "start_date": "2026-01-01",
        "end_type": "none",
        "occurrences_count": 1,
    }
    check(
        "every 3 days",
        compute_next_occurrence(r, date(2026, 1, 1), None),
        (date(2026, 1, 4), None),
    )

    # Every 2 weeks on Mon/Wed/Fri, starting Monday 2026-01-05
    r = {
        "type": "weekly",
        "weekly_interval": 2,
        "weekdays": [0, 2, 4],
        "start_date": "2026-01-05",
        "end_type": "none",
        "occurrences_count": 1,
    }
    check(
        "biweekly Mon/Wed/Fri, after the Friday",
        compute_next_occurrence(r, date(2026, 1, 9), None),
        (date(2026, 1, 19), None),  # skips the off-week, lands two Mondays later
    )

    # Every last day of the month
    r = {
        "type": "monthly_day",
        "monthly_interval": 1,
        "day_of_month": "last",
        "start_date": "2026-01-01",
        "end_type": "none",
        "occurrences_count": 1,
    }
    check(
        "every last day of month, from Jan 31",
        compute_next_occurrence(r, date(2026, 1, 31), None),
        (date(2026, 2, 28), None),
    )

    # Every 2nd Saturday, every 2 months
    r = {
        "type": "monthly_weekday",
        "monthly_interval": 2,
        "nth_week": "2",
        "weekday": 5,  # Saturday
        "start_date": "2026-01-01",
        "end_type": "none",
        "occurrences_count": 1,
    }
    check(
        "every 2nd Saturday every 2 months",
        compute_next_occurrence(r, date(2026, 1, 10), None),
        (date(2026, 3, 14), None),
    )

    # Yearly anniversary, ends after 2 occurrences
    r = {
        "type": "yearly",
        "yearly_interval": 1,
        "anniversary": "12-24",
        "start_date": "2025-12-24",
        "end_type": "count",
        "max_occurrences": 2,
        "occurrences_count": 2,
    }
    check(
        "yearly, already at max_occurrences",
        compute_next_occurrence(r, date(2026, 12, 24), None),
        None,
    )

    # --- Cycles: appears every Monday, due the day before the next Monday ---
    weekly = {
        "type": "weekly",
        "weekly_interval": 1,
        "weekdays": [0],
        "start_date": "2026-09-01",
        "end_type": "none",
        "due_rule": "before_next",
    }
    check(
        "weekly Monday cycle, planned on a Tuesday",
        plan_cycle(weekly, date(2026, 10, 6)),
        CyclePlan(date(2026, 10, 5), date(2026, 10, 11), date(2026, 10, 12), 1),
    )
    check(
        "weekly Monday cycle, next one appears the following Monday",
        advance_cycle(weekly, date(2026, 10, 12), date(2026, 10, 12), 1),
        CyclePlan(date(2026, 10, 12), date(2026, 10, 18), date(2026, 10, 19), 2),
    )
    check(
        "weekly Monday cycle, not time for the next one yet",
        advance_cycle(weekly, date(2026, 10, 12), date(2026, 10, 11), 1),
        None,
    )
    check(
        "weekly Monday cycle, catching up after 3 weeks offline",
        advance_cycle(weekly, date(2026, 10, 12), date(2026, 10, 28), 1),
        CyclePlan(date(2026, 10, 26), date(2026, 11, 1), date(2026, 11, 2), 2),
    )

    # --- Cycles: appears on the 1st, due at the end of the month ---
    monthly = {
        "type": "monthly_day",
        "monthly_interval": 1,
        "day_of_month": 1,
        "start_date": "2026-11-01",
        "end_type": "none",
        "due_rule": "end_of_month",
    }
    check(
        "monthly 1st cycle, before its start date",
        plan_cycle(monthly, date(2026, 10, 6)),
        CyclePlan(None, date(2026, 11, 30), date(2026, 11, 1), 0),
    )
    check(
        "monthly 1st cycle, first appearance",
        advance_cycle(monthly, date(2026, 11, 1), date(2026, 11, 1), 0),
        CyclePlan(date(2026, 11, 1), date(2026, 11, 30), date(2026, 12, 1), 1),
    )

    # --- Cycles: due on a weekday, N days after, and a 2-cycle limit ---
    check(
        "due on Sunday after a Wednesday appearance",
        due_date_for_cycle({**weekly, "due_rule": "weekday", "due_weekday": 6}, date(2026, 10, 7)),
        date(2026, 10, 11),
    )
    check(
        "due 3 days after appearing",
        due_date_for_cycle({**weekly, "due_rule": "days_after", "due_days": 3}, date(2026, 10, 5)),
        date(2026, 10, 8),
    )
    limited = {**weekly, "end_type": "count", "max_occurrences": 2}
    check(
        "2-cycle limit, second cycle has no next",
        advance_cycle(limited, date(2026, 10, 12), date(2026, 10, 12), 1),
        CyclePlan(date(2026, 10, 12), date(2026, 10, 18), None, 2),
    )
    check(
        "weekly Monday set up on a Tuesday: suggest starting this past Monday",
        earlier_start_suggestion({**weekly, "start_date": "2026-10-06"}, date(2026, 10, 6)),
        date(2026, 10, 5),
    )
    check(
        "monthly 1st set up mid-month: suggest the 1st of this month",
        earlier_start_suggestion({**monthly, "start_date": "2026-10-06"}, date(2026, 10, 6)),
        date(2026, 10, 1),
    )
    check(
        "no suggestion when a cycle is already current",
        earlier_start_suggestion(weekly, date(2026, 10, 6)),
        None,
    )
    check(
        "preview of the weekly cycle",
        preview_cycles(weekly, date(2026, 10, 6), 2),
        [(date(2026, 10, 5), date(2026, 10, 11)), (date(2026, 10, 12), date(2026, 10, 18))],
    )


if __name__ == "__main__":  # pragma: no cover
    _self_test()
