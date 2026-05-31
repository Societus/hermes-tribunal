"""pre_gateway_dispatch hook -- the gatekeeper.

Classifies incoming room messages, absorbs context, handles tribunal
protocol messages, and dispatches orchestrator decomposition.

DMs are never intercepted -- they always pass through.

Room activation model:
  Rooms start in 'inactive' state. In inactive rooms, the agent follows
  Hermes core routing (returns None), so normal mention/auth rules apply.
  Once a HELLO or ASSIGN marker is seen (from any agent), the room becomes
  'active' and full tribunal dispatch logic takes over.

  Exception: if this agent is an orchestrator and a human multi-mentions
  agents in an inactive room, the room auto-activates and the orchestrator
  responds with decomposition.

Decision matrix:
  DM                          -> return None (pass through)
  Inactive room, no tribunal markers, no multi-mention of orchestrator
                              -> return None (pass through to core)
  Inactive room, multi-mention + orchestrator -> activate, decompose, ALLOW
  Tribunal HELLO (with or without roster) -> activate room, register all, SKIP
  Tribunal ASSIGN for me      -> activate room, write task, ALLOW
  Tribunal ASSIGN for other   -> activate room, write task (dep tracking), SKIP
  Tribunal DONE/PROGRESS/...  -> update task, SKIP
  Bot message (active room)   -> absorb, SKIP
  Human, multi-mention, orchestrator (active) -> decompose + ASSIGN, ALLOW
  Human, mentions me only     -> ALLOW
  Human, no mention (active)  -> absorb, react, SKIP
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

    # --- Tribunal protocol messages first (these can activate rooms) ---
    if has_tribunal_markers(text):
        return _handle_tribunal_message(event, conn, chat_key, text, platform)

    # --- Room activation check ---
    room_state = db.room_get_state(conn, chat_key)

    if room_state != "active":
        # Inactive room: check if this is an orchestrator multi-mention setup
        # that should auto-activate the room.
        sender_type = classify_sender(event, gateway, conn)
        if sender_type == SenderType.HUMAN:
            mentions = parse_mentions(event, gateway, conn)
            if mentions.is_multi_mention and config.get_role(chat_key) == "orchestrator":
                return _handle_orchestrator_setup(
                    event, conn, chat_key, text, platform, gateway, mentions,
                    auto_activate=True,
                )
        # Not a tribunal trigger: let Hermes core routing handle it.
        return None

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
        return _handle_orchestrator_setup(
            event, conn, chat_key, text, platform, gateway, mentions,
            auto_activate=False,
        )

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

    Any tribunal marker (HELLO, ASSIGN, etc.) activates the room.
    - HELLO: activate room, register emitter + full roster, SKIP
    - ASSIGN for this agent: write task, ALLOW
    - ASSIGN for other agent: write task for dep tracking, SKIP
    - DONE/PROGRESS/BLOCK/FAIL: update task, SKIP
    """
    source = getattr(event, "source", None)
    updates = extract_task_updates(text)

    # Auto-activate the room when any tribunal marker is seen
    sender_name = getattr(source, "user_name", "") if source else ""
    current_state = db.room_get_state(conn, chat_key)
    if current_state != "active":
        db.room_activate(conn, chat_key, activated_by=sender_name)
        logger.info("tribunal activated room %s (triggered by %s)", chat_key, sender_name)

    for upd in updates:
        t_type = upd.get("type", "")
        task_id = upd.get("id", "")
        agent = upd.get("agent", "")

        if t_type == "HELLO":
            # Register the announcing agent
            db.room_agent_upsert(
                conn,
                chat_key=chat_key,
                agent_name=agent,
                role=upd.get("role", "worker"),
            )
            # Remember them as a bot for sender classification
            if source:
                user_id = str(getattr(source, "user_id", "") or "")
                if user_id:
                    db.bot_remember(conn, platform, user_id, agent)

            # Register all roster members if present
            roster = upd.get("roster", [])
            for member in roster:
                db.room_agent_upsert(
                    conn,
                    chat_key=chat_key,
                    agent_name=member["agent_name"],
                    role=member["role"],
                )

        elif t_type == "ASSIGN":
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

    # HELLO never triggers an ALLOW -- it's a presence announcement
    if tribunal_type == "HELLO":
        return {"action": "skip", "reason": "hello-absorb"}

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
    auto_activate: bool = False,
) -> dict[str, str]:
    """Handle a multi-mention setup message as the orchestrator.

    Writes the human message to history and returns ALLOW so the
    orchestrator's LLM turn can decompose and emit ASSIGN markers
    in its response. The post_llm_call hook extracts those markers.

    If auto_activate is True, also activates the room (for the
    first-use case where the room was previously inactive).
    """
    source = getattr(event, "source", None)

    if auto_activate:
        sender_name = getattr(source, "user_name", "") if source else ""
        db.room_activate(conn, chat_key, activated_by=sender_name)
        logger.info(
            "tribunal auto-activated room %s via multi-mention from %s",
            chat_key, sender_name,
        )

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
