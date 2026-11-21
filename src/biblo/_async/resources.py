"""The API's endpoints, grouped by what they are about. Reached through the client:
`client.catalog.search(...)`, `client.loans.list()`, ...

Every method that belongs to a library takes `library_id`, defaulting to the client's.
"""

from __future__ import annotations

import builtins
from collections.abc import AsyncIterator, Iterable
from typing import TYPE_CHECKING, Any, Unpack, cast

from .. import models as m
from .._base import ids
from ..exceptions import BadRequestError, PickupBranchRequiredError
from ..types import SEARCH_FILTER_PARAMS, SampleFormat, SearchBy, SearchFilters

if TYPE_CHECKING:
    from .client import AsyncBiblo

LIB = "/v1/agencies/{agencyId}"
# The Mit Biblo app's x-app-type: account calls under it include physical items (#3).
MIT_BIBLO = "mybiblo"


class AsyncResource:
    def __init__(self, client: AsyncBiblo) -> None:
        self._client = client


async def _digital_and_physical[T: m.Loan | m.ReservationItem](
    client: AsyncBiblo, listing: type[list[T]], template: str, library_id: str | None
) -> list[T]:
    """An account listing as Biblo sees it, then the physical items only Mit Biblo sees
    (#3), each in the API's order. An item listed under both comes once, from the first."""
    listings = [
        await client._get(listing, template, library_id=library_id, auth=True, app_type=app_type)
        for app_type in (None, MIT_BIBLO)
    ]
    seen: set[str] = set()
    merged: list[T] = []
    for found in listings[0] + listings[1]:
        if found.id not in seen:
            seen.add(found.id)
            merged.append(found)
    return merged


class AsyncLibraries(AsyncResource):
    """Libraries ("agencies" in the API) and their branches, opening hours and events."""

    async def list(self) -> builtins.list[m.Agency]:
        """Every library in Denmark, the Faroe Islands and Greenland."""
        return await self._client._get(list[m.Agency], "/v1/agencies")

    async def find(self, text: str) -> builtins.list[m.Agency]:
        """Libraries whose name, city or id contains `text`, ignoring case."""
        needle = text.casefold()
        return [
            agency
            for agency in await self.list()
            if needle in agency.id
            or needle in agency.name.casefold()
            or (agency.address is not None and needle in (agency.address.city or "").casefold())
        ]

    async def get(self, library_id: str | None = None) -> m.Agency:
        return await self._client._get(m.Agency, LIB, library_id=library_id)

    async def configuration(self, library_id: str | None = None) -> m.AgencyConfiguration:
        """The library's rules: loan and reservation periods, pincode rules, features."""
        return await self._client._get(
            m.AgencyConfiguration, f"{LIB}/configuration", library_id=library_id
        )

    async def branches(self, library_id: str | None = None) -> builtins.list[m.Branch]:
        return await self._client._get(list[m.Branch], f"{LIB}/branches", library_id=library_id)

    async def branch(self, branch_id: str, library_id: str | None = None) -> m.Branch:
        return await self._client._get(
            m.Branch, f"{LIB}/branches/{{branchId}}", {"branchId": branch_id}, library_id=library_id
        )

    async def opening_hours(self, library_id: str | None = None) -> builtins.list[m.OpeningHours]:
        """Opening hours of every branch."""
        return await self._client._get(
            list[m.OpeningHours], f"{LIB}/openinghours", library_id=library_id
        )

    async def branch_opening_hours(
        self,
        branch_id: str,
        *,
        start: str | None = None,
        end: str | None = None,
        library_id: str | None = None,
    ) -> m.OpeningHours:
        """One branch's opening hours from `start` to `end` (YYYY-MM-DD)."""
        return await self._client._get(
            m.OpeningHours,
            f"{LIB}/branches/{{branchId}}/openinghours",
            {"branchId": branch_id},
            params={"from": start, "to": end},
            library_id=library_id,
        )

    async def open_status(self, library_id: str | None = None) -> m.AgencyOpenStatus:
        """Which branches are open now, and until when."""
        return await self._client._get(
            m.AgencyOpenStatus, f"{LIB}/openstatus", library_id=library_id
        )

    async def events(
        self,
        branch_id: str | None = None,
        *,
        start: str | None = None,
        end: str | None = None,
        library_id: str | None = None,
    ) -> builtins.list[m.Event]:
        """Events at the library, or at one branch, from `start` to `end` (YYYY-MM-DD)."""
        if branch_id is None:
            template, path_params = f"{LIB}/events", {}
        else:
            template, path_params = f"{LIB}/branches/{{branchId}}/events", {"branchId": branch_id}
        return await self._client._get(
            list[m.Event],
            template,
            path_params,
            params={"from": start, "to": end},
            library_id=library_id,
        )


