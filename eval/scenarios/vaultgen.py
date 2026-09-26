#!/usr/bin/env python3
"""vaultgen.py — a fixture-vault GENERATOR with shape parameters, for scenario tests of the plugin.

    from vaultgen import Shape, Topic, generate
    generate(Shape(topics=[Topic("alpha", n=8)], noise=4), vault_dir)

**Why the growth simulator's `Author` could not do this.** `eval/simulator/run.py` writes entries whose
vocabulary is uniform (`SUBJECTS` x `ASPECTS`), because it measures BYTES and RECALL; two of its
entries share a term only by accident, so `split.cluster()` — which joins two entries when they share
`synthesis.MIN_SHARED_TERMS` (3) significant terms in heading + bold lines — can never form a cluster
from them. A harness for the split rule has to control exactly that: which entries share vocabulary,
how many, how tightly, and where in the region they sit.

**What a shape controls.**

  Topic(name, n, vocab=3, k=3, spread=None)
      `n` entries drawn from a pool of `vocab` synthetic words, `k` per entry. With `vocab == k == 3`
      every pair shares all three words: a CLIQUE, pair density 1.0 — the fixture `test_split.py` uses.
      With `vocab > k` the pairs overlap at random and the cluster is SPARSE: two entries join only when
      they happen to share 3 of their words, and the topic may percolate into one component or break
      into several. That dial exists because the live vault's clusters are NOT cliques (pair density
      0.09-0.36 measured 2026-09-18), and a rule tested only on cliques is tested only on the case it
      cannot get wrong.
      `spread` is how the entries fall across role files: `{"Errata": 3, "Patterns": 5}` (exact
      counts, must sum to n) or `{"Errata": 0.7, "Canon": 0.3}` (weights).

  Shape(region, topics, noise, noise_spread, bridges, seed)
      `noise` entries have vocabulary nobody else has and never cluster with anything.
      `bridges=[(a, b, count)]` writes entries carrying 3 words of topic a AND 3 words of topic b — the
      union-find then MERGES the two topics into one component. This is the percolation shape and it is
      how a real region ends up proposing one 212-of-266 "topic" named after its two commonest words.

**Vocabulary is synthetic and disjoint.** Words are 6-8 letters, consonant-vowel syllables, never in
`recall.STOP`, never reused across topics or noise within one shape, and never copied from any vault:
the shape of a real vault (counts per region and role file) can be reproduced here without one line of
its text. Everything is seeded, so a shape regenerates byte-for-byte and a cluster's content-keyed id
(`split.cluster_id`) is stable across runs of the same shape — which is what makes a growth scenario
("the same region, N entries later") able to say whether a proposal is the SAME proposal.

**Nothing here decides anything.** The generator writes files; `split.py` (the real one, as a
subprocess) reads them. No number the product computes is recomputed here.
"""
from __future__ import annotations

import os
import random
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN))
import recall  # noqa: E402  — its STOP list, so no synthetic word is a stopword

ROLE_FILES = ("Errata.md", "Patterns.md", "Canon.md", "Position.md", "Course.md", "Aporia.md")
STEMS = tuple(p[:-3] for p in ROLE_FILES)
DEFAULT_SPREAD = {"Errata": 0.3, "Patterns": 0.3, "Canon": 0.2, "Position": 0.1, "Course": 0.05,
                  "Aporia": 0.05}

_CONS = "bdfgklmnprstvz"
_VOW = "aeiou"


def make_words(rng: random.Random, n: int, taken: set[str]) -> list[str]:
    """`n` fresh synthetic words, 6-8 letters, not stopwords, not already taken."""
    out: list[str] = []
    while len(out) < n:
        w = "".join(rng.choice(_CONS) + rng.choice(_VOW) for _ in range(rng.randint(3, 4)))
        if len(w) < 6 or w in taken or w in recall.STOP:
            continue
        taken.add(w)
        out.append(w)
    return out


@dataclass
class Topic:
    name: str
    n: int
    vocab: int = 3
    k: int = 3
    spread: dict | None = None

    def __post_init__(self):
        if self.k < 3:
            raise ValueError("k < 3 can never cluster: MIN_SHARED_TERMS is 3")
        if self.vocab < self.k:
            raise ValueError("vocab must be >= k")


