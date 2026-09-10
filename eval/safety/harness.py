"""harness.py — WP9 destruction-eval plumbing: build a sandbox, run one case through the REAL
gate.py doors as a subprocess, render the derived table/JSON. stdlib only.

**Every case gets its OWN fresh sandbox** (a fresh tmp dir → `build_world()`), exactly like the
`world` fixture in `tests/test_gate.py`/`test_doors.py` — so a lock planted for case 41 can never
leak into case 42, and the same target filename can be reused across cases without one case's
setup corrupting another's expectation. Slower than a shared sandbox; correct is worth it (Global/
Errata: "a suite that writes the application's real sidecar makes its own verdict depend on the
machine's state" — the sibling failure mode here would be inter-CASE state, not machine state).

**The baseline run is a real subprocess of the unmodified `hooks/gate.py`** — nothing in this file
imports or monkeypatches the plugin for that path. Only `--mutant` (see `mutant_gate.py`) runs
in-process with one named rule neutralised, and that is a SEPARATE script this harness invokes
instead of `gate.py`, never the same code path used for the real proof.
"""
from __future__ import annotations
import hashlib, json, os, subprocess, sys, tempfile, time
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[2] / "hooks"
SAFETY = Path(__file__).resolve().parent
MUTANT_GATE = SAFETY / "mutant_gate.py"

PLACEHOLDER_KEYS = ("VAULT", "REPO", "REPO_VOCAB", "OUTSIDE", "NOLANE", "SCRATCH", "HOME", "TOOLROOT")


# ---------------------------------------------------------------------- sandbox ----

def build_world(base: Path) -> dict:
    """One fresh sandbox: a vault with two lanes/regions and the shared surfaces every case needs,
    plus a repo per lane, a no-marker dir, an outside (non-vault) project, and a plain scratch dir.
    Mirrors the `world` fixtures in test_gate.py/test_doors.py, widened to two lanes because the
    cross-lane partition cases (D/E) need a SECOND declared region to write into."""
    vault = base / "Atlas"
    (vault / "Global").mkdir(parents=True)
    (vault / "Pharos" / "queues" / "regions").mkdir(parents=True)
    (vault / "Mnemosyne" / "UkrainianCard").mkdir(parents=True)
    (vault / "Mnemosyne" / "UkrainianVocab").mkdir(parents=True)
    (vault / "Speculum").mkdir()
    (vault / "Concilium" / "Melchior").mkdir(parents=True)
    (vault / "Channels" / "ledger").mkdir(parents=True)
    # NOTE: no `git init` here — gate.py never runs or inspects git status on VAULT/OUTSIDE itself
    # (confirmed by reading hooks/gate.py: every git-law rule is TEXT parsing of the command
    # string, not a real git invocation), so a real repo would be pure overhead paid 100+ times
    # per run for nothing exercised. `git -C {VAULT} status`-shaped twins still work correctly:
    # gate.py never executes them, it only classifies the command text.

    (vault / "Global" / "fleet-roster.md").write_text(
        "```fleet-roster\nlane: CARD\nrepo: repo-card\n\nlane: VOCAB\nrepo: repo-vocab\n```\n")
    (vault / "Global" / "Patterns.md").write_text("# Patterns\n\n- an entry\n")
    (vault / "Global" / "Map.md").write_text("# Map\n")
    (vault / "Pharos" / "queues" / "regions" / "ukrainian-card.md").write_text(
        "- [ ] `q:CA-2026-01-01-SEED-1` seed row\n")
    (vault / "Pharos" / "queues" / "regions" / "ukrainian-vocab.md").write_text(
        "- [ ] `q:VO-2026-01-01-SEED-1` seed row\n")
    (vault / "Pharos" / "artifacts-index.md").write_text(
        "# Artifacts index\n\nPrefix every id with the publish date.\n")
    (vault / "Mnemosyne" / "Canon.md").write_text("# Canon\n\n- a shared decision\n")
    (vault / "Mnemosyne" / "Position.md").write_text("# Position\n\n- shared state\n")
    (vault / "Mnemosyne" / "UkrainianCard" / "Errata.md").write_text("# Errata\n\n- an entry\n")
    (vault / "Mnemosyne" / "UkrainianCard" / "Inbox.md").write_text("# Inbox\n")
    (vault / "Mnemosyne" / "UkrainianVocab" / "Errata.md").write_text("# Errata\n\n- an entry\n")
    (vault / "Mnemosyne" / "UkrainianVocab" / "Inbox.md").write_text("# Inbox\n")
    (vault / "Speculum" / "Position.md").write_text("# Position\n\n- foreign state\n")
    (vault / "Channels" / "ledger" / "2026-01.tsv").write_text(
        "N-2026-01-01-0001\t2026-01-01T00:00:00\tCARD\t*\tfact\t-\tseed row\n")

    repo_card = base / "repo-card"; repo_card.mkdir()
    (repo_card / ".atlas-lane").write_text(
        "lane: CARD\npath: Mnemosyne/UkrainianCard/\npath: Global/\npath: Pharos/queues/regions/ukrainian-card.md\n")
    repo_vocab = base / "repo-vocab"; repo_vocab.mkdir()
    (repo_vocab / ".atlas-lane").write_text(
        "lane: VOCAB\npath: Mnemosyne/UkrainianVocab/\npath: Global/\npath: Pharos/queues/regions/ukrainian-vocab.md\n")
    nolane = base / "nolane"; nolane.mkdir()
    outside = base / "outside-project"; outside.mkdir()
    (outside / "build" / "media").mkdir(parents=True)
    (outside / "build" / "media" / "clip.mp4").write_text("x")
    (outside / "dist").mkdir()
    (outside / "scratch").mkdir()
    (outside / "scratch" / "other.db").write_text("x")
    (outside / "tmp").mkdir()
    (outside / "tmp" / "note.md").write_text("x")
    (outside / "whisper_cache.json").write_text("{}")
    (outside / "file.txt").write_text("x")
    scratch = base / "scratch"; scratch.mkdir()

    state = base / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "partition.mode").write_text("deny")   # the eval's standard posture: strict enforcement

    env = dict(os.environ, GEDAECHTNIS_VAULT=str(vault), GEDAECHTNIS_STATE_DIR=str(state),
               GEDAECHTNIS_FLEET_ROSTER=str(vault / "Global" / "fleet-roster.md"),
               GEDAECHTNIS_USER_MEMORY=str(base / "no-such-user-memory.md"),
               GEDAECHTNIS_CONFIG=str(base / "no-such-config.json"),
               HOME=str(base))
    for k in list(env):
        if k.startswith("GEDAECHTNIS_") and k not in (
            "GEDAECHTNIS_VAULT", "GEDAECHTNIS_STATE_DIR", "GEDAECHTNIS_FLEET_ROSTER",
            "GEDAECHTNIS_USER_MEMORY", "GEDAECHTNIS_CONFIG"):
            del env[k]           # a real ~/.claude/gedaechtnis-shaped env var must never leak into the sandbox
    return dict(vault=vault, repo=repo_card, repo_vocab=repo_vocab, nolane=nolane, outside=outside,
               scratch=scratch, home=base, state=state, env=env)


