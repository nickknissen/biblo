from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from biblo import AsyncBiblo, AsyncMemoryTokenStorage, Biblo, MemoryTokenStorage
from biblo._base import Session, Tokens
from biblo.models._lenient import strict
from tests.fake import LIBRARY, FakeBiblo


@pytest.fixture(autouse=True)
def strict_models() -> Iterator[None]:
    """Tests fail on any value that doesn't match the models, instead of keeping it."""
    with strict():
        yield


@pytest.fixture
def fake() -> FakeBiblo:
    return FakeBiblo()


@pytest.fixture
def storage() -> AsyncMemoryTokenStorage:
    return AsyncMemoryTokenStorage()


@pytest.fixture
async def biblo(fake: FakeBiblo, storage: AsyncMemoryTokenStorage) -> AsyncIterator[AsyncBiblo]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))
    async with AsyncBiblo(LIBRARY, storage=storage, http_client=http) as client:
        yield client
    await http.aclose()


@pytest.fixture
async def logged_in(fake: FakeBiblo, storage: AsyncMemoryTokenStorage) -> AsyncIterator[AsyncBiblo]:
    """A client restored from a saved session, as after `biblo auth login`."""
    fake.token_number = 1
    storage.data = saved_session(access="access-1", refresh="refresh-1")
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))
    async with AsyncBiblo(storage=storage, http_client=http) as client:
        yield client
    await http.aclose()


@pytest.fixture
def sync_biblo(fake: FakeBiblo) -> Iterator[Biblo]:
    http = httpx.Client(transport=httpx.MockTransport(fake.handle))
    with Biblo(LIBRARY, storage=MemoryTokenStorage(), http_client=http) as client:
        yield client
    http.close()


def saved_session(
    access: str = "access-1", refresh: str = "refresh-1", expires_in: timedelta = timedelta(hours=1)
) -> dict:
    tokens = Tokens(access, refresh, datetime.now(UTC) + expires_in)
    return Session(library_id=LIBRARY, installation_id="install-1", tokens=tokens).to_dict()
