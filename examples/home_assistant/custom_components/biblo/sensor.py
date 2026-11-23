"""Sensors for a Biblo account: loans, reservations, digital loans left and fees."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from biblo import models as bm
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, STATE_READY_FOR_PICKUP
from .coordinator import BibloConfigEntry, BibloCoordinator, BibloData

# Per sensor, at most this many items are listed in its attributes.
MAX_LISTED = 25


@dataclass(frozen=True, kw_only=True)
class BibloSensorDescription(SensorEntityDescription):
    """A sensor's value, and its attributes, from one snapshot of the account."""

    value_fn: Callable[[BibloData], float | int | datetime | None]
    attrs_fn: Callable[[BibloData], dict[str, Any]] | None = None


def _sorted_loans(data: BibloData) -> list[bm.Loan]:
    return sorted(data.loans, key=lambda loan: loan.due_date)


def _overdue(data: BibloData) -> list[bm.Loan]:
    now = dt_util.utcnow()
    return [loan for loan in _sorted_loans(data) if loan.due_date < now]


def _ready(data: BibloData) -> list[bm.ReservationItem]:
    return [r for r in data.reservations if r.state == STATE_READY_FOR_PICKUP]


def _loan_attrs(loans: list[bm.Loan]) -> dict[str, Any]:
    return {
        "loans": [
            {
                "title": loan.record.title,
                "author": loan.record.author,
                "type": loan.record.material_type.display,
                "due": loan.due_date.isoformat(),
                "renewable": loan.is_renewable,
            }
            for loan in loans[:MAX_LISTED]
        ]
    }


def _reservation_attrs(reservations: list[bm.ReservationItem]) -> dict[str, Any]:
    return {
        "reservations": [
            {
                "title": r.details.title,
                "author": r.details.author,
                "type": r.details.material_type.display,
                "state": str(r.state).lower(),
                "queue_position": r.physical.number_in_queue if r.physical else None,
                "pickup_branch": r.physical.pickup_branch if r.physical else None,
                "pickup_by": r.claim.available_until.isoformat() if r.claim else None,
            }
            for r in reservations[:MAX_LISTED]
        ]
    }


def _next_due(data: BibloData) -> datetime | None:
    loans = _sorted_loans(data)
    return loans[0].due_date if loans else None


def _next_due_attrs(data: BibloData) -> dict[str, Any]:
    loans = _sorted_loans(data)
    return {"title": loans[0].record.title} if loans else {}


type Pick = Callable[[bm.DigitalTypeConfig], int]


def _quota(pick: Pick) -> Callable[[BibloData], int | None]:
    """Loans of one kind left this period."""

    def value(data: BibloData) -> int | None:
        return pick(data.quotas.remaining_loan_quotas) if data.quotas else None

    return value


def _quota_attrs(pick: Pick) -> Callable[[BibloData], dict[str, Any]]:
    """Loans of one kind per period."""

    def attrs(data: BibloData) -> dict[str, Any]:
        return {"per_period": pick(data.quotas.total_loan_quotas)} if data.quotas else {}

    return attrs


SENSORS: tuple[BibloSensorDescription, ...] = (
    BibloSensorDescription(
        key="loans",
        translation_key="loans",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: len(data.loans),
        attrs_fn=lambda data: _loan_attrs(_sorted_loans(data)),
    ),
    BibloSensorDescription(
        key="next_due",
        translation_key="next_due",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_next_due,
        attrs_fn=_next_due_attrs,
    ),
    BibloSensorDescription(
        key="overdue",
        translation_key="overdue",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: len(_overdue(data)),
        attrs_fn=lambda data: _loan_attrs(_overdue(data)),
    ),
    BibloSensorDescription(
        key="reservations",
        translation_key="reservations",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: len(data.reservations),
        attrs_fn=lambda data: _reservation_attrs(data.reservations),
    ),
    BibloSensorDescription(
        key="ready_for_pickup",
        translation_key="ready_for_pickup",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: len(_ready(data)),
        attrs_fn=lambda data: _reservation_attrs(_ready(data)),
    ),
    BibloSensorDescription(
        key="ebook_loans_left",
        translation_key="ebook_loans_left",
        value_fn=_quota(lambda q: q.ebook),
        attrs_fn=_quota_attrs(lambda q: q.ebook),
    ),
    BibloSensorDescription(
        key="audiobook_loans_left",
        translation_key="audiobook_loans_left",
        value_fn=_quota(lambda q: q.audiobook),
        attrs_fn=_quota_attrs(lambda q: q.audiobook),
    ),
    BibloSensorDescription(
        key="fees",
        translation_key="fees",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="DKK",
        suggested_display_precision=2,
        value_fn=lambda data: sum(fee.amount_in_dkk for fee in data.fees),
        attrs_fn=lambda data: {
            "fees": [
                {
                    "reason": fee.reason,
                    "amount": fee.amount_in_dkk,
                    "due": fee.due_date.isoformat() if fee.due_date else None,
                }
                for fee in data.fees[:MAX_LISTED]
            ]
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BibloConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the account's sensors."""
    coordinator = entry.runtime_data
    async_add_entities(BibloSensor(coordinator, description) for description in SENSORS)


class BibloSensor(CoordinatorEntity[BibloCoordinator], SensorEntity):
    """One figure about the account."""

    _attr_has_entity_name = True
    entity_description: BibloSensorDescription

    def __init__(self, coordinator: BibloCoordinator, description: BibloSensorDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(entry.unique_id))},
            name=entry.title,
            manufacturer="Det Digitale Folkebibliotek",
            model="Biblo",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> float | int | datetime | None:
        """The figure from the latest snapshot."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The items behind the figure."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
