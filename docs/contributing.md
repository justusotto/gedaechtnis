# Publishing, testing and design rules

Before sharing or publishing this tree:

```sh
python3 tools/publish_check.py
```

It refuses, listing every hit, if any file carries a home-directory path, an email address, a
private repository name or anything shaped like an API key. There is no allowlist and it scans
itself. Every excerpt is redacted. A walk that finds zero files prints `UNCHECKED` and exits
non-zero, because "we found nothing" and "we never looked" must not read the same. `--root DIR` runs
the identical sweep over any repository (2026-09-18). There it also looks for phone-number and IBAN
shapes, any `.env`-style file (flagged by existing, not only by its contents) and, in a git working
tree, any file `.gitignore` covers that is tracked anyway.

**Never `git push --tags` and never `git push --follow-tags`.** A clone made from a private
repository inherits that repository's tags, and each points at a private commit. Pushing them
publishes those commits and their whole history, and deleting them afterwards does not remove the
objects until the host collects them. Two guards hold the rule: `publish_check.py` refuses a clone carrying any
tag that is not a `v*` release, and a `PreToolUse` gate refuses `--tags` from such a clone when the
session runs in the vault or a marked project (elsewhere it tells you). A single
tag pushed by name is never blocked. A release is built as a single commit with no history:

```sh
bash tools/release_orphan.sh --sha <the commit you are publishing>
```

It extracts the tree at that commit out of git, builds it in a temporary repository as a single commit
with no ancestry, runs `publish_check.py` over it as a git root, refuses on any finding, pushes
nothing, and prints the two commands for a person to run.

**Versions.** A release's tag is always `v` plus the `version` in `.claude-plugin/plugin.json`
(0.5.0 → `v0.5.0`). A patch is fixes and polish, a minor version a capability a user notices, and a
major version a rework or a complete new feature set. The script cuts that tag itself and refuses,
before building anything, a `--tag` that differs from the version, a `--tag` on a tree with no
version, and a version that `CHANGELOG.md` has no `## <version> ` heading for. To release a
different number, change `plugin.json` and the CHANGELOG heading in a commit and cut from that
commit.

**Tests:** `python3 -m pytest tests -q`. State is redirected into a temporary directory through the
environment variables above, so the suite never touches a real vault. The two-writer proof ships with
its control: the same run with the hooks removed, which must lose entries. A control that loses nothing
means the two processes never overlapped, and the test fails on that rather than passing.
`SIM_N=500 python3 -m pytest tests/test_doors.py -q` runs it at the full 500 appends per writer.

While the suite runs it holds a lock, `~/.claude/gedaechtnis/suite.lock/<pid>`, taken by its session
fixture from the first test to the last check and dropped afterwards. The row-done door, which
writes queue files in the vault, waits on that lock, checking every half second for up to 20 seconds
(`GEDAECHTNIS_SUITE_WAIT_S`), and writes nothing if it is still held. Other
pytest runs on the machine do not hold it and do not delay a write. An entry left by a suite that was
killed is removed the next time a writer looks. `python3 tools/suitelock.py status` shows who holds it.
Run the suite the usual way, `python3 -m pytest tests -q`, and it takes the lock itself.

**Design rules**, for anyone changing this. Every refusal cites its reason. Defaults never delete:
cleanup moves files into one documented bundle and at most to the Trash, and nothing calls `rm` on
a file you wrote. The plugin does delete its own bookkeeping: temporary files, the claim files of a parked-message delivery, and the lock and
marker files it keeps in its state directory. One exception: when a worktree clone the plugin made
itself is removed and holds nothing unique (every commit fetched back into the source repository,
no uncommitted or stashed work), the clone directory is deleted rather than sent to the Trash. A
clone that does hold something goes to the Trash. A file no rule names is kept until someone
writes the rule that names it; age never decides. A rule that needs judgment stays prose, because a guard that guesses produces false refusals. Hooks catch their own errors:
every hook body logs its traceback and exits 0. No file names a directory of its own; all read `hooks/config.py`.
