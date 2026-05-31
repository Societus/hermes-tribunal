"""Mention parsing for Discord and Matrix.

Extracts @mentions from message text and classifies them relative to
the known agent roster for the room.

Discord: uses event.raw_message.mentions (discord.py user objects).
Matrix: parses @displayname patterns in text, cross-references with
         room_agents table.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from . import config
from . import db
from .chatkey import derive_chat_key

logger = logging.getLogger("tribunal.mentions")


@dataclass
class MentionResult:
    """Result of mention parsing."""
    mentioned_agents: list[str] = field(default_factory=list)
    is_multi_mention: bool = False
    mentions_self: bool = False
    clean_text: str = ""


def parse_mentions(
    event: Any,
    gateway: Any,
    conn: Any,
) -> MentionResult:
    """Extract and classify mentions in *event*.

    Returns a MentionResult with:
      - mentioned_agents: list of agent names that were mentioned
      - is_multi_mention: True if 2+ agents were mentioned
      - mentions_self: True if this agent was among the mentioned
      - clean_text: the message text with mention patterns stripped
    """
    source = getattr(event, "source", None)
    if source is None:
        return MentionResult(clean_text=getattr(event, "text", ""))

    text = getattr(event, "text", "") or ""
    platform = _platform_str(source)

    if platform == "discord":
        return _parse_discord(event, conn, text)
    elif platform == "matrix":
        return _parse_matrix(event, conn, text)
    else:
        return MentionResult(clean_text=text)


def _parse_discord(event: Any, conn: Any, text: str) -> MentionResult:
    """Parse Discord mentions from raw_message.mentions."""
    chat_key = derive_chat_key(event)
    agents = db.room_agents(conn, chat_key)
    # Build a map of platform_id -> agent_name
    id_map: dict[str, str] = {}
    for a in agents:
        pid = a.get("platform_id", "")
        if pid:
            id_map[str(pid)] = a["agent_name"]

    mentioned: list[str] = []

    # Try raw_message.mentions first (discord.py user objects)
    raw_msg = getattr(event, "raw_message", None)
    if raw_msg and hasattr(raw_msg, "mentions"):
        for user in raw_msg.mentions:
            uid = str(user.id)
            name = id_map.get(uid)
            if name:
                mentioned.append(name)

    # Strip <@user_id> and <@!user_id> patterns from text
    clean = re.sub(r"<@!?\d+>", "", text).strip()

    return _build_result(mentioned, clean)


def _parse_matrix(event: Any, conn: Any, text: str) -> MentionResult:
    """Parse Matrix mentions from text and event content."""
    chat_key = derive_chat_key(event)
    agents = db.room_agents(conn, chat_key)
    agent_names = {a["agent_name"] for a in agents}

    mentioned: list[str] = []

    # Check m.mentions.user_ids in event content (if available)
    raw_msg = getattr(event, "raw_message", None)
    if raw_msg and isinstance(raw_msg, dict):
        content = raw_msg.get("content", {})
        if isinstance(content, dict):
            m_mentions = content.get("m.mentions", {})
            if isinstance(m_mentions, dict):
                user_ids = m_mentions.get("user_ids", [])
                for uid in user_ids:
                    # Try matching against room_agents platform_id
                    for a in agents:
                        if a.get("platform_id") == uid and a["agent_name"] not in mentioned:
                            mentioned.append(a["agent_name"])

    # Fallback: parse @displayname patterns in text
    for name in agent_names:
        pattern = re.compile(rf"@{re.escape(name)}\b", re.IGNORECASE)
        if pattern.search(text) and name not in mentioned:
            mentioned.append(name)

    # Strip @displayname patterns from text
    clean = text
    for name in mentioned:
        clean = re.sub(rf"@{re.escape(name)}\b", "", clean, flags=re.IGNORECASE).strip()

    return _build_result(mentioned, clean)


def _build_result(mentioned: list[str], clean_text: str) -> MentionResult:
    """Build a MentionResult from the list of mentioned agent names."""
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for name in mentioned:
        if name not in seen:
            seen.add(name)
            unique.append(name)

    return MentionResult(
        mentioned_agents=unique,
        is_multi_mention=len(unique) >= 2,
        mentions_self=config.AGENT_ID in unique,
        clean_text=clean_text,
    )


def _platform_str(source: Any) -> str:
    platform = getattr(source, "platform", None)
    if platform is None:
        return ""
    return str(platform.value if hasattr(platform, "value") else platform).lower()
