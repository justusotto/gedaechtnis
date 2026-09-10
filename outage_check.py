#!/usr/bin/env python3
"""outage_check.py — refuse the first prompt of a session the plugin did not load.

★ WHY THIS FILE IS NOT A PLUGIN HOOK, AND MUST NEVER BECOME ONE.

On 2026-09-09 the key `"agents": "./agents"` was added to `.claude-plugin/plugin.json`. Claude
Code's manifest validator rejects that value (`agents: Invalid input`), and a rejected manifest
disables the WHOLE plugin — every hook in `hooks/hooks.json` with it. Nothing said so. For about
fifteen hours every session ran with no doors at all: `session.log` simply stopped, `partition.log`
held only hand-run probes, and a fresh `claude -p` ran `git -C ~/Atlas add -A --dry-run` undenied.
The outage was found by an unrelated council's control arm, not by any of this plugin's 400-odd
tests — because they all exercise `gate.py` directly, and the one instrument that shows the ✘
(`claude plugin list`) was in no gate.

A check for that failure cannot live inside the thing that fails. So this file is registered in the
USER settings (`~/.claude/settings.json`, `hooks.UserPromptSubmit`) by `init.py`, outside the
plugin manifest, where the plugin's own rejection cannot reach it. Adding it to `hooks.json` would
make it silent in exactly the case it exists for.

★ WHAT IT KEYS OFF. `<state>/session-start-<session_id>.json`, written by the plugin's SessionStart
hook (`hooks/session_start.py`, path defined by `common.session_state_path`). That file is the
narrowest available proof that the plugin's hooks actually RAN in THIS session: it is per-session
(not per-cwd, not "the latest"), it is written on every SessionStart source the plugin matches
(startup|resume|clear|compact), and it exists for no other reason. A `claude plugin list` call
would be the direct instrument, but it costs a subprocess and a second on every prompt; this costs
one `stat`.

★ THE VERDICT (council 3, K-2, CLOSED 3–1 on the form, 4–0 on the falsifier; owner picked
"block-then-downgrade" 2026-09-10):

  sid present, artifact absent  -> exit 2, message on stderr. The doorless turn still runs under
                                   any softer form; exit 2 is the only one that stops it.
  sid missing from the input    -> exit 0 + one log row. A schema change to the hook input must
                                   never block a session (Caspar); the row is how we find out.
  artifact present              -> exit 0, silent, nothing written.

★ THE FALSIFIER, agreed at the council and carried as Balthasar's objection rather than outvoted:
**≥1 HEALTHY session blocked in a week → downgrade to message-only** (`"outage_check": "message"`).
His case is that one missed sentinel reads as the guard working. The known ways this can fire on a
healthy session: the SessionStart hook timing out (15 s budget) or crashing; a state directory
pruned under a live session; a Claude Code change to `session_id`'s meaning between the two events.
Every block and every message is therefore logged to `<state>/outage-check.log` — the falsifier
counts blocks, so a block that left no row could not be counted.

★ CHEAPNESS. It runs on EVERY prompt: stdlib only, no plugin import, one small JSON read (the
config file, which also carries the mode) and one `stat`. It never reads the vault, never runs git,
never shells out. The state directory is resolved with `config.py`'s own precedence
(env `GEDAECHTNIS_STATE_DIR` -> config.json `state_dir` -> `~/.claude/gedaechtnis`) rather than
imported from it, so that a broken, moved or uninstalled plugin cannot take the check down with it;
`tests/test_outage_check.py` pins the two resolutions against each other so they cannot drift.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG_NAME = "outage-check.log"


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def config_path() -> Path:
    """Same file `hooks/config.py` reads, resolved the same way (GEDAECHTNIS_CONFIG wins)."""
    return Path(os.environ.get(
        "GEDAECHTNIS_CONFIG", str(_home() / ".claude" / "gedaechtnis" / "config.json"))).expanduser()


def config() -> dict:
    """The JSON layer, or {} — a malformed config must not block a prompt, exactly as it must not
    take a hook down (`config.py._load`)."""
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def state_dir(cfg: dict) -> Path:
    """`config.STATE`, re-derived. env > config.json > default, the module's own precedence."""
    raw = os.environ.get("GEDAECHTNIS_STATE_DIR") or cfg.get("state_dir") \
        or str(_home() / ".claude" / "gedaechtnis")
    return Path(os.path.expanduser(str(raw)))


