"""Recipe extraction from URLs.

Fetches a recipe page, extracts the text content, and sends it to Claude
to parse into structured recipe data. This works for any recipe site
regardless of their HTML structure.
"""

import json
import logging
import re
import secrets
from datetime import datetime
from pathlib import Path

import httpx
from anthropic import Anthropic
from anthropic.types import Message
from fastapi import HTTPException
from pydantic import ValidationError

from app.config import settings
from app.schemas.recipe import UrlExtractResult

logger = logging.getLogger(__name__)

PHOTOS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "photos"

# Hard ceiling on thinking + response tokens combined. On current Claude
# models thinking can be on by default and shares this budget with the
# visible JSON, so the old 2048 risked truncating mid-response. 16000 is the
# largest value that is comfortably safe without streaming (same reasoning as
# app/ocr/claude.py DEFAULT_MAX_TOKENS).
MAX_TOKENS = 16000

EXTRACTION_PROMPT = """\
You are extracting a recipe from text scraped from a recipe website.

Return ONLY a JSON object with these fields (no markdown, no explanation):

{
  "title": "Recipe Title",
  "servings": 4,
  "protein_type": "salmon",
  "cuisine": "American",
  "calories_per_serving": 450,
  "instructions": "1.) First step...\\n\\n--- Green Goddess Dressing ---\\n1.) Combine...",
  "notes": "Any tips or notes, or null",
  "ingredients": [
    {"name": "salmon fillets", "amount": "4", "unit": "6-oz", "order": 0, "group": "Main", "category": "Meat & Seafood"},
    {"name": "fresh herbs", "amount": "1", "unit": "cup", "order": 0, "group": "Green Goddess Dressing", "category": "Produce"}
  ]
}

Rules:
- "instructions" should be the full step-by-step text, preserving the original wording. \
Number steps as "1.) ...", "2.) ...", etc.
- If the recipe has sub-recipes or components (e.g., a dressing, sauce, marinade), put \
the sub-recipe instructions under a section header like "--- Green Goddess Dressing ---".
- For each ingredient, include a "group" field. Use "Main" for the primary recipe. \
Use the component name (e.g., "Green Goddess Dressing") for sub-recipe ingredients.
- For each ingredient, include a "category" field for grocery store aisle. Use one of: \
"Produce", "Meat & Seafood", "Dairy", "Bakery", "Frozen", "Drinks", "Pantry". \
Use your judgment — "chicken broth" is Pantry, not Meat. "Corn tortillas" is Pantry. \
Spices, oils, sauces, canned goods, dried goods are all Pantry.
- For each ingredient, separate the amount (numeric) from the unit (cups, tbsp, lb, etc.) \
and the name. If no clear amount, set amount to null.
- Ingredients often list multiple forms in one line using "plus" or commas, like \
"3 garlic cloves, finely chopped, plus 3 whole cloves" or "1 tablespoon fresh thyme leaves, \
plus 1 thyme sprig". ALWAYS combine these into a SINGLE ingredient entry. Put the total \
amount to buy in "amount" and preserve preparation details in the name. Examples: \
{"name": "garlic cloves (3 finely chopped + 3 whole)", "amount": "6", "unit": "cloves"}, \
{"name": "fresh thyme (1 tbsp leaves + 1 sprig)", "amount": "2", "unit": "tablespoons"}. \
Never split these into multiple entries.
- ALWAYS split compound ingredients into separate entries. "Salt and pepper" must become \
two entries: {"name": "salt"} and {"name": "pepper"}. Never combine different ingredients.
- "order" should be the zero-based index within each group.
- "title" should use Title Case capitalization.
- "protein_type" should be the main protein (e.g., "chicken", "salmon", "beef", "tofu", \
"shrimp"). Use null for vegetarian/no-protein dishes.
- "cuisine" should be the cuisine style (e.g., "American", "Mexican", "Japanese", "Italian", \
"Mediterranean", "Thai", "Indian", "Korean", "French"). Use your best judgment.
- If you cannot determine calories, set calories_per_serving to null.
- If servings are not stated, estimate based on the recipe.
- Be careful with amounts. A "1½-pound salmon fillet" means amount="1.5", unit="pound". \
Read amounts carefully from the text.
- Ignore ads, navigation, comments, related recipes, and other non-recipe content.
- Extract ONLY the main recipe on the page. Do not use content from related/suggested recipes.
- Return valid JSON only. No code fences, no commentary.\
"""


