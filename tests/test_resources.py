"""The endpoints return typed models (parsed strictly in tests) and send the right bodies."""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from biblo import (
    AsyncBiblo,
    BadRequestError,
    Biblo,
    BibloError,
    NotFoundError,
    PermissionDeniedError,
    PickupBranchRequiredError,
    ResponseValidationError,
    models,
)
from biblo.models import _lenient
from biblo.models._lenient import record_drift
from tests.fake import (
    DIGITAL_LOAN_ID,
    LIBRARY,
    PHYSICAL_LOAN,
    PHYSICAL_RECORD,
    RESERVATION,
    RESERVATION_IDS,
    FakeBiblo,
    fixture,
)


async def test_catalogue(biblo: AsyncBiblo) -> None:
    result = await biblo.catalog.search("harry potter", limit=5)
    assert result.hitcount == 12
    assert isinstance(result.works[0], models.WorkLight)

    work = await biblo.catalog.work(result.works[0].id)
    assert work.title.startswith("Harry Potter")
    assert {v.material_type.display for v in work.variations} >= {"e-bog"}

    availability = await biblo.catalog.availability(work.id)
    assert all(isinstance(a.available, bool) for a in availability)

    record = await biblo.catalog.record("870970-basis:141629433")
    assert record.material_type.category == "EBOOKS"

    facets = await biblo.catalog.facets("harry potter")
    assert any(f.id == "materialTypesSpecific" for f in facets.facets)
    assert (await biblo.catalog.suggest("harr"))[0].title


async def test_iter_search_pages_through_every_hit(biblo: AsyncBiblo, fake: FakeBiblo) -> None:
    works = [w async for w in biblo.catalog.iter_search("potter", page_size=5)]
    assert len(works) == 12
    offsets = [r.url.params.get("offset") for r in fake.api_requests("/search/works")]
    assert offsets == ["0", "5", "10"]


async def test_iter_search_stops_at_max_results(biblo: AsyncBiblo, fake: FakeBiblo) -> None:
    works = [w async for w in biblo.catalog.iter_search("potter", page_size=5, max_results=7)]
    assert len(works) == 7
    limits = [r.url.params.get("limit") for r in fake.api_requests("/search/works")]
    assert limits == ["5", "2"]


async def test_libraries(biblo: AsyncBiblo) -> None:
    vejle = await biblo.libraries.find("vejle")
    assert [a.id for a in vejle] == [LIBRARY]
    config = await biblo.libraries.configuration()
    assert config.reservations.expiry_intervals
    status = await biblo.libraries.open_status()
    assert status.status_list[0].branch_id
    assert await biblo.libraries.events()
    assert await biblo.content.categories()


async def test_loans(logged_in: AsyncBiblo, fake: FakeBiblo) -> None:
    loans = await logged_in.loans.list()
    assert loans[0].id == DIGITAL_LOAN_ID
    assert loans[0].record.title.startswith("Støj")
    assert loans[0].due_date.tzinfo is not None

    loan = await logged_in.loans.borrow("870970-basis:141629433")
    assert loan.record.record_id == "870970-basis:141629433"
    assert json.loads(fake.api_requests("/account/loans")[-1].content) == {
        "recordId": "870970-basis:141629433"
    }


async def test_a_loan_left_out_of_the_renewal_answer_was_not_renewed(
    logged_in: AsyncBiblo, fake: FakeBiblo
) -> None:
    refused = {**PHYSICAL_LOAN, "id": "p-loan-2", "isRenewable": False}
    fake.loans["mybiblo"] += [PHYSICAL_LOAN, refused]
    renewed = await logged_in.loans.renew(["p-loan-1", "p-loan-2", "no-such-loan"])
    assert [r.loan_details.id for r in renewed] == ["p-loan-1"]
    assert renewed[0].loan_details.due_date.year == 2027
    sent = fake.api_requests("/renew")[-1].url.params.get_list("loanIds")
    assert sent == ["p-loan-1", "p-loan-2", "no-such-loan"]


async def test_renewing_a_digital_loan_is_refused(logged_in: AsyncBiblo) -> None:
    """The API renews only physical loans, and refuses the whole request (recorded)."""
    with pytest.raises(BadRequestError, match="Could not resolve request"):
        await logged_in.loans.renew([DIGITAL_LOAN_ID])


