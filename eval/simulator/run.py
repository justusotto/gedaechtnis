#!/usr/bin/env python3
"""simulator/run.py — grow a real vault through the plugin's own hooks and watch what happens.

    python3 run.py --horizon 90                       # every load x every curve, to day 90
    python3 run.py --load medium --curve linear --json out.json
    python3 run.py --ab 20000,25000,40000 --seeds 3   # the boot-budget A/B

**What this is, and what the earlier one was not.** `complete1_simulator.py` (the arc's seed,
2026-09-11) was an ARITHMETIC MODEL: bytes per day added to a number. It could not see a hook, a
role file, a wikilink or a recall answer, so every claim it made about what a user would
experience was a claim about a spreadsheet. This harness installs the plugin into a sandbox HOME
with `init.py`, writes real entries into real role files day by day, and reads its boot number out
of the plugin's own measurement code — the same `session_start.boot_chain` a live session runs.
Nothing here re-derives a number the plugin already computes.

**The four things it reports, per day.**

  boot bytes        `hooks/session_start.boot_chain()`, the @-import closure of the user-level
                    CLAUDE.md and the repo's — imported, not reimplemented. At every snapshot day
                    the REAL hook is additionally run as a subprocess and its `session-start.json`
                    `boot_bytes` must equal the in-process number, or the run raises. That
                    equality is the anchor: without it this is a library test, not a hook test.
  recall            TWO numbers, because they answer different questions.
                      boot_recall — is the answer inside the @-import chain? i.e. what the model
                        knows with no tool call at all. This is what a boot budget BUYS.
                      tool_recall — `eval/recall_bench.score_question` (the real `recall.py`,
                        unmodified) hit-at-limit over the whole vault, archives included. This is
                        what the model can still find after compaction moved the text away.
                    A budget that lowers boot_recall without lowering tool_recall has cost the
                    user a tool call, not an answer. Reporting one number would hide exactly that.
  cleanup           compactions actually performed (size-triggered, deterministic — see below) and
                    role files over their soft limit at the snapshot, i.e. what a cleanup pass
                    would be handed.
  wikilink health   share of `[[Stem#Heading]]` links whose target heading still exists in that
                    file. Entries link to each other, and compaction MOVES headings to a
                    `-archive` sibling, so this number measures the archive seam, which is
                    precisely what row R3 has to build. See "What this harness deliberately does
                    NOT do" below before reading a low number as a bug in the simulator.

**Growth rates** (bytes/day), carried from `complete1_simulator.py` with their provenance intact:

  low         one quiet repo, 3 sessions/week: 2,000 B/session across role files, 500 B/session in
              the boot file. The PER-SESSION rates are MEASURED (three vault regions, 2026-09-11);
              the cadence is assumed.
  medium      MEASURED, on the busiest region of the vault this plugin was developed against,
              day 7->30 of that project: 144,764 B / 23 d = 6,294 B/day across role files;
              Position.md 671 B/commit x 3.2 commits/day = 2,147 B/day in the boot file. It is one
              project's rate, not a population estimate — it is here because a measured rate from
              one real vault beats an invented one, not because it is typical.
  high        PROJECTED, 4x medium.       extra-high  PROJECTED, 12x medium ("fast and wild").
  none        zero growth. The NEGATIVE CONTROL arm: a harness that only ever sees a vault cross
              its budget cannot tell you whether crossing it is what triggered anything.

**Curves.** linear = constant rate; exponential = rate doubles every 60 days; bursty = alternating
weeks at 5x and 0.2x (mean 2.6x).

**Compaction is size-triggered and deterministic**, per the plan's finding: at Stop, while the boot
file is over budget, the OLDEST entry moves to the `-archive` sibling under a byte-identical
heading. No question is asked; nothing is deleted; the text stays in the vault and stays findable
by `recall.py`, which searches archives by design. A calendar-gated pass (`--cadence 14`) is
offered as the CONTROL that shows why the plan does not use one.

**What this harness deliberately does NOT do**, so no number here is read as more than it is:

  - It does not rewrite a wikilink when compaction moves its target. The naive rolling window is
    simulated exactly as specified, and the resulting broken-link curve is the EVIDENCE handed to
    R3 (archive seams), not a defect to paper over here.
  - It does not model a user reading, editing or reorganising anything. Growth is append-only.
  - It writes no prose a model wrote. Entries are generated from a seeded RNG, so recall is
    measured against synthetic text whose vocabulary is uniform — an easier corpus than a real
    vault. Treat tool_recall as an UPPER bound: on a real vault the recall bench scores well
    below it (42 of 60 questions, 2026-09-10).
  - It never touches the real vault or the real state directory: every path comes from a temp
    sandbox through the plugin's own env seam (GEDAECHTNIS_VAULT / _STATE_DIR / _CONFIG / HOME).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN))
sys.path.insert(0, str(PLUGIN / "hooks"))
sys.path.insert(0, str(PLUGIN / "eval"))

import session_start  # noqa: E402  the plugin's own boot measurement; never reimplemented
from recall_bench import run as bench  # noqa: E402  the plugin's own recall scorer

SNAPSHOTS = (30, 90, 180, 365)
CURVES = ("linear", "exponential", "bursty")

# (region B/day, boot-file B/day, provenance) — see the module docstring.
LOADS = {
    "none": (0.0, 0.0, "control: no growth"),
    "low": (2000 * 3 / 7, 500 * 3 / 7, "projected from MEASURED per-session rates"),
    "medium": (6294.0, 2147.0, "MEASURED (one active project, day 7->30)"),
    "high": (6294.0 * 4, 2147.0 * 4, "projected, 4x medium"),
    "extra-high": (6294.0 * 12, 2147.0 * 12, "projected, 12x medium"),
}

# Role files an entry can land in. The boot file (the repo CLAUDE.md's only @-import) is Position.
BOOT_STEM = "Position"
OTHER_STEMS = ("Canon", "Patterns", "Errata", "Aporia")

# Role soft limits in LINES, the vault's own table. Carried here as the simulator's input, not as
# the package's authority: row R1 moves this table to rules/limits.json and everything reads it
# from there. Duplicated in exactly one place for exactly one row, and this comment is the note.
SOFT_LIMITS = {"Map": 100, "Position": 250, "Canon": 800, "Patterns": 400,
               "Errata": 400, "Aporia": 200}

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)#([^\]|]+?)(?:\|[^\]]*)?\]\]")
HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)

# Vocabulary for generated entries. Small on purpose: a question has to DISCRIMINATE between
# entries for recall to mean anything, so the discriminating token is the unique id, and these
# words are the noise around it.
SUBJECTS = ["deploy", "cache", "index", "queue", "parser", "schema", "worker", "token",
            "session", "retry", "upload", "digest", "socket", "migration", "rollout", "quota"]
ASPECTS = ["timeout", "identity", "threshold", "encoding", "ordering", "fallback", "budget",
           "checksum", "backoff", "partition", "cadence", "ceiling"]
FILLER = ("The change landed after the earlier approach was tried and left the state it was "
          "supposed to protect in a form the next step could not read. Keeping the value here "
          "means the next session does not have to re-derive it from the code.")


def curve_factor(kind: str, day: int) -> float:
    if kind == "linear":
        return 1.0
    if kind == "exponential":
        return 2 ** (day / 60)
    if kind == "bursty":
        return 5.0 if (day // 7) % 2 == 0 else 0.2
    raise ValueError(f"unknown curve {kind!r}: expected one of {', '.join(CURVES)}")


class Sandbox:
    """A throwaway HOME + repo + vault, installed by the plugin's own `init.py`.

    Every path the plugin could reach is redirected through its documented env seam, and the
    sandbox root is a fresh temp directory per run: no test, and no run of this harness, can read
    or write the machine's real vault or state directory: a test suite that writes the
    application's REAL sidecar makes its own verdict depend on the machine's state.
    """

    def __init__(self, root: Path, region: str = "project"):
        self.root = root
        self.home = root / "home"
        self.repo = root / region
        self.vault = root / "vault"
        self.state = root / "state"
        self.region = region

    @property
    def env(self) -> dict:
        e = dict(os.environ)
        e.update({
            "HOME": str(self.home),
            "GEDAECHTNIS_CONFIG": str(self.home / ".claude" / "gedaechtnis" / "config.json"),
            "GEDAECHTNIS_VAULT": str(self.vault),
            "GEDAECHTNIS_STATE_DIR": str(self.state),
            "GEDAECHTNIS_USER_MEMORY": str(self.home / ".claude" / "CLAUDE.md"),
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        return e

    def install(self) -> None:
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)
        (self.home / ".claude" / "CLAUDE.md").write_text(
            "# User memory\n\nNothing here yet.\n", encoding="utf-8")
        self.repo.mkdir(parents=True, exist_ok=True)
        run(["git", "init", "-q", "."], cwd=self.repo)
        # Deliberately not an address. `tools/publish_check.py` refuses ANY email-shaped string in
        # the tree, and it is right to: the rule that keeps a maintainer's address out of a public
        # repo cannot carry an exception for addresses that happen to be fake. git takes this.
        run(["git", "config", "user.email", "sim"], cwd=self.repo)
        run(["git", "config", "user.name", "sim"], cwd=self.repo)
        (self.repo / "README.md").write_text("sandbox\n", encoding="utf-8")
        run(["git", "add", "README.md"], cwd=self.repo)
        run(["git", "commit", "-qm", "sandbox"], cwd=self.repo)
        rc, out, err = run([sys.executable, "-B", str(PLUGIN / "init.py"), "--yes",
                            "--repo", str(self.repo), "--no-outage-check"],
                           cwd=PLUGIN, env=self.env)
        if rc != 0:
            raise RuntimeError(f"init.py failed (rc={rc}): {err or out}")

    # -- the two paths the harness measures ---------------------------------
    @property
    def boot_entrypoints(self) -> list:
        return [self.home / ".claude" / "CLAUDE.md", self.repo / "CLAUDE.md"]

    def role(self, stem: str) -> Path:
        return self.vault / self.region / f"{stem}.md"

    def boot_bytes(self) -> int:
        """The plugin's own number. `boot_chain` is imported from hooks/session_start.py."""
        total, _n = session_start.boot_chain(self.boot_entrypoints)
        return total

    def hook_boot_bytes(self, day: int) -> int:
        """The REAL hook, as a subprocess, reading its answer back out of the state file it
        writes. Run at snapshot days only (it costs a process and two git calls); its number is
        the anchor the in-process one is checked against."""
        payload = json.dumps({"session_id": f"sim-day-{day}", "cwd": str(self.repo),
                              "source": "startup"})
        rc, out, err = run([sys.executable, "-B", str(PLUGIN / "hooks" / "session_start.py")],
                           cwd=PLUGIN, env=self.env, stdin=payload)
        if rc != 0:
            raise RuntimeError(f"session_start.py failed (rc={rc}): {err or out}")
        data = json.loads((self.state / "session-start.json").read_text(encoding="utf-8"))
        return int(data["boot_bytes"])


