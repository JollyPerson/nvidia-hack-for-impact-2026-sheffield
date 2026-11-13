"""TrialMatch UI mockup. Run: streamlit run app/app.py

All data comes from mock_data.py. Swap those functions for the real pipeline later.

Visual language: NHS design system colours and components (inset text, tags, summary list,
warning callout) for the clinical parts, NVIDIA green only for the on-device / DGX Spark signal.
No NHS logo is used and no endorsement is implied.
"""
import html
import time

import streamlit as st

import mock_data as data

st.set_page_config(page_title="TrialMatch", page_icon="🧬", layout="wide")

# NHS palette (service-manual.nhs.uk/design-system/styles/colour)
CSS = """
<style>
:root{--blue:#005eb8;--dblue:#003087;--pblue:#ccdff1;--text:#212b32;--grey1:#4c6272;--grey2:#768692;
--grey4:#d8dde0;--grey5:#f0f4f5;--green:#007f3b;--pgreen:#cce5d8;--dgreen:#004c23;--red:#d5281b;--pred:#f7d4d1;
--dred:#801810;--wy:#ffb81c;--pwy:#fff1d2;--dwy:#664a0b;--yellow:#ffeb3b;--pyellow:#fff9c4;--nv:#76b900;}
html, body, [class*="css"], .stMarkdown, button, input, select {font-family: Arial, "Helvetica Neue", sans-serif;}
[data-testid="stHeader"]{display:none;}
.block-container{padding-top:0 !important; max-width:1320px;}
[data-testid="stSidebar"]{background:#fff; border-right:1px solid var(--grey4);}
/* top bar, NHS header style without logo */
.topbar{background:var(--blue); color:#fff; margin:0 -5rem 0 -5rem; padding:14px 5rem; margin-top:-1rem; display:flex; align-items:center; gap:18px;}
.topbar .brand{font-weight:700; font-size:1.35rem; letter-spacing:-0.01em;}
.topbar .sub{opacity:.85; font-size:.95rem; border-left:1px solid #ffffff55; padding-left:18px;}
.topbar .pill{margin-left:auto; background:#00000033; border:1px solid #ffffff44; border-radius:999px; padding:5px 12px; font-size:.82rem; font-weight:700;}
.topbar .dot{display:inline-block; width:8px; height:8px; border-radius:50%; background:var(--nv); margin-right:6px; box-shadow:0 0 0 3px #76b90044;}
.crumbs{color:var(--grey1); font-size:.85rem; margin:18px 0 2px;}
h1.title{font-size:1.9rem; color:var(--text); margin:0 0 4px; line-height:1.2;}
.meta{color:var(--grey1); font-size:.92rem; margin-bottom:14px;}
/* KPI cards */
.kpis{display:grid; grid-template-columns:repeat(5,1fr); gap:14px; margin:6px 0 18px;}
.kpi{background:#fff; border:1px solid var(--grey4); border-radius:10px; padding:14px 16px;}
.kpi .l{color:var(--grey1); font-size:.8rem; font-weight:700; text-transform:uppercase; letter-spacing:.03em;}
.kpi .v{font-size:1.9rem; font-weight:700; color:var(--text); margin-top:4px;}
.kpi .s{font-size:.8rem; color:var(--grey1);}
.kpi.nv{border-color:#76b90088; background:linear-gradient(180deg,#76b90010,#fff);}
.kpi.nv .v{color:#3f6212;}
/* cards */
.card{background:#fff; border:1px solid var(--grey4); border-radius:10px; padding:18px 20px; margin-bottom:14px;}
.card h3{font-size:1.05rem; margin:0 0 12px; color:var(--text);}
/* NHS tags */
.tag{display:inline-block; white-space:nowrap; padding:2px 8px; border:1px solid; font-size:.76rem; font-weight:700; margin-right:4px; border-radius:2px;}
.t-blue{background:var(--pblue); color:var(--dblue); border-color:#99bfe3;}
.t-red{background:var(--pred); color:var(--dred); border-color:#eaa9a3;}
.t-green{background:var(--pgreen); color:var(--dgreen); border-color:#99cbb1;}
.t-grey{background:#fff; color:var(--grey1); border-color:var(--grey4);}
.t-warm{background:var(--pwy); color:var(--dwy); border-color:#ffd889;}
.t-purple{background:#ded6e8; color:#330072; border-color:#bdaed1;}
/* criteria list */
.crow{display:flex; gap:12px; padding:10px 0; border-bottom:1px solid var(--grey4); align-items:flex-start;}
.crow:last-child{border-bottom:none;}
.cid{font-weight:700; color:var(--blue); width:28px; flex:none;}
.ctext{flex:1; color:var(--text);}
.ctext code{font-size:.78rem; background:var(--grey5); color:var(--text);}
/* NHS inset text */
.inset{border-left:8px solid var(--blue); padding:10px 16px; background:#fff; color:var(--text); font-size:.95rem; line-height:1.55;}
/* agent timeline */
.tl{position:relative; padding-left:34px;}
.tl:before{content:""; position:absolute; left:13px; top:8px; bottom:8px; width:2px; background:var(--grey4);}
.step{position:relative; background:#fff; border:1px solid var(--grey4); border-radius:10px; padding:12px 16px; margin-bottom:12px;}
.step .node{position:absolute; left:-34px; top:12px; width:28px; height:28px; border-radius:50%; background:var(--green); color:#fff; font-weight:700; font-size:.85rem; display:flex; align-items:center; justify-content:center; box-shadow:0 0 0 4px var(--grey5);}
.step .hd{display:flex; align-items:center; gap:8px; flex-wrap:wrap;}
.step .name{font-weight:700; color:var(--text);}
.step .ms{margin-left:auto; color:var(--grey1); font-size:.82rem; font-variant-numeric:tabular-nums;}
.step .bar{height:4px; background:var(--grey5); border-radius:2px; margin:8px 0 6px;}
.step .bar i{display:block; height:4px; background:var(--blue); border-radius:2px;}
.step .dt{color:var(--grey1); font-size:.88rem;}
.step code{font-size:.76rem; background:var(--grey5); color:var(--text); padding:1px 5px; border-radius:3px;}
/* funnel */
.fr{margin:10px 0;}
.fr .fl{display:flex; justify-content:space-between; font-size:.86rem; color:var(--text); margin-bottom:4px;}
.fr .fl b{font-variant-numeric:tabular-nums;}
.fr .fb{height:12px; background:var(--grey5); border-radius:6px;}
.fr .fb i{display:block; height:12px; border-radius:6px; background:var(--blue);}
/* shortlist table */
table.sl{width:100%; border-collapse:collapse; background:#fff; font-size:.9rem;}
table.sl th{text-align:left; color:var(--grey1); font-size:.76rem; text-transform:uppercase; letter-spacing:.03em; padding:10px 12px; border-bottom:2px solid var(--grey4);}
table.sl td{padding:12px; border-bottom:1px solid var(--grey4); vertical-align:middle; color:var(--text);}
table.sl tr.ex td{background:#fdf5f4;}
.av{width:34px; height:34px; border-radius:50%; background:var(--pblue); color:var(--dblue); font-weight:700; display:inline-flex; align-items:center; justify-content:center; margin-right:10px; font-size:.8rem;}
.pname{font-weight:700;} table.sl td:first-child{min-width:250px;} .pid{color:var(--grey1); font-size:.8rem;}
.score{display:flex; align-items:center; gap:8px; min-width:120px;}
.score .tr{flex:1; height:6px; background:var(--grey5); border-radius:3px;} .score .tr i{display:block; height:6px; border-radius:3px; background:var(--green);}
.dots span{display:inline-block; width:22px; height:22px; line-height:20px; text-align:center; border-radius:3px; font-size:.7rem; font-weight:700; margin-right:2px; border:1px solid;}
.d-MET{background:var(--pgreen); color:var(--dgreen); border-color:#99cbb1;}
.d-NOT_MET{background:#fff; color:var(--grey2); border-color:var(--grey4);}
.d-UNKNOWN{background:var(--pwy); color:var(--dwy); border-color:#ffd889;}
.d-EXCL{background:var(--pred); color:var(--dred); border-color:#eaa9a3;}
/* review */
.qhead{font-size:.8rem; color:var(--grey1); font-weight:700; text-transform:uppercase; letter-spacing:.03em; margin-bottom:6px;}
div[role="radiogroup"]{width:100%;}
div[role="radiogroup"] label{background:#fff; border:1px solid var(--grey4); border-radius:8px; padding:10px 12px !important; margin-bottom:6px; width:100%;}
div[role="radiogroup"] label:has(input:checked){border-color:var(--blue); box-shadow:inset 4px 0 0 var(--blue);}
.summ{display:grid; grid-template-columns:150px 1fr; gap:0; border-top:1px solid var(--grey4); margin-top:8px;}
.summ div{padding:8px 0; border-bottom:1px solid var(--grey4); font-size:.92rem;}
.summ .k{font-weight:700; color:var(--text);}
.ev{border:1px solid var(--grey4); border-radius:10px; background:#fff; padding:14px 16px; margin-bottom:10px;}
.ev.bad{border-color:var(--red); box-shadow:inset 6px 0 0 var(--red);}
.ev .row1{display:flex; align-items:center; gap:8px; flex-wrap:wrap;}
.ev .ct{font-weight:700; color:var(--text); flex:1; min-width:200px;}
.ev .conf{color:var(--grey1); font-size:.8rem;}
.ev .src{color:var(--grey1); font-size:.8rem; margin:8px 0 6px;}
.ev .inset{font-family:Arial, sans-serif; font-size:.9rem; border-left-width:6px; background:var(--grey5);}
.ev mark{background:var(--yellow); color:var(--text); padding:0 2px; box-shadow:0 2px 0 var(--text);}
.warn{background:var(--pwy); border-left:6px solid var(--wy); padding:8px 12px; font-size:.86rem; color:var(--text); margin-top:8px;}
.decision{border-radius:10px; padding:12px 14px; font-weight:700; margin-bottom:10px; border:1px solid var(--grey4); background:#fff;}
.decision.ok{background:var(--pgreen); border-color:#99cbb1; color:var(--dgreen);}
.decision.no{background:var(--pred); border-color:#eaa9a3; color:var(--dred);}
/* buttons: NHS green primary, blue outline secondary */
.stButton button[kind="primary"]{background:var(--green); border:none; box-shadow:0 4px 0 var(--dgreen); border-radius:4px; font-weight:700;}
.stButton button[kind="primary"]:hover{background:#00662f;}
.stButton button[kind="secondary"]{border:2px solid var(--blue); color:var(--blue); border-radius:4px; font-weight:700; background:#fff;}
button:focus-visible{outline:3px solid var(--yellow) !important; box-shadow:0 4px 0 var(--text) !important;}
/* tabs */
.stTabs [data-baseweb="tab-list"]{gap:4px; border-bottom:1px solid var(--grey4);}
.stTabs [data-baseweb="tab"]{font-weight:700; color:var(--blue); padding:10px 14px;}
.stTabs [aria-selected="true"]{color:var(--text);}
/* sidebar device panel */
.spark{border:1px solid #76b90066; border-radius:10px; padding:12px 14px; background:linear-gradient(180deg,#76b90014,#fff); font-size:.85rem; color:var(--text);}
.spark .h{font-weight:700; color:#3f6212; margin-bottom:6px;}
.spark .r{display:flex; justify-content:space-between; padding:3px 0; border-bottom:1px dashed #76b90033;}
.spark .r:last-child{border:none;}
.spark .mem{height:6px; background:#e5e7eb; border-radius:3px; margin:6px 0 2px;} .spark .mem i{display:block; height:6px; background:var(--nv); border-radius:3px;}
.foot{margin-top:28px; padding:14px 0; border-top:1px solid var(--grey4); font-size:.82rem; color:var(--grey1);}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

STATUS_TAG = {"MET": ("Met", "t-green"), "NOT_MET": ("Not met", "t-grey"), "UNKNOWN": ("Needs check", "t-warm")}


def tag(text, cls):
    return f'<span class="tag {cls}">{html.escape(str(text))}</span>'


def md(s):
    st.markdown(s, unsafe_allow_html=True)


ss = st.session_state
ss.setdefault("ran", False)
ss.setdefault("decisions", {})

trials = data.get_trials()
stats = data.STATS

# ---------- Sidebar ----------
with st.sidebar:
    md('<div style="font-weight:700;font-size:1.15rem;color:#005eb8;margin-top:8px">🧬 TrialMatch</div>'
       '<div style="color:#4c6272;font-size:.85rem;margin-bottom:12px">Clinical trial recruitment assistant</div>')
    labels = [f"{t['id']} · {t['title']}" for t in trials]
    trial = trials[labels.index(st.selectbox("Trial", labels))]
    st.text_input("Or paste an NCT ID", placeholder="NCT0xxxxxxx")
    st.selectbox("Patient records", ["Demo GP practice · 200 synthetic patients"])
    go = st.button("Find eligible patients", type="primary", use_container_width=True)
    st.toggle("Replay cached run", value=True, help="Demo safety net: replays a pre-computed run.")
    md("<br>")
    mem = stats["gpu_mem_gb"] / 128 * 100
    md(f'<div class="spark"><div class="h">● Running on this DGX Spark</div>'
       f'<div class="r"><span>Host</span><b>GB10 · 128 GB</b></div>'
       f'<div class="r"><span>Endpoint</span><code>localhost:8000</code></div>'
       f'<div class="r"><span>Network egress</span><b>0 bytes</b></div>'
       f'<div style="margin-top:6px">Unified memory {stats["gpu_mem_gb"]} / 128 GB</div>'
       f'<div class="mem"><i style="width:{mem:.0f}%"></i></div>'
       f'<div style="color:#4c6272;font-size:.78rem;margin-top:6px">Patient notes never leave this machine.</div></div>')

# ---------- Top bar + title ----------
md('<div class="topbar"><span class="brand">TrialMatch</span><span class="sub">Find patients for clinical trials from GP records</span>'
   '<span class="pill"><span class="dot"></span>On-device · DGX Spark</span></div>')
md(f'<div class="crumbs">Trials &rsaquo; {trial["id"]}</div><h1 class="title">{html.escape(trial["title"])}</h1>'
   f'<div class="meta">{tag(trial["phase"], "t-blue")} {tag("Recruiting", "t-green")} &nbsp;{trial["sponsor"]} · target {trial["target"]} participants</div>')

criteria = data.get_criteria(trial["id"])
run = data.get_run(trial["id"])
crit_by_id = {c["id"]: c for c in criteria}
results = run["results"]

if go:
    with st.status("Agent running on DGX Spark…", expanded=True) as status:
        for step in run["trace"]:
            st.write(f"**{step['step']}. {step['agent']}** · `{step['tool']}`")
            time.sleep(0.6)
        status.update(label=f"Done in {stats['runtime_s']} s · {stats['notes_per_min']:,} notes/min · 0 bytes sent off-device",
                      state="complete", expanded=False)
    ss.ran = True

# ---------- KPI cards (shown after a run) ----------
if ss.ran:
    shortlisted = [r for r in results if not r.get("excluded")]
    approved = sum(1 for d in ss.decisions.values() if d == "approved")
    pending = sum(1 for r in shortlisted if r["patient_id"] not in ss.decisions)
    md('<div class="kpis">'
       f'<div class="kpi"><div class="l">Screened</div><div class="v">200</div><div class="s">patients · 684 notes</div></div>'
       f'<div class="kpi"><div class="l">Likely eligible</div><div class="v">12</div><div class="s">6% of practice list</div></div>'
       f'<div class="kpi"><div class="l">Awaiting review</div><div class="v">{pending}</div><div class="s">recruiter decisions</div></div>'
       f'<div class="kpi"><div class="l">Approved</div><div class="v">{approved}</div><div class="s">of target {trial["target"]}</div></div>'
       f'<div class="kpi nv"><div class="l">Sent off-device</div><div class="v">0 B</div><div class="s">{stats["notes_per_min"]:,} notes/min locally</div></div>'
       '</div>')

tab_crit, tab_trace, tab_list, tab_review = st.tabs(["Criteria", "Agent trace", "Shortlist", "Review queue"])

# ---------- 1. Criteria ----------
with tab_crit:
    left, right = st.columns([1, 1.3], gap="large")
    with left:
        md('<div class="card"><h3>Eligibility text</h3>'
           f'<div class="inset">{html.escape(trial["eligibility_text"]).replace(chr(10), "<br><br>")}</div>'
           '<div style="color:#4c6272;font-size:.8rem;margin-top:10px">Source: ClinicalTrials.gov (cached for offline use)</div></div>')
    with right:
        rows = ""
        for c in criteria:
            t = tag("Inclusion", "t-blue") if c["type"] == "inclusion" else tag("Exclusion", "t-red")
            k = tag("Record check", "t-purple") if c["kind"] == "structured" else tag("Read notes", "t-warm")
            rule = f' <code>{c["field"]} {c["op"]} {c["value"]}</code>' if c["kind"] == "structured" else ""
            rows += f'<div class="crow"><div class="cid">{c["id"]}</div><div class="ctext">{html.escape(c["text"])}{rule}<div style="margin-top:6px">{t}{k}</div></div></div>'
        md(f'<div class="card"><h3>What the criteria agent extracted</h3>{rows}'
           '<div style="color:#4c6272;font-size:.8rem;margin-top:10px">2 checked against the record with SQL, 6 need the GP notes read by the local model.</div></div>')

# ---------- 2. Trace ----------
with tab_trace:
    if not ss.ran:
        st.info("Press **Find eligible patients** to run the agent. Every step and tool call is shown here.")
    else:
        c1, c2 = st.columns([1.6, 1], gap="large")
        with c1:
            total = sum(s["ms"] for s in run["trace"])
            steps = ""
            for s in run["trace"]:
                model = tag(s["model"], "t-blue") if s["model"] else tag("No LLM", "t-grey")
                steps += (f'<div class="step"><div class="node">{s["step"]}</div><div class="hd"><span class="name">{s["agent"]}</span>'
                          f'<code>{s["tool"]}</code>{model}<span class="ms">{s["ms"]/1000:.2f} s</span></div>'
                          f'<div class="bar"><i style="width:{max(s["ms"]/total*100, 1.5):.1f}%"></i></div>'
                          f'<div class="dt">{html.escape(s["detail"])}</div></div>')
            md(f'<div class="card"><h3>Agent trace · {total/1000:.1f} s total</h3><div class="tl">{steps}</div></div>')
        with c2:
            top = run["funnel"][0][1]
            fr = "".join(f'<div class="fr"><div class="fl"><span>{l}</span><b>{n}</b></div><div class="fb"><i style="width:{max(n/top*100, 3):.0f}%"></i></div></div>'
                         for l, n in run["funnel"])
            md(f'<div class="card"><h3>Patient funnel</h3>{fr}'
               '<div style="color:#4c6272;font-size:.8rem;margin-top:10px">Cheap structured checks run first. Only remaining patients have their notes read.</div></div>')
            md('<div class="card"><h3>Models on this device</h3>'
               '<div class="summ"><div class="k">Criteria</div><div>Llama-3.3-70B FP8</div>'
               '<div class="k">Notes judge</div><div>Nemotron Nano 9B v2</div>'
               '<div class="k">Embeddings</div><div>bge-small</div>'
               '<div class="k">Serving</div><div>vLLM · localhost:8000</div></div></div>')

# ---------- 3. Shortlist ----------
with tab_list:
    if not ss.ran:
        st.info("Run the agent to build a shortlist.")
    else:
        show_excl = st.toggle("Show excluded patients", value=False)
        head = "".join(f"<th title='{html.escape(c['text'])}'>{c['id']}</th>" for c in criteria)
        body = ""
        for r in results:
            if r.get("excluded") and not show_excl:
                continue
            initials = "".join(w[0] for w in r["name"].replace(".", "").split())[:2]
            dots = ""
            for c in criteria:
                s = r["criteria"][c["id"]]["status"]
                cls = "EXCL" if (c["type"] == "exclusion" and s == "MET") else s
                sym = {"MET": "✓", "NOT_MET": "–", "UNKNOWN": "?", "EXCL": "✕"}[cls]
                dots += f'<span class="d-{cls}" title="{c["id"]}: {s}">{sym}</span>'
            d = ss.decisions.get(r["patient_id"])
            dec = (tag("Excluded", "t-red") if r.get("excluded") else
                   tag("Approved", "t-green") if d == "approved" else
                   tag("Rejected", "t-red") if d == "rejected" else tag("To review", "t-blue"))
            body += (f'<tr class="{"ex" if r.get("excluded") else ""}"><td><span class="av">{initials}</span>'
                     f'<span class="pname">{r["name"]}</span><div class="pid" style="margin-left:44px;margin-top:-8px">{r["patient_id"]} · {r["age"]}{r["sex"]} · BMI {r["bmi"]}</div></td>'
                     f'<td><div class="score"><b>{r["score"]:.2f}</b><div class="tr"><i style="width:{r["score"]*100:.0f}%"></i></div></div></td>'
                     f'<td class="dots" style="white-space:nowrap">{dots}</td><td style="max-width:360px">{html.escape(r["explanation"])}</td><td>{dec}</td></tr>')
        md(f'<div class="card" style="padding:6px 8px"><table class="sl"><thead><tr><th>Patient</th><th>Match</th>'
           f'<th>Criteria C1–C8</th><th>Why</th><th>Status</th></tr></thead><tbody>{body}</tbody></table></div>')
        md('<div style="color:#4c6272;font-size:.82rem">'
           '<span class="dots"><span class="d-MET">✓</span></span> met &nbsp; '
           '<span class="dots"><span class="d-NOT_MET">–</span></span> not met (for exclusions, that is good) &nbsp; '
           '<span class="dots"><span class="d-UNKNOWN">?</span></span> no evidence, needs a human check &nbsp; '
           '<span class="dots"><span class="d-EXCL">✕</span></span> exclusion found. '
           'Decision support only: a recruiter confirms every match.</div>')

# ---------- 4. Review queue (master-detail) ----------
with tab_review:
    if not ss.ran:
        st.info("Run the agent, then work through the review queue here.")
    else:
        queue = results[:4]
        qcol, dcol = st.columns([1, 2.6], gap="large")
        with qcol:
            md('<div class="qhead">Review queue</div>')

            def qlabel(r):
                d = ss.decisions.get(r["patient_id"])
                mark = "⛔" if r.get("excluded") else "✅" if d == "approved" else "❌" if d == "rejected" else "●"
                return f'{mark}  {r["name"]} · {r["score"]:.2f}'

            idx = st.radio("queue", range(len(queue)), format_func=lambda i: qlabel(queue[i]), label_visibility="collapsed")
            r = queue[idx]
        with dcol:
            a, b = st.columns([1.7, 1], gap="medium")
            with a:
                md(f'<h1 class="title" style="font-size:1.5rem">{r["name"]} <span style="color:#4c6272;font-weight:400;font-size:1rem">{r["patient_id"]}</span></h1>')
                md('<div class="summ">'
                   f'<div class="k">Age, sex</div><div>{r["age"]}, {r["sex"]}</div>'
                   f'<div class="k">BMI</div><div>{r["bmi"]}</div>'
                   f'<div class="k">Match score</div><div>{r["score"]:.2f}</div>'
                   f'<div class="k">Summary</div><div>{html.escape(r["explanation"])}</div></div>')
            with b:
                d = ss.decisions.get(r["patient_id"])
                if r.get("excluded"):
                    md('<div class="decision no">Excluded by agent</div>')
                elif d == "approved":
                    md('<div class="decision ok">✓ Approved for contact</div>')
                elif d == "rejected":
                    md('<div class="decision no">✕ Rejected</div>')
                else:
                    md('<div class="decision">Awaiting your decision</div>')
                if st.button("Approve for contact", key=f"ap_{r['patient_id']}", type="primary", use_container_width=True):
                    ss.decisions[r["patient_id"]] = "approved"; st.rerun()
                if st.button("Reject", key=f"rj_{r['patient_id']}", use_container_width=True):
                    ss.decisions[r["patient_id"]] = "rejected"; st.rerun()
                st.text_input("Note for the audit log", key=f"why_{r['patient_id']}", placeholder="e.g. confirm ECOG on call")

            md('<h3 style="font-size:1.1rem;margin:22px 0 10px">Evidence for each criterion</h3>')
            for cid, ev in r["criteria"].items():
                c = crit_by_id[cid]
                s = ev["status"]
                bad = c["type"] == "exclusion" and s == "MET"
                t = tag("Inclusion", "t-blue") if c["type"] == "inclusion" else tag("Exclusion", "t-red")
                st_tag = tag("Excludes patient", "t-red") if bad else tag(*STATUS_TAG[s])
                body = ""
                if ev.get("note") and ev.get("quote"):
                    q = html.escape(ev["quote"])
                    note = html.escape(ev["note"]).replace(q, f"<mark>{q}</mark>")
                    body = f'<div class="src">📄 {ev["source"]} · quoted verbatim</div><div class="inset">{note}</div>'
                elif ev.get("source"):
                    body = f'<div class="src">🗂 {html.escape(ev["source"])}</div>'
                if s == "UNKNOWN":
                    body += '<div class="warn"><b>Important:</b> no verbatim evidence in the notes. Ask the patient or check hospital letters before enrolling.</div>'
                md(f'<div class="ev {"bad" if bad else ""}"><div class="row1"><span class="cid">{cid}</span>'
                   f'<span class="ct">{html.escape(c["text"])}</span>{t}{st_tag}<span class="conf">conf. {ev["confidence"]:.2f}</span></div>{body}</div>')

md(f'<div class="foot">Models: {stats["model"]} on {stats["host"]} · <b>0 bytes sent off-device</b> · '
   f'{stats["notes_per_min"]:,} notes/min · Synthetic data only. TrialMatch is decision support for recruiters, not an eligibility decision.</div>')
