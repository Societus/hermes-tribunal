"""pre_gateway_dispatch hook -- the gatekeeper.

Classifies incoming room messages, absorbs context, handles tribunal
protocol messages, and dispatches orchestrator decomposition.

DMs are never intercepted -- they always pass through.

Decision matrix (rooms only):
  Tribunal ASSIGN for me      -> write task, ALLOW
  Tribunal ASSIGN for other   -> write task (dep tracking), SKIP
  Tribunal DONE/PROGRESS/...  -> update task, SKIP
  Bot message                 -> absorb, SKIP
  Human, multi-mention, orchestrator -> decompose + ASSIGN, SKIP
  Human, mentions me only     -> ALLOW
  Human, no mention           -> absorb, react, SKIP
  Human, mentions others      -> absorb, SKIP
  DM                          -> return None (pass through)
"""

from __future__ import annotations

import logging
from typing import Any

from .. import config
from .. import db
from ..chatkey import derive_chat_key, is_dm_event
from ..detection import (
    SenderType,
    classify_sender,
    is_supported_platform,
    platform_name,
)
from ..mentions import parse_mentions
from ..protocol import classify_message, extract_task_updates, has_tribunal_markers
from ..reactions import schedule_reaction

logger = logging.getLogger("tribunal.dispatch")


def handle(**kwargs) -> dict[str, str] | None:
    """Gatekeeper entry point."""
    event = kwargs.get("event")
    gateway = kwargs.get("gateway")
    session_store = kwargs.get("session_store")

    if event is None:
        return None

    # --- Platform gate ---
    if not is_supported_platform(event):
        return None

    # --- DM gate: NEVER intercept DMs ---
    if is_dm_event(event):
        return None

    # --- Resolve helpers ---
    source = getattr(event, "source", None)
    text = getattr(event, "text", "") or ""
    chat_key = derive_chat_key(event)
    platform = platform_name(event)
    conn = db.get_conn()

    # --- Tribunal protocol messages first ---
    if has_tribunal_markers(text):
        return _handle_tribunal_message(event, conn, chat_key, text, platform)

    # --- Classify sender ---
    sender_type = classify_sender(event, gateway, conn)

    # --- Bot messages: absorb and skip ---
    if sender_type in (SenderType.SELF_BOT, SenderType.OTHER_BOT):
        sender_name = getattr(source, "user_name", "") or "bot"
        db.write_message(
            conn,
            chat_key=chat_key,
            sender=sender_name,
            sender_type="bot",
            text=text,
            platform=platform,
            message_id=getattr(event, "message_id", "") or "",
        )
        return {"action": "skip", "reason": "bot-absorb"}

    # --- Human messages ---
    mentions = parse_mentions(event, gateway, conn)

    # Multi-mention: orchestrator decomposition
    if mentions.is_multi_mention and config.get_role(chat_key) == "orchestrator":
        return _handle_orchestrator_setup(event, conn, chat_key, text, platform, gateway, mentions)

    # Single mention of this agent: allow normal response
    if mentions.mentions_self:
        db.write_message(
            conn,
            chat_key=chat_key,
            sender=getattr(source, "user_name", "") or "human",
            sender_type="human",
            text=text,
            platform=platform,
            message_id=getattr(event, "message_id", "") or "",
        )
        return {"action": "allow"}

    # No mention of this agent: absorb as context
    db.write_message(
        conn,
        chat_key=chat_key,
        sender=getattr(source, "user_name", "") or "human",
        sender_type="human",
        text=text,
        platform=platform,
        message_id=getattr(event, "message_id", "") or "",
    )
    schedule_reaction(gateway, event)
    return {"action": "skip", "reason": "context-absorb"}


def _handle_tribunal_message(
    event: Any,
    conn: Any,
    chat_key: str,
    text: str,
    platform: str,
) -> dict[str, str] | None:
    """Handle an incoming tribunal protocol message.

    - ASSIGN for this agent: write task, ALLOW (agent should respond)
    - ASSIGN for other agent: write task for dep tracking, SKIP
    - DONE/PROGRESS/BLOCK/FAIL: update task, SKIP
    """
    source = getattr(event, "source", None)
    updates = extract_task_updates(text)

    for upd in updates:
        t_type = upd.get("type", "")
        task_id = upd.get("id", "")
        agent = upd.get("agent", "")

        if t_type == "ASSIGN":
            # Upsert the task regardless of who it's for (dep tracking)
            db.task_upsert(
                conn,
                task_id=task_id,
                chat_key=chat_key,
                agent=agent,
                goal=upd.get("goal", ""),
                depends=upd.get("depends"),
            )
            # Register the agent in the room
            db.room_agent_upsert(
                conn,
                chat_key=chat_key,
                agent_name=agent,
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

    # Write the protocol message to history
    tribunal_type = classify_message(text)
    sender_name = getattr(source, "user_name", "") if source else "bot"
    db.write_message(
        conn,
        chat_key=chat_key,
        sender=sender_name or "bot",
        sender_type="bot",
        text=text,
        platform=platform,
        message_id=getattr(event, "message_id", "") or "",
        tribunal=tribunal_type,
    )

    # ALLOW if any ASSIGN targets this agent
    for upd in updates:
        if upd.get("type") == "ASSIGN" and upd.get("agent") == config.AGENT_ID:
            return {"action": "allow"}

    return {"action": "skip", "reason": f"tribunal-{tribunal_type}"}


def _handle_orchestrator_setup(
    event: Any,
    conn: Any,
    chat_key: str,
    text: str,
    platform: str,
    gateway: Any,
    mentions: Any,
) -> dict[str, str]:
    """Handle a multi-mention setup message as the orchestrator.

    Writes the human message to history and returns ALLOW so the
    orchestrator's LLM turn can decompose and emit ASSIGN markers
    in its response. The post_llm_call hook extracts those markers.

    Also registers all mentioned agents in room_agents.
    """
    source = getattr(event, "source", None)

    # Register all mentioned agents
    for agent_name in mentions.mentioned_agents:
        db.room_agent_upsert(
            conn,
            chat_key=chat_key,
            agent_name=agent_name,
            role="worker" if agent_name != config.AGENT_ID else "orchestrator",
        )

    # Write the setup message to history
    db.write_message(
        conn,
        chat_key=chat_key,
        sender=getattr(source, "user_name", "") or "human",
        sender_type="human",
        text=text,
        platform=platform,
        message_id=getattr(event, "message_id", "") or "",
    )

    # Allow the orchestrator to respond (it will decompose via LLM)
    return {"action": "allow"}
