"""Record real API responses as test fixtures (anonymous endpoints only).

    uv run python scripts/capture_fixtures.py

Writes tests/fixtures/*.json with the raw JSON bodies, and login_page.html with
login.bib.dk's login form. Re-run when the API changes; the tests parse these strictly.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from biblo import _base
from biblo._constants import API_BASE_URL, BROWSER_USER_AGENT

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
LIBRARY = "763000"  # Vejle Bibliotekerne
WORK = "work-of:870970-basis:22252852"  # Harry Potter og De Vises Sten
RECORD = "870970-basis:141629433"  # ... its e-book

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
    "error_forbidden_app_type": ("/v1/agencies/{agencyId}/branches", {"_app_type": "biblo"}),
}


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=30) as http:
        for name, (template, params) in ENDPOINTS.items():
            params = dict(params)
            headers = _base.headers("GET", template, "fixture-capture", None)
            if forced := params.pop("_app_type", None):
                headers["x-app-type"] = forced
            path = _base.fill_path(template, {"agencyId": LIBRARY})
            response = http.get(f"{API_BASE_URL}{path}", params=params, headers=headers)
            body = response.json()
            if name != "agencies":  # tests look libraries up by name
                _trim(body)
            out = {"status": response.status_code, "body": body}
            (FIXTURES / f"{name}.json").write_text(
                json.dumps(out, indent=2, ensure_ascii=False) + "\n"
            )
            print(f"{name}: {response.status_code}")

        login = http.get(
            _base.authorize_url(), headers={"User-Agent": BROWSER_USER_AGENT}, follow_redirects=True
        )
        (FIXTURES / "login_page.html").write_text(login.text)
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


if __name__ == "__main__":
    main()
