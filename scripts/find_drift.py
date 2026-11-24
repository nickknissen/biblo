"""Crawl the live API and report where responses contradict the models (the spec).

    uv run python scripts/find_drift.py                      # a few libraries, anonymously
    uv run python scripts/find_drift.py --session ~/.config/biblo/session.json   # + account
    uv run python scripts/find_drift.py --app-types biblo    # only what the Biblo app sees

Every endpoint is called once per `x-app-type` it accepts (see src/biblo/_app_types.py), limited
to `--app-types`; an endpoint that accepts none of them is skipped. Catalogue calls go through
their public `catalogue=` keyword, the rest through the client's `_get(..., app_type=)`.

Prints each mismatch once, with a count, and suggests entries for spec/patches.json. Then, as
information only, the endpoints whose answers differ by app type, summarised by counts, ids and
kinds (material type categories, states, element types). Account answers show counts and kinds
only, never ids, names or titles.

Exits 1 on model drift or a failed call; differences between app types alone don't fail the run.
A work, category or page found under one app type and refused under another (each app has its
own catalogue and content) is such a difference; it is a failure only if every app type fails.
Only makes GET requests: it never borrows, reserves or changes anything.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from biblo import AsyncBiblo, AsyncFileTokenStorage, BibloError
from biblo import models as m
from biblo._app_types import ACCEPTED_APP_TYPES, DEFAULT_APP_TYPE
from biblo._base import CATALOGUE_APP_TYPES
from biblo.models._lenient import record_drift
from biblo.types import Catalogue

LIBRARIES = ["763000", "710100", "775100"]  # Vejle, København, Aarhus
# Under catalogue "all" (x-app-type mybiblo) "harry potter" and "krimi" find printed books, so
# printed variations, their records status and holdings get crawled too.
QUERIES = ["harry potter", "krimi", "tove ditlevsen", "podcast", "brætspil", "dvd"]
APP_TYPES = ("biblo", "biblogo", "mybiblo")
WORKS_PER_SEARCH = 2
"""Works crawled from each catalogue's hits, per query."""

LIB = "/v1/agencies/{agencyId}"
CATALOGUES: dict[str, Catalogue] = {app: name for name, app in CATALOGUE_APP_TYPES.items()}

# What a summary of a response is made of: nothing that names a person or a work.
ID_KEYS = frozenset({"id", "uuid", "record_id", "work_id", "fee_id"})
KIND_KEYS = frozenset({"category", "source", "state", "type", "account_type"})
COUNT_KEYS = ("hitcount", "works", "variations", "messages", "elements", "holdings")

type Fetch = Callable[[str | None], Awaitable[Any]]
"""Makes one call under an `x-app-type` (None: the endpoint's default)."""


@dataclass(frozen=True)
class Summary:
    """What is compared between app types: a count, the ids, the kinds; or the error's
    class where the call failed under this app type but answered under another."""

    count: int | None
    ids: frozenset[str]
    kinds: frozenset[str]
    error: str | None = None

    @classmethod
    def failed(cls, err: BibloError) -> Summary:
        return cls(None, frozenset(), frozenset(), type(err).__name__)

    @classmethod
    def of(cls, response: Any) -> Summary:
        data = _plain(response)
        ids: set[str] = set()
        kinds: set[str] = set()
        _collect(data, ids, kinds)
        return cls(_count(data), frozenset(ids), frozenset(kinds))


@dataclass
class Difference:
    """An endpoint whose answer differs by app type. `groups` holds the app types that
    agree with each other, with their summary."""

    label: str
    groups: list[tuple[list[str], Summary]]
    private: bool = False

    def lines(self) -> list[str]:
        names = " | ".join("=".join(app_types) for app_types, _ in self.groups)
        lines = [f"  {self.label}: {names}"]
        width = max(len("=".join(app_types)) for app_types, _ in self.groups)
        for i, (app_types, summary) in enumerate(self.groups):
            others = frozenset[str]().union(
                *(s.ids for j, (_, s) in enumerate(self.groups) if j != i)
            )
            if summary.error:
                lines.append(f"    {'='.join(app_types) + ':':{width + 1}} {summary.error}")
                continue
            only = sorted(summary.ids - others)
            parts = [] if summary.count is None else [f"count {summary.count}"]
            ids = f"{len(summary.ids)} ids"
            if only:
                shown = "" if self.private else f": {', '.join(only[:3])}"
                more = ", ..." if len(only) > 3 and not self.private else ""
                ids += f" ({len(only)} only here{shown}{more})"
            parts.append(ids)
            if summary.kinds:
                parts.append(f"kinds {', '.join(sorted(summary.kinds))}")
            lines.append(f"    {'='.join(app_types) + ':':{width + 1}} {'; '.join(parts)}")
        return lines


