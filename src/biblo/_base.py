"""What the async and sync clients share: everything that does no I/O."""

from __future__ import annotations

import html
import os
import re
import sys
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, get_args
from urllib.parse import parse_qs, quote, urlencode, urlparse

import httpx
from pydantic import TypeAdapter, ValidationError

from ._app_types import ACCEPTED_APP_TYPES, DEFAULT_APP_TYPE
from ._constants import (
    API_KEY,
    APP_VERSION,
    CLIENT_ID,
    LOGIN_BASE_URL,
    PROJECT_URL,
    REDIRECT_URI,
    SESSION_VERSION,
    TOKEN_RENEWAL_MARGIN_SECONDS,
)
from .exceptions import (
    AppTypeNotAcceptedError,
    InvalidCredentialsError,
    LoginFlowError,
    ResponseValidationError,
    UnknownCatalogueError,
    api_error,
)
from .types import Catalogue

try:
    VERSION = version("biblo")
except PackageNotFoundError:  # running from a source tree without installing
    VERSION = "0.0.0"

# ------------------------------------------------------------------------- session


@dataclass
class Tokens:
    """The API's OAuth tokens. The access token lasts about a month (seen 2026-11-20); the
    client refreshes it before it expires."""

    access_token: str
    refresh_token: str
    expires_at: datetime

    @classmethod
    def from_response(cls, data: dict[str, Any]) -> Tokens:
        return cls(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=datetime.now(UTC) + timedelta(seconds=int(data["expires_in"])),
        )

    @classmethod
    def from_dict(cls, data: Any) -> Tokens | None:
        """Tokens saved by `to_dict`, or None if `data` isn't such a dict."""
        try:
            return cls(
                access_token=data["access_token"],
                refresh_token=data["refresh_token"],
                expires_at=datetime.fromisoformat(data["expires_at"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def to_dict(self) -> dict[str, str]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at.isoformat(),
        }

    @property
    def expires_soon(self) -> bool:
        margin = timedelta(seconds=TOKEN_RENEWAL_MARGIN_SECONDS)
        return datetime.now(UTC) + margin >= self.expires_at

    def __repr__(self) -> str:  # keep tokens out of logs and tracebacks
        return f"Tokens(expires_at={self.expires_at.isoformat()})"


CONFIG_DIR_ENV = "BIBLO_CONFIG_DIR"


def default_session_path() -> Path:
    """The session file `biblo auth login` saves to: `$BIBLO_CONFIG_DIR/session.json`, else
    `session.json` in the platform's config folder for biblo.

    The folder is the one `click.get_app_dir("biblo")` gives (the CLI's), worked out here so
    the library needs neither typer nor click: `%APPDATA%\\biblo` (else `~\\biblo`) on
    Windows, `~/Library/Application Support/biblo` on macOS, and `$XDG_CONFIG_HOME/biblo`
    (else `~/.config/biblo`) elsewhere.
    """
    if override := os.environ.get(CONFIG_DIR_ENV):
        return Path(override) / "session.json"
    if sys.platform.startswith("win"):
        folder = os.environ.get("APPDATA", os.path.expanduser("~"))
    elif sys.platform == "darwin":
        folder = os.path.expanduser("~/Library/Application Support")
    else:
        folder = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return Path(folder, "biblo", "session.json")


def _new_installation_id() -> str:
    return str(uuid.uuid4())


@dataclass
class Session:
    """What a TokenStorage keeps between runs."""

    library_id: str | None = None
    installation_id: str = field(default_factory=_new_installation_id)
    tokens: Tokens | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": SESSION_VERSION,
            "library_id": self.library_id,
            "installation_id": self.installation_id,
            "tokens": self.tokens.to_dict() if self.tokens else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Session | None:
        """A session saved by `to_dict`; None if there is none or it has an old version."""
        if not data or data.get("version") != SESSION_VERSION:
            return None
        installation_id = data.get("installation_id")
        if not isinstance(installation_id, str):
            installation_id = _new_installation_id()
        return cls(
            library_id=data.get("library_id"),
            installation_id=installation_id,
            tokens=Tokens.from_dict(data.get("tokens")),
        )


# ------------------------------------------------------------------------- requests


def app_type(method: str, path: str, wanted: str | None = None) -> str:
    """The `x-app-type` to send (see _app_types.py): `wanted` if given, else "biblo" where the
    endpoint accepts it. `path` may be the template (`/v1/agencies/{agencyId}/branches`) or a
    filled-in path. Raises AppTypeNotAcceptedError if the endpoint refuses `wanted`."""
    accepted = _accepted_app_types(method, path)
    if wanted is None:
        return DEFAULT_APP_TYPE if accepted is None or DEFAULT_APP_TYPE in accepted else accepted[0]
    if accepted is None:
        raise AppTypeNotAcceptedError(
            f"Can't send x-app-type {wanted!r} to {method} {path}: the spec doesn't list it"
        )
    if wanted not in accepted:
        raise AppTypeNotAcceptedError(
            f"{method} {path} doesn't accept x-app-type {wanted!r}, only {', '.join(accepted)}"
        )
    return wanted


CATALOGUE_APP_TYPES: dict[Catalogue, str] = {
    "digital": "biblo",
    "children": "biblogo",
    "all": "mybiblo",
}
"""Each `types.Catalogue` and the `x-app-type` of the app that shows it."""


def catalogue_app_type(catalogue: Catalogue) -> str:
    """The `x-app-type` that asks for `catalogue`. Raises UnknownCatalogueError for a value
    that isn't a `types.Catalogue` (a caller without type checking)."""
    try:
        return CATALOGUE_APP_TYPES[catalogue]
    except KeyError:
        choices = ", ".join(get_args(Catalogue))
        raise UnknownCatalogueError(
            f"Unknown catalogue {catalogue!r}: use one of {choices}"
        ) from None


def _accepted_app_types(method: str, path: str) -> tuple[str, ...] | None:
    if found := ACCEPTED_APP_TYPES.get((method, path)):
        return found
    for pattern, accepted in _endpoint_patterns():
        if pattern.fullmatch(f"{method} {path}"):
            return accepted
    return None


@cache
def _endpoint_patterns() -> list[tuple[re.Pattern[str], tuple[str, ...]]]:
    """Each endpoint's `METHOD /path/{param}` as a regex where `{param}` is one segment,
    those with fewer parameters first, so `.../reservations/convert` beats `.../{id}`."""

    def pattern(method: str, path: str) -> re.Pattern[str]:
        literal_parts = re.split(r"\{\w+\}", f"{method} {path}")
        return re.compile("[^/]+".join(map(re.escape, literal_parts)))

    endpoints = sorted(ACCEPTED_APP_TYPES.items(), key=lambda item: item[0][1].count("{"))
    return [(pattern(*key), accepted) for key, accepted in endpoints]


def api_user_agent(product: str | None = None) -> str:
    """`biblo-python/<version> (+<url>)`, after the caller's own `product` token if given.

    The app headers are copied from Biblo; this is how the API's owners can tell our
    traffic apart from the app's and find the project."""
    ours = f"biblo-python/{VERSION} (+{PROJECT_URL})"
    return f"{product} {ours}" if product else ours


def headers(
    method: str,
    template: str,
    installation_id: str,
    tokens: Tokens | None,
    user_agent: str,
    chosen_app_type: str | None = None,
) -> dict[str, str]:
    """`chosen_app_type` is one `app_type()` already returned; without it, the default."""
    result = {
        "User-Agent": user_agent,
        "x-app": f"Android/{APP_VERSION}",
        "x-app-type": chosen_app_type or app_type(method, template),
        "x-installation-id": installation_id,
        "x-api-key": API_KEY,
    }
    if tokens is not None:
        result["Authorization"] = f"Bearer {tokens.access_token}"
    return result


def fill_path(template: str, params: dict[str, str]) -> str:
    """`/v1/agencies/{agencyId}` with agencyId="763000" -> `/v1/agencies/763000`."""
    return re.sub(r"\{(\w+)\}", lambda m: quote(str(params[m.group(1)]), safe=":"), template)


def query(params: dict[str, Any] | None) -> dict[str, Any]:
    """Drop unset parameters; lists become repeated keys (`ids=a&ids=b`), as the app sends."""
    return {k: v for k, v in (params or {}).items() if v is not None and v != []}


def ids(value: str | Iterable[str]) -> list[str]:
    """One id or several, as a list: `"a"` -> `["a"]`."""
    return [value] if isinstance(value, str) else list(value)


def transport_reason(err: httpx.TransportError) -> str:
    """`ConnectError: <text>`, or just `ReadTimeout` for the errors whose text is empty."""
    name = type(err).__name__
    return f"{name}: {err}" if str(err) else name


def payload(response: httpx.Response) -> Any:
    """The JSON body without its `{"data": ...}` envelope; raises APIError for errors."""
    if response.is_error:
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text
        raise api_error(response.status_code, body, response.request.method, str(response.url))
    if not response.content:
        return None
    body = response.json()
    if isinstance(body, dict) and "data" in body and set(body) <= {"data", "isExternal"}:
        return body["data"]
    return body


@cache
def _adapter(tp: Any) -> TypeAdapter[Any]:
    return TypeAdapter(tp)


def parse[T](tp: type[T] | Any, data: Any, method: str, path: str) -> T:
    try:
        return _adapter(tp).validate_python(data)
    except ValidationError as err:
        raise ResponseValidationError(method, path, str(err)) from err


# ---------------------------------------------------------------------------- login


def authorize_url(**extra_params: str) -> str:
    """The login.bib.dk page the app opens. Use it for logins that need a browser (MitID)."""
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "force_login": "1",  # the app always sends this
        **extra_params,
    }
    return f"{LOGIN_BASE_URL}/oauth/authorize?{urlencode(params)}"


def code_from_redirect(url_or_code: str) -> str:
    """The OAuth code from a bare code or from the `biblo://?code=...` URL."""
    text = url_or_code.strip()
    if "=" not in text:
        return text
    found = parse_qs(urlparse(text).query)
    if "error" in found:
        raise InvalidCredentialsError(f"login.bib.dk refused the login: {found}")
    if codes := found.get("code"):
        return codes[0]
    raise LoginFlowError(f"No code in the redirect URL: {text}")


def login_form_action(page: str) -> str | None:
    """The action of login.bib.dk's library-card form, or None if the page has none."""
    form = re.search(r'<form[^>]*id="borchk-login-form"[^>]*>', page)
    if form is None:
        return None
    action = re.search(r'action="([^"]+)"', form.group(0))
    return html.unescape(action.group(1)) if action else None


def login_page_error(page: str) -> str | None:
    """The message login.bib.dk shows after a refused login, if one can be found."""
    pattern = r'<[^>]+(?:class|id)="[^"]*(?:error|alert)[^"]*"[^>]*>(.*?)</'
    for raw in re.findall(pattern, page, re.S | re.I):
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", raw)).split())
        if text:
            return text
    return None
