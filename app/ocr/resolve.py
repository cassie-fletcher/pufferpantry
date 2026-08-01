"""Sub-recipe reference resolution, shared by the text-based OCR readers.

The owner's model: a sub-recipe appears AS AN INGREDIENT in the main recipe
("Goddess Sauce (page 153) or store-bought green goddess dressing"), and the
reader — like a human cook — looks for that unknown ingredient elsewhere in
the pages it was given.

Multi-page reads used to stack/concatenate ALL pages as one recipe. With this
module, every page after the first is classified:

  REFERENCED page: its text contains a heading that matches a main-list
      ingredient (heading-driven trigger). Only the matched block is
      extracted, parsed as a mini-recipe, and merged into the schema;
      everything else on that page is DISCARDED.
  CONTINUATION page: no matching heading. It joins the main parse exactly as
      before (tesseract: text concatenation; vision: geometric stacking).

Merge shape (existing production conventions from the Claude extraction
prompt): block ingredients get ``group="<heading as printed>"``;
block instructions are appended to ``schema.instructions`` as
``"\\n\\n--- <Group> ---\\n<text>"``; the referencing ingredient line stays in
Main untouched.

Like ``app/ocr/normalize.py``, everything here is a pure function on TEXT —
method-agnostic, deterministic, no I/O. Same inputs in, same outputs out.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from app.ocr.normalize import normalize_ingredients
from app.schemas.recipe import IngredientCreate, PhotoExtractResult

# --------------------------------------------------------------------------
# Heading detection
# --------------------------------------------------------------------------

# RULE (heading shape): a heading candidate is a short line — at most this
# many words — that is mostly letters and is set in ALL-CAPS or Title Case
# (every word containing a letter starts with an uppercase letter).
HEADING_MAX_WORDS = 6

# "Mostly letters": at least this fraction of the line's non-space characters
# are alphabetic. Filters out rules/ornaments and number-heavy lines while
# still admitting headings like "SERVES 4" (which is then rejected by the
# MATCHING rule, not the shape rule).
HEADING_MIN_ALPHA_FRACTION = 0.6

# RULE (heading match): after normalizing both sides (lowercase, collapse
# whitespace, strip edge punctuation), a heading matches an ingredient when
# its tokens are a subset of the ingredient NAME'S HEAD — the name up to the
# first comma or parenthesis — or when the difflib ratio against that head
# reaches this threshold. Reference case: heading "GODDESS SAUCE" matches
# ingredient "Goddess Sauce (page 153) or store-bought green goddess
# dressing" (head = "Goddess Sauce", token subset).
FUZZY_MATCH_THRESHOLD = 0.75

# Punctuation stripped from token edges during normalization.
_EDGE_PUNCT = ".,;:!?()[]{}'\"*-–—_"


# --------------------------------------------------------------------------
# Block parsing constants
# --------------------------------------------------------------------------

# RULE (block ingredient, unit): inside an extracted block, the token after
# the leading quantity is the unit iff it is one of these words
# (case-insensitive, edge punctuation stripped). Otherwise the whole
# remainder is the name.
UNIT_WORDS: frozenset[str] = frozenset(
    {
        "cup", "cups",
        "tablespoon", "tablespoons", "tbsp", "tbsps",
        "teaspoon", "teaspoons", "tsp", "tsps",
        "pound", "pounds", "lb", "lbs",
        "ounce", "ounces", "oz",
        "gram", "grams", "g", "kilogram", "kilograms", "kg",
        "milliliter", "milliliters", "ml", "liter", "liters", "l",
        "quart", "quarts", "pint", "pints", "gallon", "gallons",
        "clove", "cloves",
        "sprig", "sprigs",
        "slice", "slices",
        "stick", "sticks",
        "stalk", "stalks",
        "bunch", "bunches",
        "head", "heads",
        "can", "cans", "jar", "jars", "package", "packages",
        "pinch", "pinches", "dash", "dashes", "handful", "handfuls",
        "piece", "pieces", "sheet", "sheets",
    }
)

# Unicode vulgar fractions -> ASCII, so the quantity rule sees one
# representation. Kept local (not imported from a reader) so this module
# stays engine-free.
UNICODE_FRACTIONS: dict[str, str] = {
    "½": "1/2",
    "⅓": "1/3",
    "⅔": "2/3",
    "¼": "1/4",
    "¾": "3/4",
    "⅕": "1/5",
    "⅖": "2/5",
    "⅗": "3/5",
    "⅘": "4/5",
    "⅙": "1/6",
    "⅚": "5/6",
    "⅐": "1/7",
    "⅛": "1/8",
    "⅜": "3/8",
    "⅝": "5/8",
    "⅞": "7/8",
    "⅑": "1/9",
    "⅒": "1/10",
}
_FRACTION_CHARS = "".join(UNICODE_FRACTIONS)
# "1½" must become "1 1/2", not "11/2": insert a space when a glyph follows a
# digit, then rewrite every glyph to its ASCII form.
_FRACTION_AFTER_DIGIT_RE = re.compile(rf"(?<=\d)\s*([{_FRACTION_CHARS}])")
_FRACTION_RE = re.compile(rf"[{_FRACTION_CHARS}]")

# RULE (quantity-led line): a line is quantity-led iff it starts with a
# quantity — a mixed fraction ("1 1/2"), bare fraction ("1/2"), decimal, or
# integer, ASCII after fraction-glyph rewriting — followed by whitespace and
# at least one more character. "1. Blend..." does NOT match ("." is not
# whitespace), so numbered sub-recipe steps land in the instructions.
# Being quantity-led is necessary but NOT sufficient to be an ingredient:
# membership in the ingredient RUN decides (see parse_block).
_QUANTITY_RE = re.compile(
    r"^(\d+\s+\d+\s*/\s*\d+|\d+\s*/\s*\d+|\d+(?:\.\d+)?)\s+(\S.*)$"
)

# RULE (prose line): a line ending in sentence-final punctuation reads as
# prose, not as an ingredient. Ingredient lines are noun phrases ("Fine pink
# Himalayan salt"); an instruction sentence sitting right after the list
# ("Blend everything until smooth.") ends with a period. Used only to stop
# the ingredient run at a quantity-LESS line — quantity-led lines are never
# tested against it.
_PROSE_END_RE = re.compile(r"[.!?]$")


# --------------------------------------------------------------------------
# Normalization helpers
# --------------------------------------------------------------------------


def _normalize_fractions(text: str) -> str:
    """Rewrite unicode vulgar fractions to ASCII ("1½ cups" -> "1 1/2 cups")."""
    text = _FRACTION_AFTER_DIGIT_RE.sub(lambda m: " " + m.group(1), text)
    return _FRACTION_RE.sub(lambda m: UNICODE_FRACTIONS[m.group(0)], text)


def _norm_tokens(text: str) -> list[str]:
    """Lowercase, collapse whitespace, strip edge punctuation per token."""
    tokens: list[str] = []
    for token in text.lower().split():
        token = token.strip(_EDGE_PUNCT)
        if token:
            tokens.append(token)
    return tokens


def _name_head(name: str) -> str:
    """The ingredient name up to the first comma or parenthesis."""
    return re.split(r"[,(]", name, maxsplit=1)[0]


def is_heading(line: str) -> bool:
    """ALL-CAPS or Title-Case short line — used by parse_block to drop
    metadata lines ("MAKES ABOUT 1 1/2 CUPS") from a block's instructions."""
    stripped = line.strip()
    if not stripped:
        return False
    words = stripped.split()
    if len(words) > HEADING_MAX_WORDS:
        return False
    non_space = "".join(words)
    alpha = sum(ch.isalpha() for ch in non_space)
    if not non_space or alpha / len(non_space) < HEADING_MIN_ALPHA_FRACTION:
        return False
    letter_words = [w for w in words if any(ch.isalpha() for ch in w)]
    if not letter_words:
        return False
    for word in letter_words:
        first_letter = next(ch for ch in word if ch.isalpha())
        if not first_letter.isupper():
            return False
    return True


