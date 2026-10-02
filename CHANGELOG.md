# Changelog

What changed, when, and what it means for someone running this plugin. Newest first.

Dates are the day the work landed. Versions follow the `version` field in
`.claude-plugin/plugin.json`, and a release's tag is always `v` plus that version (from 0.5.0 on;
the earlier tags v0.2–v0.4 were cut from a plugin.json that still said 0.2.0). A patch (0.5.0 →
0.5.1) is fixes and polish, a minor version (0.5 → 0.6) a capability a user notices, and a major
version a rework or a complete new feature set. This is pre-1.0 software, so a minor version may
still change behaviour — where it does, the entry says so under **Changed** and names what to
re-check.

Sections used: **Added** · **Changed** · **Fixed** · **Removed** · **Known limits**.

## 0.6.1 — 2026-10-02

### Added

- **`tools/configure.py`** shows and changes the `limits` object of `config.json` (`show`, `get`,
  `set`, `unset`), checks the key and the value's type, and keeps a dated backup.
- **`hooks/mergesha.py ff <branch>`** fast-forwards `main` to a branch tip and refuses when `main`
  has moved, or is not the commit given with `--expect-main`.
- **`tools/run_reviewed.sh <script.py>`** runs a Python script only when the SHA-256 of its bytes is
  in the reviewed ledger (`tools/reviewed.py add` writes a row).
- **`tools/apply-allow-rules.sh`** merges the allow and deny rules in `rules/allow-rules.json` into
  each repository's `.claude/settings.json`; it prints the change and writes only with `--apply`.
- **`sessions.py close --when-needed`** closes the oldest finished sessions while the machine is
  short of memory and stops once it is not; **`close --all-finished`** closes every finished one.
- **The close also covers a session you started by hand in its own `screen` window**, when the
  CLI's session registry confirms its process and you have not typed into it after its first prompt.
- **`session_close_never`** lists session names the close sweep leaves alone (as shipped: Remote
  Control hosts).
- **`sessions.py launch --owner-go "<words>"`** launches past the memory test only, and writes the
  words and the readings to `owner-go.log`.
- **The worktree sweep also lists finished copy-on-write clones older than
  `worktree_clone_min_age_seconds` (a day)**; with `worktree_sweep_apply` on (off as shipped) it
  moves them to `~/Downloads/To delete <date>/` and writes a ledger line with the command that moves
  each one back; the sweep does not delete a clone.
- **The publish check reads the wording of `README.md` and `docs/*.md`** against
  `rules/public-tone.json` and fails on a listed phrase.
- **`tools/release_orphan.sh` leaves out the paths listed in a tree's `rules/publish-exclude.json`**
  unless it is run with `--with-excluded`.

### Changed

- **Closing a finished session is on as shipped** (`session_close_apply: true`): a session is
  closed by quitting its own `screen`/`tmux` session, and each close goes to `close.log`. Re-check:
  set `session_close_apply` to `false` if you want the list only.
- **A session counts as finished by time, not by its last words**: its last turn ended
  `session_close_idle_minutes` ago, or it wrote a done handoff or reported to the session that
  started it and has been quiet for 30 minutes. Re-check: `session_close_exempt` and
  `session_close_never` for sessions you want kept.
- **`sessions.py launch` tests memory by the system's pressure level and the share of memory free**
  (`session_launch_memory_floor`), not by a share of swap. Re-check: if you used
  `session_swap_cap_share` to limit launches, set `session_launch_memory_floor` instead.
- **A message to a warm session under `resume_window` is delivered when it is short
  (`resume_short_chars`) or still fits the window**; the headroom rule (`resume_min_headroom`) now
  applies to cold sessions only. Re-check: `resume_short_window` is no longer read.
- **Four doors for a vault with work queues, review pages and a shared roster are off as shipped**
  (`artifact_open_door`, `judge_md_door`, `row_identity_door`, `marker_roster_door`);
  `"profile": "atlas"` in `config.json` turns them on. Re-check: if you used these doors, add that
  line.
- **The sub-agent cap counts sub-agents that have started plus calls still waiting to start**: a
  new SubagentStart hook records each start, and a reserved slot that no sub-agent has claimed after
  `agent_pending_seconds` is released.
- **A row mark refused while the vault is busy is kept and applied at the next quiet Stop**;
  `rowdone.py owed` applies it by hand.
- **A session record without a pid stops protecting a worktree after a day**
  (`worktree_session_stale_seconds`).
- **Shipped defaults**: `context_reach_margin_tokens` is 20000 (was 30000) and `resume_short_chars`
  is 1500 (was 0).

### Fixed

- The session close no longer keeps a finished session because a background task's report was
  queued, because two `screen` names shared a prefix, or because its handoff was not named at launch.
- The session close no longer closes a finished session while a child process it started is still
  running.
- `sessions.py close --replay <days>` replays the rule the close uses now.
- A launch's worktree or handoff given as a relative path is read against the session's own folder.

## 0.6.0 — 2026-09-29 — parked messages delivered, a door for computed kill targets

### Added

- **Parked messages are delivered** (`hooks/deliver.py`). A message the resume gate refused was
  parked in `parked-<name>.jsonl` and never shown to anyone: the only reader was a count at session
  start, keyed by the process's `--name`, which a resumed session does not carry. Now, at session
  start (startup, resume, compact) and at every prompt, the session is shown what was parked for it:
  newest first, at most 6,000 characters per delivery (the rest waits for the next), each framed
  as a peer's report, not the owner's word, possibly superseded. A message older than 48 hours is
  listed with the file it moved to, not shown. The parked file is claimed by an atomic rename
  before it is read, so a message parked meanwhile waits for the next delivery; shown lines move
  to `parked-<name>.delivered.jsonl`. Any error shows nothing, exits 0, logs one line to
  `deliver.log` and leaves the lines unread (a failed put-back logs a second line and leaves a claim
  file that the next delivery takes over). `fleet.park` now writes under a file lock and only into
  the file still at its path, and delivery reads a claim under that lock, so a park in flight at the
  moment of the claim is not lost. Known gap: a hook killed between marking a line delivered and
  printing it leaves that line in the delivered file, unshown. The session-start count of parked
  messages is gone.
- **A short message may pass the resume gate's headroom rule** (`resume_short_chars`,
  `resume_short_window`; both 0, so off, as shipped). With both set, a message of at most that many
  characters to a WARM session passes the headroom rule while the session is under the window. The
  cold rules do not change.

- **A door for a kill whose target the shell computes** (`hooks/killdoor.py`, `kill` in the rule
  doors). On 2026-09-28 a session ran `pkill -P $(pgrep -o -f "…" || echo 1)`: the search found
  nothing, the fallback printed `1`, and `pkill -P 1` ended every app on the machine. The door reads
  the Bash command string and names a `kill`, `pkill` or `killall` whose target comes from `$( )`,
  backticks, a variable or `xargs`; any `pkill -P` or `pkill -f`; `pgrep -f`; and pid 0, pid 1 or a
  negative pid; a `pkill`/`killall` that selects every process (no pattern, `-v`, `.`, `-u` with
  no name, or a pattern that matches every name). It reads inside substitutions, `bash -c` (options
  before `-c` and trailing arguments included), `eval`, here-strings, background jobs, shell
  heredocs, the rest of a heredoc opener's line (`cat > f <<'EOF' && pkill -P 1`), `if`/`for`/`while`
  bodies, `case` arms (`stop)`, `stop )`, `$s)`, and a `case $(…) in` subject written over several lines), `( )`, `{ }`,
  function bodies, `xargs` through `sudo`, `env`, `nohup` or `timeout`, and lines joined by a
  backslash. It reads `\kill`, `pk''ill` and `PKILL` as the command they are, and a quoted or escaped
  option (`"-P"`, `\-P`, `$'-P'`) as the option. A kill nested more than six shells, evals or
  substitutions deep is refused, because the door cannot read it. A heredoc with a quoted end word
  (`<<'EOF'`) is text the shell never runs, so a commit message that quotes a kill passes; a
  here-string (`<<<'abc'`) is not a heredoc, so the command after it is read (in `shellread`, which
  the delete door shares), and a multi-line here-string and a `<<-` heredoc with a tab-indented end word are read whole; a `# comment` on the command line is not searched, while a `#` or an apostrophe inside an unquoted heredoc body hides nothing, since bash expands `$( )` there; a quoted heredoc body is cut at `&` nowhere, and an escaped quote outside quotes (`it\'s`, `'\''`) opens none (in `shellread` too); a backslash at a line end joins the next line as bash does, so `make && \⏎ rm -rf dist` still shows the delete door its `rm`; the kill door reads `<<\EOF` as the quoted heredoc it is. A part of the command the door cannot
  read costs only that part, never the rest. `kill 12345` with the digits typed out passes, and so
  does `kill -0` with any target and no second signal option, because signal 0 sends nothing and
  only asks whether the pid is alive; `kill -0 -s 9 -1` is refused, because bash's `kill` reads
  every signal option before the first pid. It warns until
  `limits.kill_deny_from`, then refuses inside the vault or a marked project (a note elsewhere);
  `kill_door: false` turns it off. The refusal says what to do instead: the pid captured at launch, letting the run finish, or
  handing the line to the owner.

