"""Tests for tribunal.chatkey and tribunal.detection."""

from conftest import make_event, make_gateway
from tribunal.chatkey import derive_chat_key, from_session_id, is_dm_event, is_dm_session


class TestChatKey:
    def test_matrix_room(self):
        e = make_event(chat_id="!abc:matrix.org", platform="matrix")
        assert derive_chat_key(e) == "!abc:matrix.org"

    def test_discord_channel(self):
        e = make_event(chat_id="1234567890", platform="discord")
        assert derive_chat_key(e) == "1234567890"

    def test_matrix_thread(self):
        e = make_event(chat_id="!abc:matrix.org", thread_id="$xyz", platform="matrix")
        assert derive_chat_key(e) == "!abc:matrix.org:$xyz"

    def test_discord_thread(self):
        e = make_event(chat_id="111", thread_id="222", platform="discord")
        assert derive_chat_key(e) == "111:222"


class TestFromSessionId:
    def test_full_session_id(self):
        sid = "agent:main:matrix:group:!room:matrix.org"
        assert from_session_id(sid) == "!room:matrix.org"

    def test_session_with_thread(self):
        sid = "agent:main:discord:channel:111:222"
        assert from_session_id(sid) == "111:222"

    def test_short_session_id(self):
        assert from_session_id("short") == "short"


class TestDmDetection:
    def test_dm_event(self):
        e = make_event(chat_type="dm")
        assert is_dm_event(e) is True

    def test_group_event(self):
        e = make_event(chat_type="group")
        assert is_dm_event(e) is False

    def test_private_event(self):
        e = make_event(chat_type="private")
        assert is_dm_event(e) is True

    def test_dm_session(self):
        assert is_dm_session("agent:main:matrix:dm:!room:matrix.org") is True

    def test_group_session(self):
        assert is_dm_session("agent:main:matrix:group:!room:matrix.org") is False


class TestDetection:
    def test_human_sender(self):
        from tribunal.detection import classify_sender, SenderType
        e = make_event(is_bot=False, platform="discord")
        gw = make_gateway()
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA journal_mode=WAL")
        result = classify_sender(e, gw, conn)
        assert result == SenderType.HUMAN
        conn.close()

    def test_discord_bot_sender(self):
        from tribunal.detection import classify_sender, SenderType
        e = make_event(is_bot=True, platform="discord")
        gw = make_gateway()
        import sqlite3
        conn = sqlite3.connect(":memory:")
        result = classify_sender(e, gw, conn)
        assert result == SenderType.OTHER_BOT
        conn.close()
