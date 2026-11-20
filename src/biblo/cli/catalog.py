from __future__ import annotations

from typing import Annotated, cast, get_args

import typer

from ..types import SEARCH_FILTER_PARAMS, SearchBy, SearchFilters
from ._output import console, join, print_json, table
from ._state import JsonOption, LibraryOption, UsageError, client

FORMATS = {
    "ebook": "e-bog",
    "audiobook": "lydbog (online)",
    "podcast": "podcast",
    "magazine": "tidsskrift (online)",
}


def search(
    query: Annotated[str, typer.Argument(help="What to search for.")],
    by: Annotated[
        str | None,
        typer.Option(help="Match only: title, creator or subject.", show_default=False),
    ] = None,
    ebook: Annotated[bool, typer.Option("--ebook", help="Only e-books.")] = False,
    audiobook: Annotated[bool, typer.Option("--audiobook", help="Only audiobooks.")] = False,
    podcast: Annotated[bool, typer.Option("--podcast", help="Only podcasts.")] = False,
    language: Annotated[
        str | None,
        typer.Option(help="Only this language, as a code: dan, eng, ...", show_default=False),
    ] = None,
    filters: Annotated[
        list[str] | None,
        typer.Option(
            "--filter",
            "-f",
            help="Any facet filter as name=value, e.g. -f subjects=krimi. Repeatable.",
            show_default=False,
        ),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", help="How many results.")] = 20,
    library: LibraryOption = None,
    as_json: JsonOption = False,
) -> None:
    """Search the catalogue. Show a result with: biblo work <id>"""
    if by is not None and by not in get_args(SearchBy):
        raise UsageError("--by must be title, creator or subject")
    chosen = _parse_filters(filters or [])
    wanted = {"ebook": ebook, "audiobook": audiobook, "podcast": podcast}
    if formats := [FORMATS[name] for name, on in wanted.items() if on]:
        chosen["material_types_specific"] = formats
    if language:
        chosen["main_languages"] = language

    with client(library) as biblo:
        works = list(
            biblo.catalog.iter_search(
                query, by=cast(SearchBy | None, by), max_results=limit, **chosen
            )
        )
    if as_json:
        print_json(works)
        return
    if not works:
        console.print("No results.")
        return
    t = table("Title", "By", "Year", "Formats", "Id")
    for w in works:
        title = w.title
        if w.series:
            series = join(join([s.title, s.number_in_series], " ") for s in w.series)
            title += f" [dim]({series})[/dim]"
        formats_text = join(m.display for m in w.material_types)
        t.add_row(title, join(w.creators), w.work_year or "", formats_text, f"[dim]{w.id}[/dim]")
    console.print(t)


def _parse_filters(raw: list[str]) -> SearchFilters:
    """`-f subjects=krimi -f mainLanguages=dan` (snake_case or the API's camelCase)."""
    by_any_name = {param: name for name, param in SEARCH_FILTER_PARAMS.items()} | {
        name: name for name in SEARCH_FILTER_PARAMS
    }
    result: dict[str, list[str]] = {}
    for item in raw:
        name, sep, value = item.partition("=")
        if not sep or name not in by_any_name:
            known = ", ".join(sorted(SEARCH_FILTER_PARAMS))
            raise UsageError(f"Bad filter {item!r}: use name=value with a name from: {known}")
        result.setdefault(by_any_name[name], []).append(value)
    return cast(SearchFilters, result)


def work(
    work_id: Annotated[
        str, typer.Argument(help="A work id from search, e.g. work-of:870970-basis:22252852")
    ],
    library: LibraryOption = None,
    as_json: JsonOption = False,
) -> None:
    """Show a work: description, formats, availability and the records to borrow."""
    with client(library) as biblo:
        w = biblo.catalog.work(work_id)
        availability = biblo.catalog.availability(work_id)
    if as_json:
        print_json({"work": w, "availability": availability})
        return
    console.print(f"[bold]{w.title}[/bold]")
    if w.creators:
        console.print(join(w.creators))
    facts = join(
        [w.details.year, w.details.publisher, w.details.extent, join(w.details.languages)], " · "
    )
    if facts:
        console.print(f"[dim]{facts}[/dim]")
    for s in w.series or []:
        console.print(f"[dim]Series: {s.title} {s.number_in_series or ''} ({s.series_id})[/dim]")
    if w.abstract:
        console.print(f"\n{w.abstract}")
    if w.subjects:
        console.print(f"\n[dim]Subjects: {join(w.subjects)}[/dim]")

    t = table("Format", "Available", "Reservable", title="\nAvailability")
    for a in availability:
        t.add_row(a.material_type.display, _yes(a.available), _yes(a.reservable))
    console.print(t)

    t = table("Format", "Year", "Publisher", "Extent", "Record id", title="\nRecords")
    for r in w.variations:
        t.add_row(
            r.material_type.display, r.year or "", join(r.publisher), r.extent or "", r.record_id
        )
    console.print(t)
    console.print("\n[dim]Borrow with: biblo borrow <record id>[/dim]")


def _yes(value: bool) -> str:
    return "[green]yes[/green]" if value else "[red]no[/red]"
