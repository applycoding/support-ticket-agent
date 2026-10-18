"""Load a JSON inbox of support tickets."""

import json
from pathlib import Path

from support_ops.models import Ticket


def load_tickets(path: Path) -> list[Ticket]:
    """Read a list of tickets from a JSON file."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Inbox JSON must be a list")
    return [Ticket.model_validate(item) for item in raw]
