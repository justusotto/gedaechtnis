"""PLATFORM-1 — one seam (`common.platform()`) for every call that exists on one system only.

The standard is "macOS loses nothing": every macOS string a door prints is diffed against a
fixture captured from main BEFORE the seam existed (`fixtures/platform-macos-main-57557a11.json`).
Linux and Windows are reached through `GEDAECHTNIS_PLATFORM` and a fake `common._have` — no test
here asks the real machine which system it is, or which programs it has. Windows branches are
TESTED BY SEAM only: nothing here has run on Windows.
"""
from __future__ import annotations
import json, os, subprocess, sys, tempfile, types
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "hooks"))
sys.path.insert(0, str(PLUGIN))
import common      # noqa: E402
import deletedoor  # noqa: E402
import gate        # noqa: E402
import procs       # noqa: E402
import worktree    # noqa: E402

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "platform-macos-main-57557a11.json").read_text())
FINDER_ARGV = ["osascript", "-e", "on run argv",
               "-e", 'tell application "Finder" to delete POSIX file (item 1 of argv)',
               "-e", "end run", "--"]                      # main 57557a11, worktree.py + retention.py


@pytest.fixture
def machine(monkeypatch, tmp_path):
    """Pretend to be a system with a given set of programs. Returns a setter."""
    st = tmp_path / "state"; st.mkdir()
    vault = tmp_path / "MyVault"; vault.mkdir()
    monkeypatch.setenv("GEDAECHTNIS_STATE_DIR", str(st))
    monkeypatch.setenv("GEDAECHTNIS_VAULT", str(vault))

    def set_(plat: str, programs=()):
        monkeypatch.setenv("GEDAECHTNIS_PLATFORM", plat)
        have = set(programs)
        monkeypatch.setattr(common, "_have", lambda prog: prog in have)
        return types.SimpleNamespace(state=st, vault=vault)
    return set_


def _mac_strings(m) -> dict:
    """The same strings `capture_mac.py` took from main, recomputed on this tree."""
    out = {}
    (m.state / "delete.mode").write_text("deny")
    out["facts_deny"] = deletedoor.facts_line()
    (m.state / "delete.mode").write_text("warn")
    out["facts_warn_pinned"] = deletedoor.facts_line()
    for t in ["/srv/notes/a.md", "/tmp/it's here", "rel/dir with space"]:
        out["msg:" + t] = deletedoor._msg(t, "why text")
    out["gate_trash_reason"] = gate.rule_data_integrity("rm -rf " + str(m.vault) + "/Notes")
    return {k: (v.replace(str(m.vault), "<VAULT>") if isinstance(v, str) else v) for k, v in out.items()}


# ------------------------------------------------------------------ which system is this ----

def test_platform_reads_the_override_first(monkeypatch):
    for p in ("macos", "linux", "windows"):
        monkeypatch.setenv("GEDAECHTNIS_PLATFORM", p)
        assert common.platform() == p


def test_platform_maps_sys_platform_without_an_override(monkeypatch):
    monkeypatch.delenv("GEDAECHTNIS_PLATFORM", raising=False)
    for raw, want in (("darwin", "macos"), ("linux", "linux"), ("win32", "windows"), ("freebsd14", "other")):
        monkeypatch.setattr(common.sys, "platform", raw)
        assert common.platform() == want, raw


# ------------------------------------------------- NEGATIVE CONTROL: macOS is byte-identical ----

def test_macos_door_strings_are_byte_identical_to_main(machine):
    m = machine("macos", {common.MAC_TRASH})
    assert _mac_strings(m) == FIXTURE


def test_macos_clone_copy_is_clonefile_then_reflink_as_on_main(machine):
    machine("macos")
    assert common.clone_copy_argv(Path("/a"), Path("/b")) == [
        ["cp", "-c", "-R", "/a", "/b"], ["cp", "-R", "--reflink=auto", "/a", "/b"]]


def test_macos_session_tmp_base_is_private_tmp_as_on_main(machine):
    machine("macos")
    assert common.session_tmp_base() == Path(f"/private/tmp/claude-{os.getuid()}")


