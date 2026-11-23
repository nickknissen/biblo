# Music Assistant provider (example)

A [Music Assistant](https://www.music-assistant.io/) music provider built on this library:
the Danish public libraries' audiobooks and podcasts from Biblo, in Music Assistant.

## What it does

- **Search** your library's digital catalogue for audiobooks and podcasts. No login needed.
- **Play the publisher's free preview** of an audiobook or podcast episode (an MP3 from
  `catalog.sample`, the same preview the Biblo app offers). Nothing is borrowed.
- **Your library**, if you log in with card number/CPR and pincode: your favorites
  ("Huskeliste") and current loans. Adding or removing an audiobook in Music Assistant adds
  or removes the favorite in Biblo.
- Every item links to its page on bibliotek.dk.

## What it doesn't do

It doesn't play full titles. The Biblo app plays them through WeDoBooks' player, which keeps
them protected and outside the API this library wraps. Podcasts work the same way: they are
eReolen titles, not public RSS feeds. To hear the whole thing, borrow it in the Biblo app.

## How Biblo maps onto Music Assistant

| Music Assistant | Biblo | Item id |
| --- | --- | --- |
| Audiobook | a work with an online audiobook edition (`lydbog (online)`) | work id |
| Podcast | a series of podcast works | series id |
| Podcast episode | one podcast work | work id |
| Podcast, single episode | a podcast work outside any series | `work:<work id>` |

Biblo's search returns episodes, so a podcast search groups the hits by series.

## Try it

The provider is the `biblo/` folder. Music Assistant only loads providers from its own
source tree, so use a [Music Assistant server](https://github.com/music-assistant/server)
development checkout (see its `DEVELOPMENT.md`):

```sh
cp -r examples/music_assistant/biblo <server checkout>/music_assistant/providers/biblo
```

Start the server and add the **Biblo** provider. Setup asks for your library, plus an
optional card number/CPR and pincode; both are stored encrypted. The manifest requires
`biblo==0.2.0`, which Music Assistant installs. Requests to the API say they come from
`music-assistant-biblo` in their User-Agent.

## Status

Experimental, and not submitted to Music Assistant. Tested against Music Assistant server
`4a993b3` (2026-11-23) with `biblo` 0.2.0 from PyPI:

- search, details, podcast episodes and the preview streams against the live API,
  anonymously;
- login, library and favorites using the fake login.bib.dk in `tests/fake.py`, with real
  audiobook and podcast works in place of the account data;
- Music Assistant's `ruff` and `mypy` settings.

It hasn't run inside a full Music Assistant server or against a real account yet.

Before submitting it, it still needs:

- an `icon.svg`;
- tests in Music Assistant's `tests/providers/`;
- a decision from Music Assistant's maintainers on whether they want a provider that only
  plays previews.
