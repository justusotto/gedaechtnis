---
description: Get this session ready for /compact — write a compact point (the machine facts plus three lines only you can write), then say READY.
allowed-tools: Bash(python3:*), Edit, Read
---

The owner is about to compact this session. Write the compact point that the session will read
back afterwards, in three steps.

1. Pick the file: this session's own state-of-record file — the seat's state file, or your
   handoff if you are a builder (a handoff already carrying **ORDERS IN FORCE:**, **LIVE:** and
   **RESUME ORDER:** counts on its own). Then run, with the session id from this session's boot
   facts block (the line beginning "Session id") and your `--name` if you were launched with one:

   ```sh
   python3 "${CLAUDE_PLUGIN_ROOT}/tools/compactpoint.py" write <file> --session-id <id> --name <name>
   ```

   `--session-id` may be left out when `CLAUDE_CODE_SESSION_ID` is set in the shell; the script
   refuses, and writes nothing, when it cannot tell which session this is. A handoff counts by its
   modification time, so a point in a handoff is as fresh as the last edit to that file.
   If `${CLAUDE_PLUGIN_ROOT}` is unset, the script is `tools/compactpoint.py` inside the
   gedaechtnis plugin directory. It appends a stamped `## ★ COMPACT POINT` heading, a machine
   part, and three placeholder lines. **Do not rewrite the machine part**: it is read from the
   same functions the hooks act on.

2. Replace the three `<fill in>` placeholders in that file:
   - `**ORDERS IN FORCE:**` — what the owner has ruled that still binds this session, in the owner's words
     where you have them.
   - `**LIVE:**` — what is running or half-done and why, each with its sha or path, checked now
     (not remembered).
   - `**RESUME ORDER:**` — numbered: what to do first after the compaction, then next.

   Keep the whole block under 8,000 characters. It is put back word for word after compaction,
   and the door refuses a longer one.

3. Say exactly: **READY — type /compact**

If the owner's `/compact` is refused, the refusal names what is missing; fix that and say READY
again. `/compact force …` skips the check. The door only runs where `compact_point` is `on` in the
plugin config; elsewhere this command still writes a useful point.
