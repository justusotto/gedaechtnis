#!/usr/bin/env python3
"""split.py — when one topic has outgrown its room, give it its own. stdlib only.

    python3 split.py                      # proposals, for a person
    python3 split.py --json               # the same, machine-readable
    python3 split.py --apply <region> <cluster-id> --name <SubRegion>

Until this file existed the package made ONE region per repository and never another: a folder
without `Map.md` is not a region to any tool here, and nothing created one. So a vault that runs for
a year has the same six files it started with, growing until compaction is the only thing keeping
them readable, and a topic that has earned a room of its own never gets one.

Cleanup fights the symptom. This is the structural answer.

**Measure mechanically, name by hand, move deterministically.** The three halves are deliberately
separate:

- `propose()` finds clusters. It judges nothing, and it proposes at `split_min_entries`.
- The NAME is a person's. It is the one word about this that will be read for years, and a slug
  derived from shared vocabulary is a starting point, never an answer.
- `apply()` moves entry text byte-identically, adds the parent's Map row, rewrites the wikilinks
  that pointed at what moved, and writes a receipt naming every origin and destination.

**Nothing is deleted.** Every entry is in exactly one live file before and after; the receipt is
what makes the move undoable by hand.

**Why `--apply` asks and the cleanup pass does not.** Cleanup is reversible and its decisions are
dull, so it was ruled to run without a question. A split changes paths other files point at, and it
picks a name. That is the same ruling's other half: *hooks measure, agents propose, APPROVE
applies.*
"""
from __future__ import annotations

import argparse, hashlib, itertools, json, math, os, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))
import archive                                                            # noqa: E402
import config                                                             # noqa: E402
import limits                                                             # noqa: E402
import maintenance                                                        # noqa: E402
import common                                                             # noqa: E402
import rootguard                                                          # noqa: E402
import synthesis                                                          # noqa: E402

# ★ No module-level VAULT. See `synthesis.py`'s note: resolving it at import froze it once and
# rewrote 263 files of a real vault. Every function here calls `config.vault()`.

ROLE_FILES = ("Errata.md", "Patterns.md", "Canon.md", "Position.md", "Course.md", "Aporia.md")
# Every role file a split may move entries out of. `Map.md` is NOT here: an index is rewritten by
# the move, never carried through it.

_SLUG_BAD = re.compile(r"[^A-Za-z0-9]+")


def today() -> str:
    return maintenance.today()


def min_entries() -> int:
    return int(limits.get("split_min_entries"))


def min_remaining() -> int:
    """How many entries must be left in the PARENT for a split to be a split.

    ★ A SPLIT HAS TWO SIDES AND THE RULE ONLY EVER CHECKED ONE. `cluster()` proposed any group of
    `split_min_entries` or more that was not the whole region — so a cluster of n-1 entries was a
    valid split, and applying it left the parent with a single orphan per role file: a room emptied
    into another room and a `Map.md` pointing at it. The child bar alone cannot see that, because
    the child is perfectly healthy in every such case.

    Half the child bar, by symmetry: a parent left with fewer entries than half of what it takes to
    BE a room has stopped being the room and become the leftover."""
    return int(limits.get("split_min_remaining_entries"))


def auto_apply_enabled() -> bool:
    """Whether the plugin may create a sub-region ITSELF, with no one in the loop for the move.

    The owner's bar: "human moves" is the interim, not the target — a non-expert should never be
    asked to move a file. His condition for it shipping ON was ZERO false splits in the scenario
    suite, and `auto_min_remaining_share()` earns that ON THE SUITE.

    ★ IT SHIPS OFF ANYWAY, and the reason is a measurement on a real vault rather than caution.
    A dry run over the author's own 1,133-entry vault would have created five rooms and named four
    of them after DOCUMENT CONVENTIONS rather than subjects — `ScopeLocked`, `ScopeCanonical`,
    `NotesNever` — because `scope` appears in 23.7% of every entry in that vault (the `**Scope:**`
    line each Canon entry carries) and `never` in 16.9% (the NEVER/ALWAYS convention). One of those
    rooms would have gathered a light-mode crash, a Compose blur pattern and a palette ruling
    together on the strength of sharing `**Scope**` and `**FIXED**`.

    The core-vocabulary test cannot catch it: boilerplate is carried by far MORE than half a room,
    so it passes that test by being everywhere. The gap is that `cluster()` deliberately does not
    share `synthesis`'s stoplist — right about REGION-relative frequency, and it discards the
    defence against VAULT-WIDE furniture, which is a different axis.

    ★ SPLITVOCAB-1 HAS SINCE LANDED, AND THIS STILL SHIPS OFF — on the re-measurement, not on
    caution. With the vault-wide axis in `boilerplate()`, that same vault's five rooms become ONE:
    nine of one 101-entry region's entries, offered under a name built from that region's own
    subject, parent keeping 91%. The name reads as a subject, which is the whole of what the axis was for — but the room
    gathers a China-deck shipping note, a full-sync bug and a pattern about blind review with a
    pre-registered falsifier, on `collection`, `cards` and `failed`, at cohesion 0.556. The owner's
    condition for ON was ZERO false auto-applies on the live vault, and one candidate room of
    mixed membership is not zero. So the count of rooms this would create UNASKED on the vault it
    was measured against is one, and the one is arguable — which is an argument for asking."""
    got = limits.get("split_auto_apply", False)
    return bool(False if got is None else got)


def auto_min_remaining_share() -> float:
    """How much of a region the parent must KEEP for the plugin to move the rest by itself.

    ★ THIS IS THE WHOLE SAFETY ARGUMENT, so the numbers are here rather than in a report.
    A proposal that takes most of a region out is not a split; it is the parent being renamed
    through the back door, and the reader wants to be asked. Cohesion cannot express that — it is
    ANTI-correlated with it: measured over 10 seeds, the two cases the reader does NOT want moved
    (S07, 212 of 266; S13, 34 of 40) both score cohesion 1.000, the maximum, while a genuine broad
    topic that SHOULD move scores 0.45–0.60. A cohesion bar high enough to hold those two holds
    everything.

    The moved SHARE does express it, and the bar sits in a measured gap:

        parent keeps 15% — S13, 34 of 40      HOLD (the owner's own open question)
        parent keeps 20% — S07, 212 of 266    HOLD (the reader wants a rename)
        parent keeps 20% — S21, 40 of 50      held too, and that is a MISS, not an error
        ------------------------------------- 25%, here
        parent keeps 33% — S04/S05, 8 of 12   move
        parent keeps 47–83% — every live proposal on the real vault

    ★ THE ASYMMETRY THAT MAKES THIS SAFE: holding a genuine split back costs nothing — it is still
    PROPOSED and the reader still sees it. Moving wrongly costs a restructured vault. So the bar is
    set to hold everything the reader wants held, and S21 is knowingly held with it. 25% is inside
    the 20%-to-33% gap rather than adjacent to either edge; the nearest live proposal keeps 47%."""
    got = limits.get("split_auto_min_remaining_share", 0.25)
    return 0.25 if got is None else float(got)


def core_term_share() -> float:
    """What share of a component's entries must carry a term for it to be part of the component's
    CORE VOCABULARY. A component with no core vocabulary at all is not a topic — it is a chain of
    pairwise resemblances, and the two-word name it would be given is the room's two commonest
    words. Measured over 10 seeds (SPLITCLUSTER-1): a percolated 212-entry component scores ZERO
    core terms at this share on every seed, while every genuine topic measured — cliques, and
    broad-vocabulary topics at three different densities — scores at least one. The separation is
    categorical, not a margin, which is why this is a share and not a tuned threshold."""
    got = limits.get("split_core_term_share", 0.5)
    # NOT `or 0.5`: that idiom is copied from the knobs whose fallback IS 0.0, where it is
    # harmless. Here it would make 0 mean 0.5 — the one value that switches the rule OFF would
    # silently be the value that turns it fully on.
    return 0.5 if got is None else float(got)


def max_bridge_entries() -> int:
    """How many entries may be removed from a component before it stops being ONE room.

    Two topics that share a handful of entries are joined by the union-find into a single
    "cluster" named from both vocabularies. Removing those few entries separates them again, and
    nothing else in a region behaves that way: measured over 6 seeds, a fused pair comes apart at
    exactly the number of entries bridging it (1, 2, 3, 4), while FIVE genuine single topics —
    cliques and broad-vocabulary topics at three densities — do not come apart at any cut up to 6.

    So this number is bounded by COST, not by safety: the search is C(candidates, K) connectivity
    passes — on a SPARSE component. A dense one is skipped before the search starts (see the
    degree ceiling in `_unbridge`), so raising this costs nothing on the shapes where no cut can
    exist and costs C(16, K) passes on the shapes where one might. ★ A fuse by MORE than this many entries is INVISIBLE to the rule — at the shipped 3,
    a pair bridged by 4 entries is still reported as one topic. That is a known blind spot with a
    knob, not an oversight."""
    got = limits.get("split_max_bridge_entries", 3)
    return 3 if got is None else int(got)   # NOT `or 3` — see core_term_share() above


