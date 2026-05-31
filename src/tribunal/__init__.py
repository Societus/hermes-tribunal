"""Tribunal -- multi-agent collaboration via chat-room message bus.

Registers three Hermes plugin hooks:
  - pre_gateway_dispatch: gatekeeper (classify, absorb, dispatch)
  - pre_llm_call: context assembler (inject history + tasks)
  - post_llm_call: persistence (write to local SQLite)

Plus one command hook:
  - command:tribunal: first-use setup and room management

DMs are never intercepted. Only room messages are processed.
Rooms are inactive by default -- tribunal dispatch activates when
a HELLO or ASSIGN marker is observed.
"""

from __future__ import annotations

import logging
from typing import Any

from . import config

logger = logging.getLogger("tribunal")


def register(ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    """Plugin entry point. Returns hook registrations."""
    logger.info(
        "tribunal registering hooks (agent=%s, role=%s, db=%s)",
        config.AGENT_ID, config.ROLE, config.DB_PATH,
    )
    return {
        "pre_gateway_dispatch": _on_pre_gateway_dispatch,
        "pre_llm_call": _on_pre_llm_call,
        "post_llm_call": _on_post_llm_call,
        "command:tribunal": _on_command_tribunal,
    }


# ---------------------------------------------------------------------------
# Hook stubs -- will be delegated to real implementations
# ---------------------------------------------------------------------------

def _on_pre_gateway_dispatch(**kwargs) -> dict[str, str] | None:
    """Gatekeeper: classify incoming messages, absorb context, dispatch."""
    from .hooks.dispatch import handle
    return handle(**kwargs)


def _on_pre_llm_call(**kwargs) -> dict[str, str] | str | None:
    """Context assembler: inject room history + task state."""
    from .hooks.context import handle
    return handle(**kwargs)


def _on_post_llm_call(**kwargs) -> None:
    """Persistence: write response to local SQLite."""
    from .hooks.persist import handle
    return handle(**kwargs)


def _on_command_tribunal(**kwargs) -> dict[str, str] | None:
    """Slash command: first-use setup and room management."""
    from .hooks.commands import handle_tribunal_command
    return handle_tribunal_command(**kwargs)
