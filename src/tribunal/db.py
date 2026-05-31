"""Tribunal local SQLite database.

Each agent maintains its own database. No cross-machine sharing.
Uses WAL mode for safe concurrent access from the gateway process.

Tables:
  - messages: absorbed room messages + tribunal protocol messages
  - tasks: task state observed from the room stream
  - room_agents: agent roster per room
  - known_bots: cache of known bot user IDs
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from . import config

logger = logging.getLogger("tribunal.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    chat_key    TEXT    NOT NULL,
    sender      TEXT    NOT NULL,
    sender_type TEXT    NOT NULL DEFAULT 'human',
    text        TEXT    NOT NULL,
    platform    TEXT    NOT NULL DEFAULT '',
    message_id  TEXT    NOT NULL DEFAULT '',
    tribunal    TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_msgs_chat_ts
    ON messages (chat_key, ts);

CREATE INDEX IF NOT EXISTS idx_msgs_tribunal
    ON messages (chat_key, tribunal);

CREATE TABLE IF NOT EXISTS tasks (
    id           TEXT PRIMARY KEY,
    chat_key     TEXT    NOT NULL,
    agent        TEXT    NOT NULL,
    goal         TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'assigned',
    depends      TEXT    NOT NULL DEFAULT '[]',
    note         TEXT    NOT NULL DEFAULT '',
    result       TEXT    NOT NULL DEFAULT '',
    block_reason TEXT    NOT NULL DEFAULT '',
    created_at   REAL    NOT NULL,
    updated_at   REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_chat_agent
    ON tasks (chat_key, agent);

CREATE INDEX IF NOT EXISTS idx_tasks_status
    ON tasks (status);

CREATE TABLE IF NOT EXISTS room_agents (
    chat_key    TEXT    NOT NULL,
    agent_name  TEXT    NOT NULL,
    platform_id TEXT    NOT NULL DEFAULT '',
    role        TEXT    NOT NULL DEFAULT 'worker',
    status      TEXT    NOT NULL DEFAULT 'active',
    PRIMARY KEY (chat_key, agent_name)
);

CREATE TABLE IF NOT EXISTS known_bots (
    platform    TEXT    NOT NULL,
    user_id     TEXT    NOT NULL,
    bot_name    TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (platform, user_id)
);

CREATE TABLE IF NOT EXISTS room_state (
    chat_key    TEXT    PRIMARY KEY,
    status      TEXT    NOT NULL DEFAULT 'inactive',
    activated_by TEXT   NOT NULL DEFAULT '',
    activated_at REAL   NOT NULL DEFAULT 0,
    updated_at  REAL    NOT NULL DEFAULT 0
);
"""


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

