"""No recorded fixture holds the maintainer's real personal data.

The real values can't be in the repository, so they come from a file outside it: one value
per line (name, email, phone, card number, ...), at $BIBLO_NEVER_COMMIT or else
~/.config/aula/never-commit.txt. Without the file, as in CI, the test is skipped.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.fake import FIXTURES

DEFAULT = "~/.config/aula/never-commit.txt"


def never_commit_file() -> Path:
    return Path(os.environ.get("BIBLO_NEVER_COMMIT") or DEFAULT).expanduser()


def leaks(fixtures: Path, never_commit: Path) -> list[str]:
    """Each file in `fixtures` that holds a line of `never_commit`, ignoring case. Names the
    file and line number only: the values must not end up in test output either."""
    values = [
        (number, line.strip().casefold())
        for number, line in enumerate(never_commit.read_text(encoding="utf-8").splitlines(), 1)
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return [
        f"{fixture.name} holds line {number} of {never_commit.name}"
        for fixture in sorted(fixtures.iterdir())
        if fixture.is_file()
        for text in [fixture.read_text(encoding="utf-8", errors="replace").casefold()]
        for number, value in values
        if value in text
    ]


def test_fixtures_hold_no_line_of_never_commit() -> None:
    path = never_commit_file()
    if not path.is_file():
        pytest.skip(f"no {path}: set BIBLO_NEVER_COMMIT to a file of values never to commit")
    found = leaks(FIXTURES, path)
    assert not found, "\n".join(found)


def test_the_check_names_the_file_and_line_but_not_the_value(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "account.json").write_text('{"name": "Jens Hansen"}')
    (fixtures / "other.json").write_text("{}")
    never_commit = tmp_path / "never-commit.txt"
    never_commit.write_text("# a comment\n\njens hansen\n")
    assert leaks(fixtures, never_commit) == ["account.json holds line 3 of never-commit.txt"]
