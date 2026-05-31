"""Sender classification for the gatekeeper.

Determines whether a message sender is a human, this bot, another bot,
or unknown. Uses platform-native signals for Discord and the known_bots
cache + room_agents for Matrix.

Also exposes the platform check and DM guard.
"""

from __future__ import annotations

import enum
import logging
from typing import Any

from . import config
from .chatkey import derive_chat_key, is_dm_event
from . import db

logger = logging.getLogger("tribunal.detection")


class SenderType(enum.Enum):
    HUMAN = "human"
    SELF_BOT = "self_bot"
    OTHER_BOT = "other_bot"
    UNKNOWN = "unknown"


def classify_sender(
    event: Any,
    gateway: Any,
    conn: Any,
) -> SenderType:
    """Classify the sender of *event*.

    Uses:
      - Discord: source.is_bot flag
      - Matrix: known_bots table + room_agents + own user_id
    """
    source = getattr(event, "source", None)
    if source is None:
        return SenderType.UNKNOWN

    platform = _platform_str(source)
    user_id = str(getattr(source, "user_id", "") or "")
    user_name = getattr(source, "user_name", "") or ""

    # --- Discord: native is_bot ---
    if getattr(source, "is_bot", False):
        if _is_self_discord(source, gateway):
            return SenderType.SELF_BOT
        return SenderType.OTHER_BOT

    # --- Matrix: no native is_bot, use heuristics ---
    if platform == "matrix":
        if _is_self_matrix(source, gateway):
            return SenderType.SELF_BOT
        if db.bot_known(conn, "matrix", user_id):
            return SenderType.OTHER_BOT
        # Check if sender is a registered room agent
        chat_key = derive_chat_key(event)
        agents = db.room_agents(conn, chat_key)
        for agent in agents:
            if agent.get("platform_id") == user_id or agent.get("agent_name") == user_name:
                db.bot_remember(conn, "matrix", user_id, user_name)
                return SenderType.OTHER_BOT

    return SenderType.HUMAN


def _is_self_discord(source: Any, gateway: Any) -> bool:
    """Check if the Discord sender is our own bot."""
    try:
        from gateway.config import Platform
        adapter = gateway.adapters.get(Platform.DISCORD) if hasattr(gateway, "adapters") else None
        if adapter and hasattr(adapter, "_client") and adapter._client.user:
            return str(source.user_id) == str(adapter._client.user.id)
    except Exception:
        pass
    return False


def _is_self_matrix(source: Any, gateway: Any) -> bool:
    """Check if the Matrix sender is our own bot."""
    try:
        from gateway.config import Platform
        adapter = gateway.adapters.get(Platform.MATRIX) if hasattr(gateway, "adapters") else None
        if adapter and hasattr(adapter, "_user_id"):
            own_id = str(adapter._user_id)
            sender_id = str(source.user_id)
            if own_id and sender_id and own_id == sender_id:
                return True
    except Exception:
        pass
    return False


def _platform_str(source: Any) -> str:
    """Return lowercase platform name from source."""
    platform = getattr(source, "platform", None)
    if platform is None:
        return ""
    return str(platform.value if hasattr(platform, "value") else platform).lower()


def platform_name(event: Any) -> str:
    """Extract lowercase platform name from an event."""
    source = getattr(event, "source", None)
    if source is None:
        return ""
    return _platform_str(source)


def is_supported_platform(event: Any) -> bool:
    """Check if tribunal handles this platform (Discord or Matrix)."""
    p = platform_name(event)
    if p in config.DISABLED_PLATFORMS:
        return False
    return p in ("discord", "matrix")