def min_remaining_share() -> float:
    """The SHARE of the region the parent must keep for a split to be a split (0 = off, the shipped
    behaviour). SPLITSIM-1 experiment knob: a count floor cannot see a cluster that takes 80% of a
    large region and leaves a healthy-looking absolute number behind (ROW-SPLITRULE-1 §3)."""
    return float(limits.get("split_min_remaining_share", 0.0) or 0.0)


def min_cohesion() -> float:
    """The COHESION a cluster must show to be called a topic at all (0 = off, the shipped behaviour).
    Cohesion = the share of members carrying at least two of the cluster's three most-shared terms.
    A clique scores 1.0; a percolated component — each entry resembling a neighbour, none resembling
    the far end — scores low, and its "topic" is the room's two commonest words. SPLITSIM-1 knob."""
    return float(limits.get("split_min_cohesion", 0.0) or 0.0)


# ------------------------------------------------------ the vault-wide boilerplate axis ----
# ★ HOUSE STYLE IS NOT A SUBJECT, AND IT IS ONLY VISIBLE FROM OUTSIDE ONE REGION (SPLITVOCAB-1).
#
# `cluster()` deliberately does not share `synthesis`'s stoplist, and that is right about
# REGION-relative frequency: a region's dominant topic IS most of that region, so measured against
# its own region every word the topic is made of is furniture. This is the OTHER axis, and a
# different question: a word the whole VAULT writes — a `**Scope:**` line under every Canon entry,
# a `**Last revisited:**` under every Aporia one, the NEVER/ALWAYS convention — joins entries that
# have nothing to do with each other, and then NAMES the room it made.
#
# Measured over the vault this grew from (1,133 entries, 2026-09-20): `scope` is carried by 23.7%
# of every entry in the vault, `never` by 16.9%, `revisited` by 8.4%. A dry run would have created
# five rooms and named four of them from those words.
#
# ★ WHY THE BAR IS BREADTH AND NOT FREQUENCY. Raw vault-wide frequency does not separate: sorted,
# it runs 23.7, 16.9, 10.6, 9.8, 8.4 … 4.8, 4.7, 4.1, 3.9, 3.4 with no gap anywhere, and the first
# genuine topic word (`lemma`, 3.4%) sits among furniture words at 3.9-4.8%. Any bar there is a
# number chosen to fit four names. What DOES separate is how many rooms write the word: a subject
# lives in the regions that are about it, and house style is written everywhere. Measured on the
# same vault, counting regions that carry a term in at least two entries:
#
#     scope 13 · never 13 · urgency 11 · revisited 11 · notes 11 · still 10 · content 10 · fixed 8
#     ------------------------------------------------------------------ the bar, 7 of 15 VOTERS
#     lemma 6 · miner 6 · vocab 6 · audio 6 · corpus 6 · canonical 5 · sentence 5 · cards 5 ·
#     slash 5 · gloss 5 · german 5 · override 4 · whisper 3 · compose 2 · palette 1 · images 1
#
# The term that WELDED each wrong name is at 8 or more — `scope` 13, `never` 13, `notes` 11,
# `fixed` 8 — and every topical term in that sample is at 6 or fewer. Not every word IN those names
# is: `locked` is at 5, `canonical` at 5, `images` at 1. They came along as the room's second word,
# and it is the welding term the bar has to reach. ★ THAT IS NOT A GAP IN THE DISTRIBUTION, and the first draft of this note called it one
# (the row's second reviewer disproved it). The full distribution is a continuum — 2 terms at
# breadth 13, 5 at 11, 3 at 10, 4 at 9, 17 at 8, 13 at 7, 40 at 6, 71 at 5 — and the band sitting
# exactly ON the bar holds `split`, `region`, `owner`, `layer`, `surface`, which are one region's
# real subjects. **Every setting of this bar strips some subject words**; what is measured is which
# ROOMS come out, not which words. The words still fall out BY MEASUREMENT — a hand-written
# stoplist of `Scope`, `FIXED`, `Last revisited` would be wrong the first time a convention changed
# and nothing would say so.
#
# ★ AND THE DENOMINATOR IS VOTERS, NOT REGIONS — the row's reviewer found this, and it is the
# difference between a bar and an accident. `maintenance.regions()` counts every directory with a
# `Map.md`, and on the vault this was calibrated against ELEVEN of the twenty-eight were an
# incident snapshot somebody had copied into the vault: 0-3 entries each, about to be moved to the
# Trash. They carry no term twice, so they change no breadth count — but as a share's denominator
# they set the bar, and the same measurement would have re-calibrated itself from 7 to 5 the day
# that folder was tidied away, stripping `lemma`, `canonical`, `audio` and a dozen other subjects
# with no signal that anything had changed.
#
# So a region counts toward the denominator only if it CAN vote — if it carries at least one term
# in at least two entries. Measured both ways on that vault: 28 regions and 17 regions, and
# **15 voters either way**. The denominator is invariant under exactly the change that would
# otherwise have moved it, and it needs no list of directory names to exclude.
#
# ★ AND THE SHARE IS SET FOR OUTCOME STABILITY, NOT FOR A GAP — because a share turns the vault's
# SIZE into the bar, and a vault gains a region whenever a repository is onboarded. Measured by
# running the whole vault at every bar (second reviewer, executed):
#
#     axis OFF → 5 rooms, FOUR named from conventions   the defect this row exists for
#     bar 4 → 5 rooms, none of them convention-named    (re-measured: the count coincides, the
#                                                        names do not — the first draft of this
#                                                        table read the OFF row's names onto it)
#     bar 5 → 1 room (a different, weaker one)
#     bar 6 → 1 room, `ChinaCollection`                 }  the outcome that does not change
#     bar 7 → 1 room, `ChinaCollection`                 }
#     bar 8 → 0 rooms — the rule goes SILENT on 1,134 entries
#     bar 9 → 3 rooms, two of them named from conventions again
#
# So the question is not where a gap is; it is how far today's vault can move before the answer
# changes. At 0.45 the bar is 7 and ONE more voting region makes it 8 — silence, which is the
# failure nobody sees. **0.41 is chosen so that the bar is 7 today AND today's 15 voters sit in the
# MIDDLE of the 13-to-17 range over which the bar stays 6 or 7**: two onboarded repositories either
# way and the answer is the same. Read it as the sentence it is — a term written by two in five of
# the rooms that write anything is house style, not a subject — and read the number as what it is,
# a value tuned to put the measurement in the middle of its own stable band rather than on an edge.
#
# ★ WHAT THE VOTE CANNOT SEE: it saturates at two entries. A two-entry stub that repeats one word
# is one voter, exactly like a 266-entry region, so a vault of stubs moves the bar as fast as a
# vault of rooms. The band above is the protection against that, and it is a band, not a proof.
def boilerplate_region_share() -> float:
    """What share of the vault's VOTING regions must write a term, repeatedly, for it to be house
    style. A voting region is one that carries any term in two or more entries — see the note
    above on why the denominator cannot be the region count."""
    got = limits.get("split_boilerplate_region_share", 0.41)
    # NOT `or 0.41`: 0.0 is the value that switches the axis OFF and must survive as itself.
    return 0.41 if got is None else float(got)


# A term has to be REPEATED in a region to count as that region's vocabulary at all — the same
# `df >= 2` floor `_room_terms()` uses, for the same reason: one entry mentioning a word is not
# the room writing it.
BOILERPLATE_MIN_CARRIERS = 2

# ★ AND THE AXIS REFUSES TO ANSWER WITHOUT EVIDENCE. Breadth is a statement about several rooms,
# so a vault with one or two voting regions has nothing to measure it from — and this package
# creates ONE region per repository by default, which is exactly the vault where a share would
# otherwise read `ceil(0.45 * 1) = 1` and declare every word carried twice to be furniture. The
# floor makes that case INERT rather than catastrophic, with no special-casing: no term can be
# written in three regions of a two-region vault. Said plainly, because both halves are limits
# and neither is a subtlety:
#
#   - **On a vault with fewer than three voting regions this axis finds nothing**, and the
#     boilerplate that made SPLITVOCAB-1 exist would not be caught there.
#   - **Up to seven voters the FLOOR sets the bar, not the share** (`ceil(0.41 * 7) = 3`), so on a
#     small vault the demand is "three rooms" however the knob is set — 100% of them at three
#     voters, 43% at seven. The share only starts to govern at eight.
BOILERPLATE_MIN_REGIONS = 3