def run(args, cwd=None, env=None, stdin=None, timeout=120):
    p = subprocess.run([str(a) for a in args], cwd=str(cwd) if cwd else None, env=env,
                       input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


class Author:
    """Writes entries into the vault the way a session would: an append under a `##` heading, with
    a wikilink to an earlier entry. Deterministic given a seed, so a re-run reproduces a run
    byte-for-byte and the A/B's arms differ only in the knob under test."""

    def __init__(self, sb: Sandbox, seed: int):
        self.sb = sb
        self.rng = random.Random(seed)
        self.n = 0
        self.day = 0                      # set by the caller before each day's writes
        self.questions: list[dict] = []   # the held-out set, grown as facts are written
        self.headings: list[tuple] = []   # (stem, heading) in creation order, for linking

    def entry(self, stem: str, target_bytes: int) -> int:
        """Append one entry of about `target_bytes` bytes. Returns the bytes written."""
        self.n += 1
        subject = self.rng.choice(SUBJECTS)
        aspect = self.rng.choice(ASPECTS)
        token = f"K{self.n:05d}"
        heading = f"The {aspect} for the {subject} is {token}"
        link = ""
        if self.headings:
            lst, lh = self.rng.choice(self.headings[-40:])
            link = f" See [[{lst}#{lh}]]."
        body = [f"**Decided:** day {self.n}. The {aspect} for the {subject} is `{token}`.{link}"]
        while sum(len(s) for s in body) < max(0, target_bytes - len(heading) - 8):
            body.append(FILLER)
        text = f"\n## {heading}\n\n" + "\n\n".join(body) + "\n"
        path = self.sb.role(stem)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)
        self.headings.append((stem, heading))
        rel = str(path.relative_to(self.sb.vault))
        self.questions.append({
            "question": f"what is the {aspect} for the {subject} {token}",
            "expected": f"{rel}#{heading}",
            "region": self.sb.region,
            "answer_token": token,
            "written_day": self.day,
        })
        return len(text.encode("utf-8"))


