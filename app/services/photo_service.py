"""Photo storage and Claude vision-based extraction.

This module handles:
1. Saving uploaded photos to disk (with validation and resizing)
2. Sending photos to Claude Sonnet to extract structured recipe data
3. Sending kitchen shelf photos to Claude to identify pantry items

Extraction endpoints do NOT save to the database — they return extracted data
so the user can review/edit before saving.
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

STEP 1: Before producing any JSON, transcribe every ingredient line exactly as it appears \
in the photo. For any fraction you encounter, read each digit separately: state the \
numerator, the slash, and the denominator as individual characters. For the denominator, \
describe its visual shape: does it have two curved bumps (3), a right angle with a \
diagonal (4), a straight top and curved bottom (5), or a single curve (2)? \
Report as "numerator: [digit], denominator: [digit] (shape: [description])". \
List them like this:

INGREDIENT TRANSCRIPTION:
- [exact text from photo] (fractions: [denominator check])
- ...

STEP 2: Now transcribe every line from the cooking instructions that mentions a specific \
amount or measurement. List them like this:

AMOUNTS IN STEPS:
- Step X: "[exact text mentioning an amount]" (fractions: [denominator check])
- ...

STEP 3: Cross-check — if any amount in STEP 1 disagrees with the same amount in STEP 2, \
re-examine the photo and state which reading is correct.

STEP 4: Now produce the structured JSON below, using ONLY the verified amounts from your \
cross-check above. Do not change any numbers.

IMPORTANT: Pages may contain multiple distinct recipes. A sub-recipe (like a dressing or \
sauce) that is referenced by the main recipe should be grouped WITH that recipe. But if a \
page has two completely separate main recipes (each with their own title at the top), return \
them as separate entries in a JSON array.

Return a JSON array of recipe objects after your transcription. Even if there is \
only one recipe, wrap it in an array:

[
  {
    "title": "Recipe Title",
    "servings": 4,
    "protein_type": "the main protein or vegetarian",
    "cuisine": "American",
    "calories_per_serving": 450,
    "instructions": "1.) ... 2.) ...\\n\\n--- Sauce Name ---\\n1.) ...",
    "notes": "Any tips or notes, or null",
    "ingredients": [
      {"name": "ingredient name", "amount": "READ FROM PHOTO", "unit": "READ FROM PHOTO", "order": 0, "group": "Main", "category": "Meat & Seafood", "amount_confidence": "high"},
      {"name": "ingredient name", "amount": "READ FROM PHOTO", "unit": "READ FROM PHOTO", "order": 0, "group": "Sauce Name", "category": "Produce", "amount_confidence": "high"}
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
- For each ingredient, include "amount_confidence": "high", "medium", or "low". Use "low" \
if the amount contains a fraction that was difficult to read, if you had to combine amounts \
across units, or if you are uncertain about any digit. Use "medium" if the amount is \
probably correct but the text was small or partially obscured. Use "high" only when you are \
certain the amount is exactly right.
- When the same ingredient appears multiple times in a recipe (e.g., listed with "plus" \
or used in two steps), combine into a SINGLE entry. Set "amount" to the computed total, \
"unit" to the recipe's most common unit, and put the breakdown in the name in parentheses \
using the exact amounts and units as they appear in the photo. \
Never split the same ingredient into multiple entries.
- ALWAYS split compound ingredients into separate entries. "Salt and pepper" must become \
two entries: {"name": "salt"} and {"name": "pepper"}. "Salt and freshly ground pepper" → \
two entries. Never combine different ingredients on one line.
- "order" should be the zero-based index within each group.
- "title" should use Title Case capitalization.
- "protein_type" should be the main protein (e.g., "chicken", "beef", "tofu", \
"shrimp"). If no protein is detected, set protein_type to "vegetarian".
- "cuisine" should be the cuisine style (e.g., "American", "Mexican", "Japanese", "Italian", \
"Mediterranean", "Thai", "Indian", "Korean", "French"). Use your best judgment.
- If you cannot determine calories, set calories_per_serving to null.
- If servings are not stated, estimate based on the recipe.
- To parse amounts: look for unit keywords (lb, pound, cup, tbsp, tsp, oz, gallon, quart, \
pint, g, kg, ml, L, etc.) in each ingredient line. A number connected to a unit by a dash \
(e.g., "2-pound") is the amount for that unit. Otherwise, read the number immediately \
adjacent to the unit. If a line contains multiple unit keywords, parse each number-unit \
pair separately. \
Always read the exact numbers from the photo. Never infer or default to a quantity from \
these instructions — the photo is the source of truth.
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


def _find_fraction_disagreements(raw_response: str) -> list[dict]:
    """Compare fractions from INGREDIENT TRANSCRIPTION vs AMOUNTS IN STEPS.

    Returns a list of disagreements like:
    [{"ingredient": "olive oil", "ingredient_fraction": "1/4", "step_fraction": "1/3"}]
    """
    import re

    # Extract fraction values from ingredient transcription section
    ing_section = ""
    steps_section = ""

    if "INGREDIENT TRANSCRIPTION:" in raw_response and "AMOUNTS IN STEPS:" in raw_response:
        ing_start = raw_response.index("INGREDIENT TRANSCRIPTION:")
        steps_start = raw_response.index("AMOUNTS IN STEPS:")
        ing_section = raw_response[ing_start:steps_start]
        # Find end of steps section (next STEP header or start of JSON)
        steps_end = len(raw_response)
        for marker in ["STEP 3:", "STEP 4:", "Cross-check", "[", "{"]:
            pos = raw_response.find(marker, steps_start + 20)
            if pos > 0 and pos < steps_end:
                steps_end = pos
        steps_section = raw_response[steps_start:steps_end]

    if not ing_section or not steps_section:
        return []

    # Find all fractions (N/M) in each section, paired with nearby ingredient names
    ing_fractions = re.findall(r"(\d+/\d+)\s*(?:cup|tbsp|tablespoon|tsp|teaspoon|pound|lb|oz|ounce)", ing_section, re.IGNORECASE)
    step_fractions = re.findall(r"(\d+/\d+)\s*(?:cup|tbsp|tablespoon|tsp|teaspoon|pound|lb|oz|ounce)", steps_section, re.IGNORECASE)

    # Simple approach: check if any fraction appears in steps but not in ingredients (or vice versa)
    disagreements = []
    ing_set = set(ing_fractions)
    step_set = set(step_fractions)

    # Fractions in steps that don't appear in ingredients = possible misread
    for frac in step_set - ing_set:
        disagreements.append({
            "step_fraction": frac,
            "ingredient_fractions": list(ing_set),
            "note": f"Steps mention {frac} but ingredients don't — possible misread",
        })

    for frac in ing_set - step_set:
        if step_set:  # Only flag if steps had fractions to compare against
            disagreements.append({
                "ingredient_fraction": frac,
                "step_fractions": list(step_set),
                "note": f"Ingredients mention {frac} but steps don't — possible misread",
            })

    return disagreements


def _resolve_with_voting(client, content, initial_recipes, disagreements):
    """Spawn additional parallel calls and vote on disagreed fractions."""
    import concurrent.futures

    def _call_claude():
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=8192,
            messages=[{"role": "user", "content": content}],
        )
        raw = msg.content[0].text
        print("=== VOTING CALL RESPONSE ===")
        print(raw[:1500])
        print("=== END VOTING RESPONSE ===\n")
        return raw

    num_votes = 4  # 4 additional + 1 original = 5 total
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_votes) as executor:
        futures = [executor.submit(_call_claude) for _ in range(num_votes)]
        voting_results = [f.result() for f in futures]

    # Count fraction occurrences across all responses (original + votes)
    import re
    all_responses = voting_results  # don't re-count original, it's already in disagreements
    fraction_counts = {}
    for raw in all_responses:
        fractions = re.findall(r"(\d+/\d+)\s*cup", raw, re.IGNORECASE)
        for frac in fractions:
            fraction_counts[frac] = fraction_counts.get(frac, 0) + 1

    print(f"=== FRACTION VOTE RESULTS: {fraction_counts} ===")

    # Use the response whose fractions best match the majority vote
    # For now, find the first voting response that contains the winning fraction
    if fraction_counts:
        winner = max(fraction_counts, key=fraction_counts.get)
        print(f"=== WINNING FRACTION: {winner} ===")
        for raw in voting_results:
            if winner in raw:
                try:
                    return _parse_claude_json(raw)
                except Exception:
                    continue

    # Fallback to initial result
    return initial_recipes


def _parse_claude_json(response_text: str) -> dict:
    """Parse JSON from Claude's response, stripping preamble text and markdown fences."""
    cleaned = response_text.strip()

    # Strip markdown fences anywhere in the response
    import re
    fence_match = re.search(r"```(?:json)?\s*\n([\s\S]*?)```", cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    else:
        # Find the first [ or { to skip any preamble text (e.g., transcription step)
        positions = [p for p in (cleaned.find("["), cleaned.find("{")) if p >= 0]
        if positions:
            cleaned = cleaned[min(positions):]

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
        model="claude-opus-4-6",
        max_tokens=8192,
        messages=[{"role": "user", "content": content}],
    )

    raw = message.content[0].text
    print("=== CLAUDE RAW RESPONSE ===")
    print(raw[:3000])
    print("=== END RAW RESPONSE ===")

    parsed = _parse_claude_json(raw)
    recipes = parsed if isinstance(parsed, list) else [parsed]

    # Cross-check: look for fraction disagreements between ingredients and instructions
    disagreements = _find_fraction_disagreements(raw)
    if disagreements:
        print(f"=== FRACTION DISAGREEMENTS DETECTED: {disagreements} ===")
        print("=== Spawning voting calls to resolve ===")
        recipes = _resolve_with_voting(client, content, recipes, disagreements)

    return recipes


