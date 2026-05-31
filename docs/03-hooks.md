# Tribunal -- Hook Specifications

Detailed specification for each hook implementation.

---

## `pre_gateway_dispatch`

### Purpose

Single gatekeeper for all incoming room messages. Classifies messages,
absorbs context, dispatches tribunal protocol messages, sets reactions.
Passes DMs through untouched.

### Input (kwargs)

| Field           | Type           | Source                       |
|-----------------|----------------|------------------------------|
| `event`         | MessageEvent   | Incoming message             |
| `gateway`       | GatewayRunner  | Full gateway object          |
| `session_store` | SessionDB      | Session database             |

Key event fields used:

- `event.text` -- message body
- `event.source.platform` -- Platform enum (DISCORD, MATRIX)
- `event.source.chat_id` -- room/channel ID
- `event.source.thread_id` -- thread/topic ID (nullable)
- `event.source.user_id` -- sender user ID
- `event.source.user_name` -- sender display name
- `event.source.is_bot` -- True for Discord bots (always False for Matrix)
- `event.source.chat_type` -- "dm", "group", "channel", "thread"
- `event.message_id` -- platform message ID (for reactions)

### Processing Steps

```
1. Check platform
   - If not Discord and not Matrix: return None (pass through)

2. Check chat_type
   - If "dm" or "private": return None immediately
   - Tribunal NEVER intercepts DMs. Hermes built-in flows handle them.

3. Derive chat_key from source.chat_id + source.thread_id

4. Parse tribunal protocol markers from event.text
   - Look for [TRIBUNAL:ASSIGN ...], [TRIBUNAL:DONE ...], etc.
   - Extract key=value pairs from the marker

5. If this is a tribunal protocol message:
   a. [TRIBUNAL:ASSIGN] with agent == my_name:
      - Write task to local tasks table
      - Write message to local messages table
      - Return {"action": "allow"} (agent should respond to assignment)
   b. [TRIBUNAL:ASSIGN] for a different agent:
      - Write task to local tasks table (for dependency tracking)
      - Write message to local messages table
      - Return {"action": "skip", "reason": "assigned-to-other"}
   c. [TRIBUNAL:DONE], [TRIBUNAL:PROGRESS], [TRIBUNAL:BLOCK], [TRIBUNAL:FAIL]:
      - Update local tasks table with new status
      - Write message to local messages table
      - Return {"action": "skip", "reason": "tribunal-protocol"}

6. Determine sender type (for non-tribunal messages)
   - Discord: check source.is_bot
   - Matrix: check against known_bots table + self user_id

7. Parse @mentions from event.text
   - Discord: use event.raw_message.mentions
   - Matrix: parse @displayname patterns, cross-reference room_agents

8. Apply decision matrix:

   a. Bot sender:
      - Write message to local SQLite
      - Return {"action": "skip", "reason": "bot-absorb"}

   b. Human, mentions 2+ agents, and I am orchestrator for this room:
      - Decompose the message into tasks
      - Write room_agents entries to local DB
      - Post [TRIBUNAL:ASSIGN ...] messages to the room (one per agent)
      - Schedule reaction (watched emoji)
      - Return {"action": "skip", "reason": "orchestrator-dispatched"}

   c. Human, mentions this agent only:
      - Write message to local SQLite
      - Return {"action": "allow"} -- normal response path

   d. Human, no mentions:
      - Write message to local SQLite
      - Schedule reaction (watched emoji)
      - Return {"action": "skip", "reason": "context-absorb"}

   e. Human, mentions other agents but not me:
      - Write message to local SQLite
      - Return {"action": "skip", "reason": "other-agent-mention"}
```

### Tribunal Protocol Message Parsing