class AsyncCatalog(AsyncResource):
    """Searching and looking up works (titles) and records (editions)."""

    async def search(
        self,
        q: str,
        *,
        by: SearchBy | None = None,
        offset: int | None = None,
        limit: int | None = None,
        library_id: str | None = None,
        **filters: Unpack[SearchFilters],
    ) -> m.WorkSearchResult:
        """Search the library's catalogue. `.hitcount` is the total, `.works` this page."""
        params = {"q": q, "type": by, "offset": offset, "limit": limit, **_filters(filters)}
        return await self._client._get(
            m.WorkSearchResult, f"{LIB}/search/works", params=params, library_id=library_id
        )

    async def iter_search(
        self,
        q: str,
        *,
        by: SearchBy | None = None,
        page_size: int = 50,
        max_results: int | None = None,
        library_id: str | None = None,
        **filters: Unpack[SearchFilters],
    ) -> AsyncIterator[m.WorkLight]:
        """Every work a search finds, fetching pages of `page_size` as needed."""
        offset = 0
        while max_results is None or offset < max_results:
            limit = page_size if max_results is None else min(page_size, max_results - offset)
            page = await self.search(
                q, by=by, offset=offset, limit=limit, library_id=library_id, **filters
            )
            for work in page.works:
                yield work
            offset += len(page.works)
            if not page.works or offset >= page.hitcount:
                return

    async def facets(
        self,
        q: str,
        *,
        by: SearchBy | None = None,
        values_limit: int | None = None,
        library_id: str | None = None,
        **filters: Unpack[SearchFilters],
    ) -> m.Facets:
        """The filter values a search has, with counts."""
        params = {"q": q, "type": by, "valuesLimit": values_limit, **_filters(filters)}
        return await self._client._get(
            m.Facets, f"{LIB}/search/facets", params=params, library_id=library_id
        )

    async def suggest(self, q: str, library_id: str | None = None) -> builtins.list[m.Link]:
        """Search-as-you-type suggestions."""
        return await self._client._get(
            list[m.Link], f"{LIB}/search/suggest", params={"q": q}, library_id=library_id
        )

    async def work(self, work_id: str, library_id: str | None = None) -> m.Work:
        """A work and its `variations`: the records (editions) you borrow or reserve."""
        return await self._client._get(
            m.Work, f"{LIB}/works/{{workId}}", {"workId": work_id}, library_id=library_id
        )

    async def availability(
        self, work_id: str, library_id: str | None = None
    ) -> builtins.list[m.MaterialTypeOverview]:
        """Per material type (e-book, audiobook, book, ...): can it be borrowed or reserved."""
        return await self._client._get(
            list[m.MaterialTypeOverview],
            f"{LIB}/works/{{workId}}/material-type-status",
            {"workId": work_id},
            library_id=library_id,
        )

    async def recommendations(
        self, work_id: str, library_id: str | None = None
    ) -> builtins.list[m.WorkLight]:
        return await self._client._get(
            list[m.WorkLight],
            f"{LIB}/works/{{workId}}/recommendations",
            {"workId": work_id},
            library_id=library_id,
        )

    async def reviews(
        self, work_id: str, library_id: str | None = None
    ) -> builtins.list[m.WorkReview]:
        return await self._client._get(
            list[m.WorkReview],
            f"{LIB}/works/{{workId}}/reviews",
            {"workId": work_id},
            library_id=library_id,
        )

    async def series(
        self, series_id: str, library_id: str | None = None
    ) -> builtins.list[m.WorkLight]:
        """The works in a series, in order."""
        return await self._client._get(
            list[m.WorkLight],
            f"{LIB}/series/{{seriesId}}",
            {"seriesId": series_id},
            library_id=library_id,
        )

    async def record(self, record_id: str, library_id: str | None = None) -> m.Record:
        return await self._client._get(
            m.Record, f"{LIB}/records/{{recordId}}", {"recordId": record_id}, library_id=library_id
        )

    async def records_status(
        self, record_ids: str | Iterable[str], library_id: str | None = None
    ) -> builtins.list[m.RecordStatus]:
        """Whether records can be borrowed now, or how long the queue is."""
        return await self._client._get(
            list[m.RecordStatus],
            f"{LIB}/records/status",
            params={"recordIds": ids(record_ids)},
            library_id=library_id,
        )

    async def holdings(
        self, record_ids: str | Iterable[str], library_id: str | None = None
    ) -> builtins.list[m.RecordHoldings]:
        """Where the physical copies are, and whether they are on the shelf."""
        return await self._client._get(
            list[m.RecordHoldings],
            f"{LIB}/records/holdings",
            params={"recordIds": ids(record_ids)},
            library_id=library_id,
        )

    async def sample(
        self, record_id: str, format: SampleFormat | None = None, library_id: str | None = None
    ) -> m.Sample:
        """A download link for the publisher's free sample of an e-book or audiobook."""
        return await self._client._get(
            m.Sample,
            f"{LIB}/records/{{recordId}}/sample",
            {"recordId": record_id},
            params={"format": format},
            library_id=library_id,
        )

    async def material(self, material_id: str, library_id: str | None = None) -> m.MaterialStatus:
        """A physical copy by its barcode (Scan & Loan)."""
        return await self._client._get(
            m.MaterialStatus,
            f"{LIB}/materials/{{materialId}}/status",
            {"materialId": material_id},
            library_id=library_id,
        )


