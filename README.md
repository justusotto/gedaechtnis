# Das Gedächtnis — the plugin

The mechanism half of the memory system. The vault (`~/Atlas`) is the data half and never ships.

**Load in place, no install:** `ln -s ~/PycharmProjects/atlas-system/gedaechtnis ~/.claude/skills/gedaechtnis`
and restart Claude Code (or `/reload-plugins`). Claude Code discovers a folder with
`.claude-plugin/plugin.json` under a skills directory as the plugin `gedaechtnis@skills-dir`.

## What it does today (v0.1)

| event | hook | rule |
|---|---|---|
| PreToolUse Bash | `gate.py bash` | vault git law (no `add -A`/`-a`/`.`, no bare `commit`, no `--amend`, no backticks in a double-quoted `-m`, push only to `backup`, no `reset --hard`/`clean -f`) · every `claude` launch pins `--model` and `--effort` · data integrity (no `rm` on caches, `anki_mining.db`, `media/`, `~/Pictures`, `~/.Trash`, the vault; Trash never emptied; no destructive SQL on the mining DB) · a page with an answer store is opened as the artifact, never `file://` |
| PreToolUse Edit/Write | `gate.py write` | write partition — WARN mode by default (`~/.claude/gedaechtnis/partition.mode` = `warn`\|`deny`); Concilium stem rule (always deny) |
| PreToolUse Agent | `gate.py agent` | an Agent call pins `model` unless its definition does (forks exempt) |
| PostToolUse Artifact | `chore.py artifact` | appends the row to `Pharos/artifacts-index.md` and commits it path-limited as `atlas@local` |
| PostToolUse Edit/Write | `chore.py write` | trailing newline on queue files; SHA tokens that resolve nowhere are reported |
| SessionStart | `session_start.py` | records vault HEAD + lane for the debriefer; states lane, partition, mode, vault dirt, owner answers |

Every deny cites the vault entry it enforces. Logs: `~/.claude/gedaechtnis/{deny,partition,chore,session,hook-errors}.log`.
Tests: `tests/gedaechtnis/` — each rule has a positive control (denied) and a negative control (allowed).
Env overrides for tests: `GEDAECHTNIS_VAULT`, `GEDAECHTNIS_STATE_DIR`, `GEDAECHTNIS_FLEET_ROSTER`.
