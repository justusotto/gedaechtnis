# Recall bench — pre-registered claim (WP6)

Quoted verbatim from `.orchestration/review/gedaechtnis-everybody-2026-09-09/DESIGN.md` §4.4, dated
**2026-09-09**, the day this skeleton was built. Per the standing rule on pre-registration
(`Global/Patterns-verification.md` — a bar holds after the numbers land, including when
inconvenient; it is superseded only by a new, dated ruling, never re-read to rescue a run), this
text is not to be edited when a result comes in. A change to the claim is a NEW dated entry below
it, never an edit to this one.

> **Recall bench:** 30 held-out questions whose answers are known entries (drawn from the vault's
> Errata/Patterns files by someone other than the builder), three arms: (a) today's grep recall,
> (b) index-first, (c) link-walk from the region Map. Metrics: recall@3 (does the right
> `File#heading` appear in the top 3), tokens-to-answer (bytes read into context before the
> answer), wall time. **Claim:** index-first beats grep by ≥30% tokens-to-answer at recall@3 ≥
> grep's. **If it does not, the index is not built into recall** and stays a tool for the
> vanished-terms chore only. Noise floor measured by a repeat of arm (a).
>
> **Second claim, cheaper:** ≥70% of cross-region wikilinks in the vault point at a `#heading`, not
> a bare file (measured before the build; if bare-file links dominate, the entry unit is not how
> the system is actually used and the index needs a per-file fallback first).

(The DESIGN.md source says "his" — the vault this claim was written against; the wording above
keeps the claim's substance UNCHANGED per WP6's build brief and just avoids naming anyone, since
this file ships inside the public plugin. Nothing about the metric, the arms, or the ≥30% / ≥70%
thresholds has been altered.)

## What this skeleton can and cannot decide yet

- **Arm (a), grep, is implemented** — `recall_bench/run.py --arm grep` runs the real
  `gedaechtnis/recall.py` against a vault and scores recall@N and bytes-to-answer.
