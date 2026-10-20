# Generated from src/biblo/_async by scripts/generate.py. Do not edit.
from __future__ import annotations

import threading
from types import TracebackType
from typing import Any, Self

import httpx

from .. import _base, models
from .._base import Session, Tokens
from .._constants import (
    API_BASE_URL,
    CLIENT_ID,
    CLIENT_SECRET,
    DEFAULT_TIMEOUT_SECONDS,
    REDIRECT_URI,
)
from ..exceptions import (
    AuthenticationError,
    BadRequestError,
    BibloConnectionError,
    NoLibraryError,
    NotLoggedInError,
)
from .login import password_login_code
from .resources import (
    Account,
    Catalog,
    Content,
    Devices,
    Favorites,
    Libraries,
    Loans,
    Messages,
    Reservations,
)
from .storage import TokenStorage


class Biblo:
    """Client for the Biblo API, the backend of the Danish public libraries' Biblo app.

        async with AsyncBiblo("763000") as biblo:          # Vejle Bibliotekerne
            result = await biblo.catalog.search("harry potter")
            await biblo.login("1234567890", "1234")
            for loan in await biblo.loans.list():
                print(loan.record.title, loan.due_date)

    `library_id` is the library ("agency") catalogue calls default to;
    `biblo.libraries.find()` looks it up. Searching and browsing work without logging in.
    Account calls default to the library the user logged in at. With a `storage`, the
    session (tokens, that library, installation id) survives restarts and token refreshes
    are saved as they happen.

    Pass `http_client` to share an `httpx.AsyncClient`, e.g. Home Assistant's; the client
    then leaves closing it to you.
    """

    def __init__(
        self,
        library_id: str | None = None,
        *,
        storage: TokenStorage | None = None,
        http_client: httpx.Client | None = None,
        base_url: str = API_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._library_id = library_id
        self._storage = storage
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout)
        self._base_url = base_url.rstrip("/")
        self._session: Session | None = None
        self._refresh_lock = threading.Lock()

        self.libraries = Libraries(self)
        self.catalog = Catalog(self)
        self.account = Account(self)
        self.loans = Loans(self)
        self.reservations = Reservations(self)
        self.favorites = Favorites(self)
        self.messages = Messages(self)
        self.content = Content(self)
        self.devices = Devices(self)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    # ------------------------------------------------------------------ session

    def _loaded(self) -> Session:
        session = self._session
        if session is None:
            saved = self._storage.load() if self._storage else None
            session = Session.from_dict(saved) or Session()
            self._session = session
        return session

    def _save(self) -> None:
        if self._storage is not None and self._session is not None:
            self._storage.save(self._session.to_dict())

    def library_id(self) -> str | None:
        """The library catalogue calls default to: the client's, else the logged-in user's."""
        return self._library_id or (self._loaded()).library_id

    def login_library_id(self) -> str | None:
        """The library the user logged in at; account calls default to it."""
        return (self._loaded()).library_id

    def is_logged_in(self) -> bool:
        return (self._loaded()).tokens is not None

    def tokens(self) -> Tokens | None:
        """The current tokens, refreshed if they are about to expire."""
        self._loaded()
        return self._fresh_tokens()

    # --------------------------------------------------------------------- auth

    def login(self, user_id: str, pincode: str, library_id: str | None = None) -> None:
        """Log in with card number or CPR and pincode at a library. No browser needed.

        Raises InvalidCredentialsError if login.bib.dk refuses them.
        """
        session = self._loaded()
        library = library_id or self._library_id or session.library_id
        if not library:
            raise NoLibraryError("Logging in needs a library_id (see libraries.find())")
        try:
            code = password_login_code(self._http, user_id, pincode, library)
        except httpx.TransportError as err:
            raise BibloConnectionError(f"login.bib.dk could not be reached: {err}") from err
        session.library_id = library
        self.login_with_code(code)

    @staticmethod
    def authorize_url() -> str:
        """The login.bib.dk page to open in a browser for MitID and other browser logins.

        After logging in the browser is sent to a `biblo://?code=...` URL it can't open; pass
        that URL (or just the code) to `login_with_code`.
        """
        return _base.authorize_url()

    def login_with_code(self, code_or_redirect_url: str) -> None:
        session = self._loaded()
        code = _base.code_from_redirect(code_or_redirect_url)
        data = self._token_request("/v1/auth/login", {"code": code, "redirect_uri": REDIRECT_URI})
        session.tokens = Tokens.from_response(data)
        self._save()

    def refresh(self) -> None:
        """Get new tokens with the refresh token. Happens on its own before they expire."""
        session = self._loaded()
        if session.tokens is None:
            raise NotLoggedInError("Not logged in")
        try:
            data = self._token_request(
                "/v1/auth/token-refresh", {"refresh_token": session.tokens.refresh_token}
            )
        except (AuthenticationError, BadRequestError) as err:
            session.tokens = None
            self._save()
            raise NotLoggedInError("The session has expired; log in again") from err
        session.tokens = Tokens.from_response(data)
        self._save()

    def logout(self) -> None:
        """End the session at the API and forget the tokens. The library is remembered."""
        session = self._loaded()
        if session.tokens is None:
            return
        try:
            self._call(
                None,
                "POST",
                "/v1/auth/logout",
                json={"accessToken": session.tokens.access_token},
            )
        finally:
            session.tokens = None
            self._save()

    def _token_request(self, template: str, form: dict[str, str]) -> dict[str, Any]:
        session = self._loaded()
        response = self._send(
            "POST",
            template,
            template,
            session,
            None,
            data=form,
            auth=httpx.BasicAuth(CLIENT_ID, CLIENT_SECRET),
        )
        return _base.payload(response)

    def _fresh_tokens(self) -> Tokens | None:
        session = self._loaded()
        if session.tokens is not None and session.tokens.expires_soon:
            with self._refresh_lock:
                if session.tokens is not None and session.tokens.expires_soon:
                    self.refresh()
        return session.tokens

    # ------------------------------------------------------------------- global

    def config(self) -> models.GlobalConfig:
        return self._get(models.GlobalConfig, "/v1/config")

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        """Call any endpoint and get its JSON (without the `data` envelope), for endpoints
        this library doesn't cover yet. `{agencyId}` in `path` is filled in."""
        return self._call(Any, method.upper(), path, params=params, json=json)

    # ---------------------------------------------------------------- transport

    def _get[T](
        self,
        tp: type[T] | Any,
        template: str,
        path_params: dict[str, str] | None = None,
        *,
        params: dict[str, Any] | None = None,
        library_id: str | None = None,
        auth: bool = False,
    ) -> T:
        return self._call(
            tp, "GET", template, path_params, params=params, library_id=library_id, auth=auth
        )

    def _call(
        self,
        tp: Any,
        method: str,
        template: str,
        path_params: dict[str, str] | None = None,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        library_id: str | None = None,
        auth: bool = False,
    ) -> Any:
        session = self._loaded()
        if auth and session.tokens is None:
            raise NotLoggedInError(f"{method} {template} needs a logged-in user")
        path_values = dict(path_params or {})
        if "{agencyId}" in template:
            # The user's account lives at the library they logged in at.
            default = (session.library_id or self._library_id) if auth else None
            library = library_id or default or self._library_id or session.library_id
            if not library:
                raise NoLibraryError("No library given (see libraries.find())")
            path_values["agencyId"] = library
        path = _base.fill_path(template, path_values)

        tokens = self._fresh_tokens()
        kwargs: dict[str, Any] = {"params": _base.query(params), "json": json}
        response = self._send(method, template, path, session, tokens, **kwargs)
        if response.status_code == 401 and tokens is not None:
            with self._refresh_lock:
                if session.tokens is tokens:  # nobody refreshed while we waited
                    self.refresh()
            response = self._send(method, template, path, session, session.tokens, **kwargs)
        data = _base.payload(response)
        return data if tp is Any else _base.parse(tp, data, method, template)

    def _send(
        self,
        method: str,
        template: str,
        path: str,
        session: Session,
        tokens: Tokens | None,
        **kwargs: Any,
    ) -> httpx.Response:
        headers = _base.headers(method, template, session.installation_id, tokens)
        try:
            return self._http.request(method, f"{self._base_url}{path}", headers=headers, **kwargs)
        except httpx.TransportError as err:
            raise BibloConnectionError(f"The Biblo API could not be reached: {err}") from err
