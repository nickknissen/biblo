# biblo

An unofficial Python client and command line for **Biblo**, the Danish public libraries' app
for e-books, audiobooks and podcasts (by [Det Digitale Folkebibliotek](https://detdigitalefolkebibliotek.dk)).

Search the catalogue, borrow and renew, reserve, and keep track of your loans: from Python
(sync or async) or from the terminal.

```console
$ biblo auth login --library vejle
Card number or CPR: 0102031234
Pincode:
✓ Logged in to Vejle Bibliotekerne (763000) as Test Testesen

$ biblo search "tove ditlevsen" --audiobook
$ biblo work work-of:870970-basis:143298507
$ biblo borrow 870970-basis:143304299
$ biblo loans
Title                          By               Format           Due                         Loan id
Barndommens gade               Tove Ditlevsen   lydbog (online)  2026-12-20 (in 30 days)     123456
```

> Not affiliated with Det Digitale Folkebibliotek or the libraries. It talks to the same API
> as the Android app, with your own library login. Reading and listening still happen in the
> Biblo app: books are DRM-protected and this project does not download them.

## Install

```console
pip install 'biblo[cli]'      # library + the biblo command
pip install biblo             # just the library
uvx --from 'biblo[cli]' biblo search "harry potter" -l vejle   # try it without installing
```

Python 3.12 or newer.

## Command line

Everything a library belongs to takes `--library`/`-l` with an id or (part of) a name:
`-l vejle`, `-l 763000`, or set `BIBLO_LIBRARY`. Once you've logged in, your own library is the
default.

| Command | |
| --- | --- |
| `biblo libraries [town]` | find your library's id |
| `biblo auth login` / `status` / `logout` / `token` | your session |
| `biblo search QUERY` | `--ebook`, `--audiobook`, `--podcast`, `--magazine`, `--language dan`, `--by title`, `-f subjects=krimi`, `--catalogue all` |
| `biblo work WORK_ID` | description, availability and the records (editions) to borrow or reserve; `--catalogue all` lists printed books and CDs too |
| `biblo borrow RECORD_ID` | borrow an e-book, audiobook or podcast |
| `biblo loans` / `biblo renew [--all]` | your loans |
| `biblo reservations` / `reserve` / `cancel` | reservations; `reserve` picks up at your account's branch unless `--branch` says otherwise; `--days` must be one the library offers |
| `biblo reservations update ID [--branch NAME] [--days N]` | move a physical reservation to another pickup branch, or change how long it waits |
| `biblo account set --pickup-branch NAME` | your pickup branch, shared with the apps and the library's website |
| `biblo favorites [add\|remove]` | your saved works |
| `biblo account` / `messages` | details, digital quota left, fees, messages |
| `biblo branches` / `hours` / `events` | the physical library |
| `biblo api PATH` | call any endpoint, like `gh api` |

Every listing takes `--json` for scripts, e.g. `biblo loans --json | jq '.[].due_date'`.

**Catalogues.** `search` and `work` look in Biblo's digital catalogue unless `--catalogue`
says otherwise: `children` is the children's part of it (Biblo GO), `all` the whole library,
print and CDs too (Mit Biblo).

**Logging in.** `biblo auth login` asks for your card number or CPR and pincode and logs in
at login.bib.dk the way the app does. For scripts, pass `--user` (or `BIBLO_USER`) and the
pincode in `BIBLO_PIN` or on stdin with `--pin-stdin`. The pincode is never stored; the
session's tokens are, in `~/.config/biblo/session.json` (readable only by you; override the
folder with `BIBLO_CONFIG_DIR`). For MitID, use `biblo auth login --web`.

Exit codes: 0 ok, 1 error, 2 bad usage, 4 not logged in.

## Python

```python
from biblo import Biblo, FileTokenStorage

with Biblo("763000", storage=FileTokenStorage("session.json")) as biblo:
    result = biblo.catalog.search("harry potter", material="ebook")
    print(result.hitcount)
    work = biblo.catalog.work(result.works[0].id)
    ebook = next(r for r in work.variations if r.material_type.display == "e-bog")

    biblo.login("0102031234", "1234")          # once; the session is saved
    loan = biblo.loans.borrow(ebook.record_id)
    for loan in biblo.loans.list():
        print(loan.record.title, loan.due_date)
```

`AsyncBiblo` is the same client for asyncio:

```python
from biblo import AsyncBiblo

async with AsyncBiblo("763000") as biblo:
    async for work in biblo.catalog.iter_search("krimi", max_results=200):
        print(work.title)
```

The client groups the API:

| | |
| --- | --- |
| `catalog` | `search`, `iter_search`, `facets`, `suggest`, `work`, `availability`, `record`, `records_status`, `holdings`, `series`, `recommendations`, `reviews`, `sample` |
| `loans` | `list`, `borrow`, `renew`, `borrow_physical`, `return_physical` |
| `reservations` | `list`, `create`, `update`, `cancel`, `convert` |
| `account` | `get`, `update`, `quotas`, `fees`, `change_pincode` |
| `favorites`, `messages`, `devices` | the user's saved works, messages and push tokens |
| `libraries` | `list`, `find`, `get`, `configuration`, `branches`, `opening_hours`, `open_status`, `events` |
| `content` | the app's editorial front page |

`biblo.request(method, path)` reaches anything else.

**Results are typed** Pydantic models generated from the API's own OpenAPI spec, with
snake_case fields (`loan.due_date`, a timezone-aware `datetime`). Fields and enum values the
spec doesn't know yet are kept rather than rejected, and if the API sends something its spec
doesn't allow (it sometimes sends `null` for a promised string), that one value is passed
through as sent and logged at DEBUG on the `biblo` logger instead of failing the whole call.

**Libraries.** Calls default to the library given to the client; account calls default to
the library you logged in at. Every method also takes `library_id=`.

**Catalogues.** Searching, works and the content pages see Biblo's digital catalogue (e-books,
online audiobooks, podcasts, magazines) by default. Pass `catalogue="children"` for Biblo
GO's children's part of it, or `catalogue="all"` for what Mit Biblo shows: printed books,
CDs and the rest of the shelves too. `catalog.search`, `iter_search`, `facets`, `suggest`,
`work`, `availability`, `recommendations`, `content.known_pages`, `page`, `page_element` and
`favorites.list` take it. `"all"` is mostly but not strictly a superset: a work can list a
digital edition under `"digital"` that it leaves out under `"all"`.

