/**
 * Builds and manages the "Add Recipe" form.
 * Returns a form element; calls onSubmit(recipeData) when the user submits.
 *
 * If initialData is provided (from a photo scan), the form is pre-filled
 * with the extracted values so the user can review and edit before saving.
 */
function createRecipeForm({ onSubmit, onCancel, initialData = null }) {
  const form = document.createElement("form");
  form.className = "recipe-form";

  let heading = "Add Recipe";
  if (initialData && initialData.id) {
    heading = "Edit Recipe";
  } else if (initialData) {
    heading = "Review Scanned Recipe";
  }

  form.innerHTML = `
    <h2>${heading}</h2>

    <div class="form-row">
      <div class="form-group">
        <label for="title">Title *</label>
        <input type="text" id="title" name="title" required>
      </div>
      <div class="form-group">
        <label for="meal_type">Meal Type *</label>
        <select id="meal_type" name="meal_type" required>
          <option value="dinner">Dinner</option>
          <option value="lunch">Lunch</option>
          <option value="breakfast">Breakfast</option>
          <option value="dessert">Dessert</option>
          <option value="drinks">Drinks</option>
        </select>
      </div>
    </div>

    <div class="form-row">
      <div class="form-group">
        <label for="protein_type">Protein</label>
        <input type="text" id="protein_type" name="protein_type" placeholder="e.g., chicken, salmon, tofu">
      </div>
      <div class="form-group">
        <label for="cuisine">Cuisine</label>
        <input type="text" id="cuisine" name="cuisine" placeholder="e.g., Mexican, Japanese, Italian">
      </div>
    </div>

    <div class="form-row">
      <div class="form-group">
        <label for="servings">Servings</label>
        <input type="number" id="servings" name="servings" value="2" min="1">
      </div>
      <div class="form-group">
        <label for="calories_per_serving">Calories per Serving</label>
        <input type="number" id="calories_per_serving" name="calories_per_serving">
      </div>
    </div>

    <div class="form-group">
      <label>Ingredients</label>
      <div id="ingredient-groups"></div>
      <div class="ingredient-group-actions">
        <button type="button" class="btn btn-secondary btn-small" id="add-component-btn">
          + Add Component (e.g., dressing, sauce)
        </button>
      </div>
    </div>

    <div class="form-group">
      <label for="instructions">Instructions</label>
      <textarea id="instructions" name="instructions"
                placeholder="Step-by-step cooking instructions..."></textarea>
    </div>

    <div class="form-group">
      <label for="notes">Notes</label>
      <textarea id="notes" name="notes"
                placeholder="Personal notes, tips, etc."></textarea>
    </div>

    <div class="form-actions">
      <button type="submit" class="btn btn-primary">Save Recipe</button>
      <button type="button" class="btn btn-secondary" id="cancel-btn">Cancel</button>
    </div>
  `;

  const groupsContainer = form.querySelector("#ingredient-groups");

  // Pre-fill from scanned data, or start with a Main group
  if (initialData) {
    form.title.value = initialData.title || "";
    form.meal_type.value = initialData.meal_type || "dinner";
    form.protein_type.value = initialData.protein_type || "";
    form.cuisine.value = initialData.cuisine || "";
    form.servings.value = initialData.servings || 2;
    form.calories_per_serving.value = initialData.calories_per_serving || "";
    form.instructions.value = initialData.instructions || "";
    form.notes.value = initialData.notes || "";

    if (initialData.ingredients && initialData.ingredients.length > 0) {
      // Group ingredients by their group field
      const groups = {};
      for (const ing of initialData.ingredients) {
        const g = ing.group || "Main";
        if (!groups[g]) groups[g] = [];
        groups[g].push(ing);
      }
      for (const [groupName, ings] of Object.entries(groups)) {
        const groupEl = addIngredientGroup(groupsContainer, groupName);
        const rows = groupEl.querySelector(".ingredient-rows");
        for (const ing of ings) {
          addIngredientRow(rows, ing);
        }
      }
    } else {
      const groupEl = addIngredientGroup(groupsContainer, "Main");
      addIngredientRow(groupEl.querySelector(".ingredient-rows"));
    }
  } else {
    const groupEl = addIngredientGroup(groupsContainer, "Main");
    addIngredientRow(groupEl.querySelector(".ingredient-rows"));
  }

  form.querySelector("#add-component-btn").addEventListener("click", () => {
    const name = prompt("Component name (e.g., Green Goddess Dressing):");
    if (name && name.trim()) {
      const groupEl = addIngredientGroup(groupsContainer, name.trim());
      addIngredientRow(groupEl.querySelector(".ingredient-rows"));
    }
  });

  form.querySelector("#cancel-btn").addEventListener("click", onCancel);

  form.addEventListener("submit", (e) => {
    e.preventDefault();

    const data = {
      title: form.title.value.trim(),
      meal_type: form.meal_type.value,
      protein_type: form.protein_type.value.trim() || null,
      cuisine: form.cuisine.value.trim() || null,
      servings: parseInt(form.servings.value) || 2,
      calories_per_serving: form.calories_per_serving.value
        ? parseInt(form.calories_per_serving.value)
        : null,
      instructions: form.instructions.value.trim() || null,
      notes: form.notes.value.trim() || null,
      ingredients: collectAllIngredients(groupsContainer),
    };

    // Include metadata from photo scan or URL import
    if (initialData) {
      if (initialData.photo_filename) {
        data.photo_filename = initialData.photo_filename;
      }
      if (initialData.dish_photo_filename) {
        data.dish_photo_filename = initialData.dish_photo_filename;
      }
      if (initialData.source_type) {
        data.source_type = initialData.source_type;
      }
      if (initialData.source_details) {
        data.source_details = initialData.source_details;
      }
      // Default source_type for cookbook scans
      if (initialData.photo_filename && !data.source_type) {
        data.source_type = "cookbook";
      }
    }

    onSubmit(data);
  });

  return form;
}


