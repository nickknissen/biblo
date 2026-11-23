"""Small facts every integration computes from the models.

    from biblo.helpers import days_until_due, is_overdue

    for loan in biblo.loans.list():
        print(loan.record.title, days_until_due(loan), is_overdue(loan))

Dates are Danish: a loan is due on its due date in Copenhagen time, wherever the code runs.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .models import Loan, Record, ReservationItem, Work

__all__ = ["days_until_due", "edition", "is_overdue", "is_ready_for_pickup", "work_url"]

DANISH_TIME = ZoneInfo("Europe/Copenhagen")


def _danish_date(value: date | datetime) -> date:
    """The calendar date in Copenhagen of an instant; a naive datetime is read as UTC."""
    if isinstance(value, datetime):
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return aware.astimezone(DANISH_TIME).date()
    return value


def _danish_today() -> date:
    return datetime.now(DANISH_TIME).date()


def days_until_due(loan: Loan, today: date | None = None) -> int:
    """Whole days from `today` to the day `loan` is due: 0 on the due day, negative once overdue.

    The due date is its calendar date in Danish time (Europe/Copenhagen); the time of day in
    the API's timestamp is not a deadline. `today` defaults to the current date in Danish
    time, so the answer doesn't depend on the machine's time zone. Works for digital and
    physical loans.
    """
    start = _danish_today() if today is None else _danish_date(today)
    return (_danish_date(loan.due_date) - start).days


def is_overdue(loan: Loan, today: date | None = None) -> bool:
    """Whether `loan` was due before `today` (in Danish time); see `days_until_due`."""
    return days_until_due(loan, today) < 0


def is_ready_for_pickup(reservation: ReservationItem) -> bool:
    """Whether the reservation is waiting to be picked up (state `READY_FOR_PICKUP`)."""
    return reservation.state == "READY_FOR_PICKUP"


def edition(work: Work, category: str) -> Record | None:
    """The first of `work.variations` whose material-type category is `category`, or None.

    `category` is a `MaterialType.category` such as `"EBOOKS"`, `"AUDIO_BOOKS_ONLINE"` or
    `"BOOKS"`. Which editions a work lists depends on the `catalogue=` it was fetched with:
    printed books, say, only under `catalogue="all"`.
    """
    return next((r for r in work.variations if r.material_type.category == category), None)


def work_url(work_id: str) -> str:
    """The work's public page on bibliotek.dk, e.g. for `work-of:870970-basis:22252852`.

    The id is URL-quoted, keeping its `:` characters as the site expects.
    """
    return f"https://bibliotek.dk/work/{quote(work_id, safe=':')}"
