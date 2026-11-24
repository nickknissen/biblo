# Fixtures

Each file is `{"status": <HTTP status>, "body": <JSON body>}`, as received.

## Anonymous captures

Written by `scripts/capture_fixtures.py` (library 763000, no login). The script's `ENDPOINTS`
says which request each one is. `*_all` is the same request under `x-app-type: mybiblo`.
`login_page.html` is login.bib.dk's login form.

## Account captures

These come from a real account at library 763000 (Vejle), captured by hand on 2026-11-23
with the CLI's saved session (#7, #8). `capture_fixtures.py --session PATH` now records the
same files the same way, GETs only (see its docstring); run against these recordings it
reproduces them unchanged. The fake API (`tests/fake.py`) serves them, each to the app type
it was recorded under. Each file is named after its endpoint. A file without a
suffix was captured under `x-app-type: biblo`. A `_biblogo` or `_mybiblo` suffix marks
answers that differ under that app type. Where an app type's answer was identical to `biblo`'s,
it was not kept: `account`, `account_fees` and `account_reservations` (also empty under
`biblogo`), and `account_quotas`, which `mybiblo` does not accept.

`account_borrow.json`, `error_renew_digital.json`, `error_invalid_token.json` and
`error_missing_api_key.json` are served too, as the answers to a borrow, a renewal of a
digital loan, a bad bearer token and a missing API key.

**Anonymised.** Personal fields were replaced by field name. One real value always got the
same placeholder, so the same item links up across files. The capture was then checked for
every real value and every line of the maintainer's `never-commit.txt`, which
`tests/test_fixture_privacy.py` repeats whenever that file exists:

| Field | Placeholder |
| --- | --- |
| account `name`, `email.emailAddress`, `phoneNumber.phoneNumber` | `Test Låner`, `laaner1@example.com`, `12345671` |
| loan `id` (20 characters) | `TestLoan000000000001`, ... |
| reservation `id` (9 digits) | `900000001`, ... |
| `claim.id` (the pickup shelf, e.g. a branch code plus a number) | `Xxx001` |
| message `id` (UUID) | `00000000-0000-4000-8000-000000000001` |

Kept as public data: library and branch ids (`DK-763004`) and branch names, work and record
ids, titles, dates, and the library's message texts.

**What the account held:**
- 3 digital loans: online audiobooks, all blue titles. Two were borrowed in the Biblo GO app
  (`biblogo`) on 2026-11-21. The third (`TestLoan000000000001`, Kahneman et al.'s *Støj*,
  `870970-basis:39283662`) was borrowed through the API under `biblo` just before the capture.
  The borrow's answer is in `account_borrow.json` (`POST account/loans` with `{"recordId": ...}`).
- 3 physical reservations, all books: one `READY_FOR_PICKUP` with a `claim`, and two
  `RESERVED` with `claim: null`.
- 4 favorites, no fees, and one unseen message.

**What the app types showed:**
- `account_loans` (`biblo`) lists all 3 digital loans. `account_loans_biblogo` lists only
  the 2 borrowed in Biblo GO, so a loan made under `biblo` is not listed under `biblogo`.
  `account_loans_mybiblo` is empty: Mit Biblo lists no digital loans.
- Physical reservations are listed only under `mybiblo`, with `physical.pickupBranch` as a
  branch id. The digital listings are empty.
- `favorites*` lists the same 4 works under each app type, but each work's `materialTypes`,
  `variations`, cover and edition details are the ones that app type's catalogue sees. This
  is the same split as in #1.
- `messages_mybiblo` has a "reservation ready for pickup" message, with
  `contentPath: content/reservations`, that `biblo` does not list.
- `account_quotas`: 6 of 6 loans and 3 of 3 reservations left, for both e-books and
  audiobooks, even with 3 digital loans out. Blue titles do not count against the quota.

## Second account capture (2026-11-23, same account and day)

- `auth_token_refresh.json`: `POST auth/token-refresh`, made through `Biblo.refresh()`. The
  body is the plain token object, with no `data` wrapper. Both tokens are replaced by
  placeholders (the real access token was 545 characters, the refresh token 64).
  `expires_in` is the real value: 2678399 seconds, so an access token lasts 31 days.
- `error_renew_digital.json`: `POST account/loans/renew?loanIds=<the Støj loan>`, under
  `biblo`. Per the spec, `loanIds` takes "Only physical loan IDs". A digital loan id is
  refused as a whole request, with 400 `VALIDATION` "Could not resolve request" and no
  per-loan `renewalStatus`. The loan's due date did not change. (The library's `renew --all`
  already skips loans without `isRenewable`, which digital loans don't have.)
- Read-only error shapes. The fake API answers with the first two:
  - `error_invalid_token.json`: `GET account` with a made-up bearer token. 401 `4104`
    "Invalid bearer token".
  - `error_missing_api_key.json`: the same request without `x-api-key`. 401 `4101` "Missing
    API key".
  - `error_unknown_route.json`: `GET /v1/no-such-route`. 404 `4000`, whose message names the
    method and the full URL, with `http://`.
  - `error_forbidden_app_type_account.json`: `GET account/quotas` under `mybiblo`. 403
    `4205`, naming the accepted app types.

These were checked against the session file's values, the loan id and `never-commit.txt`.
All parse strictly as `AuthTokens` or `AppErrorResponse`.

## Hand-recorded errors

- `error_borrow_external.json`: `POST account/loans` for `150061-netlydbog:ODN0002766628`,
  an eReolen Global (OverDrive) audiobook, on 2026-11-23. The record's `source` is
  `["eReolen Global"]` and it has `webAccess.externalAccess.url` (link.overdrive.com). The
  API refuses it with 404 `NOT_FOUND` and changes nothing. Such titles are borrowed through
  OverDrive (Libby), not the Biblo API. The body was copied from the raised `NotFoundError`.
