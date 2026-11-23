"""
An MCP server for Biblo: let an AI assistant search your public library and read your account.

Read-only: the tools search the catalogue and list loans, reservations, favorites, quotas
and fees, but never borrow, renew, reserve or change anything.

Log in once with the CLI (`biblo auth login`); the server uses the session it saves, so
neither the card number nor the pincode goes into the MCP client's config. Without a login
the catalogue tools still work, given a library in BIBLO_LIBRARY.

Run over stdio:

    uv run --with "biblo[cli]==0.2.0" --with "mcp==2.3.0" examples/mcp_server/biblo_mcp.py
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import click
from mcp.server.apps import Apps, ResourceCsp
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from biblo import (
    AsyncBiblo,
    AsyncFileTokenStorage,
    BibloError,
    NoLibraryError,
    NotFoundError,
    NotLoggedInError,
)
from biblo import models as bm

# Sent before the library's own `biblo-python/<version>`, so the API's owners can tell this
# traffic apart.
USER_AGENT = "biblo-mcp"
# The same as the CLI's, so `biblo auth login` logs the server in.
CONFIG_DIR_ENV = "BIBLO_CONFIG_DIR"
LIBRARY_ENV = "BIBLO_LIBRARY"

# The `material_types_specific` facet values, as the CLI's `--ebook` etc. use them.
FORMATS = {
    "ebook": "e-bog",
    "audiobook": "lydbog (online)",
    "podcast": "podcast",
    "magazine": "tidsskrift (online)",
}
Format = Literal["any", "ebook", "audiobook", "podcast", "magazine"]
Catalogue = Literal["digital", "children", "all"]

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)

MY_LIBRARY_URI = "ui://biblo/my-library.html"
MY_LIBRARY_HTML = Path(__file__).with_name("my_library.html")
# Where loan and reservation covers are served from.
COVER_HOSTS = ["https://fbiinfo-present.dbc.dk"]

INSTRUCTIONS = """\
Tools for the Danish public libraries through Biblo (Det Digitale Folkebibliotek).
The catalogue is Danish: search in Danish for the best results. The default catalogue is
the digital one (e-books, online audiobooks, podcasts); use catalogue="all" for printed
books, films and music too. Search returns works; get_work tells what editions a work has
and whether they can be borrowed now. The my_* tools read the user's own library account.
All tools only read: to borrow, renew or reserve, the user uses the Biblo app, the
library's website or the `biblo` CLI.
"""


def session_path() -> Path:
    """Where the CLI saves its session: $BIBLO_CONFIG_DIR, or the platform's config dir."""
    if override := os.environ.get(CONFIG_DIR_ENV):
        return Path(override) / "session.json"
    return Path(click.get_app_dir("biblo")) / "session.json"


def default_client() -> AsyncBiblo:
    return AsyncBiblo(
        os.environ.get(LIBRARY_ENV),
        storage=AsyncFileTokenStorage(session_path()),
        user_agent=USER_AGENT,
    )


@contextmanager
def _as_tool_errors() -> Iterator[None]:
    """Turn the library's errors into messages the model can act on."""
    try:
        yield
    except NotLoggedInError as err:
        raise ToolError(
            "Not logged in. The user can log in by running `biblo auth login` in a terminal."
        ) from err
    except NoLibraryError as err:
        raise ToolError(
            f"No library chosen. Log in with `biblo auth login`, or set {LIBRARY_ENV} to a "
            "library id (find one with find_library)."
        ) from err
    except NotFoundError as err:
        raise ToolError(f"Not found: {err}") from err
    except BibloError as err:
        raise ToolError(f"Biblo request failed: {err}") from err


def _formats(types: list[bm.MaterialType]) -> list[str]:
    return [t.display for t in types]


