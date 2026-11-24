"""What goes over the wire: headers, app types, paths, query strings, library choice."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from biblo import (
    AsyncBiblo,
    AsyncMemoryTokenStorage,
    Biblo,
    BibloConnectionError,
    BibloError,
    NoLibraryError,
    NotLoggedInError,
    UnknownCatalogueError,
    UnknownMaterialError,
    __version__,
)
from biblo._base import app_type, catalogue_app_type
from biblo._constants import API_KEY, APP_VERSION
from biblo.exceptions import AppTypeNotAcceptedError
from biblo.types import Catalogue
from tests.fake import LIBRARY, PAGE, FakeBiblo


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


@pytest.mark.parametrize(
    ("method", "path", "wanted"),
    [
        ("GET", "/v1/agencies/763000/account/reservations", "mybiblo"),
        ("GET", "/v1/agencies/{agencyId}/account/quotas", "biblogo"),
        ("GET", "/v1/agencies/763000/branches", "mybiblo"),
    ],
)
def test_a_call_can_ask_for_an_app_type_the_endpoint_accepts(
    method: str, path: str, wanted: str
) -> None:
    assert app_type(method, path, wanted) == wanted


@pytest.mark.parametrize(
    ("method", "path", "wanted"),
    [
        ("GET", "/v1/agencies/763000/account/quotas", "mybiblo"),
        ("GET", "/v1/user/messages", "biblogo"),
        ("GET", "/v1/agencies/{agencyId}/branches", "biblo"),
        ("GET", "/v1/not-in-the-spec", "mybiblo"),
    ],
)
def test_an_app_type_the_endpoint_refuses_raises_before_sending(
    method: str, path: str, wanted: str
) -> None:
    with pytest.raises(BibloError, match=wanted):
        app_type(method, path, wanted)


WORK = "work-of:870970-basis:22252852"
# Each call that takes `catalogue`: the path it requests, and the call (sync or async client).
CATALOGUE_CALLS: list[tuple[str, Callable[..., Any]]] = [
    ("/search/works", lambda b, **kw: b.catalog.search("harry potter", **kw)),
    ("/search/facets", lambda b, **kw: b.catalog.facets("harry potter", **kw)),
    ("/search/suggest", lambda b, **kw: b.catalog.suggest("harr", **kw)),
    (f"/works/{WORK}", lambda b, **kw: b.catalog.work(WORK, **kw)),
    ("/recommendations", lambda b, **kw: b.catalog.recommendations(WORK, **kw)),
    ("/material-type-status", lambda b, **kw: b.catalog.availability(WORK, **kw)),
    ("/known-pages", lambda b, **kw: b.content.known_pages(**kw)),
    (f"/pages/{PAGE}", lambda b, **kw: b.content.page(PAGE, **kw)),
    ("/elements/", lambda b, **kw: b.content.page_element(PAGE, "e-1", **kw)),
]
CALL_IDS = [path for path, _ in CATALOGUE_CALLS]


@pytest.mark.parametrize(("path_part", "call"), CATALOGUE_CALLS, ids=CALL_IDS)
@pytest.mark.parametrize(
    ("catalogue", "expected"), [("digital", "biblo"), ("children", "biblogo"), ("all", "mybiblo")]
)
async def test_catalogue_calls_ask_the_chosen_catalogue(
    biblo: AsyncBiblo,
    sync_biblo: Biblo,
    fake: FakeBiblo,
    path_part: str,
    call: Callable[..., Any],
    catalogue: Catalogue,
    expected: str,
) -> None:
    await call(biblo, catalogue=catalogue)
    call(sync_biblo, catalogue=catalogue)
    sent = [r.headers["x-app-type"] for r in fake.api_requests(path_part)]
    assert sent == [expected, expected]


@pytest.mark.parametrize(("path_part", "call"), CATALOGUE_CALLS, ids=CALL_IDS)
async def test_catalogue_calls_ask_the_digital_catalogue_by_default(
    biblo: AsyncBiblo,
    sync_biblo: Biblo,
    fake: FakeBiblo,
    path_part: str,
    call: Callable[..., Any],
) -> None:
    await call(biblo)
    call(sync_biblo)
    sent = [r.headers["x-app-type"] for r in fake.api_requests(path_part)]
    assert sent == ["biblo", "biblo"]


async def test_iter_search_asks_the_chosen_catalogue_for_every_page(
    biblo: AsyncBiblo, sync_biblo: Biblo, fake: FakeBiblo
) -> None:
    works = [w async for w in biblo.catalog.iter_search("potter", page_size=5, catalogue="all")]
    sync_works = list(sync_biblo.catalog.iter_search("potter", page_size=5, catalogue="all"))
    assert len(works) == len(sync_works) == 12
    sent = {r.headers["x-app-type"] for r in fake.api_requests("/search/works")}
    assert sent == {"mybiblo"}


async def test_favorites_ask_the_chosen_catalogue(logged_in: AsyncBiblo, fake: FakeBiblo) -> None:
    await logged_in.favorites.list()
    favorites = await logged_in.favorites.list(catalogue="all")
    sent = [r.headers["x-app-type"] for r in fake.api_requests("/favorites")]
    assert sent == ["biblo", "mybiblo"]
    assert "bog" in [v.material_type.display for v in favorites[0].variations]


def test_a_catalogue_an_endpoint_refuses_raises_before_sending() -> None:
    # Account quotas answer only Biblo and Biblo GO, so not the catalogue "all" (Mit Biblo).
    with pytest.raises(AppTypeNotAcceptedError, match="mybiblo"):
        app_type("GET", "/v1/agencies/763000/account/quotas", catalogue_app_type("all"))


async def test_an_unknown_catalogue_raises_before_sending(
    biblo: AsyncBiblo, fake: FakeBiblo
) -> None:
    with pytest.raises(UnknownCatalogueError, match="nope"):
        await biblo.catalog.search("potter", catalogue="nope")  # ty: ignore[invalid-argument-type]
    assert not fake.api_requests()


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


async def test_material_sends_the_catalogues_facet_value(
    biblo: AsyncBiblo, fake: FakeBiblo
) -> None:
    await biblo.catalog.search("harry potter", material="audiobook")
    params = fake.api_requests("/search/works")[-1].url.params
    assert params.get_list("materialTypesSpecific") == ["lydbog (online)"]


async def test_several_materials_and_raw_facet_values_are_all_sent(
    biblo: AsyncBiblo, fake: FakeBiblo
) -> None:
    await biblo.catalog.search("potter", material=["ebook", "audiobook"])
    params = fake.api_requests("/search/works")[-1].url.params
    assert params.get_list("materialTypesSpecific") == ["e-bog", "lydbog (online)"]

    await biblo.catalog.facets("potter", material="magazine", material_types_specific="podcast")
    params = fake.api_requests("/search/facets")[-1].url.params
    assert sorted(params.get_list("materialTypesSpecific")) == ["podcast", "tidsskrift (online)"]

    works = [
        w async for w in biblo.catalog.iter_search("potter", material="podcast", max_results=1)
    ]
    assert works
    params = fake.api_requests("/search/works")[-1].url.params
    assert params.get_list("materialTypesSpecific") == ["podcast"]


@pytest.mark.parametrize("material", ["vinyl", ["ebook", "vinyl"]])
async def test_an_unknown_material_raises_before_sending(
    biblo: AsyncBiblo, fake: FakeBiblo, material: Any
) -> None:
    with pytest.raises(UnknownMaterialError, match="vinyl") as raised:
        await biblo.catalog.search("potter", material=material)
    assert isinstance(raised.value, ValueError)
    with pytest.raises(UnknownMaterialError):
        await biblo.catalog.facets("potter", material=material)
    assert not fake.api_requests()


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


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (httpx.ReadTimeout(""), "ReadTimeout"),  # stringifies to ""
        (httpx.ConnectError("boom"), "ConnectError: boom"),
    ],
)
async def test_an_unreachable_api_names_the_transport_error(
    error: httpx.TransportError, reason: str
) -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise error

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(unreachable)) as http,
        AsyncBiblo(LIBRARY, http_client=http) as biblo,
    ):
        with pytest.raises(BibloConnectionError) as caught:
            await biblo.libraries.list()
    assert str(caught.value) == f"The Biblo API could not be reached: {reason}"
    assert caught.value.__cause__ is error
