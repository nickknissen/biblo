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

    @property
    def access_token(self) -> str:
        return f"access-{self.token_number}"

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == LOGIN:
            return self._login(request)
        if request.url.host != API:
            raise AssertionError(f"Unexpected host {request.url}")
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

        work = f"/v1/agencies/{LIBRARY}/works/work-of:870970-basis:22252852"
        public = {
            "/v1/agencies": "agencies",
            f"/v1/agencies/{LIBRARY}/configuration": "agency_configuration",
            f"/v1/agencies/{LIBRARY}/branches": "branches",
            f"/v1/agencies/{LIBRARY}/openstatus": "open_status",
            f"/v1/agencies/{LIBRARY}/events": "events",
            f"/v1/agencies/{LIBRARY}/search/facets": "facets",
            f"/v1/agencies/{LIBRARY}/search/suggest": "suggest",
            work: "work",
            f"{work}/material-type-status": "availability",
            f"/v1/agencies/{LIBRARY}/records/870970-basis:141629433": "record",
            f"/v1/agencies/{LIBRARY}/content/categories": "categories",
        }
        if method == "GET" and path in public:
            return httpx.Response(200, json=fixture(public[path]))
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
        works = fixture("search")["data"]["works"]
        offset = int(request.url.params.get("offset", 0))
        limit = int(request.url.params.get("limit", 10))
        catalogue = [works[i % len(works)] for i in range(12)]
        page = catalogue[offset : offset + limit]
        return httpx.Response(200, json={"data": {"hitcount": 12, "works": page}})

    def _account(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        base = f"/v1/agencies/{LIBRARY}/account"
        if (method, path) == ("GET", base):
            return httpx.Response(200, json={"data": ACCOUNT})
        if (method, path) == ("GET", f"{base}/quotas"):
            return httpx.Response(200, json={"data": QUOTAS})
        if (method, path) == ("GET", f"{base}/fees"):
            return httpx.Response(200, json={"data": []})
        if (method, path) == ("GET", f"{base}/loans"):
            return httpx.Response(200, json={"data": self.loans})
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
            return httpx.Response(200, json={"data": [RESERVATION]})
        if (method, path) == ("POST", f"{base}/reservations"):
            return httpx.Response(200, json={"data": [RESERVATION]})
        if (method, path) == ("DELETE", f"{base}/reservations"):
            return httpx.Response(204)
        if (method, path) == ("GET", "/v1/user/favorites"):
            return httpx.Response(200, json={"data": [fixture("work")["data"]]})
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


def _error(status: int, code: int, kind: str, message: str) -> httpx.Response:
    return httpx.Response(status, json={"id": code, "type": kind, "message": message})
