"""Setup flow for the Biblo provider: pick a library, optionally log in."""

from __future__ import annotations

from typing import TYPE_CHECKING

from biblo import AsyncBiblo, BibloError
from music_assistant_models.config_entries import ConfigEntry, ConfigValueOption
from music_assistant_models.enums import ConfigEntryType

from music_assistant.models.setup_flow import AbortFlow, SetupFlowError

from .constants import CONF_LIBRARY_ID, CONF_PINCODE, CONF_USER_ID

if TYPE_CHECKING:
    from music_assistant.models.setup_flow import SetupSession


async def _library_options() -> list[ConfigValueOption]:
    async with AsyncBiblo() as biblo:
        libraries = await biblo.libraries.list()
    return [ConfigValueOption(lib.name, lib.id) for lib in sorted(libraries, key=lambda x: x.name)]


async def run_setup(session: SetupSession) -> None:
    """Ask for the library and, optionally, a login; then create the provider."""
    try:
        libraries = await _library_options()
    except BibloError as err:
        raise AbortFlow(f"Could not list the libraries from Biblo: {err}") from err

    setup_data = dict(session.context.setup_data)
    errors: dict[str, str | SetupFlowError] | None = None
    while True:
        entries = [
            ConfigEntry(
                key=CONF_LIBRARY_ID,
                type=ConfigEntryType.STRING,
                required=True,
                options=libraries,
                value=setup_data.get(CONF_LIBRARY_ID),
            ),
            # A CPR number is as sensitive as the pincode, so both are stored encrypted.
            ConfigEntry(
                key=CONF_USER_ID,
                type=ConfigEntryType.SECURE_STRING,
                required=False,
                value=setup_data.get(CONF_USER_ID),
            ),
            ConfigEntry(
                key=CONF_PINCODE,
                type=ConfigEntryType.SECURE_STRING,
                required=False,
                value=setup_data.get(CONF_PINCODE),
            ),
        ]
        submitted = await session.form(entries, step_id="user", errors=errors, last_step=True)
        setup_data.update(submitted)
        if bool(setup_data.get(CONF_USER_ID)) != bool(setup_data.get(CONF_PINCODE)):
            errors = {"base": SetupFlowError("Enter both the card number/CPR and the pincode.")}
            continue
        try:
            # Loads the provider, whose handle_async_init logs in when given credentials.
            await session.finish(setup_data)
            return
        except SetupFlowError as err:
            errors = {"base": err}
