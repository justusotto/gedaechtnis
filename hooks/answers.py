#!/usr/bin/env python3
"""answers.py — apply the routes an answer router can place, and say so in plain words.

WHAT THIS IS FOR
----------------
A person answers a review page in their browser; the page downloads a JSON of their answers; the
download sits in their downloads folder. Something has to carry it back to the work it belongs to.
Measured in one vault on 2026-09-22: six answered exports had been sitting there for up to four
weeks, and the only thing that had noticed was a nightly line nobody reads.

Noticing is not carrying. This arm CARRIES — but only where there is exactly one place to carry
to. An export the router resolves to ONE arc and ONE row is applied; anything ambiguous or
unplaceable is LISTED BY NAME and left exactly where it is.

GENERIC BY CONSTRUCTION
-----------------------
This package ships with no knowledge of any particular vault's repos, arcs or queue files, and
none is added here. The router is a COMMAND this installation names in its config
(`answer_router`), exactly as `owner_pages_status` is named. A vault that configures none gets
nothing new — not a warning, not a stub line, nothing — because a vault with no review pages has
no answers to route and should not be told about a mechanism it does not use.

THE CONTRACT A CONFIGURED ROUTER MUST MEET (four keys; everything else is ignored):

    <router> --json                       -> {"to_route": [ {name, arc_name, queue_file,
                                                             row_line, …}, … ],
                                              "to_file":   [ {name, row_reason, …}, … ],
                                              "unroutable":[ {name, reason}, … ],
                                              "n_routed":  <int> }
    <router> --json --apply --only A B    -> the same object, having performed A and B
    <router> --lane-marker <path>         -> the partition that governs the row mark

`to_route` MEANS "one arc, one row" — that is the router's judgement, not this file's, and this
file never second-guesses it. `to_file` and `unroutable` are reported and never applied.

TWO THINGS THIS DELIBERATELY DOES NOT DO
----------------------------------------
1. It never applies `to_file` or `unroutable`. Filing by resemblance is the failure the whole
   mechanism exists to refuse, and a sweep that runs unattended at boot is the worst possible
   place to start guessing.
2. It never decides the lane itself. The marker path comes from the CALLER — the session's own —
   because a router invoked on behalf of another session would otherwise inherit whichever lane
   its own repo declares, and mark (or refuse) the wrong rows. DECLARED, never inferred.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TIMEOUT = 25


def _run(args, timeout=TIMEOUT):
    try:
        p = subprocess.run(args, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except (subprocess.TimeoutExpired, OSError) as e:
        return 124, "", str(e)


def _cmd(python, router, marker=None, extra=()):
    args = [str(python), str(router), "--json"]
    if marker:
        args += ["--lane-marker", str(marker)]
    return args + list(extra)


def _names(rows):
    out = []
    for r in rows or []:
        n = (r or {}).get("name")
        if n:
            out.append(n)
    return out


def _plural(n, one, many=None):
    return one if n == 1 else (many or one + "s")


def sweep(python, router, marker=None, runner=_run) -> "str | None":
    """Apply every unambiguous route and return ONE fact line, or None when there is nothing to say.

    `runner` is injectable so the tests can drive both halves without a real router on disk —
    a fixture cannot contain the conventions of the system it is a fixture for, so the tests
    exercise the CONTRACT, and the live proof is run separately against the real script.
    """
    if not router:
        return None
    rc, out, err = runner(_cmd(python, router, marker))
    if rc == 124 or not out:
        # Never silent, and never a reassuring zero: an instrument that could not run has not
        # told us there is nothing there.
        return ("- Answered pages: the configured answer router did not report "
                f"({Path(str(router)).name}, rc={rc}{'; ' + err[:120] if err else ''}); "
                "nothing was applied and nothing is known about what is waiting.")
    try:
        rep = json.loads(out)
    except json.JSONDecodeError:
        return (f"- Answered pages: {Path(str(router)).name} returned non-JSON; nothing applied, "
                "nothing measured.")

    to_route = rep.get("to_route") or []
    applied, failed = [], []
    if to_route:
        rc2, out2, err2 = runner(_cmd(python, router, marker, ["--apply", "--only", *_names(to_route)]))
        after = {}
        if out2:
            try:
                after = json.loads(out2)
            except json.JSONDecodeError:
                after = {}
        done = {r.get("name") for r in (after.get("routed") or [])}
        for r in to_route:
            (applied if r.get("name") in done else failed).append(r)

    parts = []
    for r in applied:
        where = r.get("arc_name") or "its arc"
        row = ""
        if r.get("queue_file"):
            row = " and noted the row in %s" % Path(str(r["queue_file"])).name
        parts.append("copied `%s` into `%s`%s" % (r.get("name"), where, row))

    left = []
    for r in (rep.get("to_file") or []):
        left.append("`%s` — %s" % (r.get("name"), r.get("row_reason") or "no row to mark"))
    for r in (rep.get("unroutable") or []):
        left.append("`%s` — %s" % (r.get("name"), r.get("reason") or "unplaceable"))
    for r in failed:
        left.append("`%s` — the router resolved it but the apply did not complete" % r.get("name"))

    if not parts and not left:
        n = rep.get("n_routed") or 0
        if not n:
            return None                      # nothing waiting and nothing done: say nothing
        return ("- Answered pages: nothing waiting — %d answered export%s already sits with the "
                "work it belongs to." % (n, "" if n == 1 else "s"))

    line = "- Answered pages: "
    if parts:
        line += "applied %d %s — %s." % (len(applied), _plural(len(applied), "answer"),
                                         "; ".join(parts))
    elif to_route:
        # There WAS something unambiguous and it did not go through. That is a different fact
        # from "there was nothing to do", and the two must never print the same sentence.
        line += "nothing could be applied, though %d was unambiguous." % len(to_route)
    else:
        line += "nothing to apply."
    if left:
        line += " Left for a person (%d): %s" % (len(left), "; ".join(x.rstrip(".") for x in left))
        if not line.endswith("."):
            line += "."
    return line


def main(argv=None) -> int:
    """`python3 answers.py [--marker PATH]` — the same sweep, runnable by hand."""
    import argparse
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import config                                            # noqa: E402

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--marker", help="the .atlas-lane marker whose partition governs row marks")
    a = ap.parse_args(argv)
    router = config.answer_router()
    if not router:
        print("no `answer_router` configured in %s — nothing to sweep." % config.config_path())
        return 0
    line = sweep(config.python(), router, a.marker)
    print(line or "nothing to say: no answered export is waiting.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