def mode(cfg: dict) -> str:
    """`block` (default) · `message` (the falsifier's downgrade) · `off`.

    GEDAECHTNIS_OUTAGE_CHECK wins over the config file, which is what makes the one-session
    off-switch a launch-time variable and the permanent one a config key."""
    v = os.environ.get("GEDAECHTNIS_OUTAGE_CHECK")
    if v is None:
        v = cfg.get("outage_check")
    if v is None:
        return "block"
    s = str(v).strip().lower()
    if s in ("", "0", "false", "no", "off", "none"):
        return "off"
    if s in ("message", "warn", "warning"):
        return "message"
    return "block"


def log(st: Path, line: str) -> None:
    """One tab-separated row. Never raises: a logging failure may not block a prompt."""
    try:
        st.mkdir(parents=True, exist_ok=True)
        with open(st / LOG_NAME, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{line}\n")
    except OSError:
        pass


def message(st: Path, sid: str) -> str:
    return (
        "Gedächtnis did not load this session.\n\n"
        f"Its SessionStart hook never ran: {st / ('session-start-' + sid + '.json')} is absent, so\n"
        "the doors it installs (vault git law, model and effort pinning, write partition, data\n"
        "integrity) are NOT guarding this session. Anything you do here is unguarded.\n\n"
        "Most likely cause: the plugin manifest was rejected. ONE bad key disables the whole\n"
        "plugin, hooks included, and Claude Code says nothing (2026-09-09: `\"agents\": \"./agents\"`\n"
        "cost fifteen hours of doorless sessions). Check it:\n"
        "    claude plugin list                 # a Failed / ✘ row on gedaechtnis is the answer\n"
        f"    claude plugin validate {HERE}\n\n"
        "Or the hook ran and failed: see ~/.claude/gedaechtnis/session.log for this session's row.\n\n"
        "To proceed without fixing it, restart Claude with the check off or downgraded:\n"
        "    one session:   GEDAECHTNIS_OUTAGE_CHECK=off claude\n"
        f"    permanently:   \"outage_check\": \"off\"       in {config_path()}\n"
        f"    warn instead:  \"outage_check\": \"message\"   in {config_path()}\n"
        f"    remove it:     python3 {HERE / 'init.py'} --remove-outage-check\n"
    )


def main() -> int:
    raw = sys.stdin.read()
    try:
        inp = json.loads(raw) if raw.strip() else {}
    except ValueError:
        inp = {}
    if not isinstance(inp, dict):
        inp = {}
    cfg = config()
    m = mode(cfg)
    if m == "off":
        return 0
    st = state_dir(cfg)
    sid = str(inp.get("session_id") or "").strip()
    if not sid:
        # A schema change, not an outage. Never block; leave the one row that says the check has
        # gone blind, so the next reader of the log knows the guard stopped guarding.
        log(st, "no-session-id\tinput-keys=" + (",".join(sorted(inp)) or "-"))
        return 0
    if (st / f"session-start-{sid}.json").exists():          # the one stat
        return 0
    if m == "message":
        log(st, f"message\tsid={sid}")
        sys.stderr.write(message(st, sid))
        print("Gedächtnis did not load this session: its doors are not guarding this turn "
              "(`claude plugin list`). Say so before doing anything the doors would have refused.")
        return 0
    log(st, f"block\tsid={sid}")
    sys.stderr.write(message(st, sid))
    return 2


if __name__ == "__main__":
    sys.exit(main())
