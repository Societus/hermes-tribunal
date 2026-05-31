"""Tests for tribunal.protocol HELLO marker with roster support."""

from tribunal.protocol import (
    format_hello,
    parse_markers,
    extract_task_updates,
    classify_message,
    parse_roster,
)


class TestHelloMarker:
    def test_format_hello_basic(self):
        """Basic HELLO marker formatting."""
        result = format_hello(agent="researcher", role="worker")
        assert "[TRIBUNAL:HELLO" in result
        assert "agent=researcher" in result
        assert "role=worker" in result

    def test_format_hello_with_capabilities(self):
        """HELLO marker with capabilities."""
        result = format_hello(
            agent="coder",
            role="worker",
            capabilities="python, rust, testing",
        )
        assert "capabilities=" in result
        assert "python, rust, testing" in result

    def test_format_hello_with_roster(self):
        """HELLO marker with roster."""
        result = format_hello(
            agent="goibniu",
            role="orchestrator",
            roster={"sisyphus": "worker", "hermes": "researcher"},
        )
        assert "roster=" in result
        assert "sisyphus:worker" in result
        assert "hermes:researcher" in result

    def test_format_hello_without_roster(self):
        """HELLO marker without roster omits roster param."""
        result = format_hello(agent="test", role="worker")
        assert "roster=" not in result

    def test_parse_hello(self):
        """Parse a HELLO marker."""
        text = '[TRIBUNAL:HELLO agent=researcher role=worker]'
        markers = parse_markers(text)
        assert len(markers) == 1
        assert markers[0]["type"] == "HELLO"
        assert markers[0]["agent"] == "researcher"
        assert markers[0]["role"] == "worker"

    def test_parse_hello_with_capabilities(self):
        """Parse a HELLO marker with capabilities."""
        text = '[TRIBUNAL:HELLO agent=coder role=worker capabilities="python and rust"]'
        markers = parse_markers(text)
        assert len(markers) == 1
        assert markers[0]["capabilities"] == "python and rust"

    def test_parse_hello_with_roster(self):
        """Parse a HELLO marker with roster."""
        text = '[TRIBUNAL:HELLO agent=goibniu role=orchestrator roster="sisyphus:worker,hermes:researcher"]'
        markers = parse_markers(text)
        assert len(markers) == 1
        assert markers[0]["roster"] == "sisyphus:worker,hermes:researcher"

    def test_classify_hello(self):
        """classify_message returns HELLO."""
        text = '[TRIBUNAL:HELLO agent=test role=worker]'
        assert classify_message(text) == "HELLO"

    def test_extract_task_updates_hello_standalone(self):
        """extract_task_updates handles standalone HELLO."""
        text = '[TRIBUNAL:HELLO agent=researcher role=worker capabilities="search"]'
        updates = extract_task_updates(text)
        assert len(updates) == 1
        assert updates[0]["type"] == "HELLO"
        assert updates[0]["agent"] == "researcher"
        assert updates[0]["role"] == "worker"
        assert updates[0]["capabilities"] == "search"
        assert updates[0]["roster"] == []

    def test_extract_task_updates_hello_with_roster(self):
        """extract_task_updates handles HELLO with roster."""
        text = '[TRIBUNAL:HELLO agent=goibniu role=orchestrator roster="sisyphus:worker,hermes:researcher"]'
        updates = extract_task_updates(text)
        assert len(updates) == 1
        assert updates[0]["type"] == "HELLO"
        assert updates[0]["agent"] == "goibniu"
        roster = updates[0]["roster"]
        assert len(roster) == 2
        assert {"agent_name": "sisyphus", "role": "worker"} in roster
        assert {"agent_name": "hermes", "role": "researcher"} in roster

    def test_hello_with_assign_in_same_message(self):
        """HELLO and ASSIGN can coexist in one message."""
        text = (
            '[TRIBUNAL:HELLO agent=orch role=orchestrator] '
            '[TRIBUNAL:ASSIGN id=T-001 agent=worker goal="task" depends=]'
        )
        markers = parse_markers(text)
        assert len(markers) == 2
        assert markers[0]["type"] == "HELLO"
        assert markers[1]["type"] == "ASSIGN"


class TestParseRoster:
    def test_empty(self):
        assert parse_roster("") == []

    def test_single_entry(self):
        result = parse_roster("sisyphus:worker")
        assert result == [{"agent_name": "sisyphus", "role": "worker"}]

    def test_multiple_entries(self):
        result = parse_roster("sisyphus:worker,hermes:researcher,goibniu:orchestrator")
        assert len(result) == 3
        assert result[0] == {"agent_name": "sisyphus", "role": "worker"}
        assert result[1] == {"agent_name": "hermes", "role": "researcher"}
        assert result[2] == {"agent_name": "goibniu", "role": "orchestrator"}

    def test_entry_without_role(self):
        """Entries without colon default to worker."""
        result = parse_roster("newbot")
        assert result == [{"agent_name": "newbot", "role": "worker"}]

    def test_mixed_entries(self):
        """Mix of typed and untyped entries."""
        result = parse_roster("sisyphus:worker,newbot")
        assert len(result) == 2
        assert result[0] == {"agent_name": "sisyphus", "role": "worker"}
        assert result[1] == {"agent_name": "newbot", "role": "worker"}

    def test_whitespace_handling(self):
        result = parse_roster(" sisyphus : worker , hermes : researcher ")
        assert len(result) == 2
        assert result[0] == {"agent_name": "sisyphus", "role": "worker"}
        assert result[1] == {"agent_name": "hermes", "role": "researcher"}

    def test_roundtrip(self):
        """format_hello -> parse_markers -> parse_roster roundtrip."""
        roster = {"sisyphus": "worker", "hermes": "researcher"}
        msg = format_hello(agent="goibniu", role="orchestrator", roster=roster)
        markers = parse_markers(msg)
        raw_roster = markers[0]["roster"]
        parsed = parse_roster(raw_roster)
        assert len(parsed) == 2
        names = {p["agent_name"] for p in parsed}
        assert names == {"sisyphus", "hermes"}
