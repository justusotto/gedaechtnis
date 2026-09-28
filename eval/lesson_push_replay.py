#!/usr/bin/env python3
"""lesson_push_replay.py — does the push earn its place? Replayed over REAL writes, held out.

This is the gate `hooks/lesson_push.py` ships behind, and it has now decided TWO designs. It does
not simulate writes: it reads the tool inputs that actually happened, out of the session
transcripts `LESSONYIELD-1` censused, and asks three questions of them.

★ WHAT CHANGED FOR DESIGN 2 (LESSONPUSH-2), and why each gate had to move with it:

  * **The population is bigger.** Design 1 replayed Edit/Write/MultiEdit only and measured that
    this was 16.9% of the corpus's file-producing tool calls. This replay also reads **Bash calls
    that literally produce a file**, through the hook's own `lesson_push.bash_targets` — the same
    detector that ships, not a second one written for the harness.
  * **Gate 1 is no longer a threshold.** A citation match is EXACT: it either names the same thing
    or it does not, so there is no score to put a bar on. What replaces the noise floor is the
    matcher's FALSE-POSITIVE RATE — the identical matched control design 1 used, with the signal
    removed: the write's own region index, scored against a DIFFERENT write's tokens. If that
    fires as often as the real thing, the matcher is not matching, it is agreeing.
  * **Gate 2 should have more to check.** 11 of the 17 RE-OCCURRENCE rows were UNCHECKED in design
    1 because their evidence artifacts were written through Bash and were invisible. The count of
    CHECKABLE rows is therefore itself a result, reported next to the hits.

  Gate 3, the relevance sample, is unchanged and is the reviewer's to judge, not this file's.

  1. **FALSE-POSITIVE RATE** — the matched control above, plus the per-class breakdown of which
     kind of token did the matching. A class that fires on the control as often as on the signal
     is a class that should not be in the index, and the breakdown is how that is seen.

  2. **HELD-OUT REPLAY** — for each RE-OCCURRENCE row in the LESSONYIELD TSV, find the write that
     preceded the re-occurrence (the row's own evidence names the artifact and the date) and ask
     whether the push would have put THAT lesson in front of THAT write. **0 hits is a result, not
     a failure of the harness**: it says the design does not do what it was built for, and the hook
     then ships OFF with the number in its limits prose. A row whose evidence artifact is not in
     this repo's transcripts is UNCHECKED — a third outcome, never silently a miss.

  3. **CONTEXT COST** — lines and bytes the push would have added, per session, beside the measured
     boot floor. A design that fires often and helps nobody costs context in every session.

Usage:

    python3 gedaechtnis/eval/lesson_push_replay.py --json OUT.json --md OUT.md
    python3 gedaechtnis/eval/lesson_push_replay.py --edit-only     # design 1's population
    python3 gedaechtnis/eval/lesson_push_replay.py --bash-audit OUT.md --bash-audit-n 200
"""
from __future__ import annotations
import argparse, json, os, random, re, statistics, sys
from datetime import datetime
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import common                                        # noqa: E402
import config                                        # noqa: E402
import lesson_push                                   # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_YIELD_TSV = (REPO_ROOT / ".orchestration" / "review"
                     / "staletruth-2026-09-19" / "LESSONYIELD-2026-09-20.tsv")
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
BOOT_FLOOR_MEDIAN_TOKENS = 141_965                   # CTXCAP-1, 157 sessions / 30 days


def default_transcripts() -> Path:
    """Where this checkout's session transcripts live, derived rather than spelled out.

    Claude Code names a project's transcript directory after the project's absolute path with every
    separator turned into a dash. Deriving it keeps a private path out of the tree (the plugin's own
    publish check refuses one, and is right to) and makes the tool work in any checkout.

    A WORKTREE is its own project path and has its own -- usually empty -- transcript directory,
    while the sessions being measured ran in the MAIN checkout. So the main worktree, found through
    `git rev-parse --git-common-dir`, is tried second, and the first directory that EXISTS wins.
    Neither existing returns the checkout's own, so the error names the path actually looked at."""
    import subprocess
    roots = [REPO_ROOT]
    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--git-common-dir"],
                             capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=10)
        if out.returncode == 0:
            common_dir = Path(out.stdout.strip())
            if not common_dir.is_absolute():
                common_dir = REPO_ROOT / common_dir
            roots.append(common_dir.resolve().parent)
    except (OSError, subprocess.SubprocessError):
        pass
    base = Path.home() / ".claude" / "projects"
    for r in roots:
        cand = base / str(r).replace("/", "-")
        if cand.is_dir():
            return cand
    return base / str(REPO_ROOT).replace("/", "-")


# ---------------------------------------------------------------- transcripts ----

def first_timestamp(path: Path) -> datetime | None:
    """The first `timestamp` field ANYWHERE in the file, not the first line's.

    LESSONYIELD-1's population rule, reproduced deliberately: many sessions open with an
    untimestamped `custom-title` record, and keying on line 1 drops them. Replaying a different
    population than the census would make every comparison between the two meaningless."""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"timestamp"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                ts = rec.get("timestamp")
                if isinstance(ts, str):
                    try:
                        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    except ValueError:
                        continue
    except OSError:
        return None
    return None


def in_window(path: Path, lo: str, hi: str) -> bool:
    ts = first_timestamp(path)
    return bool(ts and lo <= ts.date().isoformat() <= hi)


def tool_calls(path: Path):
    """Every `tool_use` block in one transcript, DE-DUPLICATED by `(message.id, block.id)`.

    A streaming assistant message is written to the transcript many times; counting records rather
    than calls inflates every figure downstream. Sidechains are INCLUDED, and that is a decision
    rather than an oversight: a subagent's write is a write, and the hook fires on it exactly as it
    fires on the parent's."""
    seen = set()
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"tool_use"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                msg = rec.get("message")
                if not isinstance(msg, dict) or not isinstance(msg.get("content"), list):
                    continue
                for b in msg["content"]:
                    if not isinstance(b, dict) or b.get("type") != "tool_use":
                        continue
                    key = (msg.get("id"), b.get("id"))
                    if key in seen:
                        continue
                    seen.add(key)
                    yield rec, b
    except OSError:
        return


def writes_in(path: Path, include_bash: bool = True):
    """Every WRITE this session made, through either trigger, in the shape the hook sees it.

    `trigger` is `edit` or `bash` and travels with the record all the way to the report: a result
    that cannot say which arm produced it cannot answer the question this row was ignited for."""
    for rec, b in tool_calls(path):
        name = b.get("name")
        ti = b.get("input")
        if not isinstance(ti, dict):
            continue
        ts = rec.get("timestamp") or ""
        if name in EDIT_TOOLS:
            fp = ti.get("file_path") or ti.get("notebook_path")
            if isinstance(fp, str) and fp:
                yield {"sid": path.stem, "ts": ts, "trigger": "edit", "path": fp,
                       "text": lesson_push.text_of(ti)}
        elif name == "Bash" and include_bash:
            cmd = ti.get("command")
            if not isinstance(cmd, str) or not cmd:
                continue
            # The SHIPPED detector, called the way the hook calls it — INCLUDING the cwd. The
            # transcript records the session's working directory on every tool call, and the hook
            # is handed the same value in its payload; replaying with `None` instead made every
            # `cd <worktree> && cat > <relative>` resolve against the harness's own root, which
            # named a file in another repo and was found by reading the audit sample rather than
            # by any test. A replay that carried its own copy of the detector would measure a
            # different instrument; so would one that called the same detector with different
            # inputs.
            targets = lesson_push.bash_targets(cmd, rec.get("cwd"))
            for p, how in targets[:1]:               # the hook speaks once per command
                yield {"sid": path.stem, "ts": ts, "trigger": "bash", "path": str(p),
                       "text": cmd, "how": how}


