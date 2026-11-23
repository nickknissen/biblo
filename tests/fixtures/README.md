# Fixtures

Each file is `{"status": <HTTP status>, "body": <JSON body>}`, as received.

## Anonymous captures

Written by `scripts/capture_fixtures.py` (library 763000, no login). The script's `ENDPOINTS`
says which request each one is. `*_all` is the same request under `x-app-type: mybiblo`.
`login_page.html` is login.bib.dk's login form.

## Account captures

These come from a real account at library 763000 (Vejle), captured by hand on 2026-11-23
with the CLI's saved session (#7, #8). They are not written by `capture_fixtures.py`, and the
fake API does not serve them yet. Each file is named after its endpoint. A file without a
suffix was captured under `x-app-type: biblo`. A `_biblogo` or `_mybiblo` suffix marks
answers that differ under that app type. Where an app type's answer was identical to `biblo`'s,
it was not kept: `account`, `account_fees` and `account_reservations` (also empty under
`biblogo`), and `account_quotas`, which `mybiblo` does not accept.

**Anonymised.** Personal fields were replaced by field name. One real value always got the
same placeholder, so the same item links up across files. The capture was then checked for
every real value and every line of the maintainer's `never-commit.txt`:

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

## Hand-recorded errors

- `error_borrow_external.json`: `POST account/loans` for `150061-netlydbog:ODN0002766628`,
  an eReolen Global (OverDrive) audiobook, on 2026-11-23. The record's `source` is
  `["eReolen Global"]` and it has `webAccess.externalAccess.url` (link.overdrive.com). The
  API refuses it with 404 `NOT_FOUND` and changes nothing. Such titles are borrowed through
  OverDrive (Libby), not the Biblo API. The body was copied from the raised `NotFoundError`.