ENTRY_B = 700   # mean bytes per generated entry; growth is converted to a whole number of these

# The `recent` question population is "written in the last N days". 30 is declared here, before
# the A/B runs, rather than chosen after seeing which value separates the arms.
RECENT_WINDOW_DAYS = 30

# A ceiling on what one simulated day may write, in bytes across all role files. It exists because
# extra-high x exponential asks for ~5 MB on day 365 — about 1.8 GB of file appends over the run —
# which is not a scenario a file-level harness can execute faithfully in any useful time. A day
# that hits the cap is written AT the cap and counted, and the cell is reported SHAPE-LIMITED
# rather than dropped: the arithmetic model (`complete1_simulator.py`) still projects those cells
# unbounded, and a harness that silently truncated them would report a number for a run it did not
# do. Raise it with --max-daily-bytes when you have the time to spend.
MAX_DAILY_BYTES = 2_000_000


def compact(sb: Sandbox, budget: int, floor_share: float = 0.4) -> int:
    """The rolling window, as the plan specifies it: while the boot chain is over `budget`, move
    the OLDEST entry out of the boot file into its `-archive` sibling under a byte-identical
    heading. Deterministic, asks nothing, deletes nothing. Returns entries moved.

    It stops at `floor_share` of the budget (what our own graduations achieved, 0.1-0.6) rather
    than emptying the file: a boot file compacted to nothing is a vault with no state."""
    boot = sb.boot_bytes()
    if boot <= budget:
        return 0
    live = sb.role(BOOT_STEM)
    arch = live.with_name(f"{BOOT_STEM}-archive.md")
    text = live.read_text(encoding="utf-8")
    parts = text.split("\n## ")
    head, entries_ = parts[0], parts[1:]
    if not entries_:
        return 0                        # a head with no entries left: nothing to move
    # ONE pass. The loop this replaces re-read and re-wrote the whole boot file per entry moved,
    # which is O(n^2) in the entry count and made the heavy cells unrunnable rather than slow.
    # The boot chain's size changes here by exactly the bytes removed from this one file, so the
    # projection below is arithmetic on a measured number, not a second estimate of it.
    target = budget * floor_share
    moved = 0
    removed = 0
    while moved < len(entries_) and boot - removed > target:
        removed += len(("\n## " + entries_[moved]).encode("utf-8"))
        moved += 1
    if not moved:
        return 0
    live.write_text(head + ("\n## " + "\n## ".join(entries_[moved:]) if entries_[moved:] else "\n"),
                    encoding="utf-8")
    if not arch.exists():
        arch.write_text(f"# {sb.region} — Position (archive)\n\n"
                        "Narrative the boot file compacted away. Headings are byte-identical "
                        "to the ones they had in the live file.\n", encoding="utf-8")
    with open(arch, "a", encoding="utf-8") as fh:
        fh.write("".join("\n## " + e for e in entries_[:moved]))
    return moved


def wikilink_health(sb: Sandbox) -> tuple[int, int]:
    """(resolved, total) over every `[[Stem#Heading]]` in the region's files. A link resolves when
    that file exists AND carries that exact heading. Archive siblings count as their own file, so
    a link to a moved heading is BROKEN until something rewrites it — see the docstring."""
    region = sb.vault / sb.region
    headings: dict = {}
    for path in sorted(region.glob("*.md")):
        headings[path.stem] = set(HEADING_RE.findall(path.read_text(encoding="utf-8")))
    resolved = total = 0
    for path in sorted(region.glob("*.md")):
        for stem, heading in WIKILINK_RE.findall(path.read_text(encoding="utf-8")):
            total += 1
            if heading.strip() in headings.get(stem.strip(), ()):
                resolved += 1
    return resolved, total


def unsearchable(sb: Sandbox) -> list:
    """Role files `recall.py` will not look inside, because they are over its own
    `MAX_FILE_BYTES`. Measured against the plugin's constant, imported — not a number repeated
    here.

    This exists because the failure is SILENT: `md_files()` skips an oversize file, `search()`
    returns no hit, and the caller sees "the vault does not know that" — which is indistinguishable
    from the answer never having been written. A harness that reported only the resulting zero
    would be reporting a mystery. (Global kernel: "a wrong state that produces no failure signal is
    never caught".)"""
    import recall
    out = []
    for p in sorted((sb.vault / sb.region).glob("*.md")):
        size = p.stat().st_size
        if size > recall.MAX_FILE_BYTES:
            out.append({"file": p.name, "bytes": size, "limit": recall.MAX_FILE_BYTES})
    return out


