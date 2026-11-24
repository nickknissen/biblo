"""Printing: rich tables for people, JSON for scripts (`--json`)."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

from pydantic_core import to_jsonable_python
from rich.console import Console
from rich.table import Table

from ..helpers import _danish_date, days_until_due
from ..models import Loan

console = Console(highlight=False)
err = Console(stderr=True, highlight=False)


def print_json(data: Any) -> None:
    text = json.dumps(to_jsonable_python(data, by_alias=False), indent=2, ensure_ascii=False)
    sys.stdout.write(text + "\n")


def table(*columns: str, title: str | None = None) -> Table:
    """A borderless table. Id columns never wrap, so ids stay copyable; others fold."""
    t = Table(title=title, title_justify="left", box=None, pad_edge=False, header_style="bold")
    for column in columns:
        is_id = column.lower().endswith("id")
        t.add_column(column, no_wrap=is_id, overflow="ignore" if is_id else "fold")
    return t


def success(message: str) -> None:
    err.print(f"[green]✓[/green] {message}")


def warn(message: str) -> None:
    err.print(f"[yellow]![/yellow] {message}")


def join(values: Iterable[str | None], sep: str = ", ") -> str:
    return sep.join(v for v in values if v)


def day(value: date | datetime | None) -> str:
    """The date in Danish time, as the library means it."""
    return "" if value is None else _danish_date(value).isoformat()


def due(loan: Loan) -> str:
    """'2026-12-01 (in 11 days)', red when overdue, yellow within three days."""
    target = _danish_date(loan.due_date)
    days = days_until_due(loan)
    if days < 0:
        return f"[red]{target} ({-days} days overdue)[/red]"
    text = {0: "today", 1: "tomorrow"}.get(days, f"in {days} days")
    colour = "yellow" if days <= 3 else "default"
    return f"[{colour}]{target} ({text})[/{colour}]"
