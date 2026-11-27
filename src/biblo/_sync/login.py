# Generated from src/biblo/_async by scripts/generate.py. Do not edit.
"""The browser's part of logging in, done over plain HTTP.

The app opens login.bib.dk's /oauth/authorize in a browser tab. The user picks their library
and types their card number or CPR and pincode into an ordinary HTML form, and login.bib.dk
redirects to `biblo://?code=...`. This module submits that form and returns the code.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlsplit

import httpx

from .._base import (
    authorize_url,
    blocked_login_error,
    code_from_redirect,
    login_form_action,
    returned_login_form_error,
)
from .._constants import BROWSER_USER_AGENT, LOGIN_BASE_URL, LOGIN_MAX_REDIRECTS, REDIRECT_URI
from ..exceptions import LoginFlowError


def password_login_code(http: httpx.Client, user_id: str, pincode: str, library_id: str) -> str:
    """Log in with card number or CPR + pincode at a library; return the OAuth code."""
    try:
        page = _follow(http, _get(http, authorize_url()))
        if isinstance(page, str):  # login.bib.dk remembered a session despite force_login
            return code_from_redirect(page)
        action = login_form_action(page.text)
        if action is None:
            raise LoginFlowError(
                f"No library login form on {page.url}; has login.bib.dk changed its page?"
            )
        response = http.post(
            urljoin(str(page.url), action),
            data={
                "agency": library_id,
                "loginBibDkUserId": user_id.strip(),
                "pincode": pincode.strip(),
            },
            headers={"User-Agent": BROWSER_USER_AGENT},
            follow_redirects=False,
        )
        result = _follow(http, response)
    finally:
        _forget_login_cookies(http)

    if isinstance(result, str):
        return code_from_redirect(result)
    if result.status_code == 429:  # blocked after too many failed logins
        raise blocked_login_error(result.text)
    if login_form_action(result.text) is not None:
        raise returned_login_form_error(str(result.url), result.text)
    where = result.url.copy_with(query=None)  # login.bib.dk's URLs carry a session token
    raise LoginFlowError(f"login.bib.dk did not send the browser back to the app ({where})")


def _get(http: httpx.Client, url: str) -> httpx.Response:
    return http.get(url, headers={"User-Agent": BROWSER_USER_AGENT}, follow_redirects=False)


def _follow(http: httpx.Client, response: httpx.Response) -> httpx.Response | str:
    """Follow redirects to a page, or return the biblo:// URL when the flow reaches it."""
    for _ in range(LOGIN_MAX_REDIRECTS):
        if not response.has_redirect_location:
            return response
        location = urljoin(str(response.url), response.headers["location"])
        if location.startswith(REDIRECT_URI):
            return location
        response = _get(http, location)
    raise LoginFlowError("Too many redirects on login.bib.dk")


def _forget_login_cookies(http: httpx.Client) -> None:
    """Don't leave login.bib.dk's cookies in an httpx client the caller may share."""
    domain = urlsplit(LOGIN_BASE_URL).hostname
    for cookie in list(http.cookies.jar):
        if cookie.domain.lstrip(".") == domain:
            http.cookies.jar.clear(cookie.domain, cookie.path, cookie.name)
