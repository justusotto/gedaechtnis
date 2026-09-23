#!/usr/bin/env python3
"""personas.py — Part B of the dad test: does the package make a session ASK things?

    python3 personas.py --payload-dir <dir from Part A> --json out.json
    python3 personas.py --payload-dir <dir> --dry-run      # prints the prompts, calls nothing

Part A measures what the plugin DOES to a vault, deterministically and for free. This measures the
one criterion that is a property of the shipped PROSE rather than of any code: **≤3 questions to the
user across the run.** `rules/operating-rules.md` makes the stronger claim itself — *"You ask the
user exactly two things, ever, and each at most once"* — so the criterion has exactly one question
of slack over what the package promises about itself.

**Each probe is a real session, deliberately unhelpfully briefed.** A clean `claude -p` is given the
sandbox's ACTUAL assembled boot payload at a sampled day, then one message from a person who is not
technical — and nothing else. It is never told to avoid questions; being told would measure the
instruction rather than the package.

**The tasks are chosen to TEMPT a question.** A probe that hands over an unambiguous chore proves
nothing: every session answers that one without asking. So each carries a real invitation to ask —
a note that contradicts an older one, a file that has grown long, a thing that looks like it wants
deleting, an "is everything tidy?" with no criterion in it. A pass means the rules held under
temptation; a pass with no temptation would mean nothing at all.

**Why a subprocess and not an in-harness subagent.** A subagent inherits the calling session's own
system prompt and tools — the fleet's, not a stranger's — and would be answering as somebody who
already knows this project. `claude -p --setting-sources project` from a neutral directory was
measured (row A1 §2) NOT to load the machine owner's `~/.claude/CLAUDE.md`: asked directly whether
its context contained two private names, it answered NO. That is the clean boot a stranger has, and
it is the only instrument here that is one.

**★ WHAT THIS FILE CANNOT YET MEASURE, and it is two things, both found by reading transcripts
rather than counts.** The first real run returned *9 questions across 10 sessions, FAIL* — every
question a permission artefact (*"could you grant write access"*), because the probes could not
write. Granting `acceptEdits` cut that but did not clear it: **8 of 10 replies still mention access
or a blocked action**, so some friction remains outside the edit path.

The second is worse because it is quieter: **the fixture vault is the growth simulator's synthetic
filler** — entries like *"The threshold for the upload is K00001"*, the same paragraph three times
per entry — while the persona talks about an allotment. Three replies noticed and said so outright;
day 90's opens *"this isn't really 'tidy or not' … it's synthetic filler … generated placeholder
data."* A session asking what the vault is FOR is answering the fixture, not the package.

So the honest verdict of this file today is **UNCHECKED**, and the count it prints (7 of 10) is
recorded as evidence about the harness. Clearing it needs a fixture vault whose CONTENT is a
plausible allotment memory rather than load-generated filler — real work, not a flag — and a second
pass over the remaining access friction. Neither is a reason to soften the criterion.

**The count is mechanical and the transcript is kept.** A question is a sentence ending in `?`
addressed to the user. A model judging another model's helpfulness would be a second opinion about
tone; the criterion is about a number, and the number is countable. Every reply is saved next to the
result so the count can be argued with.
"""
from __future__ import annotations

import argparse, hashlib, json, re, subprocess, sys, time
from pathlib import Path

MODEL = "claude-sonnet-5"
EFFORT = "low"

