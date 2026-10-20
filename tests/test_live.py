"""Against the real API, read-only and anonymous: uv run pytest -m live

Responses are parsed strictly (see conftest), so a failure here means the API changed or
contradicts its spec: fix it in spec/patches.json, or refresh the spec with
`uv run python scripts/generate.py --fetch`. scripts/find_drift.py crawls more widely.
"""

from __future__ import annotations

import pytest

from biblo import AsyncBiblo

pytestmark = pytest.mark.live

VEJLE = "763000"


@pytest.fixture
async def live() -> AsyncBiblo:
    return AsyncBiblo(VEJLE)


async def test_catalogue(live: AsyncBiblo) -> None:
    async with live:
        result = await live.catalog.search("harry potter", limit=5)
        assert result.hitcount > 0
        work = await live.catalog.work(result.works[0].id)
        assert work.variations
        await live.catalog.availability(work.id)
        await live.catalog.record(work.variations[0].record_id)
        await live.catalog.facets("harry potter")
        await live.catalog.suggest("harr")


async def test_libraries_and_content(live: AsyncBiblo) -> None:
    async with live:
        assert any(a.id == VEJLE for a in await live.libraries.list())
        await live.libraries.configuration()
        branches = await live.libraries.branches()
        assert branches
        await live.libraries.open_status()
        await live.libraries.events()
        await live.content.categories()
        await live.config()
