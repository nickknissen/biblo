"""The `biblo` command. Needs the `cli` extra: pip install 'biblo[cli]'."""

from __future__ import annotations

import sys
from typing import Annotated

import typer

from .. import __version__
from ..exceptions import (
    BibloConnectionError,
    BibloError,
    InvalidCredentialsError,
    NoLibraryError,
    NotLoggedInError,
    ResponseValidationError,
)
from . import account, api, auth, catalog, libraries
from ._output import err
from ._state import UsageError

app = typer.Typer(
    name="biblo",
    help="Search, borrow and reserve at Danish public libraries, from the terminal.",
    no_args_is_help=True,
    rich_markup_mode="rich",
    pretty_exceptions_enable=False,
)

EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NOT_LOGGED_IN = 4  # like gh


def _version(value: bool) -> None:
    if value:
        print(f"biblo {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=_version, is_eager=True, help="Show the version."),
    ] = None,
) -> None:
    """Search, borrow and reserve at Danish public libraries, from the terminal.

    Start with [bold]biblo libraries <your town>[/bold] and [bold]biblo auth login[/bold].
    """


app.add_typer(auth.app, name="auth")

CATALOG = "Catalogue"
ACCOUNT = "Your account"
LIBRARY = "The library"
MORE = "More"

app.command(rich_help_panel=CATALOG)(catalog.search)
app.command(rich_help_panel=CATALOG)(catalog.work)
app.command(rich_help_panel=ACCOUNT)(account.loans)
app.command(rich_help_panel=ACCOUNT)(account.borrow)
app.command(rich_help_panel=ACCOUNT)(account.renew)
app.command(rich_help_panel=ACCOUNT)(account.reservations)
app.command(rich_help_panel=ACCOUNT)(account.reserve)
app.command(rich_help_panel=ACCOUNT)(account.cancel)
app.add_typer(account.favorites, name="favorites", rich_help_panel=ACCOUNT)
app.command(rich_help_panel=ACCOUNT)(account.account)
app.command(rich_help_panel=ACCOUNT)(account.messages)
app.command(rich_help_panel=LIBRARY)(libraries.libraries)
app.command(rich_help_panel=LIBRARY)(libraries.branches)
app.command(rich_help_panel=LIBRARY)(libraries.hours)
app.command(rich_help_panel=LIBRARY)(libraries.events)
app.command(rich_help_panel=MORE)(api.api)


def main(argv: list[str] | None = None) -> None:
    """Run the CLI; always ends with SystemExit carrying the exit status."""
    try:
        app(args=argv, prog_name="biblo", standalone_mode=True)
    except NotLoggedInError as e:
        err.print(f"[red]{e}.[/red] Log in with: [bold]biblo auth login[/bold]")
        sys.exit(EXIT_NOT_LOGGED_IN)
    except InvalidCredentialsError as e:
        err.print(f"[red]Login refused:[/red] {e}")
        sys.exit(EXIT_ERROR)
    except UsageError as e:
        err.print(f"[red]{e}[/red]")
        sys.exit(EXIT_USAGE)
    except NoLibraryError:
        err.print(
            "[red]Which library?[/red] Pass --library (e.g. -l vejle), set BIBLO_LIBRARY, "
            "or log in with [bold]biblo auth login[/bold]."
        )
        sys.exit(EXIT_USAGE)
    except BibloConnectionError as e:
        err.print(f"[red]{e}[/red]\nCheck your connection and try again.")
        sys.exit(EXIT_ERROR)
    except ResponseValidationError as e:
        err.print(f"[red]{e}[/red]")
        sys.exit(EXIT_ERROR)
    except BibloError as e:
        err.print(f"[red]{e}[/red]")
        sys.exit(EXIT_ERROR)
    except KeyboardInterrupt:
        sys.exit(130)
