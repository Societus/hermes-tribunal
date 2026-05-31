"""Tests for tribunal.hooks.dispatch -- gatekeeper logic."""

from conftest import make_event, make_gateway, monkeypatch_db


class TestDispatch:
    def test_unsupported_platform(self, monkeypatch_db):
        """Non-Discord/Matrix returns None."""
        from tribunal.hooks.dispatch import handle
        e = make_event(platform="telegram")
        gw = make_gateway()
        result = handle(event=e, gateway=gw, session_store=None)
        assert result is None

    def test_dm_returns_none(self, monkeypatch_db):
        """DM events are passed through."""
        from tribunal.hooks.dispatch import handle
        e = make_event(chat_type="dm", platform="matrix")
        gw = make_gateway()
        result = handle(event=e, gateway=gw, session_store=None)
        assert result is None

    def test_bot_message_absorbed(self, monkeypatch_db):
        """Bot messages are absorbed and skipped."""
        from tribunal.hooks.dispatch import handle
        e = make_event(
            text="I did some work",
            is_bot=True,
            platform="discord",
        )
        gw = make_gateway()
        result = handle(event=e, gateway=gw, session_store=None)
        assert result is not None
        assert result.get("action") == "skip"
        assert "bot" in result.get("reason", "")

    def test_tribunal_assign_for_self(self, monkeypatch_db):
        """ASSIGN message targeting this agent is ALLOWED."""
        from tribunal.hooks.dispatch import handle
        import tribunal.config as cfg
        old_id = cfg.AGENT_ID
        cfg.AGENT_ID = "researcher"
        try:
            e = make_event(
                text='[TRIBUNAL:ASSIGN id=T-001 agent=researcher goal="auth" depends="[]"]',
                platform="matrix",
                user_name="orchestrator",
            )
            gw = make_gateway()
            result = handle(event=e, gateway=gw, session_store=None)
            assert result is not None
            assert result.get("action") == "allow"
        finally:
            cfg.AGENT_ID = old_id

    def test_tribunal_assign_for_other(self, monkeypatch_db):
        """ASSIGN message targeting a different agent is skipped."""
        from tribunal.hooks.dispatch import handle
        import tribunal.config as cfg
        old_id = cfg.AGENT_ID
        cfg.AGENT_ID = "researcher"
        try:
            e = make_event(
                text='[TRIBUNAL:ASSIGN id=T-001 agent=coder goal="scaffold" depends="[]"]',
                platform="matrix",
                user_name="orchestrator",
            )
            gw = make_gateway()
            result = handle(event=e, gateway=gw, session_store=None)
            assert result is not None
            assert result.get("action") == "skip"
        finally:
            cfg.AGENT_ID = old_id

    def test_tribunal_done_skipped(self, monkeypatch_db):
        """DONE messages are absorbed (skip)."""
        from tribunal.hooks.dispatch import handle
        e = make_event(
            text='[TRIBUNAL:DONE id=T-001 agent=researcher result="done"]',
            platform="matrix",
            user_name="researcher",
        )
        gw = make_gateway()
        result = handle(event=e, gateway=gw, session_store=None)
        assert result is not None
        assert result.get("action") == "skip"

    def test_human_no_mention_absorbed(self, monkeypatch_db):
        """Human message with no mentions is absorbed."""
        from tribunal.hooks.dispatch import handle
        e = make_event(
            text="just chatting here",
            platform="matrix",
            user_name="alice",
        )
        gw = make_gateway()
        result = handle(event=e, gateway=gw, session_store=None)
        assert result is not None
        assert result.get("action") == "skip"
        assert "absorb" in result.get("reason", "")
