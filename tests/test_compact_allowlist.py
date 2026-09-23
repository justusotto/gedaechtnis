"""The compactor moves entries only out of files named for a memory role.

On 2026-09-22 a vault that had not declared its queue folder had its work queue compacted: every
open row of a queue file moved into an archive segment that no queue reader looks at. The compactor
had worked from a deny-list (folders declared as queues or notice outboxes) and the folder had never
been declared. These tests build exactly that vault — no queue folder in the config — and assert the
operational files stay byte-identical while a role file of the same shape and size, in the same run,
is compacted. The second half is the positive control: without it, a compactor that did nothing
would pass.

Driven in a subprocess because `common.VAULT` is resolved once per process (see test_bootfile.py).
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parent.parent
HOOKS = PLUGIN / "hooks"
LIMIT = 1500

PROBE = """
import json, sys
sys.path.insert(0, {hooks!r})
import maintenance
skipped = []
moved = maintenance.compact_vault(cwd=sys.argv[1], skipped=skipped)
print(json.dumps({{"moved": [m["path"] for m in moved], "skipped": skipped}}))
"""

MAIN_PROBE = """
import json, sys, io
sys.path.insert(0, {hooks!r})
import maintenance
sys.stdin = io.StringIO(json.dumps({{"cwd": sys.argv[1], "session_id": "t"}}))
maintenance.main()
"""


def sections(title: str, n: int = 12, size: int = 300, row: str = "") -> str:
    return f"# {title}\n" + "".join(f"\n## part {i}\n{row}" + "x" * size + "\n" for i in range(n))


# The files the incident touched, and their near relatives. Every one is over the bound and has
# `## ` sections, so every one would be compacted by a compactor that looked only at size.
OPERATIONAL = {
    "Pharos/queues/regions/lane.md": sections("Queue", row="- [ ] `q:XX-2026-01-01-ROW-1` open\n"),
    "Channels/LANE/N-2026-01-01-notice.md": sections("Notice"),
    "Pharos/README.md": sections("Readme"),
    "Proj/Inbox.md": sections("Inbox"),
    "Proj/Patterns-verification.md": sections("Not a role name"),
    "Proj/notes.md": sections("Loose notes"),
}

# The notice outbox is left out of the memory population by the package's own default folder
# name, before the allow-list is reached; it is still asserted untouched above. Every other file
# reaches the allow-list and is declined there.
DECLINED = set(OPERATIONAL) - {"Channels/LANE/N-2026-01-01-notice.md"}


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "vault"
    (v / "Proj").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(v)], check=True)
    (v / "Proj" / "Map.md").write_text("# Proj\n")
    for rel, text in OPERATIONAL.items():
        f = v / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    (v / "Proj" / "Position.md").write_text(sections("Position"))
    return v


def run(tmp_path, vault, probe=PROBE):
    limits_file = tmp_path / "limits.json"
    shipped = json.loads((PLUGIN / "rules" / "limits.json").read_text())
    limits_file.write_text(json.dumps({**shipped, "max_memory_file_bytes": LIMIT,
                                       "compaction_apply": True, "max_move_share": 1.0}))
    script = tmp_path / "probe.py"
    script.write_text(probe.format(hooks=str(HOOKS)), encoding="utf-8")
    state = tmp_path / "state"
    env = dict(os.environ, GEDAECHTNIS_VAULT=str(vault), GEDAECHTNIS_STATE_DIR=str(state),
               GEDAECHTNIS_LIMITS=str(limits_file),
               GEDAECHTNIS_USER_MEMORY=str(tmp_path / "no-user.md"),
               GEDAECHTNIS_FLEET_ROSTER=str(tmp_path / "no-roster.md"),
               # No config at all: the vault declares no queue folder, which is the incident.
               GEDAECHTNIS_CONFIG=str(tmp_path / "no-config.json"))
    p = subprocess.run([sys.executable, "-B", str(script), str(tmp_path)],
                       capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 0, p.stderr
    return p.stdout, state


def test_fixture_is_valid_every_operational_file_is_over_the_bound(vault):
    """Precondition, asserted: a file under the bound would be left alone for the wrong reason."""
    for rel in OPERATIONAL:
        assert (vault / rel).stat().st_size > LIMIT, rel
    assert (vault / "Proj" / "Position.md").stat().st_size > LIMIT


def test_a_queue_notice_or_non_role_file_over_every_threshold_is_UNTOUCHED(vault, tmp_path):
    before = {rel: (vault / rel).read_bytes() for rel in OPERATIONAL}
    out = json.loads(run(tmp_path, vault)[0])
    for rel, data in before.items():
        assert (vault / rel).read_bytes() == data, f"{rel} was rewritten"
    archives = sorted(str(p.relative_to(vault)) for p in vault.rglob("*-archive*.md"))
    assert archives and all(a.startswith("Proj/Position-archive") for a in archives), archives
    # The queue parser still reads every open row.
    rows = (vault / "Pharos/queues/regions/lane.md").read_text().count("- [ ] `q:")
    assert rows == 12


def test_POSITIVE_CONTROL_the_role_file_beside_them_IS_compacted(vault, tmp_path):
    out = json.loads(run(tmp_path, vault)[0])
    assert out["moved"] == ["Proj/Position.md"], out["moved"]
    assert (vault / "Proj" / "Position.md").stat().st_size < LIMIT


def test_each_declined_file_is_named_with_the_reason(vault, tmp_path):
    out = json.loads(run(tmp_path, vault)[0])
    declined = {s["path"]: s for s in out["skipped"] if s.get("kind") == "not-a-role"}
    assert set(declined) == DECLINED, sorted(declined)
    assert all("not a memory role file" in s["why"] for s in declined.values())


def test_a_non_role_file_UNDER_the_bound_is_not_reported(vault, tmp_path):
    """The notice names files that are actually growing — not every markdown file in the vault."""
    small = vault / "Proj" / "small-notes.md"
    small.write_text("# small\n\n## a\nx\n")
    out = json.loads(run(tmp_path, vault)[0])
    assert "Proj/small-notes.md" not in {s["path"] for s in out["skipped"]}


def test_the_stop_hook_logs_the_decline_and_the_next_session_is_told(vault, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "maintenance.json").write_text(json.dumps(
        {"last_cleanup": "2026-01-01", "last_synthesis": "2026-01-01", "version": 1}))
    run(tmp_path, vault, probe=MAIN_PROBE)
    log = (state / "maintenance.log").read_text()
    assert "NOT compacted: Pharos/queues/regions/lane.md — not a memory role file" in log, log
    doc = json.loads((state / "maintenance.json").read_text())
    assert doc["not_compacted"]["n_files"] == len(DECLINED)
    sys.path.insert(0, str(HOOKS))
    import maintenance
    lines = [l for l in maintenance.facts_lines(doc) if "not memory role files" in l]
    assert len(lines) == 1 and "Pharos/queues/regions/lane.md" in lines[0], lines


def test_facts_line_is_silent_when_nothing_was_declined():
    sys.path.insert(0, str(HOOKS))
    import maintenance
    doc = {"not_compacted": {"files": [], "n_files": 0}}
    assert not [l for l in maintenance.facts_lines(doc) if "not memory role files" in l]


def test_the_allow_list_is_the_packages_one_role_roster():
    sys.path.insert(0, str(HOOKS))
    import maintenance, common
    for stem in common.ROLE_STEMS:
        assert maintenance.is_role_file(Path(f"R/{stem}.md")), stem
    for name in ("Inbox.md", "lane.md", "README.md", "Position.txt", "Patterns-verification.md"):
        assert not maintenance.is_role_file(Path(f"R/{name}")), name
