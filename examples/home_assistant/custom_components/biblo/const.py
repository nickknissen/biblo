"""Constants for the Biblo integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "biblo"

CONF_LIBRARY_ID: Final = "library_id"
CONF_USER_ID: Final = "user_id"
CONF_PINCODE: Final = "pincode"

# Loans and reservations change a few times a week; there's no need to ask more often.
UPDATE_INTERVAL: Final = timedelta(minutes=30)

# Sent before the library's own `biblo-python/<version>`, so the API's owners can tell
# Home Assistant's traffic apart.
USER_AGENT: Final = "home-assistant-biblo"

STATE_READY_FOR_PICKUP: Final = "READY_FOR_PICKUP"
