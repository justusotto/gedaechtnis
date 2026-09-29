# Gedächtnis

A memory for Claude Code, kept as plain Markdown files in a git repository on your own disk. Each
project gets a small set of notes (status, decisions, mistakes, open questions). Every session in
that project loads them at start, and at the end the session's own changes to them are committed
with git. The rules that protect those notes and a script can check, such as "never `git add -A`
in the notes repository", are hooks: the tool call is refused with the reason, so a session does
not have to remember the rule. Rules that need judgment stay as prose.

It sends nothing and fetches nothing: no account, no telemetry, no network calls. Python 3.9 or
later; nothing to build.

## Install

```sh
git clone <this repository's URL> ~/src/gedaechtnis
cd ~/src/my-project                     # any git repository you work in
python3 ~/src/gedaechtnis/init.py --repo .
```

`init.py` creates the notes repository (`~/Gedaechtnis`), a folder in it for this project, one
`@`-import line in the project's `CLAUDE.md`, a `.atlas-lane` marker naming what this project's
sessions may write, and the symlink that loads the plugin. It overwrites nothing; a second run
reports `kept`. Run it with no `--repo` to be offered every project Claude Code has worked in.
Installed from a plugin directory instead, the plugin writes nothing until a project is given a
memory. Details: [docs/install.md](docs/install.md).

## Ten minutes: see it work

Every command below was run in a throwaway home directory on 2026-09-29, macOS. The session
steps (3 and 4) need a logged-in Claude Code; steps 5 and 6 show the same checks without one.

1. Install, as above, with the starter headings for a software project:

   ```sh
   git clone <this repository's URL> ~/src/gedaechtnis
   mkdir -p ~/src/my-project && git -C ~/src/my-project init -q
   cd ~/src/my-project && python3 ~/src/gedaechtnis/init.py --repo . --shape project --yes
   ```

   It prints one `created` line per file, then
   `committed 8 created file(s) as the vault's first commit` and the next step.

2. Look at what it made: `ls ~/Gedaechtnis/my-project` shows `Aporia.md Canon.md Errata.md Map.md
   Patterns.md Position.md`, and `cat ~/src/my-project/.atlas-lane` shows the paths this
   project's sessions may write.

3. Start a session in the project (`claude`). Its context begins with a facts block, for example:

   ```
   Gedächtnis session facts (hook-generated):
   - Lane MY-PROJECT, declared by ~/src/my-project/.atlas-lane; vault write partition: my-project, Global.
   - Memory files in my-project: Index (Map.md) · Status (Position.md) · Decisions (Canon.md) · …
   - Partition hook mode: WARN (logs would-be refusals to ~/.claude/gedaechtnis/partition.log, blocks nothing).
   - Vault ~/Gedaechtnis: HEAD at session start 3d937f4b; dirty paths in the vault right now: 0.
   ```

   `/gedaechtnis-status` shows the same facts on request.

4. Ask the session to run `git -C ~/Gedaechtnis add -A`. The call is refused before it runs:

   ```
   Vault law: never `git add -A`/`-a`/`-u`/`.` in ~/Gedaechtnis — a broad add sweeps another
   lane's in-flight work into your commit. Add the exact paths: `git -C ~/Gedaechtnis add -- <file>`.
   ```

5. The same door, without a session. A hook is a script that reads the tool call as JSON:

   ```sh
   P=~/src/gedaechtnis
   echo '{"session_id":"demo","tool_name":"Bash","tool_input":{"command":"git -C ~/Gedaechtnis add -A"},"cwd":"'$PWD'"}' \
     | CLAUDE_PLUGIN_ROOT=$P python3 -B $P/hooks/gate.py bash
   ```

   It prints `"permissionDecision": "deny"` and the reason above. Change the command to
   `git -C ~/Gedaechtnis status --short` and it prints nothing: the call is allowed.

6. A second door: a whole-file overwrite of an existing note is refused, and Edit is offered.

   ```sh
   echo '{"session_id":"demo","tool_name":"Write","tool_input":{"file_path":"'$HOME'/Gedaechtnis/my-project/Canon.md","content":"x"},"cwd":"'$PWD'"}' \
     | CLAUDE_PLUGIN_ROOT=$P python3 -B $P/hooks/gate.py write
   ```

   Each refusal is also a line in `~/.claude/gedaechtnis/deny.log`.

## What is in the box

- **The memory.** One folder per project in a git repository you own, with plain-English names
  over short file names (`Canon.md` is shown as *Decisions*). Loaded at session start through
  `CLAUDE.md`; a session's own writes are committed at Stop, path by path.
  [docs/install.md](docs/install.md)
- **The doors.** `PreToolUse` hooks that refuse, or ask about, calls that would damage the notes or
  their history, with the reason. Most act only inside the notes repository or a project carrying
  the marker; newer ones warn before they refuse. [docs/doors.md](docs/doors.md),
  [docs/hooks.md](docs/hooks.md)
- **Session tools.** `tools/sessions.py` starts sessions detached and lists every session on the
  machine with its state; `tools/status.py`, `tools/suitelock.py`, `tools/compactpoint.py` and
  `tools/facets.py` answer narrower questions (`--help` on each).
- **Resuming and parking.** A message to an idle session that would be expensive to wake is parked,
  and shown to that session when it next starts or gets a prompt. `/compact-ready` writes a short
  point that is put back word for word after a compaction.
- **Benches.** `eval/` holds the safety eval, a two-writer test, a recall bench, a memory eval
  against a plain file, and a 90-day simulated install. Results, dated:
  [docs/measurements.md](docs/measurements.md)

Commands: `/gedaechtnis-status`, `/gedaechtnis-recall`, `/gedaechtnis-debrief`,
`/gedaechtnis-cleanup`, `/gedaechtnis-synthesis`, `/gedaechtnis-split`, `/gedaechtnis-graduate`,
`/gedaechtnis-facets`, `/compact-ready`.
Agents, all read-only: memory-debriefer, memory-reviewer, memory-cleaner, memory-synthesizer,
memory-distiller. Descriptions are in each file under `commands/` and `agents/`.

## Limits

- Measured on one machine, on macOS. Linux and Windows code paths are tested by simulation only.
- It has not been shown to do better than a small memory file pasted into the prompt on the tasks
  measured so far (36 of 36 against 35 of 36, inside the noise).
- The write-partition door ships in warn mode. Several features that move files only propose until
  you switch them on.

The full list is in [docs/measurements.md](docs/measurements.md).

## Further reading

- [Why hooks, not prompts](docs/why-hooks-not-prompts.md): the reasoning, what it cost, the limits
- [Install and what it creates](docs/install.md)
- [The doors](docs/doors.md) and [what runs when](docs/hooks.md)
- [Configuration](docs/configuration.md)
- [What it runs, sends and fetches](docs/what-it-runs.md)
- [Platforms](docs/platforms.md)
- [Measurements and limits](docs/measurements.md)
- [Publishing, testing and design rules](docs/contributing.md)
- [CHANGELOG.md](CHANGELOG.md) and [PRIVACY.md](PRIVACY.md)

## Licence

MIT — see [LICENSE](LICENSE).
