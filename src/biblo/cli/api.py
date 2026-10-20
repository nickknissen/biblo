from __future__ import annotations

import json
from typing import Annotated

import typer

from ._output import print_json
from ._state import LibraryOption, UsageError, client


def api(
    path: Annotated[
        str,
        typer.Argument(help="An API path, e.g. v1/config or v1/agencies/{agencyId}/account/loans."),
    ],
    method: Annotated[str, typer.Option("--method", "-X", help="HTTP method.")] = "GET",
    params: Annotated[
        list[str] | None,
        typer.Option(
            "--param", "-p", help="Query parameter name=value. Repeatable.", show_default=False
        ),
    ] = None,
    body: Annotated[
        str | None, typer.Option("--body", "-d", help="JSON request body.", show_default=False)
    ] = None,
    library: LibraryOption = None,
) -> None:
    """Call any API endpoint and print its JSON, like `gh api`.

    `{agencyId}` in the path becomes your library; your session's token is sent. The
    endpoints are described at https://api.detdigitalefolkebibliotek.dk/
    """
    query: dict[str, str | list[str]] = {}
    for item in params or []:
        name, sep, value = item.partition("=")
        if not sep:
            raise UsageError(f"Bad --param {item!r}: use name=value")
        existing = query.get(name)
        if existing is None:
            query[name] = value
        else:
            query[name] = [*([existing] if isinstance(existing, str) else existing), value]
    try:
        payload = json.loads(body) if body is not None else None
    except json.JSONDecodeError as e:
        raise UsageError(f"--body is not valid JSON: {e}") from e
    with client(library) as biblo:
        result = biblo.request(method, "/" + path.lstrip("/"), params=query, json=payload)
    if result is not None:
        print_json(result)
