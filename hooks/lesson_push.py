#!/usr/bin/env python3
"""lesson_push.py — put the lesson in front of the writer, at the moment the writer is writing.

WHY THIS EXISTS, and the measurement that bought it. `LESSONYIELD-1` (2026-09-20, repo artifact
`.orchestration/review/staletruth-2026-09-19/LESSONYIELD-2026-09-20.md`) censused 209 lessons
written into one vault over seven weeks and asked whether writing them down changed anything:

  * **recall — the PULL path — was invoked in 1 of 71 sessions (1.4%)**, strict `tool_use` count;
  * **none of the 17 re-occurrences was preceded by a recall on its topic**;
  * hook-ENFORCED lessons re-occurred at roughly half the rate of kernel-PUSHED ones (6.6% vs
    12.9%, numerators 5 and 8 — too small to rank, large enough to say pull is not the lever).

A path nobody walks cannot be the mechanism. So this module PUSHES: after a write, it names the
lessons this vault already holds ABOUT THE THING THAT WAS JUST TOUCHED. It is a NOTE, never a
refusal, and it never writes the vault.

★ TWO DESIGNS HAVE BEEN BUILT AND MEASURED HERE. THIS FILE IS THE SECOND.

  **Design 1 (LESSONPUSH-1, `ec716757`) matched SHARED VOCABULARY** between the edit and a lesson
  HEADING, scored by idf coverage × mass. Its own three gates refused it, and the numbers are kept
  in `rules/limits.json` beside `lesson_push_enabled` because they are the reason this file looks
  the way it does: the score did not SEPARATE (a matched control's median sat on the signal's
  median), there was NO held-out hit at any threshold including zero, and the trigger saw 16.9% of
  the population's file-producing tool calls.

  **Design 2 — this one — changes exactly two things**, on the owner's instruction (2026-09-20,
  `q:CU-2026-09-20-LESSONPUSH-2`):

    * the TRIGGER also fires on a **Bash command that produces a file** (`chore.py do_bash`),
      because five writes in six here go through a heredoc or a redirect and design 1 could not
      see them;
    * the MATCHER is **CITATION, not vocabulary**. A lesson is surfaced when the write touches
      something the lesson's OWN BODY cites — a path, a queue-row id, a ruling id, a backticked
      script or function name, or a heading it wikilinks. The comparison is an EXACT token match
      against an index built once per session, resolved with the same citation grammar
      `citations.py` (STALEWRITE-1) already uses on the write side.

  A citation match has no score and therefore no threshold: it either names the same thing or it
  does not. What it has instead is a FALSE-POSITIVE RATE, measured the same way design 1's floor
  was — `eval/lesson_push_replay.py` runs the identical matcher against a foreign write's tokens
  and reports how often that fires too.

WHAT IT CANNOT SEE, stated plainly because it bounds every number this module produces: a lesson
that cites nothing. A heading with a body of pure prose — no path, no id, no backticked name — is
invisible to this matcher however relevant it is. That is the honest failure mode of design 2, as
"a lesson written in another idiom" was of design 1, and it is why the default is decided by a
held-out replay rather than by how reasonable the design sounds.

BOUNDS. At most `lesson_push_max_per_fire` lines per write, at most `lesson_push_max_per_session`
lines per session, and never the same heading twice in one session. The session cap is checked
FIRST, before any file is read, so a session past its budget costs nothing at all.

EVERY SURFACED LINE IS LOGGED to the state dir's `lesson_push.log` (ts, sid, trigger, path,
heading, match). That log is the measurement surface for `SYSTEMYIELD-1`, and it lives in the
state directory rather than the vault for the reason a failure recorder always does: the recorder
must not depend on the thing it records.
"""
from __future__ import annotations
import json, re, shlex
from pathlib import Path

import citations
import common
import config
import limits
import recall
from common import log

# The three role files a lesson lives in. Errata is what went wrong, Patterns is what works, and
# Patterns-verification is the vault-specific third that holds the no-signal failure class. A stem
# absent from a region simply contributes nothing — a region with none of them is SILENT, which is
# one of this module's negative controls.
LESSON_STEMS = ("Errata", "Patterns", "Patterns-verification")

# Bounded reads. A PostToolUse hook may never be the reason a tool call feels slow.
MAX_LESSON_FILE_BYTES = 400_000
MAX_EDIT_TEXT_BYTES = 20_000

# Terms shorter than this are furniture in a technical vault ("file", "line", "code"). Used only by
# the TITLE rule below, which is design 1's and survives unchanged; the matcher itself is exact.
MIN_TERM_LEN = 5

# ★ ROLE STEMS ARE NOT CITATIONS. Every region has an `Errata.md`, a `Position.md`, a `Canon.md`;
# a lesson body naming `Errata.md` and a write to some other region's `Errata.md` have nothing to
# do with each other. A bare basename whose stem is one of these is therefore NOT a token — the
# two-segment form (`Global/Errata.md`) still is, because that one names a particular file.
#
# DERIVED from `common.ROLE_STEMS` rather than copied: the role roster is the package's, it has
# grown twice, and a second hand-maintained list of it here would be wrong the first time it did.
# The extension beneath it is this module's own and is about FURNITURE rather than roles — files
# every repo has one of, which name a role in the same way and are not part of the vault taxonomy.
_FURNITURE = ("patterns-verification", "readme", "changelog", "claude", "index", "notes", "inbox")
ROLE_STEMS = {s.lower() for s in common.ROLE_STEMS} | set(_FURNITURE)

# ★ A TOKEN THAT NAMES WHAT EVERYONE NAMES IS NOT A CITATION, and where to cut was measured on
# the real vault rather than chosen. Entries-per-token over three regions' live indices:
#
#     region A (336 tokens)  1:270  2:50  3:4  4:7  5:2  7:1  15:1  18:1
#     region B (144)         1:122  2:17  3:3  10:1  11:1
#     Global (105)           1:91   2:11  3:1  10:1  11:1
#
# Over 95% of tokens name ONE OR TWO entries — that is what a citation looks like. The tail above
# eight is furniture every arc has one of: `DEVIATIONS.md`, `report.md`, `settings.json`. A write
# to a file eleven lessons mention is not a write those eleven lessons are about, and without this
# rule the weakest class would fire most often, which is the opposite of what the ranking says.
# The cut sits ABOVE the largest real fan-out seen (a much-cited Global heading at 7), so it
# removes the furniture and nothing else on this corpus.
MAX_ENTRIES_PER_TOKEN = 8