@dataclass
class Shape:
    topics: list = field(default_factory=list)
    noise: int = 0
    region: str = "Proj"
    noise_spread: dict | None = None
    bridges: list = field(default_factory=list)      # (topic_index_a, topic_index_b, count)
    seed: int = 7
    # ★ THE CONVENTION THE WHOLE VAULT WRITES (SPLITVOCAB-1). Words added to the BOLD line of
    # EVERY entry this shape writes — topic, bridge and noise alike — the synthetic stand-in for
    # `**Scope:** universal`, `**Last revisited:**`, a NEVER/ALWAYS line. They are passed IN
    # rather than generated, because the point of them is that SEVERAL REGIONS write the same
    # ones: a per-shape pool would make each region's house style its own vocabulary, which is
    # the one thing house style is not.
    #
    # Nothing in the generator before this could express a convention, and that is why the
    # scenario suite scored a rule safe that the real vault refuted: a fixture cannot contain the
    # conventions of the system it is a fixture for unless someone puts them there.
    boilerplate: list = field(default_factory=list)
    # What SHARE of this region's entries carry it. 1.0 = the whole region (a vault whose every
    # entry is a Canon entry); below 1.0 the carriers are the FIRST `round(share * n)` entries in
    # generation order — topics first, then bridges, then noise — which is deterministic and lets a
    # scenario put the convention on exactly the entries it means to. A real convention is carried
    # by a KIND of entry (`**Scope:**` under every Canon entry and nowhere else), never by a random
    # 60%, and a fixture where every single entry carries it cannot show the defect at all: the
    # welded component is then the WHOLE region, which `cluster()` already refuses as "not a
    # sub-region of itself".
    boilerplate_share: float = 1.0


@dataclass
class Entry:
    stem: str
    heading: str
    body: str
    kind: str          # "topic:<name>" | "noise" | "bridge:<a>+<b>"

    def text(self) -> str:
        return f"\n## {self.heading}\n\n{self.body}\n"


def _assign_stems(rng: random.Random, n: int, spread: dict | None) -> list[str]:
    spread = spread or DEFAULT_SPREAD
    bad = set(spread) - set(STEMS)
    if bad:
        raise ValueError(f"unknown role stems in spread: {sorted(bad)}")
    if all(isinstance(v, int) for v in spread.values()):
        if sum(spread.values()) != n:
            raise ValueError(f"exact spread sums to {sum(spread.values())}, topic has {n} entries")
        out = [s for s, c in spread.items() for _ in range(c)]
        rng.shuffle(out)
        return out
    stems = list(spread)
    weights = [float(spread[s]) for s in stems]
    return [rng.choices(stems, weights)[0] for _ in range(n)]


def entries_for(shape: Shape) -> list[Entry]:
    """Every entry the shape describes, in a deterministic order. Per-topic RNGs, so growing one topic
    does not reshuffle another: entry i of topic t is the same text in every shape that contains it."""
    # Reserved FIRST, so no topic or noise word can ever be generated equal to a convention word —
    # a collision would put a topic's own vocabulary into every region of the vault.
    taken: set[str] = set(shape.boilerplate)
    vocab_rng = random.Random(shape.seed * 7919 + 1)
    pools = []
    for t in shape.topics:
        pools.append(make_words(vocab_rng, t.vocab, taken))
    out: list[Entry] = []
    for ti, t in enumerate(shape.topics):
        # Stems from one stream (its first n draws are the same whatever n is), words from a stream
        # PER ENTRY keyed on (seed, topic, i) — so growing a topic from 10 to 14 leaves entries
        # 0-9 byte-identical. Before SPLITSIM-1 session 2 one stream served both, and the extra
        # stem draws of a larger n shifted every entry's words (the prefix test caught it).
        stem_rng = random.Random(shape.seed * 104729 + ti * 31 + 5)
        stems = _assign_stems(stem_rng, t.n, t.spread)
        pool = pools[ti]
        for i in range(t.n):
            rng = random.Random(shape.seed * 104729 + ti * 31 + 5 + (i + 1) * 1_000_003)
            words = pool if t.vocab == t.k else rng.sample(pool, t.k)
            if os.environ.get("SPLITSIM_MUTANT_NO_TOPICS"):
                # The harness's own mutant: every "topic" entry gets vocabulary nobody shares, so no
                # cluster can form anywhere. A sweep under it must go entirely quiet; if a table still
                # shows a split, the table was not read from the product.
                words = make_words(rng, t.k, taken)
            uid = f"{t.name}{i:04d}"
            # Only the drawn words are >=5 letters here: the topic's label must NOT be a shared
            # term, or a sparse topic would need two overlapping words instead of three and the
            # cohesion dial would lie. `uid` is one token, unique per entry.
            heading = f"{' '.join(words)} ({uid})"
            body = (f"**{' '.join(words[:2])}** — one more case of it, {uid}. "
                    f"Body text is not read by the cluster rule.")
            out.append(Entry(stems[i], heading, body, f"topic:{t.name}"))
    for bi, (a, b, count) in enumerate(shape.bridges):
        rng = random.Random(shape.seed * 7 + 900 + bi)
        stems = _assign_stems(rng, count, None)
        for i in range(count):
            wa = pools[a][:3] if shape.topics[a].vocab == 3 else rng.sample(pools[a], 3)
            wb = pools[b][:3] if shape.topics[b].vocab == 3 else rng.sample(pools[b], 3)
            uid = f"bridge{bi}x{i:03d}"
            heading = f"{' '.join(wa)} and {' '.join(wb)} ({uid})"
            out.append(Entry(stems[i], heading, f"An entry that belongs to both topics ({uid}).",
                             f"bridge:{a}+{b}"))
    if shape.noise:
        rng = random.Random(shape.seed * 3 + 77)
        stems = _assign_stems(rng, shape.noise, shape.noise_spread)
        nrng = random.Random(shape.seed * 13 + 99)
        for i in range(shape.noise):
            words = make_words(nrng, 3, taken)
            uid = f"noise{i:04d}"
            out.append(Entry(stems[i], f"{' '.join(words)} ({uid})",
                             f"Nothing here is shared with any other entry ({uid}).", "noise"))
    if shape.boilerplate:
        # In the BOLD span, where `synthesis.significant_terms` reads — the same place the real
        # convention sits (`**Scope:** universal`). Prepended to what the entry already emphasises,
        # so a topic entry keeps its own bold words and gains the house style, exactly as an entry
        # in a vault with a house style does.
        line = " ".join(shape.boilerplate)
        n_carry = int(round(shape.boilerplate_share * len(out)))
        for e in out[:n_carry]:
            e.body = f"**{line}** — {e.body}"

    # Interleave so a topic is not a contiguous block in any file — real regions are not sorted by
    # topic, and `apply()`'s index arithmetic is exercised on gaps rather than on one run.
    random.Random(shape.seed * 17 + 3).shuffle(out)
    return out


