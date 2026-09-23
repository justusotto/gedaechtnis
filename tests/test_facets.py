"""Facets — rule files beside a Boot file, loaded once per session when their trigger fires.

Organised as the body arm's suite is: every trigger kind has a POSITIVE control (the matching call
injects the facet's own text) and a NEGATIVE one (a near-miss call injects nothing), and the silent
side decides whether the arm is safe — a facet that fires on everything is a second Boot file
charged per tool call.

Vault, state, limits and config are redirected into tmp_path; nothing here reads the real vault.
"""
from __future__ import annotations
import importlib, json, os, subprocess, sys
from pathlib import Path
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
HOOKS = PLUGIN / "hooks"
REGION = "Studio/Cards"

ANKI = """---
aliases: [Anki rules]
tools: [mcp__anki__*]
bash: ['localhost:8765', 'anki_backup\\.py', 'x{1,3}yz']
---
# Anki facet

RUN THE BACKUP BEFORE EVERY WRITE.
"""

TEMPLATES = """---
paths:
  - 'templates/**'
  - '*collection.anki2'
rows: ['q:CD-2026-*']
---
# Templates facet

TEMPLATE CSS STAYS IN ONE FILE.
"""


def write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def world(tmp_path, monkeypatch):
    vault = tmp_path / "Vault"
    write(vault / REGION / "Kernel.md", "# Boot\n\nAn index.\n")
    write(vault / REGION / "Kernel-anki.md", ANKI)
    write(vault / REGION / "Kernel-templates.md", TEMPLATES)
    write(vault / REGION / "Position.md", "# Position\n")
    # Graduated, no facets.
    write(vault / "Toolkit" / "Kernel.md", "# Toolkit\n")
    # NOT graduated, but carrying a facet-shaped file: must never fire.
    write(vault / "Loose" / "Position.md", "# Loose\n")
    write(vault / "Loose" / "Kernel-anki.md", ANKI)
    queues = vault / "Queues" / "regions"
    write(queues / "cards.md",
          "- [ ] `q:CU-2026-09-22-TAGGED-1` topic:x facet:anki — a row that needs the anki rules\n"
          "- [ ] `q:CU-2026-09-22-CITER-1` topic:x — cites `q:CU-2026-09-22-TAGGED-1` facet:templates\n")
    write(queues / "other.md",
          "- [ ] `q:SI-2026-09-22-FOREIGN-1` topic:x facet:anki — another region's row, its own reasons\n")
    repo = tmp_path / "cards-repo"
    write(repo / "CLAUDE.md", f"@{vault}/{REGION}/Kernel.md\n")
    write(repo / ".atlas-lane", "lane: CARD\npath: Studio/Cards/\npath: Queues/regions/cards.md\n")
    (repo / ".git").mkdir()
    tool_repo = tmp_path / "tool-repo"
    write(tool_repo / "CLAUDE.md", f"@{vault}/Toolkit/Kernel.md\n")
    (tool_repo / ".git").mkdir()
    state = tmp_path / "state"
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"topology": {"non_region_tops": ["Global", "Queues"],
                                            "queues_dir": "Queues/regions"}}), encoding="utf-8")
    limits_file = tmp_path / "limits.json"
    limits_file.write_text((PLUGIN / "rules" / "limits.json").read_text(encoding="utf-8"), encoding="utf-8")
    env = {"GEDAECHTNIS_VAULT": str(vault), "GEDAECHTNIS_STATE_DIR": str(state),
           "GEDAECHTNIS_LIMITS": str(limits_file), "GEDAECHTNIS_CONFIG": str(cfg),
           "GEDAECHTNIS_USER_MEMORY": str(tmp_path / "no-user-memory.md"),
           "GEDAECHTNIS_FLEET_ROSTER": str(tmp_path / "no-roster.md")}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    for d in (HOOKS, PLUGIN):
        if str(d) not in sys.path:
            sys.path.insert(0, str(d))
    import config, limits, common                              # noqa: PLC0415
    importlib.reload(config); importlib.reload(common); importlib.reload(limits)
    import lesson_push, lazy_body                              # noqa: PLC0415
    importlib.reload(lesson_push); importlib.reload(lazy_body)
    yield dict(tmp=tmp_path, vault=vault, repo=repo, tool_repo=tool_repo, state=state,
               limits_file=limits_file, env=env, mod=lazy_body)
    for k in env:
        os.environ.pop(k, None)
    importlib.reload(config); importlib.reload(common); importlib.reload(limits)
    importlib.reload(lesson_push); importlib.reload(lazy_body)