# How specific each class of token is, lowest first. The per-fire cap picks by this, so when a
# write matches two lessons the one that matched on a QUEUE ROW ID beats the one that matched on a
# filename: an id names exactly one thing, a filename names a file many lessons may mention.
CLASS_RANK = {"q": 0, "ruling": 0, "head": 1, "path": 2, "name": 3, "file": 4}

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.M)

# An ISO date anywhere in an entry's prose. The vault this package grew from dates almost every
# lesson it writes; a vault that does not simply loses the boot arm's recency tie-break.
_ISO_DATE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")

# A path-shaped token. The extension list is closed on purpose: an open one turns every
# `word.word` in prose into a citation.
_PATHY = re.compile(
    r"(?<![\w/.~-])((?:~?[\w.@+-]+/)*[\w.@+-]+"
    r"\.(?:md|py|json|jsonl|tsv|csv|sh|txt|html|yml|yaml|toml|js|css|sql))(?![\w/])")
# A backticked script or function name: `region_claim.sh`, `score_all`, `_update`, `do_bash()`.
_BACKTICKED = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]{3,})(?:\(\))?`")
# `[[File#Heading]]` — the heading half only. The file half is a role stem in almost every case.
_WIKILINK = re.compile(r"\[\[([^\]\[|]+)\]\]")


# ------------------------------------------------------------------ vocabulary (title rule only) ----

def terms_of(text: str) -> set:
    """The package tokenizer, one length rule, no second implementation.

    `recall.terms` carries the stoplist and the character class (it is Unicode-aware — this vault
    holds Cyrillic and CJK headings); this only narrows it by length. Design 2 uses it for ONE
    thing: the title rule in `entries_of`, which asks whether a heading says anything its own
    file's path does not."""
    return {t for t in recall.terms(text) if len(t) >= MIN_TERM_LEN}


def path_words(rel: str) -> str:
    """A path as prose, so `hooks/context_cap.py` contributes `hooks`, `context`, `cap`."""
    return re.sub(r"[/_.\-]+", " ", rel)


def text_of(tool_input: dict) -> str:
    """What an Edit/Write/MultiEdit actually PUT THERE, from the tool payload.

    Written here rather than at the two call sites because the hook and the replay must read a
    payload the same way or the replay measures a different instrument than the one that ships.
    `MultiEdit` carries a list and neither of the other shapes' keys — a reader that only knew
    `new_string`/`content` would see every MultiEdit as empty and never say so."""
    if not isinstance(tool_input, dict):
        return ""
    parts = []
    for key in ("new_string", "content"):
        v = tool_input.get(key)
        if isinstance(v, str):
            parts.append(v)
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        for e in edits:
            if isinstance(e, dict) and isinstance(e.get("new_string"), str):
                parts.append(e["new_string"])
    return "\n".join(parts)


# ------------------------------------------------------------------ citation tokens ----

def norm_path_token(raw: str) -> tuple:
    """`(path_token, file_token)` for one path-shaped string — either may be None.

    ★ THE TAIL, NOT THE WHOLE PATH. The same file is written three ways in this corpus:
    `~/vault/Global/Errata.md` in a lesson body, `Global/Errata.md` in a vault-relative write, and
    an absolute path under someone's home directory in a shell command. Comparing whole strings would make
    those three different things. The last TWO segments are what a reader means by "that file":
    enough to tell `hooks/limits.py` from `rules/limits.json`, and short enough that a repo prefix
    or a home directory cannot defeat the match.

    A one-segment path (a bare filename) yields only the `file:` token, and not even that when its
    stem is a role stem — see `ROLE_STEMS`."""
    raw = raw.strip().strip("`'\"").lstrip("~")
    parts = [x for x in raw.split("/") if x not in ("", ".", "..")]
    if not parts:
        return None, None
    base = parts[-1].lower()
    stem = base.rsplit(".", 1)[0]
    file_tok = None if stem in ROLE_STEMS else f"file:{base}"
    path_tok = f"path:{'/'.join(parts[-2:]).lower()}" if len(parts) >= 2 else None
    return path_tok, file_tok


def cited_tokens(text: str, include_file: bool = True) -> set:
    """Every citation token in `text` — the whole matcher's vocabulary, both sides.

    ★ FENCES AND BLOCKQUOTES ARE EXCLUDED, by `citations.quotable_text`, and the rule is applied
    to BOTH SIDES. A token applied asymmetrically is two tokenizers, and the asymmetry is exactly
    what would make a lesson that QUOTES a command match every session that runs it. Inline
    backticks are NOT excluded — that is how a real citation is written here, and excluding them
    would switch the matcher off while looking like caution (the same call `citations.py` makes,
    for the same reason)."""
    body = citations.quotable_text((text or "")[:MAX_EDIT_TEXT_BYTES])
    out = set()
    for q in citations.QID.findall(body):
        out.add(f"q:{q.upper()}")
    for r in citations.RULING.findall(body):
        out.add(f"ruling:{r.lower()}")
    for m in _PATHY.findall(body):
        p_tok, f_tok = norm_path_token(m)
        if p_tok:
            out.add(p_tok)
        # ★ A QUALIFIED PATH ALWAYS CONTRIBUTES ITS FILENAME TOO, on both sides, and the first
        # real-vault smoke is why. One file is written two ways across this corpus — a lesson
        # says `kernel_budget_guard.py` and a session writes `scripts/kernel_budget_guard.py` —
        # and with the filename suppressed on one side the two never met. `include_file` is
        # therefore about BARE filenames in prose, which is the noise class it was added for, not
        # about filenames as such: a path that named a directory has already proved it means a
        # file rather than a word.
        if f_tok and (include_file or p_tok):
            out.add(f_tok)
    for nm in _BACKTICKED.findall(body):
        low = nm.lower()
        if low.rsplit(".", 1)[0] in ROLE_STEMS:
            continue
        if "." in low:                       # `lesson_push.py` — already a path token above
            continue
        if "_" not in low:                   # a single English word in backticks is not a name
            continue
        out.add(f"name:{low}")
    for link in _WIKILINK.findall(body):
        if "#" not in link:
            continue
        head = " ".join(link.split("#", 1)[1].split()).lower()
        if head:
            out.add(f"head:{head}")
    return out


def write_tokens(rel: str, new_text: str) -> set:
    """What the WRITE touches: the citations in what was written, plus the file it was written to.

    The target path is a token in its own right and it is usually the strongest one: a session
    editing `hooks/gate.py` is touching `hooks/gate.py` whether or not the diff says so.

    ★ A BARE FILENAME IN PROSE IS NOT A TOKEN ON THIS SIDE (`include_file=False`), and design 1
    is why. An edit is up to MAX_EDIT_TEXT_BYTES of text; a handoff or a row report names dozens
    of files in passing, and represented by all of them it touches most of the vault. So the weak
    `file:` class is contributed by the TARGET PATH only — the file actually being written — while
    the text contributes the classes that name one thing each (ids, two-segment paths, wikilinked
    headings, backticked names). The lesson side keeps `file:`, so "this lesson is about the file
    you are writing" still matches; "this lesson mentions a file your document mentions" does
    not."""
    toks = cited_tokens(new_text, include_file=False)
    p_tok, f_tok = norm_path_token(rel)
    if p_tok:
        toks.add(p_tok)
    if f_tok:
        toks.add(f_tok)
    return toks


# ------------------------------------------------------------------ the Bash trigger ----
# ★ WHY THIS EXISTS AT ALL. Design 1 rode on PostToolUse for Edit/Write/MultiEdit and measured its
# own blindness: 543 of 3,222 file-producing tool calls in the LESSONYIELD-1 population, 16.9%.
# Five writes in six in this corpus are a heredoc, a redirect or a `tee` inside a Bash call, and a
# push that cannot see them has a ceiling no matcher fixes.
#
# WHY IT IS NOT `gate.bash_write_targets`. That function answers a different question and answers
# it correctly: it returns the VAULT paths a command writes, filtered to the vault by its last
# line, because every door it feeds is a vault rule. The re-occurrences this arm was built for are
# in REPO artifacts — handoffs, row reports, deviation logs — so a vault-filtered target list is
# empty for exactly the population that matters. This detector is deliberately narrower in what it
# recognises and wider in where it looks.
#
# CONSERVATIVE, AND IN THE DIRECTION THAT COSTS THE ARM RATHER THAN THE SESSION. It takes only
# LITERAL targets: a target carrying `$`, a glob character or a shell substitution is dropped
# rather than guessed at, because a guessed path is a token that matches the wrong lesson. Every
# miss makes the arm quieter; every wrong guess would make it wrong.

_SPLIT = re.compile(r"\s*(?:\|\||&&|;|\||\n)\s*")
_UNRESOLVABLE = re.compile(r"[$*?\[\]{}!]|`")
# ★ A TARGET MUST LOOK LIKE A PATH, and three real commands in the 200-call audit are why. The
# segment splitter breaks on `|`, which is inside a quoted string as often as it is a pipe, so
# `grep -c '<<<<<<<\|>>>>>>>'` arrived here as the token `>>>>>>>'` and was read as a redirect to
# `>>>>>`; `--stop at >=98%` in a quoted note became a redirect to `=98%.`. Both are the same
# defect — a shell-quoting artefact reaching the target slot — and a charset the shell would
# accept in a filename but a quoting artefact never survives removes the class without a parser.
_PATHLIKE = re.compile(r"^[\w./@+~-]+$")
_DEV = ("/dev/", "/proc/", "/sys/")
# `python - <<'PY' ... open("out.json", "w") ... PY` — the interpreter writes, the shell does not,
# so no redirect appears anywhere in the command. The literal path inside the `open()` is the only
# statement of intent there is, and it is a statement: a quoted string with a mode that writes.
_OPEN_WRITE = re.compile(r"""open\(\s*(['"])([^'"]+)\1\s*,\s*(['"])(?:w|a|wb|ab|w\+|x)\3""")
# `cmd <<'EOF' … EOF` — the body is DATA, not command text, and it must be removed before the
# target parse. A markdown blockquote inside a heredoc body opens with `>` at the start of a line,
# and a parser that did not know it was inside a body would read that as a redirect to the next
# word — a wrong path, in a document about lessons, cited to a lesson. The body is kept for the
# CITATION side (it is what was written) and dropped from the TARGET side.
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def strip_heredocs(cmd: str) -> str:
    """The command with every heredoc BODY removed, line structure preserved."""
    lines = (cmd or "").split("\n")
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = _HEREDOC.search(line)
        i += 1
        if not m:
            continue
        term = m.group(2)
        while i < len(lines) and lines[i].strip() != term:
            i += 1
        if i < len(lines):
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def _literal(tok: str) -> bool:
    return (bool(tok) and not _UNRESOLVABLE.search(tok) and not tok.startswith(_DEV)
            and bool(_PATHLIKE.match(tok)))


def bash_targets(cmd: str, cwd: str | None) -> list:
    """`[(Path, how)]` — the files a Bash command literally produces. `how` in
    redirect · redirect-append · tee · tee-append · cp · mv · install · python-open.

    Not a security boundary and not a completeness claim: it is the trigger for a NOTE. A command
    that writes from inside a script it invokes is invisible here, and that is recorded rather than
    patched over — the coverage figure the replay reports is an upper bound because of it."""
    out = []
    cur, stack = cwd, []
    for seg in _SPLIT.split(strip_heredocs(cmd)):
        seg = seg.strip()
        if not seg:
            continue
        try:
            w = shlex.split(seg)
        except ValueError:
            w = seg.split()
        # ★ A GROUP'S `cd` DOES NOT OUTLIVE THE GROUP, and the flat tracker below said it did.
        # Found by this row's reviewer, not by the audit: `( cd /other-repo && cat x ) && cat >
        # bar.md` resolved `bar.md` into the OTHER repo, because `cur` persisted past the
        # subshell's closing paren. That is the same "named a file in another repo" class the `cd`
        # tracking was added to close, arriving through the one shape the 200-call sample happened
        # not to contain. A depth stack costs two lines and answers correctly: the cwd a group
        # inherits is restored when the group ends.
        for tok in w:
            if tok in ("(", "{"):
                stack.append(cur)
            elif tok in (")", "}") and stack:
                cur = stack.pop()
        w = [t for t in w if t not in ("(", ")", "{", "}")]
        # ★ `cd` IS TRACKED ACROSS SEGMENTS, and the 200-call audit is why: nine of the nineteen
        # detections in it were `cd <worktree> && cat > <relative path>`, which without this
        # resolved against the wrong root and named a file in another repo. `gate.py` has tracked
        # `cd` for the same reason since its own door was written — "an ordinary shape a person
        # types", and a guard that answers about the wrong file is worse than one that says it
        # does not know.
        _w = list(w)
        while _w and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", _w[0]):
            _w = _w[1:]
        if _w and _w[0] == "cd":
            args = [a for a in _w[1:] if a != "--"]
            if not args:                            # bare `cd` is HOME, which is knowable
                cur = str(common.HOME)
            elif args[0] == "-":
                # `cd -` returns to $OLDPWD, which this module does not track. Read as a directory
                # literally named `-` it produced paths like `/tmp/-/out.txt` — a confident wrong
                # answer, which this detector's whole rule is that it must never give. UNKNOWN.
                cur = None
            elif cur is not None and _literal(args[0]):
                cur = str(expand_path(args[0], cur))
            else:
                cur = None
            continue
        if cur is None:
            # ★ AN UNRESOLVABLE RELATIVE TARGET IS SKIPPED, NOT GUESSED. After an untracked `cd`
            # the cwd is unknown, and a relative path resolved against the session's own root is a
            # confident wrong answer — the one outcome worse than silence for a matcher that keys
            # on file identity. Absolute targets are unaffected.
            w = [x for x in w if x.startswith(("/", "~")) or ">" in x or "=" in x]
        for i, tok in enumerate(w):
            m = re.match(r"^(?:\d+|&)?(>>|>)\|?(.*)$", tok)
            if not m or tok.startswith(">&") or re.match(r"^\d+>&", tok):
                continue
            target = m.group(2) or (w[i + 1] if i + 1 < len(w) else "")
            if _literal(target) and target not in ("|",) and not target.startswith("&"):
                out.append((expand_path(target, cur),
                            "redirect-append" if m.group(1) == ">>" else "redirect"))
        j = 0
        while j < len(w) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w[j]):
            j += 1
        if j >= len(w):
            continue
        verb = Path(w[j]).name
        args = [x for x in w[j + 1:] if not x.startswith("-")]
        if verb == "tee":
            how = "tee-append" if any(x in ("-a", "--append") for x in w[j + 1:]) else "tee"
            for x in args:
                if _literal(x):
                    out.append((expand_path(x, cur), how))
        elif verb in ("cp", "mv", "install") and len(args) >= 2 and _literal(args[-1]):
            # The DESTINATION only. `mv`'s source leaves its place, which matters to a data-loss
            # guard and not to a note about what a lesson says about the file now in front of you.
            out.append((expand_path(args[-1], cur), verb))
    for m in _OPEN_WRITE.finditer(cmd or ""):
        if _literal(m.group(2)):
            out.append((expand_path(m.group(2), cwd), "python-open"))
    seen, uniq = set(), []
    for p, how in out:
        if str(p) in seen:
            continue
        seen.add(str(p))
        uniq.append((p, how))
    return uniq


def expand_path(p: str, cwd: str | None) -> Path:
    """`common.expand`, named here so the detector reads in one piece. Relative targets resolve
    against the tracked cwd exactly as the shell would have; `bash_targets` never calls this for a
    relative target whose cwd it does not know."""
    return common.expand(p, cwd)


# ------------------------------------------------------------------ candidates ----

def lesson_files(vault: Path, region: str | None, include_global: bool = True) -> list:
    """The region's three lesson files, then Global's — de-duplicated, existing files only.

    Global is ALWAYS included because a vault's cross-cutting lessons are written there and apply
    to every region by construction. When the region IS Global, the list is Global's three and
    nothing else: that is the `Global-only write` negative control, and it falls out of the
    de-duplication rather than being a special case someone has to maintain."""
    out: list = []
    seen = set()
    for folder in ([region] if region else []) + (["Global"] if include_global else []):
        if folder is None:
            continue
        for stem in LESSON_STEMS:
            p = vault / folder / f"{stem}.md"
            key = str(p)
            if key in seen:
                continue
            seen.add(key)
            try:
                if p.is_file() and not p.is_symlink() and p.stat().st_size <= MAX_LESSON_FILE_BYTES:
                    out.append(p)
            except OSError:
                continue
    return out


def entries_of(path: Path, with_meta: bool = False) -> list:
    """`(rel, heading, cited_tokens)` for every ENTRY in one lesson file.

    `with_meta=True` appends a fourth field — the latest ISO date appearing in the entry's own
    prose, or `""`. It is an ADDITIVE option rather than a change of shape because two callers
    (`candidates`, and the replay's `Indices`) unpack three fields and the boot arm is the only
    thing that has ever needed a date. The date is the boot arm's RECENCY tie-break and nothing
    else reads it; a vault that does not date its lessons simply loses that tie-break.

    ★ DESIGN 2 READS THE BODY, NOT THE HEADING. Design 1 scored heading vocabulary; this one asks
    what the entry CITES, and an entry's citations are in its prose — the heading says what the
    lesson is, the body says what it is about. An entry runs from its heading to the next heading
    at the same or a shallower depth, which is how these files are actually written (a `###`
    sub-point belongs to the `##` entry above it).

    Depth is not filtered for entry-hood: a vault writes its lessons at whatever level its file
    happens to use — this package's own regions use `##` and the vault it grew from uses `###`
    under a `##` group — so a depth rule would make the module work in one vault and go silent in
    another, which is exactly the class of failure that is invisible from inside.

    ★ A FILE'S OWN TITLE IS NOT A LESSON, and leaving it in the corpus was measured, not feared
    (design 1, 5 of the first 20 surfaced lines). Two rules remove titles, and neither can reach a
    real lesson: a level-1 heading is a title in every role file this package writes or reads; and
    a heading whose whole vocabulary is already in the file's own PATH says nothing the citation
    beside it does not."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rel = common.vault_rel(path) or path.name
    self_terms = terms_of(path_words(rel))
    marks = list(_HEADING_RE.finditer(text))
    out = []
    for i, m in enumerate(marks):
        depth = len(m.group(1))
        end = len(text)
        for later in marks[i + 1:]:
            if len(later.group(1)) <= depth:
                end = later.start()
                break
        if depth == 1:
            continue
        heading = m.group(2).strip()
        if not heading:
            continue
        ht = terms_of(heading)
        if ht and ht <= self_terms:
            continue
        body = text[m.end():end]
        toks = cited_tokens(body)
        if toks:
            if with_meta:
                dates = _ISO_DATE.findall(body)
                out.append((rel, heading, toks, max(dates) if dates else ""))
            else:
                out.append((rel, heading, toks))
    return out


def candidates(vault: Path, region: str | None, include_global: bool = True) -> list:
    """Every candidate entry for a write in `region`. `include_global=False` is a DIAGNOSTIC seam
    for the replay, never a shipped mode."""
    out = []
    for p in lesson_files(vault, region, include_global):
        out.extend(entries_of(p))
    return out


def build_index(cands: list) -> dict:
    """`token -> [(rel, heading)]`, the whole matcher's lookup. Sorted, so two runs agree."""
    idx: dict = {}
    for rel, heading, toks in cands:
        for t in toks:
            idx.setdefault(t, []).append([rel, heading])
    for t in list(idx):
        uniq = sorted({(r, h) for r, h in idx[t]})
        if len(uniq) > MAX_ENTRIES_PER_TOKEN:
            del idx[t]                              # furniture, not a citation — see the constant
            continue
        idx[t] = [list(x) for x in uniq]
    return idx


# ------------------------------------------------------------------ the per-session index ----
# ★ BUILT ONCE PER SESSION, AND INVALIDATED BY THE FILES THEMSELVES. The candidate corpus is six
# files at most, but it is re-read on every write, and a PostToolUse hook that re-parses half a
# megabyte per tool call is a tax on every session whether or not it ever speaks. The cache is a
# document in the state dir keyed by region.
#
# The STAMP is what makes it safe: every source file's (size, mtime_ns) is stored with the index
# and compared on read. A session that EDITS a lesson file — which is the commonest thing a
# session does to these files — invalidates its own cache in the same act, so the arm can never
# cite a heading that has just been renamed away. Falling back to a rebuild costs one parse.

def _index_path(sid: str, region: str | None) -> Path:
    # The region is a vault directory name and cannot traverse — but it reaches a FILENAME here,
    # and `safe_sid` exists because the same was once true of the session id. Sanitized to a
    # closed character class rather than argued about: a key that cannot contain a separator or a
    # dot cannot name anything outside the state directory, which is what the EXEMPT entry in
    # `test_mutation_coverage.py` claims about this function's writes.
    key = re.sub(r"[^A-Za-z0-9_-]", "_", region or "-")[:80]
    return config.state() / f"lesson-index-{common.safe_sid(sid)}-{key}.json"


def _stamp(files: list) -> list:
    out = []
    for p in files:
        try:
            st = p.stat()
            out.append([str(p), st.st_size, st.st_mtime_ns])
        except OSError:
            out.append([str(p), -1, -1])
    return out


def index_for(sid: str, vault: Path, region: str | None) -> dict:
    """The region's citation index, from cache when the sources have not moved."""
    files = lesson_files(vault, region)
    stamp = _stamp(files)
    cache = _index_path(sid, region)
    try:
        doc = json.loads(cache.read_text(encoding="utf-8"))
        if isinstance(doc, dict) and doc.get("stamp") == stamp and isinstance(doc.get("index"), dict):
            return doc["index"]
    except (OSError, ValueError):
        pass
    idx = build_index(candidates(vault, region))
    try:
        config.state().mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"stamp": stamp, "index": idx}), encoding="utf-8")
    except OSError as e:
        log("lesson_push", f"index\t{sid}\t{e}")     # a cache that cannot be written is not an error
    return idx


