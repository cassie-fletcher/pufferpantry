# OCR Scoring Metric — Design Notes

Captured from a 2026-04-22 session looking at six preprocessing variants of
Sunday Chicken (`ocr_preprocessing_brainstorm.py` output). The purpose of
this doc is to fix what we want the metric to *do* before we write the
scorer — so we don't end up with a number that ranks things in an order we
don't trust. These are captured decisions and open questions, not commitments.

## What the raw-data inspection told us we need

Looking at the six `tesseract_brainstorm_*.txt` files against the Sunday
Chicken ground truth surfaced the following classes of failure. Each is a
thing the metric has to be sensitive to — or explicitly decide to ignore.

1. **Character-level misreads.**
   `200°F → 200°R`, `425°F → 4 E`, `pot → put`. Individually one-character
   edits; semantically very different in load. The metric cannot weight all
   characters equally.

2. **Glyph-class confusions.**
   `1½` → `1%` (raw, sauvola) / `TA` (lanczos). The OCR is not reading the
   wrong *text*; it is mistaking one *glyph shape* for a visually adjacent
   one. This is a different failure mode than a random-noise misread and
   probably wants its own diagnostic view.

3. **Layout damage.**
   The two-column ingredient block gets interleaved unpredictably across
   variants (e.g. `1 (4-pound) chicken` appears immediately before
   `Chopped fresh basil and/or parsley`). The *characters* present may be
   mostly correct; the *order* is not. A pure bag-of-characters recall
   score would be blind to this.

4. **Missing content.**
   `bilateral` dropped from 3327 chars (raw) to 1681 — about half the
   headnote and much of the ingredient block is just gone. An edit-distance
   score computed only over what the OCR emitted would look artificially
   clean on severe truncation. The metric must penalize omission.

5. **Semantic-load asymmetry.**
   A typo in the headnote prose matters much less than a typo in a
   quantity, unit, or temperature. The extraction product cares about the
   structured fields; the prose is context. The metric probably needs to
   separate these rather than pool them.

6. **Encoding / rendering equivalence (both directions).**
   `½` / `1/2` / `0.5` all encode one-half; a raw char-diff over-penalizes
   valid renderings of the same meaning. Conversely `°F` / `°R` differ by
   one codepoint but the semantic gap is large. Comparison must not happen
   at the raw-codepoint level.

## Design implications

### A canonicalization layer sits *before* any metric

The CLAUDE.md quantity-handling section already anticipates this: ground
truth stores quantities as decimals, and OCR outputs will need to be
parsed to the same form before comparing. Canonicalization is broader
than quantities though — at minimum:

- Unicode fraction glyphs → decimal (`½ → 0.5`, `¼ → 0.25`, `¾ → 0.75`)
  or a common string form
- Degree / temperature variants → common form (`200°F`, `200 °F`,
  `200°F` all collapse)
- Whitespace / line-wrap normalization (collapse runs, optionally
  strip)
- Quote / dash normalization (`'` vs `'`, `-` vs `–` vs `—`)
- Ligatures (`ﬁ`, `ﬂ`) decomposed

This has to happen for *both sides* of the comparison (ground truth is
already clean YAML, but OCR output is not). Open question: do we apply
the same canonicalization aggressiveness to both, or asymmetrically?

### The metric is probably not one number

Things the metric likely needs to report, separately (roll-up to one
score is a later step, if useful at all):

- **Prose-level character recall/precision** on the free-text regions
  (headnote, step text) — post-canonicalization.
- **Field-level exact or near-match** on structured extractables
  (ingredient quantity, unit, item; cook times; oven temps; serves).
  Probably this is the score we actually care about for the
  extraction product.
- **Coverage / completeness** — what fraction of expected content is
  present at all. Catches bilateral-style collapse.
- **Layout fidelity** — some measure of whether lines are in the
  correct order (row-level, not char-level). May be a separate
  ordering metric (e.g. Kendall-tau on line identity) rather than
  folded into recall.