# One message per sampled day, from someone who keeps notes about an allotment and does not think
# of any of this as software. Each is an invitation to ask something.
TASKS = {
    1: "I've just installed this. I want to start keeping notes about my allotment — what I plant "
       "and what goes wrong. Can you set that up for me?",
    2: "Write down that the tomatoes did badly in the shady bed. I think I said the opposite in "
       "the spring, but this year they definitely didn't like it.",
    7: "Add a note that slugs got the lettuce again. Also that file is getting quite long now isn't "
       "it, is that a problem?",
    14: "Put this somewhere sensible: the water butt overflows when it rains hard, and I've decided "
        "not to bother fixing it this year.",
    21: "Note down that the new compost bin works well. Do you need to save this anywhere or does "
        "it happen by itself?",
    30: "Add that the apple tree needs pruning in February. There's a lot of old stuff in there "
        "from last year, should some of it be cleared out?",
    45: "I've changed my mind about the shady bed — I'm going to use it for rhubarb. I know I wrote "
        "something different before.",
    60: "There's loads of notes about slugs now and most of them say the same thing. Can you get "
        "rid of the ones that don't matter?",
    75: "Just make a note of today please.",
    90: "Is everything tidy in there? It's been about three months.",
}

# A question is a sentence ending in `?`. Rhetorical ones inside a quoted rule are excluded below;
# everything else counts, because the criterion is about what the person is asked to answer.
#
# ★ THE BOUNDARY IS A FULL STOP FOLLOWED BY SPACE, NOT ANY FULL STOP. The first version split on
# every `.`, so a reply mentioning `Position.md` produced the "question" **"md`)?"** — and the
# count that reached the row's verdict contained three such fragments out of nine. An instrument
# that manufactures the thing it counts fails in the direction that looks like a finding, which is
# the direction nobody double-checks. A sentence boundary needs the space after the stop; a
# filename does not have one.
_SENTENCE = re.compile(r"(?:[^.!?\n]|\.(?!\s|$))*\?")

_EXCLUDE = re.compile(r"^\s*[>|]|^\s*```|\byes / no / never\b", re.I)


def questions_in(text: str) -> list[str]:
    """Every question the reply puts to the user.

    Lines inside a quote or a code fence do not count: a session that QUOTES the install question
    while explaining itself has not asked it. This is generous toward the package, so the count is
    a lower bound — and the transcripts are saved so a reader can disagree with any single call."""
    out = []
    for line in text.splitlines():
        if _EXCLUDE.search(line):
            continue
        for m in _SENTENCE.finditer(line):
            q = m.group(0).strip()
            if q and len(q) > 3:
                out.append(q)
    return out


def sandbox_region(root: Path, named: str | None = None) -> str:
    """Which directory under `root` is Part A's repo.

    ★ The region used to be the literal string `project`, in Part A and here, and `notes` fixtures
    now name themselves (TEMPLATESHAPE-1) — so this reads the root Part A actually built instead of
    assuming. A wrong answer here is not quiet: `run()` refuses before spending. The rule is
    "the one directory that is not the sandbox's own furniture" (dotfile directories — a stray
    `.git`, a `.pytest_cache` — are skipped, or any one of them would make this ambiguous); where it
    IS ambiguous the old name wins, because that is what every published payload dir was built with,
    and `run()`'s refusal then catches a wrong answer before a single session is paid for. This is a
    fallback with a loud downstream check, not a rule that has to be right."""
    if named:
        return named
    furniture = {"home", "vault", "state"}
    dirs = sorted(d.name for d in root.iterdir()
                  if d.is_dir() and d.name not in furniture
                  and not d.name.startswith(".")) if root.is_dir() else []
    if len(dirs) == 1:
        return dirs[0]
    return "project"


def _sandbox(root: Path, region: str | None = None):
    """The simulator's own `Sandbox`, rebound to a root Part A already installed. Imported by file
    path under a private name: `eval/simulator/run.py` and `eval/dadtest/run.py` are both `run.py`,
    and a bare second `import run` returns the first one silently."""
    import importlib.util
    here = Path(__file__).resolve().parent
    plugin = here.parent.parent
    for extra in (str(plugin), str(plugin / "hooks")):
        if extra not in sys.path:
            sys.path.insert(0, extra)
    spec = importlib.util.spec_from_file_location("_personas_sim", plugin / "eval" / "simulator" / "run.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_personas_sim"] = mod
    spec.loader.exec_module(mod)
    return mod.Sandbox(root, region=sandbox_region(root, region))


