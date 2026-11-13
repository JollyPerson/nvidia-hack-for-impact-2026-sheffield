// Create a study: ISRCTN-style details, a free-text helper that drafts criteria, and an editable criteria list.
(function () {
  const FIELDS = [
    ["title", "Scientific title", "input", "The full title, as on the protocol.", true],
    ["short_title", "Short title or acronym", "input", "For example, PRISM-CAP."],
    ["condition", "Condition studied", "input", "", true],
    ["study_design", "Study design", "select", "", true, ["Randomised controlled trial", "Observational cohort", "Case-control", "Feasibility study"]],
    ["phase", "Phase", "select", "", false, ["Not applicable", "Phase 1", "Phase 2", "Phase 3", "Phase 4"]],
    ["intervention", "Intervention", "textarea", "", true],
    ["primary_outcome", "Primary outcome measure", "textarea", "", true],
    ["target_sample_size", "Target number of participants", "number", "", true],
    ["recruitment_start", "Recruitment start date", "date", "", true],
    ["recruitment_end", "Recruitment end date", "date", "", true],
    ["chief_investigator", "Chief investigator", "input", "", true],
    ["sponsor", "Sponsor", "input", "", true],
    ["ethics_reference", "Ethics reference (REC or IRAS)", "input", "", true],
    ["status", "Status", "select", "", true, [["recruiting", "Recruiting"], ["closed", "Closed"]]],
  ];
  const STRUCT_FIELDS = [["age", "Age (years)"], ["curb65", "CURB-65 score"], ["news2", "NEWS2 score"], ["egfr", "eGFR (mL/min/1.73m²)"], ["crp", "CRP (mg/L)"], ["spo2", "SpO2 (%)"]];
  const OPS = [[">=", "at least (≥)"], ["<=", "at most (≤)"], [">", "more than (>)"], ["<", "less than (<)"], ["==", "equal to (=)"], ["!=", "not equal to (≠)"]];

  function field([name, label, kind, hint, required, options]) {
    const id = "f-" + name, h = hint ? `<div class="nhsuk-hint" id="${id}-hint">${A.esc(hint)}</div>` : "";
    const desc = hint ? ` aria-describedby="${id}-hint"` : "";
    let control;
    if (kind === "textarea") control = `<textarea class="nhsuk-textarea" id="${id}" name="${name}" rows="3"${desc}></textarea>`;
    else if (kind === "select") control = `<select class="nhsuk-select" id="${id}" name="${name}">${options.map(o => Array.isArray(o) ? `<option value="${A.esc(o[0])}">${A.esc(o[1])}</option>` : `<option>${A.esc(o)}</option>`).join("")}</select>`;
    else {
      const type = kind === "number" ? "number" : kind === "date" ? "date" : "text";
      const cls = kind === "number" ? "nhsuk-input nhsuk-input--width-5" : kind === "date" ? "nhsuk-input nhsuk-input--width-10" : "nhsuk-input";
      control = `<input class="${cls}" id="${id}" name="${name}" type="${type}"${kind === "number" ? ' inputmode="numeric" min="1"' : ""}${desc}>`;
    }
    return `<div class="nhsuk-form-group" data-group="${name}"><label class="nhsuk-label" for="${id}">${A.esc(label)}${required ? "" : " (optional)"}</label>${h}<span class="nhsuk-error-message adv-hidden" id="${id}-error"></span>${control}</div>`;
  }
  document.getElementById("details").innerHTML = FIELDS.map(field).join("");

  // ---------- Criteria rows ----------
  const list = document.getElementById("criteria");
  let seq = 0;
  function row(c = { type: "inclusion", kind: "notes", description: "" }) {
    const n = ++seq, div = document.createElement("div");
    div.className = "adv-row nhsuk-card"; div.dataset.row = n;
    const opt = (vals, cur) => vals.map(([v, l]) => `<option value="${v}"${String(v) === String(cur ?? "") ? " selected" : ""}>${A.esc(l)}</option>`).join("");
    div.innerHTML = `<div class="nhsuk-card__content">
      <h3 class="nhsuk-heading-s adv-row-title">Criterion</h3>
      <div class="adv-row-grid">
        <div class="nhsuk-form-group"><label class="nhsuk-label" for="c${n}-type">Type</label>
          <select class="nhsuk-select" id="c${n}-type" data-k="type">${opt([["inclusion", "Inclusion"], ["exclusion", "Exclusion"]], c.type)}</select></div>
        <div class="nhsuk-form-group"><label class="nhsuk-label" for="c${n}-kind">How it is checked</label>
          <select class="nhsuk-select" id="c${n}-kind" data-k="kind">${opt([["structured", "Record check (SQL)"], ["notes", "Clinical notes (AI)"]], c.kind)}</select></div>
      </div>
      <div class="nhsuk-form-group" data-group="c${n}"><label class="nhsuk-label" for="c${n}-desc">Description</label>
        <span class="nhsuk-error-message adv-hidden" id="c${n}-desc-error"></span>
        <input class="nhsuk-input" id="c${n}-desc" data-k="description" type="text" value="${A.esc(c.description)}"></div>
      <div class="adv-structured${c.kind === "structured" ? "" : " adv-hidden"}">
        <div class="nhsuk-form-group"><label class="nhsuk-label" for="c${n}-field">Field</label>
          <select class="nhsuk-select" id="c${n}-field" data-k="field">${opt(STRUCT_FIELDS, c.field)}</select></div>
        <div class="nhsuk-form-group"><label class="nhsuk-label" for="c${n}-op">Rule</label>
          <select class="nhsuk-select" id="c${n}-op" data-k="operator">${opt(OPS, c.operator || ">=")}</select></div>
        <div class="nhsuk-form-group"><label class="nhsuk-label" for="c${n}-val">Value</label>
          <input class="nhsuk-input nhsuk-input--width-5" id="c${n}-val" data-k="value" type="number" step="any" value="${A.esc(c.value ?? "")}"></div>
        <div class="nhsuk-form-group"><label class="nhsuk-label" for="c${n}-win">Within days (optional)</label>
          <input class="nhsuk-input nhsuk-input--width-4" id="c${n}-win" data-k="time_window_days" type="number" min="1" value="${A.esc(c.time_window_days ?? "")}"></div>
      </div>
      <button class="nhsuk-button nhsuk-button--secondary nhsuk-button--small adv-remove" type="button">Remove<span class="nhsuk-u-visually-hidden"> this criterion</span></button>
    </div>`;
    div.querySelector('[data-k="kind"]').addEventListener("change", e =>
      div.querySelector(".adv-structured").classList.toggle("adv-hidden", e.target.value !== "structured"));
    div.querySelector(".adv-remove").addEventListener("click", () => { div.remove(); renumber(); });
    list.appendChild(div); renumber();
  }
  function renumber() {
    [...list.children].forEach((d, i) => d.querySelector(".adv-row-title").textContent = `Criterion ${i + 1}`);
  }
  function readRows() {
    return [...list.children].map(d => {
      const g = k => d.querySelector(`[data-k="${k}"]`).value;
      const c = { type: g("type"), kind: g("kind"), description: g("description").trim() };
      if (c.kind === "structured") {
        c.field = g("field"); c.operator = g("operator");
        c.value = g("value") === "" ? null : Number(g("value"));
        c.time_window_days = g("time_window_days") === "" ? null : Number(g("time_window_days"));
      }
      return c;
    });
  }
  document.getElementById("add-criterion").addEventListener("click", () => row());

  // ---------- Free text -> draft criteria ----------
  const describe = document.getElementById("describe");
  const showHelper = () => {
    const on = !!A.F.draft_criteria;
    document.getElementById("draft-helper").classList.toggle("adv-hidden", !on);
    if (on && !describe.placeholder) A.api("/api/criteria/example").then(r => { describe.placeholder = r.text; }).catch(() => {});
  };
  A.onFeaturesChanged = showHelper;
  A.features().then(() => { showHelper(); A.devPanel(); });
  document.getElementById("draft").addEventListener("click", async e => {
    const btn = e.currentTarget, text = describe.value.trim() || describe.placeholder;
    if (!describe.value.trim()) describe.value = text;
    btn.disabled = true; btn.textContent = "Drafting criteria…";
    try {
      const r = await A.api("/api/criteria/draft", { method: "POST", body: JSON.stringify({ text }) });
      list.innerHTML = ""; seq = 0; r.criteria.forEach(row);
      document.getElementById("assumptions").innerHTML = r.assumptions.length ? A.warning("Check these assumptions",
        `<p>Advolare drafted ${r.criteria.length} criteria. It made these assumptions, so check them before you save:</p>
         <ul>${r.assumptions.map(a => `<li>${A.esc(a)}</li>`).join("")}</ul>`) :
        A.inset(`<p>Advolare drafted ${r.criteria.length} criteria. Check each one before you save.</p>`);
      list.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      document.getElementById("assumptions").innerHTML = A.errorSummary([{ id: "describe", text: "Criteria could not be drafted. Add them by hand below." }]);
    } finally { btn.disabled = false; btn.textContent = "Draft criteria from this description"; }
  });

  // ---------- Validate and save ----------
  function showErrors(errs) {
    document.querySelectorAll(".nhsuk-form-group--error").forEach(g => g.classList.remove("nhsuk-form-group--error"));
    document.querySelectorAll(".nhsuk-error-message").forEach(m => m.classList.add("adv-hidden"));
    document.querySelectorAll(".nhsuk-input--error,.nhsuk-textarea--error").forEach(m => m.classList.remove("nhsuk-input--error", "nhsuk-textarea--error"));
    const box = document.getElementById("errors");
    if (!errs.length) { box.innerHTML = ""; return; }
    errs.forEach(({ id, text }) => {
      const input = document.getElementById(id), msg = document.getElementById(id + "-error");
      if (!input) return;
      input.closest(".nhsuk-form-group").classList.add("nhsuk-form-group--error");
      input.classList.add(input.tagName === "TEXTAREA" ? "nhsuk-textarea--error" : "nhsuk-input--error");
      if (msg) { msg.innerHTML = `<span class="nhsuk-u-visually-hidden">Error:</span> ${A.esc(text)}`; msg.classList.remove("adv-hidden"); }
    });
    box.innerHTML = A.errorSummary(errs);
    box.firstElementChild.focus();
  }
  document.getElementById("study-form").addEventListener("submit", async e => {
    e.preventDefault();
    const v = n => document.getElementById("f-" + n).value.trim();
    const errs = [];
    if (!v("title")) errs.push({ id: "f-title", text: "Enter the scientific title" });
    if (!v("condition")) errs.push({ id: "f-condition", text: "Enter the condition studied" });
    const need = { study_design: "Select the study design", intervention: "Enter the intervention",
      primary_outcome: "Enter the primary outcome measure", recruitment_start: "Enter the recruitment start date",
      recruitment_end: "Enter the recruitment end date", chief_investigator: "Enter the chief investigator",
      sponsor: "Enter the sponsor", ethics_reference: "Enter the ethics reference" };
    Object.entries(need).forEach(([n, text]) => { if (!v(n)) errs.push({ id: "f-" + n, text }); });
    if (!(Number(v("target_sample_size")) >= 1)) errs.push({ id: "f-target_sample_size", text: "Target number of participants must be 1 or more" });
    if (v("recruitment_start") && v("recruitment_end") && v("recruitment_end") < v("recruitment_start"))
      errs.push({ id: "f-recruitment_end", text: "Recruitment end date must be after the start date" });
    const criteria = readRows();
    if (!criteria.length) errs.push({ id: "describe", text: "Add at least one eligibility criterion" });
    [...list.children].forEach((d, i) => {
      if (criteria[i].kind === "structured" && criteria[i].value === null)
        errs.push({ id: `c${d.dataset.row}-val`, text: `Enter a value for criterion ${i + 1}` });
      if (!criteria[i].description) errs.push({ id: `c${d.dataset.row}-desc`, text: `Enter a description for criterion ${i + 1}` });
    });
    showErrors(errs);
    if (errs.length) return;
    const body = {};
    FIELDS.forEach(([n]) => body[n] = v(n) || null);
    body.target_sample_size = Number(body.target_sample_size);
    if (body.phase === "Not applicable") body.phase = null;
    body.criteria = criteria;
    try {
      const study = await A.api("/api/studies", { method: "POST", body: JSON.stringify(body) });
      window.location.href = `/studies/${encodeURIComponent(study.id)}?created=1`;
    } catch (err) {
      const detail = err.body && Array.isArray(err.body.detail) ? err.body.detail.map(d => {
        const f = d.loc && d.loc[1];
        return { id: f && typeof f === "string" ? "f-" + f : "f-title", text: d.msg.replace(/^Value error, /, "") };
      }) : [{ id: "f-title", text: err.message || "The study could not be saved. Check the details and try again." }];
      showErrors(detail);
    }
  });
})();
