/**
 * Shopping list component — shows a categorized, consolidated ingredient list.
 * Items can be checked off as you shop.
 */
function createShoppingList(selectedIds) {
  const container = document.createElement("div");
  container.className = "shopping-list";

  if (selectedIds.size === 0) {
    container.innerHTML = `
      <div class="empty-state">
        No recipes selected. Go to the Recipes tab and check the recipes
        you're making this week.
      </div>
    `;
    return container;
  }

  container.innerHTML = `
    <div class="shopping-header">
      <h2>Shopping List</h2>
      <p class="text-muted">${selectedIds.size} recipe${selectedIds.size === 1 ? "" : "s"} selected</p>
    </div>
    <div class="shopping-loading">
      <div class="loading-text">Generating shopping list...</div>
    </div>
  `;

  // Fetch the consolidated list from the backend
  loadShoppingList(container, selectedIds);

  return container;
}


async function loadShoppingList(container, selectedIds) {
  try {
    const response = await fetch("/api/recipes/shopping-list", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ recipe_ids: [...selectedIds] }),
    });

    if (!response.ok) throw new Error("Failed to generate list");
    const data = await response.json();

    renderShoppingList(container, data);
  } catch (err) {
    container.innerHTML = `
      <div class="shopping-header">
        <h2>Shopping List</h2>
      </div>
      <p class="text-muted">Could not generate shopping list. ${err.message}</p>
    `;
  }
}


function renderShoppingList(container, data) {
  const categories = data.categories || [];

  if (categories.length === 0) {
    container.innerHTML = `
      <div class="shopping-header">
        <h2>Shopping List</h2>
      </div>
      <p class="text-muted">No ingredients found in the selected recipes.</p>
    `;
    return;
  }

  // Collect all unique recipe names from the ingredients
  const recipeNames = new Set();
  for (const cat of categories) {
    for (const item of cat.items) {
      for (const r of item.from_recipes) {
        recipeNames.add(r);
      }
    }
  }

  const recipeList = [...recipeNames]
    .map((name) => `<li>${escapeHtml(name)}</li>`)
    .join("");

  let html = `
    <div class="shopping-header">
      <h2>Shopping List</h2>
      <div class="shopping-recipes-summary">
        <h4>Recipes this week:</h4>
        <ul>${recipeList}</ul>
      </div>
    </div>
  `;

  for (const cat of categories) {
    const items = cat.items
      .map((item) => {
        const amount = [item.amount, item.unit].filter(Boolean).join(" ");
        const recipes = item.from_recipes.join(", ");
        return `
          <div class="shopping-item">
            <input type="checkbox" class="shop-check">
            <span class="shop-name">${escapeHtml(item.name)}</span>
            ${amount ? `<span class="shop-amount">${escapeHtml(amount)}</span>` : ""}
            <span class="shop-recipes">${escapeHtml(recipes)}</span>
            <button class="shop-remove" title="Remove from list">&times;</button>
          </div>
        `;
      })
      .join("");

    html += `
      <div class="shopping-category">
        <h3 class="shopping-category-header">${escapeHtml(cat.name)}</h3>
        <div class="shopping-items">${items}</div>
      </div>
    `;
  }

  container.innerHTML = html;

  // Checking off items — strike through
  container.querySelectorAll(".shop-check").forEach((cb) => {
    cb.addEventListener("change", () => {
      cb.closest(".shopping-item").classList.toggle("checked", cb.checked);
    });
  });

  // Remove items
  container.querySelectorAll(".shop-remove").forEach((btn) => {
    btn.addEventListener("click", () => {
      const item = btn.closest(".shopping-item");
      const category = item.closest(".shopping-category");
      item.remove();
      // If category is now empty, remove the whole section
      if (category && category.querySelectorAll(".shopping-item").length === 0) {
        category.remove();
      }
    });
  });
}
