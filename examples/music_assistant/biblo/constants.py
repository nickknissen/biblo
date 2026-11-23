"""Constants for the Biblo provider."""

from __future__ import annotations

CONF_LIBRARY_ID = "library_id"
CONF_USER_ID = "user_id"
CONF_PINCODE = "pincode"

# The `material_types_specific` facet values Biblo's search filters on.
FACET_AUDIOBOOK = "lydbog (online)"
FACET_PODCAST = "podcast"

# `MaterialType.category` of the records this provider serves.
CATEGORY_AUDIOBOOK = "AUDIO_BOOKS_ONLINE"
CATEGORY_PODCAST = "PODCASTS"

# A record contributor's role for the narrator.
ROLE_NARRATOR = "indlæser"

# A podcast episode outside any series becomes a one-episode podcast with this id prefix.
SINGLE_EPISODE_PREFIX = "work:"

# Public page for a work, which also links on to the library's own site and the app.
WORK_PAGE_URL = "https://bibliotek.dk/work/{work_id}"

PREVIEW_NOTE = "Music Assistant plays the publisher's free preview. Borrow the full title in Biblo."
