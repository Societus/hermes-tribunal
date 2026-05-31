"""pre_llm_call hook -- context assembler.

Injects tribunal protocol instructions, active task state, room agent
roster, and recent room history into the agent's turn.

DM sessions get no injection. Rooms without active tasks or history
get no injection.
"""

from __future__ import annotations

import logging
from typing import Any

from .. import config
from .. import db
from ..chatkey import from_session_id, is_dm_session
from ..protocol import format_assign

logger = logging.getLogger("tribunal.context")


def handle(**kwargs) -> dict[str, str] | str | None:
    """Context assembler entry point."""
    session_id = kwargs.get("session_id", "")
    platform = kwargs.get("platform", "")

    # --- Platform gate ---
    if platform not in ("discord", "matrix"):
        return None

    # --- DM gate ---
    if is_dm_session(session_id):
        return None

    chat_key = from_session_id(session_id)
    if not chat_key:
        return None

    conn = db.get_conn()
    role = config.get_role(chat_key)

    # --- Assemble context sections ---
    parts: list[str] = []

    # Protocol instructions
    if role == "orchestrator":
        parts.append(_orchestrator_protocol())
    else:
        parts.append(_worker_protocol())

    # Active tasks
    tasks_section = _format_tasks(conn, chat_key, role)
    if tasks_section:
        parts.append(tasks_section)

    # Room agents
    agents_section = _format_agents(conn, chat_key)
    if agents_section:
        parts.append(agents_section)

    # Room history
    history = db.format_history(conn, chat_key)
    if history:
        parts.append(f"[Recent Room History]\n{history}\n[End Room History]")

    if not parts:
        return None

    return "\n\n".join(parts)


def _orchestrator_protocol() -> str:
    """Protocol instructions for the orchestrator agent."""
    return (
        "[Tribunal Protocol]\n"
        f"You are the ORCHESTRATOR agent ({config.AGENT_ID}) for this room.\n"
        "When a human @mentions multiple agents, decompose the request into tasks.\n"
        "For each task, include in your response:\n"
        f'  [TRIBUNAL:ASSIGN id=T-NNN agent=TARGET_AGENT goal="task description" depends="[\\\"T-XXX\\\"]"]\n'
        "Track which tasks are done. Agents watch the room stream for DONE messages\n"
        "and start when their dependencies are met.\n"
        "If the human sends a follow-up, re-evaluate and create new ASSIGN markers.\n"
        "Use unique task IDs (e.g. T-001, T-002, incrementing).\n"
        "[End Tribunal Protocol]"
    )


def _worker_protocol() -> str:
    """Protocol instructions for a worker agent."""
    return (
        "[Tribunal Protocol]\n"
        f"You are agent \"{config.AGENT_ID}\" in a multi-agent collaboration.\n"
        "When you start work on a task, include in your response:\n"
        f'  [TRIBUNAL:PROGRESS id=T-NNN agent={config.AGENT_ID} note="what you are doing"]\n'
        "When you finish a task, include in your response:\n"
        f'  [TRIBUNAL:DONE id=T-NNN agent={config.AGENT_ID} result="summary of findings"]\n'
        "If you need human input, include in your response:\n"
        f'  [TRIBUNAL:BLOCK id=T-NNN agent={config.AGENT_ID} reason="specific question"]\n'
        "If you cannot complete a task, include in your response:\n"
        f'  [TRIBUNAL:FAIL id=T-NNN agent={config.AGENT_ID} reason="what went wrong"]\n'
        "Do NOT start a task until all its dependencies are marked DONE.\n"
        "These markers are visible to all agents in the room.\n"
        "[End Tribunal Protocol]"
    )


def _format_tasks(conn: Any, chat_key: str, role: str) -> str:
    """Format active tasks for context injection."""
    if role == "orchestrator":
        tasks = db.tasks_for_room(conn, chat_key)
    else:
        tasks = db.tasks_for_agent(conn, chat_key, config.AGENT_ID)

    if not tasks:
        return ""

    lines = ["[Active Tasks]"]
    for t in tasks:
        status = t["status"]
        deps = t.get("depends", [])
        dep_str = ", ".join(deps) if deps else "(none)"

        if status in ("done", "failed"):
            lines.append(f"  {t['id']}: \"{t['goal']}\" -- {status.upper()}")
            if t.get("result"):
                lines.append(f"    Result: {t['result']}")
        elif status == "blocked":
            lines.append(f"  {t['id']}: \"{t['goal']}\" -- BLOCKED")
            lines.append(f"    Reason: {t.get('block_reason', 'unknown')}")
        elif status == "in_progress":
            lines.append(f"  {t['id']}: \"{t['goal']}\" -- IN PROGRESS (assigned to {t['agent']})")
            if t.get("note"):
                lines.append(f"    Note: {t['note']}")
        else:
            # assigned or waiting
            can_start = "all deps met" if not deps else f"depends on: {dep_str}"
            lines.append(f"  {t['id']}: \"{t['goal']}\"")
            lines.append(f"    Status: {status} (assigned to {t['agent']})")
            lines.append(f"    Depends on: {dep_str}")

    lines.append("[End Active Tasks]")
    return "\n".join(lines)


def _format_agents(conn: Any, chat_key: str) -> str:
    """Format room agent roster."""
    agents = db.room_agents(conn, chat_key)
    if not agents:
        return ""

    lines = ["[Room Agents]"]
    for a in agents:
        role_str = f" ({a['role']})" if a.get("role") else ""
        self_marker = " (you)" if a["agent_name"] == config.AGENT_ID else ""
        lines.append(f"  {a['agent_name']}{role_str}{self_marker}")
    lines.append("[End Room Agents]")
    return "\n".join(lines)