def test_macos_pid_probe_still_asks_the_kernel(machine, monkeypatch):
    machine("macos")
    seen = []
    monkeypatch.setattr(procs.os, "kill", lambda pid, sig: seen.append((pid, sig)))
    assert procs.pid_alive(4242) is True and seen == [(4242, 0)]


def _unique_clone(tmp_path, monkeypatch):
    root = tmp_path / "wts"; root.mkdir()
    monkeypatch.setenv("GEDAECHTNIS_WORKTREES", str(root))
    monkeypatch.delenv("GEDAECHTNIS_NO_TRASH", raising=False)
    wt = root / "repo--x-1"; wt.mkdir()
    (wt / ".gedaechtnis-clone-of").write_text(str(tmp_path / "src") + "\n")   # not git: unique = True
    return wt


def test_macos_worktree_remove_goes_through_the_seam_not_finder(machine, tmp_path, monkeypatch):
    """PUBLICPOLISH-1 changed this on purpose: macOS used Finder (which follows a link) until then."""
    machine("macos", {common.MAC_TRASH})
    wt = _unique_clone(tmp_path, monkeypatch)
    got = []
    monkeypatch.setattr(common, "move_to_trash", lambda p, **k: got.append(Path(p)) or (True, "moved"))
    monkeypatch.setattr(worktree, "sh", lambda args, **k: pytest.fail(f"no Finder any more: {args}"))
    assert worktree.remove({"worktree_path": str(wt)}) == 0
    assert got == [wt]


@pytest.mark.parametrize("plat", ["macos", "linux", "windows"])
def test_a_failed_trash_move_keeps_the_clone_and_never_falls_to_rmtree(machine, tmp_path, monkeypatch, plat):
    machine(plat, {common.MAC_TRASH})
    wt = _unique_clone(tmp_path, monkeypatch)
    (wt / "work.txt").write_text("unique")
    monkeypatch.setattr(common, "move_to_trash", lambda p, **k: (False, "left in place: no Trash"))
    monkeypatch.setattr(worktree.shutil, "rmtree", lambda *a, **k: pytest.fail("a clone with unique work was deleted"))
    assert worktree.remove({"worktree_path": str(wt)}) == 0
    assert (wt / "work.txt").read_text() == "unique"


def test_macos_retention_goes_through_the_seam_not_finder(machine, tmp_path, monkeypatch):
    import retention
    machine("macos", {common.MAC_TRASH})
    monkeypatch.delenv("GEDAECHTNIS_NO_TRASH", raising=False)
    rv = tmp_path / "repo" / "review"; rv.mkdir(parents=True)
    got = []
    monkeypatch.setattr(common, "move_to_trash", lambda p, **k: got.append(Path(p)) or (True, "moved"))
    monkeypatch.setattr(retention.subprocess, "run", lambda *a, **k: pytest.fail("no osascript any more"))
    b = retention.apply(rv, {"arcs": []}, trash=True)
    assert got == [b]


def test_macos_worktree_trash_uses_usr_bin_trash_end_to_end(machine, tmp_path, monkeypatch):
    machine("macos", {common.MAC_TRASH})
    wt = _unique_clone(tmp_path, monkeypatch)
    seen = _record_run(monkeypatch)
    assert worktree.remove({"worktree_path": str(wt)}) == 0
    assert seen == [([common.MAC_TRASH, str(wt)], None)]


# ------------------------------------------------ POSITIVE CONTROLS: each platform, each site ----

@pytest.mark.parametrize("plat,programs,cmd", [
    ("linux", {"gio"}, "gio trash <path>"),
    ("linux", {"trash-put"}, "trash-put <path>"),
    ("linux", set(), "python3 {tool} <path>"),
    ("windows", set(), "python3 {tool} <path>"),
    ("macos", set(), "python3 {tool} <path>"),                # a Mac without /usr/bin/trash
])
def test_facts_line_names_the_command_this_machine_has(machine, plat, programs, cmd):
    m = machine(plat, programs)
    (m.state / "delete.mode").write_text("deny")
    line = deletedoor.facts_line()
    assert f"`{cmd.format(tool=common.trash_tool())}`" in line, line
    assert "/usr/bin/trash" not in line
    assert ("Recycle Bin" in line) == (plat == "windows")


