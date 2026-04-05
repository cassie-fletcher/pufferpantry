"""Photo storage and Claude vision-based recipe extraction.

This module handles:
1. Saving uploaded photos to disk (with validation and resizing)
2. Sending photos to Claude Sonnet to extract structured recipe data

The extraction endpoint does NOT save the recipe to the database — it returns
the extracted data so the user can review/edit in the form before saving.
"""

import base64
import json
import secrets
from datetime import datetime
from pathlib import Path

from anthropic import Anthropic
from fastapi import HTTPException, UploadFile
from PIL import Image

from app.config import settings

PHOTOS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "photos"

# Claude vision works best with images up to 1568px on the long edge.
# Larger images cost more tokens without improving extraction quality.
MAX_IMAGE_DIMENSION = 1568

EXTRACTION_PROMPT = """\
You are extracting recipes from one or more photographs of cookbook pages.

IMPORTANT: Pages may contain multiple distinct recipes. A sub-recipe (like a dressing or \
sauce) that is referenced by the main recipe should be grouped WITH that recipe. But if a \
page has two completely separate main recipes (each with their own title at the top), return \
them as separate entries in a JSON array.

Return ONLY a JSON array of recipe objects (no markdown, no explanation). Even if there is \
only one recipe, wrap it in an array:

[
  {
    "title": "Recipe Title",
    "servings": 4,
    "protein_type": "salmon",
    "cuisine": "American",
    "calories_per_serving": 450,
    "instructions": "1.) ... 2.) ...\\n\\n--- Green Goddess Dressing ---\\n1.) ...",
    "notes": "Any tips or notes, or null",
    "ingredients": [
      {"name": "salmon fillets", "amount": "4", "unit": "6-oz", "order": 0, "group": "Main", "category": "Meat & Seafood"},
      {"name": "fresh herbs", "amount": "1", "unit": "cup", "order": 0, "group": "Green Goddess Dressing", "category": "Produce"}
    ]
  }
]

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
two entries: {"name": "salt"} and {"name": "pepper"}. "Salt and freshly ground pepper" → \
two entries. Never combine different ingredients on one line.
- "order" should be the zero-based index within each group.
- "title" should use Title Case capitalization.
- "protein_type" should be the main protein (e.g., "chicken", "salmon", "beef", "tofu", \
"shrimp"). Use null for vegetarian/no-protein dishes.
- "cuisine" should be the cuisine style (e.g., "American", "Mexican", "Japanese", "Italian", \
"Mediterranean", "Thai", "Indian", "Korean", "French"). Use your best judgment.
- If you cannot determine calories, set calories_per_serving to null.
- If servings are not stated, estimate based on the recipe.
- Be careful with amounts. A "1½-pound salmon fillet" means amount="1.5", unit="pound" — \
the number modifies the weight, not the count. "1 (4-pound) chicken" means amount="4", \
unit="pound". Read amounts carefully from the text.
- If multiple pages are provided, look for the main recipe (usually the large title at the \
top of the first page). Sub-recipes referenced by the main recipe (dressings, sauces, etc.) \
should be grouped with it using the "group" field — even if the sub-recipe appears on a \
different page. If the main recipe lists an ingredient like "Goddess Sauce" or "see page X", \
and another page contains that sauce/dressing recipe, extract the sub-recipe's ingredients \
into a named group and replace the reference ingredient with the actual ingredients.
- If a sub-recipe (like a sauce) is used by MULTIPLE main recipes on the pages, include \
the sub-recipe group in EACH main recipe that references it.
- If a page has a completely separate unrelated recipe, return it as a separate entry.
- Return valid JSON only. No code fences, no commentary.\
"""

# Map file extensions to MIME types for the Claude API
MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def save_photo(upload_file: UploadFile) -> str:
    """Validate, resize, and save an uploaded photo. Returns the filename."""
    contents = upload_file.file.read()

    # Validate it's a real image using Pillow
    try:
        img = Image.open(upload_file.file)
        img.verify()
        # Re-open after verify (verify closes the file)
        upload_file.file.seek(0)
        img = Image.open(upload_file.file)
    except Exception:
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid image")

    # Resize if the image is very large (saves Claude API tokens)
    if max(img.size) > MAX_IMAGE_DIMENSION:
        img.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION))

    # Generate a timestamped filename with random suffix to avoid collisions
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    random_suffix = secrets.token_hex(3)
    ext = Path(upload_file.filename or "photo.jpg").suffix.lower() or ".jpg"
    filename = f"{timestamp}_{random_suffix}{ext}"

    # Save to disk
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    save_path = PHOTOS_DIR / filename
    img.save(save_path, quality=85)

    return filename


def _parse_claude_json(response_text: str) -> dict:
    """Parse JSON from Claude's response, stripping markdown fences if present."""
    cleaned = response_text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=422,
            detail="Could not parse recipe from photo. Try a clearer photo.",
        )


def extract_recipe_from_photo(photo_path: Path) -> dict:
    """Send a single photo to Claude and extract structured recipe data."""
    return extract_recipe_from_photos([photo_path])


def extract_recipe_from_photos(photo_paths: list[Path]) -> dict:
    """Send one or more photos to Claude and extract structured recipe data.

    Multiple photos are sent as separate images in the same message,
    allowing Claude to read a recipe that spans multiple cookbook pages.
    """
    if not settings.anthropic_api_key:
        raise HTTPException(
            status_code=500,
            detail="Anthropic API key not configured. Add ANTHROPIC_API_KEY to your .env file.",
        )

    # Build the message content — one image block per photo, then the prompt
    content = []
    for photo_path in photo_paths:
        image_data = base64.standard_b64encode(photo_path.read_bytes()).decode("utf-8")
        ext = photo_path.suffix.lower()
        media_type = MEDIA_TYPES.get(ext, "image/jpeg")
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": image_data,
            },
        })
    content.append({"type": "text", "text": EXTRACTION_PROMPT})

    client = Anthropic(api_key=settings.anthropic_api_key)
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{"role": "user", "content": content}],
    )

    return _parse_claude_json(message.content[0].text)
