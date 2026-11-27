"""Logging in through login.bib.dk, token refresh, logout and the saved session."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from biblo import (
    APIError,
    AsyncBiblo,
    AsyncMemoryTokenStorage,
    AuthenticationError,
    BibloConnectionError,
    BibloError,
    InvalidCredentialsError,
    LoginBlockedError,
    LoginError,
    LoginFlowError,
    LoginRequiredError,
    NotFoundError,
    NotLoggedInError,
    PermissionDeniedError,
    ServerError,
    code_from_redirect,
)
from tests.conftest import saved_session
from tests.fake import LIBRARY, PIN, USER, FakeBiblo, _error


async def test_login_with_card_number_and_pincode(
    biblo: AsyncBiblo, fake: FakeBiblo, storage: AsyncMemoryTokenStorage
) -> None:
    await biblo.login(USER, PIN)

    assert await biblo.is_logged_in()
    assert storage.data is not None
    assert storage.data["tokens"]["access_token"] == "access-1"
    assert storage.data["library_id"] == LIBRARY
    form = next(r for r in fake.requests if r.method == "POST" and "borchk" in r.url.path)
    assert b"agency=763000" in form.content
    account = await biblo.account.get()
    assert account.name == "Test Låner"
    assert fake.api_requests("/account")[-1].headers["authorization"] == "Bearer access-1"


async def test_login_bib_dk_sees_a_mobile_browser(biblo: AsyncBiblo, fake: FakeBiblo) -> None:
    await biblo.login(USER, PIN)
    agents = {r.headers["user-agent"] for r in fake.requests if r.url.host == "login.bib.dk"}
    assert len(agents) == 1
    assert agents.pop().startswith("Mozilla/5.0 (Linux; Android")


async def test_login_leaves_no_login_bib_dk_cookies_in_a_shared_client(fake: FakeBiblo) -> None:
    def with_cookie(request: httpx.Request) -> httpx.Response:
        response = fake.handle(request)
        if request.url.host == "login.bib.dk":
            response.headers["set-cookie"] = "connect.sid=secret; Path=/; HttpOnly"
        return response

    http = httpx.AsyncClient(transport=httpx.MockTransport(with_cookie))
    async with AsyncBiblo(LIBRARY, http_client=http) as client:
        await client.login(USER, PIN)
    assert list(http.cookies.jar) == []
    await http.aclose()


async def test_a_refusal_without_a_code_has_the_pages_message(
    biblo: AsyncBiblo, fake: FakeBiblo
) -> None:
    fake.login_error = fake.login_retries = None
    with pytest.raises(InvalidCredentialsError, match="Forkert lånernummer") as caught:
        await biblo.login(USER, "9999")
    assert caught.value.code is None
    assert caught.value.retries_left is None
    assert not await biblo.is_logged_in()


async def test_a_wrong_pincode_says_how_many_tries_are_left(
    biblo: AsyncBiblo, fake: FakeBiblo
) -> None:
    fake.login_retries = 2
    with pytest.raises(InvalidCredentialsError) as caught:
        await biblo.login(USER, "9999")
    assert caught.value.code == "bonfd"
    assert caught.value.retries_left == 2
    assert "2 tries left" in str(caught.value)


@pytest.mark.parametrize("code", ["bonfd", "bonui"])
async def test_without_retries_the_message_does_not_count_tries(
    biblo: AsyncBiblo, fake: FakeBiblo, code: str
) -> None:
    fake.login_error, fake.login_retries = code, None
    with pytest.raises(InvalidCredentialsError) as caught:
        await biblo.login(USER, "9999")
    assert caught.value.code == code
    assert caught.value.retries_left is None
    assert "left" not in str(caught.value)
    assert "9999" not in str(caught.value) and USER not in str(caught.value)


async def test_outside_the_municipality_says_so(biblo: AsyncBiblo, fake: FakeBiblo) -> None:
    fake.login_error = "bnimu"
    with pytest.raises(InvalidCredentialsError, match="municipality") as caught:
        await biblo.login(USER, "9999")
    assert caught.value.code == "bnimu"


@pytest.mark.parametrize(
    ("page_text", "minutes"),
    [
        ("Login blokeret i 15 minutter.", 15),
        ("Login blokeret i 1 minut.", 1),
        ("Prøv igen senere.", None),
    ],
)
async def test_a_blocked_login_says_how_long_to_wait(
    biblo: AsyncBiblo, fake: FakeBiblo, page_text: str, minutes: int | None
) -> None:
    fake.login_blocked = page_text
    with pytest.raises(LoginBlockedError) as caught:
        await biblo.login(USER, "9999")
    assert caught.value.minutes == minutes
    assert isinstance(caught.value, LoginError)
    assert not isinstance(caught.value, LoginRequiredError)
    if minutes is not None:
        assert f"{minutes} minute" in str(caught.value)


@pytest.mark.parametrize("code", ["sevua", "zzzz"])
async def test_a_service_or_unknown_code_is_a_login_flow_error(
    biblo: AsyncBiblo, fake: FakeBiblo, code: str
) -> None:
    fake.login_error = code
    with pytest.raises(LoginFlowError, match=code):
        await biblo.login(USER, "9999")


async def test_a_changed_login_page_is_a_login_flow_error(
    biblo: AsyncBiblo, fake: FakeBiblo
) -> None:
    original = fake._login

    def redesigned(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            return httpx.Response(200, text="<html><form id='new-login'></form></html>")
        return original(request)

    fake._login = redesigned  # type: ignore[method-assign]
    with pytest.raises(LoginFlowError, match="No library login form"):
        await biblo.login(USER, PIN)


async def test_browser_login_with_the_redirect_url(biblo: AsyncBiblo) -> None:
    assert "client_id=204936c7" in biblo.authorize_url()
    await biblo.login_with_code("biblo://?code=the-code&state=")
    assert await biblo.is_logged_in()


@pytest.mark.parametrize(
    ("given", "code"),
    [("biblo://?code=abc&state=x", "abc"), ("abc", "abc"), ("  abc \n", "abc")],
)
def test_code_from_redirect(given: str, code: str) -> None:
    assert code_from_redirect(given) == code


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (httpx.ReadTimeout(""), "ReadTimeout"),  # stringifies to ""
        (httpx.ConnectError("boom"), "ConnectError: boom"),
    ],
)
async def test_an_unreachable_login_bib_dk_names_the_transport_error(
    fake: FakeBiblo, error: httpx.TransportError, reason: str
) -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        if request.url.host == "login.bib.dk":
            raise error
        return fake.handle(request)

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(unreachable)) as http,
        AsyncBiblo(LIBRARY, http_client=http) as biblo,
    ):
        with pytest.raises(BibloConnectionError) as caught:
            await biblo.login(USER, PIN)
    assert str(caught.value) == f"login.bib.dk could not be reached: {reason}"
    assert caught.value.__cause__ is error


def test_code_from_an_error_redirect() -> None:
    with pytest.raises(InvalidCredentialsError):
        code_from_redirect("biblo://?error=access_denied")


async def test_tokens_about_to_expire_are_refreshed_first(
    fake: FakeBiblo, storage: AsyncMemoryTokenStorage
) -> None:
    fake.token_number = 1
    storage.data = saved_session(expires_in=timedelta(seconds=10))
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))
    async with AsyncBiblo(storage=storage, http_client=http) as client:
        await client.account.get()
    assert [r.url.path for r in fake.api_requests()] == [
        "/v1/auth/token-refresh",
        f"/v1/agencies/{LIBRARY}/account",
    ]
    assert storage.data is not None
    assert storage.data["tokens"]["access_token"] == "access-2"  # saved for next time
    await http.aclose()


async def test_a_401_refreshes_once_and_retries(logged_in: AsyncBiblo, fake: FakeBiblo) -> None:
    fake.revoked.add("access-1")
    account = await logged_in.account.get()
    assert account.name == "Test Låner"
    paths = [r.url.path for r in fake.api_requests()]
    assert paths.count("/v1/auth/token-refresh") == 1
    assert fake.api_requests("/account")[-1].headers["authorization"] == "Bearer access-2"


async def test_a_refused_refresh_logs_out(
    logged_in: AsyncBiblo, fake: FakeBiblo, storage: AsyncMemoryTokenStorage
) -> None:
    fake.revoked.add("access-1")
    fake.refresh_works = False
    with pytest.raises(NotLoggedInError, match="expired"):
        await logged_in.loans.list()
    assert storage.data is not None
    assert storage.data["tokens"] is None
    assert storage.data["library_id"] == LIBRARY  # the library is remembered


async def test_a_client_adopts_tokens_another_client_refreshed(
    fake: FakeBiblo, storage: AsyncMemoryTokenStorage
) -> None:
    """Two processes sharing one session file, e.g. the CLI and a long-running server."""
    fake.token_number = 1
    storage.data = saved_session(access="access-1", refresh="refresh-1")
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))
    async with (
        AsyncBiblo(storage=storage, http_client=http) as a,
        AsyncBiblo(storage=storage, http_client=http) as b,
    ):
        assert await b.is_logged_in()  # b holds access-1 and refresh-1
        await a.refresh()  # the API now accepts only access-2 and refresh-2

        account = await b.account.get()

    assert account.name == "Test Låner"
    assert fake.api_requests("/account")[-1].headers["authorization"] == "Bearer access-2"
    assert [r.url.path for r in fake.api_requests("/token-refresh")] == ["/v1/auth/token-refresh"]
    assert storage.data is not None
    assert storage.data["tokens"]["refresh_token"] == "refresh-2"
    await http.aclose()


async def test_a_refused_refresh_adopts_tokens_saved_meanwhile(
    logged_in: AsyncBiblo, fake: FakeBiblo, storage: AsyncMemoryTokenStorage
) -> None:
    """Another process refreshes between this client's look at storage and its refresh."""
    fake.revoked.add("access-1")

    def another_process_refreshes_first(request: httpx.Request) -> httpx.Response:
        fake._new_tokens()
        storage.data = saved_session(access="access-2", refresh="refresh-2")
        return _error(401, 4103, "UNAUTHORIZED", "Invalid refresh token")

    fake.overrides[("POST", "/v1/auth/token-refresh")] = another_process_refreshes_first

    account = await logged_in.account.get()

    assert account.name == "Test Låner"
    assert fake.api_requests("/account")[-1].headers["authorization"] == "Bearer access-2"
    assert storage.data is not None
    assert storage.data["tokens"]["access_token"] == "access-2"  # not wiped


