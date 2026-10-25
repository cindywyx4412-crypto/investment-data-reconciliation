"""ISIN (ISO 6166) helpers: check-digit calculation and validation."""
from __future__ import annotations

import re

_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def isin_check_digit(body: str) -> str:
    """Return the check digit for the first 11 characters of an ISIN.

    Letters are expanded to two digits (A=10 ... Z=35), then the Luhn
    algorithm is applied to the resulting digit string.
    """
    if len(body) != 11:
        raise ValueError("ISIN body must be 11 characters")
    digits = "".join(str(int(ch, 36)) for ch in body.upper())
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:  # double every second digit, starting from the right
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def is_valid_isin(value: object) -> bool:
    """True when value is a structurally valid ISIN with a correct check digit."""
    if not isinstance(value, str):
        return False
    value = value.strip().upper()
    if not _ISIN_RE.match(value):
        return False
    return isin_check_digit(value[:11]) == value[11]


def make_isin(country: str, nsin: str) -> str:
    """Build a full ISIN from a 2-letter country code and a 9-character NSIN."""
    body = (country + nsin).upper()
    return body + isin_check_digit(body)
