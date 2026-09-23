#!/usr/bin/env python3
"""graduate.py — give a region a Boot file: measure, assemble from sections, refuse, apply. stdlib only.

    python3 graduate.py --check <Region>
    python3 graduate.py --assemble <Region> --from <sections.json>
    python3 graduate.py --apply <Region> --draft <path>

`bootfile.py` decides WHEN a region needs a Boot file and says so in the facts line. Row GRADPATH-1
made the cleanup pass name the remedy. **This is the remedy**, and it is the half that was missing:
until it existed, a fresh install over budget was told to write a `Kernel.md` by a package that
could not help it write one.

## The three separations, and why each is load-bearing

**The script measures, the agent judges, the script moves.** Same shape as the split organ, for the
same reason: what a machine can count it counts, what needs a reading of the content is a model's,
and the only thing that touches the vault is deterministic code with a receipt.

**The agent returns SECTIONS, never a file.** It answers with JSON — `at_a_glance`, `resume_point`,
`state_table`, `standing_constraints`, `canon_headlines`, `open_questions`, `pointer_map` — and the
assembler renders the template. The window markers are placed BY CONSTRUCTION around
`resume_point`, so **the agent never sees or writes a marker and a Boot file without a closed window
cannot come out of this path.** That is not tidiness: `bootfile.roll_window` refuses to compact a
file that does not declare one, so a drafter that forgot the marker would produce exactly the file
the whole mechanism cannot help, which is the state the fleet has been in for a fortnight.

**The coverage check is a FLOOR under the agent, not a review of it.** Every `Never`/`Always` line
in the region's rule-carrying bodies must appear in `standing_constraints`, matched on its
normalised first eight words. Lexical, deterministic, no judgment. It is what makes a Sonnet
drafter safe to ship: the one failure that actually matters here is a binding rule silently ceasing
to be loaded — the file a session boots on is REPLACED by this draft — and a model is exactly the
wrong instrument to be the last line of defence against its own omission.

## What this never does

It never writes a Boot file unasked; `--apply` is a separate act with a path the person can edit
first. It never touches an import block it did not write — an unmarked or hand-edited block is
printed, not rewritten (exit 7). It never applies a draft whose bodies have moved since it was
made: approval is for a distillation of a STATE, and a moved state is a different thing. And it
deletes nothing, ever.
"""
from __future__ import annotations

import argparse, json, re, subprocess, sys, unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))
import archive                                                            # noqa: E402
import bootfile                                                           # noqa: E402
import config                                                             # noqa: E402
import limits                                                             # noqa: E402
import maintenance                                                        # noqa: E402
import rootguard                                                          # noqa: E402

BOOT_FILE = bootfile.BOOT_FILE
RECEIPT_NAME = "GRADUATION-RECEIPT.md"

# The block `init.py` writes into a repo's CLAUDE.md, and the ONLY region of that file this script
# will ever rewrite. A block without both markers was written by a person or by an older install,
# and `--apply` prints the lines to paste instead of editing it.
IMPORTS_OPEN = "<!-- gedaechtnis:imports -->"
IMPORTS_CLOSE = "<!-- gedaechtnis:/imports -->"

# The sections the agent returns, in the order they are rendered. A missing key is a refusal, not a
# blank section: a Boot file silently short one part is the failure this whole file guards against.
SECTIONS = ("at_a_glance", "resume_point", "state_table", "standing_constraints",
            "canon_headlines", "open_questions", "pointer_map")

# Bodies whose Never/Always lines the draft MUST carry. Not every role file: an Errata entry saying
# "never trust the timestamps" is a lesson about a tool, and hauling every one of those into an
# always-loaded index is how the index becomes the thing it replaced.
#
# ★ `Canon.md` FIRST AND ALWAYS, because it is one of the SIX role files this package actually
# creates. The first version of this line read `("Nomos.md", "Canon.md")` — Nomos is a role the
# vault this grew from uses and the package does NOT ship, so in a stranger's install the check
# would have scanned one file that exists and one that never does. That is not merely a missing
# file: a coverage check whose corpus is half absent still returns "covered", and a vacuous green
# is the one result this check must never be able to give. Optional extras are scanned when
# PRESENT and are never assumed.
RULE_BODIES = ("Canon.md", "Nomos.md")