async def test_logout_forgets_tokens(
    logged_in: AsyncBiblo, fake: FakeBiblo, storage: AsyncMemoryTokenStorage
) -> None:
    await logged_in.logout()
    assert fake.api_requests("/v1/auth/logout")[-1].headers["authorization"] == "Bearer access-1"
    assert storage.data is not None
    assert storage.data["tokens"] is None
    assert not await logged_in.is_logged_in()


async def test_a_session_from_another_version_is_ignored(fake: FakeBiblo) -> None:
    storage = AsyncMemoryTokenStorage({"version": 999, "tokens": {"access_token": "x"}})
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))
    async with AsyncBiblo(storage=storage, http_client=http) as client:
        assert not await client.is_logged_in()
    await http.aclose()


# ------------------------------------------------------- LoginRequiredError (#20)


async def test_a_refused_pincode_is_login_required(biblo: AsyncBiblo) -> None:
    with pytest.raises(LoginRequiredError) as caught:
        await biblo.login(USER, "9999")
    assert isinstance(caught.value, InvalidCredentialsError)
    assert isinstance(caught.value, LoginError)


async def test_no_session_is_login_required(biblo: AsyncBiblo) -> None:
    with pytest.raises(LoginRequiredError) as caught:
        await biblo.loans.list()
    assert isinstance(caught.value, NotLoggedInError)


