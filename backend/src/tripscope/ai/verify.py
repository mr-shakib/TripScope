"""Numeric-claim verification (FR-11 step 6, ADR-21).

Every figure in a model's text is matched, at the precision it was written with, against the values the tools
returned: the values themselves, percentages of shares, and ratios, changes and differences between two
returned values of the same kind (the arithmetic an analyst does when comparing like with like). Dates, clock
times, years, list numbers and identifiers are not claims and are skipped. A figure that matches nothing is
"unverified" — not necessarily wrong, but not supported by the evidence, so the UI flags it and report
narratives drop it.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from itertools import combinations

_MONTHS = (
    "january|february|march|april|june|july|august|september|october|november|december"
    "|jan|feb|mar|apr|may|jun|jul|aug|sept|sep|oct|nov|dec"
)
_NOT_CLAIMS = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),  # ISO dates
    re.compile(r"\b\d{4}-\d{2}\b"),  # months
    re.compile(rf"\b(?:{_MONTHS})\.?\s+\d{{4}}\b", re.IGNORECASE),  # March 2025
    re.compile(
        rf"\b(?:{_MONTHS})\.?\s+\d{{1,2}}\s*[-–]\s*\d{{1,2}}(?:,\s*\d{{4}})?", re.IGNORECASE
    ),  # March 1-31
    re.compile(
        rf"\b(?:{_MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,\s*\d{{4}})?", re.IGNORECASE
    ),  # Mar 1, 2025
    re.compile(rf"\b\d{{1,2}}\s+(?:{_MONTHS})\b(?:\s+\d{{4}})?", re.IGNORECASE),  # 1 March 2025
    re.compile(r"\b\d{1,2}:\d{2}\b"),  # clock times
    re.compile(r"\b(?:19|20)\d{2}\b(?![.,]\d)"),  # bare years
    re.compile(r"(?m)^\s*\d{1,2}[.)]\s"),  # list numbering
    re.compile(
        r"\b(?:zone|id|ids|location|run|version|rank|top|#)\s*#?\d+(?:\s*(?:,|and)\s*\d+)*", re.IGNORECASE
    ),
    re.compile(r"\b\d+(?:st|nd|rd|th)\b", re.IGNORECASE),  # ordinals (90th percentile)
    re.compile(r"\b\d+\s+(?:days?|weeks?|months?|years?)\b", re.IGNORECASE),  # period lengths, not metrics
]
_NUMBER = re.compile(
    r"(?P<currency>\$)?(?P<sign>[-−])?(?P<value>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s?(?P<unit>%|percent\b|k\b|m\b|bn\b|b\b|thousand\b|million\b|billion\b)?",
    re.IGNORECASE,
)
_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}
SMALL_INTEGERS = frozenset(range(0, 25))  # counts like "3 zones", "24 hours" are not worth flagging


@dataclass(frozen=True)
class Claim:
    text: str
    value: float
    percent: bool
    tolerance: float
    start: int
    end: int


@dataclass(frozen=True)
class CheckedClaim:
    text: str
    value: float
    verified: bool
    start: int
    end: int


@dataclass(frozen=True)
class Verification:
    claims: list[CheckedClaim]

    @property
    def total(self) -> int:
        return len(self.claims)

    @property
    def verified(self) -> int:
        return sum(1 for c in self.claims if c.verified)

    @property
    def unverified(self) -> list[CheckedClaim]:
        return [c for c in self.claims if not c.verified]

    def as_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "verified": self.verified,
            "claims": [
                {"text": c.text, "value": c.value, "verified": c.verified, "start": c.start, "end": c.end}
                for c in self.claims
            ],
        }


def extract_claims(text: str) -> list[Claim]:
    masked = text
    for pattern in _NOT_CLAIMS:
        masked = pattern.sub(lambda m: " " * len(m.group(0)), masked)
    claims = []
    for match in _NUMBER.finditer(masked):
        raw = match.group("value")
        unit = (match.group("unit") or "").lower()
        number = float(raw.replace(",", ""))
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        scale = _SCALE.get(unit, 1.0)
        percent = unit in ("%", "percent")
        if not unit and not match.group("currency") and decimals == 0 and number in SMALL_INTEGERS:
            continue
        sign = -1 if match.group("sign") else 1
        value = sign * number * scale
        # Half a unit in the last written digit, plus a little slack for rounding of already-rounded values.
        tolerance = 0.5 * 10 ** (-decimals) * scale * 1.02
        if not percent and decimals == 0 and scale == 1 and abs(number) >= 1000:
            tolerance = max(
                tolerance, abs(number) * 0.0005
            )  # "about 133,000" style rounding stays acceptable
        claims.append(Claim(match.group(0).strip(), value, percent, tolerance, match.start(), match.end()))
    return claims


def _candidates(values: list[float], groups: list[list[float]]) -> tuple[list[float], list[float]]:
    """(plain, percent) candidates: every returned value, plus ratios, changes and differences *within* each
    group of like quantities (the same field of one tool result, e.g. trips against trips). Pairing any two
    numbers would match almost any percentage by chance."""
    base = sorted({v for v in values if math.isfinite(v)})
    plain = list(base)
    percent = [v * 100 for v in base] + list(base)  # shares as fractions, or values already in percent
    for group in groups:
        pool = sorted({v for v in group if math.isfinite(v)})[:300]
        for a, b in combinations(pool, 2):
            for x, y in ((a, b), (b, a)):
                if y:
                    percent.append((x - y) / y * 100)  # change from y to x
                    percent.append(x / y * 100)  # x as a share of y
                    plain.append(x / y)  # "1.4 times"
            plain.append(abs(a - b))  # "6,000 more trips"
    return plain, percent


def _matches(claim: Claim, plain: list[float], percent: list[float]) -> bool:
    pool = percent if claim.percent else plain
    target = abs(claim.value) if not claim.percent else claim.value
    for candidate in pool:
        if abs(candidate - target) <= claim.tolerance or abs(abs(candidate) - abs(target)) <= claim.tolerance:
            return True
    return False


def verify(text: str, values: list[float], groups: list[list[float]] | None = None) -> Verification:
    claims = extract_claims(text)
    if not claims:
        return Verification([])
    plain, percent = _candidates(values, groups or [])
    return Verification(
        [CheckedClaim(c.text, c.value, _matches(c, plain, percent), c.start, c.end) for c in claims]
    )