# A Bash command that PRODUCES A FILE, for the COVERAGE DENOMINATOR only. Deliberately crude and
# deliberately CONSERVATIVE: it counts redirections, `tee`, and heredocs into an interpreter, and
# it will miss a script that writes from inside itself. Every miss makes the coverage figure LOOK
# BETTER than the truth, so the number it produces is an upper bound on the share the hook sees —
# the safe direction for a figure used to argue a design is blind. It is NOT the trigger: the
# trigger is `lesson_push.bash_targets`, which is stricter (literal targets only), and the gap
# between the two is reported as the trigger's own coverage of the file-producing population.
WRITEY = re.compile(r"(^|[|;&\s])(cat\s*>|tee\b|python3?\s*-\s*<<|>>?\s*[\w./-]+\.(md|py|json|tsv|txt|html|sh))")


def trigger_coverage(files: list) -> dict:
    """What share of FILE-PRODUCING tool calls each trigger can see.

    Three numbers, and the middle one is the row's question: how many of the corpus's writes go
    through Edit/Write (design 1's ceiling), how many more the Bash arm's LITERAL-target detector
    adds, and how many file-producing Bash calls neither sees."""
    n = {"edit_tools": 0, "bash_writey": 0, "bash_detected": 0, "bash_calls": 0}
    for path in files:
        for _rec, b in tool_calls(path):
            name = b.get("name")
            if name in EDIT_TOOLS:
                n["edit_tools"] += 1
            elif name == "Bash":
                n["bash_calls"] += 1
                cmd = (b.get("input") or {}).get("command") or ""
                writey = bool(WRITEY.search(cmd))
                if writey:
                    n["bash_writey"] += 1
                if lesson_push.bash_targets(cmd, _rec.get("cwd")):
                    n["bash_detected"] += 1
    # The denominator is the UNION: a command the strict detector sees but the crude one does not
    # is still a file-producing call, and dropping it would shrink the denominator in the
    # direction that flatters the trigger.
    total = n["edit_tools"] + max(n["bash_writey"], n["bash_detected"])
    return {
        "edit_tool_calls": n["edit_tools"],
        "bash_calls": n["bash_calls"],
        "bash_file_writing_calls_crude": n["bash_writey"],
        "bash_calls_with_a_literal_target": n["bash_detected"],
        "file_producing_total": total,
        "share_visible_design1_edit_only": round(n["edit_tools"] / total, 4) if total else 0.0,
        "share_visible_design2_both": round((n["edit_tools"] + n["bash_detected"]) / total, 4) if total else 0.0,
        "_note": "the crude Bash detector is an upper bound on the denominator's Bash half; the "
                 "strict one is the shipped trigger",
    }


def bash_audit(files: list, n: int, seed: int) -> dict:
    """A sample of REAL Bash calls with the shipped detector's verdict on each — the false-positive
    evidence, laid out so a reviewer can disagree with it line by line.

    The classification here is MECHANICAL and deliberately narrow: a detection is counted
    `suspect` when the target is a path no write would leave behind (a device, a pipe name) or
    when the command that produced it has no writing verb at all. Anything else is `plausible` and
    is left to the reviewer, whose sample this is — a harness that judged its own detector's
    output would be answering the question it was built to ask."""
    rng = random.Random(seed)
    pool = []
    for path in files:
        for _rec, b in tool_calls(path):
            if b.get("name") != "Bash":
                continue
            cmd = (b.get("input") or {}).get("command") or ""
            if cmd:
                pool.append((cmd, _rec.get("cwd")))
    rng.shuffle(pool)
    sample = pool[:n]
    rows, detected = [], 0
    for cmd, cwd in sample:
        targets = lesson_push.bash_targets(cmd, cwd)
        if not targets:
            continue
        detected += 1
        verdicts = []
        for p, how in targets:
            s = str(p)
            suspect = (s.startswith(("/dev/", "/proc/", "/sys/"))
                       or p.name in ("", "-")
                       or (how.startswith("redirect") and p.suffix == "" and "/" not in s))
            verdicts.append({"target": s, "how": how, "mechanical": "suspect" if suspect else "plausible"})
        rows.append({"command": " ".join(cmd.split())[:300], "targets": verdicts})
    return {"n_sampled": len(sample), "n_detected": detected,
            "detection_rate": round(detected / len(sample), 4) if sample else 0.0,
            "n_mechanically_suspect": sum(1 for r in rows
                                          if any(v["mechanical"] == "suspect" for v in r["targets"])),
            "rows": rows}


# ---------------------------------------------------------------- corpora ----

