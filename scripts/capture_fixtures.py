"""Record real API responses as test fixtures.

    uv run python scripts/capture_fixtures.py [NAME ...]
    uv run python scripts/capture_fixtures.py --session ~/.config/biblo/session.json [NAME ...]

Without --session: the anonymous endpoints (ENDPOINTS), and login.bib.dk's login form as
login_page.html. Names (keys of ENDPOINTS) record only those fixtures.

With --session: the logged-in account's GET endpoints (ACCOUNT_ENDPOINTS), under every
`x-app-type` each one accepts. Names (keys of ACCOUNT_ENDPOINTS) record only those. It sends
GETs only: it never refreshes the session, borrows, reserves or changes anything. Biblo's
answer is `<name>.json`; another app type's is `<name>_<app type>.json`, kept only when it
differs from Biblo's. Personal fields are replaced by placeholders (Anonymiser), the same real
value by the same placeholder in every file. If a real value still shows up anywhere, nothing
is written and the script exits 1, naming the field but not the value. Review the diff of
tests/fixtures before committing it.

Writes tests/fixtures/*.json as `{"status": ..., "body": ...}`. Re-run when the API changes;
the tests parse these strictly.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from biblo import FileTokenStorage, _base
from biblo._app_types import ACCEPTED_APP_TYPES, DEFAULT_APP_TYPE
from biblo._constants import API_BASE_URL, BROWSER_USER_AGENT

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
LIBRARY = "763000"  # Vejle Bibliotekerne
WORK = "work-of:870970-basis:22252852"  # Harry Potter og De Vises Sten
RECORD = "870970-basis:141629433"  # ... its e-book
PAGE = "2cc7760a-33ff-4268-b07a-d0d283dc88f4"  # Biblo's "Opdag" (known-pages' explore)
ELEMENT = "946be7d1-09e1-4b16-8c9d-aa194d4e39aa"  # ... its "Populære titler" grid

ENDPOINTS = {
    "agencies": ("/v1/agencies", {}),
    "agency_configuration": ("/v1/agencies/{agencyId}/configuration", {}),
    "branches": ("/v1/agencies/{agencyId}/branches", {}),
    "open_status": ("/v1/agencies/{agencyId}/openstatus", {}),
    "events": ("/v1/agencies/{agencyId}/events", {}),
    "search": ("/v1/agencies/{agencyId}/search/works", {"q": "harry potter", "limit": 5}),
    "facets": ("/v1/agencies/{agencyId}/search/facets", {"q": "harry potter", "valuesLimit": 3}),
    "suggest": ("/v1/agencies/{agencyId}/search/suggest", {"q": "harr"}),
    "work": (f"/v1/agencies/{{agencyId}}/works/{WORK}", {}),
    "availability": (f"/v1/agencies/{{agencyId}}/works/{WORK}/material-type-status", {}),
    "record": (f"/v1/agencies/{{agencyId}}/records/{RECORD}", {}),
    "categories": ("/v1/agencies/{agencyId}/content/categories", {}),
    "known_pages": ("/v1/agencies/{agencyId}/content/known-pages", {}),
    "known_pages_all": ("/v1/agencies/{agencyId}/content/known-pages", {"_app_type": "mybiblo"}),
    "page": (f"/v1/agencies/{{agencyId}}/content/pages/{PAGE}", {}),
    "page_element": (f"/v1/agencies/{{agencyId}}/content/pages/{PAGE}/elements/{ELEMENT}", {}),
    "recommendations": (f"/v1/agencies/{{agencyId}}/works/{WORK}/recommendations", {}),
    # The same work and search as Mit Biblo sees them: print and CDs too (catalogue="all").
    "search_all": (
        "/v1/agencies/{agencyId}/search/works",
        {"q": "harry potter", "limit": 5, "_app_type": "mybiblo"},
    ),
    "work_all": (f"/v1/agencies/{{agencyId}}/works/{WORK}", {"_app_type": "mybiblo"}),
    "error_forbidden_app_type": ("/v1/agencies/{agencyId}/branches", {"_app_type": "biblo"}),
}

# Each is a GET in ACCEPTED_APP_TYPES. {agencyId} is the session's login library.
ACCOUNT_ENDPOINTS = {
    "account": "/v1/agencies/{agencyId}/account",
    "account_loans": "/v1/agencies/{agencyId}/account/loans",
    "account_reservations": "/v1/agencies/{agencyId}/account/reservations",
    "account_fees": "/v1/agencies/{agencyId}/account/fees",
    "account_quotas": "/v1/agencies/{agencyId}/account/quotas",
    "favorites": "/v1/user/favorites",
    "messages": "/v1/user/messages",
}


def main(
    argv: list[str] | None = None, http: httpx.Client | None = None, out: Path = FIXTURES
) -> int:
    """Records the fixtures; returns the exit code. Tests pass the fake API as `http`."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("names", nargs="*", metavar="NAME", help="record only these fixtures")
    parser.add_argument(
        "--session", type=Path, help="a logged-in session file: record its account endpoints"
    )
    args = parser.parse_args(argv)
    known = ACCOUNT_ENDPOINTS if args.session else ENDPOINTS
    wanted = set(args.names)
    if unknown := wanted - known.keys():
        print(f"Unknown fixtures: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    client = http or httpx.Client(timeout=30)
    try:
        if args.session:
            return capture_account(args.session, wanted, client, out)
        capture_anonymous(wanted, client, out)
        return 0
    finally:
        if http is None:
            client.close()


# ------------------------------------------------------------------------------ anonymous


def capture_anonymous(wanted: set[str], http: httpx.Client, out: Path) -> None:
    """Records every anonymous fixture, or only those in `wanted`."""
    out.mkdir(parents=True, exist_ok=True)
    for name, (template, params) in ENDPOINTS.items():
        if wanted and name not in wanted:
            continue
        params = dict(params)
        headers = _base.headers("GET", template, "fixture-capture", None, _base.api_user_agent())
        if forced := params.pop("_app_type", None):
            headers["x-app-type"] = forced
        path = _base.fill_path(template, {"agencyId": LIBRARY})
        response = http.get(f"{API_BASE_URL}{path}", params=params, headers=headers)
        body = response.json()
        if name != "agencies":  # tests look libraries up by name
            _trim(body)
        _write(out, name, {"status": response.status_code, "body": body})
        print(f"{name}: {response.status_code}")

    if wanted:
        return
    login = http.get(
        _base.authorize_url(), headers={"User-Agent": BROWSER_USER_AGENT}, follow_redirects=True
    )
    (out / "login_page.html").write_text(login.text)
    print(f"login_page.html: {login.status_code}")


def _trim(body: object) -> None:
    """Keep fixtures small: long lists are cut to their first few items."""
    if isinstance(body, dict):
        for key, value in body.items():
            if isinstance(value, list) and len(value) > 5 and key in ("data", "elements"):
                del value[5:]
            _trim(value)
    elif isinstance(body, list):
        for item in body:
            _trim(item)


# -------------------------------------------------------------------------------- account


def capture_account(session_file: Path, wanted: set[str], http: httpx.Client, out: Path) -> int:
    """Records the account endpoints in `wanted` (all if empty) under each app type."""
    session = _base.Session.from_dict(FileTokenStorage(session_file).load())
    if session is None or session.tokens is None or not session.library_id:
        return _fail(f"{session_file} holds no logged-in session. Run `biblo auth login`.")
    if session.tokens.expires_soon:
        # Refreshing is a POST, and this script only reads.
        return _fail(
            "The session's access token has expired or is about to. Run an account command "
            "such as `biblo account` to refresh it, then try again."
        )

    answers: dict[str, dict[str, Any]] = {}
    for name, template in ACCOUNT_ENDPOINTS.items():
        if wanted and name not in wanted:
            continue
        path = _base.fill_path(template, {"agencyId": session.library_id})
        answers[name] = {}
        for app_type in _biblo_first(ACCEPTED_APP_TYPES[("GET", template)]):
            headers = _base.headers(
                "GET",
                template,
                session.installation_id,
                session.tokens,
                _base.api_user_agent("fixture-capture"),
                app_type,
            )
            response = http.get(f"{API_BASE_URL}{path}", headers=headers)
            if response.status_code != 200:
                return _fail(
                    f"{name} as {app_type}: HTTP {response.status_code}. Nothing was written."
                )
            answers[name][app_type] = response.json()

    anonymiser = Anonymiser()
    fixtures: dict[str, dict[str, Any] | None] = {}
    for name, by_app_type in answers.items():
        for app_type, body in by_app_type.items():
            if app_type == DEFAULT_APP_TYPE:
                file = name
            else:
                file = f"{name}_{app_type}"
                if body == by_app_type.get(DEFAULT_APP_TYPE):
                    fixtures[file] = None  # the same as Biblo's: not kept
                    continue
            fixtures[file] = {"status": 200, "body": anonymiser.anonymise(body, file)}

    texts = {file: _dump(fixture) for file, fixture in fixtures.items() if fixture is not None}
    if leaks := [
        f"{file}.json: {leak}" for file, text in texts.items() for leak in anonymiser.leaks(text)
    ]:
        print("Personal values are left after anonymising. Nothing was written:", file=sys.stderr)
        for leak in leaks:
            print(f"  {leak}", file=sys.stderr)
        return 1

    out.mkdir(parents=True, exist_ok=True)
    for file, fixture in fixtures.items():
        target = out / f"{file}.json"
        if fixture is None:
            if target.exists():
                target.unlink()
                print(f"{file}: removed, now the same as Biblo's answer")
            continue
        target.write_text(texts[file])
        print(f"{file}: written")
    print(f"Review the diff of {out} before committing it.")
    return 0


def _biblo_first(app_types: tuple[str, ...]) -> list[str]:
    """Biblo's answer is the base fixture, so it is anonymised first and numbers first."""
    return sorted(app_types, key=lambda app_type: app_type != DEFAULT_APP_TYPE)


KIND_LABELS = {
    "name": "account name",
    "email": "email address",
    "phone": "phone number",
    "address": "address",
    "patron": "patron or user id",
    "loan": "loan id",
    "reservation": "reservation id",
    "claim": "claim id",
    "message": "message id",
    "fee": "fee id",
}
PLACEHOLDERS: dict[str, Callable[[int], str]] = {
    "name": lambda n: "Test Låner" if n == 1 else f"Test Låner {n}",
    "email": lambda n: f"laaner{n}@example.com",
    "phone": lambda n: str(12345670 + n),
    "address": lambda n: f"Testvej {n}",
    "patron": lambda n: f"TestPatron{n:04d}",
    "loan": lambda n: f"TestLoan{n:012d}",  # 20 characters, like the real ones
    "reservation": lambda n: str(900000000 + n),  # 9 digits, like the real ones
    "claim": lambda n: f"Xxx{n:03d}",
    "message": lambda n: f"00000000-0000-4000-8000-{n:012d}",
    "fee": lambda n: f"TestFee{n:04d}",
}
# Personal wherever they occur. The value may be a string, a number, or an object or list
# whose strings and numbers are then all personal.
PERSONAL_KEYS = {
    "emailAddress": "email",
    "email": "email",
    "phoneNumber": "phone",
    "phone": "phone",
    "mobile": "phone",
    "mobilePhone": "phone",
    "address": "address",
    "postalAddress": "address",
    "street": "address",
    "patronId": "patron",
    "patronIdentifier": "patron",
    "patronIdOfReservation": "patron",
    "patronInformation": "patron",
    "userId": "patron",
    "cpr": "patron",
    "loanId": "loan",
    "loanIds": "loan",
    "reservationId": "reservation",
    "reservationIds": "reservation",
    "claimId": "claim",
    "claimIds": "claim",
    "feeId": "fee",
}


class Anonymiser:
    """Replaces personal fields by placeholders, recognised by field name (`_kind`).

    Public ids stay: library and branch ids, work and record ids. One real value always gets
    the same placeholder, so items still link up across fixtures. The real values are kept
    in memory only, for `leaks`."""

    def __init__(self) -> None:
        self.placeholders: dict[str, str] = {}  # real value -> placeholder
        self.seen_at: dict[str, str] = {}  # real value -> the field it was first seen in
        self.counts: Counter[str] = Counter()

    def anonymise(self, value: Any, file: str) -> Any:
        """A copy of the JSON `value` with its personal fields replaced."""
        return self._walk(value, file, "", None)

    def leaks(self, text: str) -> list[str]:
        """Which fields' real values `text` still holds, as their label and source: never the
        value itself."""
        folded = text.casefold()
        found = []
        for real, placeholder in self.placeholders.items():
            if real == placeholder:  # already a placeholder, e.g. a re-recorded fixture
                continue
            forms = {real, json.dumps(real, ensure_ascii=False)[1:-1]}
            if any(form.casefold() in folded for form in forms):
                found.append(self.seen_at[real])
        return sorted(set(found))

    def _walk(self, value: Any, file: str, where: str, kind: str | None) -> Any:
        if isinstance(value, dict):
            return {
                key: self._walk(
                    item, file, f"{where}.{key}" if where else key, kind or _kind(key, value, where)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._walk(item, file, f"{where}[]", kind) for item in value]
        if kind is None or isinstance(value, bool) or not isinstance(value, str | int):
            return value
        real = str(value)
        if not real:
            return value
        if real not in self.placeholders:
            self.counts[kind] += 1
            self.placeholders[real] = PLACEHOLDERS[kind](self.counts[kind])
            self.seen_at[real] = f"the {KIND_LABELS[kind]} from {file}.json {where}"
        placeholder = self.placeholders[real]
        return int(placeholder) if isinstance(value, int) and placeholder.isdigit() else placeholder


def _kind(key: str, parent: dict[str, Any], where: str) -> str | None:
    """What personal value `parent[key]` holds, or None. `where` is the parent's path."""
    if key in PERSONAL_KEYS:
        return PERSONAL_KEYS[key]
    if key == "name" and "accountType" in parent:  # a branch's or creator's name is public
        return "name"
    if key == "id":
        if where.endswith("claim"):
            return "claim"
        if "loanDate" in parent:
            return "loan"
        if "reservationDate" in parent:
            return "reservation"
        if "contentPath" in parent or "seen" in parent:
            return "message"
    return None


def _dump(fixture: dict[str, Any]) -> str:
    return json.dumps(fixture, indent=2, ensure_ascii=False) + "\n"


def _write(out: Path, name: str, fixture: dict[str, Any]) -> None:
    (out / f"{name}.json").write_text(_dump(fixture))


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
