"""Lenient parsing: one unexpected value doesn't fail a whole response.

The API's spec is good but not perfect; it sometimes sends null where the spec promises a
string, say. Every generated field carries `Lenient`: if its value doesn't validate, the raw
value is kept (and logged at DEBUG on the "biblo" logger) instead of raising, so the rest of
the response is still usable.

Tests and scripts/find_drift.py turn this off with `strict()` to find such mismatches; the
fixes go in spec/patches.json.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from pydantic import ValidationError, ValidationInfo, ValidatorFunctionWrapHandler, WrapValidator

log = logging.getLogger("biblo")

_strict: ContextVar[bool] = ContextVar("biblo_strict_models", default=False)
_drift: ContextVar[list[tuple[str, str, str]] | None] = ContextVar("biblo_drift", default=None)


@contextmanager
def strict() -> Generator[None]:
    """Raise on any value that doesn't match the models, instead of keeping it."""
    token = _strict.set(True)
    try:
        yield
    finally:
        _strict.reset(token)


@contextmanager
def record_drift() -> Generator[list[tuple[str, str, str]]]:
    """Collect (model, field, error) for every value kept despite not matching."""
    found: list[tuple[str, str, str]] = []
    token = _drift.set(found)
    try:
        yield found
    finally:
        _drift.reset(token)


def _lenient(value: Any, handler: ValidatorFunctionWrapHandler, info: ValidationInfo) -> Any:
    try:
        return handler(value)
    except ValidationError as err:
        if _strict.get():
            raise
        model = str(info.config.get("title", "?")) if info.config else "?"
        field = info.field_name or "?"
        first = err.errors()[0]
        log.debug("Keeping %s.%s = %r as sent: %s", model, field, value, first["msg"])
        if (found := _drift.get()) is not None:
            found.append((model, field, f"{first['type']}: {first['msg']} (got {value!r:.60})"))
        return value


Lenient = WrapValidator(_lenient)
