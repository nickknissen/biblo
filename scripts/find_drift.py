"""Crawl the live API and report where responses contradict the models (the spec).

    uv run python scripts/find_drift.py                      # a few libraries, anonymously
    uv run python scripts/find_drift.py --session ~/.config/biblo/session.json   # + account

Prints each mismatch once, with a count, and suggests entries for spec/patches.json. Only
reads: it never borrows, reserves or changes anything.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from collections.abc import Awaitable
from pathlib import Path
from typing import Any

from biblo import AsyncBiblo, AsyncFileTokenStorage, BibloError
from biblo.models._lenient import record_drift

LIBRARIES = ["763000", "710100", "775100"]  # Vejle, København, Aarhus
QUERIES = ["harry potter", "krimi", "tove ditlevsen", "podcast", "brætspil", "dvd"]


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", type=Path, help="session file of a logged-in user")
    parser.add_argument("--libraries", nargs="*", default=LIBRARIES)
    args = parser.parse_args()

    calls = 0
    failures: list[str] = []

    async def call(label: str, coro: Awaitable[Any]) -> Any:
        nonlocal calls
        calls += 1
        try:
            return await coro
        except BibloError as err:
            failures.append(f"{label}: {type(err).__name__}: {str(err)[:150]}")
            return None

    with record_drift() as drift:
        async with AsyncBiblo() as anon:
            await call("libraries", anon.libraries.list())
            await call("config", anon.config())
            for library in args.libraries:
                await crawl_library(anon, library, call)
        if args.session:
            storage = AsyncFileTokenStorage(args.session)
            async with AsyncBiblo(storage=storage) as user:
                await crawl_account(user, call)

    print(f"{calls} calls, {len(failures)} failed, {len(drift)} mismatched values\n")
    for failure in failures:
        print(f"  FAILED {failure}")
    counts = Counter(drift)
    by_field: dict[str, list[str]] = {}
    for (model, field, error), count in sorted(counts.items()):
        print(f"  {count:5}x {model}.{field}: {error}")
        by_field.setdefault(f"{model}.{field}", []).append(error)
    nullable = {k: v for k, v in by_field.items() if all("got None" in e for e in v)}
    if nullable:
        print("\nSuggested spec/patches.json entries (null where the spec forbids it):")
        suggestion = {k: {"nullable": True, "reason": "Live API sends null."} for k in nullable}
        print(json.dumps(suggestion, indent=2, ensure_ascii=False))
    return 1 if drift or failures else 0


async def crawl_library(b: AsyncBiblo, lib: str, call: Any) -> None:
    await call(f"{lib} agency", b.libraries.get(lib))
    await call(f"{lib} configuration", b.libraries.configuration(lib))
    branches = await call(f"{lib} branches", b.libraries.branches(lib)) or []
    for branch in branches[:3]:
        await call(f"{lib} branch", b.libraries.branch(branch.id, lib))
        await call(
            f"{lib} branch hours", b.libraries.branch_opening_hours(branch.id, library_id=lib)
        )
        await call(f"{lib} branch events", b.libraries.events(branch.id, library_id=lib))
    await call(f"{lib} hours", b.libraries.opening_hours(lib))
    await call(f"{lib} open", b.libraries.open_status(lib))
    await call(f"{lib} events", b.libraries.events(library_id=lib))

    categories = await call(f"{lib} categories", b.content.categories(lib)) or []
    for category in categories[:4]:
        await call(f"{lib} category", b.content.category(category.id, lib))
    known = await call(f"{lib} known pages", b.content.known_pages(lib))
    if known is not None:
        for page_id in list(known.model_dump().values())[:4]:
            if isinstance(page_id, str):
                await call(f"{lib} page", b.content.page(page_id, lib))

    for q in QUERIES:
        result = await call(f"{lib} search {q}", b.catalog.search(q, limit=10, library_id=lib))
        await call(f"{lib} facets {q}", b.catalog.facets(q, library_id=lib))
        await call(f"{lib} suggest {q}", b.catalog.suggest(q[:4], lib))
        for work in (result.works if result else [])[:3]:
            full = await call(f"{lib} work", b.catalog.work(work.id, lib))
            await call(f"{lib} availability", b.catalog.availability(work.id, lib))
            await call(f"{lib} recommendations", b.catalog.recommendations(work.id, lib))
            await call(f"{lib} reviews", b.catalog.reviews(work.id, lib))
            if full is None:
                continue
            for series in getattr(full, "series", None) or []:
                if getattr(series, "series_id", None):
                    await call(f"{lib} series", b.catalog.series(series.series_id, lib))
            record_ids = [v.record_id for v in full.variations][:4]
            for record_id in record_ids[:2]:
                await call(f"{lib} record", b.catalog.record(record_id, lib))
            if record_ids:
                await call(f"{lib} status", b.catalog.records_status(record_ids, lib))
                await call(f"{lib} holdings", b.catalog.holdings(record_ids, lib))


async def crawl_account(b: AsyncBiblo, call: Any) -> None:
    await call("account", b.account.get())
    await call("fees", b.account.fees())
    await call("quotas", b.account.quotas())
    await call("loans", b.loans.list())
    await call("reservations", b.reservations.list())
    await call("favorites", b.favorites.list())
    await call("messages", b.messages.list())


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
