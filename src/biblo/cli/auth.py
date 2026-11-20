from __future__ import annotations

import os
import sys
import webbrowser
from datetime import UTC, datetime
from typing import Annotated

import typer

from .._sync import Biblo
from ..exceptions import NotLoggedInError
from ._output import console, err, print_json, success, warn
from ._state import JsonOption, LibraryOption, UsageError, client, session_path

app = typer.Typer(help="Log in, log out and check the session.", no_args_is_help=True)


@app.command()
def login(
    library: LibraryOption = None,
    user: Annotated[
        str | None,
        typer.Option(
            "--user",
            "-u",
            envvar="BIBLO_USER",
            help="Card number or CPR. Prompted for if not given.",
            show_default=False,
        ),
    ] = None,
    pin_stdin: Annotated[
        bool, typer.Option("--pin-stdin", help="Read the pincode from standard input.")
    ] = False,
    web: Annotated[
        bool,
        typer.Option("--web", help="Log in in a browser instead, e.g. with MitID."),
    ] = False,
) -> None:
    """Log in with your library card number or CPR and pincode.

    The pincode is read from $BIBLO_PIN, standard input (--pin-stdin) or a hidden prompt,
    and never stored: only the session's tokens are, in the file `biblo auth status` shows.
    """
    if library is None:
        library = typer.prompt("Library (name, city or id)")
    with client(library) as biblo:
        library_id = biblo.library_id()
        assert library_id is not None
        name = biblo.libraries.get(library_id).name
        if web:
            _web_login(biblo)
        else:
            user_id = user or typer.prompt("Card number or CPR")
            biblo.login(user_id, _pincode(pin_stdin), library_id)
        account = biblo.account.get()
    who = f" as {account.name}" if account.name else ""
    success(f"Logged in to {name} ({library_id}){who}")
    if account.preferred_pickup_branch is None:
        warn(
            "No pickup branch for physical reservations yet; set one with "
            "[bold]biblo account set --pickup-branch[/bold] NAME"
        )


def _pincode(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().strip()
    if pin := os.environ.get("BIBLO_PIN"):
        return pin
    if not sys.stdin.isatty():
        raise UsageError("No pincode: set BIBLO_PIN or use --pin-stdin")
    return typer.prompt("Pincode", hide_input=True)


def _web_login(biblo: Biblo) -> None:
    url = biblo.authorize_url()
    err.print("Log in in the browser. Afterwards it tries to open a [bold]biblo://[/bold] link")
    err.print("it can't: copy that link (the address bar, or the browser's developer tools >")
    err.print("Network > the last redirect's Location) and paste it here.\n")
    err.print(url, soft_wrap=True)
    webbrowser.open(url)
    biblo.login_with_code(typer.prompt("\nbiblo:// link or code"))


@app.command()
def status(as_json: JsonOption = False) -> None:
    """Show whether you are logged in, where, and until when."""
    with client() as biblo:
        tokens = biblo.tokens() if biblo.is_logged_in() else None
        library_id = biblo.login_library_id()
        account = biblo.account.get() if tokens else None
    info = {
        "logged_in": tokens is not None,
        "library_id": library_id,
        "name": account.name if account else None,
        "session_file": str(session_path()),
        "token_expires_at": tokens.expires_at if tokens else None,
    }
    if as_json:
        print_json(info)
        return
    if tokens is None:
        console.print("Not logged in. Log in with: [bold]biblo auth login[/bold]")
        console.print(f"Session file: {session_path()}")
        raise typer.Exit(4)
    left = tokens.expires_at - datetime.now(UTC)
    hours = int(left.total_seconds() // 3600)
    valid = f"{left.days} days" if left.days >= 2 else f"{hours} hours"
    who = f" as {account.name}" if account and account.name else ""
    console.print(f"[green]✓[/green] Logged in at library {library_id}{who}")
    console.print(f"  Access token valid for {valid} more (refreshed automatically)")
    console.print(f"  Session file: {session_path()}")


@app.command()
def logout() -> None:
    """Log out and delete the tokens. The library is remembered."""
    with client() as biblo:
        if not biblo.is_logged_in():
            err.print("Not logged in.")
            return
        biblo.logout()
    success("Logged out")


@app.command()
def token() -> None:
    """Print a valid access token, e.g. for curl against the API."""
    with client() as biblo:
        tokens = biblo.tokens()
    if tokens is None:
        raise NotLoggedInError("Not logged in")
    print(tokens.access_token)