# ★ A RULE DOES NOT HAVE TO START A LINE, and anchoring to `^` made this check answer "covered"
# on a real region whose rules are the whole reason it has any. That region's Nomos says
# "... while working this arc. **Never add them to any @-import block**, and never quote their
# contents into a file that is imported" — two binding rules about the very mechanism a Boot file
# IS, both mid-line, both invisible to a start-of-line matcher, and the check passed in silence.
#
# So the token is matched at the start of a line OR after a clause boundary: sentence punctuation,
# a dash, a semicolon, or an opening emphasis marker. This over-matches — a sentence ABOUT rules
# ("an always/never rule goes in X") is caught too — and that is the deliberate direction: a false
# refusal costs one more agent run, and a false pass costs a binding rule that silently stops being
# loaded. `--check` prints the matched lines so the cost is visible before anything is drafted.
# CASE-INSENSITIVE, which the first two versions were not — and the same real region showed why:
# its rules are written "**Never add them ...**, and never quote ...", so the second half of one
# sentence is lower-case and was invisible while the first half matched. A rule does not stop
# binding when its author stops capitalising it, any more than when they stop shouting it.
RULE_TOKEN = re.compile(
    r"(?:^|[.;:!?]\s+|[—–]\s*|,\s+and\s+|\*\*)[\s>*_#\-]*\**\s*(NEVER|ALWAYS)\b",
    re.M | re.I)
COVERAGE_WORDS = 8


def _norm_words(text: str, n: int | None = COVERAGE_WORDS) -> str:
    """The first `n` words of a rule line, normalised for comparison and nothing else.

    Case, punctuation, emphasis markers and unicode form are all dropped: a draft that carries a
    rule faithfully but writes `**Never** rewrite` where the body wrote `NEVER rewrite —` has
    carried the rule. What is NOT dropped is the words themselves, in order."""
    t = unicodedata.normalize("NFKC", text)
    t = re.sub(r"[`*_\[\]()<>#>\-—–:;,.!?\"'/\\]", " ", t)
    words = [w for w in t.lower().split() if w]
    return " ".join(words if n is None else words[:n])


def rule_lines(region: Path) -> list[dict]:
    """Every binding line in the region's rule-carrying bodies, with its source.

    A line COUNTS when it begins with a Never/Always token once leading emphasis, quoting and list
    markers are stripped — bold or not, because a rule does not stop binding when its author stops
    shouting."""
    out = []
    for name in RULE_BODIES:
        body = region / name
        if not body.is_file():
            continue
        try:
            text = body.read_text(encoding="utf-8")
        except OSError:
            continue
        for raw in text.splitlines():
            m = RULE_TOKEN.search(raw)
            if m:
                # The key starts at the TOKEN, not at the start of the line: a rule buried after a
                # clause is the same rule, and keying on the line's opening words would make a
                # faithful draft of it fail to match.
                out.append({"file": name, "line": raw.strip(),
                            "key": _norm_words(raw[m.start(1):])})
    return out


def coverage(region: Path, sections: dict) -> list[dict]:
    """Rule lines the draft does not carry. Empty means covered.

    A rule is covered when SOME constraint in the draft CONTAINS its first eight words, in order.
    Consecutive words, never fuzzy or bag-of-words: a match this check cannot justify is a match
    that lets an omission through.

    ★ CONTAINS, not starts-with, and the live control is what taught that. The first version
    compared each constraint's own first eight words against the rule's, which works only when a
    constraint opens with the rule. A real draft does not write like that: it carries a rule inside
    a sentence that sets it up ("... they are read only by explicitly opening the file; **Never add
    them to any @-import block**, and never quote ..."). All three of a real region's rules were
    carried faithfully and all three were reported missing — a FALSE REFUSAL, which is the
    survivable direction but would have sent every honest draft round the loop for ever."""
    carried = [_norm_words(s, n=None) for s in sections.get("standing_constraints") or []]
    out = []
    for r in rule_lines(region):
        if not r["key"]:
            continue
        if not any(r["key"] in c for c in carried):
            out.append(r)
    return out


