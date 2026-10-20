# AGENTS.md

Unofficial Python client and CLI for the Biblo app's API (Det Digitale Folkebibliotek).
The goal is to be *the* Python library for it: stable public API, typed, sync and async.

## Generated code: never edit by hand

`uv run python scripts/generate.py` writes, and `--check` verifies (CI fails on drift):

- `src/biblo/models/_generated.py` from `spec/ddf-openapi.json` (datamodel-codegen, configured
  in pyproject.toml), then: spec patterns/lengths dropped, `spec/patches.json` applied, and
  every field given the `Lenient` validator.
- `src/biblo/_app_types.py`: endpoints that refuse `x-app-type: biblo`.
- `src/biblo/_sync/` from `src/biblo/_async/` (unasync). Write async code only. Names
  starting with `Async` lose the prefix; `asyncio` becomes `threading`, so use nothing from
  asyncio but `Lock`.

`--fetch` first downloads the live spec. Review its diff: renamed schemas rename models.

## When the API disagrees with its spec

Tests parse strictly (`tests/conftest.py`). For real-world mismatches:

1. `uv run python scripts/find_drift.py` crawls the live API read-only and prints each
   mismatch with a suggested `spec/patches.json` entry (keys are generated class and Python
   field names). Pass `--session` with a logged-in session file to include account endpoints.
2. Add the patch with a dated reason, regenerate, rerun.

Users never see these as errors: `Lenient` keeps the raw value and logs at DEBUG.

## Tests

- `uv run python scripts/check.py` runs what CI runs (ruff format, ruff, ty, generate
  --check, pytest on the newest Python and on 3.12).
- `tests/fake.py` serves recorded responses (`tests/fixtures/`, refreshed by
  `scripts/capture_fixtures.py`) and a fake login.bib.dk. Account data there is shaped by
  the spec: nobody has recorded a real account's responses yet.
- `uv run pytest -m live`: read-only anonymous calls to the real API.
- Never borrow, reserve or change anything on a real account from tests or scripts.

## Design rules

- Library calls take `library_id=`; account calls default to the session's login library,
  not the client's default (`AsyncBiblo._call`).
- Errors raised to users are `BibloError` subclasses, never raw httpx/pydantic errors.
- Tokens must not appear in reprs, logs or error messages.
- The CLI (`src/biblo/cli/`) uses the sync client; every listing supports `--json`; exit
  codes: 1 error, 2 usage, 4 not logged in.
