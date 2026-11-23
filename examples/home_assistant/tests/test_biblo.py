"""The Biblo integration end to end, against the fake API in the repo's tests/fake.py."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.biblo.config_flow import _unique_id
from custom_components.biblo.const import CONF_LIBRARY_ID, CONF_PINCODE, CONF_USER_ID, DOMAIN

from .conftest import LIBRARY, PIN, USER, fake_module

LOGIN = {CONF_LIBRARY_ID: LIBRARY, CONF_USER_ID: USER, CONF_PINCODE: PIN}


def _entry(**data: str) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Vejle Bibliotekerne",
        unique_id=_unique_id(LIBRARY, USER),
        data={**LOGIN, **data},
    )


async def _set_up(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _state(hass: HomeAssistant, key: str) -> Any:
    unique_id = f"{_unique_id(LIBRARY, USER)}_{key}"
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, unique_id)
    assert entity_id is not None, key
    return hass.states.get(entity_id)


async def test_config_flow_creates_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], LOGIN)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == LOGIN
    # The unique id doesn't contain the card number/CPR.
    assert USER not in result["result"].unique_id


async def test_config_flow_wrong_pincode(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**LOGIN, CONF_PINCODE: "0000"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    # Correcting it in the same form works.
    result = await hass.config_entries.flow.async_configure(result["flow_id"], LOGIN)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_config_flow_same_login_twice(hass: HomeAssistant) -> None:
    _entry().add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], LOGIN)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_sensors(hass: HomeAssistant) -> None:
    entry = _entry()
    await _set_up(hass, entry)
    assert entry.state is ConfigEntryState.LOADED

    # The fake account: one digital and one physical loan and reservation, none overdue
    # or ready, 4 of 5 e-book loans left, no fees.
    loans = _state(hass, "loans")
    assert loans.state == "2"
    assert [loan["title"] for loan in loans.attributes["loans"]] == [
        "Harry Potter og De Vises Sten",
        "En trykt bog",
    ]
    assert _state(hass, "next_due").state == "2026-12-10T10:00:00+00:00"
    assert _state(hass, "overdue").state == "0"
    reservations = _state(hass, "reservations")
    assert reservations.state == "2"
    physical = reservations.attributes["reservations"][1]
    assert (physical["queue_position"], physical["pickup_branch"]) == (3, "DK-763004")
    assert _state(hass, "ready_for_pickup").state == "0"
    ebooks = _state(hass, "ebook_loans_left")
    assert (ebooks.state, ebooks.attributes["per_period"]) == ("4", 5)
    assert _state(hass, "audiobook_loans_left").state == "5"
    assert float(_state(hass, "fees").state) == 0


async def test_overdue_and_ready_for_pickup(hass: HomeAssistant, fake: Any) -> None:
    fake.loans = [{**fake_module.LOAN, "dueDate": "2026-01-05T10:00:00.000Z"}]
    fake.physical_reservations = [
        {
            **fake_module.PHYSICAL_RESERVATION,
            "state": "READY_FOR_PICKUP",
            "claim": {"id": "c-1", "availableUntil": "2027-01-10T10:00:00.000Z"},
        }
    ]
    await _set_up(hass, _entry())

    overdue = _state(hass, "overdue")
    assert overdue.state == "1"
    assert overdue.attributes["loans"][0]["title"] == "Harry Potter og De Vises Sten"
    ready = _state(hass, "ready_for_pickup")
    assert ready.state == "1"
    assert ready.attributes["reservations"][0]["pickup_by"] == "2027-01-10T10:00:00+00:00"


async def test_wrong_stored_pincode_asks_to_log_in_again(hass: HomeAssistant) -> None:
    entry = _entry(**{CONF_PINCODE: "0000"})
    await _set_up(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    [flow] = hass.config_entries.flow.async_progress()
    assert flow["context"]["source"] == "reauth"

    result = await hass.config_entries.flow.async_configure(flow["flow_id"], {CONF_PINCODE: PIN})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert entry.data[CONF_PINCODE] == PIN
    assert entry.state is ConfigEntryState.LOADED


async def test_api_down_marks_sensors_unavailable(hass: HomeAssistant, fake: Any) -> None:
    entry = _entry()
    await _set_up(hass, entry)
    assert _state(hass, "loans").state == "2"

    base = f"/v1/agencies/{LIBRARY}/account"
    fake.overrides[("GET", f"{base}/loans")] = lambda _: fake_module._error(
        503, 5000, "UNAVAILABLE", "down"
    )
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert _state(hass, "loans").state == "unavailable"

    # Back once the API answers again.
    del fake.overrides[("GET", f"{base}/loans")]
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert _state(hass, "loans").state == "2"


async def test_unload(hass: HomeAssistant) -> None:
    entry = _entry()
    await _set_up(hass, entry)
    http = entry.runtime_data.http
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert http.is_closed
