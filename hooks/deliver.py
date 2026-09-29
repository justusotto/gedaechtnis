#!/usr/bin/env python3
"""deliver.py — hand a session the messages the resume gate parked for it (CONTEXTMSG-1).

WHY. The resume gate refuses a message that would wake an idle session at a cost a fresh start
avoids, and parks it in `parked-<name>.jsonl` (`fleet.park`). Nothing ever read that file back to
its recipient: the only reader was a one-line count at session start, keyed by the process's
`--name`, which a resumed session does not carry. A session that compacted and went on working was
never told what its peers had sent it.

WHEN. SessionStart (`startup`, `resume`, `compact`) and every UserPromptSubmit. The first moment
the session has room again — a new window, a compacted one, or simply the next prompt — is when it
reads what waited for it.

HOW, in the order the steps run:
  1. The recipient's name is looked up by session id in the session registry, where the gate reads
     the target's name; the process's `--name` is the fallback (`fleet.session_name_of`).
  2. The parked file is CLAIMED by an atomic rename, then read under the lock `fleet.park` writes
     under. A message parked after the rename lands in a NEW parked file and waits for the next
     delivery; one whose write was already in flight finishes before the read (a park that finds its
     open file is no longer the one at the path writes again to the new one). A claim file left behind by
     a delivery that died is picked up by the next one.
  3. Messages are shown newest first, each in a frame that says who wrote it, when, and that it is a
     peer's report — not the owner's word, possibly superseded. At most `MAX_CHARS` characters are
     shown per delivery; what does not fit is put back and comes at the next one. A message older
     than `OLD_S` is not shown as text: it is named in one list line with the file it was moved to.
  4. What was shown or listed is appended to `parked-<name>.delivered.jsonl`; the claim is removed;
     only then is the text printed. KNOWN GAP, accepted: a hook killed between that mark and the
     print (a timeout) leaves a line marked delivered that was never shown; it is still in the
     delivered file. Printing first would break the fail-open rule that an error prints nothing.

FAIL OPEN. A hook that breaks here breaks every session's start and every prompt. Any error prints
nothing, exits 0, writes one line to `deliver.log`, and puts every claimed line back where the next
delivery finds it: unread, never lost. A repeated delivery after a half-finished mark is the
accepted cost of that direction; a lost message is not.
"""
from __future__ import annotations
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config                                          # light; `fleet` and `procs` wait for a parked file

MAX_CHARS = 6000
OLD_S = 48 * 3600
CLAIM = ".claim-"


def _pending(state: Path) -> bool:
    """Cheap first look, run on every prompt: is there any parked file or claim at all?"""
    try:
        for p in state.glob("parked-*"):
            if not p.name.endswith(".delivered.jsonl"):
                return True
    except OSError:
        return False
    return False


def log(name: str, line: str) -> None:
    import common                                      # noqa: PLC0415
    common.log(name, line)


def _read_locked(path: Path) -> str:
    """Read a claim under the park lock, so a write already in flight lands before the read."""
    import common                                      # noqa: PLC0415
    with path.open("r+", encoding="utf-8") as fh:
        common.lock_file(fh)
        try:
            return fh.read()
        finally:
            common.unlock_file(fh)


def _claim_owner(p: Path) -> int | None:
    try:
        return int(p.name.rsplit(CLAIM, 1)[1].split("-", 1)[0])
    except (IndexError, ValueError):
        return None


def _claim(live: Path, pid: int) -> list[Path]:
    """Rename the parked file, and any claim a dead delivery left, to claims of THIS process."""
    got: list[Path] = []
    n = 0
    try:
        leftovers = sorted(live.parent.glob(live.name + CLAIM + "*"))
    except OSError:
        leftovers = []
    for old in leftovers:
        owner = _claim_owner(old)
        import procs                                   # noqa: PLC0415
        if owner == pid or (owner is not None and procs.pid_alive(owner)):
            continue                                   # a live delivery's claim is its own
        n += 1
        mine = live.with_name(f"{live.name}{CLAIM}{pid}-{n}")
        try:
            os.rename(old, mine)
            got.append(mine)
        except OSError:
            continue                                   # another delivery took it first
    n += 1
    mine = live.with_name(f"{live.name}{CLAIM}{pid}-{n}")
    try:
        os.rename(live, mine)
        got.append(mine)
    except FileNotFoundError:
        pass
    return got


def _restore(live: Path, claims: list[Path]) -> None:
    """Put every claimed line back where the next delivery reads it. The single-claim case with no
    new parked file is a rename back: the file is then exactly as it was."""
    if len(claims) == 1:
        try:
            os.link(claims[0], live)                   # never replaces a file parked meanwhile
            claims[0].unlink()
            return
        except FileExistsError:
            pass
    import fleet                                       # noqa: PLC0415
    fleet.append_lines(live, "".join(c.read_text(encoding="utf-8") for c in claims))
    for c in claims:
        c.unlink()


