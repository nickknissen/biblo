from __future__ import annotations

from typing import Annotated

import typer

from .._sync import Biblo
from ..exceptions import BadRequestError, BibloError, PickupBranchRequiredError
from ._output import console, day, due, join, print_json, success, table, warn
from ._state import JsonOption, LibraryOption, UsageError, client, resolve_branch

DIGITAL_CATEGORIES = ("EBOOKS", "AUDIO_BOOKS_ONLINE", "PODCASTS")

account = typer.Typer(
    help="Show your details, digital loans left this period, and fees.",
    invoke_without_command=True,
)


@account.callback()
def account_show(
    ctx: typer.Context, library: LibraryOption = None, as_json: JsonOption = False
) -> None:
    """Show your details, digital loans left this period, and fees."""
    if ctx.invoked_subcommand is not None:
        return
    with client(library) as biblo:
        me = biblo.account.get()
        quotas = biblo.account.quotas()
        fees = biblo.account.fees()
    if as_json:
        print_json({"account": me, "quotas": quotas, "fees": fees})
        return
    console.print(f"[bold]{me.name or 'Unknown name'}[/bold]")
    if me.email:
        console.print(f"Email: {me.email.email_address}")
    if me.phone_number:
        console.print(f"Phone: {me.phone_number.phone_number}")
    if me.preferred_pickup_branch:
        console.print(f"Pickup branch: {me.preferred_pickup_branch.name}")
    t = table("", "E-books", "Audiobooks", title="\nDigital quota")
    rows = [
        ("Loans left", quotas.remaining_loan_quotas, quotas.total_loan_quotas),
        ("Reservations left", quotas.remaining_reservation_quotas, quotas.total_reservation_quotas),
    ]
    for label, left, total in rows:
        t.add_row(label, f"{left.ebook} of {total.ebook}", f"{left.audiobook} of {total.audiobook}")
    console.print(t)
    if fees:
        t = table("Amount", "Reason", "Due", title="\nFees")
        for fee in fees:
            t.add_row(f"{fee.amount_in_dkk:.2f} kr", fee.reason, day(fee.due_date))
        console.print(t)


@account.command("set")
def account_set(
    pickup_branch: Annotated[
        str | None,
        typer.Option(
            help="Where physical reservations are picked up: a branch id or part of its name "
            "(biblo branches). `biblo reserve` uses it when --branch is left out.",
            show_default=False,
        ),
    ] = None,
    as_json: JsonOption = False,
) -> None:
    """Change settings on your account, the same ones the apps and the library's website use."""
    if pickup_branch is None:
        raise UsageError("Nothing to change: pass --pickup-branch")
    with client() as biblo:
        branch = resolve_branch(biblo, pickup_branch)
        me = biblo.account.update(preferred_pickup_branch_id=branch.id)
    if as_json:
        print_json(me)
        return
    success(f"Pickup branch: {branch.name}")


def loans(library: LibraryOption = None, as_json: JsonOption = False) -> None:
    """List your loans, soonest due first."""
    with client(library) as biblo:
        found = sorted(biblo.loans.list(), key=lambda loan: loan.due_date)
    if as_json:
        print_json(found)
        return
    if not found:
        console.print("No loans.")
        return
    t = table("Title", "By", "Format", "Due", "Loan id")
    for loan in found:
        r = loan.record
        renew = "" if loan.is_renewable is not False else " [dim](not renewable)[/dim]"
        t.add_row(
            r.title, r.author or "", r.material_type.display, due(loan.due_date) + renew, loan.id
        )
    console.print(t)


def borrow(
    record_id: Annotated[str, typer.Argument(help="A record id from `biblo work`.")],
    library: LibraryOption = None,
    as_json: JsonOption = False,
) -> None:
    """Borrow an e-book, audiobook or podcast. Uses one of this period's loans."""
    with client(library) as biblo:
        loan = biblo.loans.borrow(record_id)
    if as_json:
        print_json(loan)
        return
    success(f"Borrowed {loan.record.title}, due {due(loan.due_date)}")


def renew(
    loan_ids: Annotated[
        list[str] | None, typer.Argument(help="Loans to renew.", show_default=False)
    ] = None,
    all_loans: Annotated[bool, typer.Option("--all", help="Renew every renewable loan.")] = False,
    library: LibraryOption = None,
    as_json: JsonOption = False,
) -> None:
    """Renew physical loans. Digital loans can't be renewed."""
    titles: dict[str, str] = {}
    with client(library) as biblo:
        if all_loans:
            renewable = [loan for loan in biblo.loans.list() if loan.is_renewable]
            titles = {loan.id: loan.record.title for loan in renewable}
            loan_ids = list(titles)
        if not loan_ids:
            raise UsageError("Name loans to renew, or use --all")
        try:
            renewed = biblo.loans.renew(loan_ids)
        except BadRequestError as e:
            if e.type != "VALIDATION":
                raise
            raise BibloError(_renew_refused(biblo, loan_ids)) from e
    # Like the apps, judge by which loans come back: `renewalStatus` has no documented values.
    returned = {item.loan_details.id for item in renewed}
    if as_json:
        print_json(renewed)
    else:
        for item in renewed:
            loan = item.loan_details
            status = f" [dim]({join(item.renewal_status)})[/dim]" if item.renewal_status else ""
            success(f"{loan.record.title}: due {due(loan.due_date)}{status}")
    for loan_id in loan_ids:
        if loan_id not in returned:
            title = titles.get(loan_id)
            warn(f"{title} ({loan_id}): not renewed" if title else f"{loan_id}: not renewed")
    if not returned:
        raise BibloError("No loans were renewed")


