"""Parameter types for the client's methods."""

from __future__ import annotations

from typing import Literal, TypedDict

SearchBy = Literal["all", "subject", "creator", "title"]
"""What `q` is matched against: everything (the default), or one kind of field."""

SampleFormat = Literal["epub", "mp3"]

Material = Literal["ebook", "audiobook", "podcast", "magazine"]
"""A format to search for with `material=`: e-books, online audiobooks, podcasts or online
magazines. biblo sends the catalogue's own facet value for it (e.g. "lydbog (online)")."""

Catalogue = Literal["digital", "children", "all"]
"""Which catalogue catalogue and content calls see; each is what one of the apps shows.

- "digital" (the default): Biblo's e-books, online audiobooks, podcasts and magazines.
- "children": Biblo GO's, the children's part of the digital catalogue.
- "all": Mit Biblo's, printed books, CDs, films and the rest of the library's shelves too.
  Mostly a superset of "digital", but not strictly: a work can list a digital edition under
  "digital" that it doesn't list under "all".
"""


class SearchFilters(TypedDict, total=False):
    """Facet filters for search. Each takes one value or a list (any of them matches).

    `catalog.facets(q)` lists the values a query has, e.g.
    `material_types_specific=["e-bog", "lydbog (online)"]`, `access_types="online"`,
    `main_languages="dansk"`.
    """

    access_types: str | list[str]
    age: str | list[str]
    age_range: str | list[str]
    branch_id: str | list[str]
    can_always_be_loaned: str | list[str]
    children_or_adults: str | list[str]
    creators: str | list[str]
    department: str | list[str]
    dk5: str | list[str]
    fiction_nonfiction: str | list[str]
    fictional_characters: str | list[str]
    game_platform: str | list[str]
    general_audience: str | list[str]
    genre_and_form: str | list[str]
    let_range: str | list[str]
    library_recommendation: str | list[str]
    lix_range: str | list[str]
    location: str | list[str]
    main_languages: str | list[str]
    material_types_general: str | list[str]
    material_types_specific: str | list[str]
    status: str | list[str]
    subjects: str | list[str]
    sublocation: str | list[str]
    work_types: str | list[str]
    year: str | list[str]


SEARCH_FILTER_PARAMS: dict[str, str] = {
    name: name.split("_")[0] + "".join(part.title() for part in name.split("_")[1:])
    for name in SearchFilters.__annotations__
}
"""snake_case filter name -> the API's camelCase query parameter."""
