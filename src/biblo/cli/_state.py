"""Where the CLI keeps its session, and how commands get a client."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

from .._sync import Biblo, FileTokenStorage
from ..exceptions import BibloError
from ..models import Branch

APP_NAME = "biblo"
CONFIG_DIR_ENV = "BIBLO_CONFIG_DIR"
LIBRARY_ENV = "BIBLO_LIBRARY"

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


def config_dir() -> Path:
    if override := os.environ.get(CONFIG_DIR_ENV):
        return Path(override)
    return Path(typer.get_app_dir(APP_NAME))


def session_path() -> Path:
    return config_dir() / "session.json"


def client(library: str | None = None) -> Biblo:
    """A client with the saved session, defaulting to `library` (an id or a name)."""
    return Biblo(resolve_library(library), storage=FileTokenStorage(session_path()))


def resolve_library(library: str | None) -> str | None:
    """A library id from an id or (part of) a name or city."""
    if library is None or library.isdigit():
        return library
    with Biblo() as anon:
        matches = anon.libraries.find(library)
    exact = [a for a in matches if a.name.casefold() == library.casefold()]
    if len(exact) == 1 or len(matches) == 1:
        return (exact or matches)[0].id
    if not matches:
        raise UsageError(f"No library matches {library!r}. List them with: biblo libraries")
    names = "\n".join(f"  {a.id}  {a.name}" for a in matches[:15])
    raise UsageError(f"{library!r} matches several libraries; use an id:\n{names}")


def resolve_branch(biblo: Biblo, branch: str) -> Branch:
    """A branch of the login library, from its id or (part of) its name."""
    branches = biblo.libraries.branches(library_id=biblo.login_library_id())
    wanted = branch.casefold()
    by_id = [b for b in branches if b.id.casefold() == wanted]
    exact = [b for b in branches if b.name.casefold() == wanted]
    matches = [b for b in branches if wanted in b.name.casefold()]
    if len(found := by_id or exact or matches) == 1:
        return found[0]
    if not found:
        raise UsageError(f"No branch matches {branch!r}. List them with: biblo branches")
    names = "\n".join(f"  {b.id}  {b.name}" for b in found)
    raise UsageError(f"{branch!r} matches several branches; use an id:\n{names}")
