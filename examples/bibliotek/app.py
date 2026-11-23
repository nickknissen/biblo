"""A bibliotek.dk-style site built only on the Biblo API, through the `biblo` library.

Proof of concept. Run from the repo root:

    uv run --with fastapi --with uvicorn --with jinja2 --with python-multipart \
        uvicorn examples.bibliotek.app:app --reload

then open http://127.0.0.1:8000.

What it shows: search across every material (books, films, music, e-books, ...) with facets,
a work page with editions, availability and where the copies are on the shelf, which other
libraries have it home, and, logged in, loans, reservations and reserving with pickup branch.

The one trick: the Biblo app's catalogue is digital only. The same API answers with the
physical catalogue (and holdings) when a request says it comes from the Mit Biblo app, so an
httpx hook sends `x-app-type: mybiblo` on catalogue reads.
"""

from __future__ import annotations

import asyncio
import re
import secrets
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from biblo import (
    AsyncBiblo,
    AsyncMemoryTokenStorage,
    BadRequestError,
    BibloError,
    InvalidCredentialsError,
    NotFoundError,
    NotLoggedInError,
    models,
)
from biblo.types import SEARCH_FILTER_PARAMS

HERE = Path(__file__).parent
DEFAULT_LIBRARY = "763000"  # Vejle Bibliotekerne
PAGE_SIZE = 20
FACETS_SHOWN = [
    "accessTypes",
    "materialTypesSpecific",
    "fictionNonfiction",
    "childrenOrAdults",
    "mainLanguages",
    "creators",
    "subjects",
    "genreAndForm",
    "year",
]
# The API's camelCase facet ids -> the client's snake_case search filters.
FILTER_FOR_FACET = {camel: snake for snake, camel in SEARCH_FILTER_PARAMS.items()}

# ------------------------------------------------------------------ the physical catalogue

_CATALOGUE_READ = re.compile(r"^/v1/agencies/[^/]+/(search|works|series|records)/")


async def _physical_catalogue(request: httpx.Request) -> None:
    if (
        request.method == "GET"
        and _CATALOGUE_READ.match(request.url.path)
        and not request.url.path.endswith("/sample")
    ):
        request.headers["x-app-type"] = "mybiblo"


# ------------------------------------------------------------------ app state

http = httpx.AsyncClient(timeout=30, event_hooks={"request": [_physical_catalogue]})
anonymous = AsyncBiblo(http_client=http)
sessions: dict[str, AsyncBiblo] = {}  # cookie -> a logged-in user's client (in memory only)
agencies: dict[str, models.Agency] = {}
_cache: dict[tuple[Any, ...], tuple[float, Any]] = {}


async def cached(key: tuple[Any, ...], ttl: float, make: Any) -> Any:
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    value = await make()
    _cache[key] = (time.monotonic() + ttl, value)
    return value


@asynccontextmanager
async def lifespan(_: FastAPI):
    for agency in await anonymous.libraries.list():
        agencies[agency.id] = agency
    yield
    await http.aclose()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


def library_of(request: Request) -> str:
    lib = request.cookies.get("library")
    return lib if lib in agencies else DEFAULT_LIBRARY


def user_client(request: Request) -> AsyncBiblo | None:
    sid = request.cookies.get("sid")
    return sessions.get(sid) if sid else None


def render(request: Request, name: str, **context: Any) -> HTMLResponse:
    library = library_of(request)
    return templates.TemplateResponse(
        request,
        name,
        {
            "library": agencies.get(library),
            "agencies": sorted(agencies.values(), key=lambda a: a.name),
            "logged_in": user_client(request) is not None,
            **context,
        },
    )


@app.exception_handler(BibloError)
async def biblo_error(request: Request, exc: BibloError) -> HTMLResponse:
    status = 404 if isinstance(exc, NotFoundError | BadRequestError) else 502
    response = render(request, "error.html", message=str(exc))
    response.status_code = status
    return response


# ------------------------------------------------------------------ pages


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return render(request, "home.html")


@app.post("/library")
async def choose_library(request: Request, library: str = Form(...), back: str = Form("/")):
    response = RedirectResponse(back if back.startswith("/") else "/", status_code=303)
    if library in agencies:
        response.set_cookie("library", library, max_age=60 * 60 * 24 * 365, samesite="lax")
    return response


