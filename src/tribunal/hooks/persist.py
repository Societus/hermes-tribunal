"""post_llm_call hook -- persistence + protocol extraction.

Writes the agent's response to local SQLite and extracts tribunal
protocol markers to update the local task state.

DM sessions are not persisted. Only room responses are stored.
"""

from __future__ import annotations

import logging
from typing import Any

from .. import config
from .. import db
from ..chatkey import from_session_id, is_dm_session
from ..protocol import extract_task_updates, has_tribunal_markers

logger = logging.getLogger("tribunal.persist")


def handle(**kwargs) -> None:
    """Persistence entry point. Fire-and-forget side effects."""
    session_id = kwargs.get("session_id", "")
    user_message = kwargs.get("user_message", "") or ""
    assistant_response = kwargs.get("assistant_response", "") or ""
    platform = kwargs.get("platform", "")

    # --- Platform gate ---
    if platform not in ("discord", "matrix"):
        return

    # --- DM gate ---
    if is_dm_session(session_id):
        return

    chat_key = from_session_id(session_id)
    if not chat_key:
        return

    conn = db.get_conn()

    # --- Write user trigger message (dedup by message_id is handled by write_message) ---
    db.write_message(
        conn,
        chat_key=chat_key,
        sender="human",
        sender_type="human",
        text=user_message,
        platform=platform,
    )

    # --- Determine tribunal type from response ---
    tribunal_type = ""
    if has_tribunal_markers(assistant_response):
        updates = extract_task_updates(assistant_response)
        tribunal_type = updates[0]["type"] if updates else ""

        for upd in updates:
            t_type = upd.get("type", "")
            task_id = upd.get("id", "")

            if t_type == "ASSIGN":
                # Orchestrator creating tasks
                db.task_upsert(
                    conn,
                    task_id=task_id,
                    chat_key=chat_key,
                    agent=upd.get("agent", ""),
                    goal=upd.get("goal", ""),
                    depends=upd.get("depends"),
                )
                db.room_agent_upsert(
                    conn,
                    chat_key=chat_key,
                    agent_name=upd.get("agent", ""),
                    role="worker",
                )

            elif t_type == "PROGRESS":
                db.task_update(
                    conn,
                    task_id=task_id,
                    status="in_progress",
                    note=upd.get("note", ""),
                )

            elif t_type == "DONE":
                db.task_update(
                    conn,
                    task_id=task_id,
                    status="done",
                    result=upd.get("result", ""),
                )

            elif t_type == "BLOCK":
                db.task_update(
                    conn,
                    task_id=task_id,
                    status="blocked",
                    block_reason=upd.get("reason", ""),
                )

            elif t_type == "FAIL":
                db.task_update(
                    conn,
                    task_id=task_id,
                    status="failed",
                    block_reason=upd.get("reason", ""),
                )

    # --- Write agent response ---
    db.write_message(
        conn,
        chat_key=chat_key,
        sender=config.BOT_NAME,
        sender_type="self",
        text=assistant_response,
        platform=platform,
        tribunal=tribunal_type,
    )

    # --- Prune old messages ---
    db.prune(conn, chat_key)