@dataclass
class Crawler:
    app_types: Sequence[str] = APP_TYPES
    calls: int = 0
    failures: list[str] = field(default_factory=list)
    differences: list[Difference] = field(default_factory=list)

    def app_types_for(self, template: str) -> list[str | None]:
        """The chosen app types `GET template` accepts; [None] if the spec doesn't list it."""
        accepted = ACCEPTED_APP_TYPES.get(("GET", template))
        if accepted is None:
            return [None]
        return [app_type for app_type in self.app_types if app_type in accepted]

    async def each(
        self,
        label: str,
        template: str,
        fetch: Fetch,
        *,
        private: bool = False,
        missing_ok: bool = False,
    ) -> dict[str, Any]:
        """Calls `fetch` under each app type `template` accepts, and notes where the answers
        differ. Returns the answers that came, by app type. `private` keeps ids out of the
        report.

        `missing_ok` is for an item found in another answer (a work, a category): an app
        type that fails on it while another answers is a difference, not a failure, since
        each app sees its own catalogue and content."""
        answers: dict[str, Any] = {}
        errors: dict[str, BibloError] = {}
        for app_type in self.app_types_for(template):
            name = app_type or DEFAULT_APP_TYPE
            self.calls += 1
            try:
                answers[name] = await fetch(app_type)
            except BibloError as err:
                errors[name] = err
        if not (missing_ok and answers):
            for name, err in errors.items():
                self.failures.append(f"{label} [{name}]: {type(err).__name__}: {str(err)[:150]}")
            errors = {}
        self.compare(label, answers, errors, private=private)
        return answers

    def compare(
        self,
        label: str,
        answers: dict[str, Any],
        errors: dict[str, BibloError] | None = None,
        *,
        private: bool = False,
    ) -> None:
        groups: dict[Summary, list[str]] = {}
        for app_type, answer in answers.items():
            groups.setdefault(Summary.of(answer), []).append(app_type)
        for app_type, err in (errors or {}).items():
            groups.setdefault(Summary.failed(err), []).append(app_type)
        if len(groups) > 1:
            pairs = [(app_types, summary) for summary, app_types in groups.items()]
            self.differences.append(Difference(label, pairs, private))


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", type=Path, help="session file of a logged-in user")
    parser.add_argument("--libraries", nargs="*", default=LIBRARIES)
    parser.add_argument(
        "--app-types",
        type=app_types_arg,
        default=APP_TYPES,
        help=f"comma-separated x-app-type values to crawl under (default: {','.join(APP_TYPES)})",
    )
    args = parser.parse_args()

    crawler = Crawler(args.app_types)
    with record_drift() as drift:
        async with AsyncBiblo() as anon:
            await crawl_global(crawler, anon)
            for library in args.libraries:
                await crawl_library(crawler, anon, library)
        if args.session:
            storage = AsyncFileTokenStorage(args.session)
            async with AsyncBiblo(storage=storage) as user:
                await crawl_account(crawler, user)

    report(crawler, drift)
    return 1 if drift or crawler.failures else 0


