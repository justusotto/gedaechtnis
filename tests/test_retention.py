import json, os, subprocess, sys
from pathlib import Path
import pytest
SCRIPT = Path(__file__).resolve().parents[1] / "retention.py"

def sh(*a, **k):
    return subprocess.run([sys.executable, str(SCRIPT), *a], capture_output=True, text=True, **k)

@pytest.fixture
def review(tmp_path):
    repo = tmp_path / "repo"; rv = repo / ".orchestration" / "review"
    arc = rv / "arc-2026-09-01"; (arc / "_shots").mkdir(parents=True); (arc / "round-1").mkdir(); (arc / "round-2" / "shots").mkdir(parents=True); (arc / "answers").mkdir()
    (arc / "build_page.py").write_text("print('x')"); (arc / "REPORT.md").write_text("r"); (arc / "answers" / "v.json").write_text("{}")
    (arc / "_shots" / "a.png").write_bytes(b"x" * 100); (arc / "page.html").write_text("<p>")
    (arc / "round-1" / "r1.png").write_bytes(b"y" * 50); (arc / "round-1" / "ROUND.json").write_text('{"closed": "2026-09-05"}')
    (arc / "round-2" / "shots" / "r2.png").write_bytes(b"z" * 70)
    (arc / "mystery.bin").write_bytes(b"?")
    photo = rv / "fotocull-2026-08-05"; photo.mkdir(); (photo / "_DSC0001.jpg").write_bytes(b"j" * 300)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"], check=True)
    return rv

def test_scan_classifies_and_touches_nothing(review, tmp_path):
    out = tmp_path / "r.json"; p = sh("scan", str(review), "--json", str(out)); assert p.returncode == 0, p.stderr
    rep = json.loads(out.read_text()); arcs = {a["arc"]: a for a in rep["arcs"]}
    a = arcs["arc-2026-09-01"]
    assert a["generator_tracked"] is True
    cands = {c["path"] for c in a["candidates"]}
    assert cands == {"round-1/r1.png"}                       # derived + round closed + generator tracked
    assert a["unknown"] == ["mystery.bin"]                   # no rule → irreplaceable
    assert a["classes"]["derived_protected"][0] == 3         # _shots/a.png, page.html, round-2/shots/r2.png (root/round-2 not closed)
    assert a["classes"]["evidence"][0] == 4                  # build_page.py, REPORT.md, answers/v.json, round-1/ROUND.json
    ph = arcs["fotocull-2026-08-05"]
    assert ph["candidates"] == [] and ph["classes"]["derived_protected"][0] == 1 and "generator not tracked" in list(ph["protected_reasons"])[0]
    assert (review / "arc-2026-09-01" / "round-1" / "r1.png").exists()   # dry run moved nothing

def test_apply_refuses_without_yes_and_bundles_with_readme(review):
    p = sh("apply", str(review)); assert p.returncode == 2 and (review / "arc-2026-09-01" / "round-1" / "r1.png").exists()
    p = sh("apply", str(review), "--yes"); assert p.returncode == 0, p.stderr
    bundles = list(review.parent.glob("Cleanup *")); assert len(bundles) == 1
    b = bundles[0]
    assert (b / "arc-2026-09-01" / "round-1" / "r1.png").exists() and not (review / "arc-2026-09-01" / "round-1" / "r1.png").exists()
    assert (b / "README-what-went-where.html").read_text().count("round-1/r1.png") == 1
    assert (review / "arc-2026-09-01" / "mystery.bin").exists() and (review / "fotocull-2026-08-05" / "_DSC0001.jpg").exists()

def test_close_round_then_candidate(review):
    r2 = review / "arc-2026-09-01" / "round-2"
    assert sh("close-round", str(r2), "--date", "2026-09-08").returncode == 0
    assert json.loads((r2 / "ROUND.json").read_text())["closed"] == "2026-09-08"
    p = sh("scan", str(review)); assert "round-2/shots/r2.png" not in p.stdout  # summary only prints totals
    out = review.parent / "x.json"; sh("scan", str(review), "--json", str(out))
    a = [a for a in json.loads(out.read_text())["arcs"] if a["arc"] == "arc-2026-09-01"][0]
    assert {c["path"] for c in a["candidates"]} == {"round-1/r1.png", "round-2/shots/r2.png"}

def test_manifest_override_and_inventory(review, tmp_path):
    arc = review / "arc-2026-09-01"; (arc / "MANIFEST.toml").write_text('[classes]\nevidence = ["mystery.bin"]\n')
    out = tmp_path / "r.json"; sh("scan", str(review), "--json", str(out))
    a = [a for a in json.loads(out.read_text())["arcs"] if a["arc"] == "arc-2026-09-01"][0]
    assert a["unknown"] == []
    inv = tmp_path / "inv.tsv"; p = sh("inventory", str(arc / "answers"), "--out", str(inv)); assert "1 files hashed" in p.stdout
    assert sh("inventory", "--verify", str(inv)).returncode == 0
    (arc / "answers" / "v.json").write_text("{changed}")
    p = sh("inventory", "--verify", str(inv)); assert p.returncode == 1 and "CHANGED" in p.stdout