def _heading_matches_ingredient(heading: str, ingredient_name: str) -> bool:
    """The heading-match rule (see FUZZY_MATCH_THRESHOLD)."""
    heading_tokens = _norm_tokens(heading)
    head_tokens = _norm_tokens(_name_head(ingredient_name))
    if not heading_tokens or not head_tokens:
        return False
    if set(heading_tokens) <= set(head_tokens):
        return True
    ratio = difflib.SequenceMatcher(
        None, " ".join(heading_tokens), " ".join(head_tokens)
    ).ratio()
    return ratio >= FUZZY_MATCH_THRESHOLD


def _same_heading(a: str, b: str) -> bool:
    """Do two heading lines name the same thing? (Symmetric variant of the
    match rule, used to keep a repeated block heading from ending its own
    block in extract_block.)"""
    tokens_a, tokens_b = _norm_tokens(a), _norm_tokens(b)
    if not tokens_a or not tokens_b:
        return False
    if set(tokens_a) <= set(tokens_b) or set(tokens_b) <= set(tokens_a):
        return True
    ratio = difflib.SequenceMatcher(
        None, " ".join(tokens_a), " ".join(tokens_b)
    ).ratio()
    return ratio >= FUZZY_MATCH_THRESHOLD


# --------------------------------------------------------------------------
# The four public functions
# --------------------------------------------------------------------------