```python
import re

TRIBUNAL_RE = re.compile(
    r'\[TRIBUNAL:(ASSIGN|PROGRESS|DONE|BLOCK|FAIL)\s+'
    r'([^\]]*)\]'
)

def parse_tribunal_message(text):
    """Extract tribunal protocol markers from message text.

    Returns list of dicts with 'type' and key-value pairs.
    """
    results = []
    for match in TRIBUNAL_RE.finditer(text):
        msg_type = match.group(1)
        params_str = match.group(2)
        params = {'type': msg_type}
        # Parse key="value" and key=value pairs
        for kv in re.findall(r'(\w+)="([^"]*)"|(\w+)=(\S+)', params_str):
            if kv[0]:
                params[kv[0]] = kv[1]
            else:
                params[kv[2]] = kv[3]
        results.append(params)
    return results
```

### Reaction Setting

Same as before -- schedule async reactions from the sync hook:

```python
import asyncio

def _schedule_reaction(gateway, event, emoji):
    platform = event.source.platform
    adapter = gateway.adapters.get(platform)
    if not adapter:
        return

    async def _do_react():
        try:
            if platform == Platform.MATRIX:
                await adapter._send_reaction(
                    event.source.chat_id,
                    event.message_id,
                    emoji,
                )
            elif platform == Platform.DISCORD:
                channel = adapter._client.get_channel(
                    int(event.source.chat_id)
                )
                if channel:
                    msg = await channel.fetch_message(
                        int(event.message_id)
                    )
                    if msg:
                        await adapter._add_reaction(msg, emoji)
        except Exception as exc:
            logger.debug("reaction failed: %s", exc)

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_do_react(), loop=loop)
    except RuntimeError:
        pass
```

### Bot Detection (Matrix-specific)

Matrix does not set `source.is_bot` reliably. Detection strategy:

1. Match sender against the bot's own Matrix user ID -> SELF_BOT
2. Match sender against `known_bots` table -> OTHER_BOT
3. Match sender against `room_agents` for this chat_key -> OTHER_BOT
4. Cache result in `known_bots` for future lookups

---

## `pre_llm_call`

### Purpose

Inject tribunal protocol instructions, task assignments, and room
history into the agent's turn.

### Input (kwargs)

| Field                  | Type   | Source                      |
|------------------------|--------|-----------------------------|
| `session_id`           | str    | Hermes session identifier   |
| `user_message`         | str    | The trigger message         |
| `conversation_history` | list   | Prior messages in session   |
| `is_first_turn`        | bool   | First turn of the session   |
| `model`                | str    | Active model name           |
| `platform`             | str    | Platform name               |

### Processing Steps

```
1. Check platform
   - If not Discord and not Matrix: return None

2. Derive chat_key from session_id or context vars

3. Check if this is a DM session
   - If chat_key indicates a DM: return None (no tribunal in DMs)

4. Read room history from local SQLite
   - SELECT last N messages for this chat_key
   - Format with sender names, timestamps, and tribunal markers
   - N controlled by TRIBUNAL_HISTORY_COUNT env var (default: 30)

5. Read active tasks for this agent
   - SELECT from tasks WHERE chat_key = ? AND agent = ?
   - Include dependency status (are deps done?)

6. Read room agent roster
   - SELECT from room_agents WHERE chat_key = ?

7. Assemble context block:
   a. [Tribunal Protocol] -- rules for producing structured output
   b. [Active Tasks] -- current assignments and dependency state
   c. [Room Agents] -- who else is in this collaboration
   d. [Recent Room History] -- formatted messages
   e. [End Tribunal Context]

8. Return {"context": assembled_text}
   - If no active tasks and no room history: return None
```

### Context Block Format

