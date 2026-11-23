"""
Biblo provider for Music Assistant: the Danish public libraries' audiobooks and podcasts.

Search the digital catalogue of your library, see your favorites and loans, and play the
publisher's free preview of an audiobook or podcast episode. The full titles are played by
WeDoBooks' player inside the Biblo app, which this provider does not touch: everything here
goes through the documented DDF API via the `biblo` library.

How Biblo maps onto Music Assistant:

- An audiobook is a work with an online audiobook edition; its id is the work id.
- A podcast is a series of podcast works, its id the series id. Every podcast work is one
  episode, with the work id as its id. An episode outside any series becomes a podcast of
  one episode, with the id `work:<work id>`.
- Your library: favorites ("Huskeliste") plus current loans, after logging in. Adding or
  removing an audiobook in Music Assistant adds or removes the favorite.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncGenerator, Iterable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from biblo import (
    AsyncBiblo,
    AsyncMemoryTokenStorage,
    AuthenticationError,
    BibloConnectionError,
    BibloError,
    InvalidCredentialsError,
    NotFoundError,
    NotLoggedInError,
    RateLimitError,
    ServerError,
)
from biblo import models as bm
from music_assistant_models.enums import (
    ContentType,
    ImageType,
    LinkType,
    MediaType,
    ProviderFeature,
    StreamType,
)
from music_assistant_models.errors import (
    LoginFailed,
    MediaNotFoundError,
    ProviderUnavailableError,
    SetupFailedError,
    UnplayableMediaError,
)
from music_assistant_models.media_items import (
    Audiobook,
    AudioFormat,
    ItemMapping,
    MediaItemImage,
    MediaItemLink,
    Podcast,
    PodcastEpisode,
    ProviderMapping,
    SearchResults,
    UniqueList,
)
from music_assistant_models.streamdetails import StreamDetails

from music_assistant.models.music_provider import MusicProvider

from .constants import (
    CATEGORY_AUDIOBOOK,
    CATEGORY_PODCAST,
    CONF_LIBRARY_ID,
    CONF_PINCODE,
    CONF_USER_ID,
    FACET_AUDIOBOOK,
    FACET_PODCAST,
    PREVIEW_NOTE,
    ROLE_NARRATOR,
    SINGLE_EPISODE_PREFIX,
    WORK_PAGE_URL,
)

if TYPE_CHECKING:
    from music_assistant_models.config_entries import ProviderConfig
    from music_assistant_models.media_items import MediaItemType
    from music_assistant_models.provider import ProviderManifest

    from music_assistant.mass import MusicAssistant
    from music_assistant.models import ProviderInstanceType

# Sent before the library's own `biblo-python/<version>`, so the API's owners can tell
# Music Assistant's traffic apart.
USER_AGENT = "music-assistant-biblo"

BASE_FEATURES = {ProviderFeature.SEARCH}
# Only with a login: the library is the account's favorites and loans.
ACCOUNT_FEATURES = {
    ProviderFeature.LIBRARY_AUDIOBOOKS,
    ProviderFeature.LIBRARY_AUDIOBOOKS_EDIT,
    ProviderFeature.LIBRARY_PODCASTS,
}


async def setup(
    mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
) -> ProviderInstanceType:
    """Create the provider instance."""
    return BibloProvider(mass, manifest, config)


@contextmanager
def _mapped_errors() -> Iterator[None]:
    """Raise the `biblo` library's errors as the Music Assistant errors that mean the same."""
    try:
        yield
    except NotFoundError as err:
        raise MediaNotFoundError(str(err)) from err
    except (InvalidCredentialsError, NotLoggedInError, AuthenticationError) as err:
        raise LoginFailed(str(err)) from err
    except (BibloConnectionError, ServerError, RateLimitError) as err:
        raise ProviderUnavailableError(f"Biblo is unavailable: {err}") from err
    except BibloError as err:
        raise ProviderUnavailableError(f"Biblo request failed: {err}") from err


def _category(material_type: bm.MaterialType) -> str:
    return str(material_type.category)


def _record_of(work: bm.Work, category: str) -> bm.Record | None:
    """Return the work's first edition of the given category (online audiobook or podcast)."""
    return next((r for r in work.variations if _category(r.material_type) == category), None)


def _has(material_types: Iterable[bm.MaterialType], category: str) -> bool:
    return any(_category(t) == category for t in material_types)


def _number(number_in_series: str | None) -> int | None:
    """`Del 12` -> 12."""
    match = re.search(r"\d+", number_in_series or "")
    return int(match.group()) if match else None


def _description(abstract: str | None, extent: str | None) -> str:
    parts = [abstract or "", f"Length: {extent}" if extent else "", PREVIEW_NOTE]
    return "\n\n".join(p for p in parts if p)


