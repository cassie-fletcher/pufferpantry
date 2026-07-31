"""Tests for app/ocr/resolve.py — sub-recipe reference resolution.

All synthetic: pure functions on text, no OCR engines invoked.
"""

from app.ocr.resolve import (
    find_reference,
    parse_block,
    resolve_references,
    split_blocks,
)
from app.schemas.recipe import IngredientCreate, PhotoExtractResult


def ing(name, amount=None, unit=None, order=0, group="Main"):
    return IngredientCreate(
        name=name, amount=amount, unit=unit, order=order, group=group
    )


REFERENCE_NAME = (
    "Goddess Sauce (page 153) or store-bought green goddess dressing"
)


def main_schema():
    return PhotoExtractResult(
        title="Slow-Roasted Salmon",
        servings=4,
        instructions=(
            "1. Heat the oven to 300 degrees.\n\n"
            "2. Roast the salmon until just opaque."
        ),
        ingredients=[
            ing("skin-on salmon fillets", amount="4", order=0),
            ing("olive oil", amount="2", unit="tablespoons", order=1),
            ing(REFERENCE_NAME, amount="1", unit="cup", order=2),
            ing("Fine pink Himalayan salt", order=3),
        ],
        photo_filename="salmon.jpg",
    )


# The referenced page: an unrelated recipe fills most of it; the goddess
# sauce block sits at the bottom (the common real-page layout).
REFERENCED_PAGE = """\
ROASTED CAULIFLOWER STEAKS
SERVES 4

1 head cauliflower, cut into thick slabs
3 tablespoons olive oil

1. Heat the oven to 425 degrees.
2. Roast the cauliflower until deeply browned.

GODDESS SAUCE
1/2 cup mayonnaise
2 tablespoons lemon juice
½ cup packed fresh basil leaves
Blend everything until smooth and green.
Season with salt."""

# A plain continuation page: main-recipe step prose, no matching heading.
CONTINUATION_PAGE = """\
3. Transfer the salmon to a platter.
4. Spoon the sauce over the top and serve warm."""


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------


def test_find_reference_detects_goddess_sauce():
    match = find_reference(main_schema().ingredients, REFERENCED_PAGE)
    assert match is not None
    assert match.heading == "GODDESS SAUCE"
    assert match.group == "GODDESS SAUCE"
    assert match.ingredient_index == 2
    block = split_blocks(REFERENCED_PAGE)[match.block_index]
    assert block.splitlines()[0] == "GODDESS SAUCE"


def test_find_reference_none_on_continuation_page():
    assert find_reference(main_schema().ingredients, CONTINUATION_PAGE) is None


def test_find_reference_fuzzy_matches_case_and_ocr_noise():
    # Title Case instead of ALL-CAPS still matches (token subset after
    # lowercasing), and a one-letter OCR drop matches via the difflib path.
    ingredients = main_schema().ingredients
    # A leading block stands in for the page's own content: only blocks
    # after the first are candidates under the blocking model.
    assert find_reference(ingredients, "OTHER CONTENT\n\nGoddess Sauce\n1/2 cup mayo") is not None
    assert find_reference(ingredients, "OTHER CONTENT\n\nGODDES SAUCE\n1/2 cup mayo") is not None


def test_serves_line_does_not_match_any_ingredient():
    # "SERVES 4" is heading-shaped but must not be mistaken for a reference.
    ingredients = main_schema().ingredients
    assert find_reference(ingredients, "SOME RECIPE\n\nSERVES 4") is None


# --------------------------------------------------------------------------
# Block extraction and parsing
# --------------------------------------------------------------------------


def test_split_blocks_isolates_the_sauce_block():
    blocks = split_blocks(REFERENCED_PAGE)
    assert len(blocks) == 4  # title/serves, ingredients, steps, sauce
    sauce = blocks[-1]
    assert sauce.splitlines()[0] == "GODDESS SAUCE"
    assert sauce.splitlines()[-1] == "Season with salt."
    assert "cauliflower" not in sauce.lower()