async def test_reservations(logged_in: AsyncBiblo, fake: FakeBiblo) -> None:
    made = await logged_in.reservations.create("870970-basis:141661116", expiry_days=30)
    assert made[0].state == "RESERVED"
    assert json.loads(fake.api_requests("/account/reservations")[-1].content) == {
        "recordIds": ["870970-basis:141661116"],
        "expiryInterval": 30,
    }
    await logged_in.reservations.cancel(["r-1", "r-2"])
    cancel = fake.api_requests("/account/reservations")[-1]
    assert cancel.method == "DELETE"
    assert cancel.url.params.get_list("reservationIds") == ["r-1", "r-2"]


def reserved_with(fake: FakeBiblo) -> dict:
    [post] = [r for r in fake.api_requests("/account/reservations") if r.method == "POST"]
    return json.loads(post.content)


async def test_reserving_a_periodical_issue_sends_the_specs_fields(
    logged_in: AsyncBiblo, fake: FakeBiblo
) -> None:
    await logged_in.reservations.create(
        "870970-basis:141661116",
        pickup_branch_id="b",
        periodical_volume="1",
        periodical_number="2",
        periodical_year="2026",
    )
    assert reserved_with(fake)["periodical"] == {"volume": "1", "number": "2", "year": "2026"}


async def test_a_periodical_issue_can_be_picked_by_year_alone(
    logged_in: AsyncBiblo, fake: FakeBiblo
) -> None:
    await logged_in.reservations.create("870970-basis:141661116", periodical_year="2026")
    assert reserved_with(fake)["periodical"] == {"year": "2026"}


async def test_the_old_periodical_parameters_are_gone(logged_in: AsyncBiblo) -> None:
    with pytest.raises(TypeError):
        await logged_in.reservations.create("870970-basis:141661116", volume_number="2")  # ty: ignore[unknown-argument]


DIGITAL_LOAN_IDS = [DIGITAL_LOAN_ID, "TestLoan000000000002", "TestLoan000000000003"]


async def test_account_listings_include_physical_items(
    logged_in: AsyncBiblo, fake: FakeBiblo
) -> None:
    """The API hides physical items unless asked as Mit Biblo (#3)."""
    reservations = await logged_in.reservations.list()
    assert [r.id for r in reservations] == RESERVATION_IDS  # recorded under "mybiblo" only
    assert all(r.physical is not None for r in reservations)
    assert reservations[0].claim is not None

    fake.reservations["biblo"].append(RESERVATION)
    fake.loans["mybiblo"].append(PHYSICAL_LOAN)
    reservations = await logged_in.reservations.list()
    assert [(r.id, r.physical is not None) for r in reservations][:2] == [
        ("r-1", False),
        (RESERVATION_IDS[0], True),
    ]
    loans = await logged_in.loans.list()
    assert [loan.id for loan in loans] == [*DIGITAL_LOAN_IDS, "p-loan-1"]


async def test_an_item_listed_under_both_app_types_appears_once(
    logged_in: AsyncBiblo, fake: FakeBiblo
) -> None:
    fake.reservations["biblo"].append(fake.reservations["mybiblo"][0])
    fake.loans["mybiblo"].append(fake.loans["biblo"][0])
    assert [r.id for r in await logged_in.reservations.list()] == RESERVATION_IDS
    assert [loan.id for loan in await logged_in.loans.list()] == DIGITAL_LOAN_IDS


def test_sync_account_listings_include_physical_items(sync_biblo: Biblo, fake: FakeBiblo) -> None:
    fake.loans["mybiblo"].append(PHYSICAL_LOAN)
    sync_biblo.login("0102031234", "1234")
    assert [r.id for r in sync_biblo.reservations.list()] == RESERVATION_IDS
    assert [loan.id for loan in sync_biblo.loans.list()] == [*DIGITAL_LOAN_IDS, "p-loan-1"]


async def test_a_reservation_with_a_pickup_branch_is_made_as_mit_biblo(
    logged_in: AsyncBiblo, fake: FakeBiblo
) -> None:
    """Under "biblo" the API doesn't know physical records (#3)."""
    await logged_in.reservations.create(["870970-basis:141661116"], pickup_branch_id="DK-1")
    await logged_in.reservations.create(["870970-basis:141661116"])
    physical, digital = [r for r in fake.api_requests("/reservations") if r.method == "POST"]
    assert physical.headers["x-app-type"] == "mybiblo"
    assert digital.headers["x-app-type"] == "biblo"


