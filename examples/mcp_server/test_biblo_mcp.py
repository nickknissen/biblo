"""The MCP server through an in-process MCP client, against the repo's fake API (tests/fake.py).

    uv run --with "biblo[cli]==0.2.0" --with "mcp==2.3.0" --with pytest --with pytest-asyncio \
        pytest examples/mcp_server -o addopts=""
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.client import Client

from biblo import AsyncBiblo, AsyncMemoryTokenStorage

sys.path.insert(0, str(Path(__file__).parent))
from biblo_mcp import create_server  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
# Loaded by path: the MCP client stack has its own `tests` modules on some installs.
_spec = importlib.util.spec_from_file_location("biblo_fake", REPO / "tests" / "fake.py")
assert _spec is not None and _spec.loader is not None
fake_module = importlib.util.module_from_spec(_spec)
sys.modules["biblo_fake"] = fake_module
_spec.loader.exec_module(fake_module)


@pytest.fixture
def fake() -> Any:
    return fake_module.FakeBiblo()


def _client_factory(fake: Any, storage: AsyncMemoryTokenStorage, library: str | None) -> Any:
    def make() -> AsyncBiblo:
        http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))
        return AsyncBiblo(library, storage=storage, http_client=http)

    return make


async def _logged_in_storage(fake: Any) -> AsyncMemoryTokenStorage:
    storage = AsyncMemoryTokenStorage()
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))
    async with AsyncBiblo(storage=storage, http_client=http) as client:
        await client.login(fake_module.USER, fake_module.PIN, fake_module.LIBRARY)
    await http.aclose()
    return storage


async def _call(server: Any, tool: str, **arguments: Any) -> Any:
    async with Client(server) as client:
        result = await client.call_tool(tool, arguments)
    if result.is_error:
        return {"error": result.content[0].text}
    # Structured output; a list comes wrapped as {"result": [...]}.
    data = result.structured_content
    assert data is not None
    return data["result"] if set(data) == {"result"} else data


@pytest.fixture
async def server(fake: Any) -> Any:
    storage = await _logged_in_storage(fake)
    return create_server(_client_factory(fake, storage, None))


@pytest.fixture
def anonymous(fake: Any) -> Any:
    return create_server(_client_factory(fake, AsyncMemoryTokenStorage(), fake_module.LIBRARY))


async def test_every_tool_is_read_only(server: Any) -> None:
    async with Client(server) as client:
        tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == {
        "search_catalogue",
        "get_work",
        "find_library",
        "my_loans",
        "my_reservations",
        "my_favorites",
        "my_account",
        "show_my_library",
    }
    assert all(t.annotations and t.annotations.read_only_hint for t in tools)


async def test_search_without_login(anonymous: Any) -> None:
    result = await _call(anonymous, "search_catalogue", query="harry potter", format="ebook")
    assert result["hits"] > 0
    assert result["works"][0]["work_id"].startswith("work-of:")


async def test_get_work(anonymous: Any) -> None:
    work_id = "work-of:870970-basis:22252852"
    result = await _call(anonymous, "get_work", work_id=work_id)
    assert result["title"] == "Harry Potter og De Vises Sten"
    assert result["editions"] and result["availability"]
    assert result["web_page"].endswith(work_id)


async def test_find_library(anonymous: Any) -> None:
    result = await _call(anonymous, "find_library", name_or_city="vejle")
    assert {"library_id": "763000", "name": "Vejle Bibliotekerne"}.items() <= result[0].items()


async def test_account_tools_need_a_login(anonymous: Any) -> None:
    result = await _call(anonymous, "my_loans")
    assert "biblo auth login" in result["error"]


async def test_my_loans(server: Any, fake: Any) -> None:
    fake.loans = [{**fake_module.LOAN, "dueDate": "2026-01-05T10:00:00.000Z"}]
    loans = await _call(server, "my_loans")
    assert [(loan["title"], loan["overdue"]) for loan in loans] == [
        ("Harry Potter og De Vises Sten", True),
        ("En trykt bog", False),
    ]


async def test_my_reservations_name_the_pickup_branch(server: Any) -> None:
    reservations = await _call(server, "my_reservations")
    physical = next(r for r in reservations if r["queue_position"] is not None)
    assert physical["queue_position"] == 3
    assert physical["pickup_branch"] != "DK-763004"  # the branch's name, not its id
    assert not physical["ready_for_pickup"]


async def test_my_favorites(server: Any) -> None:
    favorites = await _call(server, "my_favorites")
    assert favorites[0]["title"] == "Harry Potter og De Vises Sten"


async def test_my_account(server: Any) -> None:
    account = await _call(server, "my_account")
    assert account["loans_left_this_period"] == {"ebook": 4, "audiobook": 5}
    assert account["fees_dkk"] == 0
    # Nothing that identifies the user.
    assert "name" not in account and "email" not in account


# ---------------------------------------------------------------- the MCP App


async def test_show_my_library_links_its_view(server: Any) -> None:
    async with Client(server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        uri = tools["show_my_library"].meta["ui"]["resourceUri"]
        resource = await client.read_resource(uri)
    [content] = resource.contents
    assert uri == "ui://biblo/my-library.html"
    assert content.mime_type == "text/html;profile=mcp-app"
    assert "ui/initialize" in content.text
    # Without this the view's sandbox blocks the cover images.
    assert content.meta["ui"]["csp"]["resourceDomains"] == ["https://fbiinfo-present.dbc.dk"]


async def test_show_my_library_data(server: Any, fake: Any) -> None:
    fake.physical_reservations = [
        {
            **fake_module.PHYSICAL_RESERVATION,
            "state": "READY_FOR_PICKUP",
            "claim": {"id": "c-1", "availableUntil": "2027-01-10T10:00:00.000Z"},
        }
    ]
    data = await _call(server, "show_my_library")
    assert [loan["title"] for loan in data["loans"]] == [
        "Harry Potter og De Vises Sten",
        "En trykt bog",
    ]
    assert all(loan["web_page"].startswith("https://bibliotek.dk/work/") for loan in data["loans"])
    # Ready for pickup comes first.
    assert data["reservations"][0]["ready_for_pickup"]
    assert data["reservations"][0]["pickup_by"] == "2027-01-10T10:00:00+00:00"
    assert data["loans_left_this_period"] == {"ebook": 4, "audiobook": 5}
    assert data["as_of"]
