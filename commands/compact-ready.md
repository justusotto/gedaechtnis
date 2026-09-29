---
description: Get this session ready for /compact — write a compact point (the machine facts plus three lines only you can write), then say READY.
allowed-tools: Bash(python3:*), Edit, Read
---

The owner is about to compact this session. Write the compact point that the session will read
back afterwards, in four steps.

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

   Write nothing below the point in that file: text after it is read as part of the point and
   counts toward its 8,000 characters.

3. Run the door's own check, with the same session id:

   ```sh
   python3 "${CLAUDE_PLUGIN_ROOT}/tools/compactpoint.py" check <file> --session-id <id>
   ```

   It judges the point the door will judge — this session's newest point in any file it recorded,
   or in a handoff it wrote — and says NOT READY, naming that file, when it is not `<file>`. It
   measures the point exactly as the door will: its age (at most `compact_point_fresh_min` minutes
   from the plugin config, 30 unless set), the three lines, and its size. **If you edited an
   existing point instead of writing a new one, its stamp is still the old one** — re-check its
   three lines against what is true now, and only then run `compactpoint.py renew <file>` (it
   restamps that heading to now and changes nothing else), then `check` again. A handoff has no
   heading to renew: its stamp is its modification time, so saving it renews it. Without a session
   id (flag or `CLAUDE_CODE_SESSION_ID`), `check` answers NOT CONFIRMED, never READY, while the door
   is on. Only when `check` prints READY go on; it prints `READY until HH:MM`, the minute
   after which the point is too old and has to be renewed.

4. Say exactly: **READY — type /compact**

If the owner's `/compact` is refused anyway, the refusal is meant to come back to you as a task (a
second PreCompact hook wakes this session with it; this has not been seen live yet): fix what it
names, run `check`, and say READY again without waiting to be asked. `/compact force …` skips the check. The door only runs where `compact_point` is `on` in the
plugin config; elsewhere this command still writes a useful point.
