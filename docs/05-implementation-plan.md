# Tribunal -- Implementation Plan

Ordered by dependency. Each phase produces a testable increment.

---

## Phase 1: Skeleton

Create the plugin structure with empty hook implementations that log
received events and return None (pass-through).

**Files:**
- `plugin.yaml` -- plugin manifest
- `__init__.py` -- register() with three no-op hooks
- `config.py` -- env var parsing, constants, path helpers

**Test:** Install plugin, start gateway, send messages (DM and room),
verify no interference with normal operation. Logs show hook
invocations. DMs are completely unaffected.

**Estimated effort:** 1-2 hours.

---

## Phase 2: Local SQLite

Implement the database layer: schema creation, read/write helpers,
chat_key derivation, pruning.

**Files:**
- `db.py` -- connection management, schema init, CRUD operations
  - `tribunal_connect(db_path)` -- open/init DB with WAL mode
  - `tribunal_write_message(db, chat_key, sender, sender_type, text, platform, message_id, tribunal_type)`
  - `tribunal_read_history(db, chat_key, limit)` -> formatted string
  - `tribunal_prune(db, chat_key, max_age_hours)`
  - `tribunal_task_upsert(db, task_id, chat_key, agent, goal, status, depends, ...)`
  - `tribunal_task_update(db, task_id, status, note, result, ...)`
  - `tribunal_tasks_for_agent(db, chat_key, agent)` -> list of task dicts
  - `tribunal_task_get(db, task_id)` -> task dict or None
  - `tribunal_room_agent_upsert(db, chat_key, agent_name, platform_id, role)`
  - `tribunal_room_agents(db, chat_key)` -> list of agent records
  - `tribunal_bot_known(db, platform, user_id)` -> bool
  - `tribunal_bot_remember(db, platform, user_id, name)`

**Test:** Unit tests with temp DB. Verify WAL mode active. Verify
pruning. Verify task upsert/update cycles.

**Estimated effort:** 2-3 hours.

---

## Phase 3: Protocol Parser

Implement parsing and generation of tribunal structured messages.

**Files:**
- `protocol.py` -- structured message handling
  - `parse_tribunal_messages(text)` -> list of parsed dicts
  - `format_assign(task_id, agent, goal, depends)` -> str
  - `format_progress(task_id, agent, note)` -> str
  - `format_done(task_id, agent, result)` -> str
  - `format_block(task_id, agent, reason)` -> str
  - `format_fail(task_id, agent, reason)` -> str
  - `is_tribunal_message(text)` -> bool

**Test:** Round-trip parse/format tests. Edge cases: nested quotes,
special characters in goal text, missing fields.

**Estimated effort:** 1-2 hours.

---

## Phase 4: Bot Detection + DM Guard

Implement per-platform bot detection and the DM exclusion gate.

**Files:**
- `detection.py` -- sender classification
  - `classify_sender(event, gateway, db_conn)` -> SenderType enum
    (HUMAN, SELF_BOT, OTHER_BOT, UNKNOWN)
  - `is_dm(event)` -> bool
- `chatkey.py` -- chat_key derivation from event source
  - `derive_chat_key(event)` -> str

**DM Guard:**
```python
def is_dm(event):
    """Check if the message is a DM/private message."""
    return event.source.chat_type in ("dm", "private")
```

Every hook entry point checks `is_dm(event)` first and returns None
if true. This is the hard boundary -- no DM content is ever processed,
stored, or modified by tribunal.

**Discord bot detection:**
- `source.is_bot == True` -> OTHER_BOT
- Match `source.user_id` against adapter's own bot ID -> SELF_BOT
- Otherwise -> HUMAN

**Matrix bot detection:**
- Match sender against adapter's own user ID -> SELF_BOT
- Match sender against `known_bots` table -> OTHER_BOT
- Match sender against `room_agents` for this chat_key -> OTHER_BOT
- Otherwise -> HUMAN

**Test:** Mock events with various sender types. DM events return None
immediately. Verify bot classification for both platforms.

**Estimated effort:** 2-3 hours.

---

## Phase 5: Mention Parsing

Extract @mentions from message text for both platforms.

**Files:**
- `mentions.py` -- mention extraction
  - `parse_mentions(event, gateway, db_conn)` -> MentionResult
    - MentionResult fields:
      - mentioned_agents: list of agent names mentioned
      - is_multi_mention: bool (2+ agents mentioned)
      - mentions_self: bool
      - clean_text: str (message with mentions stripped)

**Discord:**
- `event.raw_message.mentions` provides resolved user objects
- Match mentioned user IDs against room_agents platform_id values
- Strip `<@user_id>` patterns from text

**Matrix:**
- Parse `event.text` for `@displayname` patterns
- Cross-reference with room_agents for this chat_key
- Check Matrix event content `m.mentions.user_ids` if present
- Strip mention patterns from text

**Test:** Various message formats with single/multi mentions.

**Estimated effort:** 2-3 hours.