# ------------------------------------------------------------------ region ----

def region_for(p: Path, cwd: str | None) -> str | None:
    """The vault region a written path belongs to.

    Two cases, in this order:
      * the path is IN the vault — the region is its first path segment, when that segment is a
        real directory holding at least one role file (`Global/Kernel.md`, `<Region>/Errata.md`);
      * the path is in a REPO — the region comes from that repo's own `CLAUDE.md` @-imports, via
        `common.region_of_repo`, which validates the result as a contained path (the traversal
        BLASTRADIUS-1 found), and failing that from the lane marker's declared vault paths.

    The lane fallback is last, not first, because a lane's partition is a list of WRITE
    permissions, not a statement about subject: a lane may declare seven prefixes and work in one."""
    vault = config.vault()
    rel = common.vault_rel(p)
    if rel:
        seg = rel.split("/")[0]
        return seg if (vault / seg).is_dir() and seg != rel else None
    repo = common.repo_root_of(p)
    if repo:
        region = common.region_of_repo(repo)
        if region and (vault / region).is_dir():
            return region
    _lane, prefixes, _m = common.lane_for(cwd)
    for pref in prefixes:
        head = pref.split("/")[0]
        if "." in head:
            continue
        d = vault / head
        try:
            if d.is_dir() and any((d / f"{s}.md").is_file() for s in LESSON_STEMS):
                return head
        except OSError:
            continue
    return None