RULES_MARKER = "The operating rules for this vault"


def strip_rules(payload: str) -> tuple[str, bool]:
    """The same payload with the PACKAGE'S OWN RULES removed — the control arm's input.

    ★ WHY A CONTROL ARM AT ALL. A count of questions over the real payload is a claim about the
    package only if the same model would NOT have asked them anyway. Some of these tasks invite a
    question from anybody: *"is that a problem?"*, *"should some of it be cleared out?"* A verdict
    about the thing under test is a claim about the HARNESS until the harness has been varied, and
    this is the variation — same model, same task, same vault, same tools, rules gone.

    What is removed is the operating-rules block and nothing else. The vault CONTENT stays, because
    a session with no memory to read is answering a different question than a session with one; it
    is the package's PROSE that is being isolated, not its data."""
    i = payload.find(RULES_MARKER)
    if i < 0:
        return payload, False
    return payload[:i].rstrip() + "\n", True


def prompt_for(payload: str, task: str) -> str:
    """What the session is given: the rules it would have been injected, then the person's message.

    Nothing else. No instruction about how to behave, no mention of a test, and no hint that
    questions are being counted — all three would measure the instruction instead of the package."""
    return (f"{payload}\n\n---\n\nThe user says:\n\n{task}\n")


def ask(prompt: str, cwd: Path, env: dict, timeout: int = 300) -> tuple[int, str, str]:
    """One clean session, IN the sandbox, able to write to it.

    `--setting-sources project` keeps the machine owner's own memory out of the context (measured,
    row A1 §2). stdin is DEVNULL because `claude -p` reads INHERITED stdin and appends it to the
    prompt; a harness that left it open would be asking a different question than the one it
    printed.

    ★ `--permission-mode acceptEdits`, and the first version of this file did NOT have it. Without
    it every probe ran with no write access to the vault, hit a blocked edit, and asked the user to
    grant permission — so the run came back "9 questions across 10 sessions, FAIL" and every single
    question was about the harness's own file permissions. A criterion about what the PACKAGE makes
    a session ask cannot be measured by a session that cannot act. What is bounded instead of the
    permission is the BLAST RADIUS: `run()` fingerprints the real vault and the real user memory
    before and after and REFUSES to report a result if either moved."""
    p = subprocess.run(
        ["claude", "-p", prompt, "--model", MODEL, "--effort", EFFORT,
         "--setting-sources", "project", "--permission-mode", "acceptEdits"],
        cwd=str(cwd), env=env, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=timeout)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def sandbox_fingerprint(sb) -> dict:
    """What the probes of ONE arm saw, so the other arm's comparison can be CHECKED not assumed.

    ★ THE SUBTRACTION'S HIDDEN PRECONDITION. Part B's headline is a comparison between two arms —
    a question asked in both is the model's, not the package's. That is only sound if both arms read
    the SAME vault. They did: the probes' writes never actually landed, because `acceptEdits`
    does not reach files outside the session's cwd. But that is a HARNESS LIMITATION doing the work
    of an isolation guarantee, and the day the limitation is fixed the second arm would silently be
    reading the first arm's edits — and the subtraction would be between unlike things while
    printing exactly the same shape of number.

    So each arm records what it saw. A reader comparing two result files can check the precondition
    instead of inheriting it; if the fingerprints differ, the arms are not comparable and the
    subtraction is void."""
    out = {}
    try:
        vault = Path(sb.vault)
        files = sorted(vault.rglob("*.md"))
        h = hashlib.sha256()
        for f in files:
            h.update(str(f.relative_to(vault)).encode())
            h.update(f.read_bytes())
        out["vault_md_files"] = len(files)
        out["vault_md_sha256"] = h.hexdigest()[:16]
    except (OSError, AttributeError) as e:
        out["vault_unreadable"] = f"{type(e).__name__}: {e}"
    return out