def log_rows(world):
    f = world["state"] / "facets.log"
    if not f.is_file():
        return []
    return [l.split("\t") for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]


def injects(world):
    return [r for r in log_rows(world) if r[1] == "inject"]


def set_limit(world, **kw):
    data = json.loads(world["limits_file"].read_text(encoding="utf-8"))
    data.update(kw)
    world["limits_file"].write_text(json.dumps(data), encoding="utf-8")
    import limits, lazy_body                                   # noqa: PLC0415
    importlib.reload(limits); importlib.reload(lazy_body)
    return lazy_body


# ------------------------------------------------------------------ tool-name trigger ----

def test_tool_trigger_fires_on_a_matching_tool(world):
    out = world["mod"].facet_notice("t1", "mcp__anki__addNote", {}, str(world["repo"]))
    assert out and "RUN THE BACKUP BEFORE EVERY WRITE." in out
    assert "TEMPLATE CSS" not in out                            # only the facet that matched
    assert "tool:mcp__anki__*" in out


def test_tool_trigger_is_silent_on_a_near_miss(world):
    assert world["mod"].facet_notice("t2", "mcp__ankix_addNote", {}, str(world["repo"])) is None
    # The glob is anchored: a tool whose name merely CONTAINS the pattern's stem is not a match.
    assert world["mod"].facet_notice("t2", "other__mcp__anki__addNote", {}, str(world["repo"])) is None
    assert world["mod"].facet_notice("t2", "Read", {}, str(world["repo"])) is None
    assert injects(world) == []


# ------------------------------------------------------------------ bash trigger ----

def test_bash_trigger_fires_on_a_matching_command(world):
    cmd = "curl -s localhost:8765 -d '{\"action\":\"version\"}'"
    out = world["mod"].facet_notice("b1", "Bash", {"command": cmd}, str(world["repo"]))
    assert out and "RUN THE BACKUP BEFORE EVERY WRITE." in out


def test_bash_trigger_is_silent_on_a_near_miss(world):
    cmd = "curl -s localhost:8766; python3 anki_backupXpy"
    assert world["mod"].facet_notice("b2", "Bash", {"command": cmd}, str(world["repo"])) is None


def test_a_bash_pattern_on_a_non_bash_tool_is_ignored(world):
    """The `command` field is only read for Bash; another tool carrying one must not fire it."""
    out = world["mod"].facet_notice("b3", "SomeTool", {"command": "localhost:8765"}, str(world["repo"]))
    assert out is None


# ------------------------------------------------------------------ path trigger ----

def test_path_trigger_fires_on_a_repo_relative_glob(world):
    f = write(world["repo"] / "templates" / "front" / "card.css", "x")
    out = world["mod"].facet_notice("p1", "Edit", {"file_path": str(f)}, str(world["repo"]))
    assert out and "TEMPLATE CSS STAYS IN ONE FILE." in out and "path:templates/**" in out


def test_path_trigger_fires_from_a_bash_argument(world):
    """A facet's surface is also what a command OPENS, not only what it writes."""
    cmd = "sqlite3 ~/Library/Anki2/User/collection.anki2 'select 1'"
    out = world["mod"].facet_notice("p2", "Bash", {"command": cmd}, str(world["repo"]))
    assert out and "TEMPLATE CSS STAYS IN ONE FILE." in out


def test_path_trigger_is_silent_on_a_near_miss(world):
    f = write(world["repo"] / "src" / "templates.py", "x")
    assert world["mod"].facet_notice("p3", "Edit", {"file_path": str(f)}, str(world["repo"])) is None


# ------------------------------------------------------------------ row trigger ----

def test_row_tag_in_the_prompt_fires(world):
    out = world["mod"].facet_prompt_notice("r1", "Work the row. facet:anki", str(world["repo"]))
    assert out and "RUN THE BACKUP BEFORE EVERY WRITE." in out and "row:facet:anki" in out


def test_row_tag_near_miss_is_silent(world):
    m = world["mod"]
    assert m.facet_prompt_notice("r2", "facet:ankis and prefacet:anki and facet:other", str(world["repo"])) is None


def test_a_q_id_glob_in_the_facet_fires(world):
    out = world["mod"].facet_prompt_notice("r3", "Do `q:CD-2026-09-30-X-1`.", str(world["repo"]))
    assert out and "TEMPLATE CSS STAYS IN ONE FILE." in out