def placeholders(w: dict) -> dict:
    return {"VAULT": str(w["vault"]), "REPO": str(w["repo"]), "REPO_VOCAB": str(w["repo_vocab"]),
            "OUTSIDE": str(w["outside"]), "NOLANE": str(w["nolane"]), "SCRATCH": str(w["scratch"]),
            "HOME": str(w["home"]), "TOOLROOT": str(HOOKS.parent)}


def substitute(obj, subs: dict):
    """Recursively replace every `{KEY}` placeholder in every string value/key of `obj`."""
    if isinstance(obj, str):
        for k, v in subs.items():
            obj = obj.replace("{" + k + "}", v)
        return obj
    if isinstance(obj, dict):
        return {substitute(k, subs): substitute(v, subs) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute(x, subs) for x in obj]
    return obj


# ---------------------------------------------------------------------- setup ops ----

def plant_lock(w: dict, path: Path, session_id: str, age_s: float) -> None:
    lk = w["state"] / "filelocks"; lk.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha1(os.path.realpath(str(path)).encode("utf-8")).hexdigest()
    (lk / name).write_text(json.dumps({"session_id": session_id, "ts": time.time() - age_s, "path": str(path)}))


def apply_setup(w: dict, setup: list, subs: dict) -> None:
    for op in setup or []:
        kind = op.get("op")
        if kind == "write":
            p = Path(substitute(op["path"], subs))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(substitute(op.get("content", ""), subs))
        elif kind == "mkdir":
            Path(substitute(op["path"], subs)).mkdir(parents=True, exist_ok=True)
        elif kind == "plant_lock":
            plant_lock(w, Path(substitute(op["path"], subs)), op["session_id"], float(op["age_s"]))
        elif kind == "partition_mode":
            (w["state"] / "partition.mode").write_text(op["value"])
        else:
            raise ValueError(f"unknown setup op: {kind!r}")


# ---------------------------------------------------------------------- running a case ----

def gate_cmd(mutant: str | None) -> list[str]:
    return [sys.executable, str(MUTANT_GATE)] if mutant else [sys.executable, str(HOOKS / "gate.py")]


def run_gate(w: dict, which: str, payload: dict, mutant: str | None, extra_env: dict | None = None) -> tuple[str | None, str]:
    """Invoke the door (real subprocess, or the mutant wrapper) and return (decision, reason)."""
    env = dict(w["env"])
    if extra_env:
        env.update(extra_env)
    if mutant:
        env["GEDAECHTNIS_EVAL_MUTANT"] = mutant
    p = subprocess.run(gate_cmd(mutant) + [which], input=json.dumps(payload), capture_output=True,
                       text=True, env=env, timeout=30)
    if p.returncode != 0:
        return "ERROR", f"subprocess exited {p.returncode}: {p.stderr.strip()[:400]}"
    out = p.stdout.strip()
    if not out:
        return None, ""
    try:
        doc = json.loads(out)
    except json.JSONDecodeError:
        return "ERROR", f"non-JSON stdout: {out[:400]}"
    hso = doc.get("hookSpecificOutput", {})
    return hso.get("permissionDecision"), hso.get("permissionDecisionReason", "")


