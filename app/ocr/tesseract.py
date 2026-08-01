"""Tesseract OCR backend for cookbook-page photos.

What this file is
-----------------
One of several interchangeable "readers". A reader takes a photo of a recipe
page and returns a `Reading`: the raw text it produced plus a best-effort parse
of that text into `PhotoExtractResult`. Other readers (e.g. the Claude vision
extractor in `app/services/photo_service.py`) implement the same contract, so
their outputs can be compared or voted against each other.

Contract
--------
    read(image_path: Path, **opts) -> Reading

`Reading` (from `app.ocr.base`) is a frozen dataclass with exactly:
    method:   str                 -> always "tesseract" here
    schema:   PhotoExtractResult  -> the parsed result
    raw_text: str | None          -> the OCR text, before parsing

Everything here is local and deterministic: no network, no LLM calls. Same
image + same options in, same output out.

The parse is a **first pass meant for human review**. Every heuristic is a
named module-level constant with a comment stating the rule in words, so a
reviewer can read the rules without reverse-engineering the code. Where a rule
is known to be wrong on real pages, the comment says so.

Preprocessing
-------------
Default is *no preprocessing* — the raw JPEG goes straight to Tesseract. This
is empirical, not laziness: a prior pipeline (Otsu binarize + connected-
component filter + deskew + cubic upsample) scored *worse* than the raw image
on this project's test photo. A cut-down `preprocess=True` path is kept so that
negative result stays reproducible, but it is off by default.
"""

from __future__ import annotations

import argparse
import re
import warnings
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np
import pytesseract
from PIL import Image

from app.ocr.base import Reading
from app.ocr.xycut import SKEW_GATE_DEG, xycut_read
from app.ocr.normalize import normalize_ingredients
from app.ocr.resolve import resolve_references
from app.schemas.recipe import IngredientCreate, PhotoExtractResult

# --------------------------------------------------------------------------
# OCR options
# --------------------------------------------------------------------------

METHOD_NAME = "tesseract"

# Page segmentation mode. Tesseract's `--psm`. The ones worth trying on a
# cookbook page, and why:
#
#   3  Fully automatic page segmentation, no orientation detection. Tesseract's
#      own default. It finds the columns and reads them in a sensible order,
#      and it keeps "3." attached to the start of a numbered step. This is our
#      default. Weakness: on this project's test page the ingredient list is
#      set in two narrow sub-columns, and PSM 3 zips them together onto shared
#      lines ("hole cloves    2 tablespoons balsamic vinegar").
#   4  Assume a single column of variable-sized text. Worth trying on a page
#      that really is one column; on a multi-column page it interleaves badly.
#   6  Assume a single uniform block of text. Good for a cropped ingredient
#      panel, bad for a whole page.
#  11  Sparse text, no assumed layout. On the test page this recovers the
#      ingredient lines *individually* (a real win over PSM 3), but it strips
#      the numbered-step structure ("2." lands on its own line, away from its
#      text) and emits fragments in a jumbled order.
#  12  Sparse text + orientation detection. Near-identical to 11 here.
#
# Measured on the test photo (`python -m app.ocr.tesseract --psm N`):
#   psm 3  -> 11 ingredients, 6 of 7 steps recovered
#   psm 11 -> 17 ingredients (more of the right-hand column survives) but
#             0 steps, and because no "N." marker survives, the
#             ingredient-region rule below has nothing to stop at, so three
#             lines of step prose leak into the ingredient list.
#
# Rule of thumb: PSM 3 for the whole page, PSM 11 if you specifically want the
# two-column ingredient block un-interleaved and are willing to lose the steps.
DEFAULT_PSM = 3

# OCR engine mode. 3 = "default", i.e. let Tesseract pick LSTM or legacy.
DEFAULT_OEM = 3

# Only `eng` traineddata is installed in this environment.
DEFAULT_LANG = "eng"


# --------------------------------------------------------------------------
# Text-normalisation constants
# --------------------------------------------------------------------------

# Unicode vulgar fractions are printed on real cookbook pages ("1½ cups orzo").
# We rewrite them to ASCII "1/2" form so the quantity regex only has to handle
# one representation. NOTE: Tesseract usually *mangles* these glyphs rather
# than emitting them (on the test photo "½" comes out as "%", "Y", "VY", "‘A").
# This table therefore mostly protects us from crashing / mis-splitting when a
# glyph *does* survive; it does not fix Tesseract's fraction errors.
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
    "⅛": "1/8",
    "⅜": "3/8",
    "⅝": "5/8",
    "⅞": "7/8",
}