@app.get("/suggest")
async def suggest(request: Request, q: str = ""):
    if len(q) < 2:
        return JSONResponse([])
    links = await anonymous.catalog.suggest(q, library_id=library_of(request))
    # Titles come as "Harry Potter (emne)"; the suffix says what kind of match it is.
    titles = dict.fromkeys(re.sub(r"\s*\([^)]*\)$", "", link.title) for link in links)
    return JSONResponse(list(titles)[:8])


@app.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = "", page: int = 1, by: str = "all"):
    library = library_of(request)
    if not q.strip():
        return RedirectResponse("/", status_code=303)
    chosen: dict[str, list[str]] = defaultdict(list)
    for key, value in request.query_params.multi_items():
        if key in FILTER_FOR_FACET:
            chosen[key].append(value)
    filters = {FILTER_FOR_FACET[k]: v for k, v in chosen.items()}
    search_by = by if by in ("all", "title", "creator", "subject") else "all"
    page = max(page, 1)

    result, facets = await asyncio.gather(
        anonymous.catalog.search(
            q,
            by=search_by,  # type: ignore[arg-type]
            offset=(page - 1) * PAGE_SIZE,
            limit=PAGE_SIZE,
            library_id=library,
            **filters,  # type: ignore[arg-type]
        ),
        cached(
            (
                "facets",
                library,
                q,
                search_by,
                tuple(sorted((k, tuple(v)) for k, v in chosen.items())),
            ),
            300,
            lambda: anonymous.catalog.facets(
                q,
                by=search_by,
                values_limit=12,
                library_id=library,
                **filters,  # type: ignore[arg-type]
            ),
        ),
    )
    shown = sorted(
        (f for f in facets.facets if f.id in FACETS_SHOWN and f.values),
        key=lambda f: FACETS_SHOWN.index(f.id),
    )

    def url(**changes: Any) -> str:
        params = [(k, v) for k, v in request.query_params.multi_items() if k not in changes]
        for key, value in changes.items():
            if value is not None:
                params.append((key, value))
        return "/search?" + urlencode(params)

    def toggle(facet_id: str, key: str) -> str:
        params = [(k, v) for k, v in request.query_params.multi_items() if k != "page"]
        if (facet_id, key) in params:
            params.remove((facet_id, key))
        else:
            params.append((facet_id, key))
        return "/search?" + urlencode(params)

    pages = max(1, -(-result.hitcount // PAGE_SIZE))
    return render(
        request,
        "search.html",
        q=q,
        by=search_by,
        result=result,
        facets=shown,
        chosen=chosen,
        toggle=toggle,
        url=url,
        page=page,
        pages=pages,
    )


@app.get("/work/{work_id}", response_class=HTMLResponse)
async def work(request: Request, work_id: str):
    library = library_of(request)
    catalog = anonymous.catalog
    work, availability, reviews, recommendations = await asyncio.gather(
        catalog.work(work_id, library_id=library),
        catalog.availability(work_id, library_id=library),
        _quietly(catalog.reviews(work_id, library_id=library), []),
        _quietly(catalog.recommendations(work_id, library_id=library), []),
    )
    by_type: dict[str, list[models.Record]] = defaultdict(list)
    for record in work.variations:
        by_type[record.material_type.display].append(record)
    status = {a.material_type.display: a for a in availability}
    physical = [r.record_id for t, rs in by_type.items() if _is_physical(status.get(t)) for r in rs]
    holdings = (
        {h.record_id: h for h in await _quietly(catalog.holdings(physical, library_id=library), [])}
        if physical
        else {}
    )

    user = user_client(request)
    branches = []
    if user is not None and physical:
        branches = await _quietly(user.libraries.branches(await user.login_library_id()), [])

    return render(
        request,
        "work.html",
        work=work,
        by_type=by_type,
        status=status,
        holdings=holdings,
        reviews=[r for r in reviews if r.reviews],
        recommendations=recommendations[:12],
        branches=branches,
        physical_ids=physical,
        is_physical=lambda t: _is_physical(status.get(t)),
    )


@app.get("/work/{work_id}/libraries", response_class=HTMLResponse)
async def other_libraries(request: Request, work_id: str):
    """Which libraries in the country have the work's physical editions home, like
    bibliotek.dk's "find it at another library". One holdings call per library."""
    library = library_of(request)
    work = await anonymous.catalog.work(work_id, library_id=library)
    availability = await anonymous.catalog.availability(work_id, library_id=library)
    status = {a.material_type.display: a for a in availability}
    records = [r for r in work.variations if _is_physical(status.get(r.material_type.display))]
    record_ids = [r.record_id for r in records]
    kinds = {r.record_id: r.material_type.display for r in records}

    rows = []
    if record_ids:
        limit = asyncio.Semaphore(12)

        async def one(agency: models.Agency):
            async with limit:
                found = await cached(
                    ("holdings", agency.id, tuple(record_ids)),
                    600,
                    lambda: _quietly(
                        anonymous.catalog.holdings(record_ids, library_id=agency.id), []
                    ),
                )
            if not found:
                return None
            home: dict[str, int] = defaultdict(int)
            total = 0
            for h in found:
                total += sum(len(item.materials) for item in h.holdings)
                home[kinds.get(h.record_id, "?")] += h.available_materials
            if not total:
                return None
            return {
                "agency": agency,
                "home": dict(home),
                "home_total": sum(home.values()),
                "total": total,
            }

        results = await asyncio.gather(*(one(a) for a in agencies.values()))
        rows = sorted(
            (r for r in results if r),
            key=lambda r: (r["agency"].id != library, -r["home_total"], r["agency"].name),
        )
    return render(request, "libraries.html", work=work, rows=rows, current=library)


# ------------------------------------------------------------------ account


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, back: str = "/account"):
    return render(request, "login.html", back=back, error=None)


@app.post("/login", response_class=HTMLResponse)
async def login(
    request: Request, user: str = Form(...), pincode: str = Form(...), back: str = Form("/account")
):
    client = AsyncBiblo(storage=AsyncMemoryTokenStorage(), http_client=http)
    try:
        await client.login(user.strip(), pincode, library_id=library_of(request))
    except InvalidCredentialsError:
        return render(request, "login.html", back=back, error="Forkert lånernummer eller pinkode.")
    sid = secrets.token_urlsafe(24)
    sessions[sid] = client
    response = RedirectResponse(back if back.startswith("/") else "/account", status_code=303)
    response.set_cookie("sid", sid, httponly=True, samesite="lax")
    return response


@app.post("/logout")
async def logout(request: Request):
    sid = request.cookies.get("sid")
    client = sessions.pop(sid, None) if sid else None
    if client is not None:
        await _quietly(client.logout(), None)
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("sid")
    return response


@app.get("/account", response_class=HTMLResponse)
async def account(request: Request, notice: str | None = None):
    client = user_client(request)
    if client is None:
        return RedirectResponse("/login?back=/account", status_code=303)
    try:
        me, loans, reservations = await asyncio.gather(
            client.account.get(), client.loans.list(), client.reservations.list()
        )
    except NotLoggedInError:
        return RedirectResponse("/login?back=/account", status_code=303)
    branch_names = {
        b.id: b.name
        for b in await _quietly(client.libraries.branches(await client.login_library_id()), [])
    }
    return render(
        request,
        "account.html",
        me=me,
        loans=sorted(loans, key=lambda loan: loan.due_date),
        reservations=reservations,
        branch_names=branch_names,
        notice=notice,
    )


@app.post("/reserve")
async def reserve(
    request: Request,
    record_id: str = Form(...),
    work_id: str = Form(...),
    pickup_branch_id: str | None = Form(None),
):
    client = user_client(request)
    if client is None:
        return RedirectResponse(f"/login?back=/work/{work_id}", status_code=303)
    await client.reservations.create(record_id, pickup_branch_id=pickup_branch_id or None)
    return RedirectResponse("/account?notice=Reserveret", status_code=303)


@app.post("/borrow")
async def borrow(request: Request, record_id: str = Form(...), work_id: str = Form(...)):
    client = user_client(request)
    if client is None:
        return RedirectResponse(f"/login?back=/work/{work_id}", status_code=303)
    await client.loans.borrow(record_id)
    return RedirectResponse("/account?notice=Lånt", status_code=303)


# ------------------------------------------------------------------ helpers


def _is_physical(status: models.MaterialTypeOverview | None) -> bool:
    return status is not None and status.source == "physical"


async def _quietly(call: Any, default: Any) -> Any:
    """A side panel that fails (no reviews, a library without holdings) shouldn't sink the page."""
    try:
        return await call
    except BibloError:
        return default