- **A refused `/compact` is handed to the session, not only to you.** A PreCompact hook's refusal
  is shown only to the person (the hooks documentation, fetched 2026-09-28). A second PreCompact
  registration now carries `asyncRewake`: when the door refuses, it wakes the session with the
  refusal as a task, so the session fixes its point and says READY again. It never blocks anything
  and stays silent on a pass, a `/compact force` and every automatic compaction. That Claude Code
  honours `asyncRewake` on PreCompact is read from the field's general definition; it has not been
  seen live yet, so the door's own refusal also tells you to ask for `/compact-ready` if the
  session has not answered within a minute. The check command in both refusals is one
  shell-quoted line, safe to paste.
- **`compactpoint.py check <file> --session-id <id>`** runs the door's own check (age, the three
  lines, size) on the point the door will judge — this session's newest point in any file it
  recorded, or in a handoff — and says NOT READY, naming that file, when it is not `<file>`. The
  session id defaults to `CLAUDE_CODE_SESSION_ID`, as for `write`; without one, where the door is
  on, it never says READY: a point that passes on its own gets `NOT CONFIRMED — pass --session-id`
  and exit 1, because the door may judge another file. Of two points stamped the same minute the
  later one counts, as at the door.
  READY names the minute the point turns too old (`READY until HH:MM`). `/compact-ready` runs it
  before it says READY. **`compactpoint.py renew <file>`** restamps the newest point's heading,
  because editing an existing point kept its old stamp and the door refused it as stale
  (2026-09-28, 46 minutes); it changes only the heading's bytes, keeps CRLF line endings and the
  file's mode, renews a symlink's target and leaves the link, and refuses a file that is not UTF-8.
  Text written below a point is named when it pushes the point past 8,000 characters (2026-09-27,
  10,859), and `write` refuses to leave another writer's text after a new point.

### Changed

- **The README is short; the detail moved to `docs/`.** The README now says what the plugin does,
  how to install it, a ten-minute demo path with commands that were run, and what is in the box.
  The door table, the hook table, configuration, platforms, measurements and the publishing notes
  are pages under `docs/`, and `docs/why-hooks-not-prompts.md` explains the design choice with
  dated figures. The tests that check the README against the code read README and `docs/` together.
- **The context notices fire once per context WINDOW, not once per session.** A compaction clears
  the levels fired and the cached floor; the next window's floor is its own first call. The REACH
  notice no longer says the gate refuses "every" message, and with `compact_point: "on"` it prints
  the `compactpoint.py write` line for the session (it writes nothing).
- **The claim line at a prompt is printed only when it changed.** Session start always prints it.
- **Parked messages name their sender through the session registry**, as the gate names targets,
  so a resumed sender is no longer "?".

### Fixed

- **A session that had just compacted was measured by its old figure.** The resume gate and the
  context notice read the last usage record, which after a compaction is the old window's until the
  session's next call (specimen: refused at 374,589; the next call measured 118,717). A compaction
  boundary after the last usage record now reads as "compacted, not yet measured": the gate lets
  the message through (the Fable-cold rule still applies) and the notice stays quiet.
- **The session record is written whole or not at all.** Every writer of `session-start-<sid>.json`
  (the session-state update, the claims, SessionStart) writes a hidden temp file beside it and
  renames it into place, keeping the old mode and writing through a symlink, so a reader never
  sees it half-written. The session-state update still holds its lock across the whole
  read, change and rename.
- **The launch-pin door no longer warns on a `claude` call that launches nothing.** It warned on
  `claude --version; claude remote-control --help`. `--help` and `--version` anywhere in the call,
  `claude plugin …` and the other admin subcommands, and `claude remote-control` (which has no
  `--model` flag; the Project sets the model) now pass. A quoted prompt is one word, so
  `claude -p "what does --help do"` is still a launch; only `claude`'s own words count, so a
  `--help` in a heredoc body, after a `&` (a glued `)&` too) or in a comment does not make a launch
  pass; a comment starts only where bash starts one, at the start of a word; after `--` a
  `--help` is the prompt, so `claude -p -- --help` is a launch. A `$( )`, `<( )`, `>( )` or
  backtick span is one word whose parentheses cut nothing, and a heredoc body is cut out with the
  lines after its end word kept, on any line of the command; a `<<` inside quotes or `$(( ))` is
  text, not a heredoc (`claude -p "std::cout << x` ⏎ `…" --model … --effort …` keeps its pins), and
  a `<<` with no end-word line after it cuts nothing, so `claude --resume $(cat .sid) --model … --effort …` and `claude -p
  "$(cat <<'EOF'` … `)" --model … --effort …` keep their pins; a `--model $(cat .model)` counts as
  pinned, as it did before. `claude --settings s.json mcp list` passes. The door now also sees a launch behind `nohup`, `sudo -u me` /
  `--user me`, `time -p`, `timeout`, `env --unset`/`--chdir`/`-`/`-S '…'`, inside `$( )`, in a
  heredoc a shell reads (`bash <<'EOF'`, `cat <<EOF | bash`), in a function body and after
  `then`/`do`. `command -v claude`, `-V`, `-pv` and `-p -v` only look it up, and only that
  command is skipped: `command -v claude & claude -p "task"` is still a launch. A quoted heredoc
  body (`<<'EOF'`) is text the shell never runs, so a commit or PR message that quotes
  `claude -p` passes. The older opt-in `require_launch_model` / `require_launch_effort` flags read
  launches the same way (they kept their own copy of the list); with both off they read nothing,
  and a command the reader cannot read is logged and runs, and costs no later door. The shared
  prefix reader (`shellread.strip_prefix`) reads on from the value of `env -S` /
  `--split-string`, so `env --split-string kill -9 -1` reaches the kill door and `env -S git -C /v
  add -A` the git doors; an empty or one-word quoted value (`env -S '' git add -A`, `env -S 'nohup'
  kill …`) keeps the head word after it.

- **`compact_door.py --help` no longer hangs.** Any argument that is not a hook entry prints the
  usage and returns before reading standard input, which a terminal never closes.
- **The session record is never read half written.** `session-start-<id>.json` was rewritten by
  truncating it and writing it again, while every door reads it without the lock; a reader in
  between saw an empty record. The compact door's wake-up hook read it that way and told a session
  its `/compact` had been refused while the door had let it through (345 of 8,573 threaded reads in
  review). Every writer of the record (`update_session_state`, the SessionStart record and the
  region-claim record) now writes a new file beside it and renames it into place. The wake-up hook
  also judges a refusal again after a second and stays silent when the door recorded a pass or a
  `/compact force` for the same `/compact`.
- **A stale handoff is saved, not renewed.** When the point the door judges is a handoff (stamped
  by its modification time), the task handed to the session now says to re-check its three lines
  and save it; it said to run `renew`, which refuses a file with no compact-point heading. For a
  point with a heading, the task says to re-check the three lines now and only then `renew`.

## 0.5.0 — 2026-09-27 — Linux and Windows, and checks for rules a script can decide

### Added

- **`tools/sessions.py` starts, lists and closes Claude sessions that run in the background**
  (`launch`, `ls`, `close`), each in its own `screen`. `launch` refuses a second session for the
  same row, worktree or name. `/gedaechtnis-status` shows the sessions page. A check at Stop lists
  the sessions that have finished and closes them only once `session_close_apply` is on.

- **The close check reads each session, not the folder it runs in.** A session counts as finished
  when it wrote a handoff or report, or its last words say the work is done, and it has then been
  quiet for `session_close_idle_minutes` (now 180). It is kept, however long it sits, when its last
  words ask or wait, a tool call or a prompt is still open, a background task, agent, monitor or
  scheduled wake-up it started has not reported back, or you have typed into it. Unsaved work
  counts only for files the session itself wrote, or its own worktree. Sessions started outside
  `sessions.py` are judged too; ones in a terminal window, an attached screen or a name in
  `session_close_exempt` are left to you. Every proposal names the command that reopens it
  (`claude -r <name>`).

- **`sessions.py close --replay DAYS`** runs the close rules over the last DAYS of transcripts and
  lists, per session, when they would have closed it and whether it was used again afterwards.

- **`session_cap` counts working sessions, not idle ones; `session_cap_per_seat`** (off by default)
  limits how many working sessions one orchestrator may launch; and on a machine without swap the
  memory limit reads memory pressure instead.