def _terms(e: dict) -> set[str]:
    """The entry's vocabulary, computed once. Two passes read it (the boilerplate measurement and
    the clustering), and `significant_terms` is the expensive half of both."""
    got = e.get("_terms")
    if got is None:
        got = e["_terms"] = synthesis.significant_terms(e["heading"], e["body"])
    return got


def _repeat_counts(entries: list[dict]) -> dict[str, int]:
    """How many entries of one region carry each term. The unit both halves of the measurement
    read: `boilerplate()` for the breadth and the vote, `propose()` for the echoed voter count."""
    df: dict[str, int] = {}
    for e in entries:
        for t in _terms(e):
            df[t] = df.get(t, 0) + 1
    return df


def boilerplate(per_region: dict[str, list[dict]]) -> tuple[set[str], int, int]:
    """(the vault's house-style terms, the bar they were measured against, the voters counted).

    A term is house style when it is written — twice or more, so not in passing — in at least
    `max(BOILERPLATE_MIN_REGIONS, ceil(share * voters))` of the vault's VOTING regions, a voter
    being a region that writes ANY term twice. Returns an empty set and a bar of 0 when the axis is
    switched off, so a report can tell OFF from FOUND NOTHING.

    ★ The denominator is counted from the same pass that counts the breadth AND RETURNED FROM IT,
    so the two can never disagree: a region that contributes no vote contributes no denominator
    either. That is not tidiness — a region can be in the vault and cast nothing (an empty one, a
    stub, an incident snapshot somebody copied in), and counting it would let a directory with no
    memory in it move the bar for every region that has some. The count was briefly recomputed in
    `propose()` for the echo, which is how two numbers that "can never disagree" start to."""
    share = boilerplate_region_share()
    breadth: dict[str, int] = {}
    voters = 0
    for entries in per_region.values():
        repeated = [t for t, c in _repeat_counts(entries).items()
                    if c >= BOILERPLATE_MIN_CARRIERS]
        if repeated:
            voters += 1
        for t in repeated:
            breadth[t] = breadth.get(t, 0) + 1
    # OFF is decided after the count, not before it: the voter number is a fact about the vault
    # and a report says it whether the axis is switched on or not.
    if share <= 0:
        return set(), 0, voters
    bar = max(BOILERPLATE_MIN_REGIONS, math.ceil(share * voters))
    return {t for t, b in breadth.items() if b >= bar}, bar, voters


# What a proposal is asking for. `split` moves the cluster into a new sub-region; `refocus` moves
# NOTHING and says the region IS the topic under a better name. They are separate KINDS rather than
# a flag, because `apply()` must be able to refuse one of them outright.
SPLIT, REFOCUS, DIFFUSE = "split", "refocus", "diffuse"
# `diffuse` (SPLITSIM-1, only when `split_min_cohesion` > 0): the union-find joined these entries, but
# too few of them share the cluster's own top vocabulary for it to be ONE topic. Carried as a
# finding — the reader may still see a room in it — and `apply()` refuses it like a refocus.


# ---------------------------------------------------------------- measuring ----
def region_entries(region: Path, vault: Path) -> tuple[list[dict], list[str]]:
    """(entries, unreadable) for one region. A file that cannot be read is NAMED, never counted as
    an empty one — a region reported as having no cluster because half of it was unreadable looks
    exactly like a region that is fine."""
    out, unreadable = [], []
    for name in ROLE_FILES:
        p = region / name
        if not p.is_file():
            continue
        got, ok = synthesis.read_entries(p)
        rel = str(p.relative_to(vault))
        if not ok:
            unreadable.append(rel)
            continue
        for i, e in enumerate(got):
            e.update(file=rel, index=i, stem=p.stem)
            out.append(e)
    return out, unreadable


def slug_for(terms: list[str], taken: set[str]) -> str:
    """A candidate name from the cluster's own vocabulary — a STARTING POINT, never the answer.

    Deterministic so two runs propose the same word, and never silently reused: a second cluster
    whose best terms collide gets a numbered suffix rather than a name that already means something
    else in this region."""
    parts = [_SLUG_BAD.sub("", t).title() for t in terms[:2] if _SLUG_BAD.sub("", t)]
    base = "".join(parts) or "Topic"
    name, n = base, 1
    while name in taken:
        n += 1
        name = f"{base}{n}"
    return name


# How many of the least-cohesive entries the cut search considers. The search is
# C(_CUT_CANDIDATES, K) connectivity passes, so this is the cost dial; a bridge sits at the very
# bottom of the local-clustering ranking (measured: 0.53 against a median of 1.0 on a fused pair),
# so a deeper candidate list buys nothing. ★ It is also a FALSE-NEGATIVE surface: a fuse whose
# bridges rank below this cut-off is not found, and the component is reported as one topic.
_CUT_CANDIDATES = 16


def _room_terms(entries: list[dict], members: list[int], cut: set) -> list[str]:
    """The terms a room is DESCRIBED by, most-carried first — its name, its receipt, and the first
    five of them the new sub-region's `Map.md` is written from.

    Two exclusions, and neither is cosmetic; both were found by review after I called them so.

    `df >= 2` — a term carried by a SINGLE entry is not what the room is about, and a room with
    fewer than five terms above the bar would otherwise pad `Map.md` with one.

    `carriers <= cut` — a cut entry belongs to two topics and carries both vocabularies, so after
    unbridging a foreign term enters this room's counts at df 1..k. It can never reach the NAME (a
    core term needs df >= half the room, and a room is at least 8 entries, so >= 4 > k) but it
    reached the Map.md line, where a Beta room's description ended in `alphaone`. A term carried
    ONLY by the entries that were holding two rooms together is the other room's — which is exact,
    not a threshold: no genuine term of this room can have all its carriers inside the cut."""
    room_df: dict[str, int] = {}
    for i in members:
        for t in entries[i]["rare"]:
            room_df[t] = room_df.get(t, 0) + 1
    carriers = {t: {i for i in members if t in entries[i]["rare"]} for t in room_df}
    shared = {t for t, c in room_df.items() if c >= 2 and not (cut and carriers[t] <= cut)}
    return sorted(shared, key=lambda t: (-room_df[t], t))


def _core_vocabulary(entries: list[dict], members: list[int]) -> list[str]:
    """The terms at least `core_term_share()` of the members carry. Empty = not one subject."""
    df: dict[str, int] = {}
    for i in members:
        for t in entries[i]["rare"]:
            df[t] = df.get(t, 0) + 1
    floor = core_term_share() * len(members)
    return sorted((t for t, c in df.items() if c >= floor), key=lambda t: (-df[t], t))


def _pieces_without(members: set[int], adj: dict, drop: set[int], floor: int) -> list[list[int]]:
    """Connected pieces of `members - drop` that are big enough to be rooms of their own."""
    rest, seen, out = members - drop, set(), []
    for i in rest:
        if i in seen:
            continue
        stack, comp = [i], []
        seen.add(i)
        while stack:
            x = stack.pop()
            comp.append(x)
            for y in adj[x]:
                if y in rest and y not in seen:
                    seen.add(y)
                    stack.append(y)
        if len(comp) >= floor:
            out.append(sorted(comp))
    return out


def _local_clustering(i: int, adj: dict, members: set[int]) -> float:
    """Do this entry's neighbours know each other? A BRIDGE's neighbours are two sets of strangers.

    This is the candidate ranking for the cut search, and it has to be this rather than DEGREE: an
    entry joining two large topics has a HIGH degree, so the low-degree end of the component is
    exactly where it is NOT."""
    nb = sorted(adj[i] & members)
    if len(nb) < 2:
        return 1.0
    pairs = len(nb) * (len(nb) - 1) / 2
    edges = sum(1 for a in range(len(nb)) for b in range(a + 1, len(nb)) if nb[b] in adj[nb[a]])
    return edges / pairs


