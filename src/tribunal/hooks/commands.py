"""command:tribunal hook -- first-use setup and room management.

Provides a slash command that the agent's operator uses to configure
the agent for tribunal participation in a room.

Usage:
  /tribunal hello                              -- standalone HELLO (self only)
  /tribunal hello name:role name:role ...      -- roster HELLO (declares all)
  /tribunal status                             -- show room state
  /tribunal activate                           -- force-activate room
  /tribunal deactivate                         -- suspend tribunal in room
"""

from __future__ import annotations

import logging
from typing import Any

from .. import config
from .. import db
from ..chatkey import from_session_id, is_dm_session
from ..protocol import format_hello, parse_roster

logger = logging.getLogger("tribunal.commands")


def handle_tribunal_command(**kwargs) -> dict[str, str] | None:
    """Handle the 'tribunal' slash command."""
    session_id = kwargs.get("session_id", "")
    platform = kwargs.get("platform", "")
    args = kwargs.get("args", "").strip()

    if platform not in ("discord", "matrix"):
        return None

    if is_dm_session(session_id):
        return {"action": "respond", "text": "Tribunal commands only work in rooms, not DMs."}

    chat_key = from_session_id(session_id)
    if not chat_key:
        return None

    conn = db.get_conn()
    parts = args.split()
    subcmd = parts[0] if parts else "status"
    rest = parts[1:] if len(parts) > 1 else []

    if subcmd == "hello":
        return _cmd_hello(conn, chat_key, rest)
    elif subcmd == "status":
        return _cmd_status(conn, chat_key)
    elif subcmd == "activate":
        return _cmd_activate(conn, chat_key)
    elif subcmd == "deactivate":
        return _cmd_deactivate(conn, chat_key)
    else:
        return {
            "action": "respond",
            "text": f"Unknown tribunal subcommand: {subcmd}. Use: hello, status, activate, deactivate",
        }


def _cmd_hello(conn: Any, chat_key: str, roster_args: list[str]) -> dict[str, str]:
    """Emit a HELLO marker, activating the room.

    With no args: standalone HELLO (self only).
    With args: roster HELLO declaring all participants.
      Format: name:role name:role ...
      e.g. /tribunal hello sisyphus:worker hermes:researcher
    """
    db.room_activate(conn, chat_key, activated_by=config.AGENT_ID)

    # Always register self
    my_role = config.get_role(chat_key)
    db.room_agent_upsert(
        conn,
        chat_key=chat_key,
        agent_name=config.AGENT_ID,
        role=my_role,
    )

    # Parse roster args if present
    roster: dict[str, str] = {}
    if roster_args:
        for entry in roster_args:
            entry = entry.strip()
            if not entry:
                continue
            if ":" in entry:
                name, role = entry.split(":", 1)
                roster[name.strip()] = role.strip()
            else:
                roster[entry.strip()] = "worker"

        # Register all roster members
        for name, role in roster.items():
            db.room_agent_upsert(
                conn,
                chat_key=chat_key,
                agent_name=name,
                role=role,
            )

    hello_msg = format_hello(
        agent=config.AGENT_ID,
        role=my_role,
        roster=roster or None,
    )

    if roster:
        roster_display = ", ".join(f"{n} ({r})" for n, r in roster.items())
        return {
            "action": "respond",
            "text": (
                f"Tribunal activated for this room. Roster declared.\n"
                f"Participants: {config.AGENT_ID} ({my_role}), {roster_display}\n"
                f"Emitting HELLO:\n{hello_msg}"
            ),
        }
    else:
        return {
            "action": "respond",
            "text": f"Tribunal activated for this room. Emitting HELLO:\n{hello_msg}",
        }


def _cmd_status(conn: Any, chat_key: str) -> dict[str, str]:
    """Show tribunal state for the current room."""
    state = db.room_get_state(conn, chat_key)
    agents = db.room_agents(conn, chat_key)
    agent_list = ", ".join(
        f"{a['agent_name']} ({a['role']})" for a in agents
    ) or "none"

    return {
        "action": "respond",
        "text": (
            f"Tribunal room status: **{state}**\n"
            f"This agent: {config.AGENT_ID} (role: {config.get_role(chat_key)})\n"
            f"Registered agents: {agent_list}"
        ),
    }


def _cmd_activate(conn: Any, chat_key: str) -> dict[str, str]:
    """Force-activate the current room."""
    db.room_activate(conn, chat_key, activated_by=config.AGENT_ID)
    return {
        "action": "respond",
        "text": "Tribunal activated for this room. Other agents will see your messages as tribunal traffic when they contain protocol markers.",
    }


def _cmd_deactivate(conn: Any, chat_key: str) -> dict[str, str]:
    """Suspend tribunal in the current room."""
    db.room_deactivate(conn, chat_key)
    return {
        "action": "respond",
        "text": "Tribunal suspended for this room. The agent will use normal Hermes routing until re-activated.",
    }