class AsyncAccount(AsyncResource):
    """The logged-in user's details, fees and digital loan quota."""

    async def get(self, library_id: str | None = None) -> m.Account:
        return await self._client._get(
            m.Account, f"{LIB}/account", library_id=library_id, auth=True
        )

    async def update(
        self,
        *,
        receive_messages_in_app: bool | None = None,
        email: str | None = None,
        email_notifications: bool | None = None,
        phone: str | None = None,
        phone_notifications: bool | None = None,
        preferred_pickup_branch_id: str | None = None,
        library_id: str | None = None,
    ) -> m.Account:
        body: dict[str, Any] = {}
        if receive_messages_in_app is not None:
            body["receiveMessagesInApp"] = receive_messages_in_app
        if email is not None:
            body["email"] = _without_none(address=email, receiveNotification=email_notifications)
        if phone is not None:
            body["phone"] = _without_none(number=phone, receiveNotification=phone_notifications)
        if preferred_pickup_branch_id is not None:
            body["preferredPickupBranchId"] = preferred_pickup_branch_id
        return await self._client._call(
            m.Account, "PATCH", f"{LIB}/account", json=body, library_id=library_id, auth=True
        )

    async def change_pincode(
        self, old_pincode: str, new_pincode: str, library_id: str | None = None
    ) -> None:
        await self._client._call(
            None,
            "PUT",
            f"{LIB}/account/pincode",
            json={"oldPincode": old_pincode, "pincode": new_pincode},
            library_id=library_id,
            auth=True,
        )

    async def fees(self, library_id: str | None = None) -> builtins.list[m.Fee]:
        return await self._client._get(
            list[m.Fee], f"{LIB}/account/fees", library_id=library_id, auth=True
        )

    async def quotas(self, library_id: str | None = None) -> m.QuotaSummary:
        """How many e-books and audiobooks are left to borrow this period."""
        return await self._client._get(
            m.QuotaSummary, f"{LIB}/account/quotas", library_id=library_id, auth=True
        )

    async def reader_token(self) -> str:
        """A sign-in token for the WeDoBooks reader/player SDK the app uses for playback."""
        result = await self._client._call(
            m.WdbAuthorizeResponse, "POST", "/v1/user/wdb-sdk/authorize", auth=True
        )
        return result.wdb_token


