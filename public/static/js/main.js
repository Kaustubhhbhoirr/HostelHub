/*
 * main.js — small UI behaviours used on every page.
 *
 *  1. Auto-hide success/info flash messages
 *  2. Confirmation dialog for forms/buttons marked with data-confirm="..."
 *  3. Loading spinner on submit buttons (prevents double submission)
 *  4. Clickable table rows (data-href)
 *  5. Bootstrap tooltips
 *
 * JavaScript here only improves the experience. All real checks
 * (permissions, validation) still happen in Flask on the server.
 */

document.addEventListener("DOMContentLoaded", function () {
  // ---------- 1. Auto-hide flash messages after 5 seconds ----------
  document.querySelectorAll(".alert[data-autohide]").forEach(function (alertBox) {
    setTimeout(function () {
      bootstrap.Alert.getOrCreateInstance(alertBox).close();
    }, 5000);
  });

  // ---------- 2 + 3. Confirmation dialog and loading state ----------
  const confirmModalElement = document.getElementById("confirmModal");
  const confirmModal = new bootstrap.Modal(confirmModalElement);
  const confirmMessage = confirmModalElement.querySelector("[data-confirm-message]");
  const confirmYesButton = confirmModalElement.querySelector("[data-confirm-yes]");
  let waitingForm = null;
  let waitingSubmitter = null;

  document.addEventListener("submit", function (event) {
    const form = event.target;
    const submitter = event.submitter; // the button that was clicked
    const message = (submitter && submitter.dataset.confirm) || form.dataset.confirm;

    // Needs confirmation and not confirmed yet -> stop and show the dialog.
    if (message && form.dataset.confirmed !== "yes") {
      event.preventDefault();
      waitingForm = form;
      waitingSubmitter = submitter;
      confirmMessage.textContent = message;
      confirmYesButton.textContent =
        (submitter && submitter.dataset.confirmButton) || form.dataset.confirmButton || "Confirm";
      confirmModal.show();
      return;
    }

    // The form is really being sent: show a spinner on the clicked button.
    const button = submitter || form.querySelector("[type=submit]");
    if (button && !form.hasAttribute("data-no-loading")) {
      // Disable on the next tick, otherwise the button's name/value would
      // not be included in the submitted form data.
      setTimeout(function () {
        button.disabled = true;
        button.dataset.originalHtml = button.innerHTML;
        button.innerHTML = '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Please wait…';
      }, 0);
    }
  });

  confirmYesButton.addEventListener("click", function () {
    if (!waitingForm) return;
    waitingForm.dataset.confirmed = "yes";
    confirmModal.hide();
    // requestSubmit() runs the browser's built-in validation and keeps the clicked button.
    waitingForm.requestSubmit(waitingSubmitter || undefined);
    waitingForm.dataset.confirmed = "";
  });

  // ---------- 4. Clickable table rows ----------
  document.querySelectorAll("tr[data-href]").forEach(function (row) {
    row.classList.add("row-link");
    row.addEventListener("click", function (event) {
      // Let real links, buttons and form controls inside the row work normally.
      if (event.target.closest("a, button, input, select, form")) return;
      window.location.href = row.dataset.href;
    });
  });

  // ---------- 5. Tooltips ----------
  document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(function (element) {
    new bootstrap.Tooltip(element);
  });
});

// When the user comes "back" to a page, re-enable buttons we disabled.
window.addEventListener("pageshow", function () {
  document.querySelectorAll("button[data-original-html]").forEach(function (button) {
    button.disabled = false;
    button.innerHTML = button.dataset.originalHtml;
    delete button.dataset.originalHtml;
  });
});
