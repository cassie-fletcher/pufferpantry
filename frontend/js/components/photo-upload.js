/**
 * Photo upload component for scanning recipes from cookbook photos.
 * Supports drag-and-drop and multi-select. Automatically handles multiple pages.
 */
function createPhotoUpload({ onExtracted, onCancel }) {
  const container = document.createElement("div");
  container.className = "recipe-form photo-upload";

  container.innerHTML = `
    <h2>Scan Recipe from Photo</h2>

    <div class="drop-zone" id="drop-zone">
      <input type="file" accept="image/*" multiple class="hidden" id="photo-input">
      <p class="drop-zone-text">
        Drag &amp; drop photos here, or <button type="button" class="btn-link" id="browse-btn">browse</button>
      </p>
      <p class="drop-zone-hint">Select multiple photos for multi-page recipes</p>
    </div>

    <div class="photo-thumbnails" id="thumbnails"></div>

    <div class="form-actions">
      <button type="button" class="btn btn-primary hidden" id="scan-btn">
        Scan Recipe
      </button>
      <button type="button" class="btn btn-secondary" id="upload-cancel-btn">
        Cancel
      </button>
    </div>

    <div class="loading hidden" id="loading">
      <div class="loading-text">Extracting recipe from photos...</div>
    </div>

    <div class="error-message hidden" id="error-message"></div>
  `;

  const dropZone = container.querySelector("#drop-zone");
  const fileInput = container.querySelector("#photo-input");
  const browseBtn = container.querySelector("#browse-btn");
  const thumbnails = container.querySelector("#thumbnails");
  const scanBtn = container.querySelector("#scan-btn");
  const loading = container.querySelector("#loading");
  const errorMsg = container.querySelector("#error-message");

  let files = [];
  // Index of the FIRST page of the recipe (the one with the title). Only
  // meaningful when there are multiple pages; the starred file is moved to
  // the front of the upload, and the backend treats upload order as page
  // order with the first file as the title page.
  let firstPageIndex = 0;

  // Browse button opens file picker
  browseBtn.addEventListener("click", () => fileInput.click());

  // File input change (browse or additional files)
  fileInput.addEventListener("change", () => {
    addFiles(Array.from(fileInput.files));
    fileInput.value = ""; // Reset so the same files can be re-selected
  });

  // Drag and drop
  dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropZone.classList.add("drag-over");
  });

  dropZone.addEventListener("dragleave", () => {
    dropZone.classList.remove("drag-over");
  });

  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("drag-over");
    const droppedFiles = Array.from(e.dataTransfer.files).filter((f) =>
      f.type.startsWith("image/")
    );
    addFiles(droppedFiles);
  });

  function addFiles(newFiles) {
    for (const file of newFiles) {
      files.push(file);
    }
    renderThumbnails();
    scanBtn.classList.toggle("hidden", files.length === 0);
  }

  function renderThumbnails() {
    thumbnails.innerHTML = "";
    files.forEach((file, index) => {
      const thumb = document.createElement("div");
      thumb.className = "photo-thumb";

      const img = document.createElement("img");
      img.src = URL.createObjectURL(file);
      img.alt = `Page ${index + 1}`;

      const label = document.createElement("span");
      label.className = "thumb-label";
      label.textContent =
        files.length > 1 && index === firstPageIndex
          ? `Page ${index + 1} \u2014 first`
          : `Page ${index + 1}`;

      const removeBtn = document.createElement("button");
      removeBtn.className = "thumb-remove";
      removeBtn.textContent = "\u00d7";
      removeBtn.addEventListener("click", () => {
        files.splice(index, 1);
        // Keep the star pointing at the same file after removal.
        if (index === firstPageIndex) firstPageIndex = 0;
        else if (index < firstPageIndex) firstPageIndex -= 1;
        renderThumbnails();
        scanBtn.classList.toggle("hidden", files.length === 0);
      });

      thumb.appendChild(img);
      thumb.appendChild(label);
      thumb.appendChild(removeBtn);

      // Star = "this is the first page (has the title)". Only shown when the
      // recipe spans multiple photos; with one photo there is nothing to pick.
      if (files.length > 1) {
        const starBtn = document.createElement("button");
        starBtn.className =
          "thumb-star" + (index === firstPageIndex ? " active" : "");
        starBtn.textContent = index === firstPageIndex ? "\u2605" : "\u2606";
        starBtn.title = "Mark as the first page (the one with the title)";
        starBtn.addEventListener("click", () => {
          firstPageIndex = index;
          renderThumbnails();
        });
        thumb.appendChild(starBtn);
      }

      thumbnails.appendChild(thumb);
    });
  }

  // Scan
  scanBtn.addEventListener("click", async () => {
    if (files.length === 0) return;

    scanBtn.classList.add("hidden");
    dropZone.classList.add("hidden");
    thumbnails.classList.add("hidden");
    loading.classList.remove("hidden");
    errorMsg.classList.add("hidden");

    try {
      const formData = new FormData();
      // Starred page first; the rest keep their relative order. The backend
      // treats upload order as page order, first file = title page.
      const ordered = [
        files[firstPageIndex],
        ...files.filter((_, i) => i !== firstPageIndex),
      ];
      for (const file of ordered) {
        formData.append("photos", file);
      }

      const response = await fetch("/api/recipes/extract-from-photo", {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || "Failed to extract recipe");
      }

      const data = await response.json();
      onExtracted(data);
    } catch (err) {
      loading.classList.add("hidden");
      scanBtn.classList.remove("hidden");
      dropZone.classList.remove("hidden");
      thumbnails.classList.remove("hidden");
      errorMsg.textContent = err.message;
      errorMsg.classList.remove("hidden");
    }
  });

  container.querySelector("#upload-cancel-btn").addEventListener("click", onCancel);

  return container;
}