class AsyncLoans(AsyncResource):
    async def list(self, library_id: str | None = None) -> builtins.list[m.Loan]:
        """Digital loans, then physical ones."""
        return await _digital_and_physical(
            self._client, list[m.Loan], f"{LIB}/account/loans", library_id
        )

    async def borrow(self, record_id: str, library_id: str | None = None) -> m.Loan:
        """Borrow a digital record (e-book, audiobook, podcast). Counts against the quota."""
        return await self._client._call(
            m.Loan,
            "POST",
            f"{LIB}/account/loans",
            json={"recordId": record_id},
            library_id=library_id,
            auth=True,
        )

    async def borrow_physical(self, material_id: str, library_id: str | None = None) -> m.Loan:
        """Scan & Loan: borrow a physical copy by its barcode."""
        return await self._client._call(
            m.Loan,
            "POST",
            f"{LIB}/account/loans",
            json={"materialId": material_id},
            library_id=library_id,
            auth=True,
        )

    async def renew(
        self, loan_ids: str | Iterable[str], library_id: str | None = None
    ) -> builtins.list[m.RenewedLoan]:
        return await self._client._call(
            list[m.RenewedLoan],
            "POST",
            f"{LIB}/account/loans/renew",
            params={"loanIds": ids(loan_ids)},
            library_id=library_id,
            auth=True,
        )

    async def return_physical(
        self, material_id: str, branch_id: str, library_id: str | None = None
    ) -> m.MaterialReturnResult:
        """Scan & Loan: return a physical copy at a branch."""
        return await self._client._call(
            m.MaterialReturnResult,
            "POST",
            f"{LIB}/account/loans/return",
            json={"materialId": material_id, "branchId": branch_id},
            library_id=library_id,
            auth=True,
        )


class AsyncReservations(AsyncResource):
    async def list(self, library_id: str | None = None) -> builtins.list[m.ReservationItem]:
        """Digital reservations, then physical ones."""
        return await _digital_and_physical(
            self._client, list[m.ReservationItem], f"{LIB}/account/reservations", library_id
        )

    async def create(
        self,
        record_ids: str | Iterable[str],
        *,
        pickup_branch_id: str | None = None,
        expiry_days: int | None = None,
        periodical_volume: str | None = None,
        periodical_number: str | None = None,
        periodical_year: str | None = None,
        library_id: str | None = None,
    ) -> builtins.list[m.ReservationItem]:
        """Reserve records. A physical one needs `pickup_branch_id`.

        `expiry_days` must be one the library's configuration lists.

        `periodical_volume`, `periodical_number` and `periodical_year` pick an issue of a
        periodical, as strings like `"01"`, `"01"` and `"2026"`. The year is what identifies
        the issue (the Mit Biblo app sends the others only along with it); give each you know.
        """
        issue = (periodical_volume, periodical_number, periodical_year)
        request = m.ReserveRequest(
            recordIds=[m.RecordId(i) for i in ids(record_ids)],
            pickupBranchId=pickup_branch_id,
            expiryInterval=expiry_days,
            periodical=m.ReservePeriodicalRequest(
                volume=periodical_volume, number=periodical_number, year=periodical_year
            )
            if any(part is not None for part in issue)
            else None,
        )
        body = request.model_dump(mode="json", by_alias=True, exclude_none=True)
        try:
            result = await self._client._call(
                list[m.ReservationItem] | None,
                "POST",
                f"{LIB}/account/reservations",
                json=body,
                library_id=library_id,
                auth=True,
                # Under "biblo" the API doesn't know physical records, which need a branch (#3).
                app_type=MIT_BIBLO if pickup_branch_id is not None else None,
            )
        except BadRequestError as err:
            if pickup_branch_id is None and err.message.startswith("No material types found"):
                error = PickupBranchRequiredError(err.status_code, err.body, err.method, err.url)
                raise error from err
            raise
        return result or []

    async def update(
        self,
        reservation_id: str,
        *,
        pickup_branch_id: str | None = None,
        expiry_days: int | None = None,
        library_id: str | None = None,
    ) -> m.ReservationItem:
        return await self._client._call(
            m.ReservationItem,
            "PATCH",
            f"{LIB}/account/reservations/{{reservationId}}",
            {"reservationId": reservation_id},
            json=_without_none(pickupBranch=pickup_branch_id, expiryInterval=expiry_days),
            library_id=library_id,
            auth=True,
        )

    async def cancel(
        self, reservation_ids: str | Iterable[str], library_id: str | None = None
    ) -> None:
        await self._client._call(
            None,
            "DELETE",
            f"{LIB}/account/reservations",
            params={"reservationIds": ids(reservation_ids)},
            library_id=library_id,
            auth=True,
        )

    async def convert(
        self, claim_ids: str | Iterable[str], library_id: str | None = None
    ) -> builtins.list[m.ConvertDigitalReservationsResponse]:
        """Turn digital reservations that are ready (their `claim.id`) into loans."""
        return await self._client._call(
            list[m.ConvertDigitalReservationsResponse],
            "POST",
            f"{LIB}/account/reservations/convert",
            json={"claimIds": ids(claim_ids)},
            library_id=library_id,
            auth=True,
        )