def test_facts_line_on_an_unknown_system_names_the_gap(machine):
    m = machine("other")
    (m.state / "delete.mode").write_text("deny")
    line = deletedoor.facts_line()
    assert "Cleanup YYYY-MM-DD/" in line and "no Trash the plugin knows" in line and "never removed" in line


def test_refusal_on_linux_names_gio_and_the_linux_restore(machine):
    machine("linux", {"gio"})
    msg = deletedoor._msg("/srv/a b", "why")
    assert "`gio trash '/srv/a b'`" in msg and "Restore brings it back" in msg and "Put Back" not in msg


def test_refusal_on_windows_names_the_recycle_bin(machine):
    machine("windows")
    msg = deletedoor._msg("C:/x/y.txt", "why")
    assert "Move it to the Recycle Bin instead" in msg and "moved to the Recycle Bin" in msg
    assert f"python3 {common.trash_tool()} C:/x/y.txt" in msg


def test_refusal_on_an_unknown_system_refuses_in_words_and_never_says_rm_it(machine):
    machine("other")
    msg = deletedoor._msg("/x", "why")
    assert "There is no Trash here" in msg and "moved to Cleanup" in msg and "/usr/bin/trash" not in msg


def test_gate_reason_on_linux_names_gio_without_the_finder_clause(machine):
    m = machine("linux", {"gio"})
    r = gate.rule_data_integrity("rm -rf " + str(m.vault) + "/Notes")
    assert "Deletions go to the Trash with `gio trash <path>`, in one `Cleanup" in r and "Finder" not in r


def test_gate_reason_on_windows_names_the_recycle_bin(machine):
    m = machine("windows")
    r = gate.rule_data_integrity("rm -rf " + str(m.vault) + "/Notes")
    assert "Deletions go to the Recycle Bin with `python3" in r


def test_gate_reason_on_an_unknown_system_names_the_gap(machine):
    m = machine("other")
    r = gate.rule_data_integrity("rm -rf " + str(m.vault) + "/Notes")
    assert "Deletions are moved into one `Cleanup YYYY-MM-DD/` bundle" in r and "no Trash the plugin knows" in r


def test_linux_clone_skips_gnu_cp_c(machine):
    machine("linux")
    assert common.clone_copy_argv(Path("/a"), Path("/b")) == [["cp", "-R", "--reflink=auto", "/a", "/b"]]


def test_windows_clone_goes_straight_to_git_worktree(machine, tmp_path, monkeypatch):
    machine("windows")
    root = tmp_path / "wts"; root.mkdir()
    monkeypatch.setenv("GEDAECHTNIS_WORKTREES", str(root))
    src = tmp_path / "src"; src.mkdir()
    calls = []

    def fake(args, **k):
        calls.append(args)
        if args[:2] == ["git", "-C"] and "rev-parse" in args:
            return subprocess.CompletedProcess(args, 0, str(src) + "\n", "")
        if "worktree" in args and "add" in args:
            Path(args[-2]).mkdir(parents=True)
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(worktree, "sh", fake)
    assert worktree.create({"name": "b", "cwd": str(src)}) == 0
    assert not any(a[0] == "cp" for a in calls), calls
    assert any("worktree" in a and "add" in a for a in calls)


def test_linux_session_tmp_base_is_tmp(machine):
    machine("linux")
    assert common.session_tmp_base() == Path(f"/tmp/claude-{os.getuid()}")


def test_delete_door_finds_the_session_tree_under_the_seams_base(machine, tmp_path, monkeypatch):
    """Written because a mutation said it was missing: the door ignoring the seam stayed green."""
    machine("linux")
    monkeypatch.delenv("GEDAECHTNIS_SESSION_TMP_ROOT", raising=False)
    base = tmp_path / "claude-1000"; (base / "-srv-proj" / "sid42").mkdir(parents=True)
    monkeypatch.setattr(common, "session_tmp_base", lambda: base)
    assert deletedoor.session_tmp("sid42") == (base / "-srv-proj" / "sid42").resolve()


