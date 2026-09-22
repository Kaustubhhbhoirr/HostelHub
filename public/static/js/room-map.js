/*
 * room-map.js — Interaction for the warden's visual allocation map.
 *
 * The bed data (status, occupant, etc.) is written into data-* attributes
 * by the Jinja template from the DATABASE. This script only reads those
 * attributes to fill the modal and to filter rooms. It never decides
 * occupancy itself, and every action is a normal form POST to Flask.
 */

document.addEventListener("DOMContentLoaded", function () {
  const grid = document.getElementById("roomGrid");
  if (!grid) return;

  const modalElement = document.getElementById("bedModal");
  const modal = new bootstrap.Modal(modalElement);

  // Helper to find an element inside the modal by its data-field name.
  function field(name) {
    return modalElement.querySelector('[data-field="' + name + '"]');
  }

  // URL templates look like "/warden/rooms/beds/0/allocate"; swap 0 for the real bed id.
  function urlFor(template, bedId) {
    return template.replace("/0/", "/" + bedId + "/");
  }

  // Text and colours for each bed status (an object used like a Python dictionary).
  const STATUS_INFO = {
    available:   { title: "Available", text: "This bed is free and can be allocated.", icon: "bi-check-circle-fill", tone: "tone-green" },
    occupied:    { title: "Occupied", text: "A student is staying in this bed.", icon: "bi-person-fill", tone: "tone-red" },
    reserved:    { title: "Reserved", text: "Held for a pending room change request.", icon: "bi-hourglass-split", tone: "tone-amber" },
    maintenance: { title: "Under maintenance", text: "This bed cannot be allocated until it is repaired.", icon: "bi-wrench", tone: "tone-blue" },
    unavailable: { title: "Unavailable", text: "This bed is closed and cannot be used.", icon: "bi-slash-circle", tone: "tone-slate" },
  };

  function initials(name) {
    return name.split(" ").slice(0, 2).map(function (word) { return word[0]; }).join("").toUpperCase();
  }

  // ---------- Open the modal when a bed is clicked ----------
  grid.addEventListener("click", function (event) {
    const bed = event.target.closest("button.bed");
    if (!bed) return;
    const data = bed.dataset;
    const info = STATUS_INFO[data.status] || STATUS_INFO.unavailable;

    modalElement.querySelector(".modal-title").textContent = "Room " + data.room + " · Bed " + data.bedNumber;
    field("subtitle").textContent = data.roomStatus === "active" ? "Room is active" : "Room is " + data.roomStatus;
    field("statusBox").className = "modal-bed-status " + info.tone;
    field("statusIcon").className = "bi fs-4 " + info.icon;
    field("statusTitle").textContent = info.title;
    field("statusText").textContent = info.text;

    // Show only the section that matches this bed's status.
    modalElement.querySelectorAll("[data-section]").forEach(function (section) {
      section.classList.toggle("d-none", section.dataset.section !== data.status);
    });

    if (data.status === "occupied") {
      field("studentInitials").textContent = initials(data.studentName);
      field("studentName").textContent = data.studentName;
      field("studentMeta").textContent = data.studentRoll + " · " + data.studentDept + " · since " + data.studentSince;
      field("studentLink").href = data.studentUrl;
      modalElement.querySelector('[data-form="vacate"]').action = urlFor(grid.dataset.vacateUrl, data.bedId);
    }

    if (data.status === "reserved") {
      field("reservedFor").textContent = data.reservedFor || "a student";
    }

    const allocateForm = modalElement.querySelector('[data-form="allocate"]');
    if (allocateForm) {
      allocateForm.action = urlFor(grid.dataset.allocateUrl, data.bedId);
      // Coming from "Allocate a bed" on a student page: pre-select that student.
      if (grid.dataset.selectedStudent) {
        allocateForm.querySelector("select").value = grid.dataset.selectedStudent;
      }
    }

    // Manual status buttons only make sense for free beds, and never for the current status.
    const statusForm = modalElement.querySelector('[data-form="status"]');
    const canChangeStatus = ["available", "maintenance", "unavailable"].includes(data.status);
    statusForm.classList.toggle("d-none", !canChangeStatus);
    statusForm.action = urlFor(grid.dataset.statusUrl, data.bedId);
    statusForm.querySelectorAll("[data-status-button]").forEach(function (button) {
      const target = button.dataset.statusButton;
      const blocked = target === data.status || (target === "available" && data.roomStatus !== "active");
      button.classList.toggle("d-none", blocked);
    });

    modal.show();
  });

  // ---------- Live search: dim rooms that do not match ----------
  const searchInput = document.getElementById("mapSearch");
  const noResults = document.getElementById("mapNoResults");
  const roomCards = grid.querySelectorAll(".room-card");

  searchInput.addEventListener("input", function () {
    const term = searchInput.value.trim().toLowerCase();
    let visible = 0;
    roomCards.forEach(function (card) {
      const matches = term === "" || card.dataset.search.includes(term);
      card.classList.toggle("d-none", !matches);
      if (matches) visible++;
    });
    noResults.classList.toggle("d-none", visible > 0);
  });

  // When allocating for a specific student, gently highlight free beds.
  if (grid.dataset.selectedStudent) {
    grid.querySelectorAll('button.bed[data-status="available"]').forEach(function (bed) {
      bed.classList.add("bed-selected");
    });
  }
});