def test_a_q_id_whose_queue_row_carries_the_tag_fires(world):
    out = world["mod"].facet_prompt_notice("r4", "Your row: q:CU-2026-09-22-TAGGED-1", str(world["repo"]))
    assert out and "RUN THE BACKUP BEFORE EVERY WRITE." in out
    assert "TEMPLATE CSS" not in out


def test_another_regions_queue_row_does_not_fire_this_regions_facet(world):
    out = world["mod"].facet_prompt_notice("r7", "q:SI-2026-09-22-FOREIGN-1", str(world["repo"]))
    assert out is None


def test_parse_frontmatter_reads_crlf_text(world):
    """At the function's own contract. Through the hook, `Path.read_text` already turns CRLF into LF
    (universal newlines), so the file-level control below cannot tell the guard is there; a caller
    handing text read another way (bytes decoded, `newline=""`) is what this protects."""
    fields, body = world["mod"].parse_frontmatter(ANKI.replace("\n", "\r\n"))
    assert fields["tools"] == ["mcp__anki__*"] and body.startswith("# Anki facet")


def test_a_crlf_facet_file_still_fires(world):
    write(world["vault"] / REGION / "Kernel-anki.md", ANKI.replace("\n", "\r\n"))
    out = world["mod"].facet_notice("cr1", "mcp__anki__x", {}, str(world["repo"]))
    assert out and "RUN THE BACKUP BEFORE EVERY WRITE." in out and "tools:" not in out


def test_a_tab_in_a_pattern_keeps_the_log_row_at_six_columns(world):
    write(world["vault"] / REGION / "Kernel-anki.md", "---\nbash: ['a\tb']\n---\nTAB BODY\n")
    assert world["mod"].facet_notice("tb1", "Bash", {"command": "echo a\tb"}, str(world["repo"]))
    (row,) = injects(world)
    assert len(row) == 6


def test_a_citing_rows_tag_is_not_the_cited_rows(world):
    """Anchored on the row's checkbox line: CITER-1 cites TAGGED-1 and carries facet:templates, and
    that tag belongs to CITER-1 — naming TAGGED-1 must not load templates."""
    out = world["mod"].facet_prompt_notice("r5", "q:CU-2026-09-22-TAGGED-1", str(world["repo"]))
    assert "TEMPLATE CSS" not in (out or "")
    out2 = world["mod"].facet_prompt_notice("r6", "q:CU-2026-09-22-CITER-1", str(world["repo"]))
    assert out2 and "TEMPLATE CSS STAYS IN ONE FILE." in out2


# ------------------------------------------------------------------ once per session ----

def test_a_facet_fires_once_per_session(world):
    m = world["mod"]
    cmd = {"command": "curl localhost:8765"}
    assert m.facet_notice("o1", "Bash", cmd, str(world["repo"]))
    assert m.facet_notice("o1", "Bash", cmd, str(world["repo"])) is None
    assert m.facet_notice("o1", "mcp__anki__x", {}, str(world["repo"])) is None     # another trigger, same facet
    assert m.facet_prompt_notice("o1", "facet:anki", str(world["repo"])) is None
    assert len(injects(world)) == 1
    assert m.facet_notice("o2", "Bash", cmd, str(world["repo"]))                    # a new session fires again


def test_a_claim_that_cannot_reach_disk_is_silent(world, monkeypatch):
    m = world["mod"]
    monkeypatch.setattr(m, "_claim_facet", lambda sid, key: False)
    assert m.facet_notice("c1", "mcp__anki__x", {}, str(world["repo"])) is None
    assert injects(world) == []


# ------------------------------------------------------------------ no facets ----

def test_a_region_without_facets_injects_nothing_and_logs_once(world):
    m = world["mod"]
    for _ in range(3):
        assert m.facet_notice("n1", "mcp__anki__x", {"command": "localhost:8765"}, str(world["tool_repo"])) is None
    rows = log_rows(world)
    assert [r[1:4] for r in rows] == [["none", "n1", "Toolkit"]]
    assert rows[0][5] == "0"