def test_windows_has_no_session_tmp_so_no_carve_out(machine, monkeypatch):
    machine("windows")
    monkeypatch.delenv("GEDAECHTNIS_SESSION_TMP_ROOT", raising=False)
    assert common.session_tmp_base() is None and deletedoor.session_tmp("abc") is None


def test_windows_pid_probe_never_calls_os_kill(machine, monkeypatch):
    machine("windows")
    monkeypatch.setattr(procs.os, "kill", lambda *a: pytest.fail("os.kill TERMINATES a process on Windows"))
    assert procs.pid_alive(4242) is True


@pytest.mark.parametrize("plat", ["linux", "windows"])
def test_worktree_remove_off_macos_uses_the_seams_trash(machine, tmp_path, monkeypatch, plat):
    machine(plat)
    wt = _unique_clone(tmp_path, monkeypatch)
    got = []
    monkeypatch.setattr(common, "move_to_trash", lambda p, **k: got.append(Path(p)) or (True, "moved"))
    monkeypatch.setattr(worktree, "sh", lambda args, **k: pytest.fail(f"no program off macOS here: {args}"))
    assert worktree.remove({"worktree_path": str(wt)}) == 0
    assert got == [wt]


def test_retention_off_macos_uses_the_seams_trash(machine, tmp_path, monkeypatch):
    import retention
    machine("linux")
    monkeypatch.delenv("GEDAECHTNIS_NO_TRASH", raising=False)
    rv = tmp_path / "repo" / "review"; rv.mkdir(parents=True)
    got = []
    monkeypatch.setattr(common, "move_to_trash", lambda p, **k: got.append(Path(p)) or (True, "moved"))
    monkeypatch.setattr(retention.subprocess, "run", lambda *a, **k: pytest.fail("no osascript off macOS"))
    b = retention.apply(rv, {"arcs": []}, trash=True)
    assert got == [b]


# ------------------------------------------------------------- move_to_trash, per route ----

def _record_run(monkeypatch, rc=0):
    seen = []

    def run(argv, **k):
        seen.append((argv, k.get("env")))
        return subprocess.CompletedProcess(argv, rc, "", "boom" if rc else "")
    monkeypatch.setattr(subprocess, "run", run)
    return seen


def test_move_macos_uses_usr_bin_trash_with_the_path_as_argv(machine, tmp_path, monkeypatch):
    machine("macos", {common.MAC_TRASH})
    f = tmp_path / "a"; f.write_text("x")
    seen = _record_run(monkeypatch)
    assert common.move_to_trash(f)[0] is True
    assert seen == [([common.MAC_TRASH, str(f)], None)]


def test_move_macos_without_usr_bin_trash_uses_finder_and_refuses_a_link(machine, tmp_path, monkeypatch):
    machine("macos")
    f = tmp_path / "a"; f.write_text("x")
    link = tmp_path / "l"; link.symlink_to(f)
    seen = _record_run(monkeypatch)
    ok, words = common.move_to_trash(link)
    assert ok is False and "symbolic link" in words and seen == []
    assert common.move_to_trash(f)[0] is True and seen == [(FINDER_ARGV + [str(f)], None)]


def test_move_linux_gio(machine, tmp_path, monkeypatch):
    machine("linux", {"gio"})
    f = tmp_path / "a"; f.write_text("x")
    seen = _record_run(monkeypatch)
    assert common.move_to_trash(f)[0] is True and seen == [(["gio", "trash", "--", str(f)], None)]


def test_move_linux_trash_put(machine, tmp_path, monkeypatch):
    machine("linux", {"trash-put"})
    f = tmp_path / "a"; f.write_text("x")
    seen = _record_run(monkeypatch)
    assert common.move_to_trash(f)[0] is True and seen == [(["trash-put", "--", str(f)], None)]