def connect(db_path: str | None = None) -> sqlite3.Connection:
    """Open (and initialise) the local tribunal database.

    Creates parent directories and applies the schema if the file does
    not yet exist.  Enables WAL mode.
    """
    path = Path(db_path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def get_conn() -> sqlite3.Connection:
    """Return a module-cached connection (lazy, one per process)."""
    if not hasattr(get_conn, "_conn") or get_conn._conn is None:
        get_conn._conn = connect()
    return get_conn._conn

get_conn._conn: sqlite3.Connection | None = None  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

def write_message(
    conn: sqlite3.Connection,
    *,
    chat_key: str,
    sender: str,
    sender_type: str,
    text: str,
    platform: str = "",
    message_id: str = "",
    tribunal: str = "",
) -> None:
    """Insert a message row.  Deduplicates by message_id when present."""
    now = time.time()
    try:
        if message_id:
            existing = conn.execute(
                "SELECT 1 FROM messages WHERE message_id = ?",
                (message_id,),
            ).fetchone()
            if existing:
                return
        conn.execute(
            "INSERT INTO messages (ts, chat_key, sender, sender_type, text, platform, message_id, tribunal) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (now, chat_key, sender, sender_type, text, platform, message_id, tribunal),
        )
        conn.commit()
    except sqlite3.Error:
        logger.exception("write_message failed")


def read_history(
    conn: sqlite3.Connection,
    chat_key: str,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return the last *limit* messages for a room, oldest first."""
    limit = limit or config.HISTORY_COUNT
    rows = conn.execute(
        "SELECT ts, sender, sender_type, text, tribunal "
        "FROM messages WHERE chat_key = ? "
        "ORDER BY ts DESC LIMIT ?",
        (chat_key, limit),
    ).fetchall()
    # reverse so oldest is first
    return [
        {
            "ts": r[0],
            "sender": r[1],
            "sender_type": r[2],
            "text": r[3],
            "tribunal": r[4],
        }
        for r in reversed(rows)
    ]


def format_history(
    conn: sqlite3.Connection,
    chat_key: str,
    limit: int | None = None,
) -> str:
    """Return a human-readable history block for context injection."""
    msgs = read_history(conn, chat_key, limit)
    if not msgs:
        return ""
    lines: list[str] = []
    for m in msgs:
        import datetime
        ts_str = datetime.datetime.fromtimestamp(m["ts"]).strftime("%H:%M")
        sender = m["sender"]
        text = m["text"]
        if m["sender_type"] == "bot":
            prefix = f"[Bot: {sender}]"
        elif m["sender_type"] == "self":
            prefix = f"[Bot: {sender}]"  # own messages look the same
        else:
            prefix = f"**{sender}**"
        lines.append(f"{ts_str} {prefix}: {text}")
    return "\n".join(lines)


def prune(conn: sqlite3.Connection, chat_key: str) -> None:
    """Delete messages older than PRUNE_HOURS for the given room."""
    cutoff = time.time() - (config.PRUNE_HOURS * 3600)
    try:
        conn.execute(
            "DELETE FROM messages WHERE chat_key = ? AND ts < ?",
            (chat_key, cutoff),
        )
        conn.commit()
    except sqlite3.Error:
        logger.exception("prune failed")


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

def task_upsert(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    chat_key: str,
    agent: str,
    goal: str,
    status: str = "assigned",
    depends: list[str] | None = None,
) -> None:
    """Insert a new task or update an existing one."""
    now = time.time()
    depends_json = json.dumps(depends or [])
    try:
        conn.execute(
            "INSERT INTO tasks (id, chat_key, agent, goal, status, depends, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "  agent=excluded.agent, goal=excluded.goal, "
            "  depends=excluded.depends, updated_at=excluded.updated_at",
            (task_id, chat_key, agent, goal, status, depends_json, now, now),
        )
        conn.commit()
    except sqlite3.Error:
        logger.exception("task_upsert failed for %s", task_id)


def task_update(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    status: str | None = None,
    note: str | None = None,
    result: str | None = None,
    block_reason: str | None = None,
) -> None:
    """Update mutable fields on a task."""
    now = time.time()
    sets: list[str] = ["updated_at = ?"]
    params: list[Any] = [now]
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if note is not None:
        sets.append("note = ?")
        params.append(note)
    if result is not None:
        sets.append("result = ?")
        params.append(result)
    if block_reason is not None:
        sets.append("block_reason = ?")
        params.append(block_reason)
    params.append(task_id)
    try:
        conn.execute(
            f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        conn.commit()
    except sqlite3.Error:
        logger.exception("task_update failed for %s", task_id)


def task_get(
    conn: sqlite3.Connection,
    task_id: str,
) -> dict[str, Any] | None:
    """Return a single task dict, or None."""
    row = conn.execute(
        "SELECT id, chat_key, agent, goal, status, depends, note, result, block_reason, created_at, updated_at "
        "FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    if not row:
        return None
    return _row_to_task(row)


def tasks_for_agent(
    conn: sqlite3.Connection,
    chat_key: str,
    agent: str,
) -> list[dict[str, Any]]:
    """Return all tasks assigned to *agent* in *chat_key*."""
    rows = conn.execute(
        "SELECT id, chat_key, agent, goal, status, depends, note, result, block_reason, created_at, updated_at "
        "FROM tasks WHERE chat_key = ? AND agent = ?",
        (chat_key, agent),
    ).fetchall()
    return [_row_to_task(r) for r in rows]


def tasks_for_room(
    conn: sqlite3.Connection,
    chat_key: str,
) -> list[dict[str, Any]]:
    """Return all tasks in a room (for orchestrator overview)."""
    rows = conn.execute(
        "SELECT id, chat_key, agent, goal, status, depends, note, result, block_reason, created_at, updated_at "
        "FROM tasks WHERE chat_key = ?",
        (chat_key,),
    ).fetchall()
    return [_row_to_task(r) for r in rows]


def _row_to_task(row: tuple) -> dict[str, Any]:
    return {
        "id": row[0],
        "chat_key": row[1],
        "agent": row[2],
        "goal": row[3],
        "status": row[4],
        "depends": json.loads(row[5]),
        "note": row[6],
        "result": row[7],
        "block_reason": row[8],
        "created_at": row[9],
        "updated_at": row[10],
    }


# ---------------------------------------------------------------------------
# Room agents
# ---------------------------------------------------------------------------

def room_agent_upsert(
    conn: sqlite3.Connection,
    *,
    chat_key: str,
    agent_name: str,
    platform_id: str = "",
    role: str = "worker",
    status: str = "active",
) -> None:
    """Register an agent in a room."""
    try:
        conn.execute(
            "INSERT INTO room_agents (chat_key, agent_name, platform_id, role, status) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(chat_key, agent_name) DO UPDATE SET "
            "  platform_id=excluded.platform_id, role=excluded.role, status=excluded.status",
            (chat_key, agent_name, platform_id, role, status),
        )
        conn.commit()
    except sqlite3.Error:
        logger.exception("room_agent_upsert failed")


def room_agents(
    conn: sqlite3.Connection,
    chat_key: str,
) -> list[dict[str, str]]:
    """Return all agents registered for a room."""
    rows = conn.execute(
        "SELECT agent_name, platform_id, role, status "
        "FROM room_agents WHERE chat_key = ?",
        (chat_key,),
    ).fetchall()
    return [
        {"agent_name": r[0], "platform_id": r[1], "role": r[2], "status": r[3]}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Known bots
# ---------------------------------------------------------------------------

def bot_known(
    conn: sqlite3.Connection,
    platform: str,
    user_id: str,
) -> bool:
    """Check if a user_id is a known bot."""
    row = conn.execute(
        "SELECT 1 FROM known_bots WHERE platform = ? AND user_id = ?",
        (platform, user_id),
    ).fetchone()
    return row is not None


def bot_remember(
    conn: sqlite3.Connection,
    platform: str,
    user_id: str,
    bot_name: str = "",
) -> None:
    """Record a user_id as a known bot."""
    try:
        conn.execute(
            "INSERT OR IGNORE INTO known_bots (platform, user_id, bot_name) "
            "VALUES (?, ?, ?)",
            (platform, user_id, bot_name),
        )
        conn.commit()
    except sqlite3.Error:
        logger.exception("bot_remember failed")


# ---------------------------------------------------------------------------
# Room state (activation tracking)
# ---------------------------------------------------------------------------

def room_get_state(
    conn: sqlite3.Connection,
    chat_key: str,
) -> str:
    """Return the tribunal state for a room: 'inactive', 'active', or 'suspended'.

    Returns 'inactive' for rooms with no row.
    """
    row = conn.execute(
        "SELECT status FROM room_state WHERE chat_key = ?",
        (chat_key,),
    ).fetchone()
    return row[0] if row else "inactive"


def room_activate(
    conn: sqlite3.Connection,
    chat_key: str,
    activated_by: str = "",
) -> None:
    """Mark a room as tribunal-active.

    Sets status='active' and records who activated it and when.
    """
    now = time.time()
    try:
        conn.execute(
            "INSERT INTO room_state (chat_key, status, activated_by, activated_at, updated_at) "
            "VALUES (?, 'active', ?, ?, ?) "
            "ON CONFLICT(chat_key) DO UPDATE SET "
            "  status='active', activated_by=excluded.activated_by, "
            "  activated_at=excluded.activated_at, updated_at=excluded.updated_at",
            (chat_key, activated_by, now, now),
        )
        conn.commit()
    except sqlite3.Error:
        logger.exception("room_activate failed for %s", chat_key)


def room_deactivate(
    conn: sqlite3.Connection,
    chat_key: str,
) -> None:
    """Suspend tribunal in a room (sets status='suspended')."""
    now = time.time()
    try:
        conn.execute(
            "INSERT INTO room_state (chat_key, status, updated_at) "
            "VALUES (?, 'suspended', ?) "
            "ON CONFLICT(chat_key) DO UPDATE SET status='suspended', updated_at=?",
            (chat_key, now, now),
        )
        conn.commit()
    except sqlite3.Error:
        logger.exception("room_deactivate failed for %s", chat_key)
