"""Values from the Biblo Android app (dk.detdigitalefolkebibliotek.biblo 1.3.1).

Every copy of the app embeds them (`biblioApplication.onCreate` in the decompiled APK). If the
API starts refusing requests after an app update, check these first.
"""

API_BASE_URL = "https://api.detdigitalefolkebibliotek.dk"
SPEC_URL = f"{API_BASE_URL}/_openapi.json"

API_KEY = "wnFOwN6DkmQazm_jiahemlIVgWuQ-fgtNSfBRNdun6w"
"""Sent as `x-api-key` on every request."""

APP_VERSION = "1.3.1"
"""Sent as `x-app: Android/<version>`. The API rejects requests without it."""

LOGIN_BASE_URL = "https://login.bib.dk"
"""Adgangsplatformen, the Danish libraries' shared OAuth login."""

CLIENT_ID = "204936c7-d008-4d90-884b-0134a9918c3d"
CLIENT_SECRET = "d3fc019530c7d52b05ec0223ab3a67077fcecc57117d205e9005fba93525abf7"
"""HTTP Basic auth for the API's /v1/auth/login and /v1/auth/token-refresh."""

REDIRECT_URI = "biblo://"

DEFAULT_TIMEOUT_SECONDS = 30.0
TOKEN_RENEWAL_MARGIN_SECONDS = 60
"""Refresh the access token this long before it expires."""
LOGIN_MAX_REDIRECTS = 15
SESSION_VERSION = 1
PROJECT_URL = "https://github.com/nickknissen/biblo"
"""Not from the app: this library's home, named in its User-Agent and error messages."""

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Mobile Safari/537.36"
)