# ------------------------------------------------------------------ check ----
def worthiness(vault: Path, region: Path) -> dict:
    """Is this region ACTIVELY WORKED, and is its churn in one file?

    A person reads this before anything is drafted. Graduating a dormant region spends a judgment
    on a room nobody is in; graduating one whose every commit is a `Position.md` state update
    produces an index that is stale the day after it is written. Neither is a refusal — both are
    facts a reader should have first, which is why this prints and never decides."""
    rel = bootfile._rel(vault, region)
    out = {"region": rel, "commits_30d": None, "position_share": None, "checked": False}
    try:
        p = subprocess.run(["git", "-C", str(vault), "log", "--since=30.days",
                            "--format=%H", "--name-only", "--", rel],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return out
    if p.returncode != 0:
        return out
    shas, position = set(), 0
    sha = None
    for line in p.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.fullmatch(r"[0-9a-f]{40}", line):
            sha = line
            shas.add(sha)
        elif line.endswith("Position.md"):
            position += 1
    out.update(commits_30d=len(shas), position_touches=position, checked=True,
               position_share=(position / len(shas)) if shas else None)
    return out


def check(vault: Path, region_rel: str) -> dict:
    region = vault / region_rel
    total, members = bootfile.region_payload(region)
    rules = rule_lines(region)
    return {"region": region_rel, "bytes": total, "largest": members,
            "boot_file": (region / BOOT_FILE).is_file(),
            "boot_file_budget": bootfile.budget(),
            "chain_budget": bootfile.chain_budget(),
            "rules": len(rules),
            "rule_lines": [r["line"] for r in rules],
            "rule_bodies_present": [n for n in RULE_BODIES if (region / n).is_file()],
            "worthiness": worthiness(vault, region)}


def render_check(c: dict) -> str:
    w = c["worthiness"]
    out = [f"{c['region']}: {c['bytes']:,} B of memory in {len(c['largest'])} largest file(s); "
           f"Boot file {'PRESENT' if c['boot_file'] else 'absent'}.",
           "  largest: " + ", ".join(f"{m['path']} ({m['bytes']:,} B)" for m in c["largest"]),
           f"  binding rule lines a draft must carry: {c['rules']} "
           f"(from {', '.join(c['rule_bodies_present']) or 'no rule-carrying body at all'})"]
    for line in c["rule_lines"][:20]:
        out.append(f"      · {line[:120]}")
    if c["rule_bodies_present"] and not c["rules"]:
        # Not a refusal — a region may honestly have no Never/Always rule. But it is NEVER reported
        # as "covered": zero found and zero to find are the same output and opposite facts.
        out.append("  ★ ZERO rule lines were FOUND in a body that exists. The coverage check will "
                   "have nothing to check, which is not the same as the draft being verified — "
                   "read the bodies yourself before applying.")
    if not w["checked"]:
        out.append("  WORTHINESS: UNCHECKED — the vault's git could not be read. Not zero commits; "
                   "no answer at all.")
    else:
        share = w["position_share"]
        out.append(f"  WORTHINESS: {w['commits_30d']} commit(s) in the last 30 days"
                   + (f", {share:.0%} of them touching Position.md" if share is not None else ""))
        if w["commits_30d"] == 0:
            out.append("  → DORMANT. Distilling a region nobody is working spends a judgment on a "
                       "room nobody is in.")
        elif share is not None and share >= 0.9:
            out.append("  → CHURNING STATE. Nearly every commit is a state update, so an index "
                       "written today is stale tomorrow. A Boot file is not what this needs.")
    return "\n".join(out)


# --------------------------------------------------------------- assemble ----
def _fence(items, bullet="- "):
    return "\n".join(f"{bullet}{s}" for s in items)


def render_boot_file(region_rel: str, s: dict) -> str:
    """The template. The window markers go around `resume_point` and nowhere else."""
    rows = "\n".join(f"| {r} |" for r in s["state_table"]) if s["state_table"] else "| — |"
    return f"""# {region_rel} — Boot file

> Always loaded. It REPLACES this region's bodies in what a session reads at boot; the bodies stay
> on disk and are read on demand. Rules are carried here in operative form; the reasoning stays in
> the body the pointer map names.

## At a glance

{_fence(s["at_a_glance"])}

## Resume point

{bootfile.WINDOW_OPEN}

{_fence(s["resume_point"])}

{bootfile.WINDOW_CLOSE}

## State

| what |
|---|
{rows}

## Standing constraints

{_fence(s["standing_constraints"])}

## Canon headlines

{_fence(s["canon_headlines"])}

## Open questions

{_fence(s["open_questions"])}

## Pointer map

{_fence(s["pointer_map"])}
"""


def body_shas(vault: Path, region_rel: str) -> dict:
    """The last commit touching each watched body, at draft time. `--apply` refuses if any body has
    moved to a commit the draft's own record cannot reach."""
    out = {}
    for name in bootfile.WATCHED_BODIES:
        body = vault / region_rel / name
        if body.is_file():
            out[name] = bootfile._last_commit(vault, f"{region_rel}/{name}")
    return out


def assemble(vault: Path, region_rel: str, sections: dict, state_dir: Path) -> dict:
    """Render the draft, run the coverage check, and REFUSE rather than write a short one."""
    region = vault / region_rel
    missing_keys = [k for k in SECTIONS if k not in sections]
    if missing_keys:
        return {"ok": False, "exit": 9, "why": f"the sections JSON is missing {', '.join(missing_keys)}",
                "missing_rules": []}
    if not isinstance(sections.get("resume_point"), list) or not sections["resume_point"]:
        return {"ok": False, "exit": 9, "missing_rules": [],
                "why": "resume_point is empty — a declared window with nothing in it can never roll, "
                       "so the Boot file would be uncompactable from the day it is written"}
    rules = rule_lines(region)
    scanned = [n for n in RULE_BODIES if (region / n).is_file()]
    if not scanned:
        # A region with none of the rule-carrying bodies cannot have its constraints checked, and a
        # check that cannot run reports UNCHECKED rather than passing. A silent pass here is how a
        # draft missing every rule in the region ships looking verified.
        return {"ok": False, "exit": 9, "missing_rules": [],
                "why": f"none of {', '.join(RULE_BODIES)} exists in {region_rel}, so the "
                       f"constraint-coverage check could not run at all. That is UNCHECKED, not "
                       f"covered — the draft is not written."}
    if rules and not (sections.get("standing_constraints") or []):
        return {"ok": False, "exit": 9, "missing_rules": [r["line"] for r in rules],
                "why": f"the region's bodies carry {len(rules)} binding rule line(s) and the draft "
                       f"carries none"}
    missing = coverage(region, sections)
    if missing:
        return {"ok": False, "exit": 9, "missing_rules": [r["line"] for r in missing],
                "why": f"{len(missing)} binding rule line(s) in {', '.join(RULE_BODIES)} are not "
                       f"carried by standing_constraints"}
    text = render_boot_file(region_rel, sections)
    size = len(text.encode("utf-8"))
    if size > bootfile.budget():
        return {"ok": False, "exit": 9, "missing_rules": [],
                "why": f"the draft is {size:,} B, over the {bootfile.budget():,} B Boot-file budget"}
    out = state_dir / "drafts" / region_rel.replace("/", "-") / BOOT_FILE
    rootguard.permit(out, "graduation draft")
    out.parent.mkdir(parents=True, exist_ok=True)
    archive.atomic_write(out, text)
    meta = out.with_suffix(".json")
    archive.atomic_write(meta, json.dumps(
        {"region": region_rel, "bytes": size, "body_shas": body_shas(vault, region_rel),
         "rules_covered": len(rules)}, indent=1))
    warn = bootfile.warn_bytes()
    return {"ok": True, "exit": 0, "draft": str(out), "meta": str(meta), "bytes": size,
            "rules_covered": len(rules), "scanned": scanned, "missing_rules": [],
            # The caller must be able to tell "checked and covered" from "nothing to check".
            "coverage_vacuous": not rules,
            "warn": size >= warn, "warn_bytes": warn}


# ------------------------------------------------------------------ apply ----
def split_imports(text: str) -> tuple[str, str, str] | None:
    """(before, block, after) for the marked import block, or None when it is not marked."""
    i = text.find(IMPORTS_OPEN)
    if i < 0:
        return None
    j = text.find(IMPORTS_CLOSE, i + len(IMPORTS_OPEN))
    if j < 0:
        return None
    nl = text.find("\n", i + len(IMPORTS_OPEN))
    start = len(text) if nl < 0 else nl + 1
    if start > j:
        return None
    return text[:start], text[start:j], text[j:]


def freshness(vault: Path, region_rel: str, drafted: dict) -> list[str]:
    """Bodies that have moved since the draft was made, by ANCESTRY.

    Deviation 60's rule transposed: approval is for a distillation of a STATE. Timestamps are not
    used for the reason `bootfile.stale_boot_files` documents — this package's own hook commits
    several files inside one second, so a same-second comparison silently reports fresh."""
    moved = []
    for name, then in (drafted or {}).items():
        now = bootfile._last_commit(vault, f"{region_rel}/{name}")
        if now is None or then is None or now == then:
            continue
        reach = bootfile._reachable(vault, now, then)
        if reach is None:
            moved.append(f"{name} (git could not decide reachability — UNCHECKED, never fresh)")
        elif not reach:
            moved.append(f"{name} (moved to {now[:8]} after the draft was made at {then[:8]})")
    return moved


def apply(vault: Path, region_rel: str, draft_path: Path, repo: Path, state_dir: Path) -> dict:
    """Copy the draft in, repoint the marked import block, write the receipt. One act.

    Refusals come FIRST and in order, and every one of them leaves the vault byte-identical."""
    region = vault / region_rel
    if not region.is_dir():
        return {"applied": False, "exit": 2, "why": f"{region_rel} is not a directory in the vault"}
    live = region / BOOT_FILE
    if live.is_file():
        return {"applied": False, "exit": 2,
                "why": f"{region_rel}/{BOOT_FILE} already exists. A Boot file is replaced by editing "
                       f"it, never by a second graduation."}
    try:
        text = draft_path.read_text(encoding="utf-8")
    except OSError as e:
        return {"applied": False, "exit": 2, "why": f"the draft could not be read ({e.__class__.__name__})"}
    if bootfile.split_window(text) is None:
        return {"applied": False, "exit": 9,
                "why": "the draft does not declare a CLOSED window, so nothing could ever compact "
                       "it. Re-assemble rather than hand-editing the markers in."}
    meta_path = draft_path.with_suffix(".json")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"applied": False, "exit": 9,
                "why": f"the draft's {meta_path.name} is missing or unreadable, so the bodies it was "
                       f"distilled from cannot be checked. A draft with no provenance is not applied."}
    moved = freshness(vault, region_rel, meta.get("body_shas") or {})
    if moved:
        return {"applied": False, "exit": 9, "moved": moved,
                "why": "the bodies this draft summarises have moved since it was made: "
                       + "; ".join(moved) + ". Re-check and re-assemble — approval is for a "
                       "distillation of a state, and the state has changed."}

    claude_md = repo / "CLAUDE.md"
    try:
        md = claude_md.read_text(encoding="utf-8")
    except OSError as e:
        return {"applied": False, "exit": 2,
                "why": f"{claude_md} could not be read ({e.__class__.__name__})"}
    parts = split_imports(md)
    import_line = f"@{live}"
    if parts is None:
        # ★ NOT TOUCHED. An unmarked block was written by a person or by an install older than the
        # markers, and this script has no way to tell which lines in it are ITS business. Rewriting
        # by guess is how a hand-tuned import list loses an entry with no error.
        return {"applied": False, "exit": 7, "paste": import_line,
                "why": f"{claude_md} has no marked import block ({IMPORTS_OPEN} … {IMPORTS_CLOSE}), "
                       f"so it was NOT edited. Replace this region's body imports with:\n"
                       f"    {import_line}\n"
                       f"then run this again, or add the markers around the block you want managed."}

    before, block, after = parts
    kept, dropped = [], []
    region_prefix = f"@{region}/"
    for line in block.splitlines():
        if line.strip().startswith(region_prefix) and line.strip() != import_line:
            dropped.append(line.strip())
        elif line.strip():
            kept.append(line)
    new_block = "\n".join(kept + [import_line]) + "\n"

    # ★ The repo is an explicitly DECLARED scratch root, and only for the two writes that target
    # it. `CLAUDE.md` and its backup are the user's file in the user's checkout: outside the vault,
    # the state dir, the config file and the worktrees dir, which are the only four places this
    # package writes. Without this the command raised `OutsideRoot` at the backup and died — on
    # every real install, while every test passed, because under pytest `rootguard` waves through
    # anything under the run's temp root and the fixtures put the repo there. The guard was right
    # and its message named this remedy: an explicit scratch root declared by the caller, never a
    # widened rule. It is scoped to `repo` and to these two paths; the vault writes below get no
    # scratch and are still bounded by the roots.
    rootguard.permit(live, "graduation apply")
    # ★ ONE BACKUP PER DAY WAS ONE BACKUP TOO FEW. Graduate region A, hand-edit `CLAUDE.md`,
    # graduate region B the same day: the second run found the backup present, skipped it, and
    # overwrote the file — so the surviving backup restores a state OLDER than the edits, which is
    # worse than no backup at all, because it looks like one. A backup whose content no longer
    # matches what is about to be replaced is not a backup of that thing.
    backup = claude_md.with_name(f"CLAUDE.md.pre-graduate-{maintenance.today()}")
    if backup.exists() and backup.read_text(encoding="utf-8") != md:
        n = 2
        while backup.with_name(f"{backup.name}-{n}").exists():
            n += 1
        backup = backup.with_name(f"{backup.name}-{n}")
    if not backup.exists():
        rootguard.permit(backup, "graduation backup", scratch=repo)
        archive.atomic_write(backup, md, scratch=repo)
        if backup.read_text(encoding="utf-8") != md:
            raise RuntimeError(f"the backup at {backup} does not match the file it is backing up; "
                               f"refusing to replace {claude_md}")
    archive.atomic_write(live, text)
    archive.atomic_write(claude_md, before + new_block + after, scratch=repo)

    receipt = region / RECEIPT_NAME
    rootguard.permit(receipt, "graduation receipt")
    archive.atomic_write(receipt, render_receipt(region_rel, meta, dropped, import_line, backup))
    return {"applied": True, "exit": 0, "boot_file": str(live), "receipt": str(receipt),
            "backup": str(backup), "imports_removed": dropped, "import_added": import_line,
            "bytes": len(text.encode("utf-8"))}


