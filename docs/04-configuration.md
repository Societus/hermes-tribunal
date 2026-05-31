# Tribunal -- Configuration

## Environment Variables

| Variable                      | Default                              | Description                                    |
|-------------------------------|--------------------------------------|------------------------------------------------|
| `TRIBUNAL_DB_PATH`            | `$HERMES_HOME/tribunal/tribunal.db`  | Local SQLite database path                     |
| `TRIBUNAL_HISTORY_COUNT`      | `30`                                 | Messages per room in injected context          |
| `TRIBUNAL_BOT_NAME`           | *(profile name)*                     | Display name for this agent in history         |
| `TRIBUNAL_ROLE`               | `worker`                             | Default role: `worker` or `orchestrator`       |
| `TRIBUNAL_PRUNE_HOURS`        | `72`                                 | Hours to retain absorbed messages              |
| `TRIBUNAL_WATCHED_EMOJI`      | `\U0001f441` (eye)                   | Emoji for "absorbed" reaction                  |
| `TRIBUNAL_DISABLED_PLATFORMS` | *(empty)*                            | Comma-separated platforms to ignore            |
| `TRIBUNAL_AGENT_ID`           | *(profile name)*                     | Agent identifier used in protocol messages     |
| `TRIBUNAL_ROOM_ROLES`         | *(empty)*                            | Per-room role overrides (see below)            |

## plugin.yaml

```yaml
name: tribunal
version: 0.1.0
description: >
  Multi-agent collaboration via chat-room message bus. Coordinates work
  across agents on separate machines using structured protocol messages.
  Operates in rooms only; DMs pass through untouched.
author: "Societus"
hooks:
  - pre_gateway_dispatch
  - pre_llm_call
  - post_llm_call
```

## Per-Agent Setup

Each Hermes agent (on its own machine) needs:

1. **Plugin installed** (symlink or copy):
   ```bash
   ln -s ~/projects/tribunal ~/.hermes/profiles/<agent>/plugins/tribunal
   ```

2. **Plugin enabled** in the profile's `config.yaml`:
   ```yaml
   plugins:
     enabled:
       - tribunal
   ```

3. **Agent identity** set via env var or auto-detected from profile:
   - `TRIBUNAL_AGENT_ID=researcher`
   - If unset, falls back to the Hermes profile name

4. **Role configuration**:
   - `TRIBUNAL_ROLE=orchestrator` or `TRIBUNAL_ROLE=worker`
   - Can be overridden per-room via `TRIBUNAL_ROOM_ROLES`

5. **Platform mention gating disabled** (Tribunal handles this):
   - Discord: `DISCORD_IGNORE_NO_MENTION=true`
   - Matrix: `MATRIX_REQUIRE_MENTION=false`

## Per-Room Role Overrides

To assign different roles in different rooms:

```bash
# Format: chat_key=role,chat_key=role
TRIBUNAL_ROOM_ROLES="!general:matrix.org=orchestrator,!dev:matrix.org=worker"
```

If a room is not listed, the default `TRIBUNAL_ROLE` is used.

## No Kanban Requirement

Tribunal does not require Hermes' kanban feature. Each agent manages
its work purely through the tribunal protocol. Kanban can optionally
be enabled per-agent for its own internal sub-task decomposition, but
the tribunal plugin does not read from or write to kanban.db.

## Required Hermes Config

- Hermes Agent v0.11.0+ with plugin system support
- Platform adapters configured for Discord and/or Matrix
- All participating agents must be members of the same room/channel

## Required Infrastructure

- Chat platform (Discord or Matrix) reachable from all agent machines
- No shared filesystem required
- No additional services to deploy
