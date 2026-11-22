"""A fake Biblo API and login.bib.dk, served through httpx.MockTransport.

Catalogue endpoints answer with real responses recorded by scripts/capture_fixtures.py.
Account endpoints answer with data shaped by the spec (we can't record a real user's).
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx

from biblo._constants import API_KEY, CLIENT_ID, CLIENT_SECRET

FIXTURES = Path(__file__).parent / "fixtures"
API = "api.detdigitalefolkebibliotek.dk"
LOGIN = "login.bib.dk"
USER, PIN = "0102031234", "1234"
LIBRARY = "763000"
PAGE = "2cc7760a-33ff-4268-b07a-d0d283dc88f4"  # the recorded content page


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())["body"]


LOAN: dict[str, Any] = {
    "id": "123456",
    "loanDate": "2026-11-10T10:00:00.000Z",
    "dueDate": "2026-12-10T10:00:00.000Z",
    "isRenewable": True,
    "record": {
        "recordId": "870970-basis:141629433",
        "workId": "work-of:870970-basis:22252852",
        "title": "Harry Potter og De Vises Sten",
        "author": "Joanne K. Rowling",
        "publishYear": "2025",
        "materialType": {
            "display": "e-bog",
            "displayWithPronoun": "Denne e-bog",
            "category": "EBOOKS",
        },
        "blueTitle": False,
        "series": True,
    },
}
RESERVATION = {
    "id": "r-1",
    "reservationDate": "2026-11-01T10:00:00.000Z",
    "state": "RESERVED",
    "details": {
        "recordId": "870970-basis:141661116",
        "workId": "work-of:870970-basis:22252852",
        "title": "Harry Potter og De Vises Sten",
        "materialType": {
            "display": "lydbog (online)",
            "displayWithPronoun": "Denne lydbog (online)",
            "category": "AUDIO_BOOKS_ONLINE",
        },
        "blueTitle": False,
    },
}
PRINTED_BOOK = {"display": "bog", "displayWithPronoun": "Denne bog", "category": "BOOKS"}
# A made-up printed book: fixtures carry no real account's ids (#3).
PHYSICAL_RECORD = "870970-basis:99999901"
# Physical items, as `x-app-type: mybiblo` lists them (shape recorded 2026-11-20, #3).
PHYSICAL_RESERVATION = {
    "id": "p-1",
    "reservationDate": "2026-11-15T10:00:00.000Z",
    "state": "RESERVED",
    "physical": {"expiryDate": "2027-05-15", "pickupBranch": "DK-763004", "numberInQueue": 3},
    "details": {
        "recordId": PHYSICAL_RECORD,
        "workId": "work-of:870970-basis:99999901",
        "title": "En trykt bog",
        "coverUrl": None,
        "author": "En Forfatter",
        "publishYear": "2024",
        "materialType": PRINTED_BOOK,
        "blueTitle": False,
    },
}
PHYSICAL_LOAN = {
    "id": "p-loan-1",
    "loanDate": "2026-11-12T10:00:00.000Z",
    "dueDate": "2026-12-10T10:00:00.000Z",
    "isRenewable": True,
    "record": {
        "recordId": PHYSICAL_RECORD,
        "workId": "work-of:870970-basis:99999901",
        "title": "En trykt bog",
        "author": "En Forfatter",
        "publishYear": "2024",
        "materialType": PRINTED_BOOK,
        "blueTitle": False,
        "series": False,
    },
}
ACCOUNT = {"accountType": "AdgangsPlatformen", "name": "Test Testesen", "email": None}
QUOTAS = {
    "totalLoanQuotas": {"ebook": 5, "audiobook": 5},
    "totalReservationQuotas": {"ebook": 3, "audiobook": 3},
    "remainingLoanQuotas": {"ebook": 4, "audiobook": 5},
    "remainingReservationQuotas": {"ebook": 3, "audiobook": 2},
}

Handler = Callable[[httpx.Request], httpx.Response]


@dataclass
class FakeBiblo:
    requests: list[httpx.Request] = field(default_factory=list)
    token_number: int = 0
    refresh_works: bool = True
    revoked: set[str] = field(default_factory=set)
    overrides: dict[tuple[str, str], Handler] = field(default_factory=dict)
    loans: list[dict[str, Any]] = field(default_factory=lambda: [LOAN])
    physical_loans: list[dict[str, Any]] = field(default_factory=lambda: [PHYSICAL_LOAN])
    reservations: list[dict[str, Any]] = field(default_factory=lambda: [RESERVATION])
    physical_reservations: list[dict[str, Any]] = field(
        default_factory=lambda: [PHYSICAL_RESERVATION]
    )
    account: dict[str, Any] = field(default_factory=lambda: dict(ACCOUNT))

    @property
    def access_token(self) -> str:
        return f"access-{self.token_number}"

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == LOGIN:
            return self._login(request)
        if request.url.host != API:
            raise AssertionError(f"Unexpected host {request.url}")
        if (invalid := _check_body(request)) is not None:
            return _error(400, 4000, "VALIDATION", invalid)
        if (override := self.overrides.get((request.method, request.url.path))) is not None:
            return override(request)
        return self._api(request)

    def api_requests(self, path_part: str = "") -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == API and path_part in r.url.path]

    # --------------------------------------------------------------- login.bib.dk

    def _login(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/oauth/authorize":
            assert request.url.params["client_id"] == CLIENT_ID
            assert request.url.params["redirect_uri"] == "biblo://"
            return httpx.Response(302, headers={"location": "/login"})
        if path == "/login" and request.method == "GET":
            return httpx.Response(200, text=(FIXTURES / "login_page.html").read_text())
        if path.startswith("/login/identityProviderCallback/borchk/") and request.method == "POST":
            form = parse_qs(request.content.decode())
            ok = form.get("loginBibDkUserId") == [USER] and form.get("pincode") == [PIN]
            if not ok or form.get("agency") != [LIBRARY]:
                page = (FIXTURES / "login_page.html").read_text()
                page = page.replace(
                    "<body",
                    '<body><div class="error-message">Forkert lånernummer eller pinkode</div',
                    1,
                )
                return httpx.Response(200, text=page)
            return httpx.Response(302, headers={"location": "/oauth/authorize/continue"})
        if path == "/oauth/authorize/continue":
            return httpx.Response(302, headers={"location": "biblo://?code=the-code&state="})
        return httpx.Response(404)

    # ------------------------------------------------------------------------ API

    def _api(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("x-api-key") != API_KEY:
            return _error(401, 4100, "UNAUTHORIZED", "Missing api key")
        path = request.url.path
        method = request.method

        if path == "/v1/auth/login":
            self._check_client_auth(request)
            form = parse_qs(request.content.decode())
            assert form == {"code": ["the-code"], "redirect_uri": ["biblo://"]}, form
            return self._new_tokens()
        if path == "/v1/auth/token-refresh":
            self._check_client_auth(request)
            form = parse_qs(request.content.decode())
            if not self.refresh_works or form["refresh_token"] != [f"refresh-{self.token_number}"]:
                return _error(401, 4103, "UNAUTHORIZED", "Invalid refresh token")
            return self._new_tokens()
        if path == "/v1/auth/logout":
            return httpx.Response(204)

        mybiblo_only = "/branches" in path or "/openstatus" in path
        if mybiblo_only and request.headers["x-app-type"] != "mybiblo":
            return httpx.Response(403, json=fixture("error_forbidden_app_type"))

        # Mit Biblo sees print and CDs too: where one was recorded, its own fixture (#1).
        mit_biblo = request.headers["x-app-type"] == "mybiblo"
        lib = f"/v1/agencies/{LIBRARY}"
        work = f"{lib}/works/work-of:870970-basis:22252852"
        page = f"{lib}/content/pages/{PAGE}"
        public = {
            "/v1/agencies": "agencies",
            f"{lib}/configuration": "agency_configuration",
            f"{lib}/branches": "branches",
            f"{lib}/openstatus": "open_status",
            f"{lib}/events": "events",
            f"{lib}/search/facets": "facets",
            f"{lib}/search/suggest": "suggest",
            work: "work_all" if mit_biblo else "work",
            f"{work}/material-type-status": "availability",
            f"{work}/recommendations": "recommendations",
            f"{lib}/records/870970-basis:141629433": "record",
            f"{lib}/content/categories": "categories",
            f"{lib}/content/known-pages": "known_pages_all" if mit_biblo else "known_pages",
            page: "page",
        }
        if method == "GET" and path in public:
            return httpx.Response(200, json=fixture(public[path]))
        if method == "GET" and path.startswith(f"{page}/elements/"):
            return httpx.Response(200, json=fixture("page_element"))
        if method == "GET" and (match := re.fullmatch(r"/v1/agencies/(\d+)", path)):
            agency = [a for a in fixture("agencies")["data"] if a["id"] == match.group(1)]
            if agency:
                return httpx.Response(200, json={"data": agency[0]})
        if method == "GET" and path == f"/v1/agencies/{LIBRARY}/search/works":
            return self._search(request)

        if path.startswith(f"/v1/agencies/{LIBRARY}/account") or path.startswith("/v1/user"):
            if (denied := self._check_bearer(request)) is not None:
                return denied
            return self._account(request)
        return _error(404, 4000, "NOT_FOUND", f"Cannot find any route matching [{method}] {path}")

    def _search(self, request: httpx.Request) -> httpx.Response:
        """The recorded page of 5 works, sliced as if the catalogue had 12 hits."""
        mit_biblo = request.headers["x-app-type"] == "mybiblo"
        works = fixture("search_all" if mit_biblo else "search")["data"]["works"]
        offset = int(request.url.params.get("offset", 0))
        limit = int(request.url.params.get("limit", 10))
        catalogue = [works[i % len(works)] for i in range(12)]
        page = catalogue[offset : offset + limit]
        return httpx.Response(200, json={"data": {"hitcount": 12, "works": page}})

    def _account(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        # The real API lists physical items only to Mit Biblo, and digital ones (as far as we
        # know) only to the others (#3).
        mit_biblo = request.headers["x-app-type"] == "mybiblo"
        base = f"/v1/agencies/{LIBRARY}/account"
        if (method, path) == ("GET", base):
            return httpx.Response(200, json={"data": self.account})
        if (method, path) == ("PATCH", base):
            body = json.loads(request.content)
            if (branch_id := body.get("preferredPickupBranchId")) is not None:
                [branch] = [b for b in fixture("branches")["data"] if b["id"] == branch_id]
                self.account["preferredPickupBranch"] = {"id": branch_id, "name": branch["name"]}
            return httpx.Response(200, json={"data": self.account})
        if (method, path) == ("GET", f"{base}/quotas"):
            return httpx.Response(200, json={"data": QUOTAS})
        if (method, path) == ("GET", f"{base}/fees"):
            return httpx.Response(200, json={"data": []})
        if (method, path) == ("GET", f"{base}/loans"):
            return httpx.Response(
                200, json={"data": self.physical_loans if mit_biblo else self.loans}
            )
        if (method, path) == ("POST", f"{base}/loans"):
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "data": {
                        **LOAN,
                        "id": "new-loan",
                        "record": {**LOAN["record"], "recordId": body["recordId"]},
                    }
                },
            )
        if (method, path) == ("POST", f"{base}/loans/renew"):
            ids = request.url.params.get_list("loanIds")
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"loanDetails": {**LOAN, "id": i}, "renewalStatus": ["renewed"]}
                        for i in ids
                    ]
                },
            )
        if (method, path) == ("GET", f"{base}/reservations"):
            listed = self.physical_reservations if mit_biblo else self.reservations
            return httpx.Response(200, json={"data": listed})
        if (method, path) == ("POST", f"{base}/reservations"):
            record_ids = json.loads(request.content)["recordIds"]
            if PHYSICAL_RECORD in record_ids and not mit_biblo:
                # The status and message the real API answered with on 2026-11-20 (#3).
                message = f"No material types found for records: {PHYSICAL_RECORD}"
                return _error(400, 4000, "VALIDATION", message)
            return httpx.Response(200, json={"data": [RESERVATION]})
        if (method, path) == ("DELETE", f"{base}/reservations"):
            return httpx.Response(204)
        if (method, path) == ("GET", "/v1/user/favorites"):
            favorite = fixture("work_all" if mit_biblo else "work")["data"]
            return httpx.Response(200, json={"data": [favorite]})
        if method in ("PUT", "DELETE") and path.startswith("/v1/user/favorites"):
            return httpx.Response(204)
        if (method, path) == ("GET", "/v1/user/messages"):
            return httpx.Response(200, json={"data": {"messages": []}})
        return _error(404, 4000, "NOT_FOUND", f"No fake for [{method}] {path}")

    def _check_client_auth(self, request: httpx.Request) -> None:
        expected = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        assert request.headers["authorization"] == f"Basic {expected}"

    def _check_bearer(self, request: httpx.Request) -> httpx.Response | None:
        header = request.headers.get("authorization", "")
        token = re.sub(r"^Bearer ", "", header)
        if not header.startswith("Bearer ") or token != self.access_token or token in self.revoked:
            return _error(401, 4103, "UNAUTHORIZED", "Missing or invalid bearer token")
        return None

    def _new_tokens(self) -> httpx.Response:
        self.token_number += 1
        return httpx.Response(
            200,
            json={
                "access_token": self.access_token,
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": f"refresh-{self.token_number}",
            },
        )


SPEC = json.loads((Path(__file__).parents[1] / "spec" / "ddf-openapi.json").read_text())
BODY_SCHEMAS = [
    (method.upper(), re.compile(re.sub(r"\{[^}]+\}", "[^/]+", template)), body["schema"])
    for template, operations in SPEC["paths"].items()
    for method, operation in operations.items()
    if isinstance(operation, dict)
    if (body := operation.get("requestBody", {}).get("content", {}).get("application/json"))
]


def _check_body(request: httpx.Request) -> str | None:
    """Why a JSON body doesn't match the spec's request schema, or None if it does.

    Checks keys, required fields and types, not the spec's patterns and lengths. Unlike the
    spec, unknown keys are always refused, so a misspelled field can't pass unnoticed.
    """
    if not request.content or "json" not in request.headers.get("content-type", ""):
        return None
    for method, path, schema in BODY_SCHEMAS:
        if method == request.method and path.fullmatch(request.url.path):
            return _mismatch(json.loads(request.content), schema, "body")
    return None


def _mismatch(value: Any, schema: dict[str, Any], where: str) -> str | None:
    if value is None and schema.get("nullable"):
        return None
    if "$ref" in schema:
        return _mismatch(value, SPEC["components"]["schemas"][schema["$ref"].split("/")[-1]], where)
    if value is None:
        return f"{where} is null"
    for part in schema.get("allOf", []):
        if (problem := _mismatch(value, part, where)) is not None:
            return problem
    if options := schema.get("anyOf") or schema.get("oneOf"):
        problems = [_mismatch(value, option, where) for option in options]
        if None not in problems:
            return " or ".join(p for p in problems if p)
    if "enum" in schema and value not in schema["enum"]:
        return f"{where} is {value!r}, not one of {schema['enum']}"
    match schema.get("type"):
        case "object":
            if not isinstance(value, dict):
                return f"{where} is not an object"
            properties = schema.get("properties", {})
            if unknown := sorted(set(value) - set(properties)):
                return f"{where} has unknown keys {unknown}"
            if missing := sorted(set(schema.get("required", [])) - set(value)):
                return f"{where} misses {missing}"
            for key, item in value.items():
                if (problem := _mismatch(item, properties[key], f"{where}.{key}")) is not None:
                    return problem
        case "array":
            if not isinstance(value, list):
                return f"{where} is not an array"
            for i, item in enumerate(value):
                if (
                    problem := _mismatch(item, schema.get("items", {}), f"{where}[{i}]")
                ) is not None:
                    return problem
        case "string" if not isinstance(value, str):
            return f"{where} is not a string"
        case "integer" if not isinstance(value, int) or isinstance(value, bool):
            return f"{where} is not an integer"
        case "number" if not isinstance(value, int | float) or isinstance(value, bool):
            return f"{where} is not a number"
        case "boolean" if not isinstance(value, bool):
            return f"{where} is not a boolean"
    return None


def _error(status: int, code: int, kind: str, message: str) -> httpx.Response:
    return httpx.Response(status, json={"id": code, "type": kind, "message": message})
