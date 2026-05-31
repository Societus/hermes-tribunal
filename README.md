# Tribunal

Multi-agent collaboration plugin for Hermes Agent. Coordinates work
across agents running on separate machines using the chat platform
itself as the message bus.

## Problem

Running multiple Hermes agents in the same Discord channel or Matrix
room creates a dilemma:

- `require_mention: true` -- agents only respond when @mentioned, but
  have zero context of prior conversation. They respond blind.
- `trigger: "all"` -- agents see every message, including other agents'
  intermediate tool steps, causing runaway reply loops and token waste.

Neither option supports agents collaborating on a shared task. And when
agents run on separate machines, there is no shared filesystem or local
IPC to coordinate through.

## Solution

Tribunal uses the chat room as the coordination bus. Agents communicate
via structured text messages that all participants see. No shared
database across machines. No central server. No kanban dependency.

1. **Rooms only** -- Tribunal operates exclusively in group
   channels/rooms. DMs pass through untouched, using Hermes' built-in
   flows.
2. **Structured protocol** -- agents emit and consume `[TRIBUNAL:...]`
   prefixed messages for task assignment, progress, completion, and
   blocking. The chat platform delivers these to all agents.
3. **Orchestrator pattern** -- one agent decomposes human requests into
   tasks and assigns them. Other agents pick up their assignments from
   the room stream.
4. **Local state only** -- each agent maintains its own SQLite database
   with the room history and task state it has observed. No cross-machine
   database sharing.
5. **Bot-sender exclusion** -- agents never respond to other agents'
   messages unprompted. Only structured tribunal assignments trigger
   work. This eliminates runaway loops by construction.

## Supported Platforms

- Discord
- Matrix

## Status

Design phase. See `docs/` for plan files.

## License

MIT
