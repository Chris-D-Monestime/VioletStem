"""Suite defaults: local-open behavior regardless of the machine .env.

The real .env may enable AUTH for the running server; automated tests must
exercise the default local-open path unless a test explicitly enables auth.
"""
import pytest

from backend import auth as authmod
from backend.config import settings


@pytest.fixture(autouse=True)
def _local_open_defaults(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", False)
    authmod._fails.clear()
    yield
    authmod._fails.clear()
