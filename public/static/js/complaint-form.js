/*
 * complaint-form.js — Image preview, drag & drop and quick checks
 * on the "Report a maintenance issue" form.
 *
 * These checks are only for a nicer experience. Flask validates the
 * category, description and image type again on the server.
 */

document.addEventListener("DOMContentLoaded", function () {
  const form = document.getElementById("complaintForm");
  if (!form) return;

  const MAX_SIZE = 4 * 1024 * 1024; // 4 MB, same limit as MAX_CONTENT_LENGTH in config.py
  const ALLOWED_TYPES = ["image/png", "image/jpeg", "image/webp", "image/gif"];

  const fileInput = document.getElementById("image");
  const uploadZone = document.getElementById("uploadZone");
  const preview = document.getElementById("uploadPreview");
  const previewImage = document.getElementById("previewImage");
  const imageError = document.getElementById("imageError");
  const description = document.getElementById("description");
  const charCount = document.getElementById("charCount");

  function showImageError(message) {
    imageError.textContent = message;
    imageError.classList.toggle("d-none", !message);
  }

  function clearImage() {
    fileInput.value = "";
    previewImage.removeAttribute("src");
    preview.classList.add("d-none");
    uploadZone.classList.remove("d-none");
  }

  // Show a preview of the chosen image before it is uploaded.
  function handleFile(file) {
    showImageError("");
    if (!file) return clearImage();

    if (!ALLOWED_TYPES.includes(file.type)) {
      clearImage();
      return showImageError("Please choose a PNG, JPG, WEBP or GIF image.");
    }
    if (file.size > MAX_SIZE) {
      clearImage();
      return showImageError("This image is larger than 4 MB. Please choose a smaller one.");
    }

    // FileReader turns the local file into a data URL the <img> can display.
    const reader = new FileReader();
    reader.onload = function (event) {
      previewImage.src = event.target.result;
      preview.classList.remove("d-none");
      uploadZone.classList.add("d-none");
    };
    reader.readAsDataURL(file);
  }

  fileInput.addEventListener("change", function () {
    handleFile(fileInput.files[0]);
  });
  document.getElementById("removeImage").addEventListener("click", clearImage);

  // Drag and drop onto the upload zone.
  ["dragenter", "dragover"].forEach(function (name) {
    uploadZone.addEventListener(name, function (event) {
      event.preventDefault();
      uploadZone.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach(function (name) {
    uploadZone.addEventListener(name, function () {
      uploadZone.classList.remove("dragover");
    });
  });
  uploadZone.addEventListener("drop", function (event) {
    event.preventDefault();
    if (event.dataTransfer.files.length) {
      fileInput.files = event.dataTransfer.files; // put the dropped file into the real input
      handleFile(fileInput.files[0]);
    }
  });

  // Live character counter.
  description.addEventListener("input", function () {
    charCount.textContent = description.value.length;
  });

  // Quick checks before sending.
  form.addEventListener("submit", function (event) {
    const categoryChosen = form.querySelector('input[name="category"]:checked');
    const descriptionOk = description.value.trim().length >= 15;

    document.getElementById("categoryError").classList.toggle("d-none", !!categoryChosen);
    document.getElementById("descriptionError").classList.toggle("d-none", descriptionOk);
    description.classList.toggle("is-invalid", !descriptionOk);

    if (!categoryChosen || !descriptionOk) {
      event.preventDefault();
      event.stopImmediatePropagation(); // stop main.js from showing the loading spinner
      (categoryChosen ? description : form.querySelector('input[name="category"]')).focus();
    }
  });
});