async def test_a_failed_refresh_is_login_required(logged_in: AsyncBiblo, fake: FakeBiblo) -> None:
    fake.revoked.add("access-1")
    fake.refresh_works = False
    with pytest.raises(LoginRequiredError) as caught:
        await logged_in.loans.list()
    assert isinstance(caught.value, NotLoggedInError)


async def test_a_401_after_a_refresh_is_login_required(
    logged_in: AsyncBiblo, fake: FakeBiblo
) -> None:
    fake.revoked.update({"access-1", "access-2"})  # the refreshed token is refused too
    with pytest.raises(LoginRequiredError) as caught:
        await logged_in.loans.list()
    assert isinstance(caught.value, AuthenticationError)
    assert isinstance(caught.value, APIError)
    assert caught.value.status_code == 401
    assert fake.api_requests("/v1/auth/token-refresh")  # it did refresh first


async def test_a_changed_login_page_is_not_login_required(
    biblo: AsyncBiblo, fake: FakeBiblo
) -> None:
    original = fake._login

    def redesigned(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login":
            return httpx.Response(200, text="<html><form id='new-login'></form></html>")
        return original(request)

    fake._login = redesigned  # type: ignore[method-assign]
    with pytest.raises(LoginFlowError) as caught:
        await biblo.login(USER, PIN)
    assert not isinstance(caught.value, LoginRequiredError)


async def test_an_unreachable_api_is_not_login_required(fake: FakeBiblo) -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(unreachable)) as http,
        AsyncBiblo(LIBRARY, http_client=http) as biblo,
    ):
        with pytest.raises(BibloConnectionError) as caught:
            await biblo.catalog.search("x")
    assert not isinstance(caught.value, LoginRequiredError)


@pytest.mark.parametrize(
    ("status", "error"),
    [(404, NotFoundError), (403, PermissionDeniedError), (500, ServerError), (418, APIError)],
)
async def test_other_api_errors_are_not_login_required(
    logged_in: AsyncBiblo, fake: FakeBiblo, status: int, error: type[APIError]
) -> None:
    fake.overrides["GET", f"/v1/agencies/{LIBRARY}/account"] = lambda _: httpx.Response(
        status, json={"id": 4000, "type": "ERROR", "message": "nope"}
    )
    with pytest.raises(BibloError) as caught:
        await logged_in.account.get()
    assert type(caught.value) is error
    assert not isinstance(caught.value, LoginRequiredError)


def test_login_required_keeps_the_old_bases() -> None:
    assert issubclass(LoginRequiredError, BibloError)
    assert issubclass(InvalidCredentialsError, LoginError)
    assert issubclass(NotLoggedInError, BibloError)
    assert issubclass(AuthenticationError, APIError)
    assert not issubclass(LoginFlowError, LoginRequiredError)
    assert not issubclass(LoginError, LoginRequiredError)  # LoginFlowError's base


def test_tokens_stay_out_of_reprs() -> None:
    from biblo._base import Session

    session = Session.from_dict(saved_session())
    assert session is not None
    assert "access-1" not in repr(session)
    assert "refresh-1" not in repr(session)
