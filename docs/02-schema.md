# Tribunal -- Local SQLite Schema

Each agent maintains its own local SQLite database. This stores the
room message history and task state that the agent has observed from
the room stream. There is no cross-machine sharing of this database.

## Path Resolution

The DB lives within the agent's Hermes profile directory:

```
~/.hermes/profiles/<agent_name>/tribunal/tribunal.db
```

Or, for the default profile:

```
~/.hermes/tribunal/tribunal.db
```

Resolved from `HERMES_HOME` (set by Hermes per-profile), NOT from
`Path.home()`. Each profile gets its own database because each profile
is a separate agent with its own view of the room.

Path logic:

1. `TRIBUNAL_DB_PATH` env var -- if set, use verbatim (absolute path).
2. Fallback: `$HERMES_HOME/tribunal/tribunal.db`

The directory is created on first use if it doesn't exist.

Uses WAL mode for safe concurrent access from the gateway process and
any debug/inspection tools.

## Tables

### `messages`

Room messages absorbed from the stream. Written by `pre_gateway_dispatch`
(for absorbed messages and tribunal protocol messages) and
`post_llm_call` (for this agent's own responses).

```sql
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    chat_key    TEXT    NOT NULL,
    sender      TEXT    NOT NULL,
    sender_type TEXT    NOT NULL DEFAULT 'human',  -- 'human' | 'bot' | 'self'
    text        TEXT    NOT NULL,
    platform    TEXT    NOT NULL DEFAULT '',        -- 'discord' | 'matrix'
    message_id  TEXT    NOT NULL DEFAULT '',        -- platform message/event ID
    tribunal    TEXT    NOT NULL DEFAULT ''         -- parsed tribunal type, or ''
);

CREATE INDEX IF NOT EXISTS idx_msgs_chat_ts ON messages (chat_key, ts);
CREATE INDEX IF NOT EXISTS idx_msgs_tribunal ON messages (chat_key, tribunal);
```

- `chat_key`: platform room/channel ID. See derivation table below.
- `tribunal`: empty string for normal messages, or one of `ASSIGN`,
  `PROGRESS`, `DONE`, `BLOCK`, `FAIL` for protocol messages. Parsed
  from the message text on insertion.
- Auto-prune: messages older than 72 hours deleted on every write.

### `tasks`

Task state observed by this agent. Written when the agent sees
`[TRIBUNAL:ASSIGN]` messages in the room. Updated when it sees
`[TRIBUNAL:PROGRESS/DONE/BLOCK/FAIL]` messages.

```sql
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,             -- T-001, T-002, etc.
    chat_key    TEXT    NOT NULL,
    agent       TEXT    NOT NULL,             -- assigned agent name
    goal        TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'assigned',  -- assigned | in_progress | done | blocked | failed
    depends     TEXT    NOT NULL DEFAULT '[]',        -- JSON array of task IDs
    note        TEXT    NOT NULL DEFAULT '',           -- latest progress note
    result      TEXT    NOT NULL DEFAULT '',           -- result summary (on DONE)
    block_reason TEXT   NOT NULL DEFAULT '',           -- reason (on BLOCK)
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_chat_agent ON tasks (chat_key, agent);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (status);
```

The orchestrator maintains a complete view of all tasks. A worker agent
only tracks tasks assigned to it plus tasks it depends on.

### `known_bots`

Cache of known bot user IDs per platform. Used for bot-sender detection
on Matrix (Discord sets `is_bot` natively).

```sql
CREATE TABLE IF NOT EXISTS known_bots (
    platform    TEXT    NOT NULL,
    user_id     TEXT    NOT NULL,
    bot_name    TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (platform, user_id)
);
```

### `room_agents`

Agent roster for rooms where this agent is participating. Populated from
ASSIGN messages observed in the room.

```sql
CREATE TABLE IF NOT EXISTS room_agents (
    chat_key    TEXT    NOT NULL,
    agent_name  TEXT    NOT NULL,
    platform_id TEXT    NOT NULL DEFAULT '',    -- platform user ID (for mention matching)
    role        TEXT    NOT NULL DEFAULT 'worker',  -- 'orchestrator' | 'worker'
    status      TEXT    NOT NULL DEFAULT 'active',
    PRIMARY KEY (chat_key, agent_name)
);
```

---

## chat_key Derivation

| Platform        | Format                       | Example                          |
|-----------------|------------------------------|----------------------------------|
| Discord channel | `{channel_id}`               | `1234567890`                     |
| Discord thread  | `{channel_id}:{thread_id}`   | `1234567890:9876543210`          |
| Matrix room     | `{room_id}`                  | `!abc123:matrix.org`             |
| Matrix thread   | `{room_id}:{event_id}`       | `!abc123:matrix.org:$xyz`        |

---

## Concurrency Model

- WAL mode enables concurrent reads and a single writer without blocking.
- Only one process (this agent's gateway) writes to this database.
- Connections opened with `timeout=10` to wait for locks.
- `PRAGMA synchronous=NORMAL` for performance (safe with WAL).
- Write operations are small (single INSERT + optional DELETE for
  pruning) so lock contention is minimal.
