from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated

import typer

from ._output import console, join, print_json, table
from ._state import JsonOption, LibraryOption, client


def libraries(
    query: Annotated[
        str | None, typer.Argument(help="Part of a name, city or id.", show_default=False)
    ] = None,
    as_json: JsonOption = False,
) -> None:
    """List libraries, or find one by name or city."""
    with client() as biblo:
        found = biblo.libraries.find(query) if query else biblo.libraries.list()
    if as_json:
        print_json(found)
        return
    t = table("Id", "Name", "City")
    for a in sorted(found, key=lambda a: a.name):
        t.add_row(a.id, a.name, a.address.city if a.address else "")
    console.print(t)


def branches(library: LibraryOption = None, as_json: JsonOption = False) -> None:
    """List a library's branches, and whether they are open now."""
    with client(library) as biblo:
        found = biblo.libraries.branches()
        status = {s.branch_id: s for s in biblo.libraries.open_status().status_list}
    if as_json:
        print_json(found)
        return
    t = table("Id", "Name", "Address", "Open now")
    for b in found:
        address = join([b.address.street, b.address.city]) if b.address else ""
        s = status.get(b.id)
        open_now = (
            f"{s.opens.astimezone():%H:%M}–{s.closes.astimezone():%H:%M}" if s and s.opens else ""
        )
        t.add_row(b.id, b.name, address, open_now)
    console.print(t)


def hours(
    branch: Annotated[str, typer.Argument(help="A branch id from `biblo branches`.")],
    days: Annotated[int, typer.Option(help="How many days ahead.")] = 7,
    library: LibraryOption = None,
    as_json: JsonOption = False,
) -> None:
    """Show a branch's opening hours."""
    start = date.today()
    with client(library) as biblo:
        found = biblo.libraries.branch_opening_hours(
            branch, start=start.isoformat(), end=(start + timedelta(days=days)).isoformat()
        )
    if as_json:
        print_json(found)
        return
    t = table("Date", "Hours")
    for item in found.opening_hours:
        times = join((f"{c.title}: {c.start_time}–{c.end_time}" for c in item.categories), "\n")
        t.add_row(f"{item.date:%a %d %b}", times or "[dim]closed[/dim]")
    console.print(t)


def events(
    branch: Annotated[
        str | None, typer.Argument(help="Only this branch.", show_default=False)
    ] = None,
    days: Annotated[int, typer.Option(help="How many days ahead.")] = 14,
    library: LibraryOption = None,
    as_json: JsonOption = False,
) -> None:
    """List upcoming events at the library."""
    start = date.today()
    with client(library) as biblo:
        found = biblo.libraries.events(
            branch, start=start.isoformat(), end=(start + timedelta(days=days)).isoformat()
        )
        names = {} if as_json else {b.id: b.name for b in biblo.libraries.branches()}
    if as_json:
        print_json(found)
        return
    t = table("When", "Title", "Where", "Link")
    for e in sorted(found, key=lambda e: e.duration.start):
        where = join(names.get(b.id, b.id) for b in e.branch_refs)
        t.add_row(
            f"{e.duration.start.astimezone():%a %d %b %H:%M}", e.title, where, f"[dim]{e.url}[/dim]"
        )
    console.print(t)
