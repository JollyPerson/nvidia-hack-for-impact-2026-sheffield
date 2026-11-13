"""TrialMatch UI mockup. Run: streamlit run app/app.py

All data comes from mock_data.py. Swap those functions for the real pipeline later.
"""
import html
import time

import pandas as pd
import streamlit as st

import mock_data as data

st.set_page_config(page_title="TrialMatch", page_icon="🧬", layout="wide")

st.markdown("""
<style>
.block-container {padding-top: 3.2rem;}
.chip {display:inline-block; padding:2px 9px; border-radius:999px; font-size:0.78rem; font-weight:600; margin-right:4px;}
.MET {background:#dcfce7; color:#166534;}
.NOT_MET {background:#f1f5f9; color:#475569;}
.UNKNOWN {background:#fef3c7; color:#92400e;}
.EXCL {background:#fee2e2; color:#991b1b;}
.inc {background:#e0f2fe; color:#075985;}
.exc {background:#fee2e2; color:#991b1b;}
.kind {background:#f3e8ff; color:#6b21a8;}
.badge {background:#76b900; color:#fff; padding:6px 12px; border-radius:8px; font-weight:700; font-size:0.85rem; display:inline-block;}
.local {border:1px solid #76b90055; background:#76b90012; border-radius:10px; padding:10px 12px; font-size:0.85rem;}
.local b {color:#4d7c0f;}
.note {background:#f8fafc; border-left:3px solid #94a3b8; padding:8px 12px; border-radius:4px; font-family:ui-monospace,monospace; font-size:0.83rem; color:#1e293b;}
.note mark {background:#fde68a; padding:0 2px;}
.crit {border:1px solid #e2e8f0; border-radius:10px; padding:10px 14px; margin-bottom:10px;}
.funnel-row {display:flex; align-items:center; gap:10px; margin:6px 0;}
.funnel-bar {background:#76b900; height:26px; border-radius:4px; color:#fff; font-weight:700; padding-left:8px; line-height:26px; font-size:0.85rem;}
.funnel-label {width:210px; font-size:0.85rem; color:#334155;}
.footer {margin-top:2rem; padding:8px 14px; border-top:1px solid #e2e8f0; font-size:0.8rem; color:#475569;}
</style>
""", unsafe_allow_html=True)


def chip(text, cls):
    return f'<span class="chip {cls}">{html.escape(text)}</span>'


def status_chip(s):
    return chip({"MET": "✓ Met", "NOT_MET": "✗ Not met", "UNKNOWN": "? Unknown"}[s], s)


ss = st.session_state
ss.setdefault("ran", False)
ss.setdefault("decisions", {})

trials = data.get_trials()
stats = data.STATS

# ---------- Sidebar ----------
with st.sidebar:
    st.markdown("## 🧬 TrialMatch")
    st.caption("Find eligible patients for clinical trials from GP records and free-text notes.")
    trial_label = st.selectbox("Trial", [f"{t['id']} · {t['title']}" for t in trials])
    trial = trials[[f"{t['id']} · {t['title']}" for t in trials].index(trial_label)]
    st.text_input("…or paste an NCT ID", placeholder="NCT0xxxxxxx")
    st.selectbox("Patient source", ["Sheffield Demo Practice (200 synthetic patients)"])
    go = st.button("🔎 Find patients", type="primary", use_container_width=True)
    st.toggle("Replay cached run", value=True, help="Demo safety net: replays a pre-computed run.")
    st.markdown("---")
    st.markdown(f"""<div class="local">
<b>● Running locally on DGX Spark</b><br>
Host: {stats['host']}<br>
Endpoint: <code>{stats['endpoint']}</code><br>
GPU memory: {stats['gpu_mem_gb']} / 128 GB<br>
Network egress: <b>{stats['bytes_off_device']} bytes</b><br>
Notes never leave this machine.
</div>""", unsafe_allow_html=True)

# ---------- Header ----------
h1, h2 = st.columns([4, 1.3])
with h1:
    st.markdown(f"### {trial['title']}")
    st.caption(f"{trial['id']} · {trial['sponsor']} · {trial['phase']} · recruitment target {trial['target']}")
