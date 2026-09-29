# Platforms

Developed and used on macOS. Linux and Windows branches exist but have only been tested from a
Mac; this is what differs.

- **The Trash.** On macOS a deletion goes to the Trash with `/usr/bin/trash`. On Linux it goes to
  the Trash with `gio trash`, or `trash-put` from trash-cli, or, with neither installed, the
  plugin's own `tools/trash.py`, which moves the file into `~/.local/share/Trash`. A file on another
  disk cannot be moved there; it stays where it is and the reason is printed. On Windows it goes to
  the Recycle Bin through `powershell`, by way of `tools/trash.py`. The delete door names the
  command that exists on the machine it runs on. On any other system the plugin knows no Trash:
  the door still refuses `rm`, and says to move the file into a `Cleanup YYYY-MM-DD/` folder.
- **Symbolic links.** `/usr/bin/trash` on macOS and every Linux route move a link to the Trash as
  the link, never its target. Where the only route could follow a link, a link is refused instead:
  on Windows, and on a Mac without `/usr/bin/trash`, where only Finder is left.
- **Worktree clones.** macOS clones with `cp -c` (APFS). Linux uses `cp --reflink=auto`: a
  copy-on-write clone on Btrfs or XFS, a full copy elsewhere. Windows uses `git worktree`, which
  carries tracked files only.
- **Worktree sweep.** On Windows the sweep cannot tell whether a process is still working in a
  worktree (there is no `lsof`, and the liveness check used elsewhere would end the process on
  Windows), so it keeps every worktree.
- **The session's own temp folder.** The delete door lets `rm` run inside it:
  `/private/tmp/claude-<uid>/` on macOS, `/tmp/claude-<uid>/` on Linux. On Windows its location is
  not known, so removals there are refused like any other.
- **Python.** The hooks run `python3`; on Windows it has to be on `PATH` under that name.
- **What has been run.** macOS: the whole test suite. Linux and Windows: each platform branch is
  tested by making the plugin believe it runs on that system, on a Mac. Neither has run on a real
  Linux or Windows machine yet.
