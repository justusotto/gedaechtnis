# The doors

A door is a `PreToolUse` hook that can stop a tool call. A stopped call returns the reason to the
session through Claude Code's documented hook decision (`deny`, or `ask` for the ones you may still
approve). No hook ever returns `allow`: a note from this plugin never answers your permission
prompt for you.

**Where they act.** Most doors act only inside your vault. The ones that could apply anywhere act
only in the vault or in a project carrying an `.atlas-lane` marker, which `init.py` writes when you
give that project a memory, or in a session started in one. The directory a session was started
in counts only when it carries the marker itself; an unmarked one changes nothing. Everywhere else
they tell the session what they noticed and let the call run.

| door | where it acts | default | how to turn it off |
|---|---|---|---|
| Vault git rules: `add -A`/`-a`/`.`, a bare or `-a` commit, `--amend`, push to anything but `backup`, `reset --hard`, `clean -f`, `git rm` | the vault only | refuses | not switchable; they only act on the vault's own git |
| Whole-file `Write` (or `>` redirect) over an existing note | the vault only | refuses, offers Edit | not switchable |
| Generated view edited by hand | the vault only | refuses | not switchable |
| New file named after a display name (`Decisions.md`) | the vault only | refuses, names the stem | not switchable |
| Another session editing the same file | the vault only | refuses for up to ten seconds | not switchable |
| Write partition (paths the project's marker declares) | the vault only | warns and logs | `deny` in `<state>/partition.mode` turns it on; remove the file for `warn` |
| Reserved file names in one folder | the vault, only when `topology.stem_rule_dir` is set | refuses | leave `stem_rule_dir` unset |
| Authority ledger record its reader cannot resolve | the one file `authority_log` names | follows the partition mode (warns by default) | leave `authority_log` unset |
| `rm` of the vault or the Trash; emptying the Trash; destructive SQL (`DROP TABLE`, `DELETE FROM`, `TRUNCATE`) against the one database file named in `hooks/gate.py` | anywhere | asks you | not switchable; `protect_everywhere: true` also covers caches, databases and media outside the vault |
| Worktree placed outside `<repo>/.claude/worktrees/` | vault or marked project | refuses; a note elsewhere | remove the marker |
| `git push --tags` / `--follow-tags` from a clone carrying non-release tags | vault or marked project | refuses; a note elsewhere | remove the marker |
| Sub-agent starting a sub-agent without `fanout: allowed` in its brief | vault or marked project | refuses; a note elsewhere | `agent_fanout_allowed: true` |
| More than `agent_max_concurrent` (8) live sub-agents | vault or marked project | refuses; a note elsewhere | raise it in the config's `limits` |
| Message that would wake a large or cold idle session or sub-agent | vault or marked project | refuses; a note elsewhere | start the message with `RESUME-OVERRIDE: <reason>` |
| `claude` launch without `--model` / `--effort` (subcommands that start no session pass) | vault or marked project | warns; refuses from `launch_pin_deny_from` (a note elsewhere) | `launch_pin_door: false`; `require_launch_model` / `require_launch_effort: true` refuse at once, anywhere |
| An API key, token, private key or card number added to any file but a `.env` | vault or marked project | warns; refuses from `secret_deny_from` (a note elsewhere) | `secret_door: false` |
| A `kill`, `pkill` or `killall` whose target the shell computes (`$( )`, backticks, a variable, `xargs`); `pkill -P`, `pkill -f`, `pgrep -f`; pid 0, 1 or negative | vault or marked project | warns; refuses from `kill_deny_from` (a note elsewhere) | `kill_door: false` |
| Sub-agent call without a `model` | anywhere | off | `require_agent_model: true` turns it on |
| Merge or push while another session holds the merge window | repositories carrying `.merge-window` | refuses | remove `.merge-window` |
| Merge or push before a green full test run | repositories with `gedaechtnis/tests`, only with `suite_gate: true` | off | leave `suite_gate` unset; `SUITE_GATE_ALLOW=1` lets one through |
| `rm` outside a scratchpad, `.claude/worktrees/` or `__pycache__` | anywhere, only with `delete_door: true` | off; when on, warns for one day, then refuses | leave `delete_door` unset |
| `/compact` typed while this session's newest compact point is missing, stale (`compact_point_fresh_min`, 30 minutes), incomplete or over 8,000 characters (a `PreCompact` hook) | anywhere, only with `compact_point: "on"` | off | leave `compact_point` unset; `/compact force …` lets one through. An automatic compaction is not stopped |

### Doors written for one working style

These five suit a vault used with work queues, review pages and several
projects that share one roster. Turn each off with its key, or remove the marker, if you do not work that way.

| door | where it acts | default | how to turn it off |
|---|---|---|---|
| `open` of a local copy of a page that keeps its answers in an artifact store | vault or marked project | refuses; a note elsewhere | remove the marker |
| A whole HTML page whose first two lines are not `<!doctype html>` and `<meta charset="utf-8">` | vault or marked project | warns; refuses from `html_head_deny_from` | `html_head_door: false` |
| A new Markdown file that asks its reader for a verdict with two or more unmarked choices | vault or marked project | warns; refuses from `judge_md_deny_from` | `judge_md_door: false` |
| An added queue row whose two `q:` ids name different rows | vault or marked project | warns; refuses from `row_identity_deny_from` | `row_identity_door: false` |
| An `.atlas-lane` marker whose `path:` lines differ from its roster row | vault or marked project | warns only (the change is two writes; refusing the first would block the second) | `marker_roster_door: false` |
