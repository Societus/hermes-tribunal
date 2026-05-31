"""Tribunal plugin configuration.

Reads environment variables and resolves paths. All config is resolved
once at module load time and cached as module-level constants.
"""

import os
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    val = _env(name)
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Agent identity
# ---------------------------------------------------------------------------

#: Unique agent name used in tribunal protocol messages.
#: Falls back to HERMES_PROFILE or "tribunal-agent".
AGENT_ID: str = (
    _env("TRIBUNAL_AGENT_ID")
    or _env("HERMES_PROFILE")
    or "tribunal-agent"
)

#: Display name for this agent in injected room history.
BOT_NAME: str = _env("TRIBUNAL_BOT_NAME") or AGENT_ID

# ---------------------------------------------------------------------------
# Role
# ---------------------------------------------------------------------------

#: Default role for this agent: "worker" or "orchestrator".
ROLE: str = _env("TRIBUNAL_ROLE") or "worker"

#: Default behavior for inactive rooms. When "silent", the agent only
#: responds to DMs and explicit @mentions in rooms where tribunal has
#: not been activated. When "passive", it follows Hermes core routing.
INACTIVE_ROOM_MODE: str = _env("TRIBUNAL_INACTIVE_MODE") or "silent"

#: Per-room role overrides. Format: "chat_key=role,chat_key=role"
ROOM_ROLES_STR: str = _env("TRIBUNAL_ROOM_ROLES")

def _parse_room_roles(raw: str) -> dict[str, str]:
    """Parse 'key=role,key=role' into a dict."""
    out: dict[str, str] = {}
    if not raw:
        return out
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" not in pair:
            continue
        key, role = pair.split("=", 1)
        out[key.strip()] = role.strip()
    return out

ROOM_ROLES: dict[str, str] = _parse_room_roles(ROOM_ROLES_STR)


def get_role(chat_key: str) -> str:
    """Return the effective role for this agent in the given room."""
    return ROOM_ROLES.get(chat_key, ROLE)

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

#: Path to the local tribunal SQLite database.
#: Resolved from TRIBUNAL_DB_PATH env var, or $HERMES_HOME/tribunal/tribunal.db.
DB_PATH: str = (
    _env("TRIBUNAL_DB_PATH")
    or str(Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
           / "tribunal" / "tribunal.db")
)

# ---------------------------------------------------------------------------
# Behaviour tuning
# ---------------------------------------------------------------------------

#: Number of recent messages to include in injected context.
HISTORY_COUNT: int = _env_int("TRIBUNAL_HISTORY_COUNT", 30)

#: Hours to retain absorbed messages before pruning.
PRUNE_HOURS: int = _env_int("TRIBUNAL_PRUNE_HOURS", 72)

#: Emoji for "absorbed" reactions.
WATCHED_EMOJI: str = _env("TRIBUNAL_WATCHED_EMOJI") or "\U0001f441"

#: Platforms where tribunal is disabled.
DISABLED_PLATFORMS: set[str] = {
    p.strip().lower()
    for p in _env("TRIBUNAL_DISABLED_PLATFORMS").split(",")
    if p.strip()
}

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

TRIBUNAL_MARKER = "[TRIBUNAL:"
TRIBUNAL_TYPES = frozenset({"ASSIGN", "PROGRESS", "DONE", "BLOCK", "FAIL", "HELLO"})

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

import logging

logger = logging.getLogger("tribunal")