# A fraction glyph glued to a whole number ("1½") must become "1 1/2", not
# "11/2" — so insert a space when a glyph directly follows a digit.
_FRACTION_AFTER_DIGIT_RE = re.compile(r"(?<=\d)([" + "".join(UNICODE_FRACTIONS) + r"])")
_FRACTION_RE = re.compile(r"[" + "".join(UNICODE_FRACTIONS) + r"]")

# Curly quotes / dashes -> ASCII, so downstream string matching is predictable.
PUNCTUATION_FIXES: dict[str, str] = {
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "—": "-",
    "–": "-",
}


# --------------------------------------------------------------------------
# Parsing rules — each constant is one rule, stated in its comment
# --------------------------------------------------------------------------

# RULE (step): a line begins a numbered instruction step if it starts with 1-2
# digits followed by "." or ")" and then at least one more character.
# The trailing "." is what separates a step ("3. Place the chicken...") from an
# ingredient ("3 garlic cloves...").
STEP_LINE_RE = re.compile(r"^\s*(\d{1,2})\s*[.)]\s+(\S.*)$")

# RULE (quantity): a leading quantity is a mixed fraction ("1 1/2"), a bare
# fraction ("1/2"), a decimal ("1.5") or an integer ("3"), at the very start of
# the line. Captured verbatim — `IngredientCreate.amount` is a string on
# purpose (see CLAUDE.md: compound quantities can't be a float).
QUANTITY_RE = re.compile(r"^(\d+\s+\d+/\d+|\d+/\d+|\d+\.\d+|\d+)\b")

# RULE (ingredient): inside the ingredient region a line is an ingredient if it
# starts with a quantity (QUANTITY_RE), is NOT a numbered step (STEP_LINE_RE),
# contains at least one letter, and is at most INGREDIENT_MAX_WORDS words long.
# The word cap is a false-positive guard: wrapped step prose sometimes starts
# with a number ("3 hours. Remove from the oven. Increase..."), but such lines
# are long.
INGREDIENT_MAX_WORDS = 14

# RULE (ingredient region): only lines *before* the first numbered step are
# considered as ingredients. Everything from step 1 onwards is instructions.
# This is the main defence against step prose leaking into the ingredient list.

# RULE (ingredient, reject all-caps): reject a candidate whose name contains no
# lowercase letter. Recipe headers are set in small caps ("TOTAL 3 HOURS 15
# MINUTES"), start with a number, and are short — they otherwise sail through.
# Real ingredient names always contain lowercase.

# RULE (ingredient, reject function words): reject a candidate whose first name
# word is one of these. Body prose that happens to wrap onto a line starting
# with a number ("4 of course, delicious...") is caught here; no real
# ingredient name starts with a function word.
INGREDIENT_STOPWORD_STARTS: frozenset[str] = frozenset(
    {
        "of", "the", "and", "a", "an", "to", "in", "for", "or", "but",
        "that", "is", "it", "with", "from", "at", "as", "by", "on", "was",
    }
)

# RULE (dangling joiner): a line ending in one of these words is mid-phrase;
# the next line continues it ("chopped fresh dill, plus" / "cut into").
JOINER_END_WORDS: frozenset[str] = frozenset(
    {"plus", "and", "or", "with", "into", "cut", "for", "of", "the", "a", "an"}
)

# RULE (unit, leading): the word immediately after the quantity is the unit if
# it is in this set. Otherwise there is no unit and the whole remainder is the
# ingredient name ("2 lemons" -> amount "2", unit None, name "lemons").
UNIT_WORDS: frozenset[str] = frozenset(
    {
        "teaspoon", "teaspoons", "tsp", "tsps",
        "tablespoon", "tablespoons", "tbsp", "tbsps",
        "cup", "cups",
        "ounce", "ounces", "oz",
        "pound", "pounds", "lb", "lbs",
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
        "can", "cans", "jar", "jars", "package", "packages", "packet", "packets",
        "pinch", "pinches", "dash", "dashes", "handful", "handfuls",
        "sheet", "sheets", "piece", "pieces",
    }
)