def _fingerprint() -> dict:
    """What must NOT move while probes run with edits accepted.

    The probes write, by design — so the guard is not "nothing was written", it is "nothing outside
    the sandbox was". A real vault's HEAD and dirty-path count, and the hash of the machine owner's
    own memory file, are the three things a stray write would disturb first."""
    import hashlib, os
    out = {}
    home = Path(os.path.expanduser("~"))
    for name, path in (("vault", home / "Vault"),):
        if (path / ".git").is_dir():
            for key, args in (("head", ["rev-parse", "HEAD"]),
                              ("dirty", ["status", "--porcelain"])):
                r = subprocess.run(["git", "-C", str(path), *args], capture_output=True,
                                   text=True, stdin=subprocess.DEVNULL)
                out[f"{name}_{key}"] = (len(r.stdout.splitlines()) if key == "dirty"
                                        else r.stdout.strip())
    um = home / ".claude" / "CLAUDE.md"
    if um.is_file():
        out["user_memory"] = hashlib.sha256(um.read_bytes()).hexdigest()
    return out


def run(payload_dir: Path, out_dir: Path, days: list[int] | None = None,
        dry_run: bool = False, sandbox_root: Path | None = None,
        control: bool = False, region: str | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, unchecked = [], []
    before = _fingerprint()
    sandbox_before = sandbox_after = None
    sb = None
    if sandbox_root is None:
        cwd, env = out_dir, dict(__import__("os").environ)
        unchecked.append("no --sandbox-root: probes ran with no vault to write to, so any "
                         "permission question they ask is the harness's, not the package's")
    else:
        import os as _os
        sb = _sandbox(Path(sandbox_root), region)
        # ★ The sandbox's OWN env redirects HOME, and a redirected HOME is not authenticated — the
        # probe that opened this row measured exactly that (`Not logged in · Please run /login`).
        # So the GEDAECHTNIS_* seams come from the sandbox and HOME stays real. Nothing leaks in
        # from it: `--setting-sources project` was measured NOT to load the owner's own memory, and
        # the blast-radius fingerprint above is what proves nothing leaked OUT.
        env = dict(_os.environ)
        env.update({k: v for k, v in sb.env.items() if k.startswith("GEDAECHTNIS_")})
        cwd = sb.repo
        sandbox_before = sandbox_fingerprint(sb)
        if not cwd.is_dir():
            return {"generated": time.strftime("%Y-%m-%d"), "rows": [], "total_questions": 0,
                    "unchecked": [f"sandbox repo {cwd} does not exist"], "replies_in": str(out_dir),
                    "criterion_1": {"status": "UNCHECKED",
                                    "detail": f"REFUSED before spending: no sandbox repo at {cwd}. "
                                              "Ten failed sessions would have cost the same as ten "
                                              "real ones and measured nothing."}}
    for day in sorted(days or TASKS):
        payload_file = payload_dir / f"boot-day-{day:03d}.txt"
        if not payload_file.is_file():
            unchecked.append(f"day {day}: no payload at {payload_file.name}")
            continue
        try:
            payload = payload_file.read_text(encoding="utf-8")
        except OSError as exc:
            unchecked.append(f"day {day}: payload unreadable ({exc.__class__.__name__})")
            continue
        stripped = False
        if control:
            payload, stripped = strip_rules(payload)
            if not stripped:
                unchecked.append(f"day {day}: the rules block was not found in the payload, so "
                                 f"the control arm would have run the SAME input as the real arm")
                continue
        prompt = prompt_for(payload, TASKS[day])
        if dry_run:
            rows.append({"day": day, "prompt_bytes": len(prompt.encode()), "asked": None})
            continue
        t0 = time.time()
        try:
            rc, out, err = ask(prompt, cwd, env)
        except subprocess.TimeoutExpired:
            unchecked.append(f"day {day}: session timed out")
            continue
        if rc != 0 or not out:
            unchecked.append(f"day {day}: session failed (rc={rc}) {err[:120]}")
            continue
        (out_dir / f"reply-day-{day:03d}.md").write_text(out, encoding="utf-8")
        asked = questions_in(out)
        rows.append({"day": day, "arm": "control" if control else "package",
                     "seconds": round(time.time() - t0, 1),
                     "payload_bytes": len(payload.encode()),
                     "reply_bytes": len(out.encode()),
                     "asked": asked, "n_asked": len(asked)})

    total = sum(r["n_asked"] for r in rows if r.get("asked") is not None)
    measured = [r for r in rows if r.get("asked") is not None]
    if not measured:
        verdict = {"status": "UNCHECKED", "detail": "no session produced a reply"}
    elif unchecked:
        # A count over a partial sample cannot CLEAR the criterion — the sessions that did not run
        # are exactly where an extra question could be hiding. It can still fail it.
        verdict = ({"status": "FAIL", "detail": f"{total} question(s) asked across "
                    f"{len(measured)} session(s), over the limit of 3"} if total > 3 else
                   {"status": "UNCHECKED", "detail": f"{total} question(s) over {len(measured)} of "
                    f"{len(rows) + len(unchecked)} sampled session(s); "
                    f"{len(unchecked)} not measured"})
    else:
        verdict = {"status": "PASS" if total <= 3 else "FAIL",
                   "detail": f"{total} question(s) asked across {len(measured)} sampled session(s)"}
    after = _fingerprint()
    if before != after:
        moved = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        return {"generated": time.strftime("%Y-%m-%d"),
                "criterion_1": {"status": "UNCHECKED",
                                "detail": f"REFUSED to report: something outside the sandbox moved "
                                          f"while the probes ran ({', '.join(moved)}). The result "
                                          f"would be about this machine, not the package."},
                "blast_radius": {"before": before, "after": after}, "rows": rows,
                "unchecked": unchecked, "total_questions": total, "replies_in": str(out_dir)}
    if sb is not None:
        sandbox_after = sandbox_fingerprint(sb)
    return {"generated": time.strftime("%Y-%m-%d"), "model": MODEL, "effort": EFFORT,
            "blast_radius_unchanged": True,
            "arm": "control" if control else "package",
            # The precondition of any cross-arm subtraction: both arms must have read the SAME
            # vault. Recorded rather than assumed — see `sandbox_fingerprint`.
            "sandbox_vault_before": sandbox_before, "sandbox_vault_after": sandbox_after,
            "sampled_days": sorted(days or TASKS), "rows": rows,
            "unchecked": unchecked, "total_questions": total, "criterion_1": verdict,
            "replies_in": str(out_dir)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Part B of the dad test: questions put to the user.")
    ap.add_argument("--payload-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--day", type=int, action="append", help="repeatable; default all sampled days")
    ap.add_argument("--dry-run", action="store_true", help="print the prompts, call nothing")
    ap.add_argument("--region", default=None,
                    help="the fixture region's folder name under --sandbox-root (default: the one "
                         "directory there that is not home/vault/state, else `project`)")
    ap.add_argument("--sandbox-root", help="Part A's `sandbox_root`; without it the probes cannot "
                                           "write and the run is UNCHECKED by construction")
    ap.add_argument("--control", action="store_true",
                    help="the CONTROL ARM: identical run with the package's operating rules "
                         "stripped from the payload. A question asked in BOTH arms is the "
                         "model's, not the package's, and is subtracted.")
    ap.add_argument("--json")
    args = ap.parse_args(argv)
    res = run(Path(args.payload_dir), Path(args.out_dir), args.day, args.dry_run,
              Path(args.sandbox_root) if args.sandbox_root else None, control=args.control,
              region=args.region)
    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
    v = res["criterion_1"]
    for r in res["rows"]:
        if r.get("asked") is not None:
            print(f"  day {r['day']:>3}: {r['n_asked']} question(s)"
                  + (f" — {r['asked'][0][:70]}" if r["asked"] else ""))
    for u in res["unchecked"]:
        print(f"  UNCHECKED — {u}")
    print(f"\n[{v['status']:>9}] 1_questions_to_user: {v['detail']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