def oversize(sb: Sandbox) -> list:
    """Role files past their soft limit — what a cleanup pass would be handed."""
    out = []
    for stem, limit in SOFT_LIMITS.items():
        p = sb.role(stem)
        if not p.is_file():
            continue
        n = len(p.read_text(encoding="utf-8").splitlines())
        if n > limit:
            out.append({"stem": stem, "lines": n, "limit": limit})
    return out


def boot_recall(sb: Sandbox, questions: list):
    """Share of held-out answers whose token is inside the @-import chain — what the model knows
    with NO tool call. This is the thing a bigger boot budget actually buys.

    `None` when there are no questions to ask (the `none` control writes nothing, so there is
    nothing to recall). A vault with no facts has UNMEASURED recall, not zero recall."""
    if not questions:
        return None
    blob = ""
    for entry in sb.boot_entrypoints:
        blob += _chain_text(entry)
    hit = sum(1 for q in questions if q["answer_token"] in blob)
    return hit / len(questions)


def _chain_text(entry: Path, seen=None) -> str:
    """The text of the @-import closure, using the hook's own import grammar."""
    seen = seen if seen is not None else set()
    p = session_start._readable(entry)
    if p is None or p in seen:
        return ""
    seen.add(p)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    out = text
    for line in text.splitlines():
        m = session_start.IMPORT_LINE_RE.match(line.strip())
        if m:
            out += _chain_text(Path(os.path.expanduser(m.group(1))), seen)
    return out


def located(sb: Sandbox, q: dict) -> dict:
    """`q` with its `expected` path pointing at wherever the entry actually IS right now.

    Compaction moves an entry from `<Stem>.md` to `<Stem>-archive.md` under a byte-identical
    heading, and `bench.score_question` matches on PATH and heading. Scoring an archived answer
    against the path it was written to therefore counts a perfectly findable entry as a miss —
    which would have made "tool recall falls after compaction" the harness's headline finding,
    when the real cause was the harness asking the wrong question. Caught by
    `test_boot_recall_counts_only_what_is_in_the_import_chain` on 2026-09-14.

    The expected location is read off disk rather than assumed: the live file first (it is the
    authority when both carry the heading), then the archive sibling. A heading in neither is left
    pointing at the live file, so it scores as the genuine miss it is."""
    rel, _, heading = q["expected"].partition("#")
    live = sb.vault / rel
    arch = live.with_name(f"{live.stem}-archive.md")
    for path in (live, arch):
        try:
            if heading in HEADING_RE.findall(path.read_text(encoding="utf-8")):
                return {**q, "expected": f"{path.relative_to(sb.vault)}#{heading}"}
        except OSError:
            continue
    return q


def tool_recall(sb: Sandbox, questions: list, limit: int) -> tuple:
    """(recall_at_limit, mean bytes-to-answer) from the plugin's own recall scorer, or
    (None, None) when there is nothing to ask — see `boot_recall`."""
    if not questions:
        return None, None
    rows = [bench.score_question(sb.vault, located(sb, q), limit=limit, max_bytes=6000)
            for q in questions]
    hit = sum(1 for r in rows if r["recall_at_limit"])
    return hit / len(rows), statistics.mean(r["bytes_to_answer"] for r in rows)


def simulate(load: str, kind: str, horizon: int, budget: int, seed: int = 7,
             cadence: int = 0, n_questions: int = 12, limit: int = 3,
             compaction: bool = True, keep: Path | None = None, verbose: bool = False,
             max_daily_bytes: int = MAX_DAILY_BYTES) -> dict:
    """One cell: grow a sandbox vault for `horizon` days and report at every snapshot day.

    A day whose curve asks for more than `max_daily_bytes` is written AT THE CAP and counted in
    `capped_days`; a cell with any capped day carries `shape_limited: true` and must be reported
    as SHAPE-LIMITED, never as a faithful run of that scenario. See MAX_DAILY_BYTES."""
    rr, rb, provenance = LOADS[load]
    root = Path(tempfile.mkdtemp(prefix="ged-sim-"))
    try:
        sb = Sandbox(root)
        sb.install()
        author = Author(sb, seed)
        carry_boot = carry_other = 0.0
        compactions = 0
        capped_days = 0
        snaps: dict = {}
        for day in range(1, horizon + 1):
            author.day = day
            f = curve_factor(kind, day)
            want = (rb + rr) * f
            if want > max_daily_bytes:
                f *= max_daily_bytes / want
                capped_days += 1
            carry_boot += rb * f
            carry_other += rr * f
            while carry_boot >= ENTRY_B:
                carry_boot -= author.entry(BOOT_STEM, ENTRY_B)
            while carry_other >= ENTRY_B:
                carry_other -= author.entry(author.rng.choice(OTHER_STEMS), ENTRY_B)
            if compaction and (cadence == 0 or day % cadence == 0):
                compactions += compact(sb, budget)
            if day in SNAPSHOTS or day == horizon:
                snaps[day] = snapshot(sb, author, day, n_questions, limit, seed)
                if verbose:
                    s = snaps[day]
                    print(f"  day {day:>3}  boot {s['boot_bytes']:>8,} B "
                          f"({s['boot_tokens']:>6,} tok)  recent: boot"
                          f"{fmt(s['boot_recall_recent'], 5)} tool{fmt(s['tool_recall_recent'], 5)}"
                          f"  all: boot{fmt(s['boot_recall'], 5)} tool{fmt(s['tool_recall'], 5)}"
                          f"  links{fmt(s['wikilink_health'], 5)}", flush=True)
        result = {
            "load": load, "curve": kind, "horizon": horizon, "budget": budget, "seed": seed,
            "cadence": cadence, "compaction": compaction, "provenance": provenance,
            "compactions": compactions, "entries_written": author.n,
            "capped_days": capped_days, "shape_limited": capped_days > 0,
            "max_daily_bytes": max_daily_bytes,
            "questions_total": len(author.questions), "snapshots": snaps,
        }
        if keep:
            shutil.copytree(sb.vault, keep, dirs_exist_ok=True)
        return result
    finally:
        shutil.rmtree(root, ignore_errors=True)