def _unbridge(members: list[int], adj: dict, floor: int) -> list[tuple[list[int], set]] | None:
    """Two rooms joined by a few shared entries, or one room? Returns the rooms, or None.

    Removes up to `max_bridge_entries()` entries and asks whether two viable pieces fall out. A cut
    entry is returned in EVERY piece it touches: an entry that genuinely belongs to both topics
    belongs to both proposals. What happens if the reader applies BOTH, measured rather than assumed:
    the first apply moves the shared entries, so the second proposal's member set has changed, its
    content-addressed id no longer exists, and `apply()` REFUSES it by name and re-proposes the
    region as it now stands. Nothing is lost or duplicated — the refusal IS the protection — but
    the reader is asked to look again rather than silently getting a second room built from the
    remains of the first."""
    ms = set(members)
    # ★ A DENSE COMPONENT CANNOT HAVE A SMALL CUT, and proving that costs one pass instead of 696.
    # The search is C(_CUT_CANDIDATES, K) connectivity passes, each O(V+E) — and E is quadratic in
    # a CLIQUE, which is exactly what a healthy topic looks like. Measured before this guard: a
    # 1000-entry clique spent 59s here, every pass of it futile, because no 3-vertex cut can
    # disconnect a clique at all.
    #
    # The test is a NECESSARY condition, so it can only skip searches that were going to fail.
    # Any valid cut leaves a smaller piece P with floor <= |P| <= (n-k)/2, and every entry of P
    # has all its neighbours inside P u drop — so it has degree at most (|P|-1) + k. If the
    # least-connected entry in the component is above that ceiling, no cut of size <= k exists and
    # the search cannot find one.
    k_max = max_bridge_entries()
    if k_max >= 1 and len(ms) > 2 * floor:
        ceiling = (len(ms) - k_max) / 2 - 1 + k_max
        if min(len(adj[i] & ms) for i in members) > ceiling:
            return None
    ranked = [i for _, i in sorted((_local_clustering(i, adj, ms), i) for i in members)]
    candidates = ranked[:_CUT_CANDIDATES]
    for k in range(1, max_bridge_entries() + 1):
        for drop in itertools.combinations(candidates, k):
            pieces = _pieces_without(ms, adj, set(drop), floor)
            if len(pieces) >= 2:
                return [(sorted(set(piece) | {d for d in drop if adj[d] & set(piece)}), set(drop))
                        for piece in pieces]
    return None


def cluster(entries: list[dict],
            boilerplate_terms: set[str] | frozenset = frozenset()) -> tuple[list[dict], list[dict]]:
    """Clusters WITHIN one region, on `synthesis`'s vocabulary and its corpus-measured stoplist.

    ★ `boilerplate_terms` IS THE VAULT-WIDE AXIS, BESIDE THE REGION-RELATIVE ONE, NEVER REPLACING
    IT (SPLITVOCAB-1). It is measured by `boilerplate()` over every region and subtracted here
    before a single edge is drawn, so house style can neither JOIN two entries nor NAME the room
    they were joined into. It defaults to empty: a caller with one region's entries and no vault
    behind them gets exactly the behaviour it had before, and `propose()` is what supplies it.

    ★ THE SAME IMPLEMENTATION, IMPORTED. `synthesis.py` clusters across regions and refuses an edge
    inside one; this clusters inside one region and refuses nothing. That is a difference of AXIS,
    not of rule, and writing the term extraction a second time here is how two functions that
    should agree stop agreeing — four times in this package already.

    ★ THE TOKENIZER IS SHARED AND THE STOPLIST IS NOT, deliberately. `synthesis.mark_rare` drops
    terms that appear across a quarter of the corpus, which is right for its question and wrong for
    this one: a region's dominant topic IS most of that region, so every word the topic is made of
    is furniture by that measure and the one cluster worth finding can never form. Measured twice —
    the first fixture run here returned none, and so did the second, after trying the whole vault as
    the corpus (this package creates ONE region per repository by default, so for most installs the
    vault and the region are the same set).

    What replaces it is the rule that actually expresses a split: a cluster must be a PROPER SUBSET
    of its region. A "cluster" of everything is not a topic that outgrew its room — it is the room,
    and moving all of it produces an empty parent and a renamed child."""
    n = len(entries)
    for e in entries:
        e["rare"] = _terms(e) - set(boilerplate_terms)

    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    weight: dict[str, int] = {}
    adj: dict[int, set[int]] = {i: set() for i in range(n)}
    for i in range(n):
        for j in range(i + 1, n):
            both = entries[i]["rare"] & entries[j]["rare"]
            if len(both) >= synthesis.MIN_SHARED_TERMS:
                a, b = find(i), find(j)
                if a != b:
                    parent[b] = a
                adj[i].add(j)
                adj[j].add(i)
                for t in both:
                    weight[t] = weight.get(t, 0) + 1

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    # ★ A COMPONENT IS A CANDIDATE, NOT A CLUSTER (SPLITCLUSTER-1). Single-linkage answers "can I
    # walk from this entry to that one", which is not the question. Two structural tests stand
    # between a component and a proposal, and neither is a tuned threshold:
    #
    #   1. NO CORE VOCABULARY, NO TOPIC. If not one term is carried by half the component, the
    #      entries resemble their neighbours and nothing else — a chain, not a subject. Measured:
    #      a percolated 212-entry component scores ZERO core terms on every one of 10 seeds, while
    #      every real topic measured scores at least one. It was the 212-entry case that made this
    #      row exist: the old rule offered to move 212 entries under a name made of the room's two
    #      commonest words.
    #   2. A FEW ENTRIES HOLDING TWO ROOMS TOGETHER IS TWO ROOMS. If removing up to
    #      `max_bridge_entries()` entries leaves two pieces that are each big enough to be a room,
    #      they are proposed separately. Measured over 6 seeds: a fused pair comes apart at exactly
    #      the number of entries bridging it, and five genuine single topics do not come apart at
    #      any cut up to 6 — so this separates by STRUCTURE, with no bar sitting inside anyone's
    #      noise floor.
    #
    # Both run before the size and viability rules, because those rules answer "is this cluster
    # worth moving" and cannot answer "is this a cluster".
    candidates: list[tuple[list[int], set]] = []
    chains: list[dict] = []
    for members in groups.values():
        if len(members) < min_entries():
            continue
        # The core-vocabulary test is applied ONCE, per ROOM, below — not here as well. A second
        # copy of it at component level was unreachable: on every shape where it would have fired,
        # no cut is found, so the room IS the component and the room-level test already refused it.
        # Two guards for one rule means neither can be shown to bite (both mutated green), and the
        # cost it was saving is 0.15s on the largest percolated component measured.
        rooms = _unbridge(members, adj, min_entries()) if len(members) > min_entries() else None
        for room, cut in (rooms or [(members, set())]):
            # `_pieces_without` already refused anything under the floor and a room only ever
            # GAINS cut entries, so a size test here is provably always true — the same dead
            # second guard removed just above, found by the reviewer's mutation M4.
            if _core_vocabulary(entries, room):
                candidates.append((room, cut))
            else:
                # ★ REPORTED, NEVER SILENT. This module's own rule, written where REFOCUS is
                # defined: dropping a finding answers "is there anything here" with "no" when the
                # honest answer is "yes, and it is a chain rather than a subject". A chain is NOT
                # a proposal — there is nothing to move and nothing to name — so it is carried in
                # its own short list and rendered as one line, not as a room the reader is being
                # offered. Restoring it as a proposal would put back exactly the noise this row
                # removed (the live vault went 13 proposals to 5).
                chains.append({"entries": len(room), "region_entries": n})

    out = []
    for members, cut in candidates:
        if len(members) == n:
            continue                    # the whole region is not a sub-region of itself
        remaining = n - len(members)
        # ★ RANKED WITHIN THE ROOM, NOT WITHIN THE REGION (SPLITCLUSTER-1 reviewer, MUST-FIX 1).
        # This used to rank the shared terms by `weight` — how many pairs ANYWHERE IN THE REGION
        # share the term — and that is wrong the moment a room is smaller than its component. An
        # entry bridging two topics carries both vocabularies, so after unbridging it imported the
        # OTHER room's terms into this one's list, and the second room was offered to the reader
        # under the FIRST room's name: a 12-entry Beta room proposed as `AlphaoneAlphathree2`,
        # with the other topic's words written into the new region's Map.md. Splitting the right
        # entries and calling them the wrong thing is most of the original defect, still standing.
        # `>= 2` is NOT cosmetic and NOT equivalent to `>= 1` — I classified it that way and the
        # reviewer disproved it. This list is not only where the NAME comes from: `apply()` writes
        # its first five terms into the new sub-region's `Map.md` ("…had gathered on one topic
        # (a, b, c, d, e)"). A room with fewer than five terms at `df >= 2` would fill the rest
        # with words carried by a SINGLE entry.
        #
        # And the cut entries need excluding outright. A bridge carries BOTH topics' vocabulary,
        # so after unbridging a foreign term enters this room's counts at df 1..k. It can never
        # reach the NAME (a core term needs df >= half the room, and a room is at least 8 entries,
        # so >= 4 > k) — but it reached the Map.md line, where a Beta room's description ended in
        # `alphaone`. A term whose carriers are ALL cut entries belongs to the other room, and
        # that is exact rather than a threshold: no genuine term of this room can be carried only
        # by the entries that were holding two rooms together.
        top = _room_terms(entries, members, cut)
        # Cohesion is MEASURED on every cluster whatever the knob says (an instrument may label);
        # it only DECIDES when `split_min_cohesion` is set.
        core = top[:3]
        carriers = sum(1 for i in members if sum(1 for t in core if t in entries[i]["rare"]) >= 2)
        # ★ ROUNDED ONCE, HERE, and the rounded value is what DECIDES as well as what is reported.
        # It used to decide on the raw float and report `round(..., 3)`, so a floor set to the
        # reported number could refuse a cluster whose own receipt said it was at the floor — the
        # displayed threshold and the enforced threshold were different numbers. Reproducing a
        # verdict from the receipt is the whole point of printing the receipt.
        cohesion = round(carriers / len(members), 3)
        # ★ BOTH SIDES, and a cluster that fails the parent's side is NOT discarded. It is a real
        # topic — it met the child bar — and dropping it silently would answer "is there anything
        # here" with "no" when the honest answer is "yes, and it is nearly the whole room". So it
        # becomes a `refocus`: the same finding, asking for a rename instead of a move.
        parent_ok = remaining >= min_remaining() and remaining >= min_remaining_share() * n
        if min_cohesion() and cohesion < min_cohesion():
            kind = DIFFUSE
        else:
            kind = SPLIT if parent_ok else REFOCUS
        def shape(i):
            return {"file": entries[i]["file"], "heading": entries[i]["heading"],
                    "stem": entries[i]["stem"], "index": entries[i]["index"]}
        member_set = set(members)
        rest = [i for i in range(n) if i not in member_set]
        out.append({
            "members": sorted(members),
            "kind": kind,
            "region_entries": n,
            "remaining": remaining,
            "min_remaining": min_remaining(),
            "min_remaining_share": min_remaining_share(),
            "cohesion": cohesion,
            "cohesion_carriers": carriers,
            "min_cohesion": min_cohesion(),
            "shared_terms": top,
            "entries": [shape(i) for i in sorted(members)],
            # Only a refocus carries them: they are what the person is being asked to look at —
            # the handful the region would be left with — and on a healthy split they are simply
            # the rest of a working region and nobody needs the list.
            "leftover": [shape(i) for i in rest] if kind in (REFOCUS, DIFFUSE) else [],
        })
    out.sort(key=lambda c: (-len(c["entries"]), c["entries"][0]["heading"]))
    return out, chains