- **Arms (b) index-first and (c) link-walk are NOT implemented.** `run.py --arm index` and
  `--arm walk` both raise `NotImplementedError`, pointing back at DESIGN.md §4.3 ("the winner:
  role files as containers, entries as the unit, one derived index") — the index builder and the
  index-first recall step are WP6's remaining, deferred work (DESIGN.md §8, R7: "WP6 and WP8 are
  DEFERRED until the owner's session limit is comfortable — only their harness skeletons are
  written").
- **The ≥30% and ≥70% claims above CANNOT be decided by this skeleton alone** — deciding them needs
  arm (b) built and a real 30-question set drawn from a real vault, neither of which exists yet.
  What this skeleton proves today is only that the SCORING MACHINERY (recall@N, bytes-to-answer,
  the per-metric noise floor from a repeat of the control) is correct and produces a reproducible
  table — so that when arm (b) is built, dropping it in requires no change to how the claim gets
  decided.

## Metric definitions, exactly as `run.py` computes them

- **recall@N** — does the `path#heading` of the question's `expected` entry appear among the top N
  hits `recall.py` returns for that question. Boolean per question; the reported rate is the mean
  over the question set.
- **bytes-to-answer** — the UTF-8 byte length of every hit's body summed up to and including the
  first hit that matches `expected` (the bytes a caller would have had to read into context before
  reaching the right answer). When `expected` is not found in the top N, this is the sum of all N
  hits' bytes shown — the reader paid the full cost and got nothing.
- **noise floor** — arm (a) is run TWICE with byte-identical configuration (same vault, same
  question order, same flags); the floor for a metric is `|value(run 1) − value(run 2)|`,
  aggregated the same way the metric itself is (mean for recall@N, mean for bytes-to-answer).
  `recall.py` has no randomness, so this floor is expected to measure **0** for a deterministic
  arm — which is the correct, honest number, not a bug: per
  `Global/Patterns-verification.md` ("a floor of 0 means the two runs agreed"), a future arm that
  DOES carry randomness (a model-scored variant, say) is the case this machinery exists for.

## 2026-09-10 — first real run of arm (a), and a new arm (a′) pre-registered

Arm (a) was run on a real 30-question held-out set (drawn by a separate model session, not the builder) against a real vault of 988 files: **recall@3 = 0 of 30**, mean bytes-to-answer 1.23 MB. The expected entry was in the hit list every time, at median rank 30. Cause, measured by varying the corpus: coverage-first ranking is won by the largest sections (a 665 KB queue section covers every term); on role files alone recall@3 is 5 of 30 and the winners become 14–34 KB state sections. The 2026-09-09 claim above is unchanged.

**New arm (a′), grep length-aware**, pre-registered today, before it is built: same term extraction, coverage that a section cannot win on bulk alone, non-role surfaces excluded by default. **Claim:** on the same 30 questions and the same vault, (a′) reaches recall@3 ≥ 15 of 30 at mean bytes-to-answer ≤ 20 KB. If it does not, ranking is not the fix and arm (b) index-first is built first. Arm (b)'s ≥30% claim is then measured against (a′), the stronger control, never against the 0-of-30 baseline.

**Result (a′), 2026-09-10 18:05 — the bar FAILS on its hit half, passes its byte half.** Built and
measured the same day (`q:CU-2026-09-10-RECALL-RANK-1`; runs, table and confounds in
`.orchestration/review/recallbench-2026-09-10/RESULT.md`, addendum of 18:05). On the same 30
questions and the same vault (988 files, `.gedaechtnis/` included, as searched): **recall@3 = 11 of
30 at 16,192 B mean bytes-to-answer**, median rank of the expected entry 6 — against the bar of ≥ 15
of 30 at ≤ 20 KB. Neither half of a′ carries it alone (rank with the queues still in: 1 of 30;
exclusion with the old rank: 1 of 30), and on the row corpus a′ reaches 18 of 30 at 7,013 B where
the OLD rank already reached 15 of 30 at 30,808 B — so the ENTRY UNIT buys the hits and the RANK
buys the bytes. Per this entry's own clause, **arm (b) index-first is next**. Two measured items
that do NOT change this verdict and are recorded so they are not rediscovered: the vault's shadow
views duplicate every imported entry and outrank the live copy in 28 of 30 questions (the same rank
over the same live content without those 72 files scores 15 of 30 at 14,169 B), and
`COMMON_TERM_SHARE` was picked after seeing this question set. The bar above is not re-read in the
light of either; a decision about the shadow copies would be a NEW dated entry below this one.

## 2026-09-10 22:20 — the shadow's views excluded from recall by default, re-run pre-registered (`q:CU-2026-09-10-RECALL-VIEWS-1`)

Implements the ruling recorded on `Speculum/Ledger-decide-and-record.md` under "Recall: the
shadow's generated views are EXCLUDED from recall by default, under a NEW dated pre-registration
entry, re-run before any index arm" (2026-09-10 22:20, handed to the seat by the owner). **Product
change, built and reviewed separately from this measurement:** `.gedaechtnis/` — the log-and-views
shadow's byte-identical `# GENERATED` mirror of every imported entry — joins `Pharos/` and
`Channels/` in `recall.py`'s excluded-by-default set (`GENERATED_DIRS`, a set distinct from
`NON_MEMORY_DIRS`: queues restate the vault's vocabulary without answering anything, the shadow
answers correctly but only by duplicating an entry already found elsewhere). A new
`--include-generated` flag restores it, exactly as `--include-queues` restores `Pharos/`/`Channels/`;
the two flags are independent of each other and of `--rank`.

**Corpus:** the whole vault (`~/Atlas`), the SAME 30 questions (`questions-30.json`), with
`.gedaechtnis/`, `Pharos/` and `Channels/` all excluded — i.e. `recall.py`'s plain default, no
flags. **Rank:** `a-prime`, as built by RECALL-RANK-1 (the shipped default — the same rank the a′
entry above measured, unchanged here). **Bar: the SAME bar as the a′ entry — recall@3 ≥ 15 of 30
at mean bytes-to-answer ≤ 20 KB.** ONE run (plus its byte-identical repeat control for the noise
floor, per this file's own convention; `recall.py` is deterministic so that floor is expected to be
0, not evidence of a vacuous check). **Clause, unchanged from a′: if this run does not clear the
bar, exclusion is not the fix either, and arm (b) index-first is built next** — the same clause the
a′ entry used, now applied to the corrected corpus rather than re-applied to the a′ table.

