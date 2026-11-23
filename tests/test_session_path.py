"""Where the CLI's session lives, and reusing it from the library without the CLI's deps."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from biblo import AsyncFileTokenStorage, FileTokenStorage, default_session_path
from biblo._base import Session

PLATFORMS = {
    "linux": ("linux", False),
    "macos": ("darwin", False),
    "windows": ("win32", True),
}


def _app_dir_getters() -> list[tuple[str, Callable[..., str], str]]:
    """(name, get_app_dir, module holding its WIN flag) for typer and, if present, click."""
    import typer
    import typer._click.utils

    getters = [("typer", typer.get_app_dir, "typer._click.utils")]
    try:
        import click.utils
    except ImportError:  # click comes with the dev tools, not the CLI
        return getters
    return [*getters, ("click", click.utils.get_app_dir, "click.utils")]


@pytest.fixture(autouse=True)
def no_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("BIBLO_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))


@pytest.mark.parametrize("getter", _app_dir_getters(), ids=lambda g: g[0])
@pytest.mark.parametrize(
    ("platform", "env"),
    [
        ("linux", {}),
        ("linux", {"XDG_CONFIG_HOME": "/xdg/config"}),
        ("macos", {}),
        ("windows", {}),
        ("windows", {"APPDATA": r"C:\Users\me\AppData\Roaming"}),
    ],
)
def test_the_path_is_the_clis_app_dir(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    env: dict[str, str],
    getter: tuple[str, Callable[..., str], str],
) -> None:
    _, get_app_dir, win_module = getter
    sys_platform, win = PLATFORMS[platform]
    for name in ("XDG_CONFIG_HOME", "APPDATA"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys, "platform", sys_platform)
    monkeypatch.setattr(f"{win_module}.WIN", win)

    assert default_session_path() == Path(get_app_dir("biblo")) / "session.json"


def test_biblo_config_dir_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BIBLO_CONFIG_DIR", str(tmp_path / "conf"))
    assert default_session_path() == tmp_path / "conf" / "session.json"
    assert AsyncFileTokenStorage.default().path == tmp_path / "conf" / "session.json"
    assert FileTokenStorage.default().path == tmp_path / "conf" / "session.json"


async def test_default_storage_reads_what_the_cli_saved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from biblo.cli import _state

    monkeypatch.setenv("BIBLO_CONFIG_DIR", str(tmp_path))
    saved = Session(library_id="763000", installation_id="install-1").to_dict()
    FileTokenStorage(_state.session_path()).save(saved)  # where `biblo auth login` saves

    assert await AsyncFileTokenStorage.default().load() == saved
    assert FileTokenStorage.default().load() == saved


def test_importing_biblo_leaves_out_the_cli_deps() -> None:
    # httpx imports click itself when it is installed (for its own CLI), as it is with the
    # dev tools, so count only what importing biblo adds after httpx.
    code = """
import sys
import httpx
before = set(sys.modules)
import biblo
added = set(sys.modules) - before
print(sorted(m for m in added if m.split(".")[0] in {"typer", "click"}))
"""
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout
    assert out.strip() == "[]"


def test_biblo_imports_without_the_cli_deps() -> None:
    code = """
import sys
from importlib.abc import MetaPathFinder

class NoCliDeps(MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in {"typer", "click"}:
            raise ImportError(name)

sys.meta_path.insert(0, NoCliDeps())
import biblo
print(biblo.default_session_path().name, biblo.FileTokenStorage.default().path.name)
"""
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout
    assert out.split() == ["session.json", "session.json"]