def cluster_id(c: dict) -> str:
    """A cluster's id, derived from WHAT IT CONTAINS — not from its position in a list.

    ★ THIS IS THE PROTECTION, and a positional id was the defect it replaces. `apply()` recomputes
    the proposals (it must: the offsets it moves from have to be the file as it is NOW), so with
    `Proj#0` as the id, a vault edited between looking and applying would hand `Proj#0` to a
    DIFFERENT set of entries — and they would be moved, under the name the person approved for the
    other ones. Approval is for a set, so the id has to name the set. A changed cluster has a
    different id and `apply()` refuses, because the thing that was approved is no longer there."""
    key = "\n".join(sorted(f"{e['file']}::{e['heading']}" for e in c["entries"]))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def propose(vault: Path | None = None) -> dict:
    vault = vault or config.vault()
    taken: set[str] = set()
    found, unreadable = [], []
    checked = True
    try:
        regions = maintenance.regions()
    except Exception:
        regions, checked = [], False
    # A region the walk could not LIST does not raise and does not appear. `regions()` uses rglob,
    # which skips an unreadable directory silently, so without this a vault with one locked region
    # reports a smaller, entirely confident count. The probe exists (R7 built it for exactly this)
    # and this row simply has to call it — which the row's reviewer found it did not.
    try:
        blind = maintenance.unreadable_dirs(vault)
    except Exception:
        blind, checked = [], False
    if blind:
        checked = False
        unreadable.extend(f"{d}/ (directory not listable)" for d in blind)
    per_region: dict[str, list[dict]] = {}
    corpus: list[dict] = []
    for region in regions:
        entries, bad = region_entries(region, vault)
        if bad:
            # ★ A FILE nobody could read makes its region a smaller, quieter region — and the
            # vault-wide boilerplate axis divides by how many regions VOTE, so one unreadable file
            # can take a region's vote away and, on a small vault, disarm the axis entirely: the
            # convention survives, and the room named after it comes back in a DIFFERENT region.
            # The list below names the file either way; this makes the SUMMARY flag say it too, so
            # "the axis found nothing" can be told from "the axis could not look".
            checked = False
        unreadable.extend(bad)
        per_region[str(region.relative_to(vault))] = entries
        corpus.extend(entries)

    # The vault-wide axis, measured ONCE over every region and applied to all of them — a term's
    # breadth is a fact about the vault, not about the region being clustered, so it must not be
    # recomputed per region from a different corpus.
    boiler, boiler_bar, boiler_voters = boilerplate(per_region)

    chains: list[dict] = []
    for region in regions:
        rel_region = str(region.relative_to(vault))
        taken |= {d.name for d in region.iterdir() if d.is_dir()} if region.is_dir() else set()
        clusters, region_chains = cluster(per_region[rel_region], boiler)
        for c in region_chains:
            chains.append(dict(c, region=rel_region))
        for c in clusters:
            name = slug_for(c["shared_terms"], taken)
            taken.add(name)
            found.append({"region": rel_region, "id": f"{rel_region}#{cluster_id(c)}",
                          "kind": c["kind"], "suggested_name": name,
                          "region_entries": c["region_entries"], "remaining": c["remaining"],
                          "min_remaining": c["min_remaining"],
                          "min_remaining_share": c["min_remaining_share"],
                          "cohesion": c["cohesion"], "min_cohesion": c["min_cohesion"],
                          "cohesion_carriers": c["cohesion_carriers"],
                          "shared_terms": c["shared_terms"], "entries": c["entries"],
                          "leftover": c["leftover"]})
    return {"generated": today(), "vault": str(vault), "min_entries": min_entries(),
            "min_remaining": min_remaining(),
            "min_remaining_share": min_remaining_share(), "min_cohesion": min_cohesion(),
            "regions": len(regions), "regions_checked": checked,
            # Echoed so a run can be reproduced from its own output, and so a harness can tell a
            # rule that HAS this axis from one that does not: `boilerplate_bar` is 0 only when the
            # axis is switched off, and the term list is what it actually removed.
            "boilerplate_region_share": boilerplate_region_share(),
            "boilerplate_bar": boiler_bar, "boilerplate_terms": sorted(boiler),
            "boilerplate_voters": boiler_voters,
            "unreadable": sorted(unreadable), "proposals": found,
            # Groups that hang together pairwise but have no subject in common. Not proposals —
            # there is nothing to move and nothing to name — but reported, because "nothing here"
            # and "something here that is not a room" are different answers.
            "chains": sorted(chains, key=lambda c: (c["region"], -c["entries"]))}


# ------------------------------------------------------------------ applying ----
def _valid_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,63}", name or ""):
        raise ValueError(
            f"{name!r} is not a usable sub-region name: letters, digits and hyphens, starting with "
            f"a letter. It becomes a directory and appears in every wikilink that points into it.")
    return name