# ------------------------------------------------------------------ per-session state ----
# This module's own file, the shape and locking discipline `context_cap.py` uses. Not the
# session-start record: that document belongs to another hook and three hooks read it for keys they
# own. A dedupe set has no business adding fields to it.

def _state_path(sid: str) -> Path:
    return config.state() / f"lesson-push-{common.safe_sid(sid)}.json"


def _load(sid: str) -> dict:
    try:
        doc = json.loads(_state_path(sid).read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _update(sid: str, mutate) -> bool:
    """True when the change reached disk. False means the dedupe did NOT record — see `notice`."""
    import fcntl
    try:
        config.state().mkdir(parents=True, exist_ok=True)
        with open(config.state() / f"lesson-push-{common.safe_sid(sid)}.lock", "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            doc = _load(sid)
            mutate(doc)
            _state_path(sid).write_text(json.dumps(doc, indent=1), encoding="utf-8")
            fcntl.flock(lk, fcntl.LOCK_UN)
        return True
    except OSError as e:
        log("lesson_push", f"state\t{sid}\t{e}")
        return False


def shown(sid: str) -> list:
    got = _load(sid).get("shown")
    return list(got) if isinstance(got, list) else []


def _record_shown(sid: str, headings: list) -> bool:
    def mutate(doc):
        got = doc.get("shown")
        got = list(got) if isinstance(got, list) else []
        for h in headings:
            if h not in got:
                got.append(h)
        doc["shown"] = got
    return _update(sid, mutate)


# ------------------------------------------------------------------ the notice ----

def is_tautological(rel: str, new_text: str, lrel: str, heading: str) -> bool:
    """Is this lesson the thing the writer is currently WRITING?

    ★ THE DOMINANT NOISE CLASS OF DESIGN 1, FOUND BY RUNNING THE HOOK OVER 537 REAL EDITS BEFORE
    TRUSTING IT — 10 of the first 26 surfaced lines. It survives into design 2 unchanged, and it
    bites HARDER here: a session editing a lesson file, or a document quoting a lesson, carries
    that lesson's own citations by construction, so the citation matcher would hand it back with
    perfect confidence. Two rules, neither of which can reach a real match:

      * the lesson lives in the file being written;
      * the heading appears VERBATIM in the text just written.

    ★ BOTH SIDES ARE WHITESPACE-NORMALISED, and the plain substring test was WRONG WITHOUT IT.
    Found by design 1's reviewer against real vault content, not against a fixture: a notice file
    cited a lesson by its exact heading inside a wikilink, and the source hard-wrapped that line in
    the middle of the heading. A newline where the heading has a space defeats `in` completely, so
    the writer was handed back a lesson they had just quoted — the precise case this function
    exists to suppress, arriving through the one shape prose actually takes.

    The test is still a substring test on bounded text, so it cannot be fooled into being
    expensive."""
    if lrel == rel:
        return True
    flat_text = " ".join((new_text or "")[:MAX_EDIT_TEXT_BYTES].split())
    return " ".join(heading.split()) in flat_text


def lines_for(index: dict, rel: str, new_text: str, already: list, per_fire: int) -> list:
    """`(line, heading, match_token)` for what this write would surface. Pure — the index is
    passed in, so the hook and the replay run the identical matcher over the identical corpus.

    ★ THERE IS NO THRESHOLD HERE, AND THAT IS THE POINT OF DESIGN 2. A token either names the same
    thing or it does not; what decides ORDER when several do is how specific the matching token is
    (`CLASS_RANK`) — a queue-row id before a wikilinked heading before a two-segment path before a
    backticked name before a filename. Ties break on the token, then the file, then the heading,
    so two runs of the replay agree and the dedupe state is reproducible."""
    want = write_tokens(rel, new_text)
    if not want:
        return []
    hits = []
    for tok in want:
        for lrel, heading in index.get(tok, []):
            hits.append((CLASS_RANK.get(tok.split(":", 1)[0], 9), tok, lrel, heading))
    hits.sort(key=lambda h: (h[0], h[1], h[2], h[3]))
    out = []
    seen = set(already)
    for _rank, tok, lrel, heading in hits:
        if heading in seen:
            continue                                # negative control: same match, same session
        if is_tautological(rel, new_text, lrel, heading):
            continue
        seen.add(heading)
        out.append((f"lesson: {lrel}#{heading}", heading, tok))
        if len(out) >= per_fire:
            break
    return out


def display_rel(p: Path) -> str:
    """How the written path is NAMED — in the log, and as the tokens the path contributes.

    Vault-relative inside the vault, repo-relative inside a repo, else the bare filename. Never an
    absolute path: an absolute path carries the machine's own directory names (`Users`, the
    owner's home) into a log this package publishes, and they are not a citation."""
    rel = common.vault_rel(p)
    if rel:
        return rel
    repo = common.repo_root_of(p)
    if repo:
        try:
            return f"{repo.name}/{p.relative_to(repo)}"
        except ValueError:
            pass
    return p.name


def _speak(sid: str, trigger: str, p: Path, new_text: str, cwd: str | None) -> str | None:
    """The shared body of both triggers: same caps, same dedupe, same index, same log line."""
    rel_written = display_rel(p)
    per_session = int(limits.get("lesson_push_max_per_session", 10) or 0)
    if per_session <= 0:
        return None
    already = shown(sid)
    # The cap is checked BEFORE anything is read. A session past its budget costs no file I/O.
    if len(already) >= per_session:
        return None
    per_fire = int(limits.get("lesson_push_max_per_fire", 2) or 0)
    if per_fire <= 0:
        return None
    per_fire = min(per_fire, per_session - len(already))
    vault = config.vault()
    region = region_for(p, cwd)
    index = index_for(sid, vault, region)
    if not index:
        return None                                 # negative control: a vault with no lessons
    hits = lines_for(index, rel_written, new_text, already, per_fire)
    if not hits:
        return None
    _record_shown(sid, [h for _l, h, _t in hits])
    for _line, heading, tok in hits:
        log("lesson_push", f"{sid}\ttrigger:{trigger}\t{rel_written}\t{heading}\tmatch:{tok}")
    body = "\n".join(line for line, _h, _t in hits)
    return (body + "\nWritten down in this vault already, and matched to what you just wrote "
            "because it CITES the same thing — read it before re-deriving. Nothing here is "
            "blocked.")


def notice(sid: str, p: Path, new_text: str, cwd: str | None) -> str | None:
    """The Edit/Write/MultiEdit trigger. One `additionalContext` paragraph, or None.

    Never raises: every path degrades to silence, because a note that cost a turn would be a bad
    bargain."""
    try:
        if not limits.get("lesson_push_enabled", False):
            return None
        return _speak(sid, "edit", p, new_text, cwd)
    except Exception as exc:                        # a note must never cost a turn
        log("lesson_push", f"error\t{exc}")
        return None


def notice_bash(sid: str, cmd: str, cwd: str | None) -> str | None:
    """The Bash trigger. The command's LITERAL write targets, and the command itself as the text.

    ★ THE COMMAND IS THE TEXT, and for a heredoc that is the whole written document — which is
    what makes this arm worth having rather than a second copy of the first. A `cat > report.md
    <<'EOF'` carries every citation the report makes, in the tool input, before the file exists.

    Several targets in one command produce ONE notice: the caps are per fire, not per path, and a
    session that writes four files in one line did one thing."""
    try:
        if not limits.get("lesson_push_enabled", False):
            return None
        targets = bash_targets(cmd, cwd)
        if not targets:
            return None
        for p, _how in targets:
            out = _speak(sid, "bash", p, cmd, cwd)
            if out:
                return out
        return None
    except Exception as exc:
        log("lesson_push", f"error\t{exc}")
        return None


# ================== DESIGN 3 — THE BOOT ARM (LESSONPUSH-3, q:CU-2026-09-20-LESSONPUSH-3) ======
#
# ★ TWO DESIGNS FAILED AT THE SAME SEAM, AND THE SEAM WAS THE MOMENT, NOT THE MATCHER. Design 1
# matched an edit's vocabulary against a lesson's heading; design 2 matched what the write TOUCHED
# against what a lesson CITES. Different matchers, different triggers, and both returned zero on
# the same held-out set, every time for the same reason: at write time the system knows a path and
# a blob of text, and the lesson about to be re-broken is reachable from neither.
#
# At BOOT the session's SUBJECT is known before any work happens. This arm therefore does not
# match an edit at all — it picks by STRUCTURE:
#
#   (a) CANDIDATES are the lesson entries of the session's own lane regions, plus Global's. That
#       is the same corpus rule the write arms use, resolved from the lane MARKER rather than from
#       a written path, because at boot there is no written path.
#   (b) A candidate is SELECTED when what it cites appears in this session's structural surface —
#       the @-import chain it actually booted with, the OPEN rows of its own work queues, and (on
#       a resume or a compact, where the transcript already exists) its opening prompt. Measured
#       on this machine, the opener is NOT available at a cold start: over 97 sessions the first
#       user record was written AFTER the SessionStart hook in 96 of them, median +0.86 s. So the
#       opener is an opportunistic THIRD source and the open rows are the proxy the row named.
#   (c) ORDER, when more are selected than may be printed: the source first (a queue row or an
#       opener is about THIS session's work; the boot chain is about every session in this lane),
#       then how specific the matching token is (`CLASS_RANK`), then how many distinct things
#       matched, then a re-occurrence count if the installation configures one, then recency.
#       Deterministic all the way down, so two runs of the replay agree.
#
# WHAT THIS ARM CANNOT SEE, stated as plainly as design 2's blindness was: a lesson that cites
# nothing, and a session whose subject is not yet written down anywhere — a fresh idea, an owner
# message that has not become a queue row. It also cannot know what the session will END UP doing,
# only what it starts pointed at, and those differ.
#
# THE COST IS PAID BY EVERY SESSION, which is the argument against it and the reason the gate bar
# is higher here than at write time: a write-time line costs a session that was writing, a boot
# line costs every session that boots. `eval/lesson_push_replay.py --boot` measures it against the
# median boot floor.

BOOT_MAX_SOURCE_BYTES = 2_000_000       # per structural source file; a queue file can be large
BOOT_MAX_QUEUE_FILES = 8
BOOT_MAX_OPEN_ROWS = 400
BOOT_MAX_OPENER_BYTES = 20_000

# Where a structural token came from, lowest first — see (c) above.
SOURCE_RANK = {"opener": 0, "rows": 0, "boot": 1}


def lane_regions(prefixes: list) -> list:
    """The vault regions in this lane's declared partition that actually hold lesson files.

    A partition prefix may name a nested region (`Umbrella/Region`), a top-level region
    (`Region`), a non-region folder (`Queues/open`) or a single file (`index.md`). Only the
    ones that are directories carrying at least one of the three lesson stems survive, which drops
    the folders and the files without a list of either kept anywhere.

    ★ THE PARTITION IS A LIST OF WRITE PERMISSIONS, NOT A STATEMENT OF SUBJECT — the same caveat
    `region_for` carries about its lane fallback. It is nevertheless the best structural statement
    of subject available at boot, and unlike a written path it exists before any work happens."""
    vault = config.vault()
    out, seen = [], set()
    for pre in prefixes or []:
        pre = (pre or "").strip().strip("/")
        if not pre or pre.endswith(".md"):
            continue
        for cand in (pre, pre.split("/")[0]):
            if not cand or cand in seen:
                continue
            seen.add(cand)
            d = vault / cand
            try:
                if d.is_dir() and any((d / f"{s}.md").is_file() for s in LESSON_STEMS):
                    out.append(cand)
            except OSError:
                continue
    return sorted(out)


def queue_files(lane: str | None, prefixes: list) -> list:
    """This lane's work-queue files, and only this lane's.

    ★ BOUNDED BY CONSTRUCTION, AND THAT IS THE POINT. `session_start.py` already carries a comment
    about the day reading the whole queue tree cost this vault a 2.4 MB read on every single boot;
    the directory here is 2.5 MB. A file is taken only when its stem matches this lane's own name
    or one of its regions', or when the marker names the file outright — so a lane reads its own
    queues and stops, whatever the tree grows to."""
    qd = config.queues_dir()
    if qd is None or not qd.is_dir():
        return []
    qrel = (config.queues_rel() or "").strip("/")
    slugs = {lane.lower()} if lane else set()
    for r in lane_regions(prefixes):
        slugs.add(r.lower().replace("/", "-"))
        slugs.add(r.split("/")[-1].lower())
    slugs = {s for s in slugs if s}
    explicit = {Path(p).stem.lower() for p in (prefixes or [])
                if p.endswith(".md") and qrel and p.startswith(qrel + "/")}
    out = []
    try:
        entries = sorted(p for p in qd.iterdir() if p.is_file() and p.suffix == ".md")
    except OSError:
        return []
    for p in entries:
        stem = p.stem.lower()
        if stem in explicit or stem in slugs or any(stem.startswith(s + "-") for s in slugs):
            out.append(p)
        if len(out) >= BOOT_MAX_QUEUE_FILES:
            break
    return out


def open_rows(path: Path) -> str:
    """The OPEN checkbox lines of one queue file, joined. Not their indented notes.

    A row's notes are its specification and are where most of its bytes are; the CHECKBOX LINE is
    where its id, its title and its backticked names are. Taking the line and leaving the notes is
    what keeps this a bounded read of a file that is hundreds of kilobytes, and the tokens the
    notes would add are overwhelmingly the same ones the line already carries."""
    out, n = [], 0
    try:
        if path.stat().st_size > BOOT_MAX_SOURCE_BYTES:
            return ""
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("- [ ]"):
                    out.append(line.rstrip("\n"))
                    n += 1
                    if n >= BOOT_MAX_OPEN_ROWS:
                        break
    except OSError:
        return ""
    return "\n".join(out)


def read_bounded(path: Path) -> str:
    try:
        if path.stat().st_size > BOOT_MAX_SOURCE_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def structural_tokens(boot_files: list, qfiles: list, opener: str | None) -> dict:
    """`token -> source class` for everything this session is structurally pointed at.

    `boot_files` is the resolved @-import chain — a list of `Path` or of `(Path, size)`, because
    that is how `session_start` already has it and re-deriving the chain here would be a second
    answer to "what does a session load" inside one package, which is this package's own recorded
    duplicated-fact failure.

    A token seen in two sources keeps the MORE SPECIFIC one (lower `SOURCE_RANK`): a path that is
    both in the boot chain and in an open row is a path this session is about, not furniture."""
    out: dict = {}

    def add(text: str, src: str):
        if not text:
            return
        for t in cited_tokens(text):
            cur = out.get(t)
            if cur is None or SOURCE_RANK.get(src, 9) < SOURCE_RANK.get(cur, 9):
                out[t] = src

    for entry in boot_files or []:
        p = entry[0] if isinstance(entry, (tuple, list)) else entry
        try:
            p = Path(p)
        except TypeError:
            continue
        add(read_bounded(p), "boot")
        tok = norm_path_token(display_rel(p))
        if tok:
            out.setdefault(tok[0], "boot")
    for p in qfiles or []:
        add(open_rows(p), "rows")
    if opener:
        add(opener[:BOOT_MAX_OPENER_BYTES], "opener")
    return out


def boot_candidates(vault: Path, regions: list) -> list:
    """`(rel, heading, toks, date)` for every lesson entry in these regions and in Global.

    De-duplicated on `(rel, heading)`: a lane with two regions and Global reads Global once."""
    out, seen = [], set()
    files: list = []
    for folder in list(regions or []) + ["Global"]:
        for f in lesson_files(vault, folder):
            if f not in files:
                files.append(f)
    for f in files:
        for rel, heading, toks, date in entries_of(f, with_meta=True):
            key = (rel, heading)
            if key in seen:
                continue
            seen.add(key)
            out.append((rel, heading, toks, date))
    return out


def reoccurrence_counts() -> dict:
    """`(file, heading) -> count` from an installation-configured TSV, or `{}`.

    ★ NOT SHIPPED WITH DATA, AND NOT DERIVABLE AT RUNTIME. How often a lesson has been re-learned
    is the output of a census over a corpus of session transcripts; this package cannot compute it
    at boot and must not pretend to. An installation that has run one names its TSV in
    `config.json` (`lesson_reoccurrence`) and gets the tie-break; every other installation gets a
    tie-break that is uniformly zero, which is the honest state and not a degraded one."""
    p = config.lesson_reoccurrence()
    if p is None:
        return {}
    out: dict = {}
    try:
        with p.open(encoding="utf-8", errors="replace") as fh:
            head = fh.readline().rstrip("\n").split("\t")
            try:
                i_file, i_head = head.index("file"), head.index("heading")
                i_verdict = head.index("verdict")
            except ValueError:
                return {}
            for line in fh:
                f = line.rstrip("\n").split("\t")
                if len(f) <= max(i_file, i_head, i_verdict):
                    continue
                if "RE-OCCURRENCE" in f[i_verdict].upper():
                    key = (f[i_file].strip(), f[i_head].strip())
                    out[key] = out.get(key, 0) + 1
    except OSError:
        return {}
    return out


def boot_select(cands: list, stoks: dict, already: list, max_lines: int,
                reocc: dict | None = None) -> list:
    """`(line, heading, token, source)` for what this boot would surface. Pure — every input is
    passed in, so the hook and the replay run the identical selector over identical material.

    The furniture cut is `build_index`'s and is reused rather than restated: a token naming more
    than `MAX_ENTRIES_PER_TOKEN` entries is what every lesson mentions, and at boot — where the
    structural surface is a whole @-import chain — that class would otherwise dominate."""
    reocc = reocc or {}
    index = build_index([(r, h, t) for r, h, t, _d in cands])
    meta = {(r, h): d for r, h, t, d in cands}
    best: dict = {}
    for tok, src in stoks.items():
        for lrel, heading in index.get(tok, []):
            key = (lrel, heading)
            rank = (SOURCE_RANK.get(src, 9), CLASS_RANK.get(tok.split(":", 1)[0], 9), tok)
            cur = best.get(key)
            if cur is None:
                best[key] = {"rank": rank, "src": src, "tok": tok, "n": 1}
            else:
                cur["n"] += 1
                if rank < cur["rank"]:
                    cur.update(rank=rank, src=src, tok=tok)
    rows = []
    for (lrel, heading), v in best.items():
        rows.append((v["rank"][0], v["rank"][1], -v["n"],
                     -reocc.get((lrel, heading), 0), _neg_date(meta.get((lrel, heading), "")),
                     lrel, heading, v["tok"], v["src"]))
    rows.sort()
    out, seen = [], set(already or [])
    for _sr, _cr, _n, _ro, _dt, lrel, heading, tok, src in rows:
        if heading in seen:
            continue
        seen.add(heading)
        out.append((f"lesson: {lrel}#{heading}", heading, tok, src))
        if len(out) >= max_lines:
            break
    return out


def _neg_date(d: str) -> str:
    """A sort key that puts the NEWEST date first inside an ascending sort, and an undated entry
    last. Digit-wise complement rather than a parsed date: these are fixed-width ISO strings, the
    comparison is lexical either way, and a malformed one must not raise inside a sort."""
    if not d:
        return "~"                                   # sorts after every digit
    return "".join(chr(ord("9") - (ord(c) - ord("0"))) if c.isdigit() else c for c in d)


def _claim_boot(sid: str) -> bool:
    """True exactly once per session — the first caller to reach this. False afterwards, and false
    when the state file could not be written, because a dedupe that cannot record must not speak:
    a repeated boot notice is the one failure this arm can inflict on every session at once."""
    got = {"first": False}

    def mutate(doc):
        got["first"] = not bool(doc.get("boot_done"))
        doc["boot_done"] = True
    return _update(sid, mutate) and got["first"]


def boot_notice(sid: str, lane: str | None, prefixes: list, boot_files: list,
                opener: str | None = None) -> str | None:
    """The SessionStart trigger. One paragraph of `lesson:` lines, or None.

    Never raises: the facts block is assembled from many organs and none of them may cost a
    session its other facts."""
    try:
        if not limits.get("lesson_push_boot_enabled", False):
            return None
        max_lines = int(limits.get("lesson_push_boot_max_lines", 5) or 0)
        if max_lines <= 0:
            return None
        regions = lane_regions(prefixes)
        cands = boot_candidates(config.vault(), regions)
        if not cands:
            return None                              # negative control: a vault with no lessons
        stoks = structural_tokens(boot_files, queue_files(lane, prefixes), opener)
        if not stoks:
            return None                              # negative control: nothing structural to go on
        hits = boot_select(cands, stoks, shown(sid), max_lines, reoccurrence_counts())
        if not hits:
            return None
        if not _claim_boot(sid):
            return None                              # negative control: a second boot is silent
        _record_shown(sid, [h for _l, h, _t, _s in hits])
        for _line, heading, tok, src in hits:
            log("lesson_push", f"{sid}\ttrigger:boot\tsource:{src}\t{heading}\tmatch:{tok}")
        body = "\n".join(line for line, _h, _t, _s in hits)
        return ("- Lessons this vault already holds about what this session is pointed at "
                "(its boot chain, its open rows), surfaced once at boot:\n" + body)
    except Exception as exc:                          # a note must never cost a session its facts
        log("lesson_push", f"error\tboot\t{exc}")
        return None
