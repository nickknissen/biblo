"""Unofficial client for Biblo, the Danish public libraries' app for e-books, audiobooks and
podcasts (Det Digitale Folkebibliotek).

    from biblo import Biblo

    with Biblo("763000") as biblo:                       # Vejle Bibliotekerne
        for work in biblo.catalog.search("harry potter").works:
            print(work.title)

`AsyncBiblo` is the same client for asyncio.
"""

from . import models, types
from ._async import AsyncBiblo, AsyncFileTokenStorage, AsyncMemoryTokenStorage, AsyncTokenStorage
from ._base import VERSION as __version__
from ._base import Tokens, authorize_url, code_from_redirect
from ._sync import Biblo, FileTokenStorage, MemoryTokenStorage, TokenStorage
from .exceptions import (
    APIError,
    AppTypeNotAcceptedError,
    AuthenticationError,
    BadRequestError,
    BibloConnectionError,
    BibloError,
    ConflictError,
    InvalidCredentialsError,
    LoginError,
    LoginFlowError,
    NoLibraryError,
    NotFoundError,
    NotLoggedInError,
    PermissionDeniedError,
    PickupBranchRequiredError,
    RateLimitError,
    ResponseValidationError,
    ServerError,
)
from .types import Catalogue

__all__ = [
    "APIError",
    "AppTypeNotAcceptedError",
    "AsyncBiblo",
    "AsyncFileTokenStorage",
    "AsyncMemoryTokenStorage",
    "AsyncTokenStorage",
    "AuthenticationError",
    "BadRequestError",
    "Biblo",
    "BibloConnectionError",
    "BibloError",
    "Catalogue",
    "ConflictError",
    "FileTokenStorage",
    "InvalidCredentialsError",
    "LoginError",
    "LoginFlowError",
    "MemoryTokenStorage",
    "NoLibraryError",
    "NotFoundError",
    "NotLoggedInError",
    "PermissionDeniedError",
    "PickupBranchRequiredError",
    "RateLimitError",
    "ResponseValidationError",
    "ServerError",
    "TokenStorage",
    "Tokens",
    "__version__",
    "authorize_url",
    "code_from_redirect",
    "models",
    "types",
]