---

## Phase 6: Gatekeeper Hook

Wire the full decision matrix into `pre_gateway_dispatch`.

**Files:**
- `hooks/dispatch.py` -- the gatekeeper implementation
- `reactions.py` -- async reaction scheduling

**Logic:**
1. DM check -> return None
2. Parse tribunal protocol markers -> handle ASSIGN/DONE/etc.
3. Classify sender (bot/human)
4. Parse mentions
5. Apply decision matrix from `03-hooks.md`
6. For absorbed messages: write to local DB + schedule reaction
7. For orchestrator multi-mention: decompose, post ASSIGN messages

**Decomposition** (orchestrator only):
When the orchestrator receives a multi-mention message, it needs to
decompose the request into tasks. Two approaches:

- **LLM-driven decomposition**: The orchestrator's own LLM turn handles
  this. The pre_gateway_dispatch creates a special "decompose" context
  and allows the message. The LLM produces ASSIGN messages as part of
  its response. post_llm_call extracts and records them.
- **Rule-based decomposition**: Simple splitting by mentioned agent
  names, with the full goal text assigned to each. No LLM call needed
  for the orchestrator itself.

Recommendation: **LLM-driven decomposition for v1** (richer task
breakdown). The orchestrator's pre_llm_call context includes special
instructions for producing ASSIGN messages.

**Test:** Integration test with mock gateway. Send various message
types, verify correct action returned, DB written, reaction scheduled.
Verify DMs always pass through.

**Estimated effort:** 4-6 hours.

---

## Phase 7: Context Injection Hook

Wire the `pre_llm_call` context assembler.

**Files:**
- `hooks/context.py` -- context formatting and injection

**Logic:**
1. DM check -> return None
2. Derive chat_key
3. Read room history from local SQLite
4. Read active tasks for this agent in this room
5. Read room agent roster
6. Format context block (protocol instructions + tasks + history)
7. Return `{"context": formatted_text}`

**Test:** Insert test data, verify context formatting. Verify DM
sessions get None.

**Estimated effort:** 3-4 hours.

---

## Phase 8: Persistence Hook

Wire the `post_llm_call` writer.

**Files:**
- `hooks/persist.py` -- post-turn persistence

**Logic:**
1. DM check -> return
2. Derive chat_key
3. Write trigger message (dedup by message_id)
4. Write assistant response (with tribunal field parsed)
5. Update local tasks table from tribunal markers
6. Prune old messages

**Test:** Run a conversation in a room, verify messages appear in local
SQLite. Verify dedup. Verify DM conversations are NOT stored.

**Estimated effort:** 1-2 hours.

---

## Phase 9: Integration Testing

End-to-end test with multiple Hermes agents on separate machines (or
simulated with separate profiles on the same machine).

**Setup:**
1. Create 2-3 test profiles with tribunal plugin
2. Start gateways for each profile, connected to the same Discord
   channel or Matrix room
3. Send multi-mention message from a human account
4. Verify orchestrator posts ASSIGN messages
5. Verify workers pick up their assignments
6. Verify workers post PROGRESS/DONE messages
7. Verify orchestrator reacts to completions
8. Verify dependent tasks start after deps are done
9. Verify no runaway loops
10. Verify DMs work normally with no tribunal interference
11. Verify agents on separate machines see each other's messages

**Estimated effort:** 4-6 hours.

---

## Phase 10: Polish and Documentation

- Error handling audit (every DB call, every protocol parse)
- Logging consistency
- Configuration validation on plugin load
- Update README with usage guide
- Add troubleshooting section
- Document multi-machine deployment pattern

**Estimated effort:** 2-3 hours.

---

## Total Estimated Effort

~23-33 hours across 10 phases. Phases 1-8 are ~18-26 hours of
implementation. Phase 9 is open-ended depending on integration
issues. Phase 10 is cleanup.

## Project File Structure

```
tribunal/
  pyproject.toml
  src/
    tribunal/
      __init__.py          -- register() + plugin metadata
      plugin.yaml          -- Hermes plugin manifest
      config.py            -- env vars, constants, paths
      db.py                -- local SQLite operations
      protocol.py          -- tribunal message parsing and formatting
      detection.py         -- bot/human sender classification + DM guard
      chatkey.py           -- chat_key derivation from events
      mentions.py          -- per-platform mention parsing
      reactions.py         -- async reaction scheduling
      hooks/
        __init__.py
        dispatch.py        -- pre_gateway_dispatch implementation
        context.py         -- pre_llm_call implementation
        persist.py         -- post_llm_call implementation
  docs/
    01-architecture.md
    02-schema.md
    03-hooks.md
    04-configuration.md
    05-implementation-plan.md
  tests/
    conftest.py            -- shared fixtures (temp DB, mock events)
    test_db.py
    test_protocol.py
    test_detection.py
    test_mentions.py
    test_dispatch.py
    test_context.py
    test_dm_exclusion.py
```