def apply(region_rel: str, cluster_id: str, name: str, vault: Path | None = None) -> dict:
    """Move one cluster into a new sub-region. Deterministic, byte-identical, and it refuses rather
    than guessing.

    The order matters and the refusals more: a role file that changed since the proposal was made is
    LEFT ALONE and named, because at a different index sits a different entry, and moving the wrong
    one is the only destructive mistake this module can make."""
    vault = vault or config.vault()
    _valid_name(name)
    found = propose(vault)
    match = [p for p in found["proposals"] if p["id"] == cluster_id and p["region"] == region_rel]
    if not match:
        return {"applied": False, "why": f"no proposal {cluster_id!r} in {region_rel!r} right now",
                "proposals": [p["id"] for p in found["proposals"]]}
    prop = match[0]
    # ★ REFUSED BEFORE ANYTHING IS TOUCHED — no directory, no Map row, no receipt. A refocus is a
    # proposal that MOVES NOTHING, so there is no apply for it, and the refusal is what makes that
    # true in the code rather than only in the prose.
    #
    # This is also where viability is RE-EVALUATED. `propose()` above was recomputed from the files
    # as they are NOW, so a cluster that was a healthy split when the person looked and has since
    # grown to swallow its parent comes back as a REFOCUS and lands here. The id names the member
    # set, never a position; the kind is recomputed against the same recomputed region.
    # ★ DIFFUSE IS A RE-EVALUATION, NOT A DURABLE VETO, and symmetrically with REFOCUS above.
    # The kind is recomputed here against the limits in force AT APPLY TIME, so a cluster shown to
    # a person as "DIFFUSE — nothing to move" becomes a plain split and MOVES if the cohesion knob
    # is off when they run the apply. Nothing records that they were once shown the refusal. The
    # shipped path has the knob off at both ends so it cannot bite today, but the limits seam is
    # machine-wide, and an auto-apply path that trusts a proposal
    # printed earlier must read this paragraph before it trusts a stored kind.
    if prop.get("kind") == DIFFUSE:
        return {"applied": False, "kind": DIFFUSE,
                "why": (f"{prop['id']} is DIFFUSE, not a topic: only "
                        f"{prop.get('cohesion_carriers', '?')} of its {len(prop['entries'])} "
                        f"entries carry two of its three most-shared terms — cohesion "
                        f"{prop['cohesion']}, floor {prop['min_cohesion']}. The entries were "
                        f"joined pairwise, not around one subject. Nothing to move."),
                "leftover": prop.get("leftover") or []}
    if prop.get("kind") == REFOCUS:
        return {"applied": False, "kind": REFOCUS,
                "why": (f"{prop['id']} is a REFOCUS, not a split: the cluster is {len(prop['entries'])} "
                        f"of {prop['region_entries']} entries and would leave the parent "
                        f"{prop['remaining']}, under the floor of {prop['min_remaining']}. Nothing "
                        f"to move — the region IS this topic. The proposal's suggested name is a "
                        f"better name for the REGION, and renaming is its own pass, not this one."),
                "leftover": prop.get("leftover") or []}
    region = vault / region_rel
    sub = region / name
    if sub.exists():
        return {"applied": False, "why": f"{region_rel}/{name} already exists; pick another name"}

    by_file: dict[str, list[dict]] = {}
    for e in prop["entries"]:
        by_file.setdefault(e["file"], []).append(e)

    moved, skipped = [], []
    stays: set[tuple[str, str]] = set()
    plan: list[tuple[Path, str, list[str], list[str], str]] = []
    for rel, items in sorted(by_file.items()):
        live = vault / rel
        try:
            text = live.read_text(encoding="utf-8")
        except OSError as exc:
            skipped.append(f"{rel}: {exc.__class__.__name__}")
            continue
        head, blocks = archive.split_entries(text)
        want = {e["index"] for e in items}
        # A last-resort guard, and honestly labelled: the id above is content-addressed, so a file
        # that changed since the person looked already fails to match and never reaches here. What
        # is left is the race between `propose()` inside `apply()` and these writes — microseconds
        # wide, and the one thing on the other side of it is moving the wrong entry. The suite does
        # not exercise this branch, because it cannot construct that window from outside the
        # process; it is here for the consequence, not for the coverage.
        heads_now = [blocks[i].lstrip("\n").partition("\n")[0].strip().lstrip("#").strip()
                     if i < len(blocks) else None for i in sorted(want)]
        if heads_now != [e["heading"] for e in sorted(items, key=lambda x: x["index"])]:
            skipped.append(f"{rel}: changed since the proposal; left untouched")
            continue
        take = [blocks[i] for i in sorted(want)]
        keep = [b for i, b in enumerate(blocks) if i not in want]
        # A heading that ALSO belongs to an entry staying behind in this same file is ambiguous in
        # link text: `[[Errata#See also]]` names both, and rewriting it would silently point it at
        # the moved one's content instead. Collected here, while both halves are in hand.
        for b in keep:
            stays.add((rel, b.lstrip("\n").partition("\n")[0].strip().lstrip("#").strip()))
        plan.append((live, head, take, keep, Path(rel).stem))

    if not plan:
        return {"applied": False, "why": "nothing movable", "skipped": skipped}

    # The write door, on the three paths in this module that do not already go through
    # `archive.atomic_write`. `sub` is built from a name a person typed, and `_valid_name` above
    # has already refused anything with a separator in it — but a refusal derived from the PATH is
    # what the door is for, and a validator two screens up is not one.
    rootguard.permit(sub, why="split: the new sub-region's directory")
    sub.mkdir(parents=True)
    rootguard.permit(sub / "Map.md", why="split: the new sub-region's index")
    (sub / "Map.md").write_text(
        f"# {name}\n\nA sub-region of [[{region.name}/Map|{region.name}]], split off on {today()} "
        f"because {len(prop['entries'])} entries had gathered on one topic "
        f"({', '.join(prop['shared_terms'][:5])}).\n\n"
        f"Its entries were moved here unchanged; the originals' headings are the same.\n",
        encoding="utf-8")

    for live, head, take, keep, stem in plan:
        dest = sub / live.name
        prior = dest.read_text(encoding="utf-8") if dest.is_file() else f"# {stem}\n"
        archive.atomic_write(dest, prior + "".join(take))
        archive.atomic_write(live, head + "".join(keep))
        for block in take:
            moved.append({"from": str(live.relative_to(vault)),
                          "to": str(dest.relative_to(vault)),
                          "heading": block.lstrip("\n").partition("\n")[0].strip().lstrip("#").strip()})

    rows = add_map_row(region, name, len(moved))
    relinked, unlinkable = relink(vault, moved, name, stays)
    receipt = {"applied": True, "day": today(), "region": region_rel, "sub_region": name,
               "moved": moved, "skipped": skipped, "map_row": rows,
               "relinked": relinked, "unlinkable": unlinkable}
    rootguard.permit(sub / "SPLIT-RECEIPT.md", why="split: the receipt that makes the move undoable")
    (sub / "SPLIT-RECEIPT.md").write_text(render_receipt(receipt), encoding="utf-8")
    return receipt


def add_map_row(region: Path, name: str, n: int) -> bool:
    """One row in the parent's index. Appended, never rewritten: a Map a person has arranged is not
    this function's to reformat."""
    m = region / "Map.md"
    try:
        text = m.read_text(encoding="utf-8") if m.is_file() else f"# {region.name}\n"
    except OSError:
        return False
    if not text.endswith("\n"):
        text += "\n"
    archive.atomic_write(m, text + f"\n- [[{name}/Map|{name}]] — {n} entries, split off {today()}.\n")
    return True


_LINK = re.compile(r"\[\[([^\[\]|#]+)#([^\]|]+)")
# A heading may contain `[` and may not contain `]` or `|`: a wikilink ends at the first and an
# alias opens at the second. The TARGET excludes `[` as well — it is a file path.
#
# There is no end-anchor and none is needed, because the heading is CAPTURED, never assembled. The
# first implementation built a pattern out of the heading text, where a moved heading that is a
# prefix of an unmoved one matched the longer link and retargeted it; capturing the whole run up to
# the terminator and comparing the result as a string removes that possibility rather than guarding
# against it. `test_a_heading_that_is_a_PREFIX_of_an_unmoved_one_is_left_alone` pins the behaviour,
# and it goes red against a prefix-matching implementation — which is the mutation that reaches it.


def _resolve(vault: Path, source: Path, target: str) -> Path | None:
    """What file does `[[target#…]]`, written in `source`, actually point at?

    Tried the way a reader resolves it: beside the linking file first, then from the vault root.
    `..` segments fall out of the first form for free. None when it resolves to nothing — a link
    that was already dangling is not this function's to repair."""
    t = target.strip()
    if not t:
        return None
    for cand in (source.parent / f"{t}.md", vault / f"{t}.md"):
        try:
            c = cand.resolve()
        except OSError:
            continue
        if c.is_file():
            return c
    return None


UNLINKABLE = re.compile(r"[\]|]")
# A heading a wikilink CANNOT carry unambiguously. `]]` ends a link and `|` opens an alias, so a
# heading containing either cannot be matched in link text without guessing where the link stops.
# Such an entry still moves; what cannot be done is find the links pointing at it, and that is
# NAMED in the receipt rather than left as a silent no-op — the whole class of defect this
# function's reviewer found was a link quietly left wrong.