```
[Tribunal Protocol]
You are agent "researcher" in a multi-agent collaboration.
When you start work on a task, include in your response:
  [TRIBUNAL:PROGRESS id=T-001 agent=researcher note="what you're doing"]
When you finish a task, include in your response:
  [TRIBUNAL:DONE id=T-001 agent=researcher result="summary of findings"]
If you need human input, include in your response:
  [TRIBUNAL:BLOCK id=T-001 agent=researcher reason="specific question"]
If you cannot complete a task, include in your response:
  [TRIBUNAL:FAIL id=T-001 agent=researcher reason="what went wrong"]
Do NOT start a task until all its dependencies are marked DONE.
These markers will be seen by all agents in the room.
[End Tribunal Protocol]

[Active Tasks]
T-001: "research auth patterns for the API"
  Status: assigned (to you)
  Depends on: (none)
  You may start this task now.

T-003: "review auth implementation"
  Status: waiting
  Depends on: T-002 (coder: "implement auth middleware") -- not done yet
  Do not start until T-002 is DONE.
[End Active Tasks]

[Room Agents]
orchestrator (coordinator)
researcher (you)
coder
security
[End Room Agents]

[Recent Room History]
12:01 **alice**: @orchestrator @researcher @coder @security build a REST API for user management with auth and tests
12:01 [Bot: orchestrator]: [TRIBUNAL:ASSIGN id=T-001 agent=researcher goal="research auth patterns for the API" depends="[]"]
12:01 [Bot: orchestrator]: [TRIBUNAL:ASSIGN id=T-002 agent=coder goal="scaffold FastAPI project structure" depends="[]"]
12:01 [Bot: orchestrator]: [TRIBUNAL:ASSIGN id=T-003 agent=security goal="review auth design once research is done" depends="[\"T-001\"]"]
12:05 [Bot: coder]: [TRIBUNAL:PROGRESS id=T-002 agent=coder note="scaffolding project with FastAPI + SQLAlchemy"]
12:08 [Bot: coder]: [TRIBUNAL:DONE id=T-002 agent=coder result="project scaffolded at /workspace/api"]
[End Room History]
```

### Orchestrator Context (special)

When the agent is the orchestrator for this room, the context block
includes additional instructions:

```
[Tribunal Protocol]
You are the ORCHESTRATOR agent for this room.
When a human @mentions multiple agents, decompose the request into tasks.
For each task, include in your response:
  [TRIBUNAL:ASSIGN id=T-NNN agent=TARGET_AGENT goal="task description" depends="[\"T-XXX\"]"]
Track which tasks are done. When a task completes and its dependents
are unblocked, you do NOT need to re-assign -- agents watch the room
stream for DONE messages and start when their deps are met.
If the human sends a follow-up message, re-evaluate and adjust tasks.
[End Tribunal Protocol]
```

---

## `post_llm_call`

### Purpose

Persist the agent's response to local SQLite and extract tribunal
protocol markers.

### Input (kwargs)

| Field                | Type   | Source                  |
|----------------------|--------|-------------------------|
| `session_id`         | str    | Hermes session ID       |
| `user_message`       | str    | The trigger message     |
| `assistant_response` | str    | The agent's response    |
| `platform`           | str    | Platform name           |
| `model`              | str    | Model used              |

### Processing Steps

```
1. Check platform
   - If not Discord and not Matrix: return

2. Derive chat_key

3. Check if this is a DM session
   - If DM: return (no tribunal persistence for DMs)

4. Determine bot name
   - From TRIBUNAL_BOT_NAME env var, or Hermes profile name

5. Write user_message (if not already written by pre_gateway_dispatch)
   - Dedup by message_id

6. Write assistant_response
   - sender = bot_name
   - sender_type = 'self'
   - Parse any [TRIBUNAL:...] markers and set the tribunal field

7. Update local tasks table based on any tribunal markers in the response
   - PROGRESS -> update note, set status='in_progress'
   - DONE -> update result, set status='done'
   - BLOCK -> update block_reason, set status='blocked'
   - FAIL -> update reason, set status='failed'
   - ASSIGN -> create new task row (orchestrator creating tasks)

8. Prune old messages (older than 72h)
```

No return value. Failures are logged but do not affect the agent's
response.
