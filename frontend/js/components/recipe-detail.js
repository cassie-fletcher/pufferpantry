/**
 * Creates a recipe detail view styled like a clean cookbook page.
 * Photo + title at top, ingredients and instructions in a two-column layout.
 * Notes appear at the bottom of the instructions column.
 * The hero photo can be repositioned via a toggle button.
 */
function createRecipeDetail(recipe, { onEdit, onClose, onUploadDishPhoto }) {
  const container = document.createElement("div");
  container.className = "recipe-detail";

  const ingredientsHtml = formatGroupedIngredients(recipe.ingredients);

  const instructions = recipe.instructions
    ? formatInstructions(escapeHtml(recipe.instructions))
    : "<p class='text-muted'>No instructions yet.</p>";

  const notesHtml = recipe.notes
    ? `<div class="detail-notes">
         <h3>Notes</h3>
         <p>${escapeHtml(recipe.notes)}</p>
       </div>`
    : "";

  const position = recipe.dish_photo_position || "center";

  const photoHtml = recipe.dish_photo_filename
    ? `<div class="detail-hero">
         <img src="/photos/${escapeHtml(recipe.dish_photo_filename)}"
              alt="${escapeHtml(recipe.title)}"
              style="object-position: ${position}">
         <button class="btn btn-small hero-reposition-btn" title="Reposition photo">Reposition</button>
       </div>`
    : "";

  container.innerHTML = `
    ${photoHtml}

    <div class="detail-title-bar">
      <div>
        <h2>${escapeHtml(recipe.title)}</h2>
        <div class="detail-subtitle">
          <span class="badge">${escapeHtml(recipe.meal_type)}</span>
          <span>${recipe.servings} servings</span>
          ${recipe.calories_per_serving ? `<span>&middot; ${recipe.calories_per_serving} cal/serving</span>` : ""}
        </div>
      </div>
      <div class="detail-actions">
        <button class="btn btn-secondary btn-small change-photo-btn">
          ${recipe.dish_photo_filename ? "Change Photo" : "Add Photo"}
        </button>
        <button class="btn btn-primary btn-small edit-btn">Edit</button>
        <button class="btn btn-secondary btn-small close-btn">Close</button>
      </div>
    </div>

    <div class="detail-content">
      <div class="detail-ingredients">
        ${ingredientsHtml}

        <div class="nutrition-panel" id="nutrition-panel">
          <h3>Nutrition Facts</h3>
          <p class="text-muted loading-text">Loading...</p>
        </div>
      </div>

      <div class="detail-instructions">
        <h3>Instructions</h3>
        ${instructions}

        <div class="detail-ratings">
          <h3>Ratings</h3>
          <div class="rating-row">
            <span class="rating-label">Cassie</span>
            <div class="rating-selector" data-person="cassie" data-current="${recipe.rating_cassie || ''}">
              ${buildRatingNumbers(recipe.rating_cassie)}
            </div>
          </div>
          <div class="rating-row">
            <span class="rating-label">Chris</span>
            <div class="rating-selector" data-person="chris" data-current="${recipe.rating_chris || ''}">
              ${buildRatingNumbers(recipe.rating_chris)}
            </div>
          </div>
        </div>

        ${notesHtml}
      </div>
    </div>
  `;

  container.querySelector(".edit-btn").addEventListener("click", () => onEdit(recipe));
  container.querySelector(".close-btn").addEventListener("click", onClose);

  // --- Ratings --- click a number to rate, saves immediately
  container.querySelectorAll(".rating-selector").forEach((selector) => {
    selector.addEventListener("click", async (e) => {
      const btn = e.target.closest(".rating-num");
      if (!btn) return;
      const person = selector.dataset.person;
      const value = parseInt(btn.dataset.value);
      const field = `rating_${person}`;

      // Update UI immediately
      selector.dataset.current = value;
      selector.querySelectorAll(".rating-num").forEach((n) => {
        n.classList.toggle("active", parseInt(n.dataset.value) <= value);
      });

      // Save to server
      await fetch(`/api/recipes/${recipe.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [field]: value }),
      });
    });
  });

  // --- Nutrition facts --- load asynchronously
  loadNutrition(recipe.id, container.querySelector("#nutrition-panel"));

  // Photo upload
  const fileInput = document.createElement("input");
  fileInput.type = "file";
  fileInput.accept = "image/*";
  fileInput.className = "hidden";
  container.appendChild(fileInput);

  container.querySelector(".change-photo-btn").addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    if (fileInput.files[0]) {
      onUploadDishPhoto(recipe.id, fileInput.files[0]);
    }
  });

  // --- Repositioning mode (activated by button) ---
  const hero = container.querySelector(".detail-hero");
  if (hero) {
    const img = hero.querySelector("img");
    const repositionBtn = hero.querySelector(".hero-reposition-btn");
    let repositionMode = false;
    let isDragging = false;
    let startY = 0;
    let startPosY = parsePositionY(position);

    repositionBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      repositionMode = !repositionMode;
      hero.classList.toggle("reposition-active", repositionMode);
      repositionBtn.textContent = repositionMode ? "Done" : "Reposition";

      // Save position when exiting reposition mode
      if (!repositionMode) {
        const newPosition = img.style.objectPosition;
        fetch(`/api/recipes/${recipe.id}/dish-photo-position`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ position: newPosition }),
        });
      }
    });

    hero.addEventListener("mousedown", (e) => {
      if (!repositionMode) return;
      isDragging = true;
      startY = e.clientY;
      startPosY = parsePositionY(img.style.objectPosition);
      hero.classList.add("dragging");
      e.preventDefault();
    });

    document.addEventListener("mousemove", (e) => {
      if (!isDragging) return;
      const deltaY = e.clientY - startY;
      const heroHeight = hero.offsetHeight;
      const deltaPercent = (deltaY / heroHeight) * 100;
      const newY = Math.max(0, Math.min(100, startPosY + deltaPercent));
      img.style.objectPosition = `center ${newY}%`;
    });

    document.addEventListener("mouseup", () => {
      if (!isDragging) return;
      isDragging = false;
      hero.classList.remove("dragging");
    });

    // Touch support
    hero.addEventListener("touchstart", (e) => {
      if (!repositionMode) return;
      isDragging = true;
      startY = e.touches[0].clientY;
      startPosY = parsePositionY(img.style.objectPosition);
      hero.classList.add("dragging");
    });

    document.addEventListener("touchmove", (e) => {
      if (!isDragging) return;
      const deltaY = e.touches[0].clientY - startY;
      const heroHeight = hero.offsetHeight;
      const deltaPercent = (deltaY / heroHeight) * 100;
      const newY = Math.max(0, Math.min(100, startPosY + deltaPercent));
      img.style.objectPosition = `center ${newY}%`;
    });

    document.addEventListener("touchend", () => {
      if (!isDragging) return;
      isDragging = false;
      hero.classList.remove("dragging");
    });
  }

  return container;
}


/** Format instructions into clean numbered paragraphs with sub-recipe headers. */
function formatInstructions(text) {
  let lines = text.split("\n").filter((l) => l.trim());

  // If it's all one line, try splitting on numbered patterns
  if (lines.length <= 1 && text.length > 100) {
    lines = text
      .split(/(?=\d+\.\)|Step\s+\d+)/i)
      .filter((l) => l.trim());
  }

  return lines
    .map((line) => line.trim())
    .filter((line) => line)
    .map((line) => line.replace(/^Step\s+(\d+)[:.]\s*/i, "$1.) "))
    .map((line) => {
      // Detect sub-recipe section headers like "--- Green Goddess Dressing ---"
      const sectionMatch = line.match(/^-{2,}\s*(.+?)\s*-{2,}$/);
      if (sectionMatch) {
        return `<h4 class="sub-recipe-header">${sectionMatch[1]}</h4>`;
      }
      return `<p>${line}</p>`;
    })
    .join("");
}


/** Format ingredients grouped by component (Main, sub-recipes). */
function formatGroupedIngredients(ingredients) {
  if (!ingredients || ingredients.length === 0) {
    return "<h3>Ingredients</h3><p class='text-muted'>None listed.</p>";
  }

  // Group ingredients by their group field
  const groups = {};
  for (const ing of ingredients) {
    const group = ing.group || "Main";
    if (!groups[group]) groups[group] = [];
    groups[group].push(ing);
  }

  const groupNames = Object.keys(groups);
  let html = "";

  for (const groupName of groupNames) {
    const items = groups[groupName];
    // Show "Ingredients" header for Main, group name for sub-recipes
    if (groupName === "Main") {
      html += "<h3>Ingredients</h3>";
    } else {
      html += `<h4 class="sub-recipe-header">${escapeHtml(groupName)}</h4>`;
    }
    html += "<ul>";
    for (const ing of items) {
      const parts = [ing.amount, ing.unit, ing.name].filter(Boolean);
      html += `<li>${escapeHtml(parts.join(" "))}</li>`;
    }
    html += "</ul>";
  }

  return html;
}


/** Parse the Y percentage from an object-position string like "center 30%". */
function parsePositionY(posStr) {
  if (!posStr || posStr === "center") return 50;
  const match = posStr.match(/([\d.]+)%/);
  if (match) return parseFloat(match[1]);
  return 50;
}


/** Build the 1-10 clickable number row for a rating. */
function buildRatingNumbers(currentValue) {
  let html = "";
  for (let i = 1; i <= 10; i++) {
    const active = currentValue && i <= currentValue ? "active" : "";
    html += `<button type="button" class="rating-num ${active}" data-value="${i}">${i}</button>`;
  }
  if (!currentValue) {
    html += `<span class="text-muted rating-unrated">Not rated</span>`;
  }
  return html;
}


/** Fetch and render nutrition facts into the panel element. */
async function loadNutrition(recipeId, panel) {
  try {
    const response = await fetch(`/api/recipes/${recipeId}/nutrition`);
    if (!response.ok) throw new Error("Failed to load");
    const data = await response.json();
    const ps = data.per_serving;

    panel.innerHTML = `
      <h3>Nutrition Facts</h3>
      <p class="nutrition-subtitle">Per serving (${data.servings} servings) &middot; Estimate</p>
      <div class="nutrition-table">
        <div class="nutrition-row nutrition-calories">
          <span>Calories</span>
          <span>${Math.round(ps.calories)}</span>
        </div>
        <div class="nutrition-row">
          <span>Protein</span>
          <span>${Math.round(ps.protein_g)}g</span>
        </div>
        <div class="nutrition-row">
          <span>Fat</span>
          <span>${Math.round(ps.fat_g)}g</span>
        </div>
        <div class="nutrition-row">
          <span>Carbs</span>
          <span>${Math.round(ps.carbs_g)}g</span>
        </div>
        <div class="nutrition-row">
          <span>Fiber</span>
          <span>${Math.round(ps.fiber_g)}g</span>
        </div>
        <div class="nutrition-row">
          <span>Sodium</span>
          <span>${Math.round(ps.sodium_mg)}mg</span>
        </div>
      </div>
    `;
  } catch {
    panel.innerHTML = `
      <h3>Nutrition Facts</h3>
      <p class="text-muted">Could not load nutrition data.</p>
    `;
  }
}