# RULE (unit, trailing): cookbooks also write the unit *after* the food
# ("3 garlic cloves", "1 rosemary sprig"). If the first word after the quantity
# is not a unit but the second word is one of these, take the second word as
# the unit and drop it from the name. Deliberately a small set — broadening it
# would misfire on things like "2 cups baby spinach".
TRAILING_UNIT_WORDS: frozenset[str] = frozenset(
    {"clove", "cloves", "sprig", "sprigs", "slice", "slices", "stick", "sticks"}
)

# RULE (servings): the first line matching this wins; the captured number
# becomes `servings`. Printed as "SERVES 4 TO 6" on the test page. Small-caps
# headers OCR badly, so this often finds nothing and we keep the schema
# default.
SERVINGS_RE = re.compile(
    r"\b(?:serves|makes|yield[s]?)\b[^\d]{0,10}(\d{1,2})"
    r"(?:\s*(?:to|-|–|—)\s*(\d{1,2}))?",
    re.IGNORECASE,
)

# RULE (title): the title is the first run of up to TITLE_MAX_LINES consecutive
# lines, from the top of the document, that look like title text: letters,
# spaces, hyphens and apostrophes only (no digits, no commas). They are joined
# with a space. Cookbook titles are frequently set across two lines
# ("slow-roasted" / "sunday chicken"), hence the run rather than one line.
TITLE_LINE_RE = re.compile(r"^[A-Za-z][A-Za-z'\- ]*$")
TITLE_MAX_LINES = 2
TITLE_MIN_CHARS = 4  # skip stray one- or two-letter OCR noise at the top
TITLE_FALLBACK = "Untitled recipe"

# RULE (note sidebar): a line that is exactly the word NOTE (any case, optional
# trailing punctuation or rule-line characters) starts a sidebar. Every
# following line up to the next blank-line gap is the note.
NOTE_HEADER_RE = re.compile(r"^\s*note\b[\s\W]*$", re.IGNORECASE)


# --------------------------------------------------------------------------
# Preprocessing (opt-in only — see module docstring)
# --------------------------------------------------------------------------


def _preprocess(image_path: Path) -> Image.Image:
    """Grayscale + Otsu binarisation. OFF BY DEFAULT.

    Kept only so the earlier negative result stays reproducible: on the test
    photo, binarising made Tesseract worse than the raw image. The old pipeline
    in `scripts/ocr_test.py` also did connected-component filtering, deskewing
    and 5x cubic upsampling; those are intentionally not carried over, since
    the whole chain lost to the raw baseline.
    """
    gray = np.array(Image.open(image_path).convert("L"))
    # THRESH_BINARY_INV + OTSU gives white text on black; invert back so
    # Tesseract sees its expected dark-text-on-light.
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return Image.fromarray(255 - binary)


# --------------------------------------------------------------------------
# Text normalisation
# --------------------------------------------------------------------------


def normalize_text(text: str) -> str:
    """Rewrite unicode fractions and curly punctuation to ASCII equivalents."""
    text = _FRACTION_AFTER_DIGIT_RE.sub(lambda m: " " + m.group(1), text)
    text = _FRACTION_RE.sub(lambda m: UNICODE_FRACTIONS[m.group(0)], text)
    for bad, good in PUNCTUATION_FIXES.items():
        text = text.replace(bad, good)
    return text


def _clean_word(word: str) -> str:
    """Lowercase a token and strip surrounding punctuation, for unit lookup."""
    return word.strip(".,;:()[]'\"").lower()


# --------------------------------------------------------------------------
# Field extractors
# --------------------------------------------------------------------------


def extract_title(lines: list[str]) -> str:
    """First run of up to TITLE_MAX_LINES title-looking lines (see TITLE_LINE_RE)."""
    collected: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            # A blank line ends the run only once we have something.
            if collected:
                break
            continue
        if TITLE_LINE_RE.match(stripped) and len(stripped) >= TITLE_MIN_CHARS:
            collected.append(stripped)
            if len(collected) == TITLE_MAX_LINES:
                break
        elif collected:
            break
    return " ".join(collected) if collected else TITLE_FALLBACK


def extract_servings(lines: list[str]) -> int | None:
    """First SERVES/MAKES/YIELDS line wins. None means 'not found'."""
    for line in lines:
        match = SERVINGS_RE.search(line)
        if match:
            return int(match.group(1))
    return None


