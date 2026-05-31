"""Tests for tribunal.hooks.dispatch -- gatekeeper logic.

Includes tests for room activation state machine:
  - Inactive rooms: dispatch returns None (core routing)
  - Active rooms: full tribunal dispatch logic
  - HELLO markers (standalone + roster): activate rooms, register agents
  - ASSIGN markers: activate rooms, create tasks
  - Multi-mention in inactive rooms: auto-activate for orchestrator
"""

from conftest import make_event, make_gateway, monkeypatch_db


class TestDispatchInactiveRooms:
    """Rooms without tribunal activation -- core routing pass-through."""

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

    def test_inactive_room_human_message_returns_none(self, monkeypatch_db):
        """In an inactive room, human messages pass through to core routing."""
        from tribunal.hooks.dispatch import handle
        e = make_event(
            text="just chatting here",
            platform="matrix",
            user_name="alice",
        )
        gw = make_gateway()
        result = handle(event=e, gateway=gw, session_store=None)
        assert result is None

    def test_inactive_room_bot_message_returns_none(self, monkeypatch_db):
        """In an inactive room, bot messages pass through to core routing."""
        from tribunal.hooks.dispatch import handle
        e = make_event(
            text="I did some work",
            is_bot=True,
            platform="discord",
        )
        gw = make_gateway()
        result = handle(event=e, gateway=gw, session_store=None)
        assert result is None


class TestDispatchRoomActivation:
    """HELLO and ASSIGN markers activate rooms."""

    def test_hello_activates_room(self, monkeypatch_db):
        """A HELLO marker activates the room."""
        from tribunal.hooks.dispatch import handle
        from tribunal import db
        e = make_event(
            text='[TRIBUNAL:HELLO agent=otherbot role=worker]',
            platform="matrix",
            user_name="otherbot",
            user_id="@otherbot:example.com",
        )
        gw = make_gateway()
        result = handle(event=e, gateway=gw, session_store=None)
        assert result is not None
        assert result.get("action") == "skip"
        assert "hello" in result.get("reason", "")
        conn = db.get_conn()
        assert db.room_get_state(conn, "12345") == "active"

    def test_hello_registers_agent(self, monkeypatch_db):
        """A HELLO marker registers the sender as a room agent."""
        from tribunal.hooks.dispatch import handle
        from tribunal import db
        e = make_event(
            text='[TRIBUNAL:HELLO agent=researcher role=worker]',
            platform="matrix",
            user_name="researcher",
            user_id="@researcher:example.com",
        )
        gw = make_gateway()
        handle(event=e, gateway=gw, session_store=None)
        conn = db.get_conn()
        agents = db.room_agents(conn, "12345")
        assert any(a["agent_name"] == "researcher" for a in agents)

    def test_assign_activates_room(self, monkeypatch_db):
        """An ASSIGN marker activates the room."""
        from tribunal.hooks.dispatch import handle
        from tribunal import db
        import tribunal.config as cfg
        old_id = cfg.AGENT_ID
        cfg.AGENT_ID = "researcher"
        try:
            e = make_event(
                text='[TRIBUNAL:ASSIGN id=T-001 agent=researcher goal="auth" depends=]',
                platform="matrix",
                user_name="orchestrator",
            )
            gw = make_gateway()
            result = handle(event=e, gateway=gw, session_store=None)
            assert result is not None
            assert result.get("action") == "allow"
            conn = db.get_conn()
            assert db.room_get_state(conn, "12345") == "active"
        finally:
            cfg.AGENT_ID = old_id


class TestDispatchRosterHello:
    """HELLO markers with roster declarations."""

    def test_roster_hello_registers_all(self, monkeypatch_db):
        """A HELLO with roster registers all declared agents."""
        from tribunal.hooks.dispatch import handle
        from tribunal import db
        e = make_event(
            text='[TRIBUNAL:HELLO agent=goibniu role=orchestrator roster="sisyphus:worker,hermes:researcher"]',
            platform="matrix",
            user_name="goibniu",
            user_id="@goibniu:example.com",
        )
        gw = make_gateway()
        result = handle(event=e, gateway=gw, session_store=None)
        assert result is not None
        assert result.get("action") == "skip"
        conn = db.get_conn()
        agents = db.room_agents(conn, "12345")
        names = {a["agent_name"] for a in agents}
        assert "goibniu" in names
        assert "sisyphus" in names
        assert "hermes" in names

    def test_roster_hello_assigns_correct_roles(self, monkeypatch_db):
        """Roster members get the roles declared in the HELLO."""
        from tribunal.hooks.dispatch import handle
        from tribunal import db
        e = make_event(
            text='[TRIBUNAL:HELLO agent=goibniu role=orchestrator roster="sisyphus:worker,hermes:researcher"]',
            platform="matrix",
            user_name="goibniu",
        )
        gw = make_gateway()
        handle(event=e, gateway=gw, session_store=None)
        conn = db.get_conn()
        agents = db.room_agents(conn, "12345")
        by_name = {a["agent_name"]: a["role"] for a in agents}
        assert by_name["goibniu"] == "orchestrator"
        assert by_name["sisyphus"] == "worker"
        assert by_name["hermes"] == "researcher"

    def test_roster_hello_activates_room(self, monkeypatch_db):
        """A roster HELLO activates the room."""
        from tribunal.hooks.dispatch import handle
        from tribunal import db
        e = make_event(
            text='[TRIBUNAL:HELLO agent=goibniu role=orchestrator roster="sisyphus:worker"]',
            platform="matrix",
            user_name="goibniu",
        )
        gw = make_gateway()
        handle(event=e, gateway=gw, session_store=None)
        conn = db.get_conn()
        assert db.room_get_state(conn, "12345") == "active"

    def test_roster_hello_without_roster_falls_back_to_self(self, monkeypatch_db):
        """A HELLO without roster only registers the emitter."""
        from tribunal.hooks.dispatch import handle
        from tribunal import db
        e = make_event(
            text='[TRIBUNAL:HELLO agent=goibniu role=orchestrator]',
            platform="matrix",
            user_name="goibniu",
        )
        gw = make_gateway()
        handle(event=e, gateway=gw, session_store=None)
        conn = db.get_conn()
        agents = db.room_agents(conn, "12345")
        assert len(agents) == 1
        assert agents[0]["agent_name"] == "goibniu"