def _work_light(work: bm.WorkLight) -> dict[str, Any]:
    return {
        "work_id": work.id,
        "title": work.title,
        "creators": work.creators,
        "year": work.work_year,
        "publisher": work.publisher,
        "formats": _formats(work.material_types),
        "series": [
            f"{s.title} ({s.number_in_series})" if s.number_in_series else s.title
            for s in work.series
        ],
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _work_page(work_id: str) -> str:
    return f"https://bibliotek.dk/work/{work_id}"


async def _loan_rows(client: AsyncBiblo) -> list[dict[str, Any]]:
    """Loans, soonest due first."""
    loans = await client.loans.list()
    now = datetime.now(UTC)
    return [
        {
            "title": loan.record.title,
            "author": loan.record.author,
            "format": loan.record.material_type.display,
            "due": _iso(loan.due_date),
            "overdue": loan.due_date < now,
            "renewable": loan.is_renewable,
            "work_id": loan.record.work_id,
            "cover_url": loan.record.cover_url,
            "web_page": _work_page(loan.record.work_id),
        }
        for loan in sorted(loans, key=lambda loan: loan.due_date)
    ]


async def _reservation_rows(client: AsyncBiblo) -> list[dict[str, Any]]:
    """Reservations, ready for pickup first; pickup branches by name."""
    reservations = await client.reservations.list()
    branches: dict[str, str] = {}
    if any(r.physical for r in reservations):
        library = await client.login_library_id()
        branches = {b.id: b.name for b in await client.libraries.branches(library)}
    rows = [
        {
            "title": r.details.title,
            "author": r.details.author,
            "format": r.details.material_type.display,
            "ready_for_pickup": r.state == "READY_FOR_PICKUP",
            "pickup_by": _iso(r.claim.available_until) if r.claim else None,
            "queue_position": r.physical.number_in_queue if r.physical else None,
            "pickup_branch": (
                branches.get(r.physical.pickup_branch, r.physical.pickup_branch)
                if r.physical
                else None
            ),
            "work_id": r.details.work_id,
            "cover_url": r.details.cover_url,
            "web_page": _work_page(r.details.work_id),
        }
        for r in reservations
    ]
    return sorted(rows, key=lambda row: not row["ready_for_pickup"])


def create_server(make_client: Callable[[], AsyncBiblo] = default_client) -> MCPServer[Any]:
    """The server; `make_client` lets tests point it at a fake API."""
    # MCP Apps: show_my_library carries a link to an HTML view the host can render.
    apps = Apps()
    apps.add_html_resource(
        MY_LIBRARY_URI,
        MY_LIBRARY_HTML.read_text(encoding="utf-8"),
        title="My library",
        description="Loans, reservations and digital loans left",
        # Covers come from DBC's image service; the view's sandbox blocks other hosts.
        csp=ResourceCsp(resource_domains=COVER_HOSTS),
        prefers_border=True,
    )

    @asynccontextmanager
    async def lifespan(_: MCPServer[Any]) -> AsyncIterator[AsyncBiblo]:
        client = make_client()
        try:
            yield client
        finally:
            await client.aclose()

    def biblo(ctx: Context[Any, Any]) -> AsyncBiblo:
        client: AsyncBiblo = ctx.request_context.lifespan_context
        return client

    # Registered before the server is built: MCPServer reads an extension's tools then.
    @apps.tool(resource_uri=MY_LIBRARY_URI, annotations=READ_ONLY)
    async def show_my_library(ctx: Context[Any, Any]) -> dict[str, Any]:
        """Show the user's loans, reservations and digital loans left as an interactive view.

        Use when the user wants an overview of their library account. Clients without MCP
        Apps get the same data as JSON.
        """
        client = biblo(ctx)
        with _as_tool_errors():
            loans, reservations = await asyncio.gather(
                _loan_rows(client), _reservation_rows(client)
            )
            quotas = await client.account.quotas()
        return {
            "loans": loans,
            "reservations": reservations,
            "loans_left_this_period": {
                "ebook": quotas.remaining_loan_quotas.ebook,
                "audiobook": quotas.remaining_loan_quotas.audiobook,
            },
            "loans_per_period": {
                "ebook": quotas.total_loan_quotas.ebook,
                "audiobook": quotas.total_loan_quotas.audiobook,
            },
            "as_of": datetime.now(UTC).isoformat(),
        }

    server: MCPServer[Any] = MCPServer(
        "biblo",
        instructions=INSTRUCTIONS,
        lifespan=lifespan,
        version="0.1.0",
        extensions=[apps],
    )

    @server.tool(annotations=READ_ONLY)
    async def search_catalogue(
        ctx: Context[Any, Any],
        query: str,
        format: Format = "any",
        catalogue: Catalogue = "digital",
        limit: int = 10,
    ) -> dict[str, Any]:
        """Search the library's catalogue for works (titles, authors, subjects).

        format narrows to e-books, audiobooks, podcasts or magazines. catalogue is
        "digital" (e-books, audiobooks, podcasts), "children" or "all" (print, films,
        music too). Returns the total number of hits and up to `limit` works.
        """
        filters: dict[str, Any] = {}
        if format != "any":
            filters["material_types_specific"] = FORMATS[format]
        with _as_tool_errors():
            result = await biblo(ctx).catalog.search(
                query, limit=max(1, min(limit, 50)), catalogue=catalogue, **filters
            )
        return {"hits": result.hitcount, "works": [_work_light(w) for w in result.works]}

    @server.tool(annotations=READ_ONLY)
    async def get_work(
        ctx: Context[Any, Any], work_id: str, catalogue: Catalogue = "digital"
    ) -> dict[str, Any]:
        """A work's description, subjects, series, editions and whether it can be had now.

        `availability` says per format whether it's available now (a copy on the shelf or a
        free licence) and whether it can be reserved. Use catalogue="all" to include
        printed editions.
        """
        client = biblo(ctx)
        with _as_tool_errors():
            work = await client.catalog.work(work_id, catalogue=catalogue)
            availability = await client.catalog.availability(work_id, catalogue=catalogue)
        return {
            "work_id": work.id,
            "title": work.title,
            "creators": work.creators,
            "description": work.abstract,
            "subjects": work.subjects,
            "fiction_or_nonfiction": work.fiction_nonfiction,
            "languages": work.details.languages,
            "year": work.details.year,
            "length": work.details.extent,
            "series": [
                {"title": s.title, "number": s.number_in_series, "works": len(s.members)}
                for s in work.series or []
            ],
            "editions": [
                {
                    "record_id": r.record_id,
                    "format": r.material_type.display,
                    "year": r.year,
                    "publisher": r.publisher,
                    "length": r.extent,
                }
                for r in work.variations
            ],
            "availability": [
                {
                    "format": a.material_type.display,
                    "available_now": a.available,
                    "reservable": a.reservable,
                    "kind": a.source,
                }
                for a in availability
            ],
            "web_page": f"https://bibliotek.dk/work/{work.id}",
        }

    @server.tool(annotations=READ_ONLY)
    async def find_library(ctx: Context[Any, Any], name_or_city: str) -> list[dict[str, str]]:
        """Find Danish public libraries by name or city; returns their ids."""
        with _as_tool_errors():
            found = await biblo(ctx).libraries.find(name_or_city)
        return [{"library_id": a.id, "name": a.name, "city": a.address.city} for a in found]

    @server.tool(annotations=READ_ONLY)
    async def my_loans(ctx: Context[Any, Any]) -> list[dict[str, Any]]:
        """The user's current loans, digital and physical, soonest due first."""
        with _as_tool_errors():
            return await _loan_rows(biblo(ctx))

    @server.tool(annotations=READ_ONLY)
    async def my_reservations(ctx: Context[Any, Any]) -> list[dict[str, Any]]:
        """The user's reservations: which are ready for pickup, and queue positions."""
        with _as_tool_errors():
            return await _reservation_rows(biblo(ctx))

    @server.tool(annotations=READ_ONLY)
    async def my_favorites(ctx: Context[Any, Any]) -> list[dict[str, Any]]:
        """The works the user saved in their list ("Huskeliste")."""
        with _as_tool_errors():
            works = await biblo(ctx).favorites.list()
        return [
            {
                "work_id": w.id,
                "title": w.title,
                "creators": w.creators,
                "formats": _formats(w.material_types),
            }
            for w in works
        ]

    @server.tool(annotations=READ_ONLY)
    async def my_account(ctx: Context[Any, Any]) -> dict[str, Any]:
        """E-book and audiobook loans left this period, preferred pickup branch, and fees."""
        client = biblo(ctx)
        with _as_tool_errors():
            account = await client.account.get()
            quotas = await client.account.quotas()
            fees = await client.account.fees()
        return {
            "preferred_pickup_branch": (
                account.preferred_pickup_branch.name if account.preferred_pickup_branch else None
            ),
            "loans_left_this_period": {
                "ebook": quotas.remaining_loan_quotas.ebook,
                "audiobook": quotas.remaining_loan_quotas.audiobook,
            },
            "loans_per_period": {
                "ebook": quotas.total_loan_quotas.ebook,
                "audiobook": quotas.total_loan_quotas.audiobook,
            },
            "fees_dkk": sum(f.amount_in_dkk for f in fees),
            "fees": [{"reason": f.reason, "amount_dkk": f.amount_in_dkk} for f in fees],
        }

    return server


if __name__ == "__main__":
    create_server().run()