def test_move_linux_freedesktop_moves_the_file_and_writes_its_restore_record(machine, tmp_path, monkeypatch):
    machine("linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    f = tmp_path / "my notes.md"; f.write_text("x")
    ok, words = common.move_to_trash(f)
    trash = tmp_path / "xdg" / "Trash"
    assert ok and not f.exists() and (trash / "files" / "my notes.md").read_text() == "x"
    info = (trash / "info" / "my notes.md.trashinfo").read_text()
    assert info.startswith("[Trash Info]\nPath=") and "my%20notes.md" in info and "DeletionDate=" in info
    f.write_text("y")                                       # a second file of the same name
    assert common.move_to_trash(f)[0] and (trash / "files" / "my notes.md.2").read_text() == "y"


def test_move_linux_freedesktop_never_takes_a_name_another_move_has_reserved(machine, tmp_path, monkeypatch):
    """A concurrent move reserves its name by creating the `.trashinfo` first; this one must pick
    the next name, never rename over the file the other move is about to put there."""
    machine("linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    info = tmp_path / "xdg" / "Trash" / "info"; info.mkdir(parents=True)
    (info / "a.txt.trashinfo").write_text("[Trash Info]\nPath=/elsewhere/a.txt\n")
    f = tmp_path / "a.txt"; f.write_text("mine")
    ok, words = common.move_to_trash(f)
    assert ok and (tmp_path / "xdg" / "Trash" / "files" / "a.txt.2").read_text() == "mine"
    assert (info / "a.txt.trashinfo").read_text().endswith("/elsewhere/a.txt\n")


def test_move_linux_a_link_goes_by_rename_even_with_gio_and_the_target_stays(machine, tmp_path, monkeypatch):
    machine("linux", {"gio"})
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    target = tmp_path / "data"; target.mkdir(); (target / "keep").write_text("k")
    link = tmp_path / "l"; link.symlink_to(target)
    seen = _record_run(monkeypatch)
    ok, _ = common.move_to_trash(link)
    assert ok and seen == [] and (target / "keep").read_text() == "k"
    assert (tmp_path / "xdg" / "Trash" / "files" / "l").is_symlink()


def test_move_linux_another_disk_is_refused_and_left_in_place(machine, tmp_path, monkeypatch):
    machine("linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    f = tmp_path / "a"; f.write_text("x")

    def exdev(a, b):
        raise OSError(18, "Invalid cross-device link")
    monkeypatch.setattr(common.os, "rename", exdev)
    ok, words = common.move_to_trash(f)
    assert ok is False and f.exists() and "left in place" in words
    assert list((tmp_path / "xdg" / "Trash" / "info").iterdir()) == []   # no record for a file not moved


def test_move_windows_passes_the_path_in_the_environment_never_the_script(machine, tmp_path, monkeypatch):
    machine("windows")
    f = tmp_path / 'a"; Remove-Item -Recurse C:\\'; f.write_text("x")
    seen = _record_run(monkeypatch)
    assert common.move_to_trash(f)[0] is True
    (argv, env), = seen
    assert argv[0] == "powershell" and str(f) not in " ".join(argv) and "SendToRecycleBin" in argv[-1]
    assert env["GEDAECHTNIS_TRASH_TARGET"] == str(f)


def test_move_windows_refuses_a_link(machine, tmp_path, monkeypatch):
    machine("windows")
    f = tmp_path / "a"; f.write_text("x")
    link = tmp_path / "l"; link.symlink_to(f)
    seen = _record_run(monkeypatch)
    ok, words = common.move_to_trash(link)
    assert ok is False and "symbolic link" in words and seen == [] and link.is_symlink()


def test_move_on_an_unknown_system_leaves_it_in_place(machine, tmp_path, monkeypatch):
    machine("other")
    f = tmp_path / "a"; f.write_text("x")
    seen = _record_run(monkeypatch)
    ok, words = common.move_to_trash(f)
    assert ok is False and f.exists() and seen == [] and "no Trash the plugin knows" in words


def test_move_a_failing_trash_program_is_reported_not_retried_as_a_delete(machine, tmp_path, monkeypatch):
    machine("linux", {"gio"})
    f = tmp_path / "a"; f.write_text("x")
    _record_run(monkeypatch, rc=1)
    ok, words = common.move_to_trash(f)
    assert ok is False and f.exists() and "exited 1" in words


# ------------------------------------------------------------------------- file locks ----

def test_locks_on_macos_are_fcntl_and_exclude_a_second_holder(machine, tmp_path):
    import fcntl
    machine("macos")
    p = tmp_path / "l.lock"
    with open(p, "w") as a, open(p, "w") as b:
        common.lock_file(a)
        with pytest.raises(OSError):
            fcntl.flock(b, fcntl.LOCK_EX | fcntl.LOCK_NB)
        common.unlock_file(a)
        fcntl.flock(b, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_locks_on_windows_use_msvcrt_and_wait_for_the_holder(machine, tmp_path, monkeypatch):
    machine("windows")
    calls, busy = [], [2]

    def locking(fd, mode, n):
        calls.append((mode, n))
        if mode == "NB" and busy[0]:
            busy[0] -= 1
            raise OSError("locked")
    fake = types.SimpleNamespace(LK_NBLCK="NB", LK_UNLCK="UN", locking=locking)
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(common.time, "sleep", lambda s: None)
    with open(tmp_path / "l.lock", "w") as fh:
        common.lock_file(fh)
        common.unlock_file(fh)
    assert calls == [("NB", 1), ("NB", 1), ("NB", 1), ("UN", 1)]


def test_no_hook_module_imports_fcntl_at_the_top_any_more():
    """A top-level `import fcntl` fails the whole hook on Windows before it can decide anything."""
    import ast
    bad = []
    for p in sorted(PLUGIN.rglob("*.py")):
        rel = p.relative_to(PLUGIN)
        if rel.parts[0] in ("tests", "eval") or "__pycache__" in rel.parts:
            continue
        for n in ast.parse(p.read_text(encoding="utf-8")).body:
            if isinstance(n, ast.Import) and any(a.name == "fcntl" for a in n.names):
                bad.append(str(rel))
    assert bad == []


# ------------------------------------------------------------------ the command it names ----

def test_the_trash_tool_runs_and_never_deletes_what_it_cannot_move(tmp_path):
    env = {**os.environ, "GEDAECHTNIS_PLATFORM": "linux", "PATH": str(tmp_path / "empty"),
           "XDG_DATA_HOME": str(tmp_path / "xdg")}
    f = tmp_path / "a.txt"; f.write_text("x")
    tool = [sys.executable, str(common.trash_tool())]
    p = subprocess.run(tool + [str(f)], capture_output=True, text=True, env=env)
    assert p.returncode == 0 and not f.exists() and (tmp_path / "xdg" / "Trash" / "files" / "a.txt").exists()
    p = subprocess.run(tool + [str(tmp_path / "missing")], capture_output=True, text=True, env=env)
    assert p.returncode == 1 and "does not exist" in p.stderr


def test_a_link_where_a_clone_should_be_is_left_alone_and_not_reported_removed(machine, tmp_path, monkeypatch, capsys):
    """Bare-review should-fix (PUBLICPOLISH-1): rmtree on a link raised, `ignore_errors` hid it, and
    the hook said "removed clone". The clone here holds NOTHING unique, so the delete path is reached."""
    machine("macos", {common.MAC_TRASH})
    root = tmp_path / "wts"; root.mkdir()
    monkeypatch.setenv("GEDAECHTNIS_WORKTREES", str(root))
    src = tmp_path / "src"; (src / ".git").mkdir(parents=True)
    real = root / "real--x-1"; (real / ".git").mkdir(parents=True)
    (real / ".gedaechtnis-clone-of").write_text(str(src) + "\n")
    link = root / "repo--x-2"; link.symlink_to(real)
    monkeypatch.setattr(worktree, "sh", lambda args, **k: subprocess.CompletedProcess(args, 0, "", ""))
    monkeypatch.setattr(worktree, "dirt_manifest", lambda wt: {})
    monkeypatch.setattr(common, "move_to_trash", lambda *a, **k: pytest.fail("nothing unique: no Trash move expected"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail(f"no real program in this test: {a}"))
    assert worktree.remove({"worktree_path": str(link)}) == 0
    assert link.is_symlink() and real.is_dir() and "removed clone" not in capsys.readouterr().err