def fmt(v, width: int, nd: int = 2) -> str:
    """A number, or `UNMEASURED` right-aligned — never 0.00 standing in for "nothing to measure"."""
    return f"{'—':>{width}}" if v is None else f"{v:>{width}.{nd}f}"


def _r(v, nd: int = 4):
    """round(), but `None` stays None: an UNMEASURED number must never print as 0.00."""
    return None if v is None else round(v, nd)


def snapshot(sb: Sandbox, author: Author, day: int, n_questions: int, limit: int,
             seed: int) -> dict:
    """One snapshot day. The hook subprocess runs HERE, and its number must equal the in-process
    one — the check that makes this a hook harness rather than a library exercise."""
    in_proc = sb.boot_bytes()
    hooked = sb.hook_boot_bytes(day)
    if in_proc != hooked:
        raise AssertionError(
            f"day {day}: boot bytes disagree — boot_chain() says {in_proc}, the hook's own "
            f"session-start.json says {hooked}. One of the two is not measuring the boot chain.")
    rng = random.Random(seed * 1000 + day)
    qs = author.questions
    uniform = rng.sample(qs, min(n_questions, len(qs))) if qs else []
    recent_pool = [q for q in qs if q["written_day"] > day - RECENT_WINDOW_DAYS] or qs
    recent = rng.sample(recent_pool, min(n_questions, len(recent_pool))) if recent_pool else []
    u_rec, u_bytes = tool_recall(sb, uniform, limit)
    r_rec, _ = tool_recall(sb, recent, limit)
    resolved, total = wikilink_health(sb)
    return {
        "day": day,
        "boot_bytes": hooked,
        "boot_files": session_start.boot_chain(sb.boot_entrypoints)[1],
        "boot_tokens": round(hooked / 4),
        "vault_bytes": sum(p.stat().st_size for p in sb.vault.rglob("*.md")),
        # POPULATION `all`: a question about anything the vault has ever recorded.
        "boot_recall": _r(boot_recall(sb, uniform)),
        "tool_recall": _r(u_rec),
        "bytes_to_answer": _r(u_bytes, 1),
        "n_questions_scored": len(uniform),
        # POPULATION `recent`: a question about the last RECENT_WINDOW_DAYS days of work. The
        # population a boot budget exists to serve — a boot file is a recent window, so measuring
        # it against a uniform sample of everything ever written reports a number that is fixed by
        # the vault's SIZE, not by the budget. Which population a figure came from is named
        # wherever it is reported; neither one is "the" recall.
        "boot_recall_recent": _r(boot_recall(sb, recent)),
        "tool_recall_recent": _r(r_rec),
        "n_questions_recent": len(recent),
        "wikilink_health": round(resolved / total, 4) if total else 1.0,
        "wikilinks": [resolved, total],
        "oversize": oversize(sb),
        "unsearchable": unsearchable(sb),
    }


# ------------------------------------------------------------------ the A/B ----

def ab(budgets: list, loads: list, curves: list, horizon: int, seeds: int,
       n_questions: int, limit: int, verbose: bool = False,
       max_daily_bytes: int = MAX_DAILY_BYTES) -> dict:
    """The boot-budget A/B. Each budget is run over the same cells with the same seeds, so the
    ONLY thing that differs between arms is the budget.

    THE DECISION RULE, pre-registered (it is in this docstring, and in the row report, before the
    numbers exist):

      metric   answers per 1,000 boot tokens, on the `recent` population — answered = the share of
               held-out questions the model can answer either FROM THE BOOT FILE (no tool call) or
               through one `recall.py` call. Cost and performance in one number, which is what the
               owner asked for ("a/b test first and choose the winner for cost and performance").
      guard    an arm is disqualified if its tool recall falls more than the noise floor below the
               best arm's. Cheapness must not be bought with an answer the user can no longer get
               at all.
      floor    the noise floor is MEASURED, not assumed: `seeds` independent seeds per cell give
               each metric a spread, and a difference between two budgets smaller than that spread
               is reported as UNREADABLE rather than as a winner. (Global kernel: "a
               pre-registered bar that is smaller than its own metric's NOISE FLOOR is not a bar".)
      tie      among arms whose answered recall ties within the floor, the CHEAPER budget wins:
               tokens are paid every session, and a tie means nothing was bought with them.
    """
    runs = []
    for budget in budgets:
        for load in loads:
            for kind in curves:
                for s in range(seeds):
                    seed = 7 + s
                    if verbose:
                        print(f"[ab] budget {budget:,} load {load} curve {kind} seed {seed}",
                              flush=True)
                    runs.append(simulate(load, kind, horizon, budget, seed=seed,
                                         n_questions=n_questions, limit=limit, verbose=verbose,
                                         max_daily_bytes=max_daily_bytes))
    out = aggregate(runs, budgets)
    out.update({"loads": loads, "curves": curves, "horizon": horizon, "seeds": seeds})
    return out


def cell_spreads(runs: list, metric: str) -> dict:
    """{(budget, load, curve): spread across SEEDS} for one metric. The raw material of a floor."""
    cells: dict = {}
    for r in runs:
        final = r["snapshots"][max(r["snapshots"])]
        v = final.get(metric)
        if v is not None:
            cells.setdefault((r["budget"], r["load"], r["curve"]), []).append(v)
    return {k: statistics.pstdev(v) for k, v in cells.items() if len(v) > 1}


