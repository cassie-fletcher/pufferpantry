"""Apple Vision OCR backend.

CONTRACT
--------
    from app.ocr.vision import read
    reading = read(Path("data/photos/foo.jpg"), recognitionLevel="accurate")

`read(image_path, **opts) -> Reading`, where `Reading` is the frozen dataclass
from `app.ocr.base` with exactly three fields:

    method    "apple_vision"
    schema    PhotoExtractResult  (app.schemas.recipe)
    raw_text  the recognized text, before parsing

HOW IT WORKS
------------
There is no pyobjc/ocrmac in this environment and we are not adding any. All
access to Vision goes through a small Swift binary (`swift/VisionOCR.swift`,
built by `swift/build.sh` into `swift/bin/vision_ocr`, which is a build
artifact and is not committed). This module shells out to it, parses one JSON
object off stdout, and does everything else here — in Python, where it can be
read and debugged.

Nothing in this path touches the network. VNRecognizeTextRequest is on-device.

COORDINATES — the single most likely bug in this file
-----------------------------------------------------
Vision reports boxes normalized to [0, 1] with the origin at the BOTTOM-LEFT
and y increasing UPWARD. So `bbox.y` is the box's BOTTOM edge, and
`bbox.y + bbox.height` is its TOP edge. Canonical image coordinates put the
origin at the TOP-LEFT with y increasing DOWNWARD, which means the two edges
swap roles:

    x0 = x * img_w
    x1 = (x + w) * img_w
    y0 = (1.0 - (y + h)) * img_h   # Vision TOP edge    -> canonical y0 (top)
    y1 = (1.0 - y) * img_h         # Vision BOTTOM edge -> canonical y1 (bottom)

`_to_canonical` does this once and checks `y0 <= y1`. Sanity check on the
Sunday Chicken page: the title must come out near y0 ~= 120 and the page
number near y1 ~= 1454. If the title lands at the bottom, the flip is
inverted.

MEASUREMENTS (macOS 14.6, Apple Silicon, revision 3, Sunday Chicken page,
1176x1568)
-------------------------------------------------------------------------
minimumTextHeight is a fraction of image height, not pixels. The often-quoted
1/32 default turns out NOT to be what this OS reports: `vision_ocr --version`
reports framework_default_minimum_text_height = 0.0. Observation counts:

    0.0      -> 85 observations, 3225 chars
    0.004    -> 85, 3225
    0.008    -> 85, 3225      <- our default
    0.016    -> 85, 3225
    0.03125  -> 85, 3204      (one line misread: "3 HOURS IS MINUTES")
    0.05     -> 69, 2585      <- 16 lines lost
    0.1      -> 69, 2585

So on this image the framework default would NOT have been catastrophic, but
the cliff is real and close (0.05 loses ~19% of lines). 0.008 is measurably
lossless here. Re-measure before trusting it on a different image.

READING ORDER
-------------
Vision returns observations in its own grouped order, and on this page that
order is correct: headnote column, then ingredient column 1, then ingredient
column 2, then the full-width numbered steps, then the sidebar. A naive
"split the page at the midline" column sort would scramble it, because the
page is not two uniform columns. So the default is to trust Vision's order.
`reading_order="topdown"` is available as an escape hatch.

PARSING
-------
The text -> PhotoExtractResult step is a FIRST PASS meant for human review,
not an authority. Every heuristic is a named module-level constant below and
each rule is written out in a comment where it is applied. See `parse_lines`.

CONFIDENCE
----------
Vision gives one confidence per LINE. There is no per-word confidence. The
line-level value is kept on `TextLine.confidence` and is deliberately NOT
propagated into PhotoExtractResult, which has no field for it — inventing a
per-ingredient confidence from a per-line number would be fabrication.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from app.ocr.base import Reading
from app.ocr.normalize import normalize_ingredients
from app.ocr.resolve import resolve_references
from app.schemas.recipe import IngredientCreate, PhotoExtractResult

logger = logging.getLogger(__name__)

METHOD_NAME = "apple_vision"

# --- The Swift binary -------------------------------------------------------

SWIFT_DIR = Path(__file__).resolve().parent / "swift"
BINARY_PATH = SWIFT_DIR / "bin" / "vision_ocr"
BUILD_SCRIPT = SWIFT_DIR / "build.sh"

#: Must match the "coordinate_space" field the Swift shim emits. If this ever
#: stops matching, the shim changed its convention and the flip below is wrong.
EXPECTED_COORDINATE_SPACE = "vision_normalized_bottom_left_y_up"

#: Options forwarded verbatim to the Swift shim (as the stdin JSON object).
#: Anything not in this set is rejected loudly rather than silently ignored.
VISION_OPTION_KEYS = frozenset(
    {
        "recognitionLevel",
        "usesLanguageCorrection",
        "recognitionLanguages",
        "minimumTextHeight",
        "maxCandidates",
        "revision",
    }
)

#: Options handled on this side, not forwarded.
PYTHON_OPTION_KEYS = frozenset({"reading_order", "timeout"})

#: "vision"  = keep the order Vision returned (default; measured best here)
#: "topdown" = sort by top edge, then left edge
DEFAULT_READING_ORDER = "vision"
READING_ORDERS = ("vision", "topdown")

DEFAULT_TIMEOUT_SECONDS = 120.0


# --- Parsing heuristics -----------------------------------------------------
# Every magic number lives here, named, so it can be tuned and argued with.

#: Bullet glyphs stripped from the front of a line before anything else.
#: Deliberately excludes "-", which is a legitimate leading character.
LEADING_BULLET_RE = re.compile(r"^[•●▪·\*]+\s*")

#: Unicode vulgar fractions -> ASCII, so downstream code never sees them.
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

#: A line "starts with a quantity" if it matches this at position 0.
#: Covers: "1 1/2", "1 1/2", "1/2", "1/2", "4", "4-6", "4 to 6", "1.5".
QUANTITY_START_RE = re.compile(
    rf"""^\s*
    (?:
        \d+\s*[{_FRACTION_CHARS}]                 # 1 1/2   (mixed, glyph form)
      | \d+\s+\d+\s*/\s*\d+                       # 1 1/2   (mixed, ascii)
      | \d+\s*/\s*\d+                             # 1/2
      | [{_FRACTION_CHARS}]                       # 1/2
      | \d+(?:\.\d+)?(?:\s*(?:-|–|to)\s*\d+(?:\.\d+)?)?   # 4, 4.5, 4-6
    )
    (?=\s|$|[-–(])                           # must end at a boundary
    """,
    re.VERBOSE,
)

#: A numbered recipe step: "1. Preheat", "2) In a bowl", or a bare "3."
STEP_START_RE = re.compile(r"^\s*(\d{1,2})\s*[.)]\s*(.*)$")

#: Header/footer metadata lines above the ingredients.
COOK_INFO_RE = re.compile(r"^\s*(PREP|COOK|TOTAL|SERVES|MAKES|YIELD)\b", re.IGNORECASE)
SERVES_RE = re.compile(r"\bSERVES\b[^\d]*(\d+)", re.IGNORECASE)

#: The sidebar heading that terminates the numbered steps.
NOTE_HEADING_RE = re.compile(r"^\s*NOTES?\s*:?\s*$", re.IGNORECASE)

#: Running heads and page numbers to drop (a line that is only digits).
PAGE_FURNITURE_RE = re.compile(r"^\s*\d{1,4}\s*$")

#: Ingredient-block detection. The block starts at the first line L such that
#: at least INGREDIENT_RUN_MIN of the INGREDIENT_RUN_WINDOW lines starting at L
#: begin with a quantity. The window guards against a single numeric line in
#: the headnote being mistaken for the start of the ingredient list.
INGREDIENT_RUN_WINDOW = 6
INGREDIENT_RUN_MIN = 3

#: Wrapped ingredient lines are typographically INDENTED relative to the line
#: that started the ingredient. Measured on the Sunday Chicken page: wrapped
#: lines are indented 15.9-21.5 px (image width 1176), while lines that start a
#: new ingredient differ from the previous start by at most 2.5 px. A jump to a
#: different column is ~316 px, hence the upper bound.
#: Both bounds are fractions of image width, so they survive rescaling.
CONTINUATION_INDENT_MIN_FRACTION = 0.006  # ~7 px at 1176 wide
CONTINUATION_INDENT_MAX_FRACTION = 0.080  # ~94 px at 1176 wide

#: Title detection: search the top of the FIRST page for the physically
#: largest line, then keep the run of lines at least this tall relative to
#: it. When a PREP/COOK/TOTAL/SERVES metadata line is present, only lines
#: BEFORE it are title candidates — see _find_title for why raw height alone
#: is not trusted.
TITLE_SEARCH_TOP_FRACTION = 0.35
TITLE_HEIGHT_RATIO = 0.70

#: The sidebar body is the run of lines under the NOTE heading that stay in
#: the same column, i.e. whose left edge is within this fraction of image
#: width of the heading's left edge.
NOTE_COLUMN_TOLERANCE_FRACTION = 0.05

#: Words treated as measurement units when splitting "2 cups baby spinach".
#: Matching is case-insensitive and tries the plural-stripped form too.
UNIT_WORDS = frozenset(
    {
        "cup",
        "tablespoon",
        "tbsp",
        "teaspoon",
        "tsp",
        "pound",
        "lb",
        "ounce",
        "oz",
        "gram",
        "g",
        "kilogram",
        "kg",
        "milliliter",
        "ml",
        "liter",
        "l",
        "quart",
        "qt",
        "pint",
        "pt",
        "gallon",
        "clove",
        "sprig",
        "stalk",
        "stick",
        "bunch",
        "head",
        "slice",
        "can",
        "jar",
        "package",
        "pkg",
        "pinch",
        "dash",
        "handful",
        "piece",
        "sheet",
        "strip",
        "wedge",
    }
)

#: PhotoExtractResult requires a title. Used when none can be found.
FALLBACK_TITLE = "Untitled recipe"

#: PhotoExtractResult.meal_type defaults to "dinner" in the schema. We do not
#: try to infer it from a photo; a human sets it during review.


# --- Data carried between the two halves of this module ---------------------


@dataclass(frozen=True)
class TextLine:
    """One Vision text observation, in canonical top-left-origin pixels.

    `confidence` is Vision's LINE-level confidence. Vision provides no
    per-word confidence; do not treat this as one.

    `candidates` are the alternative readings, best first, each a
    (string, confidence) pair. `text` is candidates[0]'s string with leading
    bullets stripped and unicode fractions normalized.
    """

    text: str
    confidence: float
    x0: float
    y0: float
    x1: float
    y1: float
    candidates: tuple[tuple[str, float], ...]

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def width(self) -> float:
        return self.x1 - self.x0


@dataclass(frozen=True)
class VisionOutput:
    """Everything the Swift shim reported, after coordinate conversion."""

    lines: tuple[TextLine, ...]
    image_width: int
    image_height: int
    options: dict[str, Any]
    #: Height of the FIRST page when several pages were stacked (see
    #: _stack_pages); None for a single page. The title search must not use
    #: the stacked height — the title lives on page 1, and 35% of a two-page
    #: stack reaches well into page 2.
    first_page_height: int | None = None


# --- Talking to the Swift binary --------------------------------------------


class VisionBinaryError(RuntimeError):
    """The Swift shim was missing, failed, or produced unparseable output."""


def _check_binary() -> None:
    """Fail with instructions rather than auto-building.

    Deliberately does NOT run build.sh. A hidden `swiftc` invocation in the
    middle of an OCR run is a surprising side effect and hides build errors.
    """
    if not BINARY_PATH.exists():
        raise VisionBinaryError(
            f"Vision OCR binary not found at {BINARY_PATH}.\n"
            f"Build it first:\n\n    bash {BUILD_SCRIPT}\n"
        )
    if not BINARY_PATH.is_file():
        raise VisionBinaryError(f"{BINARY_PATH} exists but is not a file.")


def _split_options(opts: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Separate Swift-side options from Python-side ones, rejecting unknowns."""
    unknown = set(opts) - VISION_OPTION_KEYS - PYTHON_OPTION_KEYS
    if unknown:
        raise TypeError(
            f"read() got unexpected option(s): {sorted(unknown)}. "
            f"Vision options: {sorted(VISION_OPTION_KEYS)}. "
            f"Python options: {sorted(PYTHON_OPTION_KEYS)}."
        )
    vision_opts = {k: v for k, v in opts.items() if k in VISION_OPTION_KEYS}
    python_opts = {k: v for k, v in opts.items() if k in PYTHON_OPTION_KEYS}
    return vision_opts, python_opts


def _run_binary(image_path: Path, vision_opts: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Run the shim and return its single stdout JSON object."""
    _check_binary()
    if not image_path.exists():
        raise FileNotFoundError(f"image not found: {image_path}")

    proc = subprocess.run(
        [str(BINARY_PATH), str(image_path)],
        input=json.dumps(vision_opts),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.stderr.strip():
        # The shim sends diagnostics and JSON error objects to stderr.
        logger.debug("vision_ocr stderr: %s", proc.stderr.strip())
    if proc.returncode != 0:
        raise VisionBinaryError(
            f"vision_ocr exited {proc.returncode} for {image_path}.\n"
            f"stderr: {proc.stderr.strip() or '(empty)'}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise VisionBinaryError(
            "vision_ocr did not produce a single JSON object on stdout "
            f"({exc}). A stray print() in VisionOCR.swift will cause this.\n"
            f"stdout began: {proc.stdout[:200]!r}"
        ) from exc


# --- The coordinate flip ----------------------------------------------------


def _to_canonical(
    bbox: dict[str, float], img_w: int, img_h: int
) -> tuple[float, float, float, float]:
    """Vision-normalized (bottom-left, y-up) -> canonical pixels (top-left, y-down).

    Vision's `y` is the box's BOTTOM edge and y grows upward, so the top and
    bottom edges swap when converting. Returns (x0, y0, x1, y1) with y0 the
    TOP edge in pixels.
    """
    x, y, w, h = bbox["x"], bbox["y"], bbox["width"], bbox["height"]
    x0 = x * img_w
    x1 = (x + w) * img_w
    y0 = (1.0 - (y + h)) * img_h  # Vision TOP edge    -> canonical y0
    y1 = (1.0 - y) * img_h  # Vision BOTTOM edge -> canonical y1
    if not (y0 <= y1):
        raise ValueError(
            f"coordinate flip produced y0={y0} > y1={y1} for bbox={bbox}. "
            "The top/bottom swap is inverted."
        )
    if not (x0 <= x1):
        raise ValueError(f"coordinate flip produced x0={x0} > x1={x1} for bbox={bbox}.")
    return x0, y0, x1, y1


def _clean_line_text(text: str) -> str:
    """Strip leading bullets, normalize unicode fractions, collapse whitespace."""
    text = LEADING_BULLET_RE.sub("", text)
    for glyph, ascii_form in UNICODE_FRACTIONS.items():
        if glyph in text:
            # "1 1/2" -> "1 1/2", "1/2-tablespoon" -> "1/2-tablespoon".
            text = re.sub(rf"(\d)\s*{re.escape(glyph)}", rf"\1 {ascii_form}", text)
            text = text.replace(glyph, ascii_form)
    return re.sub(r"\s+", " ", text).strip()


def read_lines(image_path: Path, **opts: Any) -> VisionOutput:
    """Run Vision and return converted lines. The debuggable half of `read`.

    Exposed separately because `Reading` has no room for geometry, and every
    parsing question ("why did it miss that ingredient?") is answered by
    looking at these lines.
    """
    vision_opts, python_opts = _split_options(opts)
    reading_order = python_opts.get("reading_order", DEFAULT_READING_ORDER)
    if reading_order not in READING_ORDERS:
        raise ValueError(f"reading_order must be one of {READING_ORDERS}, got {reading_order!r}")
    timeout = float(python_opts.get("timeout", DEFAULT_TIMEOUT_SECONDS))

    payload = _run_binary(Path(image_path), vision_opts, timeout)

    space = payload.get("coordinate_space")
    if space != EXPECTED_COORDINATE_SPACE:
        raise VisionBinaryError(
            f"vision_ocr reported coordinate_space={space!r}, expected "
            f"{EXPECTED_COORDINATE_SPACE!r}. The flip in _to_canonical assumes "
            "the expected space; refusing to guess."
        )

    img_w = int(payload["image"]["width"])
    img_h = int(payload["image"]["height"])

    lines: list[TextLine] = []
    for obs in payload["observations"]:
        candidates = tuple(
            (_clean_line_text(c["string"]), float(c["confidence"])) for c in obs["candidates"]
        )
        if not candidates or not candidates[0][0]:
            continue
        x0, y0, x1, y1 = _to_canonical(obs["bbox"], img_w, img_h)
        lines.append(
            TextLine(
                text=candidates[0][0],
                confidence=float(obs["confidence"]),
                x0=x0,
                y0=y0,
                x1=x1,
                y1=y1,
                candidates=candidates,
            )
        )

    if reading_order == "topdown":
        lines.sort(key=lambda ln: (ln.y0, ln.x0))

    if not lines:
        logger.warning("Vision returned 0 usable text lines for %s", image_path)

    return VisionOutput(
        lines=tuple(lines),
        image_width=img_w,
        image_height=img_h,
        options=payload.get("options", {}),
    )


# --- Parsing: text -> PhotoExtractResult ------------------------------------
#
# The page is segmented into blocks by walking the lines in reading order:
#
#   [title] [cook info] [headnote] [ingredients] [steps] [NOTE sidebar]
#
# Each block boundary is found by one explicit rule, documented at its
# function. Blocks that are not found are simply empty — nothing is guessed.


def _find_ingredient_start(lines: list[TextLine], image_width: int) -> int | None:
    """First index where an ingredient list plausibly begins.

    RULE: index i such that lines[i] starts with a quantity and, among the
    next INGREDIENT_RUN_WINDOW ingredient STARTS from i onward, at least
    INGREDIENT_RUN_MIN start with a quantity. The window requirement stops a
    lone numeric line in the headnote from being mistaken for the list.

    The window counts ingredient starts, not raw lines: a line indented
    within the continuation band relative to the line that started the
    current entry (the same geometric rule as _group_ingredient_lines) is a
    WRAPPED line and is skipped. Counting raw lines instead breaks lists
    whose first entries wrap — e.g. "1 pound baby potatoes, halved if /
    large" contributes one quantity hit but two lines, so a run of wrapped
    entries dilutes the window, the detected start lands mid-list, and the
    real first ingredients leak into the title/headnote blocks.
    """
    indent_min = CONTINUATION_INDENT_MIN_FRACTION * image_width
    indent_max = CONTINUATION_INDENT_MAX_FRACTION * image_width
    for i, line in enumerate(lines):
        if not QUANTITY_START_RE.match(line.text):
            continue
        hits = 0
        starts = 0
        start_x0: float | None = None
        for ln in lines[i:]:
            if start_x0 is not None and indent_min <= ln.x0 - start_x0 <= indent_max:
                continue  # wrapped continuation of the current entry
            starts += 1
            if starts > INGREDIENT_RUN_WINDOW:
                break
            start_x0 = ln.x0
            if QUANTITY_START_RE.match(ln.text):
                hits += 1
        if hits >= INGREDIENT_RUN_MIN:
            return i
    return None


def _find_steps_start(lines: list[TextLine], search_from: int) -> int | None:
    """First index of the numbered instructions.

    RULE: the first line at or after `search_from` that matches
    STEP_START_RE with step number 1. Anchoring on "1." rather than any
    number avoids latching onto "2 cups" style text.
    """
    for i in range(search_from, len(lines)):
        match = STEP_START_RE.match(lines[i].text)
        if match and match.group(1) == "1":
            return i
    return None


def _find_note_start(lines: list[TextLine], search_from: int) -> int | None:
    """Index of a standalone "NOTE" heading, which terminates the steps.

    RULE: the line's entire text is "NOTE"/"NOTES" (optionally with a colon).
    A line merely containing the word does not count.
    """
    for i in range(search_from, len(lines)):
        if NOTE_HEADING_RE.match(lines[i].text):
            return i
    return None


def _find_title(
    lines: list[TextLine], stop_before: int, page_height: int
) -> tuple[str, int, int]:
    """The recipe title, plus the half-open line range it consumed.

    RULE: among lines in the top TITLE_SEARCH_TOP_FRACTION of the FIRST page
    (and before the ingredient list), find the physically tallest. The title
    is that line plus the run of adjacent lines — BEFORE it as well as after
    — that are at least TITLE_HEIGHT_RATIO as tall. Titles wrap, and the
    tallest line is often the second one ("slow-roasted" / "sunday chicken"),
    so extending in only one direction loses half the title.

    RULE (metadata anchor): if any candidate-range line is PREP/COOK/TOTAL/
    SERVES metadata, only lines BEFORE the first such line stay candidates,
    and the title run may not extend past it. The title is printed above
    that block, never below it. This matters because bbox height alone is a
    noisy signal on hand-held photos: page tilt inflates a box by roughly
    width * sin(tilt), so a long skewed body or ingredient line can measure
    TALLER than a short display-font title line (observed: an ingredient
    line at 61 px vs title lines at 36-48 px). Document structure beats raw
    pixels here. If the restriction leaves no candidates, it is dropped.

    Display fonts are often set in lower case. If the result is entirely
    lower case, it is title-cased; if it already has capitals, it is left
    exactly as printed.

    Returns (title, start_index, end_index). The range is returned so the
    headnote block can skip these lines instead of guessing at them.
    """
    cutoff = TITLE_SEARCH_TOP_FRACTION * page_height
    candidates = [i for i in range(stop_before) if lines[i].y0 < cutoff]
    if not candidates:
        return FALLBACK_TITLE, 0, 0

    run_stop = stop_before
    meta = next(
        (i for i in range(stop_before) if COOK_INFO_RE.match(lines[i].text)), None
    )
    if meta is not None:
        restricted = [i for i in candidates if i < meta]
        if restricted:
            candidates = restricted
            run_stop = meta

    tallest = max(candidates, key=lambda i: lines[i].height)
    threshold = lines[tallest].height * TITLE_HEIGHT_RATIO

    start = tallest
    while start - 1 >= 0 and lines[start - 1].height >= threshold:
        start -= 1
    end = tallest + 1
    while end < run_stop and lines[end].height >= threshold:
        end += 1

    title = " ".join(lines[i].text for i in range(start, end)).strip()
    if not title:
        return FALLBACK_TITLE, start, end
    return (title.title() if title == title.lower() else title), start, end


def _group_ingredient_lines(lines: list[TextLine], image_width: int) -> list[str]:
    """Join wrapped ingredient lines into one string per ingredient.

    RULE: a line CONTINUES the previous ingredient if its left edge is
    indented relative to the line that started that ingredient, by between
    CONTINUATION_INDENT_MIN_FRACTION and CONTINUATION_INDENT_MAX_FRACTION of
    the image width. Otherwise it starts a new ingredient.

    Indentation, not "does it start with a digit", is the primary signal, and
    deliberately so:
      - "1/2-tablespoon slices" is a wrapped line that DOES start with a
        fraction, so a digit test would wrongly split it off.
      - "Fine pink Himalayan salt and freshly ground pepper" is a real
        ingredient that does NOT start with a digit, so a digit test would
        wrongly fold it into the line above.
    The upper bound keeps a jump to the next print column (a much larger x
    change) from reading as an indent.
    """
    indent_min = CONTINUATION_INDENT_MIN_FRACTION * image_width
    indent_max = CONTINUATION_INDENT_MAX_FRACTION * image_width

    groups: list[list[str]] = []
    start_x0: float | None = None
    for line in lines:
        indent = line.x0 - start_x0 if start_x0 is not None else None
        is_continuation = (
            groups
            and indent is not None
            and indent_min <= indent <= indent_max
            and not STEP_START_RE.match(line.text)
        )
        if is_continuation:
            groups[-1].append(line.text)
        else:
            groups.append([line.text])
            start_x0 = line.x0
    return [" ".join(g) for g in groups]


def _split_quantity_unit_name(text: str) -> tuple[str | None, str | None, str]:
    """Split "2 cups baby spinach" into ("2", "cups", "baby spinach").

    RULE: if the line begins with a quantity token, that becomes `amount`
    (kept as a STRING — per the project's quantity-handling note, compound
    quantities like "1 tablespoon + 1 sprig" cannot survive a numeric field).
    If the next word is a known unit (singular or plural), it becomes `unit`.
    Everything left is the name, verbatim.

    No quantity -> (None, None, whole line). That is the correct outcome for
    "Fine pink Himalayan salt and freshly ground pepper".
    """
    match = QUANTITY_START_RE.match(text)
    if not match:
        return None, None, text.strip()

    amount = re.sub(r"\s+", " ", match.group(0)).strip()
    rest = text[match.end() :].strip()

    unit: str | None = None
    words = rest.split()
    if words:
        head = words[0].strip(".,;:").lower()
        singular = head[:-1] if head.endswith("s") else head
        if head in UNIT_WORDS or singular in UNIT_WORDS:
            unit = words[0].strip(".,;:")
            rest = " ".join(words[1:]).strip()

    return amount or None, unit, rest


def _group_steps(lines: list[TextLine]) -> list[str]:
    """Join the numbered instruction lines into one string per step.

    RULE: a line beginning "<n>." or "<n>)" starts step n; every following
    line is appended to it until the next such marker. A bare "3." on its own
    line (the number set apart from its paragraph) works because the text
    after the marker may be empty.
    """
    steps: list[list[str]] = []
    for line in lines:
        match = STEP_START_RE.match(line.text)
        if match:
            remainder = match.group(2).strip()
            steps.append([remainder] if remainder else [])
        elif steps:
            steps[-1].append(line.text)
        else:
            # Text before the first marker: keep it rather than drop it.
            steps.append([line.text])
    return [" ".join(part for part in s if part).strip() for s in steps]


def parse_lines(vision: VisionOutput, photo_filename: str) -> PhotoExtractResult:
    """Turn recognized lines into a PhotoExtractResult for human review."""
    lines = list(vision.lines)
    if not lines:
        logger.warning("No text lines to parse for %s", photo_filename)
        return PhotoExtractResult(title=FALLBACK_TITLE, photo_filename=photo_filename)

    ing_start = _find_ingredient_start(lines, vision.image_width)
    steps_start = _find_steps_start(lines, ing_start if ing_start is not None else 0)
    note_start = _find_note_start(lines, steps_start if steps_start is not None else 0)

    title_stop = ing_start if ing_start is not None else len(lines)
    # The title lives on the first page; for stacked pages the search cutoff
    # must be a fraction of PAGE 1's height, not of the whole stack.
    page_height = (
        vision.first_page_height
        if vision.first_page_height is not None
        else vision.image_height
    )
    title, title_start, title_end = _find_title(lines, title_stop, page_height)

    # Ingredients run from the start of the list to the first numbered step.
    ingredients: list[IngredientCreate] = []
    if ing_start is not None:
        ing_end = steps_start if steps_start is not None else len(lines)
        for order, joined in enumerate(
            _group_ingredient_lines(lines[ing_start:ing_end], vision.image_width)
        ):
            amount, unit, name = _split_quantity_unit_name(joined)
            if not name:
                continue
            ingredients.append(
                IngredientCreate(name=name, amount=amount, unit=unit, order=order)
            )

    if not ingredients:
        # Loud, not silent: an empty ingredient list is almost always a
        # segmentation failure, not a recipe with no ingredients.
        logger.warning(
            "Parsed 0 ingredients from %s (%d text lines, ingredient block %s). "
            "Inspect read_lines() output.",
            photo_filename,
            len(lines),
            "not found" if ing_start is None else f"at line {ing_start}",
        )

    # Steps run from "1." to the NOTE heading (or the end of the page).
    instructions: str | None = None
    if steps_start is not None:
        steps_end = note_start if note_start is not None else len(lines)
        steps = _group_steps(lines[steps_start:steps_end])
        if steps:
            instructions = "\n\n".join(f"{i}. {s}" for i, s in enumerate(steps, start=1))
    if instructions is None:
        logger.warning("Parsed 0 instruction steps from %s", photo_filename)

    # Headnote: everything between the title and the ingredients that is not
    # part of the title itself and not a PREP/COOK/TOTAL/SERVES metadata line.
    headnote_lines: list[str] = []
    if ing_start is not None:
        for i in range(ing_start):
            if title_start <= i < title_end:
                continue
            if COOK_INFO_RE.match(lines[i].text):
                continue
            headnote_lines.append(lines[i].text)

    # Sidebar: the run of lines under the NOTE heading that stay in the same
    # print column, which excludes the page number and running head.
    sidebar_lines: list[str] = []
    if note_start is not None:
        heading_x0 = lines[note_start].x0
        tolerance = NOTE_COLUMN_TOLERANCE_FRACTION * vision.image_width
        for line in lines[note_start + 1 :]:
            if abs(line.x0 - heading_x0) > tolerance:
                break
            if PAGE_FURNITURE_RE.match(line.text):
                break
            sidebar_lines.append(line.text)

    note_parts: list[str] = []
    if headnote_lines:
        note_parts.append(" ".join(headnote_lines))
    if sidebar_lines:
        note_parts.append("NOTE: " + " ".join(sidebar_lines))
    notes = "\n\n".join(note_parts) or None

    # Servings: "SERVES 4 TO 6" -> 4. Ranges take the low end; a human can
    # raise it during review. If absent, the schema default (2) stands.
    servings: int | None = None
    for line in lines:
        match = SERVES_RE.search(line.text)
        if match:
            servings = int(match.group(1))
            break

    fields: dict[str, Any] = {
        "title": title,
        "instructions": instructions,
        "notes": notes,
        # Parsing lives in each reader; each reader opts into the shared
        # normalization (app/ocr/normalize.py) at its own boundary.
        "ingredients": normalize_ingredients(ingredients),
        "photo_filename": photo_filename,
    }
    if servings is not None:
        fields["servings"] = servings
    # meal_type and calories_per_serving are left at their schema defaults;
    # neither is reliably readable from a page image.
    return PhotoExtractResult(**fields)


# --- Public entry point -----------------------------------------------------


# Vertical white gap inserted between stacked pages, in pixels. Big enough
# that no line-grouping heuristic can mistake the junction for a wrapped
# line; otherwise arbitrary.
PAGE_STACK_GAP = 40.0

# A vertical gap this many times the page's median line-to-line spacing marks
# a BLOCK boundary for sub-recipe resolution (Cassie's blocking model: a
# sub-recipe is set off from the main content by substantial white space).
# Measured on the salmon's referenced page: normal spacing ~23-25px, the gap
# above the goddess-sauce block ~55px -> ratio ~2.2. Robust to page tilt,
# unlike line-height comparisons, because neighboring gaps inflate together.
BLOCK_GAP_FACTOR = 2.0


# Same-row jitter floor: top-to-top diffs below this many pixels are two
# columns sharing a row, not consecutive rows — excluded from the spacing
# median so column-dense pages don't drag the block threshold toward zero.
_ROW_JITTER_PX = 8.0


def _blocked_text(out: VisionOutput) -> str:
    """The page's text with blank lines at large vertical gaps.

    Encodes the page's visual block structure for the text-based resolver.
    Boundaries are judged on TOP-TO-TOP line spacing, not bottom-to-top:
    page tilt inflates Vision's box heights (a wide skewed line's y1 grows by
    ~width*sin(tilt)), so bottom-to-top gaps collapse under skew — measured
    on the salmon's referenced page, the 55px gap above the sauce block reads
    as 4px bottom-to-top. Top edges are stable under the same tilt.

    Within a block, lines keep (y, x) order — two-column blocks interleave,
    which the resolver's quantity/paragraph split tolerates.
    """
    lines = sorted(out.lines, key=lambda l: (l.y0, l.x0))
    if len(lines) < 3:
        return "\n".join(l.text for l in lines)
    spacings = sorted(
        d
        for a, b in zip(lines, lines[1:])
        if (d := b.y0 - a.y0) >= _ROW_JITTER_PX
    )
    median_spacing = spacings[len(spacings) // 2] if spacings else 24.0
    parts: list[str] = [lines[0].text]
    for prev, line in zip(lines, lines[1:]):
        if line.y0 - prev.y0 > BLOCK_GAP_FACTOR * median_spacing:
            parts.append("")  # block boundary
        parts.append(line.text)
    return "\n".join(parts)


def _stack_pages(outputs: list[VisionOutput]) -> VisionOutput:
    """Combine per-page outputs by stacking pages vertically.

    Page 2's lines get their y coordinates offset by page 1's height (plus a
    gap), as if the pages were laid out one above the other on a single tall
    page. The geometry-based parser then reads them in page order, and a step
    that continues across the page break is seen as consecutive lines.

    Assumes similar framing across pages (the indent-based wrap detection
    compares x positions between pages' lines only within a line run, but a
    grossly different crop between pages could still skew it). Untested
    against a real two-page recipe until the salmon ground truth exists.
    """
    from dataclasses import replace

    lines: list[TextLine] = []
    y_offset = 0.0
    for out in outputs:
        lines.extend(
            replace(line, y0=line.y0 + y_offset, y1=line.y1 + y_offset)
            for line in out.lines
        )
        y_offset += out.image_height + PAGE_STACK_GAP
    return VisionOutput(
        lines=tuple(lines),
        image_width=max(o.image_width for o in outputs),
        image_height=int(y_offset - PAGE_STACK_GAP),
        options=outputs[0].options,
        first_page_height=outputs[0].image_height,
    )


def read(image_path: Path | Sequence[Path], **opts: Any) -> Reading:
    """Recognize text with Apple Vision and parse it.

    `image_path` is one path, or an ordered list of paths for a recipe that
    spans several pages — list order is page order, first page carries the
    title. Pages are OCR'd separately and stacked (see _stack_pages), then
    parsed once.

    Options are either Vision options forwarded to the Swift shim
    (`recognitionLevel`, `usesLanguageCorrection`, `recognitionLanguages`,
    `minimumTextHeight`, `maxCandidates`, `revision`) or Python-side options
    (`reading_order`, `timeout`). Unknown keys raise TypeError.

    Raises VisionBinaryError if the shim is missing — run `swift/build.sh`.
    """
    paths = (
        [Path(p) for p in image_path]
        if isinstance(image_path, (list, tuple))
        else [Path(image_path)]
    )
    outputs = [read_lines(p, **opts) for p in paths]
    # raw_text deliberately stays the full join of ALL pages in order —
    # referenced pages included — for scoring and debugging.
    raw_text = "\n\n".join(
        "\n".join(line.text for line in out.lines) for out in outputs
    )

    if len(outputs) == 1:
        schema = parse_lines(outputs[0], photo_filename=paths[0].name)
        return Reading(method=METHOD_NAME, schema=schema, raw_text=raw_text or None)

    # Multi-page: pages after the first are either CONTINUATIONS (stacked
    # under page 1 as before) or REFERENCED pages — a sub-recipe that appears
    # as an ingredient in the main recipe, resolved by Cassie's blocking
    # model (app/ocr/resolve.py): the page splits into blocks at large
    # vertical gaps, and a lower block whose opening line names a main-list
    # ingredient is the sub-recipe. The resolver works on text, so the
    # geometry is encoded as blank lines here (see _blocked_text).
    # Detection uses a provisional parse of page 1 alone for the ingredient
    # list; referenced pages contribute only their text to the resolver and
    # are excluded from the geometric stack.
    page_texts = [_blocked_text(out) for out in outputs]
    provisional = parse_lines(outputs[0], photo_filename=paths[0].name)
    _, continuation_rel = resolve_references(provisional, page_texts[1:])
    continuation_pages = {i + 1 for i in continuation_rel}

    main_outputs = [outputs[0]] + [
        outputs[i] for i in range(1, len(outputs)) if i in continuation_pages
    ]
    vision = main_outputs[0] if len(main_outputs) == 1 else _stack_pages(main_outputs)
    schema = parse_lines(vision, photo_filename=paths[0].name)

    referenced_texts = [
        page_texts[i]
        for i in range(1, len(outputs))
        if i not in continuation_pages
    ]
    if referenced_texts:
        schema, _ = resolve_references(schema, referenced_texts)
    return Reading(method=METHOD_NAME, schema=schema, raw_text=raw_text or None)


if __name__ == "__main__":  # pragma: no cover - manual diagnostic
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if len(sys.argv) != 2:
        sys.exit("usage: python -m app.ocr.vision <image_path>")

    result = read(Path(sys.argv[1]))
    print(f"title      : {result.schema.title!r}")
    print(f"servings   : {result.schema.servings}")
    print(f"ingredients: {len(result.schema.ingredients)}")
    for ing in result.schema.ingredients:
        print(f"  amount={ing.amount!r:12} unit={ing.unit!r:14} name={ing.name!r}")
    steps = (result.schema.instructions or "").split("\n\n")
    print(f"steps      : {len(steps) if result.schema.instructions else 0}")
    for step in steps:
        print(f"  {step[:100]}")
    print(f"notes      : {(result.schema.notes or '')[:200]!r}")
