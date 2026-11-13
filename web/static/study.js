// Study page: details, criteria, run screening with live progress (server-sent events), results, patient evidence,
// approve or reject, and the audit trail. Everything updates in place; no page reloads.
(function () {
  const studyId = decodeURIComponent(location.pathname.split("/").pop());
  const $ = id => document.getElementById(id);
  const S = { study: null, run: null, models: {} };
  const STEP_NAMES = ["Prefilter: SQL on the warehouse", "Stage 1: screener reads notes", "Stage 2: verifier re-checks original records",
    "Validation: check every citation", "Decide: code combines verdicts and flags"];

  // ---------- Study details ----------
  async function loadStudy() {
    try { S.study = await A.api(`/api/studies/${encodeURIComponent(studyId)}`); }
    catch (e) { $("study").innerHTML = `<h1>Study not found</h1><p>Check the link, or <a href="/">go back to studies</a>.</p>`; return false; }
    const s = S.study;
    document.title = `${s.short_title || s.title} - Advolare`;
    const created = new URLSearchParams(location.search).get("created");
    $("study").innerHTML = `
      ${created ? `<div class="nhsuk-inset-text"><span class="nhsuk-u-visually-hidden">Information: </span><p>Study saved. Check the criteria, then run screening.</p></div>` : ""}
      <span class="nhsuk-caption-l">${A.esc(s.short_title || s.id)}</span>
      <h1>${A.esc(s.title)}</h1>
      <p>${A.studyStatus(s.status)}</p>
      <div class="nhsuk-grid-row"><div class="nhsuk-grid-column-two-thirds">
      ${A.summaryList([
        ["Condition", A.esc(s.condition)],
        ["Design", A.esc([s.study_design, s.phase].filter(Boolean).join(", ") || "Not set")],
        ["Intervention", A.esc(s.intervention || "Not set")],
        ["Primary outcome", A.esc(s.primary_outcome || "Not set")],
        ["Target", `${s.target_sample_size} participants`],
        ["Recruitment", s.recruitment_start ? `${A.date(s.recruitment_start)} to ${A.date(s.recruitment_end)}` : "Not set"],
        ["Chief investigator", A.esc(s.chief_investigator || "Not set")],
        ["Sponsor", A.esc(s.sponsor || "Not set")],
        ["Ethics reference", A.esc(s.ethics_reference || "Not set")],
      ])}
      </div></div>
      <h2 class="nhsuk-heading-l">Eligibility criteria</h2>
      ${A.table("", ["ID", "Criterion", "Type", "How it is checked", "SNOMED CT"], s.criteria.map(c => [
        `<strong>${A.esc(c.id)}</strong>`,
        A.esc(c.description) + (c.kind === "structured" ? `<br><span class="nhsuk-body-s nhsuk-u-secondary-text-colour">${A.esc(A.rule(c))}</span>` : ""),
        A.typeTag(c.type), A.kindTag(c.kind), `<span class="adv-snomed" data-cid="${A.esc(c.id)}"></span>`]))}
      <p class="nhsuk-body-s nhsuk-u-secondary-text-colour">SNOMED CT codes are illustrative. Check them in the UK SNOMED CT browser before real use.</p>
      <a class="nhsuk-button nhsuk-button--secondary" href="/api/studies/${encodeURIComponent(s.id)}/fhir" download>Download criteria as FHIR R5 (JSON)</a>`;
    A.api(`/api/studies/${encodeURIComponent(s.id)}/snomed`).then(map => document.querySelectorAll(".adv-snomed").forEach(el => {
      const m = map[el.dataset.cid]; el.innerHTML = m && m.code ? `${A.esc(m.display)}<br><span class="nhsuk-body-s nhsuk-u-secondary-text-colour">${A.esc(m.code)}</span>` : `<span class="nhsuk-u-secondary-text-colour">No code</span>`;
    })).catch(() => {});
    return true;
  }

  // ---------- Screening with live progress ----------
  function taskList(states) {
    return `<ul class="nhsuk-task-list" id="tasks">${STEP_NAMES.map((name, i) => {
      const st = states[i] || { state: "waiting" };
      const status = st.state === "done" ? `<div class="nhsuk-task-list__status nhsuk-task-list__status--completed">Done${st.seconds != null ? ` (${st.seconds}s)` : ""}</div>`
        : st.state === "running" ? `<div class="nhsuk-task-list__status">${A.tag("Running", "blue")}</div>`
        : `<div class="nhsuk-task-list__status nhsuk-task-list__status--cannot-start-yet">Waiting</div>`;
      const hint = st.detail ? `<div class="nhsuk-task-list__hint">${A.esc(st.detail)}${st.model ? ` Model: ${A.esc(st.model)}.` : ""}</div>` : "";
      return `<li class="nhsuk-task-list__item"><div class="nhsuk-task-list__name-and-hint"><div>${A.esc(st.name || name)}</div>${hint}</div>${status}</li>`;
    }).join("")}</ul>`;
  }
  function screeningPanel(hasRun) {
    $("screening").innerHTML = `
      <h2 class="nhsuk-heading-l">Screening</h2>
      <div class="nhsuk-grid-row"><div class="nhsuk-grid-column-two-thirds">
        <p>Advolare checks the hospital's records in 5 steps. Code filters the structured data first. Then two AI models read the clinical notes separately, every quote they give is checked against the record, and code combines the answers. You make the final decision for every patient.</p>
        <p><span class="adv-device">Runs on this device</span> No patient data leaves the DGX Spark.</p>
      </div></div>
      <div id="progress"></div>
      <button class="nhsuk-button" type="button" id="run">${hasRun ? "Run screening again" : "Run screening"}</button>`;
    $("run").addEventListener("click", runScreening);
  }
  async function runScreening() {
    const btn = $("run");
    btn.disabled = true; btn.textContent = "Screening…";
    $("results").innerHTML = ""; showPatient(null, false);
    const states = [];
    $("progress").innerHTML = `<h3>Progress</h3><div aria-live="polite">${taskList(states)}</div>`;
    let run;
    try { run = await A.api(`/api/studies/${encodeURIComponent(studyId)}/runs`, { method: "POST" }); }
    catch (e) { $("progress").innerHTML = A.errorSummary([{ text: "Screening could not start. Try again." }]); btn.disabled = false; btn.textContent = "Run screening"; return; }
    const es = new EventSource(`/api/runs/${run.run_id}/events`);
    es.addEventListener("step", ev => {
      const d = JSON.parse(ev.data);
      states[d.step - 1] = d.state === "running" ? { state: "running", name: d.name } : { ...d, state: "done" };
      $("tasks").outerHTML = taskList(states);
    });
    es.addEventListener("complete", async () => {
      es.close();
      btn.disabled = false; btn.textContent = "Run screening again";
      await loadResults(); loadAudit();
      $("results").scrollIntoView({ behavior: "smooth", block: "start" });
    });
    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) return;
      es.close(); btn.disabled = false; btn.textContent = "Run screening";
      $("progress").insertAdjacentHTML("beforeend", A.errorSummary([{ text: "Lost connection to the screening run. Run it again." }]));
    };
  }

  // ---------- Results ----------
  async function loadResults() {
    try { S.run = await A.api(`/api/studies/${encodeURIComponent(studyId)}/results`); }
    catch (e) { S.run = null; return false; }
    S.run.trace.forEach(t => { if (t.step === 2) S.models.screener = t.model; if (t.step === 3) S.models.verifier = t.model; });
    renderResults();
    return true;
  }
  function decisionTag(d) {
    return { approved: A.tag("Approved", "green"), rejected: A.tag("Rejected", "grey"), pending: A.tag("To review", "white") }[d];
  }
  function renderResults() {
    const f = S.run.funnel, max = f.candidates_after_sql || 1;
    const bar = (label, n) => `<div class="row"><div class="lbl"><span>${label}</span><strong>${n}</strong></div><div class="bar"><i style="width:${Math.round(100 * n / max)}%"></i></div></div>`;
    const group = (status, heading, intro) => {
      const rows = S.run.patients.filter(r => r.result.status === status);
      if (!rows.length) return "";
      return `<h3 class="nhsuk-heading-m">${heading} (${rows.length})</h3>${intro ? `<p>${intro}</p>` : ""}
        ${A.table("", ["Patient", "Age", "Sex", "Why", "Your decision"], rows.map(r => [
          `<button type="button" class="adv-patient-link" data-mrn="${A.esc(r.patient_id)}">${A.esc(r.patient_id)}</button>`,
          r.age, r.sex, A.esc(r.summary) + (nFlags(r) ? `<br>${A.tag(`${nFlags(r)} flag${nFlags(r) > 1 ? "s" : ""}`, "yellow")}` : ""),
          decisionTag(r.decision)]))}`;
    };
    const notShortlisted = f.candidates_after_sql - f.shortlisted_stage1;
    const removed = f.removed_before_screening || [];
    const nRemoved = removed.reduce((n, g) => n + g.count, 0);
    const tabs = [["tab-results", "Results"], ["tab-fairness", "Fairness check"], ["tab-whatif", "What if"], ["tab-log", "Screening log"]];
    S.tab = S.tab || "tab-results";
    $("results").innerHTML = `
      <h2 class="nhsuk-heading-l">Results</h2>
      <div class="nhsuk-tabs">
        <h2 class="nhsuk-tabs__title">Contents</h2>
        <ul class="nhsuk-tabs__list" role="tablist">${tabs.map(([id, label]) => `<li class="nhsuk-tabs__list-item${id === S.tab ? " nhsuk-tabs__list-item--selected" : ""}" role="presentation"><a class="nhsuk-tabs__tab" href="#${id}" id="${id}-link" role="tab" aria-controls="${id}" aria-selected="${id === S.tab}">${label}</a></li>`).join("")}</ul>
      <div class="nhsuk-tabs__panel${S.tab === "tab-results" ? "" : " nhsuk-tabs__panel--hidden"}" id="tab-results" role="tabpanel">
      <div class="nhsuk-grid-row"><div class="nhsuk-grid-column-two-thirds">
        ${nRemoved ? A.inset(`<p>${nRemoved} patients with a do-not-contact flag were removed by code before screening. Their records were not read by any model.</p>
          <ul class="nhsuk-body-s">${removed.map(g => `<li>${A.esc(g.reason)}: ${g.count}</li>`).join("")}</ul>`) : ""}
        <div class="adv-funnel">
          ${bar("Candidates after record check (SQL)", f.candidates_after_sql)}
          ${bar("Shortlisted by the screener (stage 1)", f.shortlisted_stage1)}
          ${bar("Still in after the verifier (stage 2)", f.confirmed_stage2)}
          ${bar("Likely eligible", f.likely_eligible)}
        </div>
        ${A.inset(`<p>These are suggestions. Open each patient to check the evidence before you approve them for contact.</p>`)}
      </div></div>
      ${group("likely_eligible", "Likely eligible", "Both models agree these patients meet every criterion, and every quote was found in the record.")}
      ${group("needs_review", "Needs review", "The models disagree, a quote could not be found, or the records do not say enough.")}
      ${group("excluded", "Excluded", `Both models agree an exclusion criterion applies, with checked quotes. ${notShortlisted} more candidates did not pass stage 1 and are not listed.`)}
      </div>
      <div class="nhsuk-tabs__panel${S.tab === "tab-fairness" ? "" : " nhsuk-tabs__panel--hidden"}" id="tab-fairness" role="tabpanel"><p>Loading…</p></div>
      <div class="nhsuk-tabs__panel${S.tab === "tab-whatif" ? "" : " nhsuk-tabs__panel--hidden"}" id="tab-whatif" role="tabpanel"><p>Loading…</p></div>
      <div class="nhsuk-tabs__panel${S.tab === "tab-log" ? "" : " nhsuk-tabs__panel--hidden"}" id="tab-log" role="tabpanel"><p>Loading…</p></div>
      </div>`;
    $("results").querySelectorAll(".adv-patient-link").forEach(b => b.addEventListener("click", () => showPatient(b.dataset.mrn)));
    $("results").querySelectorAll(".nhsuk-tabs__tab").forEach(a => a.addEventListener("click", e => {
      if (window.matchMedia("(max-width: 640px)").matches) return; // on phones NHS tabs show every panel, links jump
      e.preventDefault(); selectTab(a.getAttribute("href").slice(1));
    }));
    loadExtras();
  }


  // ---------- Extras: fairness, what if, screening log ----------
  function selectTab(id) {
    S.tab = id;
    $("results").querySelectorAll(".nhsuk-tabs__list-item").forEach(li => {
      const on = li.querySelector("a").getAttribute("href") === "#" + id;
      li.classList.toggle("nhsuk-tabs__list-item--selected", on); li.querySelector("a").setAttribute("aria-selected", on);
    });
    $("results").querySelectorAll(".nhsuk-tabs__panel").forEach(p => p.classList.toggle("nhsuk-tabs__panel--hidden", p.id !== id));
  }
  async function loadExtras() {
    const enc = encodeURIComponent(studyId);
    const [fair, whatif, log] = await Promise.all([`/api/studies/${enc}/fairness`, `/api/studies/${enc}/what-if`,
      `/api/studies/${enc}/screening-log`].map(u => A.api(u).catch(() => null)));
    if (fair) $("tab-fairness").innerHTML = `
      <h2 class="nhsuk-heading-m">Is the shortlist fair?</h2>
      <div class="nhsuk-grid-row"><div class="nhsuk-grid-column-two-thirds">
        <p>Compares the ${fair.shortlist_size} patients on the shortlist (likely eligible and needs review) with everyone admitted to the hospital.</p>
        ${fair.flags.length ? A.warning("Groups that may be under-represented", `<ul>${fair.flags.map(t => `<li>${A.esc(t)}</li>`).join("")}</ul><p>${A.esc(fair.note)}</p>`) : A.inset(`<p>No group is under-represented by 15 points or more. ${A.esc(fair.note)}</p>`)}
      </div></div>
      ${fair.groups.map(g => A.table(A.esc(g.characteristic), ["Group", "Admitted patients", "Shortlist"],
        g.rows.map(r => [A.esc(r.group), `${r.admitted_pct}%`, `${r.shortlist_pct}%`]), [1, 2])).join("")}`;
    if (whatif) $("tab-whatif").innerHTML = `
      <h2 class="nhsuk-heading-m">What if you removed a criterion?</h2>
      <div class="nhsuk-grid-row"><div class="nhsuk-grid-column-two-thirds">
        <p>Code works these out again from the verdicts the models have already given, so no new model calls are needed. Removing a record check also lets more candidates through, and they would need screening.</p>
      </div></div>
      ${A.table("", ["Criterion", "Likely eligible now", "Without this criterion", "Need review", "More candidates to screen"],
        whatif.map(w => {
          const diff = w.likely_eligible_without - w.likely_eligible_now;
          return [`<strong>${A.esc(w.criterion_id)}</strong> ${A.esc(w.description)}<br>${A.typeTag(w.type)}`, w.likely_eligible_now,
            `${w.likely_eligible_without}${diff > 0 ? ` ${A.tag("+" + diff, "green")}` : ""}`, w.needs_review_without,
            w.extra_candidates_to_screen ? `+${w.extra_candidates_to_screen}` : "None"];
        }), [1, 2, 3, 4])}
      ${A.inset("<p>Changing criteria may need an ethics amendment. Talk to your sponsor before you change the protocol.</p>")}`;
    if (log) $("tab-log").innerHTML = `
      <h2 class="nhsuk-heading-m">Screening log</h2>
      <div class="nhsuk-grid-row"><div class="nhsuk-grid-column-two-thirds">
        <p>Every patient screened for this study, with the outcome and your decision. Good clinical practice (ICH E6) asks studies to keep one.</p>
      </div></div>
      ${A.table("", ["Patient", "Outcome", "Reason", "Your decision"], log.map(r => [A.esc(r.patient_id), A.esc(r.outcome), A.esc(r.reason),
        r.decision ? decisionTag(r.decision) : ""]))}
      <a class="nhsuk-button nhsuk-button--secondary" href="/api/studies/${enc}/screening-log.csv" download>Download screening log (CSV)</a>`;
  }

  // ---------- Patient evidence and decision ----------
  function nFlags(p) { return p.result.assessments.reduce((n, a) => n + a.flags.length, 0); }
  function citations(v, failed) {
    if (!v.citations.length) return `<p class="nhsuk-body-s nhsuk-u-secondary-text-colour">No record cited.</p>`;
    return v.citations.map(c => `<p class="adv-cite">${A.esc(c.table)} · ${A.esc(c.record_id)} · ${A.esc(c.field)} ${failed ? A.tag("Quote not found in this record", "red") : ""}</p>
        <div class="nhsuk-inset-text adv-quote"><span class="nhsuk-u-visually-hidden">Quote from the record: </span><p><mark>${A.esc(c.quote)}</mark></p></div>`).join("");
  }
  function verdictBlock(title, model, v, failed) {
    if (!v) return `<div><h4 class="nhsuk-heading-xs">${title}</h4><p class="nhsuk-body-s nhsuk-u-secondary-text-colour">Not checked at this stage.</p></div>`;
    return `<div><h4 class="nhsuk-heading-xs">${title}${model ? ` <span class="nhsuk-body-s nhsuk-u-secondary-text-colour">(${A.esc(model)})</span>` : ""}</h4>
      <p>${A.verdict(v.verdict)}</p>${v.reasoning ? `<p class="nhsuk-body-s">${A.esc(v.reasoning)}</p>` : ""}${citations(v, failed)}</div>`;
  }
  function flagText(cid, f) {
    const where = f.stage ? ` (stage ${f.stage})` : "";
    return f.type === "citation_failed" ? `${cid}: a quote was not found in the cited record${where}. ${f.detail}`
      : `${cid}: the two models disagree. ${f.detail}`;
  }
  function patientHtml(p, contact) {
    const r = p.result;
    const crit = S.study.criteria.map(c => {
      const a = r.assessments.find(x => x.criterion_id === c.id) || { stage1: null, stage2: null, flags: [] };
      const failedStage = st => a.flags.some(f => f.type === "citation_failed" && (f.stage === st || f.stage == null));
      const flagged = a.flags.length > 0;
      const exclMet = c.type === "exclusion" && [a.stage1, a.stage2].some(v => v && v.verdict === "met");
      return `<div class="nhsuk-card adv-criterion${flagged ? " flagged" : exclMet ? " exclusion-met" : ""}"><div class="nhsuk-card__content">
        <h3 class="nhsuk-card__heading nhsuk-heading-s">${A.esc(c.id)}: ${A.esc(c.description)}</h3>
        <p>${A.typeTag(c.type)} ${A.kindTag(c.kind)} ${flagged ? A.tag("Flagged", "yellow") : ""}</p>
        ${c.kind === "structured" ? verdictBlock("Record check (SQL)", "", a.stage1, failedStage(1))
          : `<div class="adv-verdicts">${verdictBlock("Stage 1: screener", S.models.screener, a.stage1, failedStage(1))}${verdictBlock("Stage 2: verifier", S.models.verifier, a.stage2, failedStage(2))}</div>`}
      </div></div>`;
    }).join("");
    const allFlags = r.assessments.flatMap(a => a.flags.map(f => flagText(a.criterion_id, f)));
    const flags = allFlags.length ? A.warning("Check these before you decide", `<ul>${allFlags.map(t => `<li>${A.esc(t)}</li>`).join("")}</ul>`) : "";
    const reasons = r.reasons.length ? A.inset(`<p>Excluded because:</p><ul>${r.reasons.map(x => `<li>${A.esc(x)}</li>`).join("")}</ul>`) : "";
    const decided = p.decision !== "pending";
    const decisionForm = decided ? `
      <h2 class="nhsuk-heading-m">Your decision</h2>
      <p>${decisionTag(p.decision)}</p>
      <button class="nhsuk-button nhsuk-button--secondary" type="button" id="undo">Change decision</button>` : `
      <h2 class="nhsuk-heading-m">Your decision</h2>
      <div class="nhsuk-form-group"><label class="nhsuk-label" for="note">Note for the screening log (optional)</label>
        <div class="nhsuk-hint" id="note-hint">For example, why you approved or rejected this patient.</div>
        <textarea class="nhsuk-textarea" id="note" rows="3" aria-describedby="note-hint"></textarea></div>
      <div class="nhsuk-button-group">
        <button class="nhsuk-button" type="button" data-decision="approved">Approve for contact</button>
        <button class="nhsuk-button nhsuk-button--secondary" type="button" data-decision="rejected">Reject</button>
      </div>`;
    const contactHtml = contact ? `
      <h2 class="nhsuk-heading-m">Contact details</h2>
      ${A.summaryList([["Name", A.esc(contact.name)], ["Mobile", A.esc(contact.mobile)], ["Email", A.esc(contact.email)],
        ...(contact.guardian_name ? [["Next of kin", `${A.esc(contact.guardian_name)}, ${A.esc(contact.guardian_phone || "")}`]] : [])])}
      ${A.inset(`<p>These details come from the PatientContacts table. They are shown only after your approval and were never given to an AI model.</p>`)}
      <h2 class="nhsuk-heading-m">Invitation letter</h2>
      <div class="nhsuk-form-group"><label class="nhsuk-label" for="letter">Draft letter to check and edit</label>
        <div class="nhsuk-hint" id="letter-hint">It does not name the condition. Advolare never sends letters: copy it into your usual letter system.</div>
        <textarea class="nhsuk-textarea" id="letter" rows="18" aria-describedby="letter-hint">Loading…</textarea></div>
      <button class="nhsuk-button nhsuk-button--secondary" type="button" id="copy-letter">Copy letter</button>` : "";
    return `
      <a class="nhsuk-back-link" href="#results" id="back"><span class="nhsuk-u-visually-hidden">Back to </span>Results</a>
      <span class="nhsuk-caption-l">${A.esc(S.study.short_title || S.study.id)} screening result</span>
      <h2 class="nhsuk-heading-xl">${A.esc(p.patient_id)}</h2>
      <p>${A.patientStatus(r.status)}</p>
      <div class="nhsuk-grid-row"><div class="nhsuk-grid-column-two-thirds">
        ${A.summaryList([["Age", `${p.age}`], ["Sex", p.sex], ["Summary", A.esc(p.summary)], ["Decision", decisionTag(p.decision)]])}
        ${flags}${reasons}
      </div></div>
      <h2 class="nhsuk-heading-m">Evidence for each criterion</h2>
      ${crit}
      <div class="nhsuk-grid-row"><div class="nhsuk-grid-column-two-thirds">${decisionForm}${contactHtml}</div></div>`;
  }
  async function showPatient(mrn, push = true) {
    const sec = $("patient");
    if (!mrn) {
      sec.innerHTML = ""; ["study", "screening", "results", "audit"].forEach(id => $(id).classList.remove("adv-hidden"));
      if (push && location.search.includes("patient=")) history.pushState({}, "", location.pathname);
      return;
    }
    const enc = encodeURIComponent(studyId), m = encodeURIComponent(mrn);
    let p, contact = null;
    try {
      p = await A.api(`/api/studies/${enc}/patients/${m}`);
      if (p.decision === "approved") contact = await A.api(`/api/studies/${enc}/patients/${m}/contact`);
    } catch (e) { return; }
    ["study", "screening", "results", "audit"].forEach(id => $(id).classList.add("adv-hidden"));
    sec.innerHTML = patientHtml(p, contact);
    if (push) history.pushState({ mrn }, "", `${location.pathname}?patient=${m}`);
    window.scrollTo(0, 0); sec.focus();
    $("back").addEventListener("click", e => { e.preventDefault(); showPatient(null); $("results").scrollIntoView(); });
    sec.querySelectorAll("[data-decision]").forEach(b => b.addEventListener("click", () => decide(mrn, b.dataset.decision, $("note").value)));
    const undo = $("undo"); if (undo) undo.addEventListener("click", () => decide(mrn, "pending", ""));
    if ($("letter")) {
      A.api(`/api/studies/${enc}/patients/${m}/letter`).then(r => { $("letter").value = r.text; }).catch(() => { $("letter").value = ""; });
      $("copy-letter").addEventListener("click", async () => {
        try { await navigator.clipboard.writeText($("letter").value); $("copy-letter").textContent = "Copied"; }
        catch (e) { $("letter").select(); }
      });
    }
  }
  async function decide(mrn, decision, note) {
    const r = await A.api(`/api/studies/${encodeURIComponent(studyId)}/patients/${encodeURIComponent(mrn)}/decision`,
      { method: "POST", body: JSON.stringify({ decision, note }) });
    const i = S.run.patients.findIndex(x => x.patient_id === mrn);
    S.run.patients[i] = r.patient;
    renderResults(); loadAudit();
    await showPatient(mrn, false);
  }
  window.addEventListener("popstate", () => {
    const mrn = new URLSearchParams(location.search).get("patient");
    showPatient(mrn, false);
  });

  // ---------- Audit trail ----------
  async function loadAudit() {
    const rows = await A.api(`/api/studies/${encodeURIComponent(studyId)}/audit`).catch(() => []);
    $("audit").innerHTML = `<h2 class="nhsuk-heading-l">Audit trail</h2>` + (rows.length
      ? A.table("Every action on this study, newest first", ["When", "Who", "Action", "Patient", "Note"],
          rows.map(a => [A.dateTime(a.when), A.esc(a.who), A.esc(a.action), A.esc(a.patient_id || ""), A.esc(a.note || "")]))
      : `<p>Nothing has happened on this study yet.</p>`);
  }

  (async () => {
    if (!(await loadStudy())) return;
    const hasRun = await loadResults();
    screeningPanel(hasRun);
    loadAudit();
    const mrn = new URLSearchParams(location.search).get("patient");
    if (mrn && hasRun) showPatient(mrn, false);
  })();
})();
