"""Tests for DM exclusion -- verifying DMs are never intercepted."""

from conftest import make_event, make_gateway, monkeypatch_db


class TestDmExclusion:
    def test_dispatch_skips_dm(self, monkeypatch_db):
        """pre_gateway_dispatch returns None for DMs."""
        from tribunal.hooks.dispatch import handle
        e = make_event(text="hello", chat_type="dm", platform="matrix")
        gw = make_gateway()
        result = handle(event=e, gateway=gw, session_store=None)
        assert result is None

    def test_context_skips_dm_session(self):
        """pre_llm_call returns None for DM sessions."""
        from tribunal.hooks.context import handle
        result = handle(
            session_id="agent:main:matrix:dm:!room:matrix.org",
            user_message="hello",
            conversation_history=[],
            is_first_turn=True,
            model="test",
            platform="matrix",
        )
        assert result is None

    def test_persist_skips_dm_session(self, monkeypatch_db):
        """post_llm_call does nothing for DM sessions."""
        from tribunal.hooks.persist import handle
        # Should not raise
        handle(
            session_id="agent:main:matrix:dm:!room:matrix.org",
            user_message="hello",
            assistant_response="hi there",
            conversation_history=[],
            model="test",
            platform="matrix",
        )

    def test_dm_not_stored(self, monkeypatch_db):
        """DM messages are never written to the tribunal database."""
        from tribunal.hooks.persist import handle
        from tribunal import db
        handle(
            session_id="agent:main:matrix:dm:!room:matrix.org",
            user_message="secret dm message",
            assistant_response="secret reply",
            conversation_history=[],
            model="test",
            platform="matrix",
        )
        conn = db.get_conn()
        msgs = db.read_history(conn, "!room:matrix.org")
        assert len(msgs) == 0

    def test_room_message_is_stored(self, monkeypatch_db):
        """Room messages ARE stored (contrast with DM test)."""
        from tribunal.hooks.persist import handle
        from tribunal import db
        handle(
            session_id="agent:main:matrix:group:!room:matrix.org",
            user_message="room message",
            assistant_response="room reply",
            conversation_history=[],
            model="test",
            platform="matrix",
        )
        conn = db.get_conn()
        msgs = db.read_history(conn, "!room:matrix.org")
        assert len(msgs) == 2  # user + assistant
