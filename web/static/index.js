A.features().then(() => A.devPanel());
(async function () {
  const el = document.getElementById("studies");
  try {
    const studies = await A.api("/api/studies");
    el.innerHTML = A.table(`${studies.length} studies`, ["Study", "Condition", "Target", "Status"],
      studies.map(s => [
        `<a href="/studies/${A.esc(s.id)}">${A.esc(s.title)}</a><br><span class="nhsuk-body-s nhsuk-u-secondary-text-colour">${A.esc(s.short_title || s.id)}</span>`,
        A.esc(s.condition),
        `${s.target_sample_size} participants`,
        A.studyStatus(s.status) + (s.screened ? " " + A.tag("Screened", "blue") : ""),
      ]));
  } catch (e) {
    el.innerHTML = A.errorSummary([{ text: "Studies could not be loaded. Try again later." }]);
  }
})();
