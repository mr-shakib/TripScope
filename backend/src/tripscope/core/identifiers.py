"""Validation for identifiers that must be interpolated into SQL (database and table names).

Values (dates, IDs, numbers) are always passed as bound parameters; only identifiers that cannot be
parameterised go through here, and they must come from configuration or code, never from user input.
"""

from __future__ import annotations

import re

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def validate_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"invalid SQL identifier: {value!r}")
    return value


def redact(text: str, secrets: list[str]) -> str:
    """Remove any configured secret from free text (exception messages, logs)."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text
