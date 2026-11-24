"""Against the real API, read-only and anonymous: uv run pytest -m live

Responses are parsed strictly (see conftest), so a failure here means the API changed or
contradicts its spec: fix it in spec/patches.json, or refresh the spec with
`uv run python scripts/generate.py --fetch`. scripts/find_drift.py crawls more widely.
"""

from __future__ import annotations

from typing import cast, get_args

import pytest

from biblo import AsyncBiblo
from biblo._base import MATERIAL_FACETS
from biblo.types import Material

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


async def test_all_catalogues_find_more_than_the_digital_one(live: AsyncBiblo) -> None:
    async with live:
        digital = await live.catalog.search("harry potter", limit=1)
        everything = await live.catalog.search("harry potter", limit=1, catalogue="all")
        assert everything.hitcount > digital.hitcount


@pytest.mark.parametrize("material", get_args(Material))
async def test_each_material_finds_only_that_material(live: AsyncBiblo, material: str) -> None:
    # A renamed facet value on DDF's side would find nothing, or ignore the filter.
    facet = MATERIAL_FACETS[cast(Material, material)]
    async with live:
        result = await live.catalog.search("", material=cast(Material, material), limit=10)
    assert result.hitcount > 0
    assert result.works
    for work in result.works:
        assert facet in [t.display for t in work.material_types], work.title


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