def test_a_vault_with_no_facet_anywhere_logs_one_star_row(world):
    for f in (world["vault"] / REGION).glob("Kernel-*.md"):
        f.rename(f.with_suffix(".txt"))
    (world["vault"] / "Loose" / "Kernel-anki.md").rename(world["vault"] / "Loose" / "anki.txt")
    m = world["mod"]
    assert m.any_facets() is False
    for _ in range(2):
        assert m.facet_notice("v1", "mcp__anki__x", {}, str(world["repo"])) is None
        assert m.facet_prompt_notice("v1", "facet:anki", str(world["repo"])) is None
    assert [r[1:4] for r in log_rows(world)] == [["none", "v1", "*"]]


def test_bash_paths_skip_prose_and_are_capped(world):
    m = world["mod"]
    got = m.bash_paths("echo 'the end.' then more. see ./a/b and x.css " + " ".join(f"f{i}.txt" for i in range(500)),
                       str(world["repo"]))
    names = [p.name for p in got]
    assert "b" in names and "x.css" in names
    assert not any(n.endswith("end.") or n == "more." for n in names)
    assert len(got) == m.BASH_PATHS_MAX


def test_an_ungraduated_region_never_fires_its_facet_shaped_file(world):
    f = world["vault"] / "Loose" / "Position.md"
    out = world["mod"].facet_notice("u1", "mcp__anki__x", {"file_path": str(f)}, str(world["vault"] / "Loose"))
    assert out is None and injects(world) == []


def test_a_repo_naming_an_ungraduated_region_never_fires_it(world, tmp_path):
    """Reached through the REPO branch, which — unlike the vault branch — can name a region with no
    Boot file; `facets_of`'s own gate is the only thing between that region's facet-shaped file
    and the session."""
    repo = tmp_path / "loose-repo"
    write(repo / "CLAUDE.md", f"@{world['vault']}/Loose/Position.md\n")
    (repo / ".git").mkdir()
    assert world["mod"]._session_regions(str(repo), []) == ["Loose"]   # the precondition, asserted
    assert world["mod"].facet_notice("u2", "mcp__anki__x", {}, str(repo)) is None
    assert injects(world) == []


def test_facets_switched_off_inject_nothing(world):
    m = set_limit(world, facets_enabled=False)
    assert m.facet_notice("f1", "mcp__anki__x", {}, str(world["repo"])) is None
    assert m.facet_prompt_notice("f1", "facet:anki", str(world["repo"])) is None
    assert m.facts_lines(str(world["repo"])) == []


# ------------------------------------------------------------------ the log ----

def test_the_log_row_names_session_facet_trigger_and_bytes(world):
    out = world["mod"].facet_notice("l1", "Bash", {"command": "python3 anki_backup.py"}, str(world["repo"]))
    (row,) = injects(world)
    assert row[2:5] == ["l1", f"{REGION}/anki", "bash:anki_backup\\.py"]
    assert int(row[5]) == len(out.encode("utf-8"))


def test_a_facet_that_never_fires_costs_nothing(world):
    m = world["mod"]
    for tool in ("Read", "Grep", "Glob"):
        assert m.facet_notice("z1", tool, {"file_path": str(world["repo"] / "README.md")}, str(world["repo"])) is None
    assert injects(world) == []


def test_an_oversized_facet_is_named_not_injected(world):
    m = set_limit(world, facet_max_bytes=20)
    out = m.facet_notice("s1", "mcp__anki__x", {}, str(world["repo"]))
    assert out and "NOT loaded" in out and "RUN THE BACKUP" not in out


# ------------------------------------------------------------------ frontmatter + listing ----

def test_frontmatter_forms(world):
    m = world["mod"]
    fields, body = m.parse_frontmatter(ANKI)
    assert fields["bash"] == ["localhost:8765", "anki_backup\\.py", "x{1,3}yz"]  # comma inside quotes kept
    assert "aliases" not in fields and body.startswith("# Anki facet")
    fields, _ = m.parse_frontmatter(TEMPLATES)
    assert fields["paths"] == ["templates/**", "*collection.anki2"] and fields["rows"] == ["q:CD-2026-*"]
    assert m.parse_frontmatter("# no frontmatter\n") == ({}, "# no frontmatter\n")


def test_a_bad_regex_is_logged_and_the_rest_still_fire(world):
    write(world["vault"] / REGION / "Kernel-anki.md", "---\nbash: ['(unclosed', 'good']\n---\nBODY OK\n")
    out = world["mod"].facet_notice("x1", "Bash", {"command": "echo good"}, str(world["repo"]))
    assert out and "BODY OK" in out
    assert any(r[1] == "bad-regex" for r in log_rows(world))