**Sessions.** Give the client a `storage` and it saves the tokens after login and every
refresh: `FileTokenStorage`, `MemoryTokenStorage`, or your own `TokenStorage`
(`AsyncTokenStorage` for `AsyncBiblo`) with `load()` and `save()`. Access tokens are
refreshed before they expire and once more if the API answers 401. Before refreshing, the
client looks in its storage again and uses tokens another process saved there, so several
processes can share one session.

**Reuse the CLI's login.** Log in once with `biblo auth login` and point your code at the
same session file; `biblo.default_session_path()` says where it is:

```python
from biblo import AsyncBiblo, AsyncFileTokenStorage

async with AsyncBiblo(storage=AsyncFileTokenStorage.default()) as biblo:
    for loan in await biblo.loans.list():
        print(loan.record.title, loan.due_date)
```

`FileTokenStorage.default()` does the same for `Biblo`. Neither needs the `cli` extra.

**Home Assistant and other frameworks.** Pass your own `httpx` client with `http_client=`
(the client then leaves closing it to you) and a `AsyncTokenStorage` backed by the
framework's storage.

**User-Agent.** API requests say who is calling:
`biblo-python/<version> (+https://github.com/nickknissen/biblo)`. Name your application with
`user_agent="myapp/1.2"`, which goes in front. This replaces any `User-Agent` set on your
`http_client` for API requests. The login at login.bib.dk is a web form, so it keeps a
mobile browser's `User-Agent`.

**Errors** all derive from `biblo.BibloError`: `InvalidCredentialsError` for a refused
login, `NotLoggedInError` when a session is missing or can no longer be refreshed,
`BibloConnectionError`, and `APIError` subclasses by status (`NotFoundError`,
`ConflictError`, `RateLimitError`, ...) carrying the API's `code`, `type` and `message`.
To ask the user for a new login, catch `biblo.LoginRequiredError`: the one base of a refused
login, a missing or expired session, and a 401 the API keeps answering after a refresh. A
`LoginFlowError` (login.bib.dk changed) is not one, as logging in again won't fix it.

## How it works

The app's backend, `https://api.detdigitalefolkebibliotek.dk`, publishes its OpenAPI spec
(the root URL is a Swagger UI). It serves three apps: Biblo, Mit Biblo (the physical
library: branches, events, Scan & Loan) and Biblo Go, and some endpoints only answer one of
them; the client sends the right `x-app-type` for each. The header also picks the data:
only Mit Biblo sees physical loans and reservations, so `loans.list()` and
`reservations.list()` ask as both apps and merge the answers, digital items first. The API
key, app version and login.bib.dk OAuth client are the ones every copy of the Android app
ships (`src/biblo/_constants.py`).

Logging in goes through login.bib.dk (Adgangsplatformen): its library-card form is posted,
the `biblo://?code=...` redirect is caught, and the code is exchanged at `/v1/auth/login`.

## Development

```console
uv sync
uv run python scripts/check.py           # everything CI runs
uv run pytest -m live                    # read-only checks against the real API
```

The models (`src/biblo/models/_generated.py`), the app-type table and the whole sync client
(`src/biblo/_sync/`) are generated: edit `src/biblo/_async/` or `spec/`, then run
`uv run python scripts/generate.py`. See
[AGENTS.md](https://github.com/nickknissen/biblo/blob/main/AGENTS.md) for the details.

Releasing: describe the changes under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md),
then `uv run python scripts/release.py X.Y.Z` bumps the version, commits, pushes and creates
the GitHub release with those notes; publishing the release uploads it to PyPI.

## License

MIT