def app_types_arg(value: str) -> tuple[str, ...]:
    chosen = tuple(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
    if unknown := [app_type for app_type in chosen if app_type not in APP_TYPES]:
        raise argparse.ArgumentTypeError(
            f"unknown app type {', '.join(unknown)}: use {', '.join(APP_TYPES)}"
        )
    if not chosen:
        raise argparse.ArgumentTypeError("no app type given")
    return chosen


def report(crawler: Crawler, drift: Iterable[tuple[str, str, str]]) -> None:
    counts = Counter(drift)
    print(
        f"{crawler.calls} calls, {len(crawler.failures)} failed, "
        f"{sum(counts.values())} mismatched values\n"
    )
    for failure in crawler.failures:
        print(f"  FAILED {failure}")
    by_field: dict[str, list[str]] = {}
    for (model, name, error), count in sorted(counts.items()):
        print(f"  {count:5}x {model}.{name}: {error}")
        by_field.setdefault(f"{model}.{name}", []).append(error)
    nullable = {k: v for k, v in by_field.items() if all("got None" in e for e in v)}
    if nullable:
        print("\nSuggested spec/patches.json entries (null where the spec forbids it):")
        suggestion = {k: {"nullable": True, "reason": "Live API sends null."} for k in nullable}
        print(json.dumps(suggestion, indent=2, ensure_ascii=False))
    if crawler.differences:
        print(
            f"\nResponses differ by app type ({len(crawler.differences)}; information, not drift):"
        )
        for difference in crawler.differences:
            print("\n".join(difference.lines()))


# --------------------------------------------------------------------------- crawls


def catalogue(app_type: str | None) -> Catalogue:
    return CATALOGUES[app_type or DEFAULT_APP_TYPE]


async def crawl_global(c: Crawler, b: AsyncBiblo) -> None:
    await c.each(
        "libraries", "/v1/agencies", lambda at: b._get(list[m.Agency], "/v1/agencies", app_type=at)
    )
    await c.each(
        "config", "/v1/config", lambda at: b._get(m.GlobalConfig, "/v1/config", app_type=at)
    )


async def crawl_library(c: Crawler, b: AsyncBiblo, lib: str) -> None:
    def get(
        tp: Any,
        template: str,
        path_params: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Fetch:
        return lambda at: b._get(
            tp, template, path_params, params=params, library_id=lib, app_type=at
        )

    async def each(
        label: str, template: str, fetch: Fetch, *, missing_ok: bool = False
    ) -> dict[str, Any]:
        return await c.each(f"{lib} {label}", template, fetch, missing_ok=missing_ok)

    await each("agency", LIB, get(m.Agency, LIB))
    await each(
        "configuration", f"{LIB}/configuration", get(m.AgencyConfiguration, f"{LIB}/configuration")
    )

    branches = _first(
        await each("branches", f"{LIB}/branches", get(list[m.Branch], f"{LIB}/branches"))
    )
    for branch in (branches or [])[:3]:
        ids = {"branchId": branch.id}
        template = f"{LIB}/branches/{{branchId}}"
        await each("branch", template, get(m.Branch, template, ids))
        await each(
            "branch hours",
            f"{template}/openinghours",
            get(m.OpeningHours, f"{template}/openinghours", ids),
        )
        await each(
            "branch events", f"{template}/events", get(list[m.Event], f"{template}/events", ids)
        )
    await each("hours", f"{LIB}/openinghours", get(list[m.OpeningHours], f"{LIB}/openinghours"))
    await each("open", f"{LIB}/openstatus", get(m.AgencyOpenStatus, f"{LIB}/openstatus"))
    await each("events", f"{LIB}/events", get(list[m.Event], f"{LIB}/events"))

    template = f"{LIB}/content/categories"
    # Each app has its own categories and pages: the others may not know them (missing_ok).
    categories = await each("categories", template, get(list[m.Category], template))
    category_ids = dict.fromkeys(cat.id for found in categories.values() for cat in found)
    for category_id in list(category_ids)[:4]:
        path = f"{template}/{{categoryId}}"
        fetch = get(m.Category, path, {"categoryId": category_id})
        await each("category", path, fetch, missing_ok=True)
    known = await each(
        "known pages",
        f"{LIB}/content/known-pages",
        lambda at: b.content.known_pages(lib, catalogue=catalogue(at)),
    )
    page_ids = dict.fromkeys(
        link.url.rsplit("/", 1)[-1]
        for pages in known.values()
        for link in (pages.explore, pages.search, pages.always_loanable)
        if "/content/pages/" in link.url
    )
    for page_id in list(page_ids)[:4]:
        await each(
            "page",
            f"{LIB}/content/pages/{{pageId}}",
            lambda at, page_id=page_id: b.content.page(page_id, lib, catalogue=catalogue(at)),
            missing_ok=True,
        )

    crawled: set[str] = set()
    for q in QUERIES:
        results = await each(
            f"search {q}",
            f"{LIB}/search/works",
            lambda at, q=q: b.catalog.search(q, limit=10, library_id=lib, catalogue=catalogue(at)),
        )
        await each(
            f"facets {q}",
            f"{LIB}/search/facets",
            lambda at, q=q: b.catalog.facets(q, library_id=lib, catalogue=catalogue(at)),
        )
        await each(
            f"suggest {q}",
            f"{LIB}/search/suggest",
            lambda at, q=q: b.catalog.suggest(q[:4], lib, catalogue=catalogue(at)),
        )
        work_ids = [w.id for r in results.values() for w in r.works[:WORKS_PER_SEARCH]]
        for work_id in dict.fromkeys(work_ids):
            if work_id not in crawled:
                crawled.add(work_id)
                await crawl_work(c, b, lib, work_id, get)


async def crawl_work(
    c: Crawler, b: AsyncBiblo, lib: str, work_id: str, get: Callable[..., Fetch]
) -> None:
    """A work found in one catalogue, under every app type; one that doesn't know it is
    a difference, not a failure."""

    async def each(label: str, template: str, fetch: Fetch) -> dict[str, Any]:
        return await c.each(f"{lib} {label} {work_id}", template, fetch, missing_ok=True)

    work = f"{LIB}/works/{{workId}}"
    works = await each(
        "work", work, lambda at: b.catalog.work(work_id, lib, catalogue=catalogue(at))
    )
    await each(
        "availability",
        f"{work}/material-type-status",
        lambda at: b.catalog.availability(work_id, lib, catalogue=catalogue(at)),
    )
    await each(
        "recommendations",
        f"{work}/recommendations",
        lambda at: b.catalog.recommendations(work_id, lib, catalogue=catalogue(at)),
    )
    ids = {"workId": work_id}
    await each("reviews", f"{work}/reviews", get(list[m.WorkReview], f"{work}/reviews", ids))

    series_ids = dict.fromkeys(
        s.series_id for w in works.values() for s in w.series or [] if s.series_id
    )
    for series_id in list(series_ids)[:2]:
        template = f"{LIB}/series/{{seriesId}}"
        await each("series", template, get(list[m.WorkLight], template, {"seriesId": series_id}))

    # Each catalogue has its own editions: printed ones only show up under "all".
    record_ids = list(dict.fromkeys(v.record_id for w in works.values() for v in w.variations))
    for record_id in record_ids[:2]:
        template = f"{LIB}/records/{{recordId}}"
        await each("record", template, get(m.Record, template, {"recordId": record_id}))
    if record_ids:
        params = {"recordIds": record_ids[:6]}
        status, holdings = f"{LIB}/records/status", f"{LIB}/records/holdings"
        await each("status", status, get(list[m.RecordStatus], status, params=params))
        await each("holdings", holdings, get(list[m.RecordHoldings], holdings, params=params))


async def crawl_account(c: Crawler, b: AsyncBiblo) -> None:
    """Every answer here is the user's: summaries leave ids out (`private`)."""

    def get(tp: Any, template: str) -> Fetch:
        return lambda at: b._get(tp, template, auth=True, app_type=at)

    async def each(label: str, template: str, fetch: Fetch) -> None:
        await c.each(label, template, fetch, private=True)

    await each("account", f"{LIB}/account", get(m.Account, f"{LIB}/account"))
    await each("fees", f"{LIB}/account/fees", get(list[m.Fee], f"{LIB}/account/fees"))
    await each("quotas", f"{LIB}/account/quotas", get(m.QuotaSummary, f"{LIB}/account/quotas"))
    # One listing per app type, not the merged one loans.list() and reservations.list() give.
    await each("loans", f"{LIB}/account/loans", get(list[m.Loan], f"{LIB}/account/loans"))
    template = f"{LIB}/account/reservations"
    await each("reservations", template, get(list[m.ReservationItem], template))
    await each(
        "favorites",
        "/v1/user/favorites",
        lambda at: b.favorites.list(catalogue=catalogue(at)),
    )
    await each("messages", "/v1/user/messages", get(m.MessagesResponseData, "/v1/user/messages"))


# --------------------------------------------------------------------------- summaries


def _first(answers: dict[str, Any]) -> Any:
    return next(iter(answers.values()), None)


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _count(data: Any) -> int | None:
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in COUNT_KEYS:
            value = data.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
            if isinstance(value, list):
                return len(value)
    return None


def _collect(data: Any, ids: set[str], kinds: set[str]) -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, str):
                if key in ID_KEYS:
                    ids.add(value)
                elif key in KIND_KEYS:
                    kinds.add(value)
            else:
                _collect(value, ids, kinds)
    elif isinstance(data, list):
        for item in data:
            _collect(item, ids, kinds)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
