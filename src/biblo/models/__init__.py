"""Pydantic models of the API's responses, generated from its OpenAPI spec.

Field names are snake_case (`loan.due_date`); `model_dump(by_alias=True)` gives the API's
camelCase. Fields and enum values the spec doesn't know yet are kept, not rejected, so a
change on the server doesn't break the client.
"""

from ._generated import *  # noqa: F403
from ._generated import __all__  # noqa: F401
