"""Tests for tribunal.protocol -- structured message parsing and formatting."""

from tribunal.protocol import (
    parse_markers,
    has_tribunal_markers,
    parse_depends,
    format_assign,
    format_progress,
    format_done,
    format_block,
    format_fail,
    classify_message,
    extract_task_updates,
)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

class TestParseMarkers:
    def test_assign_simple(self):
        text = '[TRIBUNAL:ASSIGN id=T-001 agent=researcher goal="auth" depends="[]"]'
        markers = parse_markers(text)
        assert len(markers) == 1
        m = markers[0]
        assert m["type"] == "ASSIGN"
        assert m["id"] == "T-001"
        assert m["agent"] == "researcher"
        assert m["goal"] == "auth"

    def test_assign_with_deps(self):
        # Use format_assign to produce a correctly escaped marker
        from tribunal.protocol import format_assign
        text = format_assign("T-002", "coder", "scaffold", ["T-001"])
        markers = parse_markers(text)
        m = markers[0]
        # Depends are now comma-separated, not JSON
        assert m["depends"] == "T-001"

    def test_done(self):
        text = '[TRIBUNAL:DONE id=T-001 agent=researcher result="JWT recommended"]'
        markers = parse_markers(text)
        assert markers[0]["type"] == "DONE"
        assert markers[0]["result"] == "JWT recommended"

    def test_progress(self):
        text = '[TRIBUNAL:PROGRESS id=T-001 agent=researcher note="reading RFCs"]'
        markers = parse_markers(text)
        assert markers[0]["note"] == "reading RFCs"

    def test_block(self):
        text = '[TRIBUNAL:BLOCK id=T-003 agent=security reason="need decision"]'
        markers = parse_markers(text)
        assert markers[0]["reason"] == "need decision"

    def test_fail(self):
        text = '[TRIBUNAL:FAIL id=T-004 agent=coder reason="dep missing"]'
        markers = parse_markers(text)
        assert markers[0]["reason"] == "dep missing"

    def test_multiple_markers(self):
        text = (
            '[TRIBUNAL:ASSIGN id=T-001 agent=a goal="g1" depends="[]"]\n'
            'some text\n'
            '[TRIBUNAL:DONE id=T-001 agent=a result="done"]'
        )
        markers = parse_markers(text)
        assert len(markers) == 2

    def test_no_markers(self):
        assert parse_markers("just a normal message") == []

    def test_unquoted_goal_with_spaces(self):
        text = '[TRIBUNAL:ASSIGN id=T-001 agent=a goal="research auth patterns" depends="[]"]'
        markers = parse_markers(text)
        assert markers[0]["goal"] == "research auth patterns"


class TestHasTribunalMarkers:
    def test_present(self):
        assert has_tribunal_markers("[TRIBUNAL:DONE id=T-001]")

    def test_absent(self):
        assert not has_tribunal_markers("hello world")


class TestParseDepends:
    def test_json_array(self):
        assert parse_depends('["T-001", "T-002"]') == ["T-001", "T-002"]

    def test_empty_array(self):
        assert parse_depends("[]") == []

    def test_comma_separated(self):
        assert parse_depends("T-001,T-002") == ["T-001", "T-002"]

    def test_empty(self):
        assert parse_depends("") == []


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

class TestFormatting:
    def test_assign_roundtrip(self):
        marker = format_assign("T-001", "researcher", "research auth", ["T-000"])
        assert "[TRIBUNAL:ASSIGN" in marker
        assert "T-001" in marker
        assert "researcher" in marker
        assert "research auth" in marker
        # Should be parseable
        parsed = parse_markers(marker)
        assert len(parsed) == 1
        assert parsed[0]["id"] == "T-001"

    def test_progress_roundtrip(self):
        marker = format_progress("T-001", "researcher", "reading docs")
        parsed = parse_markers(marker)
        assert parsed[0]["note"] == "reading docs"

    def test_done_roundtrip(self):
        marker = format_done("T-001", "researcher", "found JWT")
        parsed = parse_markers(marker)
        assert parsed[0]["result"] == "found JWT"

    def test_block_roundtrip(self):
        marker = format_block("T-001", "researcher", "need input")
        parsed = parse_markers(marker)
        assert parsed[0]["reason"] == "need input"

    def test_fail_roundtrip(self):
        marker = format_fail("T-001", "researcher", "timeout")
        parsed = parse_markers(marker)
        assert parsed[0]["reason"] == "timeout"


# ---------------------------------------------------------------------------
# High-level
# ---------------------------------------------------------------------------

class TestClassifyMessage:
    def test_assign(self):
        assert classify_message("[TRIBUNAL:ASSIGN id=T-001 agent=a goal=g]") == "ASSIGN"

    def test_done(self):
        assert classify_message("[TRIBUNAL:DONE id=T-001 agent=a result=r]") == "DONE"

    def test_normal(self):
        assert classify_message("hello") == ""


class TestExtractTaskUpdates:
    def test_single_assign(self):
        from tribunal.protocol import format_assign
        text = format_assign("T-001", "researcher", "auth", [])
        updates = extract_task_updates(text)
        assert len(updates) == 1
        assert updates[0]["type"] == "ASSIGN"
        assert updates[0]["goal"] == "auth"
        assert updates[0]["depends"] == []

    def test_done(self):
        text = '[TRIBUNAL:DONE id=T-001 agent=researcher result="JWT"]'
        updates = extract_task_updates(text)
        assert updates[0]["result"] == "JWT"
