"""Tests for tribunal.db -- local SQLite operations."""

import time
import json
from conftest import tmp_db


def test_schema_initialization(tmp_db):
    """Tables are created on connect."""
    tables = tmp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    names = {r[0] for r in tables}
    assert "messages" in names
    assert "tasks" in names
    assert "room_agents" in names
    assert "known_bots" in names


def test_wal_mode(tmp_db):
    """WAL mode is active."""
    row = tmp_db.execute("PRAGMA journal_mode").fetchone()
    assert row[0] == "wal"


def test_write_and_read_message(tmp_db):
    """Write a message and read it back."""
    from tribunal.db import write_message, read_history
    write_message(
        tmp_db, chat_key="room1", sender="alice",
        sender_type="human", text="hello world",
        platform="matrix", message_id="m1",
    )
    msgs = read_history(tmp_db, "room1", limit=10)
    assert len(msgs) == 1
    assert msgs[0]["sender"] == "alice"
    assert msgs[0]["text"] == "hello world"


def test_message_dedup(tmp_db):
    """Duplicate message_id is silently ignored."""
    from tribunal.db import write_message, read_history
    write_message(tmp_db, chat_key="r", sender="a", sender_type="human",
                  text="hi", platform="matrix", message_id="dup1")
    write_message(tmp_db, chat_key="r", sender="a", sender_type="human",
                  text="hi", platform="matrix", message_id="dup1")
    msgs = read_history(tmp_db, "r", limit=10)
    assert len(msgs) == 1


def test_prune_old_messages(tmp_db):
    """Messages older than PRUNE_HOURS are deleted."""
    from tribunal.db import write_message, read_history, prune
    # Insert an old message directly (backdated)
    old_ts = time.time() - 300_000  # ~83 hours ago (> 72h PRUNE_HOURS)
    tmp_db.execute(
        "INSERT INTO messages (ts, chat_key, sender, sender_type, text, platform, message_id) "
        "VALUES (?, 'r', 'a', 'human', 'old', 'matrix', 'old1')",
        (old_ts,),
    )
    tmp_db.commit()

    # Insert a recent message
    write_message(tmp_db, chat_key="r", sender="a", sender_type="human",
                  text="new", platform="matrix", message_id="new1")

    prune(tmp_db, "r")
    msgs = read_history(tmp_db, "r", limit=10)
    assert len(msgs) == 1
    assert msgs[0]["text"] == "new"


def test_task_upsert_and_get(tmp_db):
    """Create and retrieve a task."""
    from tribunal.db import task_upsert, task_get
    task_upsert(tmp_db, task_id="T-001", chat_key="room1",
                agent="researcher", goal="research auth",
                depends=["T-000"])
    t = task_get(tmp_db, "T-001")
    assert t is not None
    assert t["agent"] == "researcher"
    assert t["goal"] == "research auth"
    assert t["depends"] == ["T-000"]
    assert t["status"] == "assigned"


def test_task_update_status(tmp_db):
    """Update task status and fields."""
    from tribunal.db import task_upsert, task_update, task_get
    task_upsert(tmp_db, task_id="T-001", chat_key="r",
                agent="a", goal="g")
    task_update(tmp_db, task_id="T-001", status="in_progress", note="working")
    t = task_get(tmp_db, "T-001")
    assert t["status"] == "in_progress"
    assert t["note"] == "working"


def test_tasks_for_agent(tmp_db):
    """Filter tasks by agent."""
    from tribunal.db import task_upsert, tasks_for_agent
    task_upsert(tmp_db, task_id="T-001", chat_key="r", agent="alice", goal="g1")
    task_upsert(tmp_db, task_id="T-002", chat_key="r", agent="bob", goal="g2")
    task_upsert(tmp_db, task_id="T-003", chat_key="r", agent="alice", goal="g3")
    result = tasks_for_agent(tmp_db, "r", "alice")
    assert len(result) == 2
    ids = {t["id"] for t in result}
    assert ids == {"T-001", "T-003"}


def test_room_agents(tmp_db):
    """Register and list room agents."""
    from tribunal.db import room_agent_upsert, room_agents
    room_agent_upsert(tmp_db, chat_key="r", agent_name="alice", role="orchestrator")
    room_agent_upsert(tmp_db, chat_key="r", agent_name="bob", role="worker")
    agents = room_agents(tmp_db, "r")
    assert len(agents) == 2
    names = {a["agent_name"] for a in agents}
    assert names == {"alice", "bob"}


def test_known_bots(tmp_db):
    """Remember and check known bots."""
    from tribunal.db import bot_known, bot_remember
    assert not bot_known(tmp_db, "matrix", "@bot:example.com")
    bot_remember(tmp_db, "matrix", "@bot:example.com", "mybot")
    assert bot_known(tmp_db, "matrix", "@bot:example.com")