def parse_ingredient_line(line: str, order: int) -> IngredientCreate | None:
    """Split one ingredient line into amount / unit / name.

    Returns None if the line does not satisfy the ingredient rule.
    """
    stripped = line.strip()
    if STEP_LINE_RE.match(stripped):
        return None

    quantity_match = QUANTITY_RE.match(stripped)
    if not quantity_match:
        return None

    remainder = stripped[quantity_match.end() :].strip()
    if not re.search(r"[A-Za-z]", remainder):
        return None
    words = remainder.split()
    if not words or len(words) + 1 > INGREDIENT_MAX_WORDS:
        return None

    amount = " ".join(quantity_match.group(1).split())  # collapse "1  1/2"
    unit: str | None = None

    if _clean_word(words[0]) in UNIT_WORDS:
        # Leading unit: "1 tablespoon fresh thyme leaves"
        unit = _clean_word(words[0])
        name_words = words[1:]
    elif len(words) >= 2 and _clean_word(words[1]) in TRAILING_UNIT_WORDS:
        # Trailing unit: "3 garlic cloves, finely chopped" -> unit "cloves"
        unit = _clean_word(words[1])
        # Keep any punctuation that was attached to the unit word ("cloves,").
        tail = words[1][len(words[1].rstrip(".,;:")) :]
        name_words = [words[0] + tail] + words[2:]
    else:
        name_words = words

    name = " ".join(name_words).strip(" ,;")
    if not name:
        return None
    if not any(character.islower() for character in name):
        return None  # all-caps header, not an ingredient
    if _clean_word(name_words[0]) in INGREDIENT_STOPWORD_STARTS:
        return None  # wrapped body prose, not an ingredient

    return IngredientCreate(name=name, amount=amount, unit=unit, order=order)


def _quantityless_ingredient(line: str, order: int) -> IngredientCreate | None:
    """A quantity-less line admitted by the run rule, or None if it fails the
    same quality guards a quantity-led name must pass.

    RULE (quantity-less ingredient): inside the ingredient run, a line with no
    leading quantity is still an ingredient ("Fine pink Himalayan salt and
    freshly ground pepper") iff it is short enough, contains lowercase
    letters (rejects small-caps headers), does not start with a function
    word, and is not cook-info metadata.
    """
    stripped = line.strip().strip(" ,;")
    words = stripped.split()
    if not words or len(words) > INGREDIENT_MAX_WORDS:
        return None
    if not any(ch.islower() for ch in stripped):
        return None  # small-caps headers ("TOTAL 3 HOURS 15 MINUTES")
    if SERVINGS_RE.search(stripped) or NOTE_HEADER_RE.match(stripped):
        return None  # cook-info metadata ("Serves 4") or the NOTE sidebar head
    if _clean_word(words[0]) in INGREDIENT_STOPWORD_STARTS:
        return None
    return IngredientCreate(name=stripped, amount=None, unit=None, order=order)