- **A Boot-file note.** A bullet written into a Boot file (`Kernel.md`) that only applies to one
  tool (it names a tool's command or file pattern) is pointed at the facet file beside it, where it
  loads only when that tool is used. It warns until a refusal date you set in the config.

- **The session-start facts can be given a size limit** (`limits.facts_max_bytes`, off by default).
  Past it, the longer lists move behind `/gedaechtnis-status`, and the facts say what moved.

- **A test-suite lock.** While the test suite runs it holds `~/.claude/gedaechtnis/suite.lock/<pid>`;
  the row-done door waits on it (every 0.5 s, up to 20 s) instead of refusing whenever any pytest
  process is running on the machine. A pytest run that is not this suite no longer delays a write.
  `python3 tools/suitelock.py status` shows the holder.

- **An apostrophe door, for repos that declare one.** A repo whose owner writes a
  `.gedaechtnis-apostrophe` file (which apostrophe codepoint each part of the repo uses, and which
  file converts between them) gets a check on every Edit, MultiEdit and Write: an in-word apostrophe between two
  Cyrillic letters in the other codepoint is named, and refused once `apostrophe_deny_from` is
  set. `python3 hooks/apostrophe_door.py walk <repo>` checks the whole declared file set. Without
  the file nothing happens. Bash writes are not seen by the door; the walk is for those.

- **Six rule doors (`hooks/ruledoors.py`).** Rules that were prose in a Boot file and a script can decide: no secret (API key, token, private key, card number) added to any file but a `.env`; an HTML page starts with `<!doctype html>` and `<meta charset="utf-8">`; every `claude` launch pins `--model` and `--effort`; a page someone judges is not a bare Markdown ballot; a queue row's two id readings agree; a lane's marker and its roster row list the same paths. Each WARNS by default and REFUSES from a date you set (`<door>_deny_from` in the config's `limits`), only inside the vault or a repo carrying an `.atlas-lane` marker; elsewhere it is a note. The marker door only ever warns. Each is logged in `ruledoors.log`, and each refusal names the rule and the ruling or row it came from.

- **Ethos (150 lines) and Eidos (400 lines) in the cleanup trigger's per-role table.** The rule this table was ported from named both; the table did not, so the trigger never read those two files.

- **The compact ritual, off by default (`compact_point: "on"` turns it on).** `/compact-ready`
  writes a compact point into the session's own state file: a stamped heading, what the plugin
  knows (context size, live managed sessions, claims, the merge window, uncommitted files), and
  three lines the session fills (orders in force, what is live, the order to resume in). A
  `PreCompact` hook then refuses a typed `/compact` while the newest point is missing, more than 30
  minutes old, has an unfilled line, or is over 8,000 characters. `/compact force …` always passes.
  An automatic compaction is never stopped, and a hook error lets the compaction through. After
  compaction the point is put back into the session word for word. A handoff carrying the three
  lines counts as a compact point.

- **A README section, "Platforms":** macOS fully; on Linux and Windows, exactly what differs.
- **`tools/trash.py`:** moves a file or folder to this machine's Trash (the Recycle Bin on
  Windows), or leaves it in place and says why. Never deletes.
- **`rowdone.py flip <q-id> --vault-sha <sha> --by <session>`** marks a row finished by a commit in
  the memory vault rather than a code merge; the sha must be a commit there.

### Changed

- **A first install is asked too.** The "This project has no memory yet — create one?" question used
  to appear only once a memory folder existed, so an install from a plugin directory, which starts
  with none, was never asked. It now appears there as well and names the folder `yes` creates.

- **The delete door names the Trash command that exists on your machine:** `/usr/bin/trash` on
  macOS (unchanged), `gio trash`, `trash-put` or `tools/trash.py` on Linux, `tools/trash.py` on
  Windows. Where the plugin knows no Trash, it says to move the file into a `Cleanup` folder.
- **Worktree removal and `retention.py apply` send things to the Trash the same way as the delete
  door:** `/usr/bin/trash` on macOS (instead of Finder, which follows a symbolic link to its
  target), the Linux and Windows routes elsewhere. Finder is left only for a Mac without
  `/usr/bin/trash`, and there a link is refused.
- **Refusal wording no longer assumes one owner** ("~/Pictures holds photographs").
- **The README's "nothing calls `rm`" now names its one exception:** a worktree clone the plugin
  made, holding nothing unique, is deleted rather than sent to the Trash.

### Fixed

- **Windows: the hooks no longer fail on import.** File locks used `fcntl`, which Windows does not
  have; they now use `msvcrt` there.
- **Windows: the worktree sweep no longer ends the processes it checks.** `os.kill(pid, 0)` is a
  harmless check on macOS and Linux and terminates the process on Windows; there the sweep now
  treats every process as alive and keeps the worktree.
- **Linux: worktree clones.** GNU `cp -c` is not a clone (it means `--preserve=context`); Linux now
  goes straight to `cp --reflink=auto`.
- **Linux: the session's own temp folder** is looked for under `/tmp`, not macOS's `/private/tmp`.
- **The apostrophe door now sees a swap at the edge of an edit.** An Edit that began or ended at
  the apostrophe itself was judged without the letters beside it and passed; it is now judged
  together with the text on either side.
- **`apostrophe_door.py walk` reports a file it cannot read** and exits 1, instead of skipping it
  and exiting 0.

### Known limits

- Linux and Windows are tested by making the plugin believe it runs there, on a Mac. Neither has
  run on a real machine yet.

## 0.2.0 — 2026-09-26 — safe to install from a plugin directory

### Changed

- **No hook answers your permission prompt any more.** The read notices, the sub-agent 8-of-8
  warning, the resume note and the authority-ledger warning used to return
  `permissionDecision: "allow"` with their text, which skipped the prompt you would otherwise have
  seen. They now return the text alone. **Re-check:** if you relied on those calls not prompting,
  allow them in your own permission settings.
- **Four doors act only where you asked for them.** The worktree-placement door, the inherited-tag
  push door, the two sub-agent rules (the `fanout: allowed` permit and the `agent_max_concurrent`
  cap) and the resume gate refuse only in your vault, in a project carrying an `.atlas-lane`
  marker, or in a session started in one. Elsewhere they say what they noticed and let the call
  run. The operating rules are injected at session start under the same condition. **Re-check:** a
  project you use without a marker no longer gets these refusals; run `init.py` there to add one.
- **`init.py` no longer writes `~/.claude/settings.json` by default.** The plugin-outage check is now
  `--outage-check` on a full run, or `--install-outage-check` on its own. `--no-outage-check` is still
  accepted and changes nothing. Run from an installed plugin (`${CLAUDE_PLUGIN_ROOT}` or anywhere
  under `~/.claude/plugins/`), `init.py` adds no `~/.claude/skills` symlink and writes no settings.
- **Manifest:** `displayName`, `license` and a plain description added; the redundant `hooks` key
  removed (`hooks/hooks.json` is found by convention). Every hook command now quotes its whole
  script path (`"${CLAUDE_PLUGIN_ROOT}/hooks/x.py"`).

### Added

- **`tools/publish_check.py` checks the directory's submission list** on a plugin root and prints
  PASS or FAIL per line: no `__pycache__`/`.pyc`, no `.DS_Store`, no symlinks, no file over 256 KiB,
  at most 512 files, the manifest keys, the hook command form, no inline `python3 -c`, and a
  `.gitignore` that excludes the three. Any FAIL makes the check exit 1.
- **README:** a table of every door (where it acts, its default, how to turn it off); what the
  plugin runs, sends and fetches (nothing is sent or fetched), checked against the source by
  `tests/test_publish_surface.py`; how to install from a plugin directory. `PRIVACY.md`.

### Fixed

- **The worktree sweep passed a path inside AppleScript text.** A worktree path containing `"` ended
  the string, and the rest of the path ran as AppleScript. The path is now an argument, as
  `retention.py` already did.

## 0.2.0, earlier work in the same release — a push that was measured, rebuilt, and held back three times

### Added

- **A facet can bind in every session** (`scope: always` in its frontmatter). Until now a facet was a
  candidate only for the session's own folder and the folders of the paths it touched, so a facet in
  a shared folder such as `Global/` — which no repo session's cwd resolves to — could never load. A
  `scope: always` facet is a candidate from any cwd; its tools/bash/paths/rows trigger still has to
  match, and it still loads once per session. Only the facets that say so travel: a region-scoped
  facet in the same folder stays home. The SessionStart facts block lists every always-facet with
  `any region` beside its size. **Re-check:** nothing, unless you add `scope: always` to a facet.

- **A finished queue row marks itself** (`hooks/rowdone.py`). When `mergesha.py verify-merge <sha>`
  prints "on main", each row that the newest handoff's `## ROWS:` line names with that sha is flipped
  `[x]`, gets `  - done: <sha> <date> by <session>` as its last sub-line, and is committed
  path-limited in the vault. At Stop the same runs over every handoff the session wrote, and over
  merge-commit SUBJECTS (`Merge <ID>` / `(q:<ID>)`) on `main` of the fleet repos from the last 14
  days. A row in another lane's queue is never written. A notice goes to your own outbox, and that
  lane's next SessionStart prints `QUEUE-STALE-OPEN <id> <repo> <sha>`. The mechanism refuses, in one
  line, when:
  - the sha is not on main;
  - the id is on no row (`FILE-IT`) or on more than one;
  - the queue file is dirty;
  - a pytest is running.
  It is off in any repository without a `.rowdone` file naming its queues and handoffs.
  **Re-check:** a Stop now adds up to one `git log` per fleet repo; the Bash PostToolUse timeout is 30 s.

### Changed

- **A message that would wake an idle session at a cost a fresh start avoids is refused.** A
  `SendMessage` to an idle session on this machine is refused when that session is cold (its last
  record is older than its own prompt cache lives: one hour if it wrote 1-hour cache, otherwise five
  minutes) and its context is at least `resume_cold_cap` (200,000 tokens); when it has less than
  `resume_min_headroom` (70,000) tokens left before `resume_window` (420,000), warm or not; or when
  it runs Fable 5.1, at any size. Every decision prints the session's size, headroom and what the
  wake costs at its model's cache rates (write rates marked assumed). A first line
  `RESUME-OVERRIDE: <reason>` sends anyway and is logged to `deny.log`.
  **Re-check:** if a script wakes long-idle sessions on purpose, give its message that first line.
- **A warm Fable session can be messaged again.** The Fable rule now refuses only a Fable session
  whose cache has expired, and not even that when the session you are messaging is the one that
  started yours (your first prompt says it ignited you). A report back to the session that launched
  you goes through with its price line. Cold-and-large and the headroom bound still apply to it.
- **Messages to sub-agents are checked too.** A `SendMessage` to a sub-agent's id is measured from
  that sub-agent's own transcript and gets the same bounds and price line as a session. When no
  transcript can be found it passes with an "UNMEASURED — no transcript" note; a missing
  measurement never refuses.
- **A vault commit whose message describes a command is no longer refused for it.** The vault-git
  rule now reads only the part of a command the shell runs: text in a heredoc, in single quotes, or
  in double quotes outside `$( )` is treated as a message. So a commit message that mentions the
  cached-removal form, or the `diff --cached --name-only` check, is judged by the commands actually
  in the line. The delete rule likewise ignores the body of a heredoc written with `cat` or `tee`.
  **Re-check:** a bare vault commit whose message merely *names* the assert check is now refused
  (it used to pass).

### Fixed

- **The delete door sees removals the shell runs indirectly** (DELETEDOOR-3). It used to read
  only segments whose own verb was `rm`/`rmdir`/`unlink`. It now also reads a shell heredoc body
  (`bash <<EOF`, `cat <<EOF | sh`) as commands, a Python heredoc or `python3 -c` string as code
  (`os.remove`, `shutil.rmtree`, `Path(…).unlink()`, an aliased import of one, and an `rm` or
  `find` command in a string or list), `( … )` and `{ …; }` groups, and refuses a `perl`/`ruby`/
  `node -e` one-liner that names a removal. The command-line reading lives in one module,
  `hooks/shellread.py`, which both this door and the vault door import.
  **Re-check:** a Python string that begins with `rm …` is read as a removal command, even in a
  `print`, and a removal call whose target is not a string literal is refused as unseeable.
- **`verify-merge` releases the merge window after a chained command** (MERGEWINDOW-2).
  `verify-merge <sha>; echo rc=$?` used to leave the window held until it expired, because the
  release read `<sha>;` as the sha and `echo` as the repository. The command is now read the way
  the doors read it, and the window is released only when the sha verified is the one reported
  on main. The door also now requires the window for commands that move `main` without checking
  it out: `git push . x:main`, `git fetch . x:main`, `git branch -f|-M|-C` onto main,
  `git update-ref refs/heads/main`, `git checkout -B main`, `git switch -C main`.
  **Known limit:** `git reset --hard <x>` on a checked-out `main` still moves it unseen.
- **`git -C <repo>` is read only before the subcommand.** A `-C` that belongs to the subcommand
  (`git switch -C main`, `git commit -C HEAD`) was taken as the repository, so a door could judge
  the wrong repository.
- **The delete rule reads an interpreter's heredoc as code** (DELETEDOOR-2). A `python3 - <<'EOF'`
  body is tokenized: comments and ordinary strings are prose, so a string that mentions
  `git rm --cached` beside a vault path no longer asks; a removal call (`os.remove`, `shutil.rmtree`,
  `Path.unlink`, `subprocess.run(['rm', …])`) on a vault path does, in a heredoc or `python3 -c`.
  A `bash`/`sh` heredoc is read line by line as commands. `bash -c 'rm …'` and `true;rm …` are now
  seen (the verb needed whitespace before it). **Re-check:** a Python heredoc whose only mention of
  `rm` is in a string or comment no longer prompts.
- **A command prefix no longer hides `git` from the vault door** (GITPREFIX-1). `env X=1`, `VAR=1`,
  `sudo`, `nohup`, `time`, `command`, `exec`, `nice`, `caffeinate`, `builtin` — with their value
  options, and by basename (`/usr/bin/env`) — are read through before the head word, so a prefixed
  `git add -A`, `commit -a`, `--amend` or bare commit is refused like an unprefixed one. Not covered:
  a quoted or escaped `git`, `xargs git`, `( git … )`, `timeout N git`.

- **A claim you already hold is named as yours.** When a region claim is not held, the session-start line now gives the holder's pid, and says so when that holder is this session's own Claude process, so a session does not wait on itself.
- **A session standing in the vault still commits under its own lane.** The vault carries no
  `.atlas-lane` marker, so a session whose working directory had moved into it was treated as
  having no lane and its writes were left uncommitted. The Stop hook now also reads the lane the
  session declared at start (checked against that marker again), and then the marker above the last
  directory the session worked in. When none of those gives a lane it still commits nothing, and
  the log line says which readings it tried.
- **A turn end that commits nothing now says so.** It used to return without a log line when
  nothing the session wrote was in its partition. It now writes one line to `commit.log` with the
  lane, the working directory, how many files the session wrote and how many partition files are
  uncommitted. Files in the partition that no Stop committed (because a script or a shell command
  wrote them, not Edit or Write) are listed at the lane's next session start.
- **Compaction only moves entries out of memory role files.** Before, it moved entries out of any
  markdown file over `max_memory_file_bytes`, apart from folders the vault had declared as queues
  or notice outboxes. A vault that had not declared its queue folder had a work queue compacted:
  its open rows were moved into an archive file that nothing reading the queue looks at. Now a file
  is compacted only if its name is one of the role names (`Position`, `Canon`, `Errata`, …); every
  other file is left as it is. A file over the limit that is skipped for this reason is logged in
  `maintenance.log`, and the next session start lists it with the advice to archive its closed
  entries by hand. **Re-check:** if you raised `max_memory_file_bytes` in `config.json` to protect
  a queue, you can remove that override.

### Added

- **A merge window: one session moves `main` at a time** (MERGEWINDOW-1). In a repository whose root
  carries `.merge-window`, `git merge`/`rebase`/`pull` run with `main` checked out is refused unless the
  session holds the window: `python3 hooks/mergewindow.py grant <session-id> <repo>` (the refusal
  prints the line). `mergesha.py verify-merge` releases it; an unverified window expires after 90
  minutes. `--abort`/`--continue`/`--quit`/`--skip` always pass. Not seen: `push . x:main`,
  `branch -f main`, `update-ref`.

- **A note when a new line defers work without naming its queue row** (`deferral_door`, off by
  default). After an edit, new lines in `.md` files and in `.py` comments and docstrings that say
  work was filed, tracked, deferred, left for later or for a follow-up, and carry no `q:` id, are
  listed with their line numbers. It never blocks. **Known limit:** measured over 30 days of this
  vault's reports it flags 1,084 lines and most are ordinary prose ("two runs later",
  "git-tracked"), so it stays off until the phrase list is narrowed.