def split_blocks(page_text: str) -> list[str]:
    """Split page text into blocks at blank lines.

    Cassie's blocking model (2026-07-31): main-recipe content sits in the
    upper blocks; a sub-recipe is a SEPARATE block set off by substantial
    white space. Readers encode that white space as blank lines — tesseract's
    xycut path already joins its layout blocks with blank lines, and the
    vision reader inserts one wherever the vertical gap between lines is
    large (see vision._blocked_text). A text-only fallback (plain psm-3
    output) gets paragraph breaks, which is coarser but the same idea.
    """
    blocks = [b for b in re.split(r"\n\s*\n", page_text) if b.strip()]
    return blocks


@dataclass(frozen=True)
class Match:
    """A lower block on an extra page that names a main-list ingredient."""

    ingredient_index: int  # index into the ingredient list that was searched
    block_index: int  # index into split_blocks(page_text)
    heading: str  # the block's opening line as printed, stripped
    group: str  # the heading as printed — the merge group name


def find_reference(
    ingredients: list[IngredientCreate], page_text: str
) -> Match | None:
    """The lower block whose OPENING LINE names a main-list ingredient.

    RULE (Cassie's blocking model): candidate blocks are every block after
    the first — the top of a page is its own recipe's content, a sub-recipe
    lives in a visually separated block below. A block is the sub-recipe iff
    its first non-empty line (a) is not quantity-led (an ingredient line is
    never a heading — measured false positive) and (b) matches an ingredient
    name's head under the fuzzy rule. No match on any block -> the page is a
    continuation, or the lower blocks are just notes for that page's recipe.
    """
    blocks = split_blocks(page_text)
    for block_index, block in enumerate(blocks[1:], start=1):
        first = next((l.strip() for l in block.splitlines() if l.strip()), "")
        if not first:
            continue
        if _QUANTITY_RE.match(_normalize_fractions(first)):
            continue  # an ingredient line is never a heading
        if len(first.split()) > HEADING_MAX_WORDS:
            continue  # sub-recipe blocks open with their name, not prose
        for ingredient_index, ingredient in enumerate(ingredients):
            if _heading_matches_ingredient(first, ingredient.name):
                # Group is the heading AS PRINTED (Cassie's ruling: the name
                # of the thing, in the page's own casing) — not re-cased.
                return Match(
                    ingredient_index=ingredient_index,
                    block_index=block_index,
                    heading=first,
                    group=first.strip(_EDGE_PUNCT + " "),
                )
    return None


def _find_ingredient_run(
    kept: list[str], quantity_led: list[bool]
) -> tuple[int, int]:
    """The half-open [start, end) index range of the block's ingredient run.

    RULE (ingredient run): a block's ingredients form ONE contiguous run of
    lines in which at least half are quantity-led. The run is built
    constructively, which both guarantees that density and makes the
    boundary unique:

      - the run starts at the first quantity-led line;
      - a quantity-led line always extends the run;
      - a quantity-less line extends the run only when (a) the line
        immediately before it is quantity-led — two quantity-less lines in
        a row mean the instruction paragraph has begun — and (b) it does
        not end like prose (see _PROSE_END_RE): "Fine pink Himalayan salt"
        joins, "Blend everything until smooth." does not.

    Because quantity-less lines can never neighbor each other inside the
    run, at least half of any run found this way is quantity-led.
    Returns (0, 0) when the block has no quantity-led line at all.
    """
    start = next((i for i, led in enumerate(quantity_led) if led), None)
    if start is None:
        return 0, 0
    end = start + 1
    for i in range(start + 1, len(kept)):
        if quantity_led[i]:
            end = i + 1
        elif quantity_led[i - 1] and not _PROSE_END_RE.search(kept[i]):
            end = i + 1
        else:
            break
    return start, end


# A sub-recipe block's yield line ("MAKES ABOUT 1 3/4 CUPS"). Captured, not
# discarded: the shopping list's store-bought line uses it as the quantity.
_YIELD_RE = re.compile(r"^\s*(?:MAKES|YIELDS?)\b[:\s]*(?:ABOUT\s+)?(.+?)\s*$", re.IGNORECASE)


