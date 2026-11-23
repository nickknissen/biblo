"""Run the integration against the repo's fake Biblo API and login.bib.dk (tests/fake.py)."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

REPO = Path(__file__).resolve().parents[3]

# Loaded by path: this folder is also called `tests`, so `import tests.fake` would be ambiguous.
_spec = importlib.util.spec_from_file_location("biblo_fake", REPO / "tests" / "fake.py")
assert _spec is not None and _spec.loader is not None
fake_module = importlib.util.module_from_spec(_spec)
sys.modules["biblo_fake"] = fake_module
_spec.loader.exec_module(fake_module)

LIBRARY: str = fake_module.LIBRARY
USER: str = fake_module.USER
PIN: str = fake_module.PIN


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: Any) -> None:
    """Let Home Assistant load custom_components/biblo."""


@pytest.fixture
def fake() -> Any:
    return fake_module.FakeBiblo()


@pytest.fixture(autouse=True)
def fake_http(fake: Any) -> Iterator[None]:
    """Every httpx client the integration makes talks to the fake."""

    def client(*_: Any, **__: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))

    with (
        patch("custom_components.biblo.create_async_httpx_client", client),
        patch("custom_components.biblo.config_flow.create_async_httpx_client", client),
    ):
        yield
