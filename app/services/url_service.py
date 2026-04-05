"""Recipe extraction from URLs.

Fetches a recipe page, extracts the text content, and sends it to Claude
to parse into structured recipe data. This works for any recipe site
regardless of their HTML structure.
"""

import json
import re
import secrets
from datetime import datetime
from pathlib import Path

import httpx
from anthropic import Anthropic
from fastapi import HTTPException

from app.config import settings

PHOTOS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "photos"

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

    Tries (in order):
    1. og:image meta tag (Open Graph — most recipe sites set this to the hero photo)
    2. First large image inside a WPRM recipe container
    3. First image in the article
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

    # Send to Claude for extraction
    client = Anthropic(api_key=settings.anthropic_api_key)
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        messages=[
            {
                "role": "user",
                "content": f"Extract the main recipe from this page ({url}):\n\n{page_text}",
            },
            {
                "role": "assistant",
                "content": "I'll extract the recipe and return it as JSON.\n\n{",
            },
        ],
        system=EXTRACTION_PROMPT,
    )

    # Parse response — Claude's response starts after our prefilled "{"
    response_text = "{" + message.content[0].text

    # Strip markdown fences if present
    cleaned = response_text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=422,
            detail="Could not parse recipe from this page. Try a different URL.",
        )

    # Download the recipe image if we found one
    if image_url:
        filename = _download_image(image_url)
        if filename:
            result["dish_photo_filename"] = filename

    return result
