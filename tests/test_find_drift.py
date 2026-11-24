"""scripts/find_drift.py: every endpoint under each app type it accepts, against the fake."""

from __future__ import annotations

from collections import defaultdict

from biblo import AsyncBiblo
from biblo.exceptions import NotFoundError
from scripts.find_drift import Crawler, Difference, Summary, crawl_account, crawl_library
from tests.fake import LIBRARY, FakeBiblo, fixture


def app_types_by_path(fake: FakeBiblo) -> dict[str, set[str]]:
    found: defaultdict[str, set[str]] = defaultdict(set)
    for request in fake.api_requests():
        found[request.url.path].add(request.headers["x-app-type"])
    return dict(found)


async def test_account_is_crawled_under_each_accepted_app_type(
    logged_in: AsyncBiblo, fake: FakeBiblo
) -> None:
    crawler = Crawler()
    await crawl_account(crawler, logged_in)

    assert crawler.failures == []
    sent = app_types_by_path(fake)
    lib = f"/v1/agencies/{LIBRARY}/account"
    every = {"biblo", "biblogo", "mybiblo"}
    assert sent[lib] == sent[f"{lib}/fees"] == every
    assert sent[f"{lib}/quotas"] == {"biblo", "biblogo"}
    assert sent[f"{lib}/loans"] == sent[f"{lib}/reservations"] == every
    assert sent["/v1/user/favorites"] == every
    assert sent["/v1/user/messages"] == {"biblo", "mybiblo"}
    assert {r.method for r in fake.api_requests()} == {"GET"}


async def test_account_differences_leave_out_ids_and_titles(
    logged_in: AsyncBiblo, fake: FakeBiblo
) -> None:
    crawler = Crawler()
    await crawl_account(crawler, logged_in)

    loans = next(d for d in crawler.differences if d.label == "loans")
    assert [app_types for app_types, _ in loans.groups][-1] == ["mybiblo"]
    report = "\n".join(line for d in crawler.differences for line in d.lines())
    for loan in fixture("account_loans_mybiblo")["data"]:
        assert loan["id"] not in report
        assert loan["record"]["title"] not in report


async def test_chosen_app_types_skip_endpoints_quietly(biblo: AsyncBiblo, fake: FakeBiblo) -> None:
    crawler = Crawler(app_types=("biblo",))
    await crawl_library(crawler, biblo, LIBRARY)

    # The fake answers some routes with 404; nothing failed for its app type.
    assert [f for f in crawler.failures if "NotFoundError" not in f] == []
    assert crawler.differences == []
    assert {r.headers["x-app-type"] for r in fake.api_requests()} == {"biblo"}
    assert not fake.api_requests("/branches")  # accepts only mybiblo


async def test_catalogue_is_crawled_under_every_catalogue(
    biblo: AsyncBiblo, fake: FakeBiblo
) -> None:
    crawler = Crawler()
    await crawl_library(crawler, biblo, LIBRARY)

    sent = app_types_by_path(fake)
    assert sent[f"/v1/agencies/{LIBRARY}/search/works"] == {"biblo", "biblogo", "mybiblo"}
    assert sent[f"/v1/agencies/{LIBRARY}/branches"] == {"mybiblo"}
    assert any(d.label.startswith(f"{LIBRARY} search ") for d in crawler.differences)


async def test_an_item_missing_under_some_app_types_is_a_difference() -> None:
    async def fetch(app_type: str | None) -> list[str]:
        if app_type == "mybiblo":
            return ["found"]
        raise NotFoundError(404, {}, "GET", "/v1/agencies/1/works/w")

    template = "/v1/agencies/{agencyId}/works/{workId}"
    crawler = Crawler()
    await crawler.each("work", template, fetch, missing_ok=True)
    await crawler.each("strict", template, fetch)

    assert [f.split(":")[0] for f in crawler.failures] == ["strict [biblo]", "strict [biblogo]"]
    assert crawler.differences[0].lines()[1:] == [
        "    mybiblo:       count 1; 0 ids",
        "    biblo=biblogo: NotFoundError",
    ]


def test_a_difference_names_ids_only_where_allowed() -> None:
    digital = Summary(2, frozenset({"a", "b"}), frozenset({"EBOOKS"}))
    printed = Summary(3, frozenset({"a", "c"}), frozenset({"BOOKS"}))
    public = Difference("search", [(["biblo", "biblogo"], digital), (["mybiblo"], printed)])
    private = Difference("loans", public.groups, private=True)

    assert public.lines() == [
        "  search: biblo=biblogo | mybiblo",
        "    biblo=biblogo: count 2; 2 ids (1 only here: b); kinds EBOOKS",
        "    mybiblo:       count 3; 2 ids (1 only here: c); kinds BOOKS",
    ]
    assert private.lines()[1:] == [
        "    biblo=biblogo: count 2; 2 ids (1 only here); kinds EBOOKS",
        "    mybiblo:       count 3; 2 ids (1 only here); kinds BOOKS",
    ]