def test_split_blocks_requires_blank_line_boundaries():
    # No blank line, no boundary: the blocking model relies on white space
    # (readers encode geometric gaps as blank lines).
    page = "GODDESS SAUCE\n1/2 cup mayonnaise\nANOTHER RECIPE\n2 cups flour"
    assert len(split_blocks(page)) == 1


def test_parse_block_quantity_unit_name_split():
    block = (
        "GODDESS SAUCE\n"
        "2 tablespoons lemon juice\n"
        "½ cup packed fresh basil leaves\n"
        "1 ½ cups mayonnaise\n"
        "2 lemons\n"
        "Blend until smooth."
    )
    ingredients, instructions = parse_block(block, group="GODDESS SAUCE")
    by_name = {i.name: i for i in ingredients}

    assert by_name["lemon juice"].amount == "2"
    assert by_name["lemon juice"].unit == "tablespoons"
    # Unicode fraction glyphs split correctly, bare and mixed.
    assert by_name["packed fresh basil leaves"].amount == "1/2"
    assert by_name["packed fresh basil leaves"].unit == "cup"
    assert by_name["mayonnaise"].amount == "1 1/2"
    assert by_name["mayonnaise"].unit == "cups"
    # No plausible unit word -> whole remainder is the name.
    assert by_name["lemons"].amount == "2"
    assert by_name["lemons"].unit is None

    assert all(i.group == "GODDESS SAUCE" for i in ingredients)
    assert [i.order for i in ingredients] == list(range(len(ingredients)))
    assert instructions == "Blend until smooth."


# --------------------------------------------------------------------------
# Merge
# --------------------------------------------------------------------------


def test_resolve_merges_referenced_page():
    schema = main_schema()
    resolved, continuations = resolve_references(schema, [REFERENCED_PAGE])
    assert continuations == []

    sauce = [i for i in resolved.ingredients if i.group == "GODDESS SAUCE"]
    assert [i.name for i in sauce] == [
        "mayonnaise",
        "lemon juice",
        "packed fresh basil leaves",
    ]
    assert [i.amount for i in sauce] == ["1/2", "2", "1/2"]
    assert [i.order for i in sauce] == [0, 1, 2]

    # The referencing ingredient line stays in Main untouched.
    assert resolved.ingredients[2].name == REFERENCE_NAME
    assert resolved.ingredients[2].group == "Main"
    # Main ingredients precede the merged group, unchanged.
    assert resolved.ingredients[:4] == schema.ingredients

    # Instructions gain the section with the --- header, after the main steps.
    assert resolved.instructions.startswith(schema.instructions)
    assert (
        "\n\n--- GODDESS SAUCE ---\n"
        "Blend everything until smooth and green. Season with salt."
    ) in resolved.instructions

    # Nothing from the unrelated recipe leaks into the result.
    dumped = resolved.model_dump_json().lower()
    assert "cauliflower" not in dumped
    assert "425" not in dumped


def test_resolve_returns_continuation_indices_and_leaves_schema_unchanged():
    schema = main_schema()
    resolved, continuations = resolve_references(schema, [CONTINUATION_PAGE])
    assert continuations == [0]
    assert resolved == schema

    # Mixed: continuation page keeps its index; referenced page merges.
    resolved, continuations = resolve_references(
        schema, [CONTINUATION_PAGE, REFERENCED_PAGE]
    )
    assert continuations == [0]
    assert any(i.group == "GODDESS SAUCE" for i in resolved.ingredients)


def test_resolve_does_not_mutate_input_schema():
    schema = main_schema()
    before = schema.model_copy(deep=True)
    resolve_references(schema, [REFERENCED_PAGE])
    assert schema == before


def test_resolve_is_deterministic():
    first = resolve_references(main_schema(), [REFERENCED_PAGE, CONTINUATION_PAGE])
    second = resolve_references(main_schema(), [REFERENCED_PAGE, CONTINUATION_PAGE])
    assert first == second
