"""
Biblo integration for Home Assistant: your public library account as sensors.

Loans and when the next one is due, reservations and which are ready for pickup, digital
loans left this period, and fees. Read-only: nothing is borrowed, renewed or reserved.
"""

from __future__ import annotations

from biblo import AsyncBiblo, AsyncMemoryTokenStorage, BibloError, InvalidCredentialsError
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.httpx_client import create_async_httpx_client

from .const import CONF_LIBRARY_ID, CONF_PINCODE, CONF_USER_ID, USER_AGENT
from .coordinator import BibloConfigEntry, BibloCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: BibloConfigEntry) -> bool:
    """Log in and start polling the account."""
    # A client of its own: logging in at login.bib.dk uses cookies, which mustn't mix with
    # Home Assistant's shared client. Closed in async_unload_entry.
    http = create_async_httpx_client(hass, auto_cleanup=False)
    # Tokens live in memory: after a restart the integration logs in again.
    client = AsyncBiblo(
        entry.data[CONF_LIBRARY_ID],
        storage=AsyncMemoryTokenStorage(),
        http_client=http,
        user_agent=USER_AGENT,
    )
    try:
        await client.login(
            entry.data[CONF_USER_ID], entry.data[CONF_PINCODE], entry.data[CONF_LIBRARY_ID]
        )
    except InvalidCredentialsError as err:
        await http.aclose()
        raise ConfigEntryAuthFailed("The library refused the card number/CPR or pincode") from err
    except BibloError as err:
        await http.aclose()
        raise ConfigEntryNotReady(f"Logging in to Biblo failed: {err}") from err

    coordinator = BibloCoordinator(hass, entry, client, http)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await coordinator.async_close()
        raise
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BibloConfigEntry) -> bool:
    """Stop polling and close the client."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_close()
    return unloaded