def relink(vault: Path, moved: list[dict], name: str,
           stays: set[tuple[str, str]] | None = None) -> tuple[list[str], list[str]]:
    """Rewrite the wikilinks that pointed at a moved entry so they still resolve.

    ★ EVERY LINK IS RESOLVED BEFORE IT IS TOUCHED, and the first implementation did not do that.
    It matched on the link's target STEM plus the heading text, vault-wide, and this row's reviewer
    demonstrated three ways that is wrong — each EXECUTED against a real vault, not argued:

      1. Two moved entries sharing a heading in different files collapsed into one dictionary key,
         so a link to the first was never rewritten and was left dangling.
      2. The heading match had no end boundary, so a moved heading that is a PREFIX of an unmoved
         one retargeted the unmoved entry's link into a sub-region it is not in.
      3. There was no region scoping at all. A bare `[[Errata#…]]` in an UNRELATED region, pointing
         correctly at that region's own `Errata.md`, was rewritten because the stem and the heading
         happened to match — breaking a link that had nothing to do with the move.

    All three have the same cause: a link was identified by how it is SPELLED rather than by what it
    POINTS AT. Resolving it first answers all three at once — a link is rewritten if and only if it
    resolves to a file an entry moved OUT of, and carries that entry's heading.

    The retarget preserves the spelling it found (`Errata` -> `<Name>/Errata`, `Proj/Errata` ->
    `Proj/<Name>/Errata`, `../Proj/Errata` -> `../Proj/<Name>/Errata`), so a link relative to the
    file it lives in stays relative and a vault-rooted one stays rooted."""
    index = {}
    unlinkable = []
    stays = stays or set()
    for m in moved:
        if UNLINKABLE.search(m["heading"]):
            unlinkable.append(m["heading"])
            continue
        if (m["from"], m["heading"]) in stays:
            # AMBIGUOUS, found by this row's reviewer on the second pass. An entry with the same
            # heading stayed behind in the same file, so `[[Errata#See also]]` names both and always
            # did. Rewriting it would leave a link that still WORKS and now points at different
            # content — a quieter failure than the dangling ones, and the same class.
            unlinkable.append(m["heading"])
            continue
        try:
            index[((vault / m["from"]).resolve(), m["heading"])] = True
        except OSError:
            continue
    touched = []
    for p in sorted(vault.rglob("*.md")):
        if any(part.startswith(".") for part in p.relative_to(vault).parts):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue

        def rewrite(mo, _p=p):
            target, heading = mo.group(1), mo.group(2)
            src = _resolve(vault, _p, target)
            if src is None or (src, heading.strip()) not in index:
                return mo.group(0)
            return "[[" + _retarget(target, name) + "#" + heading

        out = _LINK.sub(rewrite, text)
        if out != text:
            archive.atomic_write(p, out)
            touched.append(str(p.relative_to(vault)))
    return touched, sorted(set(unlinkable))


def _retarget(target: str, name: str) -> str:
    """`Proj/Errata` -> `Proj/<Name>/Errata`; a bare `Errata` -> `<Name>/Errata`.

    The prefix is preserved rather than recomputed, so a link relative to the file it lives in
    stays relative and a vault-rooted one stays rooted. `relink` has already resolved the link, so
    the only thing left to change is where the last segment sits."""
    parts = target.split("/")
    return "/".join(parts[:-1] + [name, parts[-1]])


def auto_candidates(found: dict) -> tuple[list[dict], list[dict]]:
    """(may be applied by the plugin, held back for the reader) out of a proposal set.

    A held proposal is NOT a failure and is not hidden: it is exactly what the reader is shown."""
    ok, held = [], []
    for p in found["proposals"]:
        # no `or 1`: a cluster cannot exist in an empty region — it needs at least
        # `split_min_entries` members — so the guard was unreachable, the same dead-second-
        # guard shape this module removed twice already.
        n = p["region_entries"]
        if p["kind"] != SPLIT:
            held.append(dict(p, held_because="not a split"))
        elif p["remaining"] / n <= auto_min_remaining_share():
            held.append(dict(p, held_because=(
                f"would move {len(p['entries'])} of {n} and leave the parent "
                f"{p['remaining'] / n:.0%}, at or under the {auto_min_remaining_share():.0%} the "
                f"plugin may take by itself — a region left with less than that is being renamed, "
                f"not split, and that is yours to decide")))
        else:
            ok.append(p)
    return ok, held


def auto_apply(vault: Path | None = None, dry_run: bool = False) -> dict:
    """Create a sub-region for every proposal that clears the bar. ONE notice for the reader.

    Each apply RECOMPUTES the region from disk, so applying one proposal can change or retire the
    next — an id that no longer exists is reported as `superseded`, never forced. That is the same
    protection two overlapping rooms rely on, and it is why this loop cannot be written as a list
    of moves decided up front."""
    # ★ THE FUNCTION ENFORCES ITS OWN KNOB. The CLI refuses too, but a wall that only exists at
    # one entrance is not a wall: called directly with the knob off, this used to MOVE THE FILES
    # and report `enabled: False` in the same dict. A dry run is exempt on purpose — reading what
    # would happen is how someone decides whether to turn it on.
    if not auto_apply_enabled() and not dry_run:
        return {"enabled": False, "dry_run": False, "applied": [], "held": [], "superseded": [],
                "refused": [{"id": "*", "why": "`split_auto_apply` is off in this vault's limits"}],
                "min_remaining_share": auto_min_remaining_share()}
    found = propose(vault)
    ok, held = auto_candidates(found)
    # ★ A RUN STAYS INSIDE THE LANE THAT STARTED IT. `--apply` is a person naming ONE region, so
    # the partition takes care of itself; `--auto` sweeps every region in the vault and would
    # cheerfully create a sub-region inside another lane's — measured: a marker declaring `Proj/`
    # still moved 10 entries into `Other/`. That is the vault's never-write-another-lane's-
    # partition law broken by construction, and worse than a refusal would be: the Stop hook only
    # commits this lane's paths, so the other lane's region is left restructured AND dirty, with
    # nothing saying so. A marker that declares nothing, or no marker at all, leaves this empty
    # and the run unrestricted — the same fail-open the manual path has always had, and the reason
    # `--auto` says out loud which lane it is acting as.
    lane, prefixes, _marker = common.lane_for(os.getcwd())
    if prefixes:
        outside = [p for p in ok if not common.path_in_partition(p["region"], prefixes)]
        ok = [p for p in ok if common.path_in_partition(p["region"], prefixes)]
        for p in outside:
            held.append(dict(p, held_because=(
                f"is outside this lane's declared paths ({lane or 'unnamed lane'}: "
                f"{', '.join(prefixes)}) — `--auto` only ever moves inside the lane that ran it")))
    applied, superseded, refused = [], [], []
    for p in ok:
        name = p["suggested_name"]
        if dry_run:
            applied.append({"region": p["region"], "id": p["id"], "name": name,
                            "entries": len(p["entries"]), "dry_run": True})
            continue
        # ★ THE BAR IS RE-DERIVED PER APPLY, NOT TAKEN FROM THE SNAPSHOT ABOVE.
        # `auto_candidates()` judged every proposal against the region as it was when the run
        # STARTED. Each apply shrinks the region, so a later proposal that qualified then may take
        # a region that is now far smaller — and `apply()` re-checks MEMBERSHIP and KIND but never
        # the share. Measured before this loop existed: three topics of 10 in a 36-entry region,
        # each judged at "parent keeps 72%", all three applied in one run, parent left with 6 of
        # 36 = 17% — below the 20% of the very case the bar exists to hold. Applying them ONE AT A
        # TIME gave the opposite answer, so the outcome depended on nothing but whether two applies
        # shared a run.
        live = [q for q in propose(vault)["proposals"] if q["id"] == p["id"]]
        if not live:
            superseded.append({"id": p["id"], "why": "overtaken by an earlier move in this run"})
            continue
        still_ok, now_held = auto_candidates({"proposals": live})
        if now_held:
            held.append(dict(now_held[0], held_because=(
                now_held[0]["held_because"] + " — it cleared the bar when this run started and "
                "stopped clearing it once the earlier rooms in this run had moved")))
            continue
        try:
            # the WHOLE id — `apply()` matches `p["id"]`, which is "<region>#<hash>", not the
            # bare hash. Passing the hash alone made every apply look "superseded".
            r = apply(p["region"], p["id"], name, vault)
        except ValueError as exc:
            refused.append({"id": p["id"], "why": str(exc)})
            continue
        if r.get("applied"):
            applied.append({"region": p["region"], "id": p["id"], "name": name,
                            "entries": len(p["entries"])})
        elif "no proposal" in (r.get("why") or ""):
            superseded.append({"id": p["id"], "why": r["why"]})
        else:
            refused.append({"id": p["id"], "why": r.get("why", "")})
    result = {"enabled": auto_apply_enabled(), "dry_run": dry_run, "lane": lane,
              "applied": applied, "held": held, "superseded": superseded, "refused": refused,
              "min_remaining_share": auto_min_remaining_share()}
    _log_run(result)
    return result


