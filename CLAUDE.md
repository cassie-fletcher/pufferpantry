# PufferPantry

## Collaboration mode

PufferPantry is a portfolio project — the goal is Cassie's genuine understanding,
not speed of shipping. Work this codebase in **co-create / mentorship mode**:

- **Co-write the code.** Sometimes Claude drafts, sometimes Cassie drafts; always
  both contribute. No long autonomous coding runs without check-ins.
- **Test together.** Run experiments jointly — Claude suggests what to inspect,
  Cassie runs it, we both look at the output and decide next move.
- **Pause at decision points.** Before picking an algorithm, data structure, or
  architectural choice, stop and discuss trade-offs so Cassie sees why.
- **Teach what's pedagogically important.** When a CV/ML concept, Python idiom,
  or design pattern comes up that Cassie would benefit from internalizing,
  teach it — don't just use it. Flag the moment explicitly.
- **Check for understanding.** When Cassie has just learned something non-trivial,
  quiz her (short, specific questions). Don't assume "nod = understood."
- **Whole-codebase fluency is a goal.** Cassie should be able to explain any
  file in this repo. If a change touches code she hasn't traced yet, trace it
  together first.
- **Spawn a reviewer agent** Before editing code or reaching key decision points, 
  spawn a reviewer agent to review the plan before it is executed. It should flag
  decision points to pause on, teaching moments, violations of collab mode and 
  other implicit decisions Claude would make without buy-in. 
- **DO NOT cheat and violate collab mode** Claude has done this multiple times. 
  Violations of these rules defeat the point of this exercise.  

## Development notes
- Backend: FastAPI + SQLAlchemy + SQLite
- CV pipeline: Claude vision API (Opus for recipes, Sonnet for pantry)
- Frontend: vanilla JS / Vue.js
- Tests: pytest with mocked Claude API (no integration tests yet)

## Quantity handling

- **Storage:** `amount` stays as a String in the DB
  (`app/models/recipe.py:44`). Reason: preserves compound quantities like
  "1 tablespoon thyme + 1 sprig" which a numeric field can't represent.

- **Display:** Quantities render as fractions, not decimals.

- **Fraction extraction (vision models only, e.g. Claude):** Identify
  fraction digits — especially denominators — by visual glyph shape before
  converting to text. The existing `EXTRACTION_PROMPT` in
  `app/services/photo_service.py` already does this ("two curved bumps (3)",
  "single curve (2)"). Whether this actually improves fraction accuracy is
  **untested** — to be validated empirically against ground truth.

- **Scope note:** This rule covers only Claude Vision today. OCR tools like
  Tesseract output characters directly and have no glyph-shape step; their
  fraction-handling will differ. Revisit when other CV methods are added
  (tied to the ensemble design TODO).

## Validation tasks

Empirical checks against ground truth in `scripts/ground_truth/`. Each task
produces a measurement.

1. **Evaluate new preprocessing ideas.** Prior preprocessing (Otsu binarize
   + CC-filter + deskew + upsample) empirically hurt OCR on Sunday Chicken —
   raw baseline scored best. Brainstorm new preprocessing grounded in
   observed failure modes; produce a new set of OCR outputs.

2. **Claude extraction baseline.** Run production
   `extract_recipe_from_photos` on Sunday Chicken to produce a
   vision-model output. Sets the accuracy bar the future ensemble must
   improve on.

3. **Metric exploration.** Once (1) and (2) have produced outputs, sweep a
   variety of metrics (character recall, word recall, field-level exact
   match, Levenshtein, etc.) against ground truth. Pick the ones whose
   rankings match intuition — these become the metrics we use going forward.

4. **Generalize to additional recipes.** Extend ground truth + re-score on
   Salmon (photographed, stress test) and others. Confirms findings
   generalize beyond n=1.

Cross-reference: **glyph-shape fraction extraction validation** lives as
task #6 — not duplicated here.

**Infrastructure prerequisite:** existing task #1 ("Build OCR scoring
script") underpins all of the above.

## TODO 

- [ ] **Combine OCR methods (ROVER).** Goal: combine several independent local readings of a recipe photo into one better result, with **no LLM/API calls in the ensemble**. Reading layer exists: `app/ocr/read_image(path, method=...)` with `tesseract`, `apple_vision`, `claude` (claude built but deliberately unused). Combination not designed yet — deferred by Cassie until metrics are settled. An earlier Claude-model-voter design (Sonnet voter, confidence averaging) was considered and **rejected**; do not resurrect it from old notes.

- [ ] **Shopping list: group-aware consolidation.** `shopping_service` merges same-named ingredients group-blind. With sub-recipe groups (e.g. cumin in both a main recipe and its sauce), the list must support "bought the sub-recipe ready-made → subtract its group's ingredients" (Cassie, 2026-07-31).

### Code cleanup
- [ ] "Step" is overloaded in photo_service.py — means both extraction pipeline stages and recipe cooking steps. Rename to disambiguate.
- [ ] Validate Claude's JSON response against a Pydantic schema after `_parse_claude_json` (line ~351). Currently no check that required fields exist or have correct types.
- [ ] Replace raw `dict` parameter in `extract_from_url` (routers/recipes.py) with a Pydantic model for input validation.

### New features (laptop-friendly)

### Future ideas (to refine into action items)

**Pantry/fridge model — intended design:**
The pantry system is NOT designed around constant re-scanning. The intended flow is:
1. **Setup (once):** photo of whole fridge → Claude identifies zones (shelves, drawers, door) → user confirms. Fridge structure doesn't change, so this is a one-time step.
2. **Initial seed (once per zone):** close-up photo of each zone → Claude identifies items → user reviews → saved to inventory.
3. **Ongoing (automatic):** depletion tracking is the primary update mechanism. When a recipe is cooked or a shopping list is fulfilled, pantry quantities update automatically. No photos needed.
4. **Periodic reality check (produce/drawers):** re-scan zones with high turnover or items that bypass the recipe system (snacking, spoilage, produce going bad). Crisper drawers need this most — their contents change independently of recipes.

Current code has steps 1, 2, and 4 mostly built. Step 3 (depletion tracking) is the key missing piece.


- Smart comparison between pantry inventory and recipe needs (e.g. half a quart of milk vs 1 cup needed — do we need more? is it expired?)
- Model of household staples that we always keep stocked, independent of recipes

**Shopping list:**
- Email shopping list to Chris (probably straightforward)
- Store routing — know which ingredients come from which store (e.g. sushi-grade salmon from the specialty store vs regular grocery for baking salmon). Lower priority, nice-to-have.

**Recipes:**
- Recommendation system based on: how much we liked a recipe, protein type, seasonality of ingredients, what's already in the pantry, and available cooking time
- Integration with Skylight frame for meal scheduling/display

**Overall:**
- Mobile app or polished GUI as the final layer