# ---------------------------------------------------------------------------
# Pantry / kitchen inventory extraction
# ---------------------------------------------------------------------------

PANTRY_EXTRACTION_PROMPT = """\
You are analyzing photos of a kitchen storage area to identify food items and ingredients.
The user has indicated these photos are from their {storage_location}.

Return ONLY a JSON array of identified items (no markdown, no explanation):

[
  {{
    "name": "Greek yogurt",
    "quantity_level": "Half",
    "category": "Dairy",
    "confidence_note": null
  }},
  {{
    "name": "unknown container (opaque, tall)",
    "quantity_level": "Most",
    "category": null,
    "confidence_note": "Opaque container — cannot identify contents"
  }}
]

Rules for identification:
- List every distinct food item you can see or reasonably infer.
- For branded items, use the generic name (e.g., "sriracha" not "Huy Fong Sriracha").
- If you can read a label, use that name. If you can only partially read it, include \
what you can read and note it in confidence_note.
- For opaque or unlabeled containers, describe what you see (e.g., "brown liquid in \
glass jar", "white powder in container") and set confidence_note to explain the \
uncertainty. The user will fix these names during review.

Rules for quantity estimation:
- Use one of these levels: "Full", "Most", "Half", "Low", "Almost Empty"
- "Full" = container appears unopened or completely full
- "Most" = clearly opened/used but mostly full (roughly 65-90%)
- "Half" = about half remaining (roughly 35-65%)
- "Low" = noticeably depleted (roughly 10-35%)
- "Almost Empty" = nearly gone, just scraps or residue visible
- Estimate based on visual cues: fill level in transparent containers, weight/bulk \
of bags, how squeezed a tube is, etc.
- If the container is opaque and you cannot estimate, default to "Half" and note \
"quantity estimated — container is opaque" in confidence_note.

Rules for category:
- Use one of: "Produce", "Meat & Seafood", "Dairy", "Bakery", "Frozen", "Drinks", \
"Pantry", "Condiments", "Spices"
- Use your best judgment. Condiments = sauces, dressings, ketchup, mustard, etc. \
Spices = dried herbs, spice jars, seasoning blends.
- If unsure, set category to null.

Rules for confidence:
- Set confidence_note to null when you are confident in the identification.
- Set confidence_note to a short explanation when:
  - The item is partially hidden behind other items ("partially visible behind milk")
  - The label is not readable ("label facing away")
  - The container is opaque ("opaque container")
  - You are guessing based on shape/color ("appears to be butter based on packaging")
- Be conservative: it is better to flag uncertainty than to guess wrong. \
The user will review and correct every item.

Additional guidance:
- Items at the back of a crowded shelf may be partially obscured. Still list them \
with appropriate confidence notes.
- Multiple photos may show different angles of the same shelf. Deduplicate items \
that appear in multiple photos — do not list the same item twice.
- Ignore non-food items (cleaning supplies, medications, etc.).
- For produce, note if it looks past its prime in the confidence_note (e.g., \
"lettuce looks wilted").

Return valid JSON only. No code fences, no commentary.\
"""


