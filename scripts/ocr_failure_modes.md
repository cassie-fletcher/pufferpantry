# OCR Pipeline — Generality Failure Modes

Running list of conditions a general cookbook-OCR pipeline should eventually handle. Not all need to be solved now — the point is to know what we're *not* solving yet, and to make sure the validation set eventually covers these.

## Page / paper
- Dark or cream-colored paper (low contrast vs. white-page assumption)
- Glossy pages (specular highlights, reflections from overhead light)
- Matte vs. glossy text (printed vs. photocopied vs. laser)
- Thin paper with show-through from the reverse page
- Aged / yellowed paper with stains or foxing
- Pages with decorative borders, watermarks, or background textures

## Layout
- Single-column vs. two-column recipes
- Two-page spreads (recipe spans left + right page)
- Ingredients inline with instructions (no clean column split)
- Recipes that share a page with another recipe
- Sidebars, pull quotes, chef's notes interleaved with the recipe
- Hand-written recipes / notes in margins

## Typography
- Serif vs. sans-serif, condensed vs. wide
- Italic or script headings
- Small-caps or decorative ingredient labels
- Fraction glyphs (½, ⅓, ¾) vs. ASCII fractions (1/2)
- Mixed units in one line ("1 cup (240 mL)")
- Non-English characters / accented letters (crème, jalapeño)

## Photo capture
- Perspective skew (phone held at an angle)
- Spine curl / non-linear deformation near the binding
- Partial page cutoff at edges
- Motion blur, focus blur
- Uneven lighting (one side shadowed)
- Hand, thumb, or object in frame holding the book open
- Glare / direct reflection of a light source
- Mixed lighting color temperatures
- Very high or very low resolution

## Book size / format
- Large-format coffee-table cookbooks vs. small paperbacks
- Ring-bound / spiral-bound (visible rings in the image)
- Recipe cards or printed single sheets (no binding context)
- Digital screenshots (already rectangular, no perspective correction needed)

## Content edge cases
- Recipes with photos inline (image regions mixed with text)
- Numbered instruction lists that look like quantities to the parser
- Ranges ("1–2 tsp", "350–375°F")
- Optional ingredients in parens
- "To taste", "as needed", "a pinch" — no numeric quantity