async def test_reserving_a_physical_record_without_a_branch_says_it_needs_one(
    logged_in: AsyncBiblo,
) -> None:
    with pytest.raises(PickupBranchRequiredError) as caught:
        await logged_in.reservations.create(PHYSICAL_RECORD)
    assert isinstance(caught.value, BibloError)
    assert "physical" in str(caught.value)
    assert "pickup_branch_id" in str(caught.value)


async def test_the_fake_api_refuses_bodies_the_spec_does_not_describe(
    logged_in: AsyncBiblo,
) -> None:
    """So the client can't drift from the spec's request models unnoticed."""
    body = {"recordIds": ["870970-basis:141661116"], "periodical": {"volumeNumber": "2"}}
    with pytest.raises(BadRequestError, match="volumeNumber"):
        await logged_in.request("POST", "/v1/agencies/{agencyId}/account/reservations", json=body)


def test_sync_client_reserves_a_periodical_issue(sync_biblo: Biblo, fake: FakeBiblo) -> None:
    sync_biblo.login("0102031234", "1234")
    sync_biblo.reservations.create(
        "870970-basis:141661116",
        pickup_branch_id="b",
        periodical_volume="1",
        periodical_number="2",
        periodical_year="2026",
    )
    assert reserved_with(fake)["periodical"] == {"volume": "1", "number": "2", "year": "2026"}


async def test_account_favorites_and_messages(logged_in: AsyncBiblo, fake: FakeBiblo) -> None:
    quotas = await logged_in.account.quotas()
    assert quotas.remaining_loan_quotas.ebook == 6
    assert await logged_in.account.fees() == []
    favorites = await logged_in.favorites.list()
    assert len(favorites) == 4
    assert favorites[0].title
    await logged_in.favorites.add("work-of:1")
    assert json.loads(fake.api_requests("/favorites")[-1].content) == {"workId": "work-of:1"}
    assert await logged_in.messages.list() == []  # the recorded message is Mit Biblo's


async def test_raw_request_for_uncovered_endpoints(biblo: AsyncBiblo) -> None:
    data = await biblo.request("GET", "/v1/agencies/{agencyId}/search/works", params={"q": "x"})
    assert data["hitcount"] == 12  # plain JSON, envelope removed


async def test_api_errors_are_typed(biblo: AsyncBiblo, fake: FakeBiblo) -> None:
    with pytest.raises(NotFoundError) as info:
        await biblo.catalog.work("work-of:does-not-exist")
    assert info.value.status_code == 404
    assert info.value.type == "NOT_FOUND"

    def forbidden(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"id": 4205, "type": "FORBIDDEN", "message": "app-type"})

    fake.overrides[("GET", f"/v1/agencies/{LIBRARY}/search/works")] = forbidden
    with pytest.raises(PermissionDeniedError, match="app-type"):
        await biblo.catalog.search("x")


def _with_null_title(request: httpx.Request) -> httpx.Response:
    body = fixture("work")
    body["data"]["title"] = None
    return httpx.Response(200, json=body)


async def test_unexpected_values_fail_in_strict_mode(biblo: AsyncBiblo, fake: FakeBiblo) -> None:
    fake.overrides[("GET", f"/v1/agencies/{LIBRARY}/works/w")] = _with_null_title
    with pytest.raises(ResponseValidationError, match="title"):
        await biblo.catalog.work("w")


def test_unexpected_values_are_kept_and_logged_when_lenient(
    fake: FakeBiblo, caplog: pytest.LogCaptureFixture
) -> None:
    fake.overrides[("GET", f"/v1/agencies/{LIBRARY}/works/w")] = _with_null_title
    token = _lenient._strict.set(False)
    try:
        http = httpx.Client(transport=httpx.MockTransport(fake.handle))
        with (
            caplog.at_level(logging.DEBUG, "biblo"),
            record_drift() as drift,
            Biblo(LIBRARY, http_client=http) as client,
        ):
            work = client.catalog.work("w")
    finally:
        _lenient._strict.reset(token)
    assert work.title is None  # kept as sent
    assert work.variations  # the rest still parsed
    assert drift == [("Work", "title", drift[0][2])]
    assert "Work.title" in caplog.text


def test_sync_client_mirrors_the_async_one(sync_biblo: Biblo, fake: FakeBiblo) -> None:
    works = list(sync_biblo.catalog.iter_search("potter", page_size=5, max_results=6))
    assert len(works) == 6
    sync_biblo.login("0102031234", "1234")
    assert sync_biblo.loans.list()[0].id == DIGITAL_LOAN_ID
    assert fake.api_requests("/account/loans")[-1].headers["authorization"] == "Bearer access-1"