def render_receipt(region_rel: str, meta: dict, dropped: list[str], added: str,
                   backup: Path) -> str:
    shas = "\n".join(f"- `{k}` at `{(v or 'never committed')[:8]}`"
                     for k, v in sorted((meta.get("body_shas") or {}).items()))
    return f"""# Graduation receipt — {region_rel}, {maintenance.today()}

`{BOOT_FILE}` is now this region's Boot file. **Nothing was deleted.** Every body it summarises is
still on disk, unchanged, and is read on demand; what changed is which of them a session LOADS.

## What the draft was distilled from

{shas or "- (no watched body was committed)"}

If any of those files has moved since, this Boot file summarises a state the region has left — the
maintenance pass says so at the next session start, and it is checked by ancestry rather than by
timestamps.

## The import block, before and after

- removed: {", ".join(f"`{d}`" for d in dropped) or "(nothing — the block carried no import for this region)"}
- added: `{added}`

The previous `CLAUDE.md` is beside it as `{backup.name}`.

## Undoing this

Delete `{BOOT_FILE}`, restore the import lines listed above, and the region is exactly as it was.
Nothing else was touched.
"""


# ------------------------------------------------------------------- main ----
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Give a region a Boot file. Measures, assembles from "
                                             "sections, refuses, applies. Never drafts by itself.")
    ap.add_argument("--check", metavar="REGION")
    ap.add_argument("--assemble", metavar="REGION")
    ap.add_argument("--from", dest="from_", metavar="SECTIONS_JSON")
    ap.add_argument("--apply", metavar="REGION")
    ap.add_argument("--draft", metavar="PATH")
    ap.add_argument("--repo", metavar="PATH", default=None,
                    help="the repo whose CLAUDE.md carries the import block (default: cwd)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    vault = config.vault()
    state = config.state()
    if not vault.is_dir():
        print(f"No vault at {vault}.", file=sys.stderr)
        return 2

    if args.check:
        c = check(vault, args.check)
        print(json.dumps(c, indent=1) if args.json else render_check(c))
        return 0

    if args.assemble:
        if not args.from_:
            ap.error("--assemble needs --from <sections.json>: this script never drafts, and a "
                     "section it invented would be a summary nobody wrote")
        try:
            sections = json.loads(Path(args.from_).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"REFUSED — the sections JSON could not be read ({e.__class__.__name__}: {e})",
                  file=sys.stderr)
            return 9
        r = assemble(vault, args.assemble, sections, state)
        if args.json:
            print(json.dumps(r, indent=1))
        elif r["ok"]:
            print(f"Draft written: {r['draft']}\n"
                  f"  {r['bytes']:,} B; {r['rules_covered']} binding rule line(s) carried"
                  + ("\n  ★ the coverage check found NOTHING TO CHECK in "
                     f"{', '.join(r['scanned'])} — that is not the same as verified; read the "
                     "bodies yourself before applying" if r.get("coverage_vacuous") else "")
                  + (f"\n  WARN: at or past {r['warn_bytes']:,} B, so its window will be "
                     f"compacting from early on" if r["warn"] else "")
                  + "\n  Nothing has been applied. Read it, edit it if you want, then --apply.")
        else:
            print(f"REFUSED — {r['why']}", file=sys.stderr)
            for line in r.get("missing_rules") or []:
                print(f"    not carried: {line}", file=sys.stderr)
        return r["exit"]

    if args.apply:
        if not args.draft:
            ap.error("--apply needs --draft <path>")
        repo = Path(args.repo).resolve() if args.repo else Path.cwd()
        r = apply(vault, args.apply, Path(args.draft), repo, state)
        if args.json:
            print(json.dumps(r, indent=1))
        elif r["applied"]:
            print(f"{args.apply} has a Boot file: {r['boot_file']} ({r['bytes']:,} B)\n"
                  f"  import added:   {r['import_added']}\n"
                  f"  imports removed: {', '.join(r['imports_removed']) or '(none)'}\n"
                  f"  receipt: {r['receipt']}\n"
                  f"  previous CLAUDE.md: {r['backup']}")
        else:
            print(f"REFUSED — {r['why']}", file=sys.stderr)
        return r["exit"]

    ap.error("one of --check, --assemble or --apply")


if __name__ == "__main__":
    sys.exit(main())