class TestDispatchInactiveRoomMultiMention:
    """Multi-mention in inactive rooms auto-activates for orchestrator."""

    def test_orchestrator_multi_mention_activates_room(self, monkeypatch_db):
        """Orchestrator multi-mention in inactive room auto-activates."""
        from tribunal.hooks.dispatch import handle
        from tribunal import db
        import tribunal.config as cfg

        old_id = cfg.AGENT_ID
        cfg.AGENT_ID = "goibniu"
        try:
            # Pre-register agents so mentions parse correctly
            conn = db.get_conn()
            db.room_agent_upsert(conn, chat_key="12345", agent_name="goibniu", role="orchestrator")
            db.room_agent_upsert(conn, chat_key="12345", agent_name="sisyphus", role="worker")

            e = make_event(
                text="@goibniu @sisyphus research auth patterns",
                platform="matrix",
                user_name="alice",
            )
            gw = make_gateway()
            result = handle(event=e, gateway=gw, session_store=None)
            assert result is not None
            assert result.get("action") == "allow"
            # Room should now be active
            assert db.room_get_state(conn, "12345") == "active"
        finally:
            cfg.AGENT_ID = old_id

    def test_non_orchestrator_multi_mention_does_not_activate(self, monkeypatch_db):
        """Worker multi-mention in inactive room does not activate."""
        from tribunal.hooks.dispatch import handle
        from tribunal import db
        import tribunal.config as cfg

        old_id = cfg.AGENT_ID
        old_role = cfg.ROLE
        cfg.AGENT_ID = "workerbot"
        cfg.ROLE = "worker"
        try:
            conn = db.get_conn()
            db.room_agent_upsert(conn, chat_key="12345", agent_name="workerbot", role="worker")
            db.room_agent_upsert(conn, chat_key="12345", agent_name="otherbot", role="worker")

            e = make_event(
                text="@workerbot @otherbot do something",
                platform="matrix",
                user_name="alice",
            )
            gw = make_gateway()
            result = handle(event=e, gateway=gw, session_store=None)
            # Worker in inactive room: pass through
            assert result is None
            assert db.room_get_state(conn, "12345") == "inactive"
        finally:
            cfg.AGENT_ID = old_id
            cfg.ROLE = old_role


class TestDispatchActiveRooms:
    """Full tribunal dispatch in activated rooms."""

    def _activate_room(self, monkeypatch_db):
        """Helper: activate a room via HELLO."""
        from tribunal.hooks.dispatch import handle
        e = make_event(
            text='[TRIBUNAL:HELLO agent=testbot role=worker]',
            platform="matrix",
            user_name="testbot",
        )
        gw = make_gateway()
        handle(event=e, gateway=gw, session_store=None)

    def test_bot_message_absorbed_in_active_room(self, monkeypatch_db):
        """Bot messages are absorbed and skipped in active rooms."""
        self._activate_room(monkeypatch_db)
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
        self._activate_room(monkeypatch_db)
        from tribunal.hooks.dispatch import handle
        import tribunal.config as cfg
        old_id = cfg.AGENT_ID
        cfg.AGENT_ID = "researcher"
        try:
            e = make_event(
                text='[TRIBUNAL:ASSIGN id=T-001 agent=researcher goal="auth" depends=]',
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
        self._activate_room(monkeypatch_db)
        from tribunal.hooks.dispatch import handle
        import tribunal.config as cfg
        old_id = cfg.AGENT_ID
        cfg.AGENT_ID = "researcher"
        try:
            e = make_event(
                text='[TRIBUNAL:ASSIGN id=T-001 agent=coder goal="scaffold" depends=]',
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
        self._activate_room(monkeypatch_db)
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

    def test_human_no_mention_absorbed_in_active_room(self, monkeypatch_db):
        """Human message with no mentions is absorbed in active rooms."""
        self._activate_room(monkeypatch_db)
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

    def test_hello_from_second_agent_in_active_room(self, monkeypatch_db):
        """A second HELLO in an already-active room still registers the agent."""
        self._activate_room(monkeypatch_db)
        from tribunal.hooks.dispatch import handle
        from tribunal import db
        e = make_event(
            text='[TRIBUNAL:HELLO agent=newbot role=worker]',
            platform="matrix",
            user_name="newbot",
            user_id="@newbot:example.com",
        )
        gw = make_gateway()
        result = handle(event=e, gateway=gw, session_store=None)
        assert result is not None
        assert result.get("action") == "skip"
        conn = db.get_conn()
        agents = db.room_agents(conn, "12345")
        names = [a["agent_name"] for a in agents]
        assert "testbot" in names
        assert "newbot" in names
