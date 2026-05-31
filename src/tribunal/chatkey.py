"""chat_key derivation from incoming events.

chat_key uniquely identifies a room/channel across platforms. It is
derived from the event's SessionSource fields, NOT from session_id
string parsing.

Format:
  Discord channel : {channel_id}
  Discord thread  : {channel_id}:{thread_id}
  Matrix room     : {room_id}
  Matrix thread   : {room_id}:{event_id}
"""

from __future__ import annotations

from typing import Any


def derive_chat_key(event: Any) -> str:
    """Derive a chat_key from an event's source fields.

    Uses source.chat_id as the base, appending source.thread_id
    (separated by ':') if present.
    """
    source = getattr(event, "source", None)
    if source is None:
        return ""

    chat_id = getattr(source, "chat_id", "") or ""
    thread_id = getattr(source, "thread_id", None)

    if thread_id:
        return f"{chat_id}:{thread_id}"
    return chat_id


def from_session_id(session_id: str) -> str:
    """Best-effort chat_key extraction from a Hermes session_id.

    Session IDs follow: agent:main:{platform}:{chat_type}:{chat_id}[:{thread_id}]
    This is a fallback when the event source is not available (e.g. in
    pre_llm_call / post_llm_call hooks).
    """
    parts = session_id.split(":")
    # Minimum: agent:main:platform:chat_type:chat_id (5 parts)
    if len(parts) < 5:
        return session_id  # give up, return as-is
    chat_id = parts[4]
    thread_id = parts[5] if len(parts) > 5 else None
    if thread_id:
        return f"{chat_id}:{thread_id}"
    return chat_id


def is_dm_event(event: Any) -> bool:
    """Check whether the event came from a DM/private chat.

    Tribunal never intercepts DMs. This is the hard boundary.
    """
    source = getattr(event, "source", None)
    if source is None:
        return False
    chat_type = getattr(source, "chat_type", "") or ""
    return chat_type in ("dm", "private")


def is_dm_session(session_id: str) -> bool:
    """Check whether a session_id indicates a DM session.

    Session IDs contain chat_type at index 3.
    """
    parts = session_id.split(":")
    if len(parts) >= 4:
        return parts[3] in ("dm", "private")
    return False