def extract_pantry_items_from_photos(
    photo_paths: list[Path], storage_location: str
) -> list[dict]:
    """Send kitchen shelf photos to Claude and extract a list of identified items.

    Returns a list of dicts with name, quantity_level, category, confidence_note.
    Does NOT save to the database — the caller returns the draft for user review.
    """
    if not settings.anthropic_api_key:
        raise HTTPException(
            status_code=500,
            detail="Anthropic API key not configured. Add ANTHROPIC_API_KEY to your .env file.",
        )

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

    prompt_text = PANTRY_EXTRACTION_PROMPT.format(storage_location=storage_location)
    content.append({"type": "text", "text": prompt_text})

    client = Anthropic(api_key=settings.anthropic_api_key)
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=8192,
        messages=[{"role": "user", "content": content}],
    )

    result = _parse_claude_json(message.content[0].text)
    return result if isinstance(result, list) else [result]


# ---------------------------------------------------------------------------
# Zone layout identification (setup wizard)
# ---------------------------------------------------------------------------

ZONE_IDENTIFICATION_PROMPT = """\
You are analyzing photos of a {area_type} to identify its physical layout and storage zones.

Identify each distinct storage zone visible in the photos. A zone is a physically \
separate area: a shelf, a drawer, a door shelf section, a crisper, a pull-out tray, etc.

For each zone, determine:
- "name": descriptive name (e.g., "Top Shelf", "Left Crisper Drawer", "Door - Top Shelf")
- "zone_type": one of "shelf", "crisper_drawer", "door_shelf", "pullout_tray", \
"drawer", "compartment", "rack", "bin"
- "typical_categories": what types of food typically go here based on what you see \
(e.g., ["Condiments"] for door shelves full of bottles, ["Produce"] for crisper drawers)
- "typical_container_types": what container types you see (e.g., ["bottles", "jars"], \
["tupperware", "bags"])
- "scan_strategy": "spot_check" for zones with stable items that change slowly \
(condiments, sauces, spices) or "full_rescan" for zones that change frequently \
(produce, leftovers, dairy)

Return a JSON object:
{{
  "description": "Brief description of the overall layout",
  "suggested_zones": [
    {{
      "name": "Top Shelf",
      "zone_type": "shelf",
      "typical_categories": ["Dairy", "Drinks"],
      "typical_container_types": ["cartons", "bottles"],
      "scan_strategy": "full_rescan"
    }}
  ]
}}

Order zones from top to bottom, left to right. Include door shelves as separate zones \
from interior shelves. Be specific — "Door - Top Shelf" is better than just "Door". \
If you can see drawers or bins, include each one separately.

Return valid JSON only. No code fences, no commentary.\
"""


