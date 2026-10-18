"""Temporary databases for the demo service."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from support_ops.agent import SupportAgent
from support_ops.config import make_settings


@pytest.fixture()
def agent(tmp_path: Path) -> Iterator[SupportAgent]:
    service = SupportAgent(make_settings(tmp_path / "test.db"))
    try:
        yield service
    finally:
        service.store.close()
