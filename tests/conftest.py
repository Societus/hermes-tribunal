"""Shared test fixtures for tribunal tests."""

import os
import sqlite3
import tempfile
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Mock event / source helpers
# ---------------------------------------------------------------------------

@dataclass
class MockSource:
    platform: Any = None
    chat_id: str = "12345"
    chat_name: str = "test-room"
    chat_type: str = "group"
    user_id: str = "user-001"
    user_name: str = "alice"
    thread_id: str | None = None
    is_bot: bool = False
    guild_id: str | None = None
    message_id: str | None = "msg-001"


@dataclass
class MockEvent:
    text: str = ""
    source: Any = None
    raw_message: Any = None
    message_id: str = "msg-001"
    media_urls: list = field(default_factory=list)
    reply_to_text: str | None = None


@dataclass
class MockPlatform:
    value: str = "matrix"


def make_event(
    text: str = "hello",
    *,
    chat_id: str = "12345",
    chat_type: str = "group",
    user_id: str = "user-001",
    user_name: str = "alice",
    is_bot: bool = False,
    platform: str = "matrix",
    thread_id: str | None = None,
    message_id: str = "msg-001",
) -> MockEvent:
    """Create a mock event for testing."""
    return MockEvent(
        text=text,
        source=MockSource(
            platform=MockPlatform(value=platform),
            chat_id=chat_id,
            chat_type=chat_type,
            user_id=user_id,
            user_name=user_name,
            is_bot=is_bot,
            thread_id=thread_id,
            message_id=message_id,
        ),
        message_id=message_id,
    )


def make_gateway() -> MagicMock:
    """Create a mock gateway with adapter stubs."""
    gw = MagicMock()
    gw.adapters = {}
    return gw


# ---------------------------------------------------------------------------
# Temporary database fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary tribunal database and return the connection."""
    from tribunal.db import connect
    db_path = str(tmp_path / "tribunal.db")
    conn = connect(db_path)
    yield conn
    conn.close()


@pytest.fixture
def monkeypatch_db(tmp_path, monkeypatch):
    """Monkeypatch config.DB_PATH so get_conn() uses a temp DB."""
    db_path = str(tmp_path / "tribunal.db")
    monkeypatch.setattr("tribunal.config.DB_PATH", db_path)
    # Reset cached connection
    import tribunal.db as db_mod
    db_mod.get_conn._conn = None
    yield db_path
    if db_mod.get_conn._conn is not None:
        db_mod.get_conn._conn.close()
        db_mod.get_conn._conn = None