def noise_floor(runs: list, metric: str, summary: str = "max") -> float:
    """The metric's noise floor: over CELLS, the spread of that metric across SEEDS.

    ★ This is a repeat of the CONTROL, and nothing else is. The first version took the spread
    across every cell in an ARM — which pools `low`, `medium` and `high` loads and three curves
    together, so it measured the DESIGN FACTORS, not noise. It returned 0.4737 on a 0-to-1 metric,
    i.e. a "floor" that swallows any result the experiment could ever produce, and did so silently
    while still printing a winner. A cell repeated under a different seed, and only that, tells you
    what the instrument's own wobble is. (Global kernel: "measure the floor with a repeat of the
    CONTROL, per metric".)

    ★ MAX, not mean, and the reason is what the number is USED for. This is not a descriptive
    statistic — it is a hard gate ("a difference smaller than this counts as no difference"). A
    gate built from an average is beaten by the noisiest cell roughly half the time by
    construction: a quiet cell sitting beside a noisy one drags the threshold below what the noisy
    cell's own comparisons need. Max is the conservative floor for a threshold; mean is the right
    summary for a report and the wrong one for a gate. Measured on the 2026-09-14 run, the
    difference is not cosmetic — mean 0.0252 against max 0.0636 for boot recall, so the pooled mean
    was under half of what the `low` cells alone required.

    Zero-variance cells are INCLUDED (a cell whose seeds agree is real information, not something
    to discard) — under `max` they simply never set the floor, which is the correct influence for
    them to have. `per_cell_floors` in the result carries the whole distribution, because one
    pooled scalar applied uniformly is exactly the shape of the mistake this function already made
    once."""
    spreads = list(cell_spreads(runs, metric).values())
    if not spreads:
        return 0.0
    agg = max if summary == "max" else statistics.mean
    return round(agg(spreads), 4)