def extract_ingredients(lines: list[str]) -> list[IngredientCreate]:
    """Apply the ingredient-run rule to the region before the first step.

    RULE (ingredient run — shared with the sub-recipe block parser, see
    app/ocr/resolve.py `_find_ingredient_run`): ingredients form one
    contiguous run in which at least half the lines are quantity-led. A
    quantity-led line (parse_ingredient_line succeeds) always extends the
    run; a quantity-less line extends it only when it directly follows a
    quantity-led line and does not end like prose. Lines inside the run
    that lack a quantity become ingredients with amount=None — previously
    they were structurally invisible (salt, pepper, "basil and/or parsley"
    could never be parsed), a measured recall ceiling on the reference page.
    """
    # `_find_ingredient_run` is private to resolve.py; imported anyway so the
    # run rule has exactly one implementation (same reasoning as the private
    # photo_service imports in claude.py).
    from app.ocr.resolve import _find_ingredient_run

    # The run rule is BLOCK-scoped: contiguity only means something within
    # one visual block (the xycut path joins blocks with blank lines; the
    # two ingredient sub-columns are separate blocks with OCR junk at the
    # seam, and a single region-wide run would end at the first column and
    # drop the second — measured 13/19 -> 8/19 before this was per-block).
    region_blocks: list[list[str]] = [[]]
    for line in lines:
        if STEP_LINE_RE.match(line.strip()):
            break  # ingredient region ends at the first numbered step
        if line.strip():
            region_blocks[-1].append(line)
        elif region_blocks[-1]:
            region_blocks.append([])  # blank line = block boundary

    ingredients: list[IngredientCreate] = []
    for block in region_blocks:
        if not block:
            continue
        quantity_led = [
            parse_ingredient_line(line, order=0) is not None for line in block
        ]
        # RULE (quantity-less block): a block with NO quantity-led line at
        # all can still be an ingredient — xycut crops quantity-less entries
        # ("Fine pink Himalayan salt and freshly / ground pepper") into
        # their own micro-blocks. Admitted iff the PREVIOUS block yielded
        # at least one ingredient (headnote prose blocks precede the first
        # ingredient block and are thereby rejected). The block's wrapped
        # lines are joined into ONE candidate entry.
        if not any(quantity_led):
            if ingredients:
                joined = " ".join(l.strip() for l in block)
                parsed = _quantityless_ingredient(joined, order=len(ingredients))
                if parsed is not None:
                    ingredients.append(parsed)
            continue
        start, end = _find_ingredient_run(
            [l.strip() for l in block], quantity_led
        )
        block_start_count = len(ingredients)
        for i in range(start, end):
            stripped = block[i].strip()
            if quantity_led[i]:
                parsed = parse_ingredient_line(block[i], order=len(ingredients))
                if parsed is not None:
                    ingredients.append(parsed)
                continue
            # RULE (wrapped continuation): inside the run, a quantity-less
            # line is a CONTINUATION of the entry above it — joined onto its
            # name, not a new entry — when it starts with a lowercase letter
            # or punctuation, or the previous line dangles a joiner word
            # ("...dill, plus" / "...cut into"). This is the text-only
            # analogue of vision's indent-based wrap joining, and it is what
            # lets the normalizer's sum rule see "plus 3 whole cloves".
            prev_dangles = (
                i > start
                and _clean_word(block[i - 1].strip().split()[-1]) in JOINER_END_WORDS
            )
            is_continuation = (
                stripped[:1].islower() or not stripped[:1].isalpha() or prev_dangles
            )
            if is_continuation and len(ingredients) > block_start_count:
                prev = ingredients[-1]
                ingredients[-1] = prev.model_copy(
                    update={"name": f"{prev.name} {stripped.strip(' ,;')}"}
                )
            else:
                parsed = _quantityless_ingredient(block[i], order=len(ingredients))
                if parsed is not None:
                    ingredients.append(parsed)
    return ingredients


def extract_steps(lines: list[str]) -> list[tuple[int, str]]:
    """Collect numbered steps as (printed number, text) pairs.

    RULE (step body): a line matching STEP_LINE_RE opens a step; every later
    line is appended to it until the next step opens, a NOTE sidebar starts, or
    the text ends. Blank lines are *skipped, not treated as terminators* —
    Tesseract sprinkles blank lines through the middle of a paragraph, so
    stopping at one truncates most steps mid-sentence.

    The printed number is kept rather than re-numbering 1..N. If OCR drops a
    step marker (it dropped "6." on the test photo) the gap stays visible in
    the output instead of being papered over by renumbering.
    """
    steps: list[tuple[int, str]] = []
    number: int | None = None
    body: list[str] = []

    def flush() -> None:
        if number is not None and body:
            steps.append((number, " ".join(body)))

    for line in lines:
        stripped = line.strip()
        match = STEP_LINE_RE.match(stripped)
        if match:
            flush()
            number = int(match.group(1))
            body = [match.group(2).strip()]
            continue
        if number is None:
            continue
        if NOTE_HEADER_RE.match(stripped):
            flush()
            number = None
            body = []
            continue
        if stripped:
            body.append(stripped)

    flush()
    return steps


def extract_note(lines: list[str]) -> str | None:
    """Text of the NOTE sidebar, if the page has one."""
    for index, line in enumerate(lines):
        if NOTE_HEADER_RE.match(line.strip()):
            body: list[str] = []
            for follow in lines[index + 1 :]:
                if not follow.strip():
                    break
                body.append(follow.strip())
            if body:
                return " ".join(body)
    return None


# --------------------------------------------------------------------------
# Text -> schema
# --------------------------------------------------------------------------