**Caveat, carried forward and binding on how a pass here is read.** `COMMON_TERM_SHARE` = 0.05 was
chosen after seeing this exact 30-question set (a′ entry, "two residuals" §i). A pass on this run is
evidence the corrected corpus clears the bar **with the constant as measured**, not a claim that the
rank generalizes — that needs a fresh, held-out question set the constant was never tuned against.
A pass here licenses drawing a fresh 30-question entry next, never a claim of the ≥15/30 bar as a
settled property of the ranking.

**Result, 2026-09-10 22:15 — PASSES.** recall@3 = **15 of 30** at mean bytes-to-answer **14,169.3
B**, median rank of the expected entry 4, noise floor 0 on every metric. Full table and reading:
`.orchestration/review/recallbench-2026-09-10/RESULT.md`, addendum of 22:15; run directory
`.orchestration/review/recallbench-2026-09-10/run-b0-noviews-vault/`. Arm (b) index-first is NOT
built as a consequence of this run.

## Addendum 2026-09-11 — the RECALL-VIEWS-1 pass is sensitive to `COMMON_TERM_SHARE` (recorded, not a re-read of the pass)

A $0 sweep on the same vault and the same 30 questions, everything else as shipped: 0.02 → 14/30 at 8.4 KB · **0.05 → 15/30 at 14.2 KB (the pass)** · 0.10 → 11/30 at 23.8 KB · 0.20 → 11/30 at 31.8 KB. The pass holds at the one value the constant was set to after these 30 questions were seen and fails at every neighbour. The 2026-09-10 22:15 entry stands as written (it is not edited); what it licenses is narrowed: **the pass is not cited as a bar until a held-out set, drawn by script with the constant frozen at 0.05 in a dated entry written BEFORE the draw, reproduces it.** The size of that set (30 · 40 · 60) and its source are council 4's iteration-2 item. Full table: the council-4 arc's `SWEEP-common-term-share.md` (private review tree, not shipped with the plugin).

## 2026-09-11 12:10 — the held-out set (council 4 K-6), pre-registered BEFORE the draw

**Frozen, before any question exists:** `COMMON_TERM_SHARE` = **0.05**, `LENGTH_NORM_B` = 1.0, `MIN_ENTRY_CHARS` = 200, rank `a-prime`, corpus = the plain default (no `.gedaechtnis/`, no `Pharos/`, no `Channels/`, no `--include-generated`). None of these is touched after this entry, whatever the run says.

**The draw, by script and seed, never by hand:** 60 entries drawn with a fixed seed from the vault's live `Errata.md`, `Patterns.md` and `Canon.md` role files across every region, restricted to headings whose line is at least 60 days old by `git blame` (last change on or before 2026-07-13, a lower bound on the entry's age), unique in the vault, and not among the 30 headings the 2026-09-10 set used. At most 3 per file. The script writes `expected` = `path#heading`; the question writer is a separate model session that is shown the entry (heading + body) and never `recall.py`, and reuses at most two consecutive heading words in a question. The drawn list is committed before the questions are written.

**Bar, read at 0.05 ONLY:** recall@3 ≥ **30 of 60** AND mean bytes-to-answer ≤ **20 KB**. The 0.10 and 0.20 runs are printed as sensitivity rows and decide nothing. **FAIL → arm (b) index-first is built** (the recall/index ruling's own clause). Noise floor: the byte-identical repeat. The first 40 questions are AMNESIA-1's. Positive control (K-7): the same 60 on the `views.py --cold` corpus, recorded beside the vault run.

**Result, 2026-09-11 12:55 — PASSES on the held-out set.** recall@3 = **42 of 60** at mean bytes-to-answer **8,423.5 B** at 0.05 (noise floor 0); sensitivity rows 0.10 → 34 of 60 at 18.8 KB, 0.20 → 29 of 60 at 30.2 KB (printed, deciding nothing); K-7 control on the cold-view corpus 48 of 60 at 5.8 KB. One question (id 55) reuses three consecutive heading words; excluded, the run is 41 of 59. Arm (b) index-first is NOT built as a consequence. Full table, reading and the writer's blindness caveat: the private tree's `recallheldout-2026-09-11/RESULT.md`.