def aggregate(runs: list, budgets: list) -> dict:
    """Arms, floors, ladder and winner from a list of cell results. Pure, so a saved run can be
    re-aggregated without re-simulating (`--reaggregate`) — which is what makes a correction to
    the decision rule cheap enough to actually make."""
    # ORDER-INDEPENDENCE, and it is not housekeeping. `max()` returns the FIRST maximal element,
    # so on an exact tie the winner was decided by the order `budgets` happened to arrive in —
    # `--ab` uses the order the operator typed on the command line, `--reaggregate` uses sorted().
    # Same cells, same code, different winner: demonstrated by the 2026-09-14 re-review. And ties
    # are not exotic here — the degenerate case has near-flat `answered` and recall is quantised
    # over ~12-20 held-out questions, so a 4-decimal ratio collides easily. Sorting the budgets is
    # half the fix; the explicit tie-break key below is the other half.
    budgets = sorted(budgets)
    arms = {}
    for budget in budgets:
        cells = [r for r in runs if r["budget"] == budget]
        finals = [r["snapshots"][max(r["snapshots"])] for r in cells]
        # A cell that wrote nothing (the `none` control) has UNMEASURED recall, not zero. It is a
        # control arm, never an A/B arm: averaging its None in as 0.0 would drag every mean down
        # and the winner would be an artefact of a cell that could not answer a question because
        # there was no question to ask.
        pairs = [(r, f) for r, f in zip(cells, finals) if f["tool_recall_recent"] is not None]
        cells = [r for r, _ in pairs]
        finals = [f for _, f in pairs]
        if not finals:
            raise ValueError(f"budget {budget}: every cell was UNMEASURED (a zero-growth load "
                             "cannot be an A/B arm — run it as a control instead)")
        # What the model can answer AT ALL, on the declared `recent` population: from the boot
        # file for free, or through one recall call.
        answered = [max(f["boot_recall_recent"], f["tool_recall_recent"]) for f in finals]
        toks = [f["boot_tokens"] for f in finals]
        arms[str(budget)] = {
            "budget": budget,
            "n_cells": len(cells),
            "population": "recent",
            "boot_tokens_mean": round(statistics.mean(toks), 1),
            "boot_tokens_sd": round(statistics.pstdev(toks), 1) if len(toks) > 1 else 0.0,
            "boot_recall_mean": round(statistics.mean(f["boot_recall_recent"] for f in finals), 4),
            "tool_recall_mean": round(statistics.mean(f["tool_recall_recent"] for f in finals), 4),
            "boot_recall_all_mean": round(statistics.mean(f["boot_recall"] for f in finals), 4),
            "tool_recall_all_mean": round(statistics.mean(f["tool_recall"] for f in finals), 4),
            "answered_mean": round(statistics.mean(answered), 4),
            "answered_sd": round(statistics.pstdev(answered), 4) if len(answered) > 1 else 0.0,
            "tool_recall_sd": round(
                statistics.pstdev([f["tool_recall_recent"] for f in finals]), 4)
            if len(finals) > 1 else 0.0,
            "wikilink_health_mean": round(
                statistics.mean(f["wikilink_health"] for f in finals), 4),
            "compactions_mean": round(statistics.mean(r["compactions"] for r in cells), 1),
            "shape_limited_cells": sum(1 for r in cells if r["shape_limited"]),
            # answers per 1,000 boot tokens: the cost-and-performance number the owner asked for
            "answers_per_1k_tokens": round(
                statistics.mean(answered) / max(1e-9, statistics.mean(toks) / 1000), 4),
        }
    floor = noise_floor(runs, "boot_recall_recent")
    tool_floor = noise_floor(runs, "tool_recall_recent")
    # The GUARD, applied before the metric: an arm that loses real answers is out, however cheap.
    best_tool = max(a["tool_recall_mean"] for a in arms.values())
    eligible = [a for a in arms.values() if a["tool_recall_mean"] >= best_tool - tool_floor]
    disqualified = [a["budget"] for a in arms.values() if a not in eligible]
    # The tie-break is EXPLICIT, and it implements the rule the docstring already promised ("among
    # tied arms the cheaper budget wins") instead of leaving it to iteration order to get right by
    # accident. `-budget` is the second key, so an exact tie on the metric resolves to the smaller
    # budget deterministically, on any input ordering.
    best = max(eligible, key=lambda a: (a["answers_per_1k_tokens"], -a["budget"]))

    # ── THE MARGINAL LADDER, and why the headline metric alone cannot decide this ──────────
    #
    # `answers_per_1k_tokens` uses answered = max(boot_recall, tool_recall). Where tool recall
    # dominates — which it does at every load above `low`, because the boot file is a recent
    # window over ONE role file while the vault holds everything — `answered` barely moves with
    # the budget, and the metric collapses to 1/tokens. A metric that always prefers the smallest
    # candidate offered is not choosing a budget; it is reporting that its numerator is flat.
    # Saying so is the finding. The ladder below is what remains readable: what the NEXT increment
    # of budget buys in boot recall, and what it costs in tokens, each step compared against the
    # floor measured above. A step whose gain is under the floor bought nothing measurable.
    ordered = sorted(arms.values(), key=lambda a: a["budget"])
    ladder = []
    for lo, hi in zip(ordered, ordered[1:]):
        d_recall = round(hi["boot_recall_mean"] - lo["boot_recall_mean"], 4)
        d_tokens = round(hi["boot_tokens_mean"] - lo["boot_tokens_mean"], 1)
        ladder.append({
            "from": lo["budget"], "to": hi["budget"],
            "boot_recall_gain": d_recall,
            "boot_token_cost": d_tokens,
            "gain_per_1k_tokens": round(d_recall / (d_tokens / 1000), 4) if d_tokens else None,
            "readable": bool(abs(d_recall) > floor),
        })
    metric_degenerate = all(
        abs(a["answered_mean"] - best["answered_mean"]) <= floor for a in eligible)
    ties = [a for a in eligible
            if a is not best and abs(a["answered_mean"] - best["answered_mean"]) <= floor]
    cheaper_ties = sorted(a["budget"] for a in ties
                          if a["boot_tokens_mean"] < best["boot_tokens_mean"])
    return {
        "budgets": budgets, "arms": arms,
        "population": "recent",
        "recent_window_days": RECENT_WINDOW_DAYS,
        "noise_floor_method": ("MAX across cells of the metric's standard deviation across SEEDS "
                               "— a repeat of the control, never the spread across loads and "
                               "curves, which are design factors. Max rather than mean because "
                               "this number is used as a GATE, and an averaged gate is beaten by "
                               "the noisiest cell about half the time"),
        "boot_recall_noise_floor": floor,
        "tool_recall_noise_floor": tool_floor,
        # The whole distribution, not just the scalar: one pooled number applied uniformly to
        # every load is the shape of the mistake this instrument already made once. A per-load
        # reading can differ from the pooled gate in BOTH directions and the ladder says so.
        "per_cell_floors": {f"{b}/{ld}/{cv}": round(v, 4)
                            for (b, ld, cv), v in sorted(cell_spreads(runs,
                                                                     "boot_recall_recent").items(),
                                                         key=lambda kv: str(kv[0]))},
        "disqualified_by_guard": disqualified,
        "winner": best["budget"],
        "winner_rule": ("highest answers per 1,000 boot tokens on the `recent` population, among "
                        "arms whose tool recall is within the noise floor of the best arm's; a "
                        "difference in answered recall smaller than the measured noise floor "
                        "counts as no difference, and among tied arms the cheaper budget wins"),
        "metric_degenerate": metric_degenerate,
        "metric_degenerate_note": (
            "every arm's answered recall is within the noise floor of every other's, so the "
            "headline metric reduces to 1/boot-tokens and will name the smallest budget on the "
            "ballot whatever it is. Read the marginal ladder, not the winner line."
            if metric_degenerate else ""),
        "marginal_ladder": ladder,
        "cheaper_ties": cheaper_ties,
        "runs": runs,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--horizon", type=int, default=90, help="days to simulate (default 90)")
    ap.add_argument("--load", action="append", choices=sorted(LOADS), help="repeatable")
    ap.add_argument("--curve", action="append", choices=CURVES, help="repeatable")
    ap.add_argument("--budget", type=int, default=25000, help="boot budget in bytes")
    ap.add_argument("--cadence", type=int, default=0,
                    help="CONTROL: compact at most every N days (0 = size-triggered, the plan)")
    ap.add_argument("--no-compaction", action="store_true", help="CONTROL: never compact")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--questions", type=int, default=12, help="held-out questions per snapshot")
    ap.add_argument("--limit", type=int, default=3, help="recall@N")
    ap.add_argument("--ab", help="comma-separated budgets; runs the A/B instead of a plain sweep")
    ap.add_argument("--reaggregate", metavar="JSON",
                    help="re-run the A/B's AGGREGATION over the cells saved in a previous run's "
                         "JSON, simulating nothing. For correcting a decision rule without paying "
                         "for the cells again; the cells themselves are never recomputed, so the "
                         "re-aggregated result rests on exactly the same measurements.")
    ap.add_argument("--seeds", type=int, default=2, help="seeds per cell in --ab (noise floor)")
    ap.add_argument("--max-daily-bytes", type=int, default=MAX_DAILY_BYTES,
                    help=f"ceiling on one day's writes; a capped cell is SHAPE-LIMITED "
                         f"(default {MAX_DAILY_BYTES:,})")
    ap.add_argument("--json", help="write the full result here")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    loads = a.load or ["low", "medium", "high", "extra-high"]
    curves = a.curve or list(CURVES)
    t0 = time.time()
    if a.ab or a.reaggregate:
        if a.reaggregate:
            prior = json.loads(Path(a.reaggregate).read_text(encoding="utf-8"))
            runs = prior["runs"]
            # JSON object keys are STRINGS. `aggregate` takes each cell's last snapshot with
            # max(snapshots), and over {"30","90","180","365"} that is the lexicographic max —
            # "90". A re-aggregation would then silently report day 90 as the horizon's result:
            # on the first run of this path, tool recall came back 0.96 instead of 0.51 and the
            # ladder rested on the wrong day, with nothing anywhere saying so. Restore the ints.
            for r in runs:
                r["snapshots"] = {int(k): v for k, v in r["snapshots"].items()}
            budgets = sorted({r["budget"] for r in runs})
            out = aggregate(runs, budgets)
            out["reaggregated_from"] = a.reaggregate
            loads = sorted({r["load"] for r in runs})
            curves = sorted({r["curve"] for r in runs})
            a.horizon = max(r["horizon"] for r in runs)
            a.seeds = len({r["seed"] for r in runs})
            print(f"RE-AGGREGATED from {a.reaggregate} — {len(runs)} saved cells, nothing simulated")
        else:
            budgets = [int(x) for x in a.ab.split(",") if x.strip()]
            out = ab(budgets, loads, curves, a.horizon, a.seeds, a.questions, a.limit,
                     verbose=not a.quiet, max_daily_bytes=a.max_daily_bytes)
        print(f"\nBoot-budget A/B — {a.horizon} d, loads {'/'.join(loads)}, "
              f"curves {'/'.join(curves)}, {a.seeds} seed(s) per cell, "
              f"population `recent` (last {RECENT_WINDOW_DAYS} days)")
        print(f"{'budget':>9}{'boot tok':>10}{'boot rec':>10}{'tool rec':>10}"
              f"{'answered':>10}{'ans/1k tok':>12}{'links':>8}{'compactions':>13}")
        for key in sorted(out["arms"], key=int):
            m = out["arms"][key]
            print(f"{m['budget']:>9,}{m['boot_tokens_mean']:>10,.0f}{m['boot_recall_mean']:>10.2f}"
                  f"{m['tool_recall_mean']:>10.2f}{m['answered_mean']:>10.2f}"
                  f"{m['answers_per_1k_tokens']:>12.3f}{m['wikilink_health_mean']:>8.2f}"
                  f"{m['compactions_mean']:>13.1f}"
                  # The SHAPE-LIMITED marking has to reach the READER of the decision, not only
                  # the JSON. The plain sweep printed it per row and this table did not, so an
                  # arm whose mean included capped, non-faithful cells looked exactly like one
                  # that did not — on the single table this row exists to produce.
                  + (f"   SHAPE-LIMITED: {m['shape_limited_cells']} of {m['n_cells']} cells capped"
                     if m["shape_limited_cells"] else ""))
        print(f"\nnoise floors ({out['noise_floor_method']}):")
        print(f"  boot recall {out['boot_recall_noise_floor']} · "
              f"tool recall {out['tool_recall_noise_floor']}")
        if out["disqualified_by_guard"]:
            print(f"disqualified by the recall guard: "
                  f"{', '.join(f'{b:,}' for b in out['disqualified_by_guard'])}")
        print("\nmarginal ladder — what the NEXT increment of budget buys, and costs:")
        print(f"  {'step':>18}{'boot recall gain':>19}{'boot token cost':>18}"
              f"{'gain/1k tok':>13}   readable?")
        for st in out["marginal_ladder"]:
            g = st["gain_per_1k_tokens"]
            print(f"  {st['from']:>7,} -> {st['to']:>6,}{st['boot_recall_gain']:>19.4f}"
                  f"{st['boot_token_cost']:>18,.0f}"
                  f"{('%.4f' % g) if g is not None else '—':>13}   "
                  + ("yes" if st["readable"] else
                     f"NO — gain under the floor ({out['boot_recall_noise_floor']})"))
        print(f"\nWINNER: {out['winner']:,} B — {out['winner_rule']}")
        if out["metric_degenerate"]:
            print(f"  ★ BUT THE METRIC IS DEGENERATE HERE: {out['metric_degenerate_note']}")
        if out["cheaper_ties"]:
            print(f"  cheaper arms tied within the noise floor: "
                  f"{', '.join(f'{b:,}' for b in out['cheaper_ties'])}")
    else:
        out = {"budget": a.budget, "runs": []}
        print(f"budget {a.budget:,} B · horizon {a.horizon} d · "
              f"{'no compaction' if a.no_compaction else (f'compaction every {a.cadence} d' if a.cadence else 'size-triggered compaction')}")
        for load in loads:
            for kind in curves:
                if not a.quiet:
                    print(f"{load}/{kind}:", flush=True)
                out["runs"].append(simulate(load, kind, a.horizon, a.budget, seed=a.seed,
                                            cadence=a.cadence, n_questions=a.questions,
                                            limit=a.limit, compaction=not a.no_compaction,
                                            verbose=not a.quiet,
                                            max_daily_bytes=a.max_daily_bytes))
        print(f"\npopulation `recent` = questions about the last {RECENT_WINDOW_DAYS} days; "
              f"`all` = anything ever written")
        print(f"\n{'load':12}{'curve':12}{'boot tok':>10}{'boot(rec)':>11}{'tool(rec)':>11}"
              f"{'boot(all)':>11}{'tool(all)':>11}{'links':>8}{'compact':>9}{'oversize':>10}")
        for r in out["runs"]:
            s = r["snapshots"][max(r["snapshots"])]
            print(f"{r['load']:12}{r['curve']:12}{s['boot_tokens']:>10,}"
                  f"{fmt(s['boot_recall_recent'], 11)}{fmt(s['tool_recall_recent'], 11)}"
                  f"{fmt(s['boot_recall'], 11)}{fmt(s['tool_recall'], 11)}"
                  f"{fmt(s['wikilink_health'], 8)}{r['compactions']:>9}{len(s['oversize']):>10}"
                  + ("   SHAPE-LIMITED (%d capped day(s))" % r["capped_days"]
                     if r["shape_limited"] else "")
                  + ("   RECALL-BLIND: " + ", ".join(
                      f"{u['file']} {u['bytes']:,} B > recall's {u['limit']:,}"
                      for u in s["unsearchable"]) if s["unsearchable"] else ""))
    out["wall_seconds"] = round(time.time() - t0, 1)
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
        print(f"json -> {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