def extract_zone_layout_from_photos(
    photo_paths: list[Path], area_type: str
) -> dict:
    """Send overview photo(s) to Claude to identify physical zones in a storage area.

    Returns a dict with 'description' and 'suggested_zones' list.
    """
    if not settings.anthropic_api_key:
        raise HTTPException(
            status_code=500,
            detail="Anthropic API key not configured. Add ANTHROPIC_API_KEY to your .env file.",
        )

    content = _build_image_content(photo_paths)
    prompt_text = ZONE_IDENTIFICATION_PROMPT.format(area_type=area_type)
    content.append({"type": "text", "text": prompt_text})

    client = Anthropic(api_key=settings.anthropic_api_key)
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=8192,
        messages=[{"role": "user", "content": content}],
    )

    return _parse_claude_json(message.content[0].text)


# ---------------------------------------------------------------------------
# Zone-aware scanning (ongoing use)
# ---------------------------------------------------------------------------

ZONE_AWARE_SCAN_PROMPT = """\
You are analyzing close-up photos of a specific zone in a {area_type}.

Zone: {zone_name} ({zone_type})
Typical contents: {typical_categories}
Typical containers: {typical_container_types}
Scan mode: {scan_strategy}

{existing_items_section}

Your job is to identify every food item visible in the photos and compare against \
the existing item list above.

For each item you identify:
- If it matches an existing item and quantity looks the same: set match_action to "unchanged"
- If it matches an existing item but quantity level looks different: set match_action \
to "updated" and set the new quantity_level
- If it's a new item not in the existing list: set match_action to "new"

Also check: are any existing items NOT visible in the photos? Report those as "removed" \
with a confidence_note explaining they may just be hidden.

{scan_mode_guidance}

Return a JSON array:
[
  {{
    "name": "item name",
    "quantity_level": "Full",
    "category": "Dairy",
    "confidence_note": null,
    "match_action": "unchanged",
    "matched_item_id": 42
  }}
]

Rules:
- Use generic names, not brand names.
- For opaque containers, describe what you see and note uncertainty in confidence_note.
- Quantity levels: "Full", "Most", "Half", "Low", "Almost Empty"
- Categories: "Produce", "Meat & Seafood", "Dairy", "Bakery", "Frozen", "Drinks", \
"Pantry", "Condiments", "Spices"
- List EVERY individual item — do not group items (e.g., list each condiment bottle \
separately, each beverage separately).
- Be conservative: flag uncertainty rather than guessing wrong.
- For tupperware or opaque containers that likely contain leftovers, describe the \
container and note that contents are unknown.

Return valid JSON only. No code fences, no commentary.\
"""

