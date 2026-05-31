"""Tests for tribunal.db room_state functions."""

from conftest import monkeypatch_db as _monkeypatch_db
import pytest


class TestRoomState:
    def test_default_inactive(self, tmp_db):
        """New rooms are inactive."""
        from tribunal.db import room_get_state
        assert room_get_state(tmp_db, "new-room") == "inactive"

    def test_activate(self, tmp_db):
        """Activating a room sets it to active."""
        from tribunal.db import room_activate, room_get_state
        room_activate(tmp_db, "room-1", activated_by="testbot")
        assert room_get_state(tmp_db, "room-1") == "active"

    def test_deactivate(self, tmp_db):
        """Deactivating an active room sets it to suspended."""
        from tribunal.db import room_activate, room_deactivate, room_get_state
        room_activate(tmp_db, "room-1", activated_by="testbot")
        room_deactivate(tmp_db, "room-1")
        assert room_get_state(tmp_db, "room-1") == "suspended"

    def test_reactivate(self, tmp_db):
        """Re-activating a suspended room sets it back to active."""
        from tribunal.db import room_activate, room_deactivate, room_get_state
        room_activate(tmp_db, "room-1", activated_by="testbot")
        room_deactivate(tmp_db, "room-1")
        room_activate(tmp_db, "room-1", activated_by="testbot2")
        assert room_get_state(tmp_db, "room-1") == "active"

    def test_activate_idempotent(self, tmp_db):
        """Activating an already-active room is a no-op."""
        from tribunal.db import room_activate, room_get_state
        room_activate(tmp_db, "room-1", activated_by="a")
        room_activate(tmp_db, "room-1", activated_by="b")
        assert room_get_state(tmp_db, "room-1") == "active"

    def test_independent_rooms(self, tmp_db):
        """Room states are independent."""
        from tribunal.db import room_activate, room_get_state
        room_activate(tmp_db, "room-1", activated_by="testbot")
        assert room_get_state(tmp_db, "room-1") == "active"
        assert room_get_state(tmp_db, "room-2") == "inactive"