def _epoch(at) -> float | None:
    try:
        return time.mktime(time.strptime(str(at), "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, OverflowError):
        return None


def _frame(r: dict) -> str:
    return (f"--- parked message from {r.get('from') or '?'}, written {r.get('at') or '?'}: a peer's "
            f"report, not the owner's word; it may be superseded, check the disk before acting. "
            f"(Refused because: {r.get('reason') or '?'}.)\n{r.get('text') or ''}")


def _more(n: int, live: Path) -> str:
    return f"{n} more parked message(s) wait in {live}; they come at the next prompt."


def compose(name: str, rows: list[dict], now: float, delivered_at: Path, live: Path,
            budget: int = MAX_CHARS) -> tuple[str | None, list[int]]:
    """(text, indexes of `rows` it delivers). Pure; `rows` are parked rows in file order."""
    old, recent = [], []
    for i, r in enumerate(rows):
        t = _epoch(r.get("at"))
        (old if t is not None and now - t > OLD_S else recent).append(i)
    recent.sort(key=lambda i: (_epoch(rows[i].get("at")) or now, i), reverse=True)   # newest first
    head = (f"Parked messages for {name}: the resume gate refused to wake this session when they "
            f"were sent. Newest first.")
    budget -= 1 + len(_more(len(rows), live))          # room kept for the "more wait" line
    parts, used, taken = [head], len(head), []
    for i in recent:
        block = _frame(rows[i])
        if used + 1 + len(block) > budget:
            break                                      # newest first: the older ones wait
        parts.append(block)
        used += 1 + len(block)
        taken.append(i)
    listed = []
    if old:
        lead = (f"Older than {OLD_S // 3600} hours, not shown as text (moved to {delivered_at}): ")
        items, size = [], used + 1 + len(lead)
        for i in sorted(old, key=lambda i: _epoch(rows[i].get("at")) or 0, reverse=True):
            item = f"from {rows[i].get('from') or '?'} at {rows[i].get('at')}"
            if size + len(item) + 2 > budget:
                break
            items.append(item)
            size += len(item) + 2
            listed.append(i)
        if items:
            parts.append(lead + "; ".join(items) + ".")
            used = size
    left = len(rows) - len(taken) - len(listed)
    if left:
        parts.append(_more(left, live))
    if not taken and not listed:
        return None, []
    return "\n".join(parts), taken + listed


def deliver(sid: str | None, now: float | None = None) -> str | None:
    """The text to show this session, with the shown lines already marked delivered; or None.
    Raises only after every claimed line is back in place."""
    state = config.state()
    if not _pending(state):
        return None
    import fleet                                       # noqa: PLC0415 — only when something is parked
    name = fleet.session_name_of(sid)
    if not name:
        return None
    now = time.time() if now is None else now
    live, done = fleet.parked_path(name), fleet.delivered_path(name)
    claims = _claim(live, os.getpid())
    if not claims:
        return None
    try:
        raw = []
        for c in claims:
            raw.extend(l for l in _read_locked(c).splitlines() if l.strip())
        rows, keep = [], []                            # keep: lines this hook does not own
        for line in raw:
            try:
                r = json.loads(line)
            except ValueError:
                r = None
            if isinstance(r, dict) and r.get("schema") == fleet.PARKED_SCHEMA:
                rows.append((line, r))
            else:
                keep.append(line)
        text, idx = compose(name, [r for _l, r in rows], now, done, live)
        chosen = set(idx)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))
        if chosen:
            with done.open("a", encoding="utf-8") as fh:
                for i in sorted(chosen):
                    fh.write(json.dumps(dict(rows[i][1], delivered_at=stamp, delivered_to=sid),
                                        ensure_ascii=False) + "\n")
        back = keep + [l for i, (l, _r) in enumerate(rows) if i not in chosen]
        if back:
            fleet.append_lines(live, "\n".join(back) + "\n")
        for c in claims:
            c.unlink()
    except Exception:
        try:
            _restore(live, [c for c in claims if c.exists()])
        except Exception as exc:                       # the claims stay; the next delivery takes them
            log("deliver", f"restore-failed\t{name}\t{type(exc).__name__}: {exc}")
        raise
    log("deliver", f"delivered\t{name}\tsid={sid}\tshown={len(chosen)}\tback={len(back)}")
    return text


def main() -> None:
    import common                                      # noqa: PLC0415
    inp = common.read_input()
    event = inp.get("hook_event_name")
    event = event if isinstance(event, str) and event else "SessionStart"
    try:
        text = deliver(inp.get("session_id"))
    except Exception as exc:                           # FAIL OPEN: nothing printed, lines unread
        log("deliver", f"error\t{type(exc).__name__}: {exc}")
        text = None
    if text:
        common.context(event, text)


if __name__ == "__main__":
    try:
        main()
    except Exception as _exc:                          # read_input or printing: still exit 0
        try:
            log("deliver", f"error\t{type(_exc).__name__}: {_exc}")
        except Exception:
            pass
    sys.exit(0)