function addIngredientRow(container, data = null) {
  const row = document.createElement("div");
  row.className = "ingredient-row";

  row.innerHTML = `
    <input type="text" placeholder="Ingredient name" class="ing-name">
    <input type="text" placeholder="Amount" class="ing-amount">
    <input type="text" placeholder="Unit" class="ing-unit">
    <button type="button" class="btn btn-danger btn-small remove-ing">&times;</button>
  `;

  // Pre-fill if data was provided (from photo extraction)
  if (data) {
    row.querySelector(".ing-name").value = data.name || "";
    row.querySelector(".ing-amount").value = data.amount || "";
    row.querySelector(".ing-unit").value = data.unit || "";
  }

  row.querySelector(".remove-ing").addEventListener("click", () => {
    row.remove();
  });

  container.appendChild(row);

  // Only auto-focus when adding a blank row manually
  if (!data) {
    row.querySelector(".ing-name").focus();
  }
}


function addIngredientGroup(container, groupName) {
  const group = document.createElement("div");
  group.className = "ingredient-group";
  group.dataset.group = groupName;

  const isMain = groupName === "Main";

  group.innerHTML = `
    <div class="ingredient-group-header">
      <span class="ingredient-group-name">${isMain ? "Main Ingredients" : escapeHtml(groupName)}</span>
      ${!isMain ? '<button type="button" class="btn btn-danger btn-small remove-group">&times;</button>' : ""}
    </div>
    <div class="ingredient-rows"></div>
    <button type="button" class="btn btn-secondary btn-small add-ing-btn">+ Add Ingredient</button>
  `;

  const rows = group.querySelector(".ingredient-rows");

  group.querySelector(".add-ing-btn").addEventListener("click", () => {
    addIngredientRow(rows);
  });

  const removeBtn = group.querySelector(".remove-group");
  if (removeBtn) {
    removeBtn.addEventListener("click", () => group.remove());
  }

  container.appendChild(group);
  return group;
}


function collectAllIngredients(groupsContainer) {
  const ingredients = [];
  const groups = groupsContainer.querySelectorAll(".ingredient-group");

  groups.forEach((group) => {
    const groupName = group.dataset.group;
    const rows = group.querySelectorAll(".ingredient-row");

    rows.forEach((row, index) => {
      const name = row.querySelector(".ing-name").value.trim();
      if (!name) return;

      ingredients.push({
        name,
        amount: row.querySelector(".ing-amount").value.trim() || null,
        unit: row.querySelector(".ing-unit").value.trim() || null,
        order: index,
        group: groupName,
      });
    });
  });

  return ingredients;
}