def write_region(vault: Path, region: str, entries: list[Entry]) -> dict:
    """Write one region: Map.md + one file per role stem that has entries (plus an EMPTY Canon.md,
    counted as zero entries, so a region always has at least three files). Returns per-stem counts."""
    rd = vault / region
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "Map.md").write_text(f"# {Path(region).name}\n", encoding="utf-8")
    by: dict[str, list[Entry]] = {}
    for e in entries:
        by.setdefault(e.stem, []).append(e)
    counts = {}
    for stem in STEMS:
        got = by.get(stem, [])
        if not got and stem != "Canon":
            continue
        (rd / f"{stem}.md").write_text(f"# {stem}\n" + "".join(e.text() for e in got),
                                       encoding="utf-8")
        counts[stem] = len(got)
    return counts


def generate(shape: Shape, vault: Path) -> dict:
    """Write the shape into `vault/<region>` and return a manifest: counts per stem, per kind, and the
    heading set per topic (so a test can check the product's proposal against the INTENT)."""
    entries = entries_for(shape)
    counts = write_region(vault, shape.region, entries)
    kinds: dict[str, list[str]] = {}
    for e in entries:
        kinds.setdefault(e.kind, []).append(e.heading)
    return {"region": shape.region, "n": len(entries), "per_stem": counts,
            "per_kind": {k: len(v) for k, v in kinds.items()},
            # How many entries actually carry the convention — counted from the written text, not
            # from the parameter, so a scenario asserts what the fixture IS rather than what it
            # asked for.
            "boilerplate_carriers": sum(1 for e in entries if shape.boilerplate
                                        and f"**{' '.join(shape.boilerplate)}**" in e.body),
            "headings": kinds}


def append_entries(vault: Path, region: str, entries: list[Entry]) -> None:
    """Grow a region in place — the way a session does, an append under a `##` heading."""
    for e in entries:
        p = vault / region / f"{e.stem}.md"
        prior = p.read_text(encoding="utf-8") if p.is_file() else f"# {e.stem}\n"
        p.write_text(prior + e.text(), encoding="utf-8")


_HASH = re.compile(r"[^a-z0-9]+")


def from_counts(region_label: str, cluster: dict, rest: dict, *, seed: int = 7,
                vocab: int = 3, k: int = 3) -> Shape:
    """A shape that reproduces a MEASURED region: `cluster` = per-stem counts of the proposed cluster,
    `rest` = per-stem counts of everything else. Counts only — no vault text comes through here."""
    n_c = sum(cluster.values())
    n_r = sum(rest.values())
    topic = Topic(_HASH.sub("", region_label.lower())[:12] or "topic", n_c, vocab=vocab, k=k,
                  spread={s: c for s, c in cluster.items() if c} or None)
    return Shape(topics=[topic], noise=n_r, region=region_label, seed=seed,
                 noise_spread={s: c for s, c in rest.items() if c} or None)
