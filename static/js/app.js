/* TalentMatch front-end behaviour. No inline scripts (CSP-friendly). */
(function () {
  "use strict";

  var NAVY = "#173a66", TEAL = "#14b8a6", AMBER = "#f0a53a", SLATE = "#94a3b8", RED = "#e5484d";
  var PALETTE = [TEAL, NAVY, AMBER, SLATE, RED, "#6b8bb5"];

  document.addEventListener("DOMContentLoaded", function () {
    initToasts();
    initConfirm();
    initForms();
    initFileInputs();
    initPasswordToggles();
    initRegister();
    initSkillPreview();
    initAutosubmit();
    initCharts();
    initAiDraft();
    initMisc();
  });

  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function initToasts() {
    $all(".app-toast").forEach(function (el) { new bootstrap.Toast(el).show(); });
  }

  /* ---- Confirmation modal for destructive forms ---- */
  function initConfirm() {
    var modalEl = document.getElementById("confirmModal");
    if (!modalEl) return;
    var modal = new bootstrap.Modal(modalEl);
    var msg = document.getElementById("confirmMessage");
    var ok = document.getElementById("confirmOk");
    var pending = null;

    document.addEventListener("submit", function (e) {
      var form = e.target;
      if (!form.dataset || !form.dataset.confirm || form.dataset.confirmed === "1") return;
      e.preventDefault();
      pending = form;
      msg.textContent = form.dataset.confirm;
      modal.show();
    }, true);

    ok.addEventListener("click", function () {
      if (!pending) return;
      pending.dataset.confirmed = "1";
      var form = pending;
      pending = null;
      modal.hide();
      form.requestSubmit ? form.requestSubmit() : form.submit();
    });
    modalEl.addEventListener("hidden.bs.modal", function () { pending = null; });
  }

  /* ---- Loading states + light client validation (server validates again) ---- */
  function initForms() {
    var bar = document.getElementById("page-progress");
    $all("form[data-loading]").forEach(function (form) {
      form.addEventListener("submit", function (e) {
        if (e.defaultPrevented) return;
        var valid = true;
        $all("[required]", form).forEach(function (field) {
          if (field.type === "file") {
            valid = valid && field.files && field.files.length > 0;
          } else if (!String(field.value || "").trim()) {
            field.classList.add("is-invalid");
            valid = false;
          } else {
            field.classList.remove("is-invalid");
          }
        });
        if (form.id === "register-form") {
          var pw = form.querySelector("#password"), cf = form.querySelector("#confirm_password");
          if (pw && cf && pw.value !== cf.value) { cf.classList.add("is-invalid"); valid = false; }
        }
        if (form.hasAttribute("data-needs-file")) {
          var fi = form.querySelector("input[type=file]");
          var err = form.querySelector("[data-file-error]");
          if (fi && (!fi.files || !fi.files.length)) {
            if (err) { err.textContent = "Please choose a PDF resume first."; err.hidden = false; }
            valid = false;
          }
        }
        if (!valid) { e.preventDefault(); return; }

        $all("button[type=submit]", form).forEach(function (btn) {
          btn.disabled = true;
          var label = btn.querySelector(".btn-label");
          if (label) label.innerHTML = '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>' +
            (form.dataset.loadingText || "Please wait…");
        });
        if (bar) { bar.style.opacity = "1"; bar.style.width = "70%"; }
        if (form.hasAttribute("data-loading-overlay")) {
          var ov = document.getElementById("loading-overlay"), t = document.getElementById("loading-text");
          if (t) t.textContent = form.dataset.loadingText || "Working…";
          if (ov) ov.classList.remove("d-none");
        }
      });
      $all("input,textarea,select", form).forEach(function (f) {
        f.addEventListener("input", function () { f.classList.remove("is-invalid"); });
      });
    });
    // Reset buttons if the page is restored from the back/forward cache.
    window.addEventListener("pageshow", function (ev) {
      if (!ev.persisted) return;
      $all("button[type=submit][disabled]").forEach(function (b) { b.disabled = false; });
      var ov = document.getElementById("loading-overlay"); if (ov) ov.classList.add("d-none");
      if (bar) { bar.style.width = "0"; }
    });
  }

  /* ---- PDF file input: type + size validation, drag and drop ---- */
  function initFileInputs() {
    $all("[data-pdf-input]").forEach(function (input) {
      var zone = input.closest("[data-drop-zone]");
      var label = zone && zone.querySelector("[data-drop-label]");
      var err = input.form && input.form.querySelector("[data-file-error]");
      var maxBytes = (parseFloat(input.dataset.maxMb) || 5) * 1024 * 1024;

      function show(msg) { if (err) { err.textContent = msg; err.hidden = !msg; } }
      function validate() {
        var f = input.files && input.files[0];
        show("");
        if (zone) zone.classList.remove("has-file");
        if (!f) return;
        var isPdf = /\.pdf$/i.test(f.name) && (!f.type || f.type === "application/pdf");
        if (!isPdf) { show("Only PDF files are accepted."); input.value = ""; if (label) label.textContent = "Choose a PDF or drag it here"; return; }
        if (f.size > maxBytes) { show("That file is too large (max " + (maxBytes / 1048576) + " MB)."); input.value = ""; if (label) label.textContent = "Choose a PDF or drag it here"; return; }
        if (label) label.textContent = f.name;
        if (zone) zone.classList.add("has-file");
      }
      input.addEventListener("change", validate);
      if (zone) {
        ["dragenter", "dragover"].forEach(function (n) { zone.addEventListener(n, function (e) { e.preventDefault(); zone.classList.add("dragover"); }); });
        ["dragleave", "drop"].forEach(function (n) { zone.addEventListener(n, function (e) { e.preventDefault(); zone.classList.remove("dragover"); }); });
        zone.addEventListener("drop", function (e) {
          if (e.dataTransfer && e.dataTransfer.files.length) { input.files = e.dataTransfer.files; validate(); }
        });
      }
    });
  }

  function initPasswordToggles() {
    $all("[data-toggle-password]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var input = document.querySelector(btn.dataset.togglePassword);
        if (!input) return;
        var show = input.type === "password";
        input.type = show ? "text" : "password";
        btn.querySelector("i").className = show ? "bi bi-eye-slash" : "bi bi-eye";
        btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
      });
    });
  }

  /* ---- Registration: employer-only field + password strength ---- */
  function initRegister() {
    var form = document.getElementById("register-form");
    if (!form) return;
    var group = document.getElementById("company-group");
    $all("input[name=role]", form).forEach(function (r) {
      r.addEventListener("change", function () { group.classList.toggle("d-none", r.value !== "employer" || !r.checked); });
    });
    var pw = form.querySelector("#password"), bar = document.getElementById("strength-bar"), txt = document.getElementById("strength-text");
    pw.addEventListener("input", function () {
      var v = pw.value, score = 0;
      if (v.length >= 8) score++;
      if (/[A-Za-z]/.test(v) && /\d/.test(v)) score++;
      if (v.length >= 12) score++;
      if (/[^A-Za-z0-9]/.test(v)) score++;
      var colors = ["#e5484d", "#f0a53a", "#f0a53a", TEAL, "#0f9d8e"], words = ["Too weak", "Weak", "Fair", "Good", "Strong"];
      bar.style.width = (v ? (score + 1) * 20 : 0) + "%";
      bar.style.backgroundColor = colors[score];
      txt.textContent = v ? words[score] : "At least 8 characters, with a letter and a number.";
    });
  }

  /* ---- Job form helpers ---- */
  function initSkillPreview() {
    var input = document.querySelector("[data-skill-input]"), box = document.getElementById("skills-preview");
    if (input && box) {
      var render = function () {
        box.textContent = "";
        input.value.split(",").map(function (s) { return s.trim(); }).filter(Boolean).forEach(function (s) {
          var chip = document.createElement("span");
          chip.className = "skill-chip me-1 mb-1";
          chip.textContent = s;
          box.appendChild(chip);
        });
      };
      input.addEventListener("input", render); render();
    }
    var desc = document.getElementById("description"), count = document.getElementById("desc-count");
    if (desc && count) {
      var upd = function () { count.textContent = desc.value.length + " characters"; };
      desc.addEventListener("input", upd); upd();
    }
  }

  function initAutosubmit() {
    $all("[data-autosubmit]").forEach(function (el) {
      el.addEventListener("change", function () { if (el.form) el.form.submit(); });
    });
  }

  /* ---- Charts (data comes from data-chart JSON attributes) ---- */
  function initCharts() {
    if (typeof Chart === "undefined") return;
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    Chart.defaults.color = "#64748b";
    $all("canvas[data-chart]").forEach(function (canvas) {
      var spec;
      try { spec = JSON.parse(canvas.dataset.chart); } catch (e) { return; }
      var isCircle = spec.type === "doughnut";
      var dataset = { label: spec.label || "", data: spec.data };
      if (isCircle) {
        dataset.backgroundColor = PALETTE; dataset.borderWidth = 2; dataset.borderColor = "#fff";
      } else if (spec.type === "line") {
        dataset.borderColor = TEAL; dataset.backgroundColor = "rgba(20,184,166,.15)"; dataset.fill = true; dataset.tension = .35; dataset.pointRadius = 3;
      } else {
        dataset.backgroundColor = TEAL; dataset.borderRadius = 6; dataset.maxBarThickness = 36;
      }
      new Chart(canvas, {
        type: spec.type,
        data: { labels: spec.labels, datasets: [dataset] },
        options: {
          responsive: true, maintainAspectRatio: false,
          plugins: { legend: { display: isCircle, position: "bottom", labels: { boxWidth: 10, usePointStyle: true } } },
          scales: isCircle ? {} : {
            y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: "#eef2f7" } },
            x: { grid: { display: false } }
          }
        }
      });
    });
  }

  /* ---- AI job description draft (server decides AI vs template) ---- */
  function initAiDraft() {
    var btn = document.querySelector("[data-ai-draft]");
    if (!btn) return;
    btn.addEventListener("click", function () {
      var title = document.getElementById("title"), desc = document.getElementById("description"),
          skills = document.getElementById("skills"), level = document.getElementById("experience_level");
      if (!title.value.trim() || title.value.trim().length < 3) {
        title.classList.add("is-invalid"); title.focus(); return;
      }
      if (desc.value.trim().length > 30 && !window.confirm("Replace the current description with a new draft?")) return;
      var label = btn.querySelector(".btn-label"), original = label.innerHTML;
      btn.disabled = true;
      label.innerHTML = '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Writing…';
      var body = new URLSearchParams({ title: title.value, skills: skills.value, experience_level: level ? level.value : "" });
      fetch(btn.dataset.url, {
        method: "POST", credentials: "same-origin",
        headers: { "X-CSRFToken": btn.dataset.csrf, "Content-Type": "application/x-www-form-urlencoded" },
        body: body.toString()
      }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, json: j }; }); })
        .then(function (res) {
          if (!res.ok) { window.alert(res.json.error || "Could not generate a draft."); return; }
          desc.value = res.json.description || desc.value;
          if (!skills.value.trim() && res.json.skills) skills.value = res.json.skills;
          desc.dispatchEvent(new Event("input")); skills.dispatchEvent(new Event("input"));
        })
        .catch(function () { window.alert("Network error while generating the draft."); })
        .then(function () { btn.disabled = false; label.innerHTML = original; });
    });
  }

  function initMisc() {
    $all("[data-history-back]").forEach(function (b) { b.addEventListener("click", function () { history.back(); }); });
  }
})();
