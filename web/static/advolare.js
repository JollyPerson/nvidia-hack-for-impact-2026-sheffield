// Shared helpers for building NHS.UK frontend components from API data. No framework.
const A = {
  esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  },
  tag(text, colour) {
    return `<strong class="nhsuk-tag${colour ? " nhsuk-tag--" + colour : ""}">${A.esc(text)}</strong>`;
  },
  async api(path, opts = {}) {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
    const body = res.headers.get("content-type")?.includes("json") ? await res.json() : null;
    if (!res.ok) {
      const err = new Error((body && (typeof body.detail === "string" ? body.detail : "Check the form and try again")) || res.statusText);
      err.status = res.status; err.body = body; throw err;
    }
    return body;
  },
  date(iso) {
    if (!iso) return "Not set";
    const d = new Date(iso + "T00:00:00");
    return d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
  },
  dateTime(iso) {
    const d = new Date(iso);
    return d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" }) + ", " +
      d.toLocaleTimeString("en-GB", { hour: "numeric", minute: "2-digit", hour12: true }).replace(" ", "");
  },
  studyStatus(s) { return s === "recruiting" ? A.tag("Recruiting", "green") : A.tag("Closed", "grey"); },
  patientStatus(s) {
    return { likely_eligible: A.tag("Likely eligible", "green"), needs_review: A.tag("Needs review", "yellow"), excluded: A.tag("Excluded", "red") }[s];
  },
  verdict(v) {
    return { met: A.tag("Met", "green"), not_met: A.tag("Not met", "grey"), insufficient: A.tag("Not enough information", "yellow") }[v];
  },
  typeTag(t) { return t === "inclusion" ? A.tag("Inclusion", "blue") : A.tag("Exclusion", "red"); },
  kindTag(k) { return k === "structured" ? A.tag("Record check (SQL)", "purple") : A.tag("Clinical notes (AI)", "white"); },
  rule(c) {
    if (c.kind !== "structured") return "";
    const names = { age: "Age", curb65: "CURB-65", news2: "NEWS2", egfr: "eGFR", crp: "CRP", spo2: "SpO2" };
    const ops = { ">=": "≥", "<=": "≤", "==": "=", "!=": "≠", ">": ">", "<": "<" };
    return `${names[c.field] || c.field} ${ops[c.operator] || c.operator} ${c.value}` + (c.time_window_days ? ` in the last ${c.time_window_days} days` : "");
  },
  summaryList(rows) {
    return `<dl class="nhsuk-summary-list">${rows.map(([k, v]) =>
      `<div class="nhsuk-summary-list__row"><dt class="nhsuk-summary-list__key">${A.esc(k)}</dt><dd class="nhsuk-summary-list__value">${v}</dd></div>`).join("")}</dl>`;
  },
  table(caption, head, rows, numeric = []) {
    const th = head.map((h, i) => `<th class="nhsuk-table__header${numeric.includes(i) ? " nhsuk-table__header--numeric" : ""}" scope="col">${h}</th>`).join("");
    const tr = rows.map(r => `<tr class="nhsuk-table__row">${r.map((c, i) =>
      `<td class="nhsuk-table__cell${numeric.includes(i) ? " nhsuk-table__cell--numeric" : ""}">${c}</td>`).join("")}</tr>`).join("");
    return `<div class="nhsuk-table-container"><table class="nhsuk-table">${caption ? `<caption class="nhsuk-table__caption">${caption}</caption>` : ""}
      <thead class="nhsuk-table__head"><tr class="nhsuk-table__row">${th}</tr></thead><tbody class="nhsuk-table__body">${tr}</tbody></table></div>`;
  },
  inset(html) { return `<div class="nhsuk-inset-text"><span class="nhsuk-u-visually-hidden">Information: </span>${html}</div>`; },
  warning(heading, html) {
    return `<div class="nhsuk-card nhsuk-card--warning"><div class="nhsuk-card__content"><h3 class="nhsuk-card__heading">${A.esc(heading)}</h3>${html}</div></div>`;
  },
  errorSummary(items) {
    return `<div class="nhsuk-error-summary" tabindex="-1"><div role="alert"><h2 class="nhsuk-error-summary__heading">There is a problem</h2>
      <div class="nhsuk-error-summary__body"><ul class="nhsuk-list nhsuk-error-summary__list">${items.map(i => `<li><a href="#${i.id || ""}">${A.esc(i.text)}</a></li>`).join("")}</ul></div></div></div>`;
  },
};