def _log_run(result: dict) -> None:
    """One append-only row per run, INCLUDING a run that moved nothing.

    The package's Canon for the analogous unattended path (`dispose.py --auto`) is that the run
    record is always written, zero-append runs included — because the question a reader brings to
    it is "did this run and decide nothing", and a log that only records successes cannot answer
    it. Without this the only trace of what was HELD, and why, was stdout: gone the moment the
    terminal scrolled, on the one path in this package that moves files unattended.

    A logging failure must never cost a move that already happened, so it is swallowed — but the
    swallow is narrow and the reason is here rather than in a bare `except`."""
    if result["dry_run"]:
        return
    try:
        path = config.state() / "split-runs.log"
        # The package's own mutation-coverage guard caught this `mkdir` unguarded and it was right
        # to: an unguarded write is how the incident `rootguard` exists for happened. The state
        # directory IS one of the package's roots, so this is a permit rather than an exemption —
        # an exemption is a claim somebody has to stand behind, and there is nothing to claim here.
        rootguard.permit(path, "split auto-apply run record")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": today(), "lane": result["lane"],
                                 "enabled": result["enabled"],
                                 "applied": [{"region": a["region"], "name": a["name"],
                                              "entries": a["entries"]} for a in result["applied"]],
                                 "held": [{"region": h.get("region"),
                                           "why": h.get("held_because")} for h in result["held"]],
                                 "superseded": [x["id"] for x in result["superseded"]],
                                 "refused": [x["id"] for x in result["refused"]]},
                                sort_keys=True) + "\n")
    except OSError:
        pass


def render_auto(r: dict) -> str:
    """ONE notice. The reader is told what MOVED and what is waiting on them, and nothing else."""
    out = []
    verb = "would move" if r["dry_run"] else "moved"
    for a in r["applied"]:
        out.append(f"  {verb} {a['entries']} entries into {a['region']}/{a['name']}")
    if not r["applied"]:
        out.append("  nothing moved." if not r["dry_run"] else "  nothing would move.")
    for h in r["held"]:
        out.append(f"  waiting on you — {h['region']}: {h['held_because']}")
    for sup in r["superseded"]:
        out.append(f"  {sup['id']} was overtaken by an earlier move and was NOT applied; "
                   f"run again to see the region as it now stands")
    for ref in r["refused"]:
        out.append(f"  REFUSED {ref['id']} — {ref['why']}")
    return "\n".join(out)


def render_receipt(r: dict) -> str:
    lines = [f"# Split receipt — {r['region']} → {r['region']}/{r['sub_region']}, {r['day']}",
             "",
             f"{len(r['moved'])} entry(ies) moved. Nothing was deleted: each one is in exactly one "
             f"live file before and after, and this list is what makes the move undoable by hand.",
             ""]
    for m in r["moved"]:
        lines.append(f"- **{m['heading']}** — `{m['from']}` → `{m['to']}`")
    if r["skipped"]:
        lines += ["", "## Left untouched", ""]
        lines += [f"- {s}" for s in r["skipped"]]
    if r["relinked"]:
        lines += ["", f"## Wikilinks rewritten in {len(r['relinked'])} file(s)", ""]
        lines += [f"- `{f}`" for f in r["relinked"]]
    if r.get("unlinkable"):
        lines += ["", "## UNCHECKED — links to these entries were not searched for", "",
                  "Two headings cannot be followed. One containing `]` or `|` cannot be matched "
                  "inside a wikilink without guessing where the link ends. One that ALSO belongs "
                  "to an entry staying behind in the same file is ambiguous — a link naming it "
                  "named both, and always did. The entries moved; any link to one still points at "
                  "the file it left, which is the honest answer rather than a guess.", ""]
        lines += [f"- **{h}**" for h in r["unlinkable"]]
    return "\n".join(lines) + "\n"


# -------------------------------------------------------------------- render ----
def _refocus_why(p: dict) -> str:
    """Which side of the parent bar the cluster failed — the count floor, the share floor, or both.
    Before SPLITSIM-1 session 2 this always said "under the floor of N", which was false when the
    count floor passed and the share floor (a knob OFF by default) was what refused."""
    share = p.get("min_remaining_share") or 0
    n = p.get("region_entries") or 0
    under_count = p["remaining"] < p["min_remaining"]
    under_share = bool(share) and n and p["remaining"] < share * n
    if under_count and under_share:
        return f"under the floor of {p['min_remaining']} and under {share:.0%} of the region"
    if under_share:
        return f"{p['remaining'] / n:.0%} of the region, under the share floor of {share:.0%}"
    return f"under the floor of {p['min_remaining']}"


def render(found: dict) -> str:
    splits = [p for p in found["proposals"] if p["kind"] == SPLIT]
    refocus = [p for p in found["proposals"] if p["kind"] == REFOCUS]
    diffuse = [p for p in found["proposals"] if p["kind"] == DIFFUSE]
    share = found.get("min_remaining_share") or 0
    out = [f"Split proposals — {found['generated']}. "
           f"{len(splits)} topic(s) at or over {found['min_entries']} entries that would leave the "
           f"parent at least {found['min_remaining']}"
           + (f" and at least {share:.0%} of the region" if share else "")
           + (f", and {len(refocus)} that would not." if refocus else ".")
           + (f" {len(diffuse)} cluster(s) too diffuse to be one topic." if diffuse else "")]
    if not found["regions_checked"]:
        out.append("Regions: UNCHECKED — the vault could not be walked.")
    if found.get("chains"):
        c = found["chains"]
        out.append(f"  {len(c)} group(s) hang together entry-to-entry but share no subject — "
                   f"not rooms, nothing to move. A name made from one would be the region's two "
                   f"commonest words, not a topic:")
        out += [f"       {x.get('region', '?')}: {x['entries']} of {x['region_entries']} entries"
                for x in c]
    if found["unreadable"]:
        out.append(f"UNCHECKED — {len(found['unreadable'])} file(s) could not be read: "
                   + ", ".join(found["unreadable"]))
    for p in splits:
        out += ["",
                f"  {p['id']}  suggested name: {p['suggested_name']}  "
                f"({len(p['entries'])} of {p['region_entries']} entries, "
                f"{p['remaining']} left behind; {', '.join(p['shared_terms'][:6])})"]
        out += [f"       {e['file']}: {e['heading']}" for e in p["entries"]]
    for p in refocus:
        out += ["",
                f"  {p['id']}  REFOCUS — nothing to move  "
                f"({len(p['entries'])} of {p['region_entries']} entries would go, leaving "
                f"{p['remaining']}, {_refocus_why(p)})",
                f"       {p['region']} IS this topic. A better name for the REGION would be: "
                f"{p['suggested_name']} — renaming is its own pass, not this one.",
                f"       What would be left behind ({len(p['leftover'])}), which is what to look "
                f"at:"]
        out += [f"       {e['file']}: {e['heading']}" for e in p["leftover"]]
    for p in diffuse:
        out += ["",
                f"  {p['id']}  DIFFUSE — not one topic  "
                f"({len(p['entries'])} of {p['region_entries']} entries joined pairwise; "
                f"{p.get('cohesion_carriers', '?')} of them — cohesion {p['cohesion']} — carry two "
                f"of: {', '.join(p['shared_terms'][:3])}; floor {p['min_cohesion']}). "
                f"Nothing to move; the room has no single subject here."]
    if splits:
        out += ["",
                "Nothing has moved. To apply one, give it a name you would want to read in a year:",
                f"  python3 split.py --apply <region> <id> --name <SubRegion>"]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Give a topic that outgrew its region its own room.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--apply", nargs=2, metavar=("REGION", "CLUSTER_ID"))
    ap.add_argument("--name", help="the sub-region's name (required with --apply)")
    ap.add_argument("--auto", action="store_true",
                    help="create a sub-region for every proposal that clears the bar, and print "
                         "one notice. Refuses unless `split_auto_apply` is on.")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --auto: say what would move and move nothing")
    args = ap.parse_args(argv)

    if args.auto:
        if args.name:
            ap.error("--auto names every room from its own entries; --name would apply to which "
                     "one? Use --apply <region> <id> --name <TheirName> to name one yourself.")
        if not auto_apply_enabled() and not args.dry_run:
            print("REFUSED — `split_auto_apply` is off in this vault's limits. Nothing moved.",
                  file=sys.stderr)
            return 2
        r = auto_apply(dry_run=args.dry_run)
        # `default=str`, not `sorted`: every value here is JSON-native, so the net never
        # fires — and if one ever were not, `sorted` would raise on it rather than render it.
        print(json.dumps(r, indent=1, default=str) if args.json else render_auto(r))
        return 0

    if args.apply:
        if not args.name:
            ap.error("--apply needs --name: the name is the decision, and it is not mine to guess")
        try:
            r = apply(args.apply[0], args.apply[1], args.name)
        except ValueError as exc:
            print(f"REFUSED — {exc}", file=sys.stderr)
            return 2
        print(json.dumps(r, indent=1) if args.json else
              (render_receipt(r) if r["applied"] else f"REFUSED — {r['why']}"))
        return 0 if r["applied"] else 1

    found = propose()
    print(json.dumps(found, indent=1, default=sorted) if args.json else render(found))
    return 0


if __name__ == "__main__":
    sys.exit(main())
