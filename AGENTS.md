# AGENTS.md

Unofficial Python client and CLI for the Biblo app's API (Det Digitale Folkebibliotek).
The goal is to be *the* Python library for it: stable public API, typed, sync and async.

## Generated code: never edit by hand

`uv run python scripts/generate.py` writes, and `--check` verifies (CI fails on drift):

- `src/biblo/models/_generated.py` from `spec/ddf-openapi.json` (datamodel-codegen, configured
  in pyproject.toml), then: spec patterns/lengths dropped, `spec/patches.json` applied, and
  every field given the `Lenient` validator.
- `src/biblo/_app_types.py`: the `x-app-type` values each endpoint accepts. A call picks
  another than the default with `_call(..., app_type=)`; a value outside the set raises.
- `src/biblo/_sync/` from `src/biblo/_async/` (unasync). Write async code only. Names
  starting with `Async` lose the prefix; `asyncio` becomes `threading`, so use nothing from
  asyncio but `Lock`.

`--fetch` first downloads the live spec. Review its diff: renamed schemas rename models.

## `x-app-type` picks the data

One backend serves three apps, and the header changes what comes back, not just which
endpoints answer: `biblo` sees the digital catalogue, `biblogo` its children's part,
`mybiblo` print, CDs, holdings and physical reservations and loans. Search hits, a work's
`variations`, favorites' details and `account/reservations` all differ, and no value is a
strict superset (#1, #3). Before trusting a response, compare it under every value the
endpoint accepts.

The apps are one Kotlin codebase; each build sends one fixed value. To see what an app really
sends, decompile its APK with jadx. The maintainer keeps them in the git-ignored `apk/`
(`base.apk` is Biblo, `mybiblo/`, `go/`). Class names are obfuscated: search for the DTO name
(e.g. `ReserveRequestDto`), whose serializer lists the JSON field names.

## When the API disagrees with its spec

Tests parse strictly (`tests/conftest.py`). For real-world mismatches:

1. `uv run python scripts/find_drift.py` crawls the live API read-only and prints each
   mismatch with a suggested `spec/patches.json` entry (keys are generated class and Python
   field names). Pass `--session` with a logged-in session file to include account endpoints.
   It calls each endpoint under every `x-app-type` it accepts (`--app-types biblo,mybiblo`
   limits them) and lists apart, without failing, where the answers differ.
2. Add the patch with a dated reason, regenerate, rerun.

Users never see these as errors: `Lenient` keeps the raw value and logs at DEBUG.

## Tests

- `uv run python scripts/check.py` runs what CI runs (ruff format, ruff, ty, generate
  --check, pytest on the newest Python and on 3.12).
- `tests/fake.py` serves recorded responses (`tests/fixtures/`, see its README) and a fake
  login.bib.dk. Account endpoints answer with a real account's anonymised responses, each as
  the asking `x-app-type` saw it; the few items that account lacked are made up in `fake.py`.
- `scripts/capture_fixtures.py` refreshes the anonymous fixtures. With `--session
  ~/.config/biblo/session.json` it records the account's GET endpoints under each app type
  instead: GETs only (an expiring session is not refreshed), personal fields replaced by
  placeholders, and nothing written if a real value is left. Only the maintainer runs it, on
  their own account; review `git diff tests/fixtures` before committing. Agents test it
  against the fake API only.
- `tests/test_fixture_privacy.py` fails if a fixture holds a line of
  `~/.config/aula/never-commit.txt` (or `$BIBLO_NEVER_COMMIT`), the real values that must
  never be committed. Without the file, as in CI, it is skipped.
- `uv run pytest -m live`: read-only anonymous calls to the real API.
- Never borrow, reserve or change anything on a real account from tests or scripts.

## Changelog and releases

Add user-visible changes to `## [Unreleased]` in CHANGELOG.md in the same change. Only
release when the maintainer asks: `uv run python scripts/release.py X.Y.Z` (try `--dry-run`)
turns that section into the version's, bumps, commits, pushes and creates the GitHub
release; publishing it uploads to PyPI (.github/workflows/publish.yml).

## Design rules

- Library calls take `library_id=`; account calls default to the session's login library,
  not the client's default (`AsyncBiblo._call`).
- Errors raised to users are `BibloError` subclasses, never raw httpx/pydantic errors.
- Tokens must not appear in reprs, logs or error messages.
- The CLI (`src/biblo/cli/`) uses the sync client; every listing supports `--json`; exit
  codes: 1 error, 2 usage, 4 not logged in.