def parse_text(raw_text: str, photo_filename: str) -> PhotoExtractResult:
    """Turn OCR text into a `PhotoExtractResult`.

    First pass only — a human reviews this before anything is saved. Fields the
    rules cannot find keep their schema defaults (`meal_type="dinner"`,
    `servings=2`).
    """
    text = normalize_text(raw_text)
    lines = text.splitlines()

    title = extract_title(lines)
    ingredients = extract_ingredients(lines)
    steps = extract_steps(lines)
    servings = extract_servings(lines)
    note = extract_note(lines)

    # No silent failures: an empty ingredient list almost always means the
    # layout defeated the rules, and the caller must be able to see that.
    if not ingredients:
        warnings.warn(
            f"{METHOD_NAME}: parsed 0 ingredients from {photo_filename!r} "
            f"({len(lines)} OCR lines). The ingredient rules likely did not "
            f"match this layout — try a different psm.",
            stacklevel=2,
        )
    if not steps:
        warnings.warn(
            f"{METHOD_NAME}: parsed 0 instruction steps from {photo_filename!r}.",
            stacklevel=2,
        )
    if title == TITLE_FALLBACK:
        warnings.warn(
            f"{METHOD_NAME}: no title line found in {photo_filename!r}; "
            f"using {TITLE_FALLBACK!r}.",
            stacklevel=2,
        )

    instructions = (
        "\n\n".join(f"{number}. {text}" for number, text in steps) if steps else None
    )

    # Parsing lives in each reader; each reader opts into the shared
    # normalization (app/ocr/normalize.py) at its own boundary.
    ingredients = normalize_ingredients(ingredients)

    return PhotoExtractResult(
        title=title,
        servings=servings if servings is not None else 2,
        instructions=instructions,
        notes=note,
        ingredients=ingredients,
        photo_filename=photo_filename,
    )


# --------------------------------------------------------------------------
# The reader
# --------------------------------------------------------------------------


def read(
    image_path: Path | Sequence[Path],
    *,
    psm: int = DEFAULT_PSM,
    lang: str = DEFAULT_LANG,
    oem: int = DEFAULT_OEM,
    preprocess: bool = False,
    extra_config: str = "",
    segmentation: str = "xycut",
    skew_gate_deg: float = SKEW_GATE_DEG,
) -> Reading:
    """OCR `image_path` with Tesseract and parse the result.

    This is the `read(image_path, **opts) -> Reading` contract; the options are
    spelled out as keyword arguments so their defaults are visible.

    Args:
        image_path: Path to the photo, or an ordered list of paths for a
            recipe spanning several pages — list order is page order, first
            page carries the title. Pages are OCR'd separately and their text
            concatenated in page order before the single parse, so a step
            that continues across a page break is parsed whole.
        psm: Tesseract page segmentation mode. See DEFAULT_PSM for which values
            are worth trying on a two-column cookbook page.
        lang: Tesseract language pack. Only "eng" is installed here.
        oem: OCR engine mode; 3 = Tesseract's default.
        preprocess: Opt-in Otsu binarisation. Off by default because
            preprocessing measurably hurt accuracy on the test photo. Only
            applies to segmentation="none"; the xycut path manages its own
            image handling.
        extra_config: Appended verbatim to the Tesseract config string
            (segmentation="none" path only).
        segmentation: "xycut" (default) segments the page with the recursive
            X-Y cut in app/ocr/xycut.py and OCRs each block separately —
            measurably better on multi-column layouts, falls through to
            full-page psm 3 when the page has no cuttable structure.
            "none" is the plain full-page path at `psm`.
        skew_gate_deg: (xycut path only) rotate a page level before
            segmenting iff its measured tilt exceeds this many degrees;
            below the gate the image is untouched (deskew measurably hurt
            on the flat reference photo). See xycut.SKEW_GATE_DEG for the
            measurement-based justification of the default.

    Raises:
        FileNotFoundError: if `image_path` does not exist.
        pytesseract.TesseractError / TesseractNotFoundError: propagated, so an
            OCR failure is never mistaken for an empty page.
    """
    paths = (
        [Path(p) for p in image_path]
        if isinstance(image_path, (list, tuple))
        else [Path(image_path)]
    )
    for p in paths:
        if not p.is_file():
            raise FileNotFoundError(f"No such image: {p}")
    if segmentation not in ("xycut", "none"):
        raise ValueError(f"segmentation must be 'xycut' or 'none', got {segmentation!r}")

    # Two texts per page: page_texts is the legacy raw form (raw_text field,
    # parse input — unchanged); resolver_texts is what resolve_references
    # sees. Contract with the resolver: BLANK LINES separate visual blocks,
    # single newlines join lines within a block, a block's columns come
    # left-column-first. The xycut path derives that from the cut tree's
    # top-level horizontal cuts (assemble_block_text); the plain path's
    # psm-3 paragraph breaks approximate it.
    page_texts: list[str] = []
    resolver_texts: list[str] = []
    if segmentation == "xycut":
        for p in paths:
            page = xycut_read(
                Image.open(p), lang=lang, skew_gate_deg=skew_gate_deg
            )
            page_texts.append(page.raw_text)
            resolver_texts.append(page.block_text)
    else:
        config = f"--oem {oem} --psm {psm}"
        if extra_config:
            config = f"{config} {extra_config}"
        for p in paths:
            image = _preprocess(p) if preprocess else Image.open(p)
            page_texts.append(
                pytesseract.image_to_string(image, lang=lang, config=config)
            )
        resolver_texts = page_texts
    raw_text = "\n\n".join(page_texts)

    if len(page_texts) == 1:
        schema = parse_text(raw_text, photo_filename=paths[0].name)
        return Reading(method=METHOD_NAME, schema=schema, raw_text=raw_text)

    # Multi-page: pages after the first are either CONTINUATIONS (they join
    # the main parse via the text concatenation above) or REFERENCED pages —
    # a sub-recipe that appears as an ingredient in the main recipe, detected
    # by a heading on that page matching a main-list ingredient
    # (app/ocr/resolve.py). Detection runs against a provisional parse of
    # page 1 alone; its warnings are suppressed because the provisional parse
    # is detection machinery, not a result anyone reviews (page-1-only text
    # legitimately lacks steps that continue onto page 2).
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        provisional = parse_text(page_texts[0], photo_filename=paths[0].name)
    _, continuation_rel = resolve_references(provisional, resolver_texts[1:])
    continuation_pages = {i + 1 for i in continuation_rel}

    # Re-parse the main recipe from page 1 + continuation pages (the existing
    # join), THEN resolve the referenced pages against that re-parse.
    # raw_text above deliberately stays the full join of ALL pages in order,
    # referenced pages included, for scoring and debugging.
    main_text = "\n\n".join(
        text
        for i, text in enumerate(page_texts)
        if i == 0 or i in continuation_pages
    )
    schema = parse_text(main_text, photo_filename=paths[0].name)
    referenced_texts = [
        text
        for i, text in enumerate(resolver_texts)
        if i != 0 and i not in continuation_pages
    ]
    if referenced_texts:
        schema, _ = resolve_references(schema, referenced_texts)
    return Reading(method=METHOD_NAME, schema=schema, raw_text=raw_text)