def test_facts_lines_list_each_facet_within_the_cap(world):
    lines = world["mod"].facts_lines(str(world["repo"]))
    assert len(lines) == 2
    assert all(len(l) <= 120 for l in lines)
    assert lines[0].startswith(f"- Facet {REGION}/anki") and "mcp__anki__*" in lines[0]
    assert world["mod"].facts_lines(str(world["tool_repo"])) == []


def test_the_listing_tool(world):
    p = subprocess.run([sys.executable, str(PLUGIN / "tools" / "facets.py"), REGION],
                       capture_output=True, text=True, env=dict(os.environ), timeout=30)
    assert p.returncode == 0, p.stderr
    assert "Studio/Cards/anki" in p.stdout and "Studio/Cards/templates" in p.stdout
    p = subprocess.run([sys.executable, str(PLUGIN / "tools" / "facets.py"), "Toolkit"],
                       capture_output=True, text=True, env=dict(os.environ), timeout=30)
    assert p.returncode == 1 and "no facet files" in p.stdout


# ------------------------------------------------------------------ the doors ----

def door(world, which, payload):
    p = subprocess.run([sys.executable, str(HOOKS / "chore.py"), which], input=json.dumps(payload),
                       capture_output=True, text=True, env=dict(os.environ), timeout=30)
    assert p.returncode == 0, p.stderr
    lines = [l for l in p.stdout.splitlines() if l.strip()]
    assert len(lines) <= 1, p.stdout
    return json.loads(lines[0]) if lines else None


def test_the_posttooluse_door_emits_one_object(world):
    doc = door(world, "facet", {"session_id": "d1", "cwd": str(world["repo"]), "tool_name": "Bash",
                                "tool_input": {"command": "curl localhost:8765"}})
    hso = doc["hookSpecificOutput"]
    assert hso["hookEventName"] == "PostToolUse" and "RUN THE BACKUP" in hso["additionalContext"]
    assert door(world, "facet", {"session_id": "d9", "cwd": str(world["repo"]), "tool_name": "Bash",
                                 "tool_input": {"command": "curl localhost:9"}}) is None


def test_the_prompt_door_emits_one_object(world):
    doc = door(world, "prompt", {"session_id": "d2", "cwd": str(world["repo"]), "prompt": "facet:templates"})
    hso = doc["hookSpecificOutput"]
    assert hso["hookEventName"] == "UserPromptSubmit" and "TEMPLATE CSS" in hso["additionalContext"]
    assert door(world, "prompt", {"session_id": "d3", "cwd": str(world["repo"]), "prompt": "hello"}) is None


def test_hooks_json_registers_both_doors_with_a_timeout():
    raw = (HOOKS / "hooks.json").read_text(encoding="utf-8")

    def no_dupes(pairs):
        keys = [k for k, _ in pairs]
        assert len(keys) == len(set(keys)), f"duplicate key in hooks.json: {keys}"
        return dict(pairs)
    doc = json.loads(raw, object_pairs_hook=no_dupes)["hooks"]
    for groups in doc.values():
        for g in groups:
            for h in g["hooks"]:
                assert isinstance(h.get("timeout"), int), h
    post = [h["command"] for g in doc["PostToolUse"] if "matcher" not in g for h in g["hooks"]]
    assert any(c.endswith("chore.py facet") for c in post)
    prompt = [h["command"] for g in doc["UserPromptSubmit"] for h in g["hooks"]]
    assert any(c.endswith("chore.py prompt") for c in prompt)


def test_session_start_lists_the_regions_facets(world):
    p = subprocess.run([sys.executable, str(HOOKS / "session_start.py")],
                       input=json.dumps({"session_id": "ss1", "cwd": str(world["repo"]), "source": "startup"}),
                       capture_output=True, text=True, env=dict(os.environ), timeout=30)
    assert p.returncode == 0, p.stderr
    ctx = json.loads(p.stdout.strip().splitlines()[-1])["hookSpecificOutput"]["additionalContext"]
    assert f"- Facet {REGION}/anki" in ctx and f"- Facet {REGION}/templates" in ctx
    p = subprocess.run([sys.executable, str(HOOKS / "session_start.py")],
                       input=json.dumps({"session_id": "ss2", "cwd": str(world["tool_repo"]), "source": "startup"}),
                       capture_output=True, text=True, env=dict(os.environ), timeout=30)
    assert "- Facet " not in p.stdout
