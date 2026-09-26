#!/usr/bin/env python3
"""boot_prompt_order.py — does the SessionStart hook run before or after the first prompt?

★ WHY THIS IS A SCRIPT AND NOT A SENTENCE. The boot arm of `lesson_push` has three possible
sources for what a session is about, and one of them — the session's OPENING PROMPT — is the only
one that differs between two sessions in the same lane on the same day. Whether the hook can see it
therefore decides whether a boot-time push can ever have session resolution. LESSONPUSH-3 answered
that with a measurement, the row's reviewer could not reproduce the exact figure from prose, and it
was right to say so: a number that decides a design and exists only in a paragraph is a number
nobody can check. This is the instrument.

WHAT IT COMPARES, and the honest limit of it. The hook writes one line to `session.log` at the END
of its run, just before it hands its facts block back. A transcript's first `type: "user"` record
carries the moment the first prompt was stored. So the delta measured here is

    (first user record) - (hook FINISHED)

which is a CONSERVATIVE reading of the question "did the hook start before the prompt existed": the
hook's start is earlier still, so a positive delta is a lower bound on the gap and a negative one is
unambiguous. Both clocks are read in their own zone and compared as instants, never as strings.

POPULATION IS AN ARGUMENT, NOT A DEFAULT. `--all` reads every project's transcripts; the default
reads one project directory. The two give different denominators over the same machine, and a
figure quoted without its population is the failure this package keeps recording.

    python3 gedaechtnis/eval/boot_prompt_order.py --all
    python3 gedaechtnis/eval/boot_prompt_order.py --project <dir> --json OUT.json
"""
from __future__ import annotations
import argparse, json, os, statistics, sys
from datetime import datetime
from pathlib import Path


def hook_times(log: Path) -> dict:
    """`session id -> the instant the hook finished`, from the session log's first row per id."""
    out: dict = {}
    try:
        with log.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                f = line.rstrip("\n").split("\t")
                if len(f) < 2:
                    continue
                try:
                    t = datetime.fromisoformat(f[0])
                except ValueError:
                    continue
                # The log stamps local time with no offset. `astimezone()` on a naive datetime
                # attaches the LOCAL zone, which is the one that wrote it; the transcript side is
                # explicitly UTC. Comparing two aware instants is the only correct form, and the
                # naive-vs-aware version of this script is off by the machine's whole UTC offset.
                out.setdefault(f[1], t.astimezone())
    except OSError:
        pass
    return out


def first_user_time(path: Path):
    """The instant this transcript's first `type: "user"` record was written, or None."""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"user"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("type") != "user":
                    continue
                ts = rec.get("timestamp")
                if isinstance(ts, str):
                    try:
                        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    except ValueError:
                        return None
    except OSError:
        return None
    return None


def measure(paths: list, hooks: dict) -> dict:
    after, before, deltas, no_hook, no_prompt = 0, 0, [], 0, 0
    for p in paths:
        sid = p.stem
        if sid not in hooks:
            no_hook += 1
            continue
        fu = first_user_time(p)
        if fu is None:
            no_prompt += 1
            continue
        d = (fu - hooks[sid]).total_seconds()
        deltas.append(d)
        after += int(d >= 0)
        before += int(d < 0)
    deltas.sort()
    n = len(deltas)
    return {
        "n_transcripts_seen": len(paths),
        "n_with_both_signals": n,
        "skipped_no_hook_row": no_hook,
        "skipped_no_user_record": no_prompt,
        "first_prompt_after_hook": after,
        "first_prompt_before_hook": before,
        "share_after": round(after / n, 4) if n else None,
        "median_delta_s": round(statistics.median(deltas), 2) if n else None,
        "p10_delta_s": round(deltas[n // 10], 2) if n else None,
        "max_delta_s": round(deltas[-1], 2) if n else None,
        "_reading": "a POSITIVE delta means the first prompt did not exist when the hook finished, "
                    "so the hook could not have read it. The hook STARTS earlier still, so this is "
                    "a lower bound on the gap. A negative delta is a session whose transcript "
                    "predated its hook run — a resume.",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=None, help="one project transcript directory")
    ap.add_argument("--all", action="store_true", help="every project directory")
    ap.add_argument("--log", default=None, help="the session log (default: the state dir's)")
    ap.add_argument("--json", dest="json_out", default=None)
    a = ap.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
    import config                                            # noqa: E402
    log = Path(os.path.expanduser(a.log)) if a.log else config.state() / "session.log"
    if not log.is_file():
        print(f"no session log at {log} — UNMEASURED, not zero", file=sys.stderr)
        return 2
    base = Path.home() / ".claude" / "projects"
    if a.all:
        paths = sorted(q for d in sorted(base.glob("*")) if d.is_dir()
                       for q in sorted(d.glob("*.jsonl")))
        label = "every project directory"
    else:
        d = Path(os.path.expanduser(a.project)) if a.project else base / str(
            Path.cwd()).replace("/", "-")
        paths = sorted(d.glob("*.jsonl"))
        label = str(d)
    if not paths:
        print(f"no transcripts under {label} — UNMEASURED, not zero", file=sys.stderr)
        return 2
    out = {"population": label, "session_log": str(log), **measure(paths, hook_times(log))}
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
