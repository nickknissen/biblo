"""What goes over the wire: headers, app types, paths, query strings, library choice."""

from __future__ import annotations

import httpx
import pytest

from biblo import (
    AsyncBiblo,
    AsyncMemoryTokenStorage,
    Biblo,
    NoLibraryError,
    NotLoggedInError,
    __version__,
)
from biblo._base import app_type
from biblo._constants import API_KEY, APP_VERSION
from tests.fake import LIBRARY, FakeBiblo


async def test_every_request_carries_the_app_headers(biblo: AsyncBiblo, fake: FakeBiblo) -> None:
    await biblo.libraries.list()
    request = fake.api_requests()[-1]
    assert request.headers["x-api-key"] == API_KEY
    assert request.headers["x-app"] == f"Android/{APP_VERSION}"
    assert request.headers["x-app-type"] == "biblo"
    assert request.headers["x-installation-id"]
    assert "authorization" not in request.headers


async def test_api_requests_name_the_library_in_the_user_agent(
    biblo: AsyncBiblo, fake: FakeBiblo
) -> None:
    await biblo.libraries.list()
    assert fake.api_requests()[-1].headers["user-agent"] == (
        f"biblo-python/{__version__} (+https://github.com/nickknissen/biblo)"
    )


async def test_an_application_names_itself_before_the_library(fake: FakeBiblo) -> None:
    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)) as http,
        AsyncBiblo(LIBRARY, http_client=http, user_agent="myapp/1.0") as biblo,
    ):
        await biblo.libraries.list()
    assert fake.api_requests()[-1].headers["user-agent"] == (
        f"myapp/1.0 biblo-python/{__version__} (+https://github.com/nickknissen/biblo)"
    )


def test_the_sync_client_names_the_application_too(fake: FakeBiblo) -> None:
    with (
        httpx.Client(transport=httpx.MockTransport(fake.handle)) as http,
        Biblo(LIBRARY, http_client=http, user_agent="myapp/1.0") as biblo,
    ):
        biblo.libraries.list()
    assert fake.api_requests()[-1].headers["user-agent"].startswith("myapp/1.0 biblo-python/")


async def test_the_user_agent_of_a_shared_http_client_is_replaced(fake: FakeBiblo) -> None:
    transport = httpx.MockTransport(fake.handle)
    async with (
        httpx.AsyncClient(transport=transport, headers={"User-Agent": "HomeAssistant"}) as http,
        AsyncBiblo(LIBRARY, http_client=http) as biblo,
    ):
        await biblo.libraries.list()
    assert fake.api_requests()[-1].headers["user-agent"].startswith("biblo-python/")


async def test_installation_id_is_kept_in_the_session(
    biblo: AsyncBiblo, fake: FakeBiblo, storage: AsyncMemoryTokenStorage
) -> None:
    await biblo.login("0102031234", "1234")
    first = fake.api_requests()[-1].headers["x-installation-id"]
    assert storage.data is not None
    assert storage.data["installation_id"] == first


async def test_mybiblo_only_endpoints_get_that_app_type(biblo: AsyncBiblo, fake: FakeBiblo) -> None:
    branches = await biblo.libraries.branches()
    assert fake.api_requests("/branches")[-1].headers["x-app-type"] == "mybiblo"
    assert branches[0].name


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/v1/agencies/{agencyId}/branches", "mybiblo"),
        ("GET", "/v1/agencies/763000/branches", "mybiblo"),
        ("GET", "/v1/agencies/763000/branches/DK-763001/openinghours", "mybiblo"),
        ("GET", "/v1/agencies/763000/search/works", "biblo"),
        ("POST", "/v1/agencies/763000/account/loans/return", "mybiblo"),
        ("POST", "/v1/agencies/763000/account/loans", "biblo"),
    ],
)
def test_app_type_matches_templates_and_filled_paths(method: str, path: str, expected: str) -> None:
    assert app_type(method, path) == expected


async def test_ids_with_colons_stay_readable_in_the_path(
    biblo: AsyncBiblo, fake: FakeBiblo
) -> None:
    await biblo.catalog.work("work-of:870970-basis:22252852")
    assert (
        fake.api_requests()[-1]
        .url.raw_path.decode()
        .endswith("/works/work-of:870970-basis:22252852")
    )


async def test_list_parameters_repeat_the_key(biblo: AsyncBiblo, fake: FakeBiblo) -> None:
    await biblo.catalog.search(
        "potter", material_types_specific=["e-bog", "podcast"], main_languages="dan", limit=3
    )
    params = fake.api_requests("/search/works")[-1].url.params
    assert params.get_list("materialTypesSpecific") == ["e-bog", "podcast"]
    assert params.get_list("mainLanguages") == ["dan"]
    assert params["limit"] == "3"
    assert "offset" not in params  # unset parameters are left out


async def test_unknown_search_filter_is_refused(biblo: AsyncBiblo) -> None:
    with pytest.raises(TypeError, match="colour"):
        await biblo.catalog.search("potter", colour="red")  # type: ignore[call-arg]


async def test_no_library_is_a_clear_error(fake: FakeBiblo) -> None:
    import httpx

    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))
    async with AsyncBiblo(http_client=http) as client:
        with pytest.raises(NoLibraryError):
            await client.catalog.search("potter")
        assert len(await client.libraries.list()) > 0  # not tied to a library
    await http.aclose()


async def test_account_calls_need_a_login_before_anything_is_sent(
    biblo: AsyncBiblo, fake: FakeBiblo
) -> None:
    with pytest.raises(NotLoggedInError):
        await biblo.loans.list()
    assert fake.api_requests("/account") == []


async def test_account_calls_use_the_login_library_not_the_clients_default(
    fake: FakeBiblo, logged_in: AsyncBiblo
) -> None:
    logged_in._library_id = "710100"  # e.g. browsing Copenhagen's catalogue
    await logged_in.loans.list()
    assert fake.api_requests("/account/loans")[-1].url.path.startswith(f"/v1/agencies/{LIBRARY}/")