with h2:
    st.markdown('<div style="text-align:right"><span class="badge">🔒 On-device · DGX Spark</span></div>', unsafe_allow_html=True)

criteria = data.get_criteria(trial["id"])
run = data.get_run(trial["id"])
crit_by_id = {c["id"]: c for c in criteria}

tab_crit, tab_trace, tab_list, tab_review = st.tabs(
    ["① Parsed criteria", "② Agent trace", "③ Shortlist", "④ Recruiter review"])

# ---------- Live trace on click ----------
if go:
    with tab_trace:
        with st.status("Agent running on DGX Spark…", expanded=True) as status:
            for step in run["trace"]:
                st.write(f"**{step['step']}. {step['agent']}** · `{step['tool']}`")
                time.sleep(0.6)
            status.update(label=f"Done in {stats['runtime_s']} s · {stats['notes_per_min']:,} notes/min", state="complete")
    ss.ran = True

# ---------- 1. Criteria ----------
with tab_crit:
    left, right = st.columns([1, 1.25])
    with left:
        st.markdown("**Eligibility text (from ClinicalTrials.gov)**")
        st.markdown(f'<div class="note">{html.escape(trial["eligibility_text"]).replace(chr(10), "<br><br>")}</div>', unsafe_allow_html=True)
    with right:
        st.markdown("**Criteria agent output**")
        for c in criteria:
            kind = "structured → SQL" if c["kind"] == "structured" else "free text → notes"
            rule = f' <code>{c["field"]} {c["op"]} {c["value"]}</code>' if c["kind"] == "structured" else ""
            st.markdown(
                f'<div class="crit"><b>{c["id"]}</b> {chip(c["type"], "inc" if c["type"] == "inclusion" else "exc")}'
                f'{chip(kind, "kind")}{rule}<br>{html.escape(c["text"])}</div>', unsafe_allow_html=True)
        st.caption("Recruiter can edit a criterion before running. Edits are logged in the trace.")

# ---------- 2. Trace ----------
with tab_trace:
    if not ss.ran:
        st.info("Press **Find patients** to run the agent. Each step and tool call appears here as it happens.")
    else:
        m = st.columns(4)
        m[0].metric("Runtime", f"{stats['runtime_s']} s")
        m[1].metric("Notes read", "684")
        m[2].metric("Notes / min", f"{stats['notes_per_min']:,}")
        m[3].metric("Data sent off-device", "0 bytes")
        c1, c2 = st.columns([1.5, 1])
        with c1:
            st.markdown("**Agent trace**")
            for s in run["trace"]:
                model = f" · model `{s['model']}`" if s["model"] else " · no LLM"
                with st.expander(f"✅ Step {s['step']}: {s['agent']}  ·  {s['ms']/1000:.2f} s", expanded=True):
                    st.markdown(f"Tool `{s['tool']}`{model}")
                    st.write(s["detail"])
        with c2:
            st.markdown("**Patient funnel**")
            top = run["funnel"][0][1]
            for label, n in run["funnel"]:
                st.markdown(f'<div class="funnel-row"><div class="funnel-label">{label}</div>'
                            f'<div class="funnel-bar" style="width:{max(n / top * 100, 8):.0f}%">{n}</div></div>',
                            unsafe_allow_html=True)
            st.caption("Structured filter is plain pandas. Only free-text criteria hit the LLM.")

