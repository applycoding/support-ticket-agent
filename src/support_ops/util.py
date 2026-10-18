"""Small helpers shared across the demo."""

import re
from datetime import UTC, date, datetime
from enum import Enum
from typing import Never

from pydantic import BaseModel

_ORDER_ID = re.compile(r"\bORD-\d+\b", re.IGNORECASE)
_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "a an the of to and or for on in is are was were be with your our this that it".split()
)


def assert_never(value: Never) -> Never:
    """Fail when a match statement receives a variant it does not handle."""
    raise RuntimeError(f"Unhandled variant: {value!r}")


def utc_now() -> str:
    """Return a compact UTC timestamp."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def format_usd(cents: int) -> str:
    """Format integer cents as a dollar string."""
    sign = "-" if cents < 0 else ""
    amount = abs(cents)
    return f"{sign}${amount // 100}.{amount % 100:02d}"


def extract_order_id(text: str) -> str | None:
    """Return the first ORD-#### id in text, if one is present."""
    match = _ORDER_ID.search(text)
    if match is None:
        return None
    return match.group(0).upper()


def age_days(value: str | None, today: date) -> int | None:
    """Return the number of days from an ISO date to today."""
    if value is None or value == "":
        return None
    return (today - date.fromisoformat(value[:10])).days


def tokenize(text: str) -> list[str]:
    """Lowercase tokens with a light trailing-s trim and stopwords removed."""
    tokens: list[str] = []
    for raw in _TOKEN.findall(text.lower()):
        if raw in _STOPWORDS or len(raw) <= 1:
            continue
        if len(raw) > 3 and raw.endswith("s"):
            raw = raw[:-1]
        tokens.append(raw)
    return tokens


def jsonable(value: object) -> object:
    """Convert models, enums, and dates into JSON-ready values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return str(value)
