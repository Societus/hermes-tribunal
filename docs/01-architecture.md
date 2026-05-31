# Tribunal -- Architecture

## Core Principle: The Chat Room Is the Bus

Agents running on separate machines cannot share a filesystem. They need
a coordination mechanism that relies only on infrastructure they already
have: the chat platform (Discord or Matrix).

Every agent sees every message in the room. The platform provides:

- **Message delivery** -- reliable, ordered, to all connected clients
- **Service discovery** -- agents are identified by their platform
  user ID and display name, not by network address
- **Delivery guarantee** -- messages are durably stored by the platform
  (Discord/Matrix server). A reconnecting agent can catch up on history.

Tribunal's structured protocol rides on top of this existing bus.

## Hook Surface

Tribunal registers three Hermes plugin hooks. All three operate without
modifying any core Hermes files.

### 1. `pre_gateway_dispatch` (the gatekeeper)

Runs before any per-platform mention gating, auth, or session routing.
Returns one of:

- `{"action": "skip", "reason": "..."}` -- drop the message, no response.
- `{"action": "allow"}` -- pass through to normal processing.
- `None` -- pass through (for platforms Tribunal doesn't handle).

Decision matrix (rooms only; DMs always return None):

```
Sender    | Content                      | Action
----------|------------------------------|----------------------------------
Human     | Mentions 2+ agents           | Orchestrator: decompose + ASSIGN
Human     | Mentions this agent only     | Allow (direct response)
Human     | No mentions                  | Absorb as context, skip
Any       | [TRIBUNAL:ASSIGN] for me     | Write local DB, allow (respond)
Any       | [TRIBUNAL:DONE/PROGRESS/BLOCK]| Write local DB, skip (absorbed)
Bot       | Other agent's response       | Absorb as context, skip
Any       | DM / private message         | Return None (pass through)
```

### 2. `pre_llm_call` (context assembler)

Runs before the tool-calling loop. Injects:

1. Tribunal protocol briefing (the agent's role, active tasks,
   dependency state)
2. Room history from the local SQLite (all messages, including
   structured tribunal messages from other agents)
3. The agent's current task assignments and their status

### 3. `post_llm_call` (persistence + protocol extraction)

Runs after the tool-calling loop. Writes:

- The agent's response to local SQLite (if it came from a room)
- Extracts any `[TRIBUNAL:...]` markers from the response (these get
  sent to the room as part of the normal response, so all other agents
  see them)

---

## Structured Message Protocol

All tribunal coordination uses human-readable structured messages sent
as regular chat messages. Format:

```
[TRIBUNAL:ASSIGN id=T-001 agent=researcher goal="research auth patterns" depends=]
[TRIBUNAL:ASSIGN id=T-002 agent=coder goal="scaffold project" depends=T-001]
[TRIBUNAL:ASSIGN id=T-003 agent=security goal="review auth design" depends=T-001]
[TRIBUNAL:BLOCK id=T-001 agent=security reason="need human decision on token expiry"]
[TRIBUNAL:FAIL id=T-001 agent=coder reason="dependency T-001 not found in workspace"]
```

Key=value pairs inside the brackets. `depends` uses comma-separated task
IDs (not JSON) to avoid bracket ambiguity with the marker delimiters.
All agents' plugins parse these.
The orchestrator creates ASSIGN messages; worker agents produce
PROGRESS/DONE/BLOCK/FAIL messages.

### Delivery

- **Discord**: Sent as regular messages (or embeds for visual
  distinction). Can be sent in a dedicated thread within the channel.
- **Matrix**: Sent as `m.notice` message type (conventionally used for
  bot messages, often visually de-emphasized by clients). Can target a
  specific thread/topic within the room.

### Message Visibility

These are normal chat messages. Humans see them too. This is
intentional -- it provides transparency. A human reading the room can
follow the collaboration in real time. The structured format is
readable enough to be self-documenting.

---

## Data Flow

```
Human message arrives in room (Discord/Matrix)
  |
  v
pre_gateway_dispatch hook fires
  |
  +-- DM? --> return None (Hermes built-in flows handle it)
  |
  +-- Human, no mention --> absorb to local DB, react, SKIP
  +-- Human, @this_agent --> allow (normal response)
  +-- Human, @agentA @agentB --> orchestrator decomposes into tasks,
  |                              posts [TRIBUNAL:ASSIGN] messages to room,
  |                              SKIP original message
  +-- [TRIBUNAL:ASSIGN] with my agent name --> write to local DB, ALLOW
  +-- [TRIBUNAL:DONE/PROGRESS/BLOCK] --> write to local DB, SKIP
  +-- Bot message --> absorb to local DB, SKIP
  |
  v (if ALLOW)
Hermes normal processing (auth, session routing, etc.)
  |
  v
pre_llm_call hook fires
  |
  +-- Read local SQLite for room history
  +-- Read local task state (my assignments, dependency status)
  +-- Inject tribunal protocol instructions + context
  |
  v
Agent runs (LLM call + tool loop)
  |
  v
post_llm_call hook fires
  |
  +-- Write agent response to local SQLite
  +-- Extract [TRIBUNAL:...] markers (already in the response text)
  |
  v
Response sent to room (including any tribunal markers)
  |
  v
Other agents' pre_gateway_dispatch sees the response
  --> absorbs as context or acts on ASSIGN/DONE/etc.
```

---

## DM Exclusion

Tribunal does not intercept, store, or modify DM/private messages in any
way. The `pre_gateway_dispatch` hook checks `event.source.chat_type`:

- `"dm"` or `"private"` --> return `None` immediately
- All other types --> proceed with tribunal logic

This ensures DMs flow through Hermes' built-in session management,
mention handling, and memory system without interference.

No DM message text is ever written to the local tribunal.db. No DM
content appears in room history injections. No tribunal protocol
messages are sent in DMs.

---

## Role Assignment

Each agent is configured with a tribunal role:

- **orchestrator**: Decomposes human multi-mention messages into tasks,
  creates ASSIGN messages, tracks dependency graph, reacts to DONE/BLOCK
  by assigning dependent tasks. Only one orchestrator per room.
- **worker**: Responds to ASSIGN messages addressed to it, produces
  PROGRESS/DONE/BLOCK/FAIL messages. Can be any number per room.

An agent can be an orchestrator in one room and a worker in another.
Role is configured per-room in the agent's tribunal config.

---

## Dependency Graph

The orchestrator maintains a local dependency graph. When it creates
ASSIGN messages, it includes `depends=["T-001", "T-002"]`. Worker
agents see their assignment but wait until those dependencies show
DONE in the room stream before starting work.

If the orchestrator goes down mid-collaboration, agents can continue
working on their current tasks. A new orchestrator (or the same one,
restarted) can reconstruct the dependency graph by scanning the room's
message history for tribunal protocol messages.

---

## Platform Adapter Access

The `pre_gateway_dispatch` hook receives the full `gateway` object.
From it, the plugin accesses platform adapters for reactions:

```python
from gateway.config import Platform

adapter = gateway.adapters.get(Platform.DISCORD)  # or Platform.MATRIX
```

Reactions are scheduled via `asyncio.ensure_future()` since the hook
is synchronous.

---

## What This Removes vs. the Original Design

- No shared SQLite across machines (each agent has its own local DB)
- No kanban dependency (kanban is optional per-agent for its own
  sub-task decomposition, but tribunal doesn't require it)
- No REST API server to deploy
- No single point of failure beyond the chat platform (which is
  already a hard dependency)
- No cross-machine filesystem requirements

## What This Requires

- The chat platform (Discord or Matrix) must be reachable from all
  machines running agents
- Each agent must have the tribunal plugin installed and configured
  with its agent name
- The orchestrator agent must have decomposition capability (it's a
  Hermes agent -- it already has this)
- All agents must be in the same room/channel
