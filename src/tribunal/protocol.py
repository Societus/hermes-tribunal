"""Tribunal structured message protocol.

Parses and formats [TRIBUNAL:TYPE key=value ...] markers that agents
use to coordinate via the chat room.

Supported types:
  ASSIGN    - orchestrator assigns a task to a worker
  PROGRESS  - worker reports progress on a task
  DONE      - worker reports task completion
  BLOCK     - worker needs human input to continue
  FAIL      - worker reports task failure

Marker format:
  [TRIBUNAL:ASSIGN id=T-001 agent=researcher goal="research auth patterns" depends="[]"]
  [TRIBUNAL:DONE id=T-001 agent=researcher result="JWT+RS256 recommended"]
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import config

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

#: Matches the outer bracket and captures type + params string.
#: Uses a tempered greedy token to skip over quoted strings
#: (so "]" inside depends="[]" does not end the match early).
_MARKER_RE = re.compile(
    r'\[TRIBUNAL:(ASSIGN|PROGRESS|DONE|BLOCK|FAIL|HELLO)\s+((?:[^"\]]|"[^"]*")*)\]'
)

#: Matches key="value with spaces" or key=nowhitespace
_KV_RE = re.compile(r'(\w+)="([^"]*)"|(\w+)=(\S+)')


def parse_markers(text: str) -> list[dict[str, str]]:
    """Extract all tribunal markers from *text*.

    Returns a list of dicts, each with at least a 'type' key plus
    whatever key=value pairs were inside the brackets.

    Example return::

        [{'type': 'ASSIGN', 'id': 'T-001', 'agent': 'researcher',
          'goal': 'research auth', 'depends': '[]'}]
    """
    results: list[dict[str, str]] = []
    for match in _MARKER_RE.finditer(text):
        msg_type = match.group(1)
        params_str = match.group(2)
        params: dict[str, str] = {"type": msg_type}
        for kv in _KV_RE.finditer(params_str):
            if kv.group(1):  # key="value"
                params[kv.group(1)] = kv.group(2)
            else:            # key=value
                params[kv.group(3)] = kv.group(4)
        results.append(params)
    return results


def has_tribunal_markers(text: str) -> bool:
    """Quick check whether *text* contains any tribunal marker."""
    return config.TRIBUNAL_MARKER in text


def parse_depends(depends_str: str) -> list[str]:
    """Parse a depends value (JSON array or comma-separated) into a list."""
    if not depends_str:
        return []
    try:
        val = json.loads(depends_str)
        if isinstance(val, list):
            return [str(v) for v in val]
    except (json.JSONDecodeError, ValueError):
        pass
    # fallback: comma-separated
    return [s.strip() for s in depends_str.split(",") if s.strip()]


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _esc(value: str) -> str:
    """Quote a value if it contains spaces, quotes, or brackets."""
    if any(c in value for c in (' ', '"', '[', ']')):
        return f'"{value}"'
    return value


def format_assign(
    task_id: str,
    agent: str,
    goal: str,
    depends: list[str] | None = None,
) -> str:
    """Format an ASSIGN marker.

    Depends are encoded as comma-separated task IDs (no brackets, no quotes)
    to avoid ambiguity with the marker bracket syntax.
    """
    dep_str = ",".join(depends) if depends else ""
    return (
        f"[TRIBUNAL:ASSIGN id={task_id} agent={agent} "
        f"goal={_esc(goal)} depends={_esc(dep_str)}]"
    )


def format_progress(
    task_id: str,
    agent: str,
    note: str,
) -> str:
    """Format a PROGRESS marker."""
    return (
        f"[TRIBUNAL:PROGRESS id={task_id} agent={agent} "
        f"note={_esc(note)}]"
    )


def format_done(
    task_id: str,
    agent: str,
    result: str,
) -> str:
    """Format a DONE marker."""
    return (
        f"[TRIBUNAL:DONE id={task_id} agent={agent} "
        f"result={_esc(result)}]"
    )


def format_block(
    task_id: str,
    agent: str,
    reason: str,
) -> str:
    """Format a BLOCK marker."""
    return (
        f"[TRIBUNAL:BLOCK id={task_id} agent={agent} "
        f"reason={_esc(reason)}]"
    )


def format_fail(
    task_id: str,
    agent: str,
    reason: str,
) -> str:
    """Format a FAIL marker."""
    return (
        f"[TRIBUNAL:FAIL id={task_id} agent={agent} "
        f"reason={_esc(reason)}]"
    )


def format_hello(
    agent: str,
    role: str,
    capabilities: str = "",
    roster: dict[str, str] | None = None,
) -> str:
    """Format a HELLO marker.

    Emitted when an agent first activates in a tribunal room.
    Can optionally include a roster of all participants, which is the
    preferred first-use pattern (orchestrator declares everyone at once).

    roster is {agent_name: role, ...}. If omitted, only the emitting
    agent is registered (standalone HELLO for late joins).
    """
    parts = f"[TRIBUNAL:HELLO agent={agent} role={role}"
    if capabilities:
        parts += f" capabilities={_esc(capabilities)}"
    if roster:
        # Format: "name:role,name:role,..." consistent with depends= style
        roster_str = ",".join(f"{name}:{r}" for name, r in roster.items())
        parts += f" roster={_esc(roster_str)}"
    return parts + "]"


def parse_roster(roster_str: str) -> list[dict[str, str]]:
    """Parse a roster string into a list of {agent_name, role} dicts.

    Format: "name:role,name:role,..."
    Entries without a colon default to role='worker'.
    """
    if not roster_str:
        return []
    entries: list[dict[str, str]] = []
    for part in roster_str.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            name, role = part.split(":", 1)
            entries.append({"agent_name": name.strip(), "role": role.strip()})
        else:
            entries.append({"agent_name": part.strip(), "role": "worker"})
    return entries


# ---------------------------------------------------------------------------
# High-level helpers
# ---------------------------------------------------------------------------

def classify_message(text: str) -> str:
    """Return the tribunal type of the first marker in *text*, or ''."""
    if config.TRIBUNAL_MARKER not in text:
        return ""
    markers = parse_markers(text)
    return markers[0]["type"] if markers else ""


def extract_task_updates(text: str) -> list[dict[str, Any]]:
    """Parse markers and return a list of task-update dicts.

    Each dict has at least: type, id, agent.
    Depending on type, additional keys: goal, depends, note, result, reason,
    role, capabilities.
    """
    markers = parse_markers(text)
    updates: list[dict[str, Any]] = []
    for m in markers:
        entry: dict[str, Any] = {
            "type": m["type"],
            "id": m.get("id", ""),
            "agent": m.get("agent", ""),
        }
        if m["type"] == "ASSIGN":
            entry["goal"] = m.get("goal", "")
            entry["depends"] = parse_depends(m.get("depends", "[]"))
        elif m["type"] == "PROGRESS":
            entry["note"] = m.get("note", "")
        elif m["type"] == "DONE":
            entry["result"] = m.get("result", "")
        elif m["type"] == "BLOCK":
            entry["reason"] = m.get("reason", "")
        elif m["type"] == "FAIL":
            entry["reason"] = m.get("reason", "")
        elif m["type"] == "HELLO":
            entry["role"] = m.get("role", "worker")
            entry["capabilities"] = m.get("capabilities", "")
            roster_raw = m.get("roster", "")
            entry["roster"] = parse_roster(roster_raw)
        updates.append(entry)
    return updates
