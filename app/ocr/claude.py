"""Claude-vision backend for cookbook-page photos.

What this file is
-----------------
One of several interchangeable "readers", alongside `app/ocr/tesseract.py`.
A reader takes a photo of a recipe page and returns a `Reading`.

Contract
--------
    read(image_path: Path, **opts) -> Reading

`Reading` (from `app.ocr.base`) is a frozen dataclass with exactly:
    method:   str                 -> always "claude" here
    schema:   PhotoExtractResult  -> the validated result
    raw_text: str | None          -> the model's response text, verbatim

**This reader is deliberately unused right now, and it is the odd one out.**
Every other reader in this package is local, deterministic, and offline: same
image in, same text out, no credentials, no cost. This one requires network
access and an Anthropic API key, costs money per call, and is not reproducible
run-to-run. The ensemble is being built with no API dependency, so nothing
calls `read()` today. It exists so the interface is complete and so the
vision-model baseline can be measured on demand (validation task #2).
Nothing in this module touches the network at import time — the only API call
lives inside `read()`.

The raw_text asymmetry
----------------------
For Tesseract, `raw_text` is a genuinely separate stage: OCR emits text, then a
parser turns that text into `PhotoExtractResult`. Scoring the two separately is
meaningful — a bad score can be blamed on the OCR *or* on the parser.

Claude has no such split. It returns structured JSON directly; there is no
"the text it read" distinct from "the fields it produced". So `raw_text` here
is the whole response body, which is *not* the same kind of object as
Tesseract's `raw_text`, even though it occupies the same field. It happens to
contain a transcription (the prompt's STEP 1/STEP 2 sections are emitted as
prose before the JSON), but that transcription is a by-product of the
chain-of-thought prompt, not an independent OCR stage. Do not compare
`raw_text` across the two methods with a character-level metric and expect the
comparison to mean anything.

What is reused, and what is not
-------------------------------
Reused from `app/services/photo_service.py` (production): `EXTRACTION_PROMPT`,
`MEDIA_TYPES`, `_build_image_content`, and `_parse_claude_json`. The last two
are private to that module; they are imported here anyway rather than
duplicated, so a fix to the production parser reaches this reader too.

Deliberately NOT reused: `_find_fraction_disagreements` and
`_resolve_with_voting`. Those implement an LLM-self-voting scheme that has been
rejected. This reader makes exactly one API call and returns what it says.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

from anthropic import Anthropic
from anthropic.types import Message
from pydantic import ValidationError

from app.config import settings
from app.ocr.base import Reading
from app.ocr.normalize import normalize_ingredients
from app.schemas.recipe import PhotoExtractResult

# `_build_image_content` and `_parse_claude_json` are private to photo_service
# (leading underscore). Importing them across module boundaries is a deliberate
# choice: duplicating them here would let the two copies drift, and the
# production parser is the behaviour we want to measure.
from app.services.photo_service import (  # noqa: PLC2701 - private, see comment
    EXTRACTION_PROMPT,
    MEDIA_TYPES,
    _build_image_content,
    _parse_claude_json,
)

logger = logging.getLogger(__name__)

METHOD_NAME = "claude"

# Model id comes from settings (`claude_model_extraction`), never a literal
# here — see app/config.py. Override it in .env, not in code.
#
# It is still an explicit pinned id rather than "latest", so a baseline
# measurement stays reproducible: if the setting changes, numbers produced
# under the old value are no longer comparable and should be re-run. Callers
# that need to sweep models pass `model=` to `read()` directly.

# Raised from production's 8192. On claude-opus-5 thinking is ON by default,
# and `max_tokens` is a single ceiling covering thinking *and* the visible
# response together. This prompt's response is already long (a STEP 1-3
# transcription preamble, then the recipe JSON with full instructions), so
# 8192 shared with thinking risks truncating mid-JSON. 16000 is the largest
# value that is comfortably safe without streaming; above roughly that, the
# SDK's own HTTP timeout becomes the binding constraint and the call would
# have to stream.
DEFAULT_MAX_TOKENS = 16000

# Effort levels accepted by `output_config.effort` on claude-opus-5. Left unset
# by default, which means the API default of "high" — appropriate for careful
# transcription. Exposed as an option so the accuracy/cost curve can be swept.
VALID_EFFORTS = ("low", "medium", "high", "xhigh", "max")


def read(
    image_path: Path | Sequence[Path],
    *,
    model: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    effort: str | None = None,
    prompt: str | None = None,
) -> Reading:
    """Send `image_path` to Claude and return the validated extraction.

    This is the `read(image_path, **opts) -> Reading` contract; the options are
    spelled out as keyword arguments so their defaults are visible.

    **This makes a paid network call.** It is the only place in this module
    that does.

    Args:
        image_path: Path to the photo, or an ordered list of paths for a
            recipe spanning several pages — list order is page order, first
            page carries the title. All pages go as image blocks in ONE
            message (the same shape production's `extract_recipe_from_photos`
            sends), plus an instruction that they are consecutive pages of a
            single recipe, so the model cannot split them into two recipes.
        model: Anthropic model id. Defaults to
            `settings.claude_model_extraction` (app/config.py). Pass explicitly
            only to sweep models; do not hardcode one at a call site.
        max_tokens: Hard ceiling on thinking + response tokens combined.
        effort: One of VALID_EFFORTS, or None to use the API default ("high").
        prompt: The instruction text sent after the image. None means the
            production `EXTRACTION_PROMPT`, so this reader measures what
            production actually does; pass a string to A/B a prompt change.
            (A None sentinel rather than the prompt itself as the default:
            EXTRACTION_PROMPT is ~6 KB and would swamp this function's
            signature in `help()` and every traceback.)

    Returns:
        A `Reading` whose `schema` is a validated `PhotoExtractResult` and
        whose `raw_text` is the model's full response text.

    Raises:
        FileNotFoundError: if `image_path` does not exist.
        ValueError: if the API key is missing, the file extension is not a
            format the API accepts, `effort` is not a recognised level, or the
            response is structurally unusable (refused, truncated, no text
            block, or an empty recipe list).
        fastapi.HTTPException: propagated out of `_parse_claude_json` when the
            response body contains no parseable JSON. A FastAPI exception is
            the wrong type for a non-HTTP caller; it is left as-is rather than
            re-wrapped so the production helper stays the single source of
            truth. Callers outside a request context should catch it by name.
        pydantic.ValidationError: if the parsed JSON does not fit
            `PhotoExtractResult` — re-raised untouched, because a schema
            mismatch is a real finding about the model's output and must not
            be smoothed over.
    """
    paths = (
        [Path(p) for p in image_path]
        if isinstance(image_path, (list, tuple))
        else [Path(image_path)]
    )
    for p in paths:
        if not p.is_file():
            raise FileNotFoundError(f"No such image: {p}")

    # Fail loudly and early on a missing key. No env-var fallback, no
    # interactive prompt, no partial result — the caller is asking for a paid
    # network call and needs to know it cannot be made.
    if not settings.anthropic_api_key:
        raise ValueError(
            "settings.anthropic_api_key is empty; the claude reader cannot run. "
            "Set ANTHROPIC_API_KEY in .env. (Every other reader in app/ocr "
            "works offline and needs no key.)"
        )

    for p in paths:
        suffix = p.suffix.lower()
        if suffix not in MEDIA_TYPES:
            raise ValueError(
                f"{p.name}: unsupported image type {suffix!r}. "
                f"The API accepts {sorted(MEDIA_TYPES)}."
            )

    if effort is not None and effort not in VALID_EFFORTS:
        raise ValueError(f"effort must be one of {VALID_EFFORTS}, got {effort!r}")

    # Resolved here rather than as a default argument value: a default is
    # evaluated once at import, which would freeze whatever the setting held at
    # import time and silently ignore a later change.
    model = model or settings.claude_model_extraction

    content = _build_image_content(paths)
    prompt_text = EXTRACTION_PROMPT if prompt is None else prompt
    if len(paths) > 1:
        # Kill the prompt's "pages may contain multiple distinct recipes"
        # escape hatch: multiple uploads are BY CONTRACT one recipe (owner's
        # rule), and the first image is the page with the title.
        prompt_text += (
            f"\n\nIMPORTANT: The {len(paths)} images above are consecutive "
            "pages of ONE single recipe, in reading order. The FIRST image "
            "is the first page and contains the recipe title. Do not return "
            "them as separate recipes."
        )
    content.append({"type": "text", "text": prompt_text})

    # Client construction is cheap and performs no I/O; the request below is
    # the network call.
    client = Anthropic(api_key=settings.anthropic_api_key)

    request: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": content}],
    }
    if effort is not None:
        request["output_config"] = {"effort": effort}

    message = client.messages.create(**request)

    logger.info(
        "claude reader: model=%s stop_reason=%s in=%s out=%s image=%s",
        message.model,
        message.stop_reason,
        message.usage.input_tokens,
        message.usage.output_tokens,
        ", ".join(p.name for p in paths),
    )

    raw_text = _extract_response_text(message)
    logger.debug("claude reader raw response for %s:\n%s", paths[0].name, raw_text)

    schema = _to_schema(raw_text, photo_filename=paths[0].name)
    # Parsing lives in each reader; each reader opts into the shared
    # normalization (app/ocr/normalize.py) at its own boundary.
    schema.ingredients = normalize_ingredients(schema.ingredients)
    return Reading(method=METHOD_NAME, schema=schema, raw_text=raw_text)


def _extract_response_text(message: Message) -> str:
    """Pull the assistant's visible text out of a Messages API response.

    Deliberately not `message.content[0].text` — the way production does it.
    On claude-opus-5 thinking is on by default, so `content[0]` is a thinking
    block, not text, and indexing position 0 would either crash or (with
    `display="omitted"`, the default) hand back an empty string. Scan for the
    first block that is actually text.

    Raises:
        ValueError: on a refusal, a truncated response, or no text block. Each
            of those would otherwise surface downstream as a confusing JSON
            parse error rather than as what it is.
    """
    if message.stop_reason == "refusal":
        raise ValueError(
            "Claude refused this request (stop_reason='refusal'); no extraction "
            f"was produced. stop_details={getattr(message, 'stop_details', None)}"
        )
    if message.stop_reason == "max_tokens":
        raise ValueError(
            "Response hit the max_tokens ceiling and is truncated, so any JSON "
            "in it is incomplete. Re-run with a larger max_tokens."
        )

    for block in message.content:
        if block.type == "text":
            return block.text

    raise ValueError(
        "Response contained no text block "
        f"(block types: {[b.type for b in message.content]})."
    )


def _to_schema(raw_text: str, *, photo_filename: str) -> PhotoExtractResult:
    """Parse the response body and validate it into `PhotoExtractResult`.

    `_parse_claude_json` does no schema validation at all — it returns whatever
    `json.loads` produced, so a missing `title` or a string where an int belongs
    goes unnoticed until something downstream trips over it. Adding a Pydantic
    check here is the "validate Claude's JSON response against a Pydantic
    schema" cleanup item from CLAUDE.md, done for this reader.

    Two shape mismatches are handled explicitly:

    1. `EXTRACTION_PROMPT` returns a JSON *array* of recipes (a cookbook page
       can hold two unrelated recipes). A `Reading` holds exactly one schema,
       so the first entry wins — per the prompt, that is the main recipe with
       the large title at the top of the page. A dropped second recipe is
       logged as a warning rather than passing silently.
    2. `photo_filename` is required by the schema but is not something the
       model can know, so it is injected from the caller's path.

    Fields the prompt asks for that `PhotoExtractResult` has no home for
    (`protein_type`, `cuisine`, and the per-ingredient `amount_confidence`) are
    dropped by Pydantic. That loses the confidence flags, which matter for
    scoring; they survive in `raw_text` if they are needed later.
    """
    parsed = _parse_claude_json(raw_text)
    recipes = parsed if isinstance(parsed, list) else [parsed]

    if not recipes:
        raise ValueError("Claude returned an empty recipe list; nothing to score.")
    if len(recipes) > 1:
        logger.warning(
            "Claude returned %d recipes for %s; keeping the first (%r) and "
            "dropping the rest. A Reading holds one recipe.",
            len(recipes),
            photo_filename,
            recipes[0].get("title") if isinstance(recipes[0], dict) else "?",
        )

    recipe = recipes[0]
    if not isinstance(recipe, dict):
        raise ValueError(
            f"Expected a JSON object per recipe, got {type(recipe).__name__}."
        )

    # A shallow copy: don't mutate the dict the parser handed back, in case a
    # caller is also holding it.
    fields = {**recipe, "photo_filename": photo_filename}

    try:
        return PhotoExtractResult.model_validate(fields)
    except ValidationError:
        # Re-raised unchanged. The point of validating is to make a bad
        # response visible; swallowing it here would defeat that. Logged first
        # so the offending keys are in the record even if the caller only
        # prints the exception type.
        logger.error(
            "Claude response for %s failed PhotoExtractResult validation; "
            "top-level keys were %s",
            photo_filename,
            sorted(fields),
        )
        raise