SPOT_CHECK_GUIDANCE = """\
This is a SPOT CHECK scan. Focus on:
- Checking quantity levels of known items — have they gone down?
- Flagging any clearly new items that weren't here before
- Flagging any known items that are clearly missing
- You do NOT need to re-identify every item from scratch\
"""

FULL_RESCAN_GUIDANCE = """\
This is a FULL RESCAN. Identify every item visible in the photos, whether it matches \
an existing item or is completely new. Contents in this zone change frequently.\
"""


def extract_zone_items_from_photos(
    photo_paths: list[Path],
    zone,  # StorageZone model instance
    area,  # StorageArea model instance
    existing_items: list,  # list of PantryItem model instances
) -> list[dict]:
    """Send zone close-up photos to Claude with full zone context for diffing.

    Returns a list of dicts with name, quantity_level, category, confidence_note,
    match_action, matched_item_id.
    """
    import json as _json

    if not settings.anthropic_api_key:
        raise HTTPException(
            status_code=500,
            detail="Anthropic API key not configured. Add ANTHROPIC_API_KEY to your .env file.",
        )

    # Format existing items for the prompt
    if existing_items:
        items_list = "\n".join(
            f"{i+1}. [id={item.id}] {item.name} ({item.category or 'uncategorized'}) "
            f"- quantity: {item.quantity_level or 'unknown'}"
            for i, item in enumerate(existing_items)
        )
        existing_section = f"EXISTING ITEMS currently tracked in this zone:\n{items_list}"
    else:
        existing_section = "No existing items are currently tracked in this zone — everything you see is new."

    # Pick scan mode guidance
    scan_guidance = (
        SPOT_CHECK_GUIDANCE if zone.scan_strategy == "spot_check"
        else FULL_RESCAN_GUIDANCE
    )

    typical_cats = _json.loads(zone.typical_categories or "[]")
    typical_containers = _json.loads(zone.typical_container_types or "[]")

    prompt_text = ZONE_AWARE_SCAN_PROMPT.format(
        area_type=area.area_type,
        zone_name=zone.name,
        zone_type=zone.zone_type,
        typical_categories=", ".join(typical_cats) if typical_cats else "mixed",
        typical_container_types=", ".join(typical_containers) if typical_containers else "mixed",
        scan_strategy=zone.scan_strategy,
        existing_items_section=existing_section,
        scan_mode_guidance=scan_guidance,
    )

    content = _build_image_content(photo_paths)
    content.append({"type": "text", "text": prompt_text})

    client = Anthropic(api_key=settings.anthropic_api_key)
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=8192,
        messages=[{"role": "user", "content": content}],
    )

    result = _parse_claude_json(message.content[0].text)
    return result if isinstance(result, list) else [result]


def _build_image_content(photo_paths: list[Path]) -> list[dict]:
    """Build Claude API image content blocks from a list of photo paths."""
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
    return content
