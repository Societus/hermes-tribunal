"""Async reaction scheduling from synchronous hook context.

All Hermes adapter reaction methods are async, but plugin hooks run
synchronously. This module bridges the gap via asyncio.ensure_future().
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from . import config

logger = logging.getLogger("tribunal.reactions")


def schedule_reaction(
    gateway: Any,
    event: Any,
    emoji: str | None = None,
) -> None:
    """Schedule an emoji reaction on *event* via the platform adapter.

    Non-blocking; failures are silently logged.
    """
    emoji = emoji or config.WATCHED_EMOJI
    source = getattr(event, "source", None)
    if source is None:
        return

    platform = _platform(source)
    adapter = _get_adapter(gateway, platform)
    if adapter is None:
        logger.debug("no adapter for platform %s, skipping reaction", platform)
        return

    async def _do_react() -> None:
        try:
            chat_id = getattr(source, "chat_id", "")
            message_id = getattr(event, "message_id", "")

            if not message_id:
                return

            if platform == "matrix":
                await adapter._send_reaction(chat_id, message_id, emoji)
            elif platform == "discord":
                # Discord needs the message object, fetch by channel + id
                channel = adapter._client.get_channel(int(chat_id))
                if channel:
                    msg = await channel.fetch_message(int(message_id))
                    if msg:
                        await adapter._add_reaction(msg, emoji)
        except Exception as exc:
            logger.debug("reaction failed: %s", exc)

    _schedule_coro(_do_react())


def _schedule_coro(coro: Any) -> None:
    """Schedule a coroutine on the running event loop."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(coro, loop=loop)
    except RuntimeError:
        pass


def _platform(source: Any) -> str:
    p = getattr(source, "platform", None)
    if p is None:
        return ""
    return str(p.value if hasattr(p, "value") else p).lower()


def _get_adapter(gateway: Any, platform: str) -> Any | None:
    """Get the platform adapter from the gateway."""
    try:
        from gateway.config import Platform
        platform_enum = {
            "discord": Platform.DISCORD,
            "matrix": Platform.MATRIX,
            "telegram": Platform.TELEGRAM,
        }.get(platform)
        if platform_enum and hasattr(gateway, "adapters"):
            return gateway.adapters.get(platform_enum)
    except Exception:
        pass
    return None