class BibloProvider(MusicProvider):
    """Audiobooks and podcasts from Biblo, played as the publishers' free previews."""

    def __init__(
        self, mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
    ) -> None:
        """Initialize; the features depend on whether a login was given at setup."""
        self._logged_in = bool(config.setup_data.get(CONF_USER_ID))
        features = BASE_FEATURES | (ACCOUNT_FEATURES if self._logged_in else set())
        super().__init__(mass, manifest, config, features)
        self._biblo: AsyncBiblo | None = None

    @property
    def biblo(self) -> AsyncBiblo:
        """Return the client, once initialized."""
        if self._biblo is None:
            raise SetupFailedError("Biblo provider is not initialized")
        return self._biblo

    @property
    def supported_media_types(self) -> set[MediaType]:
        """Searchable and playable without a login, so not derived from the library features."""
        return {MediaType.AUDIOBOOK, MediaType.PODCAST, MediaType.PODCAST_EPISODE}

    async def handle_async_init(self) -> None:
        """Create the client and, with credentials, log in."""
        library_id = str(self.get_setup_value(CONF_LIBRARY_ID) or "")
        if not library_id:
            raise SetupFailedError("No library chosen")
        # Tokens live in memory: Music Assistant logs in again with the stored credentials
        # after a restart, and the client refreshes them while it runs.
        self._biblo = AsyncBiblo(
            library_id, storage=AsyncMemoryTokenStorage(), user_agent=USER_AGENT
        )
        if not self._logged_in:
            return
        user_id = str(self.get_setup_value(CONF_USER_ID) or "")
        pincode = str(self.get_setup_value(CONF_PINCODE) or "")
        try:
            await self._biblo.login(user_id, pincode, library_id)
        except InvalidCredentialsError as err:
            raise LoginFailed("The library refused the card number/CPR or pincode") from err
        except BibloError as err:
            raise SetupFailedError(f"Logging in to Biblo failed: {err}") from err

    async def unload(self, is_removed: bool = False) -> None:
        """Close the client."""
        if self._biblo is not None:
            await self._biblo.aclose()
            self._biblo = None

    # ---------------------------------------------------------------- building items

    def _mappings(self, item_id: str) -> set[ProviderMapping]:
        return {
            ProviderMapping(
                item_id=item_id, provider_domain=self.domain, provider_instance=self.instance_id
            )
        }

    def _images(self, cover_url: str | None) -> UniqueList[MediaItemImage]:
        if not cover_url:
            return UniqueList()
        return UniqueList(
            [MediaItemImage(type=ImageType.THUMB, path=cover_url, provider=self.instance_id)]
        )

    @staticmethod
    def _links(work_id: str) -> set[MediaItemLink]:
        return {MediaItemLink(type=LinkType.WEBSITE, url=WORK_PAGE_URL.format(work_id=work_id))}

    def _audiobook(
        self,
        work_id: str,
        title: str,
        creators: Iterable[str],
        cover_url: str | None,
        publisher: str | None = None,
    ) -> Audiobook:
        book = Audiobook(
            item_id=work_id,
            provider=self.instance_id,
            name=title,
            publisher=publisher,
            provider_mappings=self._mappings(work_id),
        )
        book.authors.set(list(creators))
        book.metadata.images = self._images(cover_url)
        book.metadata.links = self._links(work_id)
        book.metadata.description = PREVIEW_NOTE
        return book

    def _audiobook_from_work(self, work: bm.Work) -> Audiobook:
        record = _record_of(work, CATEGORY_AUDIOBOOK)
        if record is None:
            raise MediaNotFoundError(f"{work.id} has no online audiobook edition")
        book = self._audiobook(
            work.id,
            work.title,
            work.creators,
            record.cover_url or work.cover_url,
            ", ".join(record.publisher) or None,
        )
        book.narrators.set([c.name for c in record.contributors if ROLE_NARRATOR in c.roles])
        book.metadata.description = _description(work.abstract, record.extent)
        book.metadata.genres = set(record.genre)
        return book

    def _podcast(
        self,
        podcast_id: str,
        name: str,
        cover_url: str | None,
        publisher: str | None,
        total_episodes: int,
        description: str | None = None,
    ) -> Podcast:
        podcast = Podcast(
            item_id=podcast_id,
            provider=self.instance_id,
            name=name,
            publisher=publisher,
            total_episodes=total_episodes,
            provider_mappings=self._mappings(podcast_id),
        )
        podcast.metadata.images = self._images(cover_url)
        podcast.metadata.description = _description(description, None)
        return podcast

    def _podcast_ref(self, podcast_id: str, name: str) -> ItemMapping:
        return ItemMapping(
            media_type=MediaType.PODCAST,
            item_id=podcast_id,
            provider=self.instance_id,
            name=name,
        )

    def _episode(
        self,
        work_id: str,
        title: str,
        podcast: Podcast | ItemMapping,
        number: int | None,
        cover_url: str | None,
        description: str | None = None,
    ) -> PodcastEpisode:
        episode = PodcastEpisode(
            item_id=work_id,
            provider=self.instance_id,
            name=title,
            podcast=podcast,
            episode_number=number,
            position=number or 0,
            provider_mappings=self._mappings(work_id),
        )
        episode.metadata.images = self._images(cover_url)
        episode.metadata.links = self._links(work_id)
        episode.metadata.description = description or PREVIEW_NOTE
        return episode

    @staticmethod
    def _podcast_id_of(work: bm.Work) -> tuple[str, str]:
        """(podcast id, podcast name) of a podcast work: its series, or itself."""
        if work.series:
            return work.series[0].series_id, work.series[0].title
        return SINGLE_EPISODE_PREFIX + work.id, work.title

    # ---------------------------------------------------------------- search

    async def search(
        self, search_query: str, media_types: list[MediaType], limit: int = 5
    ) -> SearchResults:
        """Search the library's digital catalogue for audiobooks and podcasts."""
        result = SearchResults()
        with _mapped_errors():
            if MediaType.AUDIOBOOK in media_types:
                found = await self.biblo.catalog.search(
                    search_query, limit=limit, material_types_specific=FACET_AUDIOBOOK
                )
                result.audiobooks = [
                    self._audiobook(w.id, w.title, w.creators, w.cover_url, w.publisher)
                    for w in found.works
                ]
            if MediaType.PODCAST in media_types:
                result.podcasts = await self._search_podcasts(search_query, limit)
        return result

    async def _search_podcasts(self, query: str, limit: int) -> list[Podcast]:
        """
        Search episodes, group them by series and return the series as podcasts.

        One series often fills a page of hits, so look at more episodes than podcasts wanted.
        """
        found = await self.biblo.catalog.search(
            query, limit=min(limit * 10, 100), material_types_specific=FACET_PODCAST
        )
        firsts: dict[str, bm.WorkLight] = {}
        for hit in found.works:
            key = hit.series[0].title if hit.series else SINGLE_EPISODE_PREFIX + hit.id
            firsts.setdefault(key, hit)
            if len(firsts) == limit:
                break
        # A search hit names its series but not the series id, which the work has.
        works = await asyncio.gather(*(self.biblo.catalog.work(h.id) for h in firsts.values()))
        podcasts = []
        for hit, work in zip(firsts.values(), works, strict=True):
            podcast_id, name = self._podcast_id_of(work)
            podcasts.append(self._podcast(podcast_id, name, hit.cover_url, hit.publisher, 0))
        return podcasts

    # ---------------------------------------------------------------- audiobooks

    async def get_audiobook(self, prov_audiobook_id: str) -> Audiobook:
        """Get an audiobook by work id."""
        with _mapped_errors():
            work = await self.biblo.catalog.work(prov_audiobook_id)
        return self._audiobook_from_work(work)

    async def get_library_audiobooks(self) -> AsyncGenerator[Audiobook]:
        """Favorites with an online audiobook edition, then audiobooks on loan."""
        with _mapped_errors():
            favorites, loans = await asyncio.gather(
                self.biblo.favorites.list(), self.biblo.loans.list()
            )
        seen: set[str] = set()
        for work in favorites:
            if _record_of(work, CATEGORY_AUDIOBOOK) is not None:
                seen.add(work.id)
                yield self._audiobook_from_work(work)
        for loan in loans:
            rec = loan.record
            if _category(rec.material_type) == CATEGORY_AUDIOBOOK and rec.work_id not in seen:
                seen.add(rec.work_id)
                yield self._audiobook(
                    rec.work_id, rec.title, [rec.author] if rec.author else [], rec.cover_url
                )

    async def library_add(self, item: MediaItemType) -> bool:
        """Add an audiobook to the library: saves it as a favorite in Biblo."""
        if item.media_type != MediaType.AUDIOBOOK:
            return False
        with _mapped_errors():
            await self.biblo.favorites.add(item.item_id)
        return True

    async def library_remove(self, prov_item_id: str, media_type: MediaType) -> bool:
        """Remove an audiobook from the library: removes the favorite in Biblo."""
        if media_type != MediaType.AUDIOBOOK:
            return False
        with _mapped_errors():
            await self.biblo.favorites.remove(prov_item_id)
        return True

    # ---------------------------------------------------------------- podcasts

    async def get_library_podcasts(self) -> AsyncGenerator[Podcast]:
        """Get the podcasts (series) of favorite podcast episodes."""
        with _mapped_errors():
            favorites = await self.biblo.favorites.list()
        seen: set[str] = set()
        for work in favorites:
            if _record_of(work, CATEGORY_PODCAST) is None:
                continue
            podcast_id, _ = self._podcast_id_of(work)
            if podcast_id not in seen:
                seen.add(podcast_id)
                yield await self.get_podcast(podcast_id)

    async def get_podcast(self, prov_podcast_id: str) -> Podcast:
        """Get a podcast by series id, or `work:<id>` for an episode outside any series."""
        with _mapped_errors():
            if prov_podcast_id.startswith(SINGLE_EPISODE_PREFIX):
                work = await self.biblo.catalog.work(prov_podcast_id[len(SINGLE_EPISODE_PREFIX) :])
                return self._podcast(
                    prov_podcast_id,
                    work.title,
                    work.cover_url,
                    work.details.publisher,
                    1,
                    work.abstract,
                )
            members = await self._series_episodes(prov_podcast_id)
            if not members:
                raise MediaNotFoundError(f"Podcast {prov_podcast_id} has no episodes")
            # The series' name and description are on its works, not in the series listing.
            first = await self.biblo.catalog.work(members[0].id)
        series = next((s for s in first.series or [] if s.series_id == prov_podcast_id), None)
        return self._podcast(
            prov_podcast_id,
            series.title if series else first.title,
            members[0].cover_url,
            members[0].publisher,
            len(members),
            series.description if series else None,
        )

    async def _series_episodes(self, series_id: str) -> list[bm.WorkLight]:
        works = await self.biblo.catalog.series(series_id)
        return [w for w in works if _has(w.material_types, CATEGORY_PODCAST)]

    async def get_podcast_episodes(self, prov_podcast_id: str) -> AsyncGenerator[PodcastEpisode]:
        """Get the episodes of a podcast, in series order."""
        if prov_podcast_id.startswith(SINGLE_EPISODE_PREFIX):
            yield await self.get_podcast_episode(prov_podcast_id[len(SINGLE_EPISODE_PREFIX) :])
            return
        podcast = await self.get_podcast(prov_podcast_id)
        with _mapped_errors():
            members = await self._series_episodes(prov_podcast_id)
        for index, work in enumerate(members, start=1):
            number = next(
                (_number(s.number_in_series) for s in work.series if s.title == podcast.name),
                None,
            )
            yield self._episode(work.id, work.title, podcast, number or index, work.cover_url)

    async def get_podcast_episode(self, prov_episode_id: str) -> PodcastEpisode:
        """Get a podcast episode by work id."""
        with _mapped_errors():
            work = await self.biblo.catalog.work(prov_episode_id)
        record = _record_of(work, CATEGORY_PODCAST)
        if record is None:
            raise MediaNotFoundError(f"{prov_episode_id} is not a podcast episode")
        podcast_id, name = self._podcast_id_of(work)
        number = _number(work.series[0].number_in_series) if work.series else None
        return self._episode(
            work.id,
            work.title,
            self._podcast_ref(podcast_id, name),
            number,
            record.cover_url or work.cover_url,
            _description(work.abstract, record.extent),
        )

    # ---------------------------------------------------------------- playback

    async def get_stream_details(self, item_id: str, media_type: MediaType) -> StreamDetails:
        """
        Return the publisher's free preview MP3 of the work's audiobook or podcast edition.

        Read-only: no loan is made. The link is signed and short-lived, so it is fetched for
        every play.
        """
        is_episode = media_type == MediaType.PODCAST_EPISODE
        category = CATEGORY_PODCAST if is_episode else CATEGORY_AUDIOBOOK
        try:
            with _mapped_errors():
                work = await self.biblo.catalog.work(item_id)
        except MediaNotFoundError as err:
            raise UnplayableMediaError(f"No preview for {item_id}: {err}") from err
        record = _record_of(work, category)
        if record is None:
            raise UnplayableMediaError(f"{item_id} has no {category} edition to preview")
        try:
            with _mapped_errors():
                sample = await self.biblo.catalog.sample(record.record_id, format="mp3")
        except MediaNotFoundError as err:
            raise UnplayableMediaError(f"No preview for {item_id}: {err}") from err
        return StreamDetails(
            provider=self.instance_id,
            item_id=item_id,
            audio_format=AudioFormat(content_type=ContentType.MP3),
            media_type=media_type,
            stream_type=StreamType.HTTP,
            path=sample.sample_url,
            can_seek=True,
            allow_seek=True,
        )