def _strip_tags(html: str) -> str:
    """Convert HTML to plain text by stripping tags."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&#8217;", "'").replace("&#8220;", '"')
    text = text.replace("&#8221;", '"').replace("&#8230;", "...").replace("&#xBC;", "1/4")
    text = text.replace("&#xBD;", "1/2").replace("&#xBE;", "3/4")
    return re.sub(r"\s+", " ", text).strip()


def _extract_text_from_html(html: str) -> str:
    """Extract recipe-relevant text from HTML.

    Strategy: try to find the recipe card container first (WPRM, Tasty Recipes,
    etc.). If found, extract only that section. Otherwise fall back to the
    full page text but remove obvious non-recipe sections.
    """
    # Try to find a recipe card container (WPRM, Tasty Recipes, etc.)
    # These plugins wrap the recipe in a known container class.
    container_patterns = [
        r'<div[^>]*class="[^"]*wprm-recipe-container[^"]*"',
        r'<div[^>]*class="[^"]*wprm-recipe\b[^"]*"',
        r'<div[^>]*class="[^"]*tasty-recipes-entry-content[^"]*"',
        r'<div[^>]*class="[^"]*recipe-card-container[^"]*"',
    ]

    for pattern in container_patterns:
        start = re.search(pattern, html, re.IGNORECASE)
        if start:
            # Take everything from the container start until a clear end marker
            chunk = html[start.start():]
            end_markers = [
                r'<div[^>]*class="[^"]*comments',
                r'<div[^>]*id="comments',
                r'<section[^>]*class="[^"]*related',
                r'<div[^>]*class="[^"]*related',
                r"</article>",
            ]
            end_pos = len(chunk)
            for marker in end_markers:
                m = re.search(marker, chunk, re.IGNORECASE)
                if m and m.start() < end_pos:
                    end_pos = m.start()
            recipe_html = chunk[:end_pos]
            text = _strip_tags(recipe_html)
            if len(text) > 200:
                return text[:10000]

    # Fallback: strip non-recipe sections and use the page text
    # Remove nav, footer, sidebar, comments, related posts
    cleaned = html
    for tag in ["nav", "footer", "aside"]:
        cleaned = re.sub(
            rf"<{tag}[^>]*>.*?</{tag}>", "", cleaned, flags=re.DOTALL | re.IGNORECASE
        )
    # Remove common non-recipe divs
    for cls in ["comments", "related", "sidebar", "widget", "advertisement", "share"]:
        cleaned = re.sub(
            rf'<div[^>]*class="[^"]*{cls}[^"]*"[^>]*>.*?</div>',
            "", cleaned, flags=re.DOTALL | re.IGNORECASE,
        )

    text = _strip_tags(cleaned)
    if len(text) > 10000:
        text = text[:10000] + "..."
    return text


def _find_recipe_image(html: str) -> str | None:
    """Find the main recipe image URL from the page HTML.

    Looks for the og:image meta tag (Open Graph — most recipe sites set this
    to the hero photo), trying both attribute orders. Nothing else is
    attempted; a page without og:image simply gets no photo.
    """
    # og:image is the most reliable — it's the social sharing image
    og_match = re.search(
        r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    )
    if not og_match:
        # Some sites put content before property
        og_match = re.search(
            r'<meta[^>]*content=["\']([^"\']+)["\'][^>]*property=["\']og:image["\']',
            html, re.IGNORECASE,
        )
    if og_match:
        return og_match.group(1)

    return None


def _download_image(image_url: str) -> str | None:
    """Download an image from a URL and save it to the photos directory.
    Returns the filename, or None if download fails.
    """
    try:
        resp = httpx.get(image_url, follow_redirects=True, timeout=15)
        resp.raise_for_status()
    except Exception:
        return None

    # Determine extension from content-type
    content_type = resp.headers.get("content-type", "")
    ext = ".jpg"
    if "png" in content_type:
        ext = ".png"
    elif "webp" in content_type:
        ext = ".webp"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    random_suffix = secrets.token_hex(3)
    filename = f"{timestamp}_{random_suffix}{ext}"

    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    save_path = PHOTOS_DIR / filename
    save_path.write_bytes(resp.content)

    return filename


def extract_recipe_from_url(url: str) -> dict:
    """Fetch a recipe URL and extract structured recipe data via Claude."""
    if not settings.anthropic_api_key:
        raise HTTPException(
            status_code=500,
            detail="Anthropic API key not configured. Add ANTHROPIC_API_KEY to your .env file.",
        )

    # Fetch the page
    try:
        response = httpx.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (PufferPantry recipe importer)"},
            follow_redirects=True,
            timeout=15,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=422, detail=f"Could not fetch URL: HTTP {e.response.status_code}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=422, detail=f"Could not fetch URL: {e}")

    page_html = response.text

    # Try to grab the recipe hero image from the page
    image_url = _find_recipe_image(page_html)

    # Extract text content
    page_text = _extract_text_from_html(page_html)
    if len(page_text) < 100:
        raise HTTPException(status_code=422, detail="Page appears to have no recipe content.")

    # Send to Claude for extraction.
    #
    # Resolved here (not at import time) so a .env / settings change takes
    # effect without a restart of module state; never hardcode a model id at
    # the call site (see app/config.py).
    #
    # No assistant prefill: prefilling the last assistant turn returns a 400
    # on current Claude models (Sonnet 4.6+ / Opus 4.6+). The prompt already
    # demands bare JSON, and _parse_recipe_json skips any preamble/fences.
    model = settings.claude_model_fast
    client = Anthropic(api_key=settings.anthropic_api_key)
    message = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=EXTRACTION_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Extract the main recipe from this page ({url}):\n\n{page_text}",
            },
        ],
    )

    logger.info(
        "url extraction: model=%s stop_reason=%s in=%s out=%s url=%s",
        message.model,
        message.stop_reason,
        message.usage.input_tokens,
        message.usage.output_tokens,
        url,
    )

    response_text = _extract_response_text(message)
    logger.debug("url extraction raw response for %s:\n%s", url, response_text)

    parsed = _parse_recipe_json(response_text)
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=422,
            detail="Could not parse recipe from this page. Try a different URL.",
        )

    # Validate Claude's JSON into the expected shape. A mismatch is a real
    # finding about the model's output — log the detail loudly, then surface
    # a clear client error instead of passing an arbitrary dict downstream.
    try:
        result = UrlExtractResult.model_validate(parsed)
    except ValidationError as exc:
        logger.error(
            "url extraction for %s failed UrlExtractResult validation "
            "(top-level keys: %s): %s",
            url,
            sorted(parsed),
            exc,
        )
        raise HTTPException(
            status_code=422,
            detail="Claude's extraction did not match the expected recipe format. "
            "Try a different URL.",
        )

    data = result.model_dump()

    # Download the recipe image if we found one
    if image_url:
        filename = _download_image(image_url)
        if filename:
            data["dish_photo_filename"] = filename

    return data


def _extract_response_text(message: Message) -> str:
    """Pull the assistant's visible text out of a Messages API response.

    Mirrors app/ocr/claude.py `_extract_response_text`, raising HTTPException
    instead of ValueError because this module runs inside a request handler.

    Deliberately not `message.content[0].text`: on current Claude models
    thinking can be on by default, so `content[0]` may be a thinking block —
    indexing position 0 would crash or hand back an empty string. Scan for
    the first block that is actually text, and surface refusal/truncation as
    clear errors rather than confusing JSON parse failures.
    """
    if message.stop_reason == "refusal":
        logger.warning(
            "url extraction refused by Claude: stop_details=%s",
            getattr(message, "stop_details", None),
        )
        raise HTTPException(
            status_code=422,
            detail="Claude declined to extract a recipe from this page.",
        )
    if message.stop_reason == "max_tokens":
        raise HTTPException(
            status_code=502,
            detail="Claude's response was truncated before the recipe was complete. "
            "Try again.",
        )

    for block in message.content:
        if block.type == "text":
            return block.text

    raise HTTPException(
        status_code=502,
        detail="Claude returned no text content for this page.",
    )


def _parse_recipe_json(response_text: str) -> dict | list:
    """Parse JSON out of Claude's response text.

    Same strategy as photo_service._parse_claude_json (kept local so the two
    paths can keep their own error messages): prefer a fenced ```json block,
    otherwise skip any preamble up to the first '{' or '['.
    """
    cleaned = response_text.strip()

    fence_match = re.search(r"```(?:json)?\s*\n([\s\S]*?)```", cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    else:
        positions = [p for p in (cleaned.find("["), cleaned.find("{")) if p >= 0]
        if positions:
            cleaned = cleaned[min(positions):]

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=422,
            detail="Could not parse recipe from this page. Try a different URL.",
        )