KIND_KEY = {"bash": "bash", "write": "write", "edit": "write", "agent": "agent"}


def build_payload(case: dict, subs: dict) -> tuple[str, dict]:
    """(gate.py subcommand, PreToolUse-shaped payload) for one case."""
    kind = case["kind"]
    which = KIND_KEY[kind]
    cwd = substitute(case.get("cwd", "{REPO}"), subs)
    sid = case.get("session_id", "A")
    if kind == "bash":
        payload = {"cwd": cwd, "tool_name": "Bash", "session_id": sid,
                  "tool_input": {"command": substitute(case["command"], subs)}}
    elif kind in ("write", "edit"):
        tool_name = case.get("tool_name", "Write" if kind == "write" else "Edit")
        ti = {"file_path": substitute(case["file_path"], subs)}
        if tool_name == "Write":
            ti["content"] = substitute(case.get("content", ""), subs)
        else:
            ti["old_string"] = substitute(case.get("old_string", ""), subs)
            ti["new_string"] = substitute(case.get("new_string", ""), subs)
        payload = {"cwd": cwd, "tool_name": tool_name, "session_id": sid, "tool_input": ti}
    elif kind == "agent":
        payload = {"cwd": cwd, "tool_name": "Agent", "session_id": sid,
                  "tool_input": substitute(case.get("tool_input", {}), subs)}
    else:
        raise ValueError(f"unknown case kind: {kind!r}")
    return which, payload


def run_case(case: dict, base: Path, mutant: str | None = None) -> dict:
    w = build_world(base)
    subs = placeholders(w)
    apply_setup(w, case.get("setup"), subs)
    which, payload = build_payload(case, subs)
    decision, reason = run_gate(w, which, payload, mutant, case.get("env"))
    expect = case["expect"]
    if expect == "allow":
        ok = decision is None
    else:
        ok = decision == expect and (case.get("rule", "").lower() in reason.lower() if case.get("rule") else True)
    return {"id": case["id"], "kind": case["kind"], "expect": expect, "decision": decision,
           "reason": reason, "ok": ok, "twin_of": case.get("twin_of"), "why": case.get("why", "")}


def run_all(cases: list[dict], mutant: str | None = None, keep_tmp: bool = False) -> list[dict]:
    results = []
    for case in cases:
        with tempfile.TemporaryDirectory(prefix="gedaechtnis-safety-") as tmp:
            results.append(run_case(case, Path(tmp), mutant))
    return results


# ---------------------------------------------------------------------- derived rendering ----

def summarize(results: list[dict]) -> dict:
    adv = [r for r in results if r["expect"] != "allow"]
    twins = [r for r in results if r["expect"] == "allow"]
    return {
        "total": len(results),
        "adversarial": len(adv), "adversarial_ok": sum(r["ok"] for r in adv),
        "twins": len(twins), "twins_ok": sum(r["ok"] for r in twins),
        "misses": [r["id"] for r in results if not r["ok"]],
        "all_ok": all(r["ok"] for r in results),
    }


def render_markdown(results: list[dict], mutant: str | None = None) -> str:
    s = summarize(results)
    verdict = "ALL GREEN" if s["all_ok"] else f"{len(s['misses'])} MISS(ES)"
    lines = [f"# Safety eval{f' — mutant `{mutant}`' if mutant else ''}", "",
            f"adversarial: {s['adversarial_ok']}/{s['adversarial']} refused as expected · "
            f"twins: {s['twins_ok']}/{s['twins']} allowed as expected · {verdict}", "",
            "| id | kind | expect | got | ok | twin_of |", "|---|---|---|---|---|---|"]
    for r in results:
        got = r["decision"] if r["decision"] is not None else "allow"
        lines.append(f"| {r['id']} | {r['kind']} | {r['expect']} | {got} | "
                     f"{'yes' if r['ok'] else 'NO'} | {r['twin_of'] or ''} |")
    if s["misses"]:
        lines += ["", "## Misses", ""]
        for r in results:
            if not r["ok"]:
                lines.append(f"- `{r['id']}`: expected **{r['expect']}**"
                             + (f" citing {r.get('rule', '')!r}" if r.get("rule") else "")
                             + f", got **{r['decision']}** — {r['reason'][:200]}")
    return "\n".join(lines) + "\n"


def load_cases(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError(f"{path}: expected a non-empty JSON array of case rows")
    ids = set()
    for row in data:
        for key in ("id", "kind", "expect"):
            if key not in row:
                raise ValueError(f"{path}: a row is missing {key!r}: {row}")
        if row["id"] in ids:
            raise ValueError(f"{path}: duplicate case id {row['id']!r}")
        ids.add(row["id"])
    return data
