"""What the async and sync clients share: everything that does no I/O."""

from __future__ import annotations

import html
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import cache
from importlib.metadata import PackageNotFoundError, version
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse

import httpx
from pydantic import TypeAdapter, ValidationError

from ._app_types import APP_TYPE_OVERRIDES, DEFAULT_APP_TYPE
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
    InvalidCredentialsError,
    LoginFlowError,
    ResponseValidationError,
    api_error,
)

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

    @property
    def expires_soon(self) -> bool:
        margin = timedelta(seconds=TOKEN_RENEWAL_MARGIN_SECONDS)
        return datetime.now(UTC) + margin >= self.expires_at

    def __repr__(self) -> str:  # keep tokens out of logs and tracebacks
        return f"Tokens(expires_at={self.expires_at.isoformat()})"


@dataclass
class Session:
    """What a TokenStorage keeps between runs."""

    library_id: str | None = None
    installation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tokens: Tokens | None = None

    def to_dict(self) -> dict[str, Any]:
        tokens = None
        if self.tokens is not None:
            tokens = {
                "access_token": self.tokens.access_token,
                "refresh_token": self.tokens.refresh_token,
                "expires_at": self.tokens.expires_at.isoformat(),
            }
        return {
            "version": SESSION_VERSION,
            "library_id": self.library_id,
            "installation_id": self.installation_id,
            "tokens": tokens,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Session | None:
        if not data or data.get("version") != SESSION_VERSION:
            return None
        session = cls(library_id=data.get("library_id"))
        if isinstance(data.get("installation_id"), str):
            session.installation_id = data["installation_id"]
        tokens = data.get("tokens")
        if isinstance(tokens, dict):
            try:
                session.tokens = Tokens(
                    access_token=tokens["access_token"],
                    refresh_token=tokens["refresh_token"],
                    expires_at=datetime.fromisoformat(tokens["expires_at"]),
                )
            except (KeyError, TypeError, ValueError):
                session.tokens = None
        return session


# ------------------------------------------------------------------------- requests


def app_type(method: str, path: str) -> str:
    """The `x-app-type` an endpoint accepts (see _app_types.py). `path` may be the template
    (`/v1/agencies/{agencyId}/branches`) or a filled-in path."""
    if found := APP_TYPE_OVERRIDES.get((method, path)):
        return found
    for pattern, override in _override_patterns():
        if pattern.fullmatch(f"{method} {path}"):
            return override
    return DEFAULT_APP_TYPE


@cache
def _override_patterns() -> list[tuple[re.Pattern[str], str]]:
    return [
        (re.compile(re.escape(f"{method} ") + re.sub(r"\\\{\w+\\\}", "[^/]+", re.escape(path))), v)
        for (method, path), v in APP_TYPE_OVERRIDES.items()
    ]


def api_user_agent(product: str | None = None) -> str:
    """`biblo-python/<version> (+<url>)`, after the caller's own `product` token if given.

    The app headers are copied from Biblo; this is how the API's owners can tell our
    traffic apart from the app's and find the project."""
    ours = f"biblo-python/{VERSION} (+{PROJECT_URL})"
    return f"{product} {ours}" if product else ours


def headers(
    method: str, template: str, installation_id: str, tokens: Tokens | None, user_agent: str
) -> dict[str, str]:
    result = {
        "User-Agent": user_agent,
        "x-app": f"Android/{APP_VERSION}",
        "x-app-type": app_type(method, template),
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


def ids(value: str | Any) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


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