def _renew_refused(biblo: Biblo, loan_ids: list[str]) -> str:
    """The message for the 400 the API answers when any requested loan is digital."""
    message = (
        "Digital loans can't be renewed, only physical ones, so the library refused the "
        "whole request and nothing was renewed."
    )
    try:
        listed = biblo.loans.list()
    except BibloError:
        return message
    digital = [
        loan
        for loan in listed
        if loan.id in loan_ids and loan.record.material_type.category in DIGITAL_CATEGORIES
    ]
    if digital:
        message += " Digital: " + join(f"{loan.record.title} ({loan.id})" for loan in digital)
    return message


def reservations(library: LibraryOption = None, as_json: JsonOption = False) -> None:
    """List your reservations."""
    with client(library) as biblo:
        found = biblo.reservations.list()
    if as_json:
        print_json(found)
        return
    if not found:
        console.print("No reservations.")
        return
    t = table("Title", "Format", "Status", "Reservation id")
    for r in found:
        if r.state == "READY_FOR_PICKUP":
            status = "[green]ready[/green]"
            if r.claim:
                status += f" until {day(r.claim.available_until)}"
            elif r.physical:
                status += f" at {r.physical.pickup_branch} until {day(r.physical.expiry_date)}"
        elif r.physical and r.physical.number_in_queue is not None:
            status = f"number {r.physical.number_in_queue} in queue"
        else:
            status = "reserved"
        t.add_row(r.details.title, r.details.material_type.display, status, r.id)
    console.print(t)


def reserve(
    record_ids: Annotated[list[str], typer.Argument(help="Record ids from `biblo work`.")],
    branch: Annotated[
        str | None,
        typer.Option(
            help="Pickup branch id for physical items (biblo branches). "
            "Default: your account's, set with `biblo account set --pickup-branch`.",
            show_default=False,
        ),
    ] = None,
    days: Annotated[
        int | None,
        typer.Option(
            help="Give up after this many days (the library sets the choices).", show_default=False
        ),
    ] = None,
    library: LibraryOption = None,
    as_json: JsonOption = False,
) -> None:
    """Reserve records that are out, and get them when it's your turn."""
    with client(library) as biblo:
        if branch is None and (preferred := biblo.account.get().preferred_pickup_branch):
            branch = preferred.id
        try:
            made = biblo.reservations.create(record_ids, pickup_branch_id=branch, expiry_days=days)
        except PickupBranchRequiredError as e:
            if branch is not None:
                raise
            raise BibloError(
                f"{e.message}\nThe record may be a physical item, which needs a pickup branch: "
                "pass --branch, or set one with biblo account set --pickup-branch"
            ) from e
    if as_json:
        print_json(made)
        return
    if not made:
        success("Reserved")
    for r in made:
        success(f"Reserved {r.details.title}")


def cancel(
    reservation_ids: Annotated[list[str], typer.Argument(help="Reservation ids.")],
    library: LibraryOption = None,
) -> None:
    """Cancel reservations."""
    with client(library) as biblo:
        biblo.reservations.cancel(reservation_ids)
    success(f"Cancelled {len(reservation_ids)} reservation(s)")


favorites = typer.Typer(help="Your saved works.", invoke_without_command=True)


@favorites.callback()
def favorites_list(ctx: typer.Context, as_json: JsonOption = False) -> None:
    """List your saved works."""
    if ctx.invoked_subcommand is not None:
        return
    with client() as biblo:
        found = biblo.favorites.list()
    if as_json:
        print_json(found)
        return
    if not found:
        console.print("No saved works.")
        return
    t = table("Title", "By", "Formats", "Id")
    for w in found:
        t.add_row(
            w.title,
            join(w.creators),
            join(m.display for m in w.material_types),
            f"[dim]{w.id}[/dim]",
        )
    console.print(t)


@favorites.command("add")
def favorites_add(work_id: Annotated[str, typer.Argument(help="A work id.")]) -> None:
    """Save a work."""
    with client() as biblo:
        biblo.favorites.add(work_id)
    success("Saved")


@favorites.command("remove")
def favorites_remove(work_id: Annotated[str, typer.Argument(help="A work id.")]) -> None:
    """Remove a saved work."""
    with client() as biblo:
        biblo.favorites.remove(work_id)
    success("Removed")


def messages(as_json: JsonOption = False) -> None:
    """List messages from the library."""
    with client() as biblo:
        found = biblo.messages.list()
    if as_json:
        print_json(found)
        return
    if not found:
        console.print("No messages.")
        return
    for msg in sorted(found, key=lambda x: x.created, reverse=True):
        marker = "" if msg.seen else "[bold blue]●[/bold blue] "
        console.print(f"{marker}[bold]{msg.title}[/bold] [dim]{day(msg.created)}[/dim]")
        console.print(f"  {msg.body}\n")
