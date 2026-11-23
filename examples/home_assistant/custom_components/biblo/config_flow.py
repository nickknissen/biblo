"""Config flow for Biblo: pick a library and log in with card number/CPR and pincode."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from typing import Any

import httpx
import probatio
from biblo import AsyncBiblo, AsyncMemoryTokenStorage, BibloError, InvalidCredentialsError
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.httpx_client import create_async_httpx_client
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import CONF_LIBRARY_ID, CONF_PINCODE, CONF_USER_ID, DOMAIN, USER_AGENT

_LOGGER = logging.getLogger(__name__)

# A CPR number is as sensitive as the pincode, so neither is shown while typing.
SECRET = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _unique_id(library_id: str, user_id: str) -> str:
    """One entry per login, without keeping the card number or CPR in the id."""
    return hashlib.sha256(f"{library_id}:{user_id.strip()}".encode()).hexdigest()


class BibloConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a Biblo account."""

    VERSION = 1

    def __init__(self) -> None:
        self._libraries: dict[str, str] = {}

    def _client(self) -> tuple[AsyncBiblo, httpx.AsyncClient]:
        """A throwaway client with its own cookie jar; the caller closes both."""
        http = create_async_httpx_client(self.hass, auto_cleanup=False)
        return AsyncBiblo(
            storage=AsyncMemoryTokenStorage(), http_client=http, user_agent=USER_AGENT
        ), http

    async def _login(self, library_id: str, user_id: str, pincode: str) -> str | None:
        """Try the login; return an error key for the form, or None."""
        client, http = self._client()
        try:
            await client.login(user_id, pincode, library_id)
        except InvalidCredentialsError:
            return "invalid_auth"
        except BibloError as err:
            _LOGGER.debug("Logging in failed: %s", err)
            return "cannot_connect"
        finally:
            await client.aclose()
            await http.aclose()
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the library and the login."""
        if not self._libraries:
            client, http = self._client()
            try:
                agencies = await client.libraries.list()
            except BibloError as err:
                _LOGGER.debug("Listing libraries failed: %s", err)
                return self.async_abort(reason="cannot_connect")
            finally:
                await client.aclose()
                await http.aclose()
            self._libraries = {a.id: a.name for a in sorted(agencies, key=lambda a: a.name)}

        errors: dict[str, str] = {}
        if user_input is not None:
            library_id = user_input[CONF_LIBRARY_ID]
            await self.async_set_unique_id(_unique_id(library_id, user_input[CONF_USER_ID]))
            self._abort_if_unique_id_configured()
            error = await self._login(
                library_id, user_input[CONF_USER_ID], user_input[CONF_PINCODE]
            )
            if error is None:
                return self.async_create_entry(
                    title=self._libraries.get(library_id, library_id), data=user_input
                )
            errors["base"] = error

        schema = probatio.Schema(
            {
                probatio.Required(CONF_LIBRARY_ID): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=lib_id, label=name)
                            for lib_id, name in self._libraries.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                probatio.Required(CONF_USER_ID): SECRET,
                probatio.Required(CONF_PINCODE): SECRET,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The library refused the stored login, e.g. after a pincode change."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the new pincode."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await self._login(
                entry.data[CONF_LIBRARY_ID], entry.data[CONF_USER_ID], user_input[CONF_PINCODE]
            )
            if error is None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PINCODE: user_input[CONF_PINCODE]}
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=probatio.Schema({probatio.Required(CONF_PINCODE): SECRET}),
            description_placeholders={"library": entry.title},
            errors=errors,
        )
