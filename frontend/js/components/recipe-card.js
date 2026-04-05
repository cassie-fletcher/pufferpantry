/**
 * Creates a recipe card DOM element from a recipe object.
 * Keeps rendering logic separate from data-fetching logic.
 */
function createRecipeCard(recipe, { onView, onEdit, onDelete, onUploadDishPhoto, onToggleSelect, isSelected }) {
  const card = document.createElement("div");
  card.className = "recipe-card" + (isSelected ? " selected" : "");

  const calories = recipe.calories_per_serving
    ? `${recipe.calories_per_serving} cal`
    : "";

  const date = new Date(recipe.created_at).toLocaleDateString();

  const ratingParts = [];
  if (recipe.rating_cassie) ratingParts.push(`Cassie: ${recipe.rating_cassie}`);
  if (recipe.rating_chris) ratingParts.push(`Chris: ${recipe.rating_chris}`);
  const ratingsText = ratingParts.length ? ratingParts.join(" &middot; ") : "";

  // Photo section: show dish photo, or a placeholder with upload option
  let photoHtml;
  if (recipe.dish_photo_filename) {
    photoHtml = `
      <div class="card-photo card-photo-clickable" title="Click to change photo">
        <img src="/photos/${escapeHtml(recipe.dish_photo_filename)}" alt="${escapeHtml(recipe.title)}"
             style="object-position: ${recipe.dish_photo_position || 'center'}">
        <div class="card-photo-overlay">Change Photo</div>
      </div>
    `;
  } else {
    photoHtml = `
      <div class="card-photo card-photo-placeholder">
        <button class="btn btn-secondary btn-small upload-photo-btn">Add Photo</button>
      </div>
    `;
  }

  card.innerHTML = `
    <label class="card-select" title="Select for shopping list">
      <input type="checkbox" class="select-checkbox" ${isSelected ? "checked" : ""}>
    </label>
    ${photoHtml}
    <div class="card-body">
      <h3>${escapeHtml(recipe.title)}</h3>
      <div class="card-badges">
        <span class="badge">${escapeHtml(recipe.meal_type)}</span>
        ${recipe.protein_type ? `<span class="badge badge-secondary">${escapeHtml(recipe.protein_type)}</span>` : ""}
        ${recipe.cuisine ? `<span class="badge badge-secondary">${escapeHtml(recipe.cuisine)}</span>` : ""}
      </div>
      <div class="meta">
        ${calories ? calories + " per serving &middot; " : ""}added ${date}
        ${ratingsText ? `<br>${ratingsText}` : ""}
      </div>
      <div class="actions">
        <button class="btn btn-secondary btn-small view-btn">View</button>
        <button class="btn btn-secondary btn-small edit-btn">Edit</button>
        <button class="btn btn-danger btn-small delete-btn">Delete</button>
      </div>
    </div>
  `;

  card.querySelector(".select-checkbox").addEventListener("change", (e) => {
    card.classList.toggle("selected", e.target.checked);
    onToggleSelect(recipe.id, e.target.checked);
  });

  card.querySelector(".view-btn").addEventListener("click", () => onView(recipe.id));
  card.querySelector(".edit-btn").addEventListener("click", () => onEdit(recipe.id));
  card.querySelector(".delete-btn").addEventListener("click", () => onDelete(recipe.id));

  // Dish photo upload via hidden file input — works for both "Add Photo" and "Change Photo"
  const fileInput = document.createElement("input");
  fileInput.type = "file";
  fileInput.accept = "image/*";
  fileInput.className = "hidden";
  card.appendChild(fileInput);

  fileInput.addEventListener("change", () => {
    if (fileInput.files[0]) {
      onUploadDishPhoto(recipe.id, fileInput.files[0]);
    }
  });

  const uploadBtn = card.querySelector(".upload-photo-btn");
  if (uploadBtn) {
    uploadBtn.addEventListener("click", () => fileInput.click());
  }

  const clickablePhoto = card.querySelector(".card-photo-clickable");
  if (clickablePhoto) {
    clickablePhoto.addEventListener("click", () => fileInput.click());
  }

  return card;
}


/** Prevent XSS by escaping HTML special characters. */
function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}