class AsyncFavorites(AsyncResource):
    """The user's saved works ("Huskeliste"). Not tied to a library."""

    async def list(self) -> builtins.list[m.Work]:
        return await self._client._get(list[m.Work], "/v1/user/favorites", auth=True)

    async def add(self, work_id: str) -> None:
        await self._client._call(
            None, "PUT", "/v1/user/favorites", json={"workId": work_id}, auth=True
        )

    async def remove(self, work_id: str) -> None:
        await self._client._call(
            None,
            "DELETE",
            "/v1/user/favorites/{workId}",
            {"workId": work_id},
            auth=True,
        )


class AsyncMessages(AsyncResource):
    """Messages from the library to the user."""

    async def list(self) -> builtins.list[m.MessagesResponseDataMessage]:
        # The response is a MessagesResponse; the client has already unwrapped its `data`.
        result = await self._client._get(m.MessagesResponseData, "/v1/user/messages", auth=True)
        return result.messages

    async def mark_seen(self, message_id: str) -> None:
        await self._client._call(
            None,
            "PUT",
            "/v1/user/messages/{messageId}/mark-as-seen",
            {"messageId": message_id},
            auth=True,
        )

    async def delete(self, message_ids: str | Iterable[str]) -> None:
        await self._client._call(
            None,
            "DELETE",
            "/v1/user/messages",
            params={"messageIds": ids(message_ids)},
            auth=True,
        )


class AsyncContent(AsyncResource):
    """The editorial front page: themed categories, pages and their elements."""

    async def categories(self, library_id: str | None = None) -> builtins.list[m.Category]:
        return await self._client._get(
            list[m.Category], f"{LIB}/content/categories", library_id=library_id
        )

    async def category(self, category_id: str, library_id: str | None = None) -> m.Category:
        return await self._client._get(
            m.Category,
            f"{LIB}/content/categories/{{categoryId}}",
            {"categoryId": category_id},
            library_id=library_id,
        )

    async def category_element(
        self, category_id: str, element_id: str, library_id: str | None = None
    ) -> m.HydratedElement:
        return await self._client._get(
            m.HydratedElement,
            f"{LIB}/content/categories/{{categoryId}}/elements/{{elementId}}",
            {"categoryId": category_id, "elementId": element_id},
            library_id=library_id,
        )

    async def known_pages(self, library_id: str | None = None) -> m.KnownPages:
        return await self._client._get(
            m.KnownPages, f"{LIB}/content/known-pages", library_id=library_id
        )

    async def page(self, page_id: str, library_id: str | None = None) -> m.Page:
        return await self._client._get(
            m.Page, f"{LIB}/content/pages/{{pageId}}", {"pageId": page_id}, library_id=library_id
        )

    async def page_element(
        self, page_id: str, element_id: str, library_id: str | None = None
    ) -> m.HydratedElement:
        return await self._client._get(
            m.HydratedElement,
            f"{LIB}/content/pages/{{pageId}}/elements/{{elementId}}",
            {"pageId": page_id, "elementId": element_id},
            library_id=library_id,
        )


class AsyncDevices(AsyncResource):
    """Push notifications for the logged-in user."""

    async def register(self, push_token: str) -> None:
        """Subscribe a Firebase Cloud Messaging token."""
        await self._client._call(
            None, "POST", "/v1/user/device", json={"pushToken": push_token}, auth=True
        )

    async def unregister(self) -> None:
        await self._client._call(None, "DELETE", "/v1/user/device", auth=True)


def _filters(filters: SearchFilters) -> dict[str, list[str]]:
    """The filters as the API's query parameters: camelCase names, list values."""
    # Every SearchFilters value is `str | list[str]`; iterating a TypedDict loses that.
    values = cast(dict[str, str | list[str]], filters)
    if unknown := values.keys() - SEARCH_FILTER_PARAMS.keys():
        raise TypeError(f"Unknown search filter: {', '.join(sorted(unknown))}")
    return {SEARCH_FILTER_PARAMS[name]: ids(value) for name, value in values.items()}


def _without_none(**fields: Any) -> dict[str, Any]:
    return {key: value for key, value in fields.items() if value is not None}
