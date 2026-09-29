# What this plugin runs, sends and fetches

**It sends nothing and fetches nothing.** No file the plugin ships imports a network library, and
no command it runs contacts a remote. It has no account, no telemetry and no update check. Your
notes stay in your vault on your disk; the plugin's own state (logs, per-session records) stays in
`~/.claude/gedaechtnis/`.

The programs its hooks and tools start, all locally (checked by `tests/test_publish_surface.py`
against the source):

- `git` — reads the vault's status and history, and commits the vault files a session wrote. The
  only `git fetch` copies commits from a local worktree clone into its source repository. Nothing
  pushes.
- `ps`, `lsof` — to tell whether a session or a worktree is still in use.
- `screen` or `tmux`, and `sysctl` (macOS) — only in `tools/sessions.py` and the close check: to
  start a session detached, read what is on a session's screen (it never types into one), end a
  finished session when you have switched that on, and read how much swap is in use.
- `grep` — to find notes that still cite a heading an edit removed.
- `cp` — to make a copy-on-write clone for an isolated build.
- `osascript` — to move a file to the Trash through Finder, only on a Mac without
  `/usr/bin/trash`. The path is passed as an argument, never as script text.
- `/usr/bin/trash` (macOS), `gio trash` or `trash-put` (Linux), `powershell` (Windows) — to move a
  file to the Trash or the Recycle Bin, whichever this machine has (see [Platforms](#platforms)).
  The path is passed as an argument, or on Windows as an environment value, never as script text.
- `python3` — the plugin's own scripts, and two commands only if you configure them:
  `notify_command` and `answer_router`.
- `bash` — the region-claim helper, only if `claim_tool` names one. The plugin ships none.
