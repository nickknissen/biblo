from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from biblo.helpers import days_until_due, edition, is_overdue, is_ready_for_pickup, work_url
from biblo.models import Loan, ReservationItem, Work
from tests.conftest import machine_time_zone
from tests.fake import PHYSICAL_LOAN, fixture

DANISH = ZoneInfo("Europe/Copenhagen")
LOAN = fixture("account_loans")["data"][0]  # a recorded digital loan


def loan_due(due: str, base: dict[str, Any] | None = None) -> Loan:
    base = LOAN if base is None else base
    return Loan.model_validate({**base, "dueDate": due})


def test_due_today_is_zero_and_not_overdue() -> None:
    loan = loan_due("2026-12-10T10:00:00.000Z")
    assert days_until_due(loan, today=date(2026, 12, 10)) == 0
    assert not is_overdue(loan, today=date(2026, 12, 10))


def test_due_yesterday_is_minus_one_and_overdue() -> None:
    loan = loan_due("2026-12-10T10:00:00.000Z")
    assert days_until_due(loan, today=date(2026, 12, 11)) == -1
    assert is_overdue(loan, today=date(2026, 12, 11))


def test_counts_whole_days_ahead() -> None:
    loan = loan_due("2026-12-23T22:59:59.999Z")
    assert days_until_due(loan, today=date(2026, 12, 1)) == 22


def test_due_date_is_taken_in_danish_time() -> None:
    # 22:59:59.999Z is 23:59:59 in Copenhagen: still the 23rd there.
    assert days_until_due(loan_due("2026-12-23T22:59:59.999Z"), today=date(2026, 12, 23)) == 0
    # 23:30Z on the 23rd is 00:30 on the 24th in Copenhagen.
    assert days_until_due(loan_due("2026-12-23T23:30:00.000Z"), today=date(2026, 12, 24)) == 0
    # Summer time: 22:30Z is 00:30 the next day in Copenhagen (UTC+2).
    assert days_until_due(loan_due("2026-07-01T22:30:00.000Z"), today=date(2026, 7, 2)) == 0
    # 00:30Z is 01:30 the same day in Copenhagen.
    assert days_until_due(loan_due("2026-12-24T00:30:00.000Z"), today=date(2026, 12, 24)) == 0


def test_physical_loans_too() -> None:
    loan = loan_due("2026-12-23T22:59:59.999Z", base=PHYSICAL_LOAN)
    assert days_until_due(loan, today=date(2026, 12, 20)) == 3


def test_naive_due_date_is_read_as_utc() -> None:
    loan = loan_due("2026-12-23T23:30:00")
    loan.due_date = loan.due_date.replace(tzinfo=None)
    assert days_until_due(loan, today=date(2026, 12, 24)) == 0


def test_today_defaults_to_the_danish_date_whatever_the_machine_zone() -> None:
    danish_today = datetime.now(DANISH).date()
    end_of_today = datetime.combine(danish_today, datetime.max.time(), DANISH).astimezone(UTC)
    loan = loan_due(end_of_today.isoformat().replace("+00:00", "Z"))
    yesterday = loan_due((end_of_today - timedelta(days=1)).isoformat().replace("+00:00", "Z"))
    # UTC+14 and UTC-11: a day ahead of and behind Copenhagen for much of the day.
    for zone in ("Pacific/Kiritimati", "Pacific/Pago_Pago"):
        with machine_time_zone(zone):
            assert days_until_due(loan) == 0
            assert not is_overdue(loan)
            assert is_overdue(yesterday)


def reservations() -> list[ReservationItem]:
    body = fixture("account_reservations_mybiblo")
    return [ReservationItem.model_validate(r) for r in body["data"]]


def test_is_ready_for_pickup() -> None:
    ready, reserved, *_ = reservations()
    assert ready.state == "READY_FOR_PICKUP"
    assert is_ready_for_pickup(ready)
    assert reserved.state == "RESERVED"
    assert not is_ready_for_pickup(reserved)


def test_edition_returns_the_first_in_the_category() -> None:
    work = Work.model_validate(fixture("work")["data"])
    audiobook = edition(work, "AUDIO_BOOKS_ONLINE")
    assert audiobook is work.variations[0]
    ebook = edition(work, "EBOOKS")
    assert ebook is not None
    assert ebook.material_type.category == "EBOOKS"
    assert edition(work, "BOOKS") is None


def test_work_url_keeps_colons() -> None:
    assert (
        work_url("work-of:870970-basis:22252852")
        == "https://bibliotek.dk/work/work-of:870970-basis:22252852"
    )
    assert work_url("work-of:a b/c") == "https://bibliotek.dk/work/work-of:a%20b%2Fc"