- **Glyph-confusion class breakdown** — diagnostic only; not used for
  ranking. Useful for deciding which preprocessing / Tesseract config
  to try next.

### The scoring script needs to parse, not just diff

To compute field-level scores, the scorer has to parse OCR text into
the same schema as the ground truth YAML. Two sub-problems this
introduces:

- **Structure recovery from unstructured text** — detecting
  "this line is an ingredient", splitting quantity/unit/item.
  This is its own small extraction problem, separate from OCR.
- **Parser robustness vs scorer fairness** — if the parser is strict
  and the OCR is messy, the metric penalizes parser failures as OCR
  failures. If the parser is lenient, it masks real OCR damage.
  We need a defensible position on where to draw this line.

## Workflow: how we pick each metric

We don't pick metrics theoretically and hope they work — we validate each
candidate empirically against cases where we already know the right answer.
The loop, run separately for each requirement from the first section:

1. **Brainstorm a few candidate metrics** for that requirement
   (e.g. for "character-level misreads": raw char-diff count, char-level
   precision/recall, normalized Levenshtein, etc.).

2. **Pick a variant that is known to exhibit that failure class more than
   the baseline.** For some requirements we have a clear case — `bilateral`
   for missing content; fraction-garbling variants for glyph-class
   confusion. For requirements where no existing variant isolates the
   failure cleanly (e.g. pure character misreads with everything else
   equal), we construct a synthetic case by injecting known errors into
   the baseline text.

3. **Score baseline and the flawed variant with each candidate metric.**

4. **Evaluate which candidate(s) correctly rank the flawed variant below
   the baseline.** A metric that fails this test — ranks the flawed
   variant equal to or above baseline — gets discarded, regardless of how
   principled it looked on paper.

5. **Every metric we keep must have been tested this way.** No
   untested metrics in the final scorer. This is the rule.

Consequences of this workflow worth being explicit about:

- **Multiple candidates pass for the same requirement:** pick the
  computationally cheapest one. If the top candidates are all cheap,
  just keep them all (as separate reported numbers).
- **No candidate passes:** stop and have a discussion about the
  requirement itself. Is it worth pursuing? Are there other approaches
  (different framing, different canonicalization, different parser
  strategy) we haven't considered? Don't force a bad metric into the
  scorer to fill a slot.
- The test cases we construct become part of the scorer's test suite.
  Once a metric is accepted, re-running the test on it later is how we
  detect regressions from canonicalization / parser changes.

## Open questions (don't decide until next session)

1. **Do we need sequence-aware metrics** (Levenshtein, or similar)
   on prose, or is bag-of-tokens with precision/recall enough?
   Sequence-aware is more expensive to interpret but catches
   systematic transpositions.

2. **How aggressive is canonicalization?** If we collapse `½ → 0.5`
   on both sides, do we lose the ability to diagnose "OCR emitted a
   literal `½` vs OCR emitted `1/2` vs OCR emitted garbage"?
   Maybe keep a raw-comparison view alongside the canonicalized one.

3. **Ordering vs content as separate axes** — should layout damage
   drop a variant's rank, or just flag it on a separate axis? If
   layout is damaged but content is recovered, for the extraction
   product that may be fine; for OCR quality per se, it's a problem.

4. **One-recipe vs multi-recipe metric stability.** Everything here
   is defined on Sunday Chicken (n=1). The validation-task #4 item
   ("generalize to Salmon + others") is the real test of whether
   these metrics are picking up something stable or overfitting to
   one recipe's quirks.

5. **Weighting / aggregation.** Do we ever produce a single scalar
   for ranking, or always report the vector? A scalar is convenient
   for "did preprocessing variant X win?" — but picking weights is a
   decision that bakes assumptions into every downstream comparison.

## What this doc is not

- Not a spec for the scorer implementation. No function signatures,
  no library choices, no data shapes. Those are next-session work.
- Not a commitment to all of the above. The design implications
  section contains Claude's read of what the failures imply; Cassie
  should push back on anything that doesn't match her priors before
  we commit.