# ---------- 3. Shortlist ----------
with tab_list:
    if not ss.ran:
        st.info("Run the agent to build a shortlist.")
    else:
        show_excl = st.checkbox("Show excluded patients", value=False)
        rows = []
        for r in run["results"]:
            if r.get("excluded") and not show_excl:
                continue
            st_counts = [r["criteria"][c["id"]]["status"] for c in criteria]
            rows.append({
                "Patient": f'{r["patient_id"]} · {r["name"]}',
                "Age": r["age"], "Sex": r["sex"], "BMI": r["bmi"],
                "Match": r["score"],
                "Criteria": " ".join({"MET": "🟢", "NOT_MET": "⚪", "UNKNOWN": "🟡"}[s] for s in st_counts),
                "Why": r["explanation"],
                "Decision": "⛔ Excluded" if r.get("excluded") else ss.decisions.get(r["patient_id"], "Pending"),
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                     column_config={
                         "Match": st.column_config.ProgressColumn("Match", min_value=0, max_value=1, format="%.2f"),
                         "Criteria": st.column_config.TextColumn("C1 … C8", help="🟢 met · ⚪ not met · 🟡 unknown"),
                         "Why": st.column_config.TextColumn("Why", width="large"),
                     })
        st.caption("🟢 met · ⚪ not met (exclusions: not present, which is good) · 🟡 unknown, needs a human check. "
                   "Decision support only: a recruiter confirms every match.")
        approved = [p for p, d in ss.decisions.items() if d == "✅ Approved"]
        st.download_button("Export approved to CSV", "patient_id\n" + "\n".join(approved), "approved.csv",
                           disabled=not approved)

# ---------- 4. Review ----------
with tab_review:
    if not ss.ran:
        st.info("Run the agent, then review patients one by one here.")
    else:
        options = [r for r in run["results"][:4]]
        pick = st.selectbox("Patient", [f'{r["patient_id"]} · {r["name"]} · match {r["score"]:.2f}' for r in options])
        r = options[[f'{x["patient_id"]} · {x["name"]} · match {x["score"]:.2f}' for x in options].index(pick)]

        top_l, top_r = st.columns([3, 1.4])
        with top_l:
            st.markdown(f"#### {r['name']} ({r['patient_id']}), {r['age']}{r['sex']}, BMI {r['bmi']}")
            if r.get("excluded"):
                st.error("Excluded by the agent: " + r["explanation"])
            else:
                st.markdown(f"_{r['explanation']}_")
        with top_r:
            decision = ss.decisions.get(r["patient_id"], "Pending")
            st.markdown(f"**Recruiter decision:** {decision}")
            b1, b2 = st.columns(2)
            if b1.button("✅ Approve", key=f"ap_{r['patient_id']}", use_container_width=True, type="primary"):
                ss.decisions[r["patient_id"]] = "✅ Approved"; st.rerun()
            if b2.button("❌ Reject", key=f"rj_{r['patient_id']}", use_container_width=True):
                ss.decisions[r["patient_id"]] = "❌ Rejected"; st.rerun()
            st.text_input("Reason / note for the log", key=f"why_{r['patient_id']}", placeholder="e.g. confirm ECOG at call")

        st.markdown("##### Criterion-by-criterion evidence")
        for cid, ev in r["criteria"].items():
            c = crit_by_id[cid]
            s = ev["status"]
            is_problem = (c["type"] == "exclusion" and s == "MET")
            head = (f'<b>{cid}</b> {chip(c["type"], "inc" if c["type"] == "inclusion" else "exc")} '
                    f'{html.escape(c["text"])} &nbsp; {chip("Excludes patient", "EXCL") if is_problem else status_chip(s)}'
                    f' <span style="color:#64748b;font-size:0.78rem">confidence {ev["confidence"]:.2f}</span>')
            body = ""
            if ev.get("note") and ev.get("quote"):
                note = html.escape(ev["note"]).replace(html.escape(ev["quote"]), f"<mark>{html.escape(ev['quote'])}</mark>")
                body = f'<div style="font-size:0.78rem;color:#64748b;margin:6px 0 4px">📄 {ev["source"]}</div><div class="note">{note}</div>'
            elif ev.get("source"):
                body = f'<div style="font-size:0.78rem;color:#64748b;margin-top:6px">🗂 {html.escape(ev["source"])}</div>'
            if s == "UNKNOWN":
                body += ('<div style="font-size:0.8rem;color:#92400e;margin-top:6px">No verbatim evidence found in notes. '
                         'Ask the patient or check the hospital letter.</div>')
            st.markdown(f'<div class="crit">{head}{body}</div>', unsafe_allow_html=True)

# ---------- Footer ----------
st.markdown(f'<div class="footer">🟩 Model: {stats["model"]} on {stats["host"]} · '
            f'<b>0 bytes sent off-device</b> · {stats["notes_per_min"]:,} notes/min · Synthetic data only. '
            f'Decision support for recruiters, not an eligibility decision.</div>', unsafe_allow_html=True)
