"""Errors raised by biblo. Catch `BibloError` for all of them, and `LoginRequiredError` for
the ones the user fixes by logging in (again): the classes marked * below.

BibloError
├── LoginRequiredError          the user has to log in (again); a base of each *
├── LoginError                  logging in did not work
│   ├── InvalidCredentialsError *   wrong card number/CPR or pincode
│   └── LoginFlowError              login.bib.dk answered unexpectedly
├── NoLibraryError              no library given (also a ValueError)
├── UnknownCatalogueError       catalogue= not a types.Catalogue (also a ValueError)
├── UnknownMaterialError        material= not a types.Material (also a ValueError)
├── NotLoggedInError *          an account call without a (still valid) session
├── BibloConnectionError        the API or login.bib.dk could not be reached
├── ResponseValidationError     the API sent data that doesn't match its spec
└── APIError                    the API answered with an error status
    ├── BadRequestError             400, e.g. a validation error
    │   └── PickupBranchRequiredError   a physical record reserved without a branch
    ├── AuthenticationError *       401, still after refreshing the session
    ├── PermissionDeniedError       403
    ├── NotFoundError               404
    ├── ConflictError               409
    ├── RateLimitError              429
    └── ServerError                 5xx
"""

from __future__ import annotations

from typing import Any

from ._constants import PROJECT_URL


class BibloError(Exception):
    """Base class for every error raised by biblo."""


class LoginRequiredError(BibloError):
    """The user has to log in (again): the login was refused, there is no session or it
    can't be refreshed, or the API still refuses its token. Catch this to ask for a login.

    `LoginFlowError` is not one: logging in again doesn't fix a changed login page."""


class LoginError(BibloError):
    """Logging in through login.bib.dk did not work."""


class InvalidCredentialsError(LoginError, LoginRequiredError):
    """login.bib.dk refused the card number/CPR or pincode."""


class LoginFlowError(LoginError):
    """login.bib.dk answered in a way biblo does not understand, e.g. after a redesign."""


class NoLibraryError(BibloError, ValueError):
    """A call that belongs to a library, with no library given and none logged in at."""


class AppTypeNotAcceptedError(BibloError, ValueError):
    """A call asked for an `x-app-type` the endpoint doesn't accept; nothing was sent.
    Internal: every endpoint that takes `catalogue=` accepts all three apps."""


class UnknownCatalogueError(BibloError, ValueError):
    """`catalogue=` was not one of `types.Catalogue`; nothing was sent."""


class UnknownMaterialError(BibloError, ValueError):
    """`material=` was not one of `types.Material`; nothing was sent."""


class NotLoggedInError(LoginRequiredError):
    """The call needs a logged-in user, and there is no session or it can't be refreshed."""


class BibloConnectionError(BibloError):
    """The Biblo API or login.bib.dk could not be reached (network error or timeout)."""


class ResponseValidationError(BibloError):
    """The API answered with data that doesn't match its own spec, e.g. null where the spec
    promises a value. Please report it, with this message, so the models can be fixed."""

    def __init__(self, method: str, path: str, detail: str) -> None:
        self.method = method
        self.path = path
        self.detail = detail
        super().__init__(
            f"{method} {path} answered with data biblo can't read. Please report this at "
            f"{PROJECT_URL}/issues:\n{detail}"
        )


class APIError(BibloError):
    """The Biblo API answered with an error status.

    The API's error bodies look like `{"id": 4205, "type": "FORBIDDEN", "message": "..."}`;
    `code` and `type` carry `id` and `type`.
    """

    def __init__(self, status_code: int, body: Any, method: str, url: str) -> None:
        self.status_code = status_code
        self.body = body
        self.method = method
        self.url = url
        detail = body if isinstance(body, dict) else {}
        self.code: int | None = detail.get("id")
        self.type: str | None = detail.get("type")
        self.message: str = str(detail.get("message") or body or f"HTTP {status_code}")
        super().__init__(f"{self.message} ({method} {url} -> {status_code})")


class BadRequestError(APIError):
    """400: the API refused the request, e.g. a missing or malformed parameter."""


class PickupBranchRequiredError(BadRequestError):
    """400 "No material types found for records" from a reservation without a pickup
    branch: what the API answers when the record is physical. `message` is the API's."""

    def __init__(self, status_code: int, body: Any, method: str, url: str) -> None:
        super().__init__(status_code, body, method, url)
        self.args = (
            f"{self.args[0]}\nThe record may be a physical item (a printed book, a CD, ...): "
            "reserving one needs a pickup branch (pickup_branch_id).",
        )


class AuthenticationError(APIError, LoginRequiredError):
    """401: missing, expired or revoked access token, still refused after a refresh."""


class PermissionDeniedError(APIError):
    """403: not allowed, e.g. an endpoint another of DDF's apps owns."""


class NotFoundError(APIError):
    """404: no such library, work, record, loan, ..."""


class ConflictError(APIError):
    """409: e.g. the record is already borrowed or reserved."""


class RateLimitError(APIError):
    """429: too many requests; wait and try again."""


class ServerError(APIError):
    """5xx: the API or a system behind it failed; usually temporary."""


_BY_STATUS: dict[int, type[APIError]] = {
    400: BadRequestError,
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    429: RateLimitError,
}


def api_error(status_code: int, body: Any, method: str, url: str) -> APIError:
    cls = _BY_STATUS.get(status_code) or (ServerError if status_code >= 500 else APIError)
    return cls(status_code, body, method, url)
