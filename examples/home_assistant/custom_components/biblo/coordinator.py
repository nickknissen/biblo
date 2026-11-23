"""Polls a Biblo account's loans, reservations, quotas and fees."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx
from biblo import (
    AsyncBiblo,
    AuthenticationError,
    BibloError,
    InvalidCredentialsError,
    NotLoggedInError,
)
from biblo import models as bm
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)

type BibloConfigEntry = ConfigEntry[BibloCoordinator]


@dataclass(frozen=True)
class BibloData:
    """One snapshot of the account."""

    loans: list[bm.Loan]
    reservations: list[bm.ReservationItem]
    quotas: bm.QuotaSummary | None
    fees: list[bm.Fee]


class BibloCoordinator(DataUpdateCoordinator[BibloData]):
    """Fetch the account every UPDATE_INTERVAL; the sensors read from the snapshot."""

    config_entry: BibloConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BibloConfigEntry,
        client: AsyncBiblo,
        http: httpx.AsyncClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        # The client's HTTP client, which AsyncBiblo leaves open because it was passed in.
        self.http = http

    async def async_close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()
        await self.http.aclose()

    async def _async_update_data(self) -> BibloData:
        try:
            loans, reservations, fees = await asyncio.gather(
                self.client.loans.list(),
                self.client.reservations.list(),
                self.client.account.fees(),
            )
            quotas = await self._quotas()
        except (InvalidCredentialsError, NotLoggedInError, AuthenticationError) as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except BibloError as err:
            raise UpdateFailed(f"Biblo request failed: {err}") from err
        return BibloData(loans=loans, reservations=reservations, quotas=quotas, fees=fees)

    async def _quotas(self) -> bm.QuotaSummary | None:
        """Digital quotas, or None where the library doesn't lend e-books and audiobooks."""
        try:
            return await self.client.account.quotas()
        except (InvalidCredentialsError, NotLoggedInError, AuthenticationError):
            raise
        except BibloError as err:
            _LOGGER.debug("No digital quotas: %s", err)
            return None
