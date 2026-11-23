# Changelog

All notable changes to this project. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/); until 1.0, minor versions may change the API.

## [Unreleased]

### Added

- `biblo.default_session_path()` returns the session file `biblo auth login` saves to
  (`$BIBLO_CONFIG_DIR/session.json`, else the platform's config folder), and
  `AsyncFileTokenStorage.default()` / `FileTokenStorage.default()` make a storage for it, so
  scripts and servers can reuse the CLI's login. Neither imports typer or click (#21).

### Fixed

- Clients sharing a session, e.g. the CLI and a long-running server, no longer log each other
  out. Before refreshing, a client re-reads its storage and uses tokens another client saved
  there; if the API refuses its refresh token, it takes newer tokens from storage and retries
  instead of saving an empty session over them (#21).

## [0.2.0] - 2026-11-23

### Added

- Search and browse the children's catalogue or the whole library, print and CDs too. A
  `catalogue="digital" | "children" | "all"` keyword (the new `biblo.Catalogue` type) on
  `catalog.search`, `iter_search`, `facets`, `suggest`, `work`, `availability`,
  `recommendations`, `content.known_pages`, `page`, `page_element` and `favorites.list` asks
  as Biblo, Biblo GO or Mit Biblo. The default, `"digital"`, sends what was sent before.
  `catalog.work(..., catalogue="all")` lists printed editions and their record ids, to
  reserve with a pickup branch. An unknown value raises the new `UnknownCatalogueError` (a
  `ValueError`) before anything is sent (#1).
- `biblo search` and `biblo work` take `--catalogue digital|children|all` (#1).
- `biblo account set --pickup-branch` sets the account's preferred pickup branch, from a branch
  id or part of its name. The setting is saved on the account, so the apps and the library's
  website use it too (#4).
- `biblo reserve` without `--branch` picks up at the account's preferred branch. If it has none
  and the library refuses the reservation, the error says how to give a branch (#4).
- `biblo auth login` points to `biblo account set --pickup-branch` when the account has no
  pickup branch (#4).

### Changed

- API requests send `User-Agent: biblo-python/<version> (+https://github.com/nickknissen/biblo)`
  instead of httpx's default. `Biblo(user_agent="myapp/1.2")` and `AsyncBiblo(...)` put your
  application's name in front, and the CLI sends `biblo-cli/<version>` first. A `User-Agent`
  on your own `http_client` is replaced on API requests; the login at login.bib.dk still
  looks like a mobile browser (#5).

### Fixed

- Physical loans and reservations show up. The API lists them only to the Mit Biblo app, so
  `loans.list()` and `reservations.list()` (and `biblo loans` / `biblo reservations`) now ask
  as Biblo and as Mit Biblo and merge the answers: digital items first, then physical, each
  once. They make two requests where they made one (#3).
- Physical items can be reserved. `reservations.create` with a `pickup_branch_id` is sent as
  Mit Biblo; under Biblo the API refused every physical record. Reserving one without a branch
  raises the new `PickupBranchRequiredError` (a `BadRequestError`), whose message says the
  record may be physical and needs a pickup branch (#3).
- Reserving an issue of a periodical sends the fields the API expects. `reservations.create`
  takes `periodical_volume`, `periodical_number` and `periodical_year` in place of
  `volume_number` and `volume_year`, which sent field names the API doesn't know (#2).
  **Breaking:** calls passing the old names now raise `TypeError`; they never made a valid
  request.
- `BibloConnectionError` always names what went wrong. A timeout used to give
  `The Biblo API could not be reached: ` with nothing after it; now it ends in `ReadTimeout`,
  and other errors in their type and text (e.g. `ConnectError: ...`). The same goes for
  login.bib.dk (#9).
- `FileTokenStorage.save` no longer leaves a `.tmp` file behind when the session can't be
  written as JSON.

## [0.1.0] - 2026-11-20

The first release: an unofficial Python client and command line for Biblo, the Danish public
libraries' app for e-books, audiobooks and podcasts.

### Added

- `Biblo` and `AsyncBiblo` clients for the Biblo app's API
  (`api.detdigitalefolkebibliotek.dk`), with the same methods, grouped by topic:
  - `catalog`: search (with facet filters and `iter_search` paging), works, records,
    availability, series, recommendations, reviews, samples
  - `loans`: list, borrow, renew, and Scan & Loan borrowing and returns
  - `reservations`: list, create, update, cancel, and turning ready digital reservations
    into loans
  - `account` (details, digital quota, fees, pincode), `favorites`, `messages`, `devices`
  - `libraries`: all libraries, branches, opening hours, open now, events
  - `content`: the app's editorial front page
  - `request()` for any other endpoint
- Login with card number or CPR and pincode through login.bib.dk, without a browser, and
  `authorize_url()` + `login_with_code()` for MitID and other browser logins.
- Sessions kept by a `TokenStorage` (file, memory or your own); tokens are refreshed before
  they expire and once more after a 401.
- Typed Pydantic models generated from the API's own OpenAPI spec. Unknown fields and enum
  values are kept, and a value that contradicts the spec is passed through and logged
  instead of failing the call.
- Each endpoint gets the `x-app-type` it requires (some only answer the Mit Biblo app).
- Errors as `BibloError` subclasses, with `APIError` subclasses per HTTP status.
- Works with a shared `httpx` client, e.g. Home Assistant's.
- The `biblo` command (`pip install 'biblo[cli]'`): `auth`, `search`, `work`, `loans`,
  `borrow`, `renew`, `reservations`, `reserve`, `cancel`, `favorites`, `account`,
  `messages`, `libraries`, `branches`, `hours`, `events` and `api`. Libraries by name
  (`-l vejle`), tables or `--json`, exit code 4 when not logged in.

### Known limitations

- Loans, reservations and fees follow the API's spec but have not yet been checked against
  a real account that has them. Please report anything that looks wrong.
- Reading and listening still happen in the Biblo app: books are DRM-protected and are not
  downloaded.
- Not affiliated with Det Digitale Folkebibliotek or the libraries.

[Unreleased]: https://github.com/nickknissen/biblo/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/nickknissen/biblo/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/nickknissen/biblo/releases/tag/v0.1.0