# --------------------------------------------------------------------------
# Manual eyeball harness
# --------------------------------------------------------------------------

DEFAULT_TEST_PHOTO = (
    Path(__file__).resolve().parents[2] / "data" / "photos" / "20260404_204704_04f01b.jpg"
)


def _main() -> None:
    parser = argparse.ArgumentParser(description="Run the Tesseract reader on one photo.")
    parser.add_argument("image", nargs="?", type=Path, default=DEFAULT_TEST_PHOTO)
    parser.add_argument("--psm", type=int, default=DEFAULT_PSM)
    parser.add_argument("--lang", default=DEFAULT_LANG)
    parser.add_argument("--preprocess", action="store_true")
    args = parser.parse_args()

    reading = read(
        args.image, psm=args.psm, lang=args.lang, preprocess=args.preprocess
    )

    print("=" * 70)
    print(f"RAW TEXT  ({reading.method}, psm={args.psm}, preprocess={args.preprocess})")
    print("=" * 70)
    print(reading.raw_text)

    result = reading.schema
    print("=" * 70)
    print("PARSED SCHEMA")
    print("=" * 70)
    print(f"title:      {result.title!r}")
    print(f"servings:   {result.servings}")
    print(f"meal_type:  {result.meal_type}")
    print(f"notes:      {result.notes!r}")
    print(f"ingredients ({len(result.ingredients)}):")
    for ingredient in result.ingredients:
        print(
            f"  [{ingredient.order:2d}] amount={ingredient.amount!r} "
            f"unit={ingredient.unit!r} name={ingredient.name!r}"
        )
    steps = (result.instructions or "").split("\n\n")
    print(f"instructions ({len(steps) if result.instructions else 0} steps):")
    for step in steps:
        if step:
            print(f"  {step}")


if __name__ == "__main__":
    _main()