def parse_block(
    block_text: str, group: str
) -> tuple[list[IngredientCreate], str, str | None]:
    """Parse an extracted block as a mini-recipe.

    After the heading/metadata drops, the block's ingredient RUN is located
    (see _find_ingredient_run). Every line INSIDE the run is an ingredient:
    quantity-led lines split into `amount` / `unit` (iff the next token is a
    plausible unit word, UNIT_WORDS) / `name`; quantity-less lines become
    ingredients with amount=None — that is how "Fine pink Himalayan salt"
    survives. Every line OUTSIDE the run joins the instruction paragraph
    (joined, whitespace-normalized) — even a quantity-led one, so a wrapped
    instruction line like "1 tablespoon at a time, as needed, to thin the"
    is NOT an ingredient, wherever a reader's ordering put it. Ingredients
    go through the shared `normalize_ingredients` with `group` set on every
    entry.
    """
    kept: list[str] = []
    block_yield: str | None = None
    for line in block_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # The yield line is captured, not discarded: the shopping list's
        # store-bought line uses it as the quantity. First one wins; a
        # quantity-led line can't be a yield ("1 3/4 cups broth" is an
        # ingredient even if OCR mangles a MAKES prefix onto it).
        yield_match = _YIELD_RE.match(stripped)
        if (
            yield_match
            and block_yield is None
            and not _QUANTITY_RE.match(_normalize_fractions(stripped))
        ):
            block_yield = yield_match.group(1).strip(" .")
            continue
        if is_heading(stripped):
            continue
        # The block's own (possibly lowercase) heading is metadata, not
        # instructions — "goddess sauce" opening its own block.
        if _same_heading(stripped, group):
            continue
        kept.append(stripped)

    quantity_led = [
        bool(_QUANTITY_RE.match(_normalize_fractions(line))) for line in kept
    ]
    run_start, run_end = _find_ingredient_run(kept, quantity_led)

    ingredients: list[IngredientCreate] = []
    instruction_lines: list[str] = []
    for i, stripped in enumerate(kept):
        if run_start <= i < run_end:
            if quantity_led[i]:
                match = _QUANTITY_RE.match(_normalize_fractions(stripped))
                amount = " ".join(match.group(1).split())  # collapse "1  1/2"
                words = match.group(2).strip().split()
                unit: str | None = None
                if words and words[0].strip(_EDGE_PUNCT).lower() in UNIT_WORDS:
                    unit = words[0].strip(_EDGE_PUNCT)
                    words = words[1:]
                name = " ".join(words).strip(" ,;")
                if name:
                    ingredients.append(
                        IngredientCreate(
                            name=name,
                            amount=amount,
                            unit=unit,
                            order=len(ingredients),
                            group=group,
                        )
                    )
                    continue
                # Quantity with no name left after the unit ("2 cups" alone):
                # nothing to admit — treat as paragraph text, as before.
                instruction_lines.append(stripped)
            else:
                # Quantity-LESS ingredient inside the run: amount stays None.
                ingredients.append(
                    IngredientCreate(
                        name=stripped.strip(" ,;"),
                        amount=None,
                        unit=None,
                        order=len(ingredients),
                        group=group,
                    )
                )
        else:
            instruction_lines.append(stripped)

    instructions = re.sub(r"\s+", " ", " ".join(instruction_lines)).strip()
    return normalize_ingredients(ingredients), instructions, block_yield


def resolve_references(
    schema: PhotoExtractResult, extra_page_texts: list[str]
) -> tuple[PhotoExtractResult, list[int]]:
    """Classify each extra page and merge referenced blocks into `schema`.

    Detection runs against `schema.ingredients` as passed in (the main list),
    for every page. Returns the updated schema (a copy — the input is not
    mutated) and the indices, into `extra_page_texts`, of pages that did NOT
    match (continuations, which the caller folds into the main parse).
    Deterministic; no I/O.
    """
    main_ingredients = list(schema.ingredients)
    merged_ingredients = list(schema.ingredients)
    instructions = schema.instructions
    sub_recipe_yields = dict(getattr(schema, "sub_recipe_yields", {}) or {})
    continuations: list[int] = []

    for page_index, page_text in enumerate(extra_page_texts):
        match = find_reference(main_ingredients, page_text)
        if match is None:
            continuations.append(page_index)
            continue
        block = split_blocks(page_text)[match.block_index]
        block_ingredients, block_instructions, block_yield = parse_block(
            block, group=match.group
        )
        if block_yield:
            sub_recipe_yields[match.group] = block_yield
        merged_ingredients.extend(block_ingredients)
        if block_instructions:
            section = f"--- {match.group} ---\n{block_instructions}"
            instructions = (
                f"{instructions}\n\n{section}" if instructions else section
            )

    updated = schema.model_copy(
        update={
            "ingredients": merged_ingredients,
            "instructions": instructions,
            "sub_recipe_yields": sub_recipe_yields,
        }
    )
    return updated, continuations
