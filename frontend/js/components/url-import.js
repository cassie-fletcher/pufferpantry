/**
 * URL import component — paste a recipe URL and extract via Claude.
 * Flow: enter URL → click "Import" → loading → calls onExtracted(data).
 */
function createUrlImport({ onExtracted, onCancel }) {
  const container = document.createElement("div");
  container.className = "recipe-form url-import";

  container.innerHTML = `
    <h2>Import Recipe from URL</h2>
    <p class="upload-hint">Paste a link from a recipe site (Half Baked Harvest, Skinnytaste, etc.)</p>

    <div class="form-group">
      <label for="recipe-url">Recipe URL</label>
      <input type="url" id="recipe-url" placeholder="https://www.halfbakedharvest.com/..." required>
    </div>

    <div class="form-actions">
      <button type="button" class="btn btn-primary" id="import-btn">Import Recipe</button>
      <button type="button" class="btn btn-secondary" id="import-cancel-btn">Cancel</button>
    </div>

    <div class="loading hidden" id="loading">
      <div class="loading-text">Fetching and extracting recipe...</div>
    </div>

    <div class="error-message hidden" id="error-message"></div>
  `;

  const urlInput = container.querySelector("#recipe-url");
  const importBtn = container.querySelector("#import-btn");
  const loading = container.querySelector("#loading");
  const errorMsg = container.querySelector("#error-message");

  importBtn.addEventListener("click", async () => {
    const url = urlInput.value.trim();
    if (!url) {
      urlInput.focus();
      return;
    }

    importBtn.classList.add("hidden");
    loading.classList.remove("hidden");
    errorMsg.classList.add("hidden");

    try {
      const response = await fetch("/api/recipes/extract-from-url", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || "Failed to import recipe");
      }

      const data = await response.json();
      onExtracted(data);
    } catch (err) {
      loading.classList.add("hidden");
      importBtn.classList.remove("hidden");
      errorMsg.textContent = err.message;
      errorMsg.classList.remove("hidden");
    }
  });

  // Allow Enter key to submit
  urlInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") importBtn.click();
  });

  container.querySelector("#import-cancel-btn").addEventListener("click", onCancel);

  // Auto-focus the URL input
  setTimeout(() => urlInput.focus(), 50);

  return container;
}