- **Chores that move or remove your files now propose first.** Compaction, the Boot-file roll and
  the worktree sweep only list what they would do until you set `compaction_apply`,
  `boot_roll_apply` or `worktree_sweep_apply` to `true` in the `limits` object of `config.json`.
  When they act, one pass moves at most `max_move_share` (0.25) of a file and changes at most
  `max_files_per_pass` (10) files; a file that changed while the pass was working is left alone;
  and each compaction commit lists what moved and ends with the command that undoes it. The
  worktree sweep now only removes worktrees under a repository's `.claude/worktrees/`.
  **Re-check:** if you relied on compaction, the Boot-file roll or the worktree sweep acting, switch
  them on; the next session start will list what they propose.

- **A merge waits for a green full test run on the tree it ships** — off unless the config says
  `"suite_gate": true`. Each pytest run appends one line to a record under the repository's git
  directory (commit, tree, full or partial, whether tracked files were modified, counts); before a
  `git merge <rev>` or `git push`, the Bash gate looks for a green, full, clean run on that commit
  or on one with the identical tree, and refuses in words when there is none. It never runs tests.
  A repository opts in to the record with a root `conftest.py` that loads `hooks/suitegate.py`.
  **Known limit:** a repository whose full suite has standing failures cannot turn this on until
  they are fixed; the gate has no list of accepted failures.
  In the repository this was built in: gate present, switch off, standing reds 32 (measured by name on three full suites 2026-09-23: eb69ba68, 2fd1b9ec, 95791c43; an earlier draft said 35), all
  outside this package and each reproduced alone on 6f1cbd94: 13 read live state that has moved on
  (5 calibration pins, 3 SEATTRIAL-2 wiring checks written for a seat that was reverted, the
  decision-card sweep of the real review folder, the live reality-check extractor pins, the
  measure-series overlay, the notice-fanout capture, the wind-down opener's model citation), 1 a
  pricing table that disagrees with its sibling copy, 1 a missing `DEEPSEEK_API_KEY`, 9 dashboard
  ignition tests the dashboard's own sandbox guard now refuses, and 8 sibling-fix probe replays
  that return no candidates. By name and class: `.orchestration/review/complete1-build-2026-09-14/
  REDS32-2026-09-23.md`.
  The record is shared by every worktree of one repository, so a full clean run of the same TREE in
  any worktree covers a merge of it — by design (a rebase keeps the tree), and worth knowing.
- **A merge claimed in a message is checked with git** — when a message from another session says
  `MERGED <sha>` or ``merged `<sha>` ``, a prompt hook asks git whether that commit is on `main` in
  the session's repository (else in the vault) and adds the one-line answer to the session's
  context: on main, NOT on main, or unknown object, plus the files a vault commit touched. It
  never blocks. `hooks/mergesha.py verify-merge <sha> [<repo>]` gives the same answer on the
  command line, with exit code 0 / 1 / 2.
- **Session start lists the managed sessions that have stopped moving** — with `session_name_pattern`
  set, a "Managed sessions" block names every live session whose `--name` matches: its model, how
  long it has run, and its last line with its age, read from its transcript. A session whose last
  word is the account-limit message, or that has said nothing for `stall_minutes` (default 20),
  is marked STALLED. The open-worktree line is shown beneath it. Read-only; absent when the
  pattern is unset, and the worktree line then prints on its own as before.
- **A session tells the seat that started it when it stops** — off unless the installation names a
  `session_name_pattern` over the `--name` its sessions are launched with. A Stop hook fires when
  the session either wrote a handoff or ran out of context; it leaves one message, once, carrying
  the handoff path and the session's last line. Who hears it is a `seat:` line in the session's
  opener, else a `seat` group in the pattern, else nobody — there is no default recipient. The
  message is appended to that seat's mailbox under the state directory and the seat is told at its
  next session start; `notify_command` optionally hands the same message to a live transport, and
  runs after the mailbox write so a transport that is missing or slow costs a late message rather
  than a lost one.
- **`hooks/idlenotify.py exit --name NAME`** — the same report from a launcher, after the process
  has exited. A session killed at its account limit, by a crash, or by a closed pane never runs its
  Stop hook, and those are the exits a waiting seat most needs to hear about. It reads the record
  the hooks already wrote, so the two paths cannot disagree.

- **A BOOT-TIME arm of the lesson push, also OFF by default** (`lesson_push_boot_enabled`,
  `lesson_push_boot_max_lines`). At SessionStart it names up to five lessons whose citations
  appear in what this session is structurally pointed at — its resolved `@`-import chain, the open
  rows of its own work queues, and, on a resume, its opening prompt. No edit is matched, because
  the two write-time designs both failed at the moment rather than at the matcher. Every line is
  logged to `lesson_push.log` with `trigger:boot`.
- **`eval/lesson_push_replay.py --boot`** — the boot arm's own three gates: two matched controls
  (swap the lesson corpus to a foreign region; swap the structural surface to another lane), a
  session-level held-out replay in which each session's block is reconstructed from the vault's
  git history **as of the day that session ran**, and the per-session cost against the measured
  boot floor.
- **Session start names the path rules that are off.** Four rules act only on paths the vault
  names under `topology` in `config.json`: work-queue files (`queues_dir`, which also keeps those
  folders out of recall), the artifact index (`artifacts_index`), shared append-only files
  (`shared_append_files`) and a reserved-name folder (`stem_rule_dir`). A rule whose key is absent,
  empty or rejected is off, and until now an off rule looked the same as a working one. One facts
  line now lists every rule that is off and its key; a vault that declares all four sees nothing.

### Known limits

- **The boot arm is refused too, and that closes the push line.** It is the only one of the three
  that separated from its controls (median set overlap 0.25 against a foreign corpus, 0.11 against
  a foreign surface, zero identical sets over four seeds), and it still surfaced **1 of 17**
  re-broken lessons against a bar of 3. Four of the eight checkable lessons cite nothing at all and
  are unreachable by any citation-based arm.
- **It has lane resolution, not session resolution.** Over 92 sessions the block was identical in
  all 92 — five lines, 747 bytes, ~187 tokens, 0.13% of the median boot floor — because a boot
  chain and a queue file belong to the lane and the day, not to the session. Given the opening
  prompt as well, 20 of 40 sampled blocks become distinct and the held-out result does not move.
  And the opening prompt is not there at a cold start: measured over 97 sessions, the first user
  record was written after the SessionStart hook in 96 of them, median +0.86 s.

## Unreleased (earlier in the same cycle) — the two write-time designs

### Fixed

- **A write from a git WORKTREE is no longer recorded as another lane's work.** `chore`'s foreign-
  work record decided whose a write was by comparing the written file's repo root with the session
  cwd's root. A worktree has its own root, so a lane's own build worktrees compared unequal: 32 of
  one lane's own reports and handoffs were filed in another region's `Inbox.md` in a single
  morning, and 7 more were filed under `UNKNOWN-LANE` because the session stood in the vault, where
  no marker lives. Identity now comes from the LANE each side
  DECLARES: for the write, the marker of the checkout that owns the repository it went into
  (`common.lane_of_repo`, which resolves a worktree to its main checkout the way
  `git rev-parse --git-common-dir` does, reading the `.git` file rather than starting a subprocess);
  for the session, its cwd marker, and failing that the lane it declared at boot, which is what
  survives a `cd`. A write into another lane's repo, worktree or not, is still recorded — including
  from a session standing in the vault. When neither side declares a lane the row is still written,
  under an unknown lane: a record that cannot name the writer is worth more than no record.

### Added

- **A fan-out door on `Agent` calls (`hooks/fanout.py`).** A sub-agent may not start a sub-agent
  unless its own brief carries a `fanout: allowed` line (or the vault sets `agent_fanout_allowed`),
  and no session may hold more than `agent_max_concurrent` live sub-agents — a warning on the call
  that reaches the cap, a refusal above it. The cap ships at **8**, the p90 of peak concurrent
  sub-agents per session measured over 30 days of transcripts (99 sessions, 2,306 sub-agents;
  median 3, p95 11, max 24) with `scripts/agent_concurrency.py`. It exists because two uncapped
  research agents spawned sub-agents of their own on 2026-09-20 — about 14 sessions at once, 30% of
  a five-hour usage window gone in roughly five minutes. A sub-agent is recognised by the payload's
  `agent_id`, or failing that by a transcript path under `subagents/`; `session_id` is the parent's
  in both cases and is never read for this. Every refusal is logged to `deny.log`.

- **`hooks/lesson_push.py`, OFF by default.** After a write it names the lessons this vault
  already holds about what was touched — at most two per write, once per heading per session, at
  most ten per session, every line logged to `lesson_push.log`. It never refuses anything and
  never writes the vault. It exists because the PULL path measurably is not used: recall was
  invoked in 1 of 71 sessions in the measured population, and none of the 17 lessons that were
  re-broken had been pulled up first.
- **A second trigger, on Bash.** `chore.py do_bash` calls the same arm for a Bash command that
  literally produces a file (a redirect, `tee`, `cp`/`mv` into a path, or a heredoc into an
  interpreter with a literal `open(..., "w")`). Design 1 rode on Edit/Write alone and measured
  that this was **16.5%** of the file-producing tool calls in its population; with the Bash arm it
  is **50.0%**. The detector takes LITERAL targets only, tracks `cd` across command segments, and
  skips a relative path whose working directory it cannot know rather than guessing at one.
- **`eval/lesson_push_replay.py`** — the gate that decided the default, replaying real tool inputs
  from real session transcripts: the matcher's false-positive rate under a matched control, a
  held-out replay over the lessons known to have re-occurred, the context cost per session, a
  random sample of what the hook would have said, and (`--bash-audit`) the trigger's own verdicts
  on a sample of real Bash commands. `--edit-only` reproduces design 1's population exactly.
- **`lesson_push_enabled` / `lesson_push_max_per_fire` / `lesson_push_max_per_session`** in
  `rules/limits.json`, with both designs' measurements recorded beside the first.

### Changed

- **The matcher was replaced, not tuned.** Design 1 scored shared vocabulary between the write and
  a lesson HEADING. Design 2 matches what a lesson's own BODY cites — a two-segment path, a
  queue-row id, a ruling id, a backticked script or function name, a wikilinked heading — against
  what the write touches, exactly, with no score and therefore no threshold. Ordering is by how
  specific the matching token is.

### Removed

- **`lesson_push_min_score`.** An exact matcher has no score, and a threshold key that changed
  nothing would read as a tuning knob to the next person. The measurement it carried is recorded
  in `_lesson_push_enabled`.

### Known limits

- **It ships OFF, on two designs' measurements rather than on caution**, all recorded beside
  `_lesson_push_enabled`. Design 1: the score did not separate a real match from a matched control
  (signal median 0.358–0.369 against a control median of 0.352–0.371), 0 of 6 checkable held-out
  lessons surfaced at any threshold including zero, 16.9% trigger coverage. Design 2: coverage
  fixed (50.0%), and the matcher still did not separate — real writes fire on 44–47% of a sample
  and a FOREIGN write's tokens on 50–66% of the same index, a separation ratio **below one** — and
  0 of **8** checkable held-out lessons surfaced, every one because the lesson cited nothing the
  write touched.
- **What that means for anyone thinking of turning it on:** the failure is not the threshold and
  not the trigger. At write time all that is known is the text and the path; neither reaches the
  lesson that was about to be re-broken. A lesson whose body cites nothing at all is invisible to
  this matcher by construction. Setting the key true runs it unchanged, at a measured cost of a
  line on 13% of writes.
## Unreleased — a vault's own house style is not one region's topic

**Added.** Before it clusters, the split pass measures how WIDELY a term is written across the
whole vault and strips the ones that are everywhere. A term that appears twice or more in enough
regions is the vault's house style — a `Scope:` line every decision carries, a `Last revisited:`
on every open question — and naming a region after it is naming it after your own file
conventions. The bar is `max(3, ceil(split_boilerplate_region_share x voters))`, shipped at
**0.41**, where a voter is a region that writes SOME term twice or more. The proposal echoes the
terms it stripped and the bar it used, so the judgement is visible rather than implied.

On the vault this was measured over, five proposed rooms — four of them named `ScopeLocked`,
`ScopeCanonical`, `NotesNever`, `FixedImages` after exactly that furniture — become none, and one
room named after an actual subject appears that the furniture had been hiding. The share was set
for OUTCOME STABILITY rather than for today's answer: at 0.45 one more region would have moved the
bar and silenced the pass entirely over 1,134 entries; 0.41 holds the bar steady across a 13–17
voter range.

**Known limits, and this one matters for a new install.** The floor of **three voting regions is
not a knob**, and below it the axis finds NOTHING — so a vault with one or two regions, which is
what installing this package creates by default, gets no protection from this rule at all. The
reason for the floor is that breadth is a statement about several rooms: on a one-region vault
`ceil(0.41 x 1)` is 1, and every repeated word would be called furniture. Whether a convention can
be seen from inside the only room that has one is genuinely open — there is no evidence either
way. Two smaller costs are named rather than smoothed: a region's vote saturates at two entries,
so a stub counts as much as a 266-entry region; and a real topical room can fall under the
eight-entry floor once its boilerplate links are stripped — it is not lost, it returns when it
grows.

**Unattended apply still ships OFF** (`split_auto_apply`, false). The pre-registered bar was zero
false auto-applies and the dry run after this change still produced one arguable room. One
arguable room is not zero.

## Unreleased — the vault's SHAPE is the installation's, not the package's

**Changed.** Every private project name and owner path is out of the shipped tree — hooks, eval,
commands, agents, rules, README, CHANGELOG and the top-level modules. Where one of them was a
literal a rule depended on, the rule now reads the vault's own shape from a new optional
`topology` object in `~/.claude/gedaechtnis/config.json`: `non_region_tops` (default `Global` and
`Channels`), `queues_dir`, `artifacts_index`, `ledger_dir`, `channels_dir`,
`shared_append_files`, `stem_rule_dir`. **A key has a default only where this package CREATES the
folder itself** — `init.py` makes `Global/` and `ledger.py` makes `Channels/ledger/`, so
`non_region_tops`, `ledger_dir` and `channels_dir` default; the other four are **absent by default
and the rule built on each is then OFF** — a vault with no work queues has no queue rule. A key
that is present and unusable is NAMED at session start and by `/gedaechtnis-status`, never
ignored. This
is a correctness change as much as a privacy one: the flat vault `init.py` creates could not
reach several of these rules at all, because the only way in was one particular region's name.

**Changed.** The default vault is discovered rather than named. `~/Gedaechtnis` still wins; where
it does not exist, a directory one level under home is adopted only if it PROVES itself a vault by
carrying `Global/fleet-roster.md`, and only if exactly one does — two real vaults are a question
for the user, not a guess. The old rule looked for one hard-coded directory name.

**Changed.** Refusal prose no longer cites files only one vault has, and the stem rule that used
to be named after one vault's folder is now the **reserved-stem rule**, named after what it does. Every refusal DECISION is unchanged:
measured across 55 hook entrypoint invocations on the vault this grew in, 44 were byte-identical
and the 11 that differed differed only inside `permissionDecisionReason` — no decision, no event
shape, no return code moved.

**Added.** `tests/test_agents.py` now sweeps the WHOLE shipped tree for private names, with a
positive control (a planted word reddens it) and a negative one (`.atlas-lane` and `atlas-region`,
the package's own published names, and ordinary English words containing them, must not fire).
`tests/` itself is deliberately not swept: a fixture's job is to be a concrete vault.

## Unreleased — a boot that tells you which of its sources has been overtaken

**Added.** At SessionStart, a document in the boot chain — your `CLAUDE.md` and everything it
@-imports — that cites a ruling the authority ledger now marks SUPERSEDED is named in one line,
but only where the ruling was superseded AFTER the citing document's own last commit. What you
loaded still reads as it did then; nothing is blocked and nothing is rewritten. Closed work-queue
ids are deliberately NOT part of the boot notice (they are checked at write time instead, where
the author can judge intent). There is no new configuration: it is live the moment it is
installed, and an install with no ledger is silent.

On the vault this grew in, the first run found 3 of 24 distinct rulings cited in the boot chain
already superseded, one of them in a sentence a session acts on daily.

**Known limits.** The notice is self-clearing in a way that is not always a fix: it stops as soon
as the citing document is next committed for ANY reason, whether or not the sentence was
corrected. That is inherent to comparing commit times with no stored state, and is written down
rather than papered over. Cost on a clean boot is zero git calls; a boot with a finding costs
about 8%.

## Unreleased — a citation checked when it is written, not at a sweep

**Added.** When a write puts a work-queue id or a ruling reference into a vault file, the ids are
resolved against the live queue and ledger and a NOTE is appended if one is stale — a row already
closed, a ruling already superseded. It is a note; it never refuses a write and never edits
anything. Tokens inside fenced code blocks and blockquotes are prose, not citations, and are
ignored; tokens in inline backticks are citations and are checked. An install with no queue files
and no ledger stays silent.

**Known limits.** At most 20 ids are resolved per write, and the queue read is bounded by a byte
ceiling past which that half goes QUIET rather than answering from a truncated read. An id inside
a markdown link URL or a table cell is treated as a citation and arguably should not be; neither
shape occurs in the vault this was measured on, so it is named rather than guessed at. Cost:
~0.03 ms for a write with no ids, ~65 ms for one citing thirty.

## Unreleased — the split can apply itself, and it ships OFF because a real vault said so

**Added.** `--auto` on the split command applies a proposal with nobody in the loop, gated on the
parent keeping more than a quarter of its entries (`split_auto_min_remaining_share`, 0.25).
**It ships OFF (`split_auto_apply`, false),** and the reason is the row's product rather than
caution: on the scenario suite it met the shipping condition exactly — zero false auto-applies —
and on a real vault the dry run would have created five rooms, four of them named after document
boilerplate rather than a topic (`ScopeLocked`, `ScopeCanonical`, `NotesNever`, `FixedImages`;
only one of the five was genuinely topical). A fixture cannot contain the conventions of the
system it is a fixture for, so the suite was structurally blind to the failure mode that matters.

**How to check your own vault:** `--dry-run` works with the knob off, moves nothing, and prints
exactly what `--auto` would have done. That is the intended way to decide whether to turn it on,
and the honest reading of the result above is that the naming layer, not the gate, is what is not
ready.

**Known limits.** The share bar is a bar, not a discovered gap: the largest share that should
move and the smallest that should be held are numerically the same in the measured cases, so the
bar works on the asymmetry of the costs (a missed auto-apply costs nothing; a false one costs a
restructured vault), not on a numeric margin. Cohesion was tested as the gate and rejected — it
is anti-correlated here. A boilerplate term passes the core-vocabulary rule by being everywhere,
which is the defect this default is waiting on.

## Unreleased — an authority record its own reader cannot resolve is refused at the write

**Added.** Where a vault keeps a ledger of standing decisions, a write that adds a record with no
resolvable reference of its own, or that supersedes an existing one without marking the old record
superseded in the same write, is caught at the write. Only what the write ADDS is judged — an
append is never refused over old unmarked history somewhere else in the file. The check knows no
path: an install that configures no ledger is completely inert.

On the ledger this grew against (134 records), the mechanism found 3 real rulings superseded in
substance and never marked.

**Known limits.** Like the write partition, this door is in WARN while the partition mode is, and
flips with it.

## Unreleased — the boot floor, measured, and a context threshold that only ever speaks

**Added.** `hooks/context_cap.py` watches the session's own context total and says so once per
level: a WARN at the measured boot floor plus `context_warn_over_floor_tokens` (400,000), a CAP at
`context_hard_cap_tokens` (700,000). It **never blocks anything** — the line rides an allow, and
what happens at the line (finish the row, hand off, start fresh) is a decision for you or your
session, not for the hook.

The numbers come from a census of 157 interactive sessions over 30 days: the median session had
**141,965 tokens of context before it did any work**, p90 177,692, and the median peak was
388,496. The on-disk @-import chain is **21–30% of that floor**, not the bulk of it — a file-size
measurement of your memory files is not a measurement of what a session costs.

**Known limits.** What the rest of the floor is made of — system prompt, tool schemas, skill and
agent listings — is NOT established here, and is printed as unmeasured rather than estimated.
The two thresholds are a taste call about when to be interrupted, and are one line each in
`rules/limits.json` (or in your vault's `limits` override).

## Unreleased — the dad test: what a non-technical user is actually asked

**Added.** `eval/dadtest/` — a 90-day simulated install measured against four criteria: at most
three questions asked of the person, a boot chain inside its budget every day, a cleanup that
becomes due and applies by itself, and wikilinks that still resolve at day 90. Part A drives the
real hooks as subprocesses with no model calls; Part B runs real sessions over a
persona-realistic vault, with a control arm reading the same vault with the shipped rules
stripped out.

**Changed 2026-09-23: criterion 3 is "proposes, then applies once switched on".** Since cleanup
became propose-first, a due pass on a fresh install records what it would change and moves
nothing, so "a cleanup applies by itself" could no longer pass and the harness read every
proposing pass as "never had anything to apply". Criterion 3 now checks four things, each read from
what the real scripts wrote: a due pass proposed something; every proposing pass left the vault
byte-identical; after the simulated person sets `cleanup_apply` (the step the proposal line asks
for), a pass applied and touched at most `max_files_per_pass` files; and its commit carries the
command that undoes it. Criterion 4's baseline moved from day 1, when no links exist yet and the
check could only pass, to the day cleanup is switched on; an empty baseline now reports VACUOUS
instead of PASS. At the default `low` load all three Part A criteria pass (cleanup due on day 15,
first proposal on day 89, applied on day 90); the window criterion 4 sees at that load is one day.

**Criterion 1 passes.** Nine questions counted in the package arm against eight in the control;
after subtracting what the control asks anyway, the package is attributable for **one** question
by the lexical instrument and **none** by reading the transcripts. Both readings are reported,
rather than the flattering one.

**Known limits.** A real session can be a clean stranger's boot or instrumented with the plugin,
not both — the plugin loads from user scope. So the questions about file access in those
transcripts are the harness's, and are counted as a lower bound rather than waved away. The
harness's ninety days all happen on one real calendar date, so nothing it does can support a
claim about a TIME-based trigger; an earlier claim of ours that real users would not meet
maintenance in three months is withdrawn for that reason and is an open question, not a finding.

**What it found in the product, and is not fixed here:** the starter `Position.md` ships
software-project headings (Shipped / In flight / Next), and sessions in BOTH arms called them
boilerplate for a vault that is a garden log. The README now says which vault shapes are
supported; making the fixture more software-like was explicitly rejected as the fix.

## Unreleased — a component is a candidate, not a cluster

**Changed.** The split proposal no longer offers a group just because its entries are transitively
connected. A candidate must share a core vocabulary term across at least
`split_core_term_share` (0.5) of its entries, and a group that comes apart when at most
`split_max_bridge_entries` (3) entries are removed is offered as the separate rooms it really is.
On the vault this was measured against, proposals drop from 13 to 5; the eight groups that are
chains of topic-to-topic overlap rather than one room are now REPORTED as chains and not offered
as moves.

**Fixed.** `split_max_bridge_entries: 0` silently became 3 (a truthy-`or` where a `None` check
belonged). Clustering a thousand tightly-linked entries went from 59.58 s to 0.40 s; nothing in a
real-sized region changed, because it was already half a second.

**Known limits.** `split_core_term_share` is measured, not categorical — the report's earlier word
"structural" was wrong and is corrected: a counterexample family produces proposals at exactly 0.5
and refusals at 0.4, and a shape sharing two thirds of its vocabulary can still be two rooms with
nothing in common at the edges.

## Unreleased — a layer that could not say it was absent

**Fixed.** The per-vault limits layer returned "nothing configured" on a real import failure, which
is indistinguishable from a vault that configures nothing — a broken config read as a clean one.
It now names the failure and the path it failed on, at session start and in the status command.

**Fixed.** A documentation key in `rules/limits.json` (the `_`-prefixed twin that carries a
threshold's prose) is only reported as a possible typo when the real key it shadows is ABSENT.
Until now every boot printed sixteen NOT-APPLIED lines about keys that were working exactly as
intended.

**Known limits.** The limits FILE layer is still unvalidated by shape: a malformed value there can
hand a caller a dict where a number belongs. That is pre-existing and is named rather than
quietly hardened.

## Unreleased — a security review, and a gate that was answering questions it never asked

**Fixed — the publish gate.** `tools/publish_check.py` printed `clean` over symlinks, over
`node_modules`, over any file whose encoding was not UTF-8, and over the entire git history. All
four are now scanned: links are read, never followed; every encoding is tried; commit messages,
ref names, annotated-tag bodies and git notes are read (`0.36 s over 2,370 commits`). Historical
BLOBS are still not scanned — that limit is now PRINTED in the clean line instead of implied.

**Fixed — the write-root floor.** The roots the package is permitted to write under had no lower
bound, so a single config key or environment variable could set them to `/` and permit a write
anywhere on the machine, against this tool's own promise that your home directory is never written
wholesale. A config-derived root must now be a proper descendant of your home or the system temp
directory, and a root that fails the floor is DROPPED, never corrected. Home itself comes from the
password database rather than `$HOME`, which a shell command can set. With every root set to `/`
the package fails CLOSED — nothing is permitted — and that is proved by execution, not assumed.

**Fixed — shell path tracking.** `cd <dir> && echo x > File.md` walked past both write-permission
doors, and a subshell form resolved into the WRONG region — a file in one project's folder judged
as though it were in a shared one. Subshells, `pushd`/`popd`, `cd -` and `cd --` are now tracked,
and a genuinely unknown working directory makes a relative target SKIPPED rather than guessed.

**Fixed — smaller, and each real:** untrusted text from a repo's `.atlas-lane` marker reached the
privileged session-start facts verbatim; `~/.claude/settings.json` was rewritten with no backup
and no fsync; an AppleScript was built by string interpolation from a caller-supplied path; a raw
session id reached two state paths unsanitised; a cleanup could turn a symlinked note into a
regular file; running the graduation twice in one day, and `init.py` likewise, overwrote a backup
with the newer pre-edit state; and the outage check read the raw session id where the session-start
hook wrote the sanitised one, so a session that loaded fine could be told it had not.

**Known limits.** File NAMES are not scanned, only contents. A `.gitattributes` clean filter can
make the committed bytes differ from the worktree, unseen. `sed -i` is only recognised as a write
when its target contains a `/` — a bare relative filename is invisible to that guard.

## Unreleased — the split under scenarios, and two knobs that ship at zero

**Added.** A scenario harness for the split mechanism — twenty-one shapes a region can be in, run
against the real proposal code — and a cohesion-based `diffuse` label for a group whose entries
are connected but not about one thing.

**Changed — nothing, on purpose.** Both new knobs ship at zero (`split_min_remaining_share` 0.0,
`split_min_cohesion` 0.0), and the shipped behaviour was proved byte-identical to before the merge
in both output shapes, with a positive control showing the comparison was not blind (forcing
cohesion to 0.4 turns thirteen splits into two).

**Fixed.** Two pairs of files in the harness claimed the same Python module name, so one silently
shadowed the other; loading is now by path with a basename guard. Six files under `eval/` still
share a module name and are dormant by luck rather than design — named here rather than left for
someone to rediscover.

## Unreleased — a code review at high, over the limits seam

**Fixed.** A limit defined only in the config file, and not in the shipped defaults, was live but
could not be overridden — the one layer that is supposed to be overridable. A non-dict value in
the file layer could turn a per-role override into a wholesale replace. A malformed `config.json`
reported "shipped defaults; config.json overrides none" — a false clean bill of health — and now
reports that it is broken. Three internal caches could disagree with each other and are now
derived from one read.

**Known limits.** One defect is reproduced and deliberately NOT fixed here, because it lives in a
shared door that needs its own review: prose DESCRIBING a `git rm --cached` command — inside a
commit message, say — is matched against the raw command line and falsely refused. It is fixed in
its own change.

## Unreleased — a vault's own thresholds live in its config

**Added.** A `limits` object in `~/.claude/gedaechtnis/config.json` overrides individual
thresholds for THIS vault, without replacing the shipped table and without a machine-wide
environment variable. Precedence: shipped defaults, then the limits file, then `config.json`.
An override that is unknown, `_`-prefixed, of the wrong type, or not an object is REJECTED AND
NAMED — never silently dropped — in the status command and at session start; an install that
overrides nothing says so in words distinct from one whose every override was refused.

**Fixed.** A dict-valued limit — the table of per-role file-size thresholds, eight roles — was
REPLACED wholesale when a vault overrode one role, silently dropping the protection on the other
seven with no problem reported. Whether an override replaces or merges is now decided by the
KEY's own type, in one function, for both layers. The first fix was applied only at the config
entrance and the identical defect was still reachable through the file layer — which is why the
decision now lives in one place rather than at each entrance.

## Unreleased — an over-budget boot chain gets a way out, in words

**Fixed.** An install whose boot chain exceeded its budget was told so every session, forever,
with nothing it could do: the unattended cleanup is forbidden to touch a file the session's own
boot chain loads, and the file that had grown WAS that file. The maintenance line now names the
concrete remedy in the same sentence as the trigger — give the region a Boot file with a declared
window and point the repo's `CLAUDE.md` import at it instead of at the bodies, because that is
what REPLACES them in what a session loads.

**Added.** A `graduate` proposal, which is REPORT-ONLY by construction: it is in a set of kinds
`apply()` cannot act on, and the cleanup command is told to relay it and forbidden to offer to
write a Boot file or edit an import line itself. Writing the file that decides what every future
session loads is not an unattended act.

**Fixed.** A file was charged to both a parent region and its child; it is now charged to the
nearest containing one. Under a symlinked vault the region name came out as a `../../` traversal.

**Known limits.** Nothing here compacts a boot-chain file. The remedy sentence has two forms, and
the second exists because the first was wrong: a region that already HAS a Boot file was silently
dropped from the finding, though writing one moves the chain by zero bytes until the import line
is repointed — silencing a loop that was still broken.

## Unreleased — a split has two sides

**Added.** A proposal is now one of two kinds. A `split` is viable on both sides; a `refocus` is a
child worth its own region whose PARENT would be left below
`split_min_remaining_entries` (4) — so instead of moving anything it suggests renaming the region
you already have and lists what would be left over. `apply()` refuses a refocus id outright,
before touching a file. Viability is re-evaluated at apply time, so a proposal that was healthy
when it was made and is not now comes back as a refocus and is refused.

**Known limits.** A refocus does not offer a destination for the leftovers — the package has no
shared tier to move them to, and inventing one for this was not in scope. The floor counts
entries and does not measure SHARE: a split that moves four fifths of a region still passes it as
ordinary. That is named, not fixed, and it is why unattended apply ships off.

## Unreleased — the publish check runs over any repository

**Added.** `python3 tools/publish_check.py --root DIR` runs the identical sweep over any
repository, through the same patterns and the same functions — no second, weaker path. Over a
`--root` it also looks for phone-number and IBAN shapes, for `.env`-style files (flagged by
existing at all, with the `.example`/`.sample`/`.template` siblings exempt), and, in a git working
tree, for a file that `.gitignore` excludes and yet is tracked — the way a force-added `.env`
survives a "we gitignore secrets" policy.

**Changed.** Every reported excerpt is REDACTED to its boundary characters: a leak report must not
itself be a leak. A walk that finds zero files prints `UNCHECKED` and exits non-zero, and a run
whose root is not a git root now NAMES the three halves it therefore could not run instead of
printing an unqualified `clean`.

## Unreleased — when one topic has outgrown its region

**Added.** `split.py` and `/gedaechtnis-split`: the first mechanism here that can create a new
region rather than one per project. It measures which entries in a region cluster together,
proposes a name you can change, and moves nothing until you say so — `--apply` needs an explicit
`--name`. Entry text moves byte-identically, the parent's Index row is APPENDED to and never
rewritten, wikilinks that pointed at moved entries are repointed, and a receipt in the new region
records what happened. Nothing is deleted. `split_min_entries` (8) is the floor for a region worth
splitting.

**Known limits.** A link is found by what it points at, so a heading containing `]` or `|` cannot
be located inside a wikilink — those entries still move, and the receipt lists them under
UNCHECKED rather than implying they were handled. Two entries with the SAME heading, one moving
and one not, leave a link that still resolves and now points at different content; the receipt
says so.

## Unreleased — what the vault has learned and not yet acted on

**Added.** `/gedaechtnis-synthesis`, the `memory-synthesizer` agent and `synthesis.py`: a pass
that looks for the two things only visible across a whole vault — a rule you have written down
and never turned into a check, and a lesson learned separately in two regions. The mechanical half
finds candidates with high recall and no judgment; the agent judges them and has Read, Glob and
Grep and no write tool at all, which is what makes "it proposes and never applies" a property
rather than a promise. Each finding is tagged `mechanizable: yes / partial / no`, and a `no` is
recorded as a finding too, so the next pass does not re-surface it.

**Fixed.** Neither the cleanup nor the synthesis timestamp was ever written after its first run,
so a time-based trigger that became due once stayed due in every session afterwards, whatever you
did about it. One function is now the sole writer of either stamp, and an unrecognised pass name
raises instead of writing a key nobody reads.

**Fixed.** A directory the scan could not READ vanished from its count with no error, while the
pass reported that the regions had been checked — a measured zero with no failure signal, in the
tool whose whole job is to notice that class. Unreadable directories are now named, and the field
that claimed everything had been checked is gone.

## Unreleased — package hygiene

The plugin was still assuming its parent directory. Three things depended on the private repo it
grew inside; a copy of the plugin alone hit them silently. That is what this entry closes.

### Added

- **`eval/pricing.json` — the price table the package ships.** A live eval run prices every call
  and refuses to start when it cannot; until now the only table was a
  pricing module belonging to the repository this plugin grew in, resolved as a sibling of the
  plugin directory, so a published copy could not price a run at all. The table now ships inside the package and is the DEFAULT. The override
  order is unchanged and still wins: `--pricing PATH`, then `$GEDAECHTNIS_PRICING_PY`, then the
  packaged file. Both shapes load — a `.py` module exposing
  `PRICE`/`PRICE_ASOF`/`price_usage`/`resolve_price_key`/`usage_from_model_usage`, or a JSON table
  in the packaged shape.
- **`eval/pricing.py` — the loader, and the drift guard.** The shipped table is a DERIVED COPY, and
  it says so: its `provenance` block names the module it came from, the upstream pricing page, and
  the date it was copied. A copy of an authority document diverges silently, so the copy is guarded
  rather than trusted — `python3 eval/pricing.py --check <path to that module>` compares the
  numbers, the aliases, the as-of date, AND what a probe usage block prices to under each
  implementation (so a formula that drifts is caught, not only a number), and exits 1 listing every
  difference. Both sides of that check have positive controls in the test suite.
- **A facts line for the region claim.** The claim hooks call a region-claim helper that this
  package deliberately does not ship — it is a fleet asset, and copying a coordination mechanism
  forks it. So on an ordinary install the region claim is **OFF**, which is supported; what is not
  supported is OFF looking like ON. `config.claim_tool_state()` now returns which of four states an
  install is in (`ready` · `off-disabled` · `off-missing` · `off-no-helper`) with a sentence saying
  so; `hooks/claim.py` prints it at SessionStart wherever a claim would otherwise have been taken
  and was not, and `/gedaechtnis-status` prints it unconditionally. Outside a region there is
  nothing to claim and nothing is printed.
- **This changelog.**

### Changed

- `memory_eval/run.py` and `fresh_home/run.py`: the `--pricing` default is now the packaged table
  rather than a pricing module beside the plugin directory. Nothing that passed `--pricing` or set
  `$GEDAECHTNIS_PRICING_PY` changes behaviour.
- The live-eval test module no longer skips wholesale where that external pricing module is absent.
  Only the handful of tests that speak specifically about THAT module skip; the rest run over the
  packaged table, which is the path a published copy takes.

### Fixed

- **The test suite is green in a copy of the plugin alone.** Outside the repo it was written in,
  `tests/test_memory_eval_live.py::test_live_without_claude_refuses` and
  `::test_the_stub_fail_switch_is_off_by_default` failed, because the run refused on the missing
  price table before reaching the thing each test was about. Both were symptoms of the dependence
  above and are fixed by removing it, not by relaxing an assertion.

### Known limits

- The packaged table is only as fresh as its `price_asof`. A run quoting a later period should
  re-read the upstream pricing page and re-derive — a price date is part of a pricing answer.
- With no claim helper, one-writer-per-region is a convention this plugin states and does not
  enforce. Coordinate by hand, or point `claim_tool` at a helper.

## 0.1.0 — 2026-09-08

First version. The plugin turns the deterministic rules of a persistent-memory vault into
machinery; rules needing judgment stay prose.

### Added

- **PreToolUse locked doors** (`hooks/gate.py`): vault git law (no `git add -A`, no bare commit, no
  `--amend`, path-limited commits), model/effort pinning on programmatic launches, data integrity
  (cache files, `rm`), reserved-stem protection, and append-only discipline on shared surfaces.
  Every denial cites the entry it enforces, so a session learns the reason rather than only meeting
  a wall.
- **PostToolUse chores** (`hooks/chore.py`): artifact index, trailing-newline repair on queue files,
  SHA checks.
- **SessionStart facts** (`hooks/session_start.py`): lane and write partition, vault HEAD and dirty
  paths, boot cost of the @-import chain, unanswered ledger rows, a by-name `~/Downloads` sweep.
- **Stop-time auto-commit** (`hooks/commit.py`), scoped to the lane's declared paths and nothing
  else; an unknown lane commits nothing, ever.
- **Region claim** (`hooks/claim.py`), taken at SessionStart and released at Stop, via an external
  helper.
- **Worktree hooks** (`hooks/worktree.py`): copy-on-write clones.
- **Context-economy notices** (`hooks/context_economy.py`): non-blocking re-read and big-read
  notices; both always allow.
- **The vault side:** `recall.py`, `importer.py`, `logstore.py`, `views.py`, `ledger.py`,
  `retention.py`, `init.py`, `names.json` (role-file display names in `en`/`de`/`latin`).
- **Commands** `/gedaechtnis-status`, `/gedaechtnis-recall`, `/gedaechtnis-debrief`, and the
  `memory-reviewer` / `memory-debriefer` agents.
- **`tools/publish_check.py`** — refuses to publish a tree carrying a person with it (home paths,
  emails, names, private repo names, anything shaped like a credential) and, for a mirror clone,
  inherited tags. No allowlist; it scans itself.
- **The eval harness** (`eval/`): the memory eval (four arms, dry-run and live), the recall bench,
  the fresh-HOME probe, and the safety eval — 52 adversarial commands with 52 legitimate twins,
  plus mutant runs that turn one rule off and show the case set fail without it.

### Known limits

- Claude Code fires `Stop` when the main agent finishes responding — every turn, not only at the
  end of a session. The claim pair is therefore honest about locks (nothing is left held by a dead
  session) but its claim covers the turn that took it, not the whole session.
- The memory eval's `automemory` arm does not exercise Claude Code's own memory loader, and a live
  run takes one sample per (arm, task). No number from it ranks two arms differing by one task.
