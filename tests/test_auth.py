"""Logging in through login.bib.dk, token refresh, logout and the saved session."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest

from biblo import (
    AsyncBiblo,
    AsyncMemoryTokenStorage,
    InvalidCredentialsError,
    LoginFlowError,
    NotLoggedInError,
    code_from_redirect,
)
from tests.conftest import saved_session
from tests.fake import LIBRARY, PIN, USER, FakeBiblo


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
    assert account.name == "Test Testesen"
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


async def test_wrong_pincode_is_invalid_credentials_with_the_pages_message(
    biblo: AsyncBiblo,
) -> None:
    with pytest.raises(InvalidCredentialsError, match="Forkert lånernummer"):
        await biblo.login(USER, "9999")
    assert not await biblo.is_logged_in()


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
        await client.loans.list()
    assert [r.url.path for r in fake.api_requests()] == [
        "/v1/auth/token-refresh",
        f"/v1/agencies/{LIBRARY}/account/loans",
    ]
    assert storage.data is not None
    assert storage.data["tokens"]["access_token"] == "access-2"  # saved for next time
    await http.aclose()


async def test_a_401_refreshes_once_and_retries(logged_in: AsyncBiblo, fake: FakeBiblo) -> None:
    fake.revoked.add("access-1")
    loans = await logged_in.loans.list()
    assert len(loans) == 1
    paths = [r.url.path for r in fake.api_requests()]
    assert paths.count("/v1/auth/token-refresh") == 1
    assert fake.api_requests("/account/loans")[-1].headers["authorization"] == "Bearer access-2"


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


def test_tokens_stay_out_of_reprs() -> None:
    from biblo._base import Session

    session = Session.from_dict(saved_session())
    assert session is not None
    assert "access-1" not in repr(session)
    assert "refresh-1" not in repr(session)
