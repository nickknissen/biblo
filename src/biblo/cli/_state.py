"""Where the CLI keeps its session, and how commands get a client."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Protocol

import typer

from .._base import VERSION, default_session_path
from .._sync import Biblo, FileTokenStorage
from ..exceptions import BibloError, NotLoggedInError
from ..models import Branch

APP_NAME = "biblo"
USER_AGENT = f"biblo-cli/{VERSION}"
"""Put before the library's own User-Agent on every API request the CLI makes."""
LIBRARY_ENV = "BIBLO_LIBRARY"

EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NOT_LOGGED_IN = 4  # like gh
EXIT_INTERRUPTED = 130  # 128 + SIGINT, like a shell

LibraryOption = Annotated[
    str | None,
    typer.Option(
        "--library",
        "-l",
        envvar=LIBRARY_ENV,
        help="Library id or name, e.g. 763000 or vejle. Default: the one you logged in at.",
        show_default=False,
    ),
]
JsonOption = Annotated[bool, typer.Option("--json", help="Print JSON instead of a table.")]


class UsageError(BibloError):
    """A problem with how the CLI was called; exits with status 2."""


def session_path() -> Path:
    return default_session_path()


def client(library: str | None = None) -> Biblo:
    """A client with the saved session, defaulting to `library` (an id or a name)."""
    return Biblo(
        resolve_library(library), storage=FileTokenStorage.default(), user_agent=USER_AGENT
    )


def resolve_library(library: str | None) -> str | None:
    """A library id from an id or (part of) a name or city."""
    if library is None or library.isdigit():
        return library
    with Biblo(user_agent=USER_AGENT) as anon:
        matches = anon.libraries.find(library)
    return _pick(library, matches, "library", "libraries").id


def resolve_branch(biblo: Biblo, branch: str) -> Branch:
    """A branch of the login library, from its id or (part of) its name."""
    if not biblo.is_logged_in():
        raise NotLoggedInError("Not logged in")
    branches = biblo.libraries.branches(library_id=biblo.login_library_id())
    wanted = branch.casefold()
    if by_id := next((b for b in branches if b.id.casefold() == wanted), None):
        return by_id
    return _pick(branch, [b for b in branches if wanted in b.name.casefold()], "branch", "branches")


class _Named(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def name(self) -> str: ...


def _pick[T: _Named](query: str, matches: list[T], kind: str, kinds: str) -> T:
    """The one match for `query`: the only one, or the only exact name match."""
    exact = [m for m in matches if m.name.casefold() == query.casefold()]
    if len(exact) == 1 or len(matches) == 1:
        return (exact or matches)[0]
    if not matches:
        raise UsageError(f"No {kind} matches {query!r}. List them with: biblo {kinds}")
    names = "\n".join(f"  {m.id}  {m.name}" for m in matches[:15])
    raise UsageError(f"{query!r} matches several {kinds}; use an id:\n{names}")