class Indices:
    """One citation index per region, built once and reused — the hook's per-session cache, in the
    replay's own shape. The hook's disk cache is keyed by session id and stamped by the source
    files; here the process is the session."""

    def __init__(self, vault: Path, include_global: bool = True):
        self.vault = vault
        self.include_global = include_global
        self._idx: dict = {}
        self._cands: dict = {}

    def for_region(self, region):
        key = region or ""
        if key not in self._idx:
            cands = lesson_push.candidates(self.vault, region, self.include_global)
            self._cands[key] = cands
            self._idx[key] = lesson_push.build_index(cands)
        return self._idx[key]

    def candidates_for(self, region):
        self.for_region(region)
        return self._cands[region or ""]

    def regions_with_lessons(self) -> list:
        out = []
        for d in sorted(self.vault.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            if any((d / f"{s}.md").is_file() for s in lesson_push.LESSON_STEMS):
                out.append(d.name)
            for sub in sorted(x for x in d.iterdir() if x.is_dir() and not x.name.startswith(".")):
                if any((sub / f"{s}.md").is_file() for s in lesson_push.LESSON_STEMS):
                    out.append(f"{d.name}/{sub.name}")
        return out


# ---------------------------------------------------------------- the three gates ----

def matched(writes: list, indices: Indices) -> list:
    """Each write with the FULL ordered hit list its region's index produces for it.

    Uncapped on purpose: the caps are applied downstream by the arms that measure what the hook
    would have PRINTED, while gate 2 needs to know where in the order a lesson actually landed."""
    out = []
    for w in writes:
        p = Path(w["path"]).expanduser()
        region = lesson_push.region_for(p, None)
        idx = indices.for_region(region)
        rel = lesson_push.display_rel(p)
        hits = lesson_push.lines_for(idx, rel, w["text"], [], 10_000)
        out.append({**w, "region": region, "rel": rel, "n_tokens_indexed": len(idx),
                    "hits": hits})
    return out


def false_positive_rate(scored: list, indices: Indices, seed: int, sample: int) -> dict:
    """Gate 1 for design 2. The MATCHED CONTROL, exactly as design 1 built it, with a score
    replaced by a fire/no-fire: the write's OWN region index, its own corpus, its own caps —
    scored against the TOKENS OF A DIFFERENT WRITE from another region. Everything is held fixed
    except the one thing under test.

    ★ WHAT COUNTS AS A FIRE IS WHAT THE HOOK WOULD PRINT: the first hit after the tautology rule.
    Both arms of the control run through `lesson_push.lines_for`, so a suppression that only
    applies to one arm cannot make the control look weak."""
    rng = random.Random(seed)
    pool = [s for s in scored if s["region"]]
    rng.shuffle(pool)
    pool = pool[:sample]
    signal_fires = sum(1 for s in pool if s["hits"])
    control_fires, control_n = 0, 0
    by_class_signal, by_class_control = {}, {}
    for s in pool:
        for _l, _h, tok in s["hits"][:1]:
            k = tok.split(":", 1)[0]
            by_class_signal[k] = by_class_signal.get(k, 0) + 1
        foreigners = [x for x in pool if x["region"] != s["region"]]
        if not foreigners:
            continue
        other = rng.choice(foreigners)
        control_n += 1
        hits = lesson_push.lines_for(indices.for_region(s["region"]), other["rel"],
                                     other["text"], [], 2)
        if hits:
            control_fires += 1
            k = hits[0][2].split(":", 1)[0]
            by_class_control[k] = by_class_control.get(k, 0) + 1
    sig_rate = signal_fires / len(pool) if pool else 0.0
    ctl_rate = control_fires / control_n if control_n else 0.0
    return {
        "n_sampled": len(pool),
        "signal_fire_rate": round(sig_rate, 4),
        "matched_control_fire_rate": round(ctl_rate, 4),
        "separation_ratio": round(sig_rate / ctl_rate, 3) if ctl_rate else None,
        "signal_match_class": dict(sorted(by_class_signal.items())),
        "control_match_class": dict(sorted(by_class_control.items())),
        "_reading": "a citation match is exact, so this replaces the noise floor: the control's "
                    "rate IS the false-positive rate of the index. Equal rates mean the matcher "
                    "is agreeing rather than matching.",
    }


ART = re.compile(r"([A-Za-z0-9][A-Za-z0-9._+-]*\.(?:md|py|tsv|json|sh))")
DATE = re.compile(r"(20\d\d-\d\d-\d\d)")


def held_out(rows: list, scored: list, per_fire: int) -> list:
    """Per RE-OCCURRENCE row: would the push have surfaced THAT lesson before THAT write?

    The row's `note` column names the artifact the re-occurrence was found in and its date. Those
    are the join keys; an artifact this repo's transcripts never wrote is UNCHECKED, which is a
    third outcome and never counted as a miss. **Design 2 reads Bash writes too, so a row whose
    artifact was only ever written through a heredoc is now checkable — the count of checkable
    rows is reported beside the verdicts because the change in it is itself a result.**"""
    by_name: dict = {}
    for s in scored:
        by_name.setdefault(Path(s["rel"]).name, []).append(s)
    out = []
    for row in rows:
        names = ART.findall(row["note"])
        dates = DATE.findall(row["note"])
        hits, relaxed = [], False
        for nm in names:
            for s in by_name.get(nm, []):
                if dates and s["ts"][:10] not in dates:
                    continue
                hits.append(s)
        if not hits:
            for nm in names:
                hits.extend(by_name.get(nm, []))
            relaxed = bool(hits)
        rec = {"file": row["file"], "heading": row["heading"], "class": row["class"],
               "evidence_artifacts": names, "evidence_dates": dates, "n_writes_found": len(hits),
               "triggers": sorted({s["trigger"] for s in hits}), "date_relaxed": relaxed}
        if not hits:
            rec["verdict"] = "UNCHECKED"
            rec["why"] = "no write to that artifact on that date in this repo's transcripts"
            out.append(rec)
            continue
        best_rank, surfaced, match_tok = None, False, None
        for s in hits:
            for i, (_line, heading, tok) in enumerate(s["hits"]):
                if heading == row["heading"]:
                    if best_rank is None or i < best_rank:
                        best_rank, match_tok = i, tok
                    if i < per_fire:
                        surfaced = True
                    break
        rec["best_rank"] = best_rank
        rec["match_token"] = match_tok
        rec["verdict"] = "HIT" if surfaced else "MISS"
        if not surfaced:
            rec["why"] = ("the lesson cites nothing this write touched"
                          if best_rank is None else
                          f"matched at rank {best_rank + 1}, outside the per-fire cap of {per_fire}")
        out.append(rec)
    return out


def context_cost(scored: list, per_fire: int, per_session: int) -> dict:
    """What the push would have ADDED, replaying the caps and the dedupe exactly as the hook does."""
    per_sid: dict = {}
    for s in sorted(scored, key=lambda x: (x["sid"], x["ts"])):
        st = per_sid.setdefault(s["sid"], {"lines": 0, "bytes": 0, "fires": 0, "writes": 0,
                                           "shown": set()})
        st["writes"] += 1
        if st["lines"] >= per_session:
            continue
        room = min(per_fire, per_session - st["lines"])
        emitted = 0
        for line, heading, _tok in s["hits"]:
            if emitted >= room:
                break
            if heading in st["shown"]:
                continue
            st["shown"].add(heading)
            st["lines"] += 1
            st["bytes"] += len(f"{line}\n".encode("utf-8"))
            emitted += 1
        if emitted:
            st["fires"] += 1
    lines = [v["lines"] for v in per_sid.values()]
    byts = [v["bytes"] for v in per_sid.values()]
    writes = sum(v["writes"] for v in per_sid.values())
    fires = sum(v["fires"] for v in per_sid.values())
    med_bytes = statistics.median(byts) if byts else 0
    return {
        "n_sessions": len(per_sid), "n_writes": writes, "n_fires": fires,
        "fire_rate": round(fires / writes, 4) if writes else 0.0,
        "lines_per_session_median": statistics.median(lines) if lines else 0,
        "lines_per_session_max": max(lines) if lines else 0,
        "lines_per_session_mean": round(statistics.fmean(lines), 2) if lines else 0.0,
        "bytes_per_session_median": med_bytes,
        "bytes_per_session_max": max(byts) if byts else 0,
        # bytes/4 is the same rule of thumb the boot budget uses; it is a rule of thumb and is
        # labelled as one wherever it appears.
        "share_of_boot_floor_median": round((med_bytes / 4) / BOOT_FLOOR_MEDIAN_TOKENS, 6),
    }


def sample_lines(scored: list, per_fire: int, n: int, seed: int) -> list:
    """A random sample of what it would actually SAY, for the relevance judgment. Verbatim lines,
    with the write they fired on and the TOKEN that matched — a relevance verdict on a line with
    no evidence beside it is a verdict on the vault's prose, not on the match."""
    rng = random.Random(seed)
    pool = []
    for s in scored:
        for line, _heading, tok in s["hits"][:per_fire]:
            pool.append({"write_path": s["rel"], "write_region": s["region"],
                         "trigger": s["trigger"], "line": line, "matched_token": tok,
                         "write_excerpt": " ".join(s["text"].split())[:280]})
    rng.shuffle(pool)
    return pool[:n]


# ---------------------------------------------------------------- main ----

def load_rows(tsv: Path) -> list:
    rows = []
    with tsv.open(encoding="utf-8") as fh:
        head = fh.readline().rstrip("\n").split("\t")
        idx = {k: i for i, k in enumerate(head)}
        for line in fh:
            f = line.rstrip("\n").split("\t")
            if len(f) <= idx["verdict"] or f[idx["verdict"]] != "RE-OCCURRENCE":
                continue
            rows.append({"file": f[idx["file"]], "heading": f[idx["heading"]],
                         "class": f[idx["class"]], "note": f[idx["note"]] if len(f) > idx["note"] else ""})
    return rows


# ================================ THE BOOT ARM (LESSONPUSH-3) ================================
#
# Design 3's gates, which are the SAME THREE, adapted to a trigger that has no write in it:
#
#   B1  NOISE FLOOR.  A citation match is exact and the boot arm has no score either, so the floor
#       is again a CONTROL RATE — but two of them, because a boot selection has two inputs and
#       each can be the thing that is really doing the work. Hold the session's structural surface
#       fixed and swap the lesson CORPUS to a foreign lane's: if the same lessons come out, the
#       arm is picking Global furniture. Hold the corpus fixed and swap the SURFACE to another
#       session's: if the same lessons come out, the arm is not reading the session at all.
#       Reported as overlap (Jaccard and identical-set share), not as a rate.
#
#   B2  HELD-OUT.  For each RE-OCCURRENCE row, find the SESSION that re-broke the lesson — the one
#       that wrote the artifact the census cites, on the date it cites — and ask whether that
#       session's boot lines would have carried the lesson. Unlike the write-time arms this is a
#       SESSION-level question, so a row is checkable whenever its session is locatable at all.
#
#       ★ RECONSTRUCTED AS OF THAT DAY, and it matters which half. The lesson corpus and the queue
#       rows are materialised from the vault's git history at the row's own date, because both
#       grow weekly and today's versions contain rows the work itself created — an arm scored
#       against them would be reading its own answer. The REPO-side boot chain (a repo's
#       `CLAUDE.md`) is taken as it is today and is named as such: it is a slowly-changing
#       lane-level document, and the direction of the error is that today's version carries
#       pointers added since, which FLATTERS the arm.
#
#   B3  RELEVANCE.  40 boot line-SETS — a session's whole block, not a single line, because a boot
#       block is read as a block — written out for a reviewer to judge.
#
# Default ON needs >=60% relevant AND >=3 held-out hits of 17, and the cost is reported beside the
# measured boot floor, because this arm is paid for by every session rather than by every write.

import subprocess, tempfile, shutil                  # noqa: E402

LESSON_GLOBS = ("Errata.md", "Patterns.md", "Patterns-verification.md")


def _git(repo: Path, *args, binary: bool = False):
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout if binary else r.stdout.decode("utf-8", "replace")


def rev_at(repo: Path, date: str) -> str | None:
    """The vault commit this day ENDED at. `None` when the repo has no commit that old, which is
    an UNCHECKED row rather than a silent fall-back to today."""
    out = _git(repo, "rev-list", "-1", f"--before={date}T23:59:59", "HEAD")
    return (out or "").strip() or None


def asof_vault(vault: Path, rev: str, dest: Path) -> Path | None:
    """Materialise the lesson files and queue files of ONE vault commit into `dest`.

    A real directory rather than an in-memory corpus, so the SHIPPED selector runs over it through
    `GEDAECHTNIS_VAULT` unchanged — the replay must run the code that ships, and a second reader
    for historical content would be a second implementation of the thing under test."""
    names = _git(vault, "ls-tree", "-r", "--name-only", rev)
    if names is None:
        return None
    qrel = (config.queues_rel() or "").strip("/")
    want = [n for n in names.splitlines()
            if n.rsplit("/", 1)[-1] in LESSON_GLOBS or (qrel and n.startswith(qrel + "/") and n.endswith(".md"))]
    if not want:
        return None
    dest.mkdir(parents=True, exist_ok=True)
    tar = _git(vault, "archive", rev, "--", *want, binary=True)
    if not tar:
        return None
    try:
        subprocess.run(["tar", "-x", "-C", str(dest)], input=tar, check=True,
                       capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    return dest


class _VaultAs:
    """`GEDAECHTNIS_VAULT` for the duration of a block, restored afterwards."""

    def __init__(self, path):
        self.path, self.prev = str(path), None

    def __enter__(self):
        self.prev = os.environ.get("GEDAECHTNIS_VAULT")
        os.environ["GEDAECHTNIS_VAULT"] = self.path
        return self

    def __exit__(self, *exc):
        if self.prev is None:
            os.environ.pop("GEDAECHTNIS_VAULT", None)
        else:
            os.environ["GEDAECHTNIS_VAULT"] = self.prev
        return False


def session_cwd(path: Path) -> str | None:
    """The working directory this session ran in, from the first record that carries one."""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i > 400 or '"cwd"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                cwd = rec.get("cwd")
                if isinstance(cwd, str) and cwd:
                    return cwd
    except OSError:
        return None
    return None


def session_opener(path: Path) -> str | None:
    """This session's first user message — what a RESUME would have had at boot and a cold start
    would not. Carried through the replay so the opener's contribution can be measured separately
    from the proxy the shipped hook actually gets."""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i > 400:
                    break
                if '"user"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("type") != "user":
                    continue
                msg = rec.get("message")
                c = msg.get("content") if isinstance(msg, dict) else None
                if isinstance(c, str) and c.strip():
                    return c[:20_000]
                if isinstance(c, list):
                    parts = [b.get("text", "") for b in c
                             if isinstance(b, dict) and b.get("type") == "text"]
                    if any(p.strip() for p in parts):
                        return "\n".join(parts)[:20_000]
    except OSError:
        return None
    return None


def session_meta(files: list) -> list:
    """One record per transcript: its cwd, the lane that cwd declares, its date, its opener."""
    out = []
    for f in files:
        cwd = session_cwd(f)
        if not cwd:
            continue
        lane, prefixes, marker = common.lane_for(cwd)
        ts = first_timestamp(f)
        out.append({"sid": f.stem, "cwd": cwd, "lane": lane, "prefixes": list(prefixes),
                    "date": ts.date().isoformat() if ts else None,
                    "opener": session_opener(f), "path": str(f)})
    return out


def repo_chain(cwd: str) -> list:
    """The @-import chain a session in `cwd` boots with, resolved by the SHIPPED walker."""
    sys.path.insert(0, str(HOOKS))
    import session_start
    try:
        return session_start.boot_chain_files([config.user_memory(), Path(cwd) / "CLAUDE.md"])
    except Exception:
        return []


def boot_set(meta: dict, vault: Path, max_lines: int, reocc: dict,
             corpus_prefixes=None, surface=None, use_opener: bool = False) -> list:
    """The boot lines one session would have seen — the SHIPPED selector, nothing re-implemented.

    `corpus_prefixes` swaps the lesson CORPUS to another lane's (control 1); `surface` swaps the
    structural surface to another session's (control 2). Both default to this session's own."""
    prefixes = corpus_prefixes if corpus_prefixes is not None else meta["prefixes"]
    src = surface if surface is not None else meta
    regions = lesson_push.lane_regions(prefixes)
    cands = lesson_push.boot_candidates(vault, regions)
    if not cands:
        return []
    qf = lesson_push.queue_files(src["lane"], src["prefixes"])
    stoks = lesson_push.structural_tokens(src.get("chain") or [], qf,
                                          src.get("opener") if use_opener else None)
    if not stoks:
        return []
    return lesson_push.boot_select(cands, stoks, [], max_lines, reocc)


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    u = a | b
    return round(len(a & b) / len(u), 4) if u else 0.0


def foreign_region_prefixes(vault: Path, own: list) -> list:
    """Vault regions with lesson files that are NOT this lane's, as partition prefixes.

    Drawn from the VAULT rather than from the sampled sessions, because a transcript directory
    belongs to ONE repo and therefore to one lane: taking the foreign arm from the sample would
    have produced no control at all, which is exactly what the first run of this gate did."""
    out = []
    try:
        tops = sorted(d for d in vault.iterdir() if d.is_dir() and not d.name.startswith("."))
    except OSError:
        return []
    for d in tops:
        cands = [d.name] + [f"{d.name}/{x.name}" for x in sorted(d.iterdir())
                            if x.is_dir() and not x.name.startswith(".")] if d.is_dir() else []
        for rel in cands:
            if rel in own or rel == "Global":
                continue
            if any((vault / rel / f"{st}.md").is_file() for st in lesson_push.LESSON_STEMS):
                out.append(rel)
    return out


def foreign_lane_surfaces(exclude_lane: str | None) -> list:
    """A structural surface per OTHER lane on this machine — its repo's @-import chain and its own
    queue rows. The markers are read from disk (`common.lane_for` walking each sibling checkout),
    so this control is as real as the signal arm and is not synthesised from it."""
    out, seen = [], set()
    try:
        sibs = sorted(d for d in REPO_ROOT.parent.iterdir() if d.is_dir())
    except OSError:
        return []
    for d in sibs:
        if not (d / ".atlas-lane").is_file():
            continue
        lane, prefixes, _m = common.lane_for(str(d))
        if not lane or lane == exclude_lane or lane in seen:
            continue
        seen.add(lane)
        out.append({"sid": f"lane:{lane}", "lane": lane, "prefixes": list(prefixes),
                    "cwd": str(d), "chain": repo_chain(str(d)), "opener": None, "date": None})
    return out


def boot_controls(metas: list, vault: Path, max_lines: int, reocc: dict, seed: int,
                  sample: int) -> dict:
    """Gate B1. Two matched controls, everything held fixed but the one swapped input."""
    rng = random.Random(seed)
    pool = [m for m in metas if m.get("lane") and m.get("real_set")]
    rng.shuffle(pool)
    pool = pool[:sample]
    corpus_j, surface_j, corpus_same, surface_same, n = [], [], 0, 0, 0
    empty_corpus, empty_surface = 0, 0
    no_foreign_corpus, no_foreign_surface = 0, 0
    for m in pool:
        real = set(m["real_set"])
        own = lesson_push.lane_regions(m["prefixes"])
        fregions = foreign_region_prefixes(vault, own)
        fsurfaces = foreign_lane_surfaces(m["lane"])
        if not fregions:
            no_foreign_corpus += 1
        if not fsurfaces:
            no_foreign_surface += 1
        if not fregions or not fsurfaces:
            continue
        n += 1
        fr = rng.choice(fregions)
        c = {h for _l, h, _t, _s in boot_set(m, vault, max_lines, reocc, corpus_prefixes=[fr])}
        o = rng.choice(fsurfaces)
        sset = {h for _l, h, _t, _s in boot_set(m, vault, max_lines, reocc, surface=o)}
        if not c:
            empty_corpus += 1
        if not sset:
            empty_surface += 1
        corpus_j.append(_jaccard(real, c))
        surface_j.append(_jaccard(real, sset))
        corpus_same += int(bool(real) and real == c)
        surface_same += int(bool(real) and real == sset)
    med = lambda xs: round(statistics.median(xs), 4) if xs else None   # noqa: E731
    return {
        "n_sampled": n,
        "median_overlap_foreign_corpus": med(corpus_j),
        "median_overlap_foreign_surface": med(surface_j),
        "mean_overlap_foreign_corpus": round(sum(corpus_j) / len(corpus_j), 4) if corpus_j else None,
        "mean_overlap_foreign_surface": round(sum(surface_j) / len(surface_j), 4) if surface_j else None,
        "identical_set_foreign_corpus": corpus_same,
        "identical_set_foreign_surface": surface_same,
        "empty_foreign_corpus": empty_corpus,
        "empty_foreign_surface": empty_surface,
        "sessions_with_no_foreign_corpus": no_foreign_corpus,
        "sessions_with_no_foreign_surface": no_foreign_surface,
        "_reading": "the floor for a selector with no score. A median overlap near 1 means the "
                    "arm is not reading the input that was swapped; near 0 means it is. Two "
                    "controls because a boot selection has two inputs and either could be "
                    "carrying it alone.",
    }


def boot_held_out(rows: list, writes_by_name: dict, metas_by_sid: dict, vault: Path,
                  max_lines: int, reocc: dict, workdir: Path) -> list:
    """Gate B2, session-level. The census names the artifact and the date; the transcripts say
    which SESSION wrote it; that session's own boot lines are then reconstructed as of that day."""
    out = []
    cache: dict = {}
    for row in rows:
        names, dates = ART.findall(row["note"]), DATE.findall(row["note"])
        sids = []
        for nm in names:
            for w in writes_by_name.get(nm.casefold(), []):
                if dates and w["ts"][:10] not in dates:
                    continue
                if w["sid"] not in sids:
                    sids.append(w["sid"])
        relaxed = False
        if not sids:
            for nm in names:
                for w in writes_by_name.get(nm.casefold(), []):
                    if w["sid"] not in sids:
                        sids.append(w["sid"])
            relaxed = bool(sids)
        rec = {"file": row["file"], "heading": row["heading"], "class": row["class"],
               "evidence_artifacts": names, "evidence_dates": dates,
               "n_sessions_found": len(sids), "date_relaxed": relaxed}
        sids = [s for s in sids if s in metas_by_sid]
        if not sids:
            rec["verdict"] = "UNCHECKED"
            rec["why"] = ("the census note names no artifact file" if not names else
                          "no session in the joined transcripts wrote that artifact")
            out.append(rec)
            continue
        surfaced, best_rank, why = False, None, None
        for sid in sids:
            m = metas_by_sid[sid]
            day = m.get("date") or (dates[0] if dates else None)
            if not day:
                continue
            if day not in cache:
                rev = rev_at(config.vault(), day)
                dest = workdir / f"asof-{day}"
                cache[day] = (rev, asof_vault(config.vault(), rev, dest) if rev else None)
            rev, vdir = cache[day]
            if vdir is None:
                continue
            with _VaultAs(vdir):
                lines = boot_set(m, Path(vdir), max_lines, reocc)
                full = boot_set(m, Path(vdir), 10_000, reocc)
                # ★ POSITIVE CONTROL ON THE CORPUS ITSELF. A MISS means three different things
                # and only one of them is about the selector: the lesson was not yet WRITTEN on
                # that day; it was written but CITES NOTHING, so no citation arm could ever reach
                # it; or it was a live candidate and was not selected. Without this check all
                # three print the same word, which is the failure this arc keeps re-finding.
                # Searched across the WHOLE as-of corpus, not only the file the census names:
                # a lesson moves between a role file and its archive, and this vault moved
                # several during the window. Accumulated with `or` over every session checked
                # for this row, because one session's day may predate the lesson and another's
                # may not — taking the last one read would have been an accident of iteration.
                cands_asof = lesson_push.boot_candidates(
                    Path(vdir), lesson_push.lane_regions(m["prefixes"]))
                present, candidate = False, False
                for _rel, _h, _t, _d in cands_asof:
                    if _h == row["heading"]:
                        candidate = True
                        break
                for f in lesson_push.lesson_files(Path(vdir), None) + [
                        Path(vdir) / row["file"]]:
                    try:
                        if f.is_file() and row["heading"] in f.read_text(
                                encoding="utf-8", errors="replace"):
                            present = True
                            break
                    except OSError:
                        continue
                rec["entry_in_asof_file"] = bool(rec.get("entry_in_asof_file")) or present or candidate
                rec["entry_is_candidate"] = bool(rec.get("entry_is_candidate")) or candidate
                rec["asof_rev"] = rev[:8] if rev else None
                rec["n_candidates_asof"] = len(cands_asof)
            heads = [h for _l, h, _t, _s in lines]
            allh = [h for _l, h, _t, _s in full]
            if row["heading"] in heads:
                surfaced = True
                best_rank = allh.index(row["heading"])
                break
            if row["heading"] in allh:
                r = allh.index(row["heading"])
                if best_rank is None or r < best_rank:
                    best_rank = r
        rec["checked_sessions"] = len(sids)
        rec["best_rank"] = best_rank
        if surfaced:
            rec["verdict"] = "HIT"
        elif not rec.get("entry_in_asof_file"):
            rec["verdict"] = "UNCHECKED"
            rec["why"] = ("the lesson is not present under this heading anywhere in the "
                          "vault as of the day that session ran — it cannot be a boot line "
                          "before it exists, and a heading the census read TODAY may have been "
                          "worded differently then")
        elif not rec.get("entry_is_candidate"):
            rec["verdict"] = "MISS"
            rec["why"] = ("the lesson existed and CITES NOTHING, so no citation-based arm can "
                          "reach it — design 2's recorded blindness, unchanged at boot")
        else:
            rec["verdict"] = "MISS"
            rec["why"] = ("a live candidate this session was not structurally pointed at"
                          if best_rank is None else
                          f"selected at rank {best_rank + 1}, outside the {max_lines}-line block")
        out.append(rec)
    return out


def boot_cost(metas: list, max_lines: int) -> dict:
    """What the block would COST, per session, against the measured boot floor."""
    lines = [len(m["real_set"]) for m in metas if m.get("real_set") is not None]
    byts = [sum(len(l) + 1 for l in m.get("real_lines", [])) for m in metas
            if m.get("real_set") is not None]
    if not lines:
        return {"n_sessions": 0}
    tok = [b / 4 for b in byts]
    return {
        "n_sessions": len(lines),
        "lines_median": statistics.median(lines), "lines_max": max(lines),
        "sessions_with_a_block": sum(1 for x in lines if x),
        "bytes_median": int(statistics.median(byts)), "bytes_max": max(byts),
        "approx_tokens_median": round(statistics.median(tok), 1),
        "share_of_boot_floor_median": round(statistics.median(tok) / BOOT_FLOOR_MEDIAN_TOKENS, 6),
        "boot_floor_median_tokens": BOOT_FLOOR_MEDIAN_TOKENS,
        "_reading": "paid by EVERY session, not only by a session that writes — which is why the "
                    "bar for this arm is higher than the write arms' and why the figure is "
                    "reported against the floor rather than on its own.",
    }


def boot_relevance_sample(metas: list, n: int, seed: int) -> list:
    """Gate B3. Whole BLOCKS, because a boot block is read as a block."""
    rng = random.Random(seed)
    pool = [m for m in metas if m.get("real_lines")]
    rng.shuffle(pool)
    out = []
    for m in pool[:n]:
        out.append({"sid": m["sid"], "lane": m["lane"], "date": m["date"],
                    "cwd_repo": Path(m["cwd"]).name,
                    "opener_first_line": (m.get("opener") or "").strip().splitlines()[:1],
                    "lines": list(m["real_lines"])})
    return out


def boot_main(a, root: Path, files: list) -> int:
    """LESSONPUSH-3's three gates. A separate entry point rather than a flag threaded through the
    write arms', because it shares a population and a corpus with them and nothing else: there is
    no write in it, so trigger coverage, the false-positive rate and the per-write cost have no
    meaning here and printing them beside these numbers would invite exactly the comparison the
    row forbids."""
    import limits
    max_lines = a.boot_max_lines or int(limits.get("lesson_push_boot_max_lines", 5))
    if a.boot_extra_furniture:
        lesson_push.ROLE_STEMS = lesson_push.ROLE_STEMS | {
            "report", "handoff", "deviations", "plan", "opener", "changelog", "row", "brief",
            "judgment", "census", "spec", "state", "checkpoint"}
    vault = config.vault()
    rows = load_rows(Path(os.path.expanduser(a.yield_tsv)))
    reocc = reocc_from_rows(rows)

    metas = session_meta(files)
    for m in metas:
        m["chain"] = repo_chain(m["cwd"])
    laned = [m for m in metas if m.get("lane")]
    for m in metas:
        if not m.get("lane"):
            m["real_set"], m["real_lines"] = None, []
            continue
        lines = boot_set(m, vault, max_lines, reocc, use_opener=a.boot_with_opener)
        m["real_lines"] = [l for l, _h, _t, _s in lines]
        m["real_set"] = [h for _l, h, _t, _s in lines]
        m["real_sources"] = [s for _l, _h, _t, s in lines]

    # Gate B2's JOIN population. The selection arms above are this repo's sessions — the census's
    # own population — but a re-occurrence the census found in another lane's repo was written by
    # a session whose transcript lives in that project's directory, and with only this one the row
    # is UNCHECKED for a reason that has nothing to do with the design. `--boot-all-projects`
    # widens the SEARCH FOR THE SESSION and nothing else: the arm those sessions are then scored
    # under is the same shipped selector, over the same as-of vault.
    join_files, join_metas = list(files), []
    if a.boot_all_projects:
        base = Path.home() / ".claude" / "projects"
        extra = [q for d in sorted(base.glob("*")) if d.is_dir()
                 for q in sorted(d.glob("*.jsonl")) if q not in files and in_window(q, a.lo, a.hi)]
        join_files += extra
        for m in session_meta(extra):
            if m.get("lane"):
                m["chain"] = repo_chain(m["cwd"])
                join_metas.append(m)                 # gate B2 only — NOT the selection population
    writes = []
    for f in join_files:
        writes.extend(writes_in(f, include_bash=True))
    by_name = {}
    for w in writes:
        # CASE-FOLDED. The census notes name artifacts as their authors typed them
        # (`qlint-2026-08-07 deviations.md`) and the tree carries `DEVIATIONS.md`; an exact join
        # left four rows UNCHECKED for a reason that was the harness's, not the design's.
        by_name.setdefault(Path(w["path"]).name.casefold(), []).append(w)
    metas_by_sid = {m["sid"]: m for m in list(metas) + join_metas if m.get("lane")}

    import tempfile
    workdir = Path(tempfile.mkdtemp(prefix="lessonpush3-asof-"))
    try:
        ho = boot_held_out(rows, by_name, metas_by_sid, vault, max_lines, reocc, workdir)
    finally:
        pass                                         # the as-of trees are named in the report
    src_counts = {}
    for m in metas:
        for s in m.get("real_sources") or []:
            src_counts[s] = src_counts.get(s, 0) + 1

    result = {
        "arm": "boot (LESSONPUSH-3)",
        "population": {"transcript_dir": str(root), "n_transcripts_in_window": len(files),
                       "window": [a.lo, a.hi], "n_sessions_with_a_lane": len(laned),
                       "n_sessions_without_a_lane": len(metas) - len(laned),
                       "n_reoccurrence_rows": len(rows), "vault": str(vault),
                       "n_join_transcripts": len(join_files),
                       "n_join_sessions_outside_this_repo": len(join_metas),
                       "opener_given": bool(a.boot_with_opener),
                       "extra_furniture": bool(a.boot_extra_furniture),
                       "asof_workdir": str(workdir)},
        "settings": {"boot_max_lines": max_lines, "seed": a.seed},
        "selection_sources": dict(sorted(src_counts.items())),
        "controls": boot_controls(metas, vault, max_lines, reocc, a.seed, a.boot_control_sample),
        "held_out": ho,
        "cost": boot_cost(metas, max_lines),
        "relevance_sample": boot_relevance_sample(metas, a.relevance_sample, a.seed),
    }
    result["held_out_summary"] = {
        "HIT": sum(1 for r in ho if r["verdict"] == "HIT"),
        "MISS": sum(1 for r in ho if r["verdict"] == "MISS"),
        "UNCHECKED": sum(1 for r in ho if r["verdict"] == "UNCHECKED"),
        "checked": sum(1 for r in ho if r["verdict"] != "UNCHECKED"),
        "total_rows": len(ho),
    }
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    if a.md_out:
        Path(a.md_out).write_text(render_boot_md(result), encoding="utf-8")
    summary = {k: result[k] for k in ("arm", "population", "settings", "selection_sources",
                                      "controls", "held_out_summary", "cost")}
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    return 0


def reocc_from_rows(rows: list) -> dict:
    """`(file, heading) -> count` from the census rows already loaded. The shipped hook reads the
    same shape from a configured TSV; here the rows are in hand, so nothing is re-read."""
    out = {}
    for r in rows:
        key = (r["file"].strip(), r["heading"].strip())
        out[key] = out.get(key, 0) + 1
    return out


def render_boot_md(r: dict) -> str:
    L = ["# lesson_push boot arm (LESSONPUSH-3) — replay", "",
         f"Population: {r['population']['n_transcripts_in_window']} transcripts in "
         f"{r['population']['window'][0]}..{r['population']['window'][1]}, "
         f"{r['population']['n_sessions_with_a_lane']} of them in a lane.", "",
         "## Gate B1 — the two matched controls", "",
         "```", json.dumps(r["controls"], indent=1, ensure_ascii=False), "```", "",
         "## Gate B2 — held out over the re-occurrence rows", "",
         "```", json.dumps(r["held_out_summary"], indent=1), "```", ""]
    for h in r["held_out"]:
        L.append(f"- **{h['verdict']}** — `{h['file']}` — {h['heading'][:110]}")
        L.append(f"  - {h.get('why', '')} (sessions found: {h.get('n_sessions_found', 0)}, "
                 f"rank: {h.get('best_rank')})")
    L += ["", "## Cost", "", "```", json.dumps(r["cost"], indent=1), "```", "",
          "## Gate B3 — relevance sample (blocks, for the reviewer)", ""]
    for s in r["relevance_sample"]:
        L.append(f"### {s['sid'][:8]} — lane {s['lane']}, {s['date']}, repo `{s['cwd_repo']}`")
        for ln in s["lines"]:
            L.append(f"- {ln}")
        L.append("")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--transcripts", default=None)
    ap.add_argument("--yield-tsv", default=str(DEFAULT_YIELD_TSV))
    ap.add_argument("--from", dest="lo", default="2026-08-01")
    ap.add_argument("--to", dest="hi", default="2026-09-20")
    ap.add_argument("--per-fire", type=int, default=None)
    ap.add_argument("--per-session", type=int, default=None)
    ap.add_argument("--control-sample", type=int, default=400)
    ap.add_argument("--relevance-sample", type=int, default=40)
    ap.add_argument("--max-writes", type=int, default=0, help="0 = every write in the window")
    ap.add_argument("--seed", type=int, default=20260920)
    ap.add_argument("--edit-only", action="store_true",
                    help="design 1's population: Edit/Write/MultiEdit only, no Bash arm")
    ap.add_argument("--no-global", action="store_true",
                    help="DIAGNOSTIC: index the region's own lessons only. Global lessons apply to "
                         "every region by construction, so they match a control correctly and make "
                         "it read stronger than it is.")
    ap.add_argument("--bash-audit", default=None,
                    help="write the Bash-detector audit sample (markdown) to this path")
    ap.add_argument("--bash-audit-n", type=int, default=200)
    ap.add_argument("--boot", action="store_true",
                    help="LESSONPUSH-3: run the BOOT arm's three gates instead of the write arms'")
    ap.add_argument("--boot-max-lines", type=int, default=None)
    ap.add_argument("--boot-control-sample", type=int, default=40)
    ap.add_argument("--boot-extra-furniture", action="store_true",
                    help="LESSONPUSH-2 §6.3 named one carry-over for a boot arm: add the arc's "
                         "document names (report, handoff, deviations, plan, opener, changelog) "
                         "to the furniture cut, because a shared FILENAME is not a shared "
                         "subject. MEASURED HERE, SHIPPED NOWHERE — extending the shared "
                         "`ROLE_STEMS` would silently re-scope the two write arms, whose numbers "
                         "are recorded beside `lesson_push_enabled` and must stay reproducible.")
    ap.add_argument("--boot-all-projects", action="store_true",
                    help="gate B2 only: look for the session that wrote a census artifact in "
                         "EVERY project's transcripts, not only this repo's. The census ran over "
                         "the whole fleet and a re-occurrence recorded in another lane's repo is "
                         "otherwise UNCHECKED — a third outcome, never a miss.")
    ap.add_argument("--boot-with-opener", action="store_true",
                    help="DIAGNOSTIC: give the selector the session's first user message. The "
                         "shipped hook has it only on a resume (measured: 96 of 97 cold starts "
                         "run the hook BEFORE the first user record exists), so this arm answers "
                         "'what would a resume see', never 'what does the hook see'.")
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--md", dest="md_out", default=None)
    a = ap.parse_args(argv)

    import limits
    per_fire = a.per_fire or int(limits.get("lesson_push_max_per_fire", 2))
    per_session = a.per_session or int(limits.get("lesson_push_max_per_session", 10))

    root = Path(os.path.expanduser(a.transcripts)) if a.transcripts else default_transcripts()
    files = sorted(p for p in root.glob("*.jsonl") if in_window(p, a.lo, a.hi))
    if a.boot:
        return boot_main(a, root, files)
    writes = []
    for f in files:
        writes.extend(writes_in(f, include_bash=not a.edit_only))
    if a.max_writes:
        writes = writes[:a.max_writes]
    if not writes:
        print(f"no writes found under {root} in [{a.lo}, {a.hi}] — nothing to replay", file=sys.stderr)
        return 2

    vault = config.vault()
    indices = Indices(vault, include_global=not a.no_global)
    scored = matched(writes, indices)
    rows = load_rows(Path(os.path.expanduser(a.yield_tsv)))
    by_trigger = {}
    for w in writes:
        by_trigger[w["trigger"]] = by_trigger.get(w["trigger"], 0) + 1

    result = {
        "population": {"transcript_dir": str(root), "n_transcripts_in_window": len(files),
                       "window": [a.lo, a.hi], "n_writes": len(writes),
                       "writes_by_trigger": by_trigger,
                       "n_reoccurrence_rows": len(rows), "vault": str(vault),
                       "include_global": not a.no_global, "edit_only": a.edit_only},
        "settings": {"per_fire": per_fire, "per_session": per_session, "seed": a.seed},
        "trigger_coverage": trigger_coverage(files),
        "false_positive": false_positive_rate(scored, indices, a.seed, a.control_sample),
        "held_out": held_out(rows, scored, per_fire),
        "context_cost": context_cost(scored, per_fire, per_session),
        "relevance_sample": sample_lines(scored, per_fire, a.relevance_sample, a.seed),
    }
    ho = result["held_out"]
    result["held_out_summary"] = {
        "HIT": sum(1 for r in ho if r["verdict"] == "HIT"),
        "MISS": sum(1 for r in ho if r["verdict"] == "MISS"),
        "UNCHECKED": sum(1 for r in ho if r["verdict"] == "UNCHECKED"),
        "checked": sum(1 for r in ho if r["verdict"] != "UNCHECKED"),
        "total_rows": len(ho),
    }
    if a.bash_audit:
        audit = bash_audit(files, a.bash_audit_n, a.seed)
        result["bash_audit_summary"] = {k: v for k, v in audit.items() if k != "rows"}
        Path(a.bash_audit).write_text(render_bash_audit(audit), encoding="utf-8")

    if a.json_out:
        Path(a.json_out).write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    if a.md_out:
        Path(a.md_out).write_text(render_md(result), encoding="utf-8")
    summary = {k: v for k, v in result.items()
               if k in ("trigger_coverage", "false_positive", "held_out_summary", "context_cost",
                        "bash_audit_summary")}
    summary["population"] = result["population"]
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    return 0


def render_bash_audit(a: dict) -> str:
    L = [f"# Bash trigger audit — {a['n_sampled']} real Bash calls, shipped detector", "",
         f"Detected a literal write target in **{a['n_detected']}** of {a['n_sampled']} "
         f"({a['detection_rate']:.1%}). Mechanically suspect: **{a['n_mechanically_suspect']}**.",
         "", "A reviewer judges these; the `mechanical` column is only the cheap half.", ""]
    for i, r in enumerate(a["rows"], 1):
        L.append(f"{i}. `{r['command']}`")
        for v in r["targets"]:
            L.append(f"   - → `{v['target']}` ({v['how']}, {v['mechanical']})")
    return "\n".join(L) + "\n"


def render_md(r: dict) -> str:
    p, s, fp, cc, hs = (r["population"], r["settings"], r["false_positive"],
                        r["context_cost"], r["held_out_summary"])
    tc = r["trigger_coverage"]
    L = [f"# lesson_push replay (design 2, citation matcher) — {p['window'][0]} → {p['window'][1]}", "",
         f"Population: **{p['n_transcripts_in_window']} transcripts**, **{p['n_writes']} writes** "
         f"({p['writes_by_trigger']}), vault `{p['vault']}`. Per-fire **{s['per_fire']}**, "
         f"per-session **{s['per_session']}**, seed {s['seed']}.", "",
         "## 0. Trigger coverage", "",
         f"- Edit/Write/MultiEdit calls: **{tc['edit_tool_calls']}**",
         f"- Bash calls: {tc['bash_calls']}, of which file-producing (crude) "
         f"{tc['bash_file_writing_calls_crude']}, with a LITERAL target the shipped detector sees "
         f"**{tc['bash_calls_with_a_literal_target']}**",
         f"- share of file-producing calls visible — design 1 (edit only): "
         f"**{tc['share_visible_design1_edit_only']:.1%}**; design 2 (both): "
         f"**{tc['share_visible_design2_both']:.1%}**", "",
         "## 1. False-positive rate (replaces design 1's noise floor)", "",
         f"- SIGNAL — real writes that fire: **{fp['signal_fire_rate']:.1%}** (n={fp['n_sampled']})",
         f"- MATCHED CONTROL — same index, foreign write's tokens: "
         f"**{fp['matched_control_fire_rate']:.1%}**",
         f"- separation ratio: **{fp['separation_ratio']}**",
         f"- matching class, signal: `{fp['signal_match_class']}`",
         f"- matching class, control: `{fp['control_match_class']}`", "",
         "## 2. Held-out replay over the RE-OCCURRENCE rows", "",
         f"**{hs['HIT']} HIT / {hs['MISS']} MISS / {hs['UNCHECKED']} UNCHECKED** of "
         f"{hs['total_rows']} rows (**{hs['checked']} checkable**; design 1 could check 6).", "",
         "| lesson | class | verdict | rank | matched on | triggers | why |", "|---|---|---|---|---|---|---|"]
    for h in r["held_out"]:
        rank = "" if h.get("best_rank") is None else h["best_rank"] + 1
        L.append(f"| `{h['file']}` — {h['heading'][:60]} | {h['class']} | **{h['verdict']}** | "
                 f"{rank} | {h.get('match_token', '') or ''} | {','.join(h.get('triggers', []))} | "
                 f"{h.get('why', '')} |")
    L += ["", "## 3. Context cost", "",
          f"- fires on **{cc['n_fires']} of {cc['n_writes']}** writes ({cc['fire_rate']:.1%})",
          f"- lines per session: median **{cc['lines_per_session_median']}**, "
          f"mean {cc['lines_per_session_mean']}, max {cc['lines_per_session_max']} "
          f"(cap {s['per_session']})",
          f"- bytes per session: median **{cc['bytes_per_session_median']}**, "
          f"max {cc['bytes_per_session_max']}",
          f"- at bytes/4, the median session pays **{cc['share_of_boot_floor_median']:.4%}** of the "
          f"measured boot floor ({BOOT_FLOOR_MEDIAN_TOKENS:,} tokens, CTXCAP-1). A rule of thumb, "
          f"not a measured token count.", "",
          "## 4. Relevance sample", "",
          f"{len(r['relevance_sample'])} lines, drawn at random from everything that would have "
          f"fired. Judged relevant/irrelevant by a reviewer, not here.", ""]
    for i, x in enumerate(r["relevance_sample"], 1):
        L.append(f"{i}. `{x['line']}`  \n   write: `{x['write_path']}` (region {x['write_region']}, "
                 f"trigger {x['trigger']}), matched on `{x['matched_token']}`  \n"
                 f"   excerpt: {x['write_excerpt'][:200]}")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
