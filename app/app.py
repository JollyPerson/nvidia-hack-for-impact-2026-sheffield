"""TrialMatch UI mockup. Run: streamlit run app/app.py

All data comes from mock_data.py. Swap those functions for the real pipeline later.

Styled to the NHS design system (service-manual.nhs.uk/design-system): colour palette and roles,
typography (Frutiger, Arial fallback, 19px body), responsive spacing scale, and components
(header, breadcrumbs, buttons, tags, tables, summary list, inset text, warning callout, cards,
details, task list, tabs, footer). The NHS logo is not used and no NHS endorsement is implied.
"""
import html
import time

import streamlit as st

import mock_data as data

st.set_page_config(page_title="TrialMatch", page_icon=None, layout="wide")

# Colour values: service-manual.nhs.uk/design-system/styles/colour
# Spacing (desktop): 4 8 16 24 32 40 48 56 64. Type scale: 48 36 26 22 19 16 14.
CSS = """
<style>
:root{
--text:#212b32; --secondary:#4c6272; --link:#005eb8; --link-hover:#7c2855; --link-visited:#330072; --link-active:#002f5c;
--focus:#ffeb3b; --focus-text:#212b32; --border:#d8dde0; --input-border:#4c6272; --error:#d5281b; --success:#007f3b;
--button:#007f3b; --button-shadow:#004c23; --sec-button:#005eb8; --bg:#f0f4f5; --white:#ffffff;
--blue:#005eb8; --dark-blue:#003087; --pale-blue:#ccdff1; --green:#007f3b; --dark-green:#004c23; --pale-green:#cce5d8;
--red:#d5281b; --dark-red:#801810; --pale-red:#f7d4d1; --yellow:#ffeb3b; --pale-yellow:#fff9c4;
--warm-yellow:#ffb81c; --dark-warm-yellow:#664a0b; --pale-warm-yellow:#fff1d2;
--aqua:#00a499; --dark-aqua:#004a45; --pale-aqua:#bae6e3; --purple:#5c338e; --dark-purple:#330072; --pale-purple:#ded6e8;
--grey-1:#4c6272; --grey-2:#768692; --grey-3:#aeb7bd; --grey-4:#d8dde0; --grey-5:#f0f4f5;
}
html, body, .stApp, .stMarkdown, .stMarkdown *, button, input, select, textarea, label, label p{font-family:"Frutiger W01", Arial, sans-serif;}
[data-testid="stIconMaterial"], .material-symbols-rounded{font-family:"Material Symbols Rounded" !important;}
.stApp{background:var(--bg); color:var(--text);}
[data-testid="stHeader"]{display:none;}
.block-container{padding:0 32px 48px !important; max-width:1280px;}
.stMarkdown p, .stMarkdown li{font-size:19px; line-height:28px;}

/* Header */
.nhs-header{background:var(--blue); margin:0 -32px; padding:16px 32px; display:flex; align-items:center; gap:24px; flex-wrap:wrap;}
.nhs-header__service{color:var(--white); font-size:22px; line-height:30px; font-weight:700;}
.nhs-header__org{color:var(--white); font-size:16px; line-height:24px;}
.nhs-header__status{margin-left:auto; background:var(--white); color:var(--dark-aqua); border:1px solid var(--aqua); font-size:16px; font-weight:700; padding:4px 8px;}
.nhs-header__nav{background:var(--blue); margin:0 -32px; padding:0 32px 8px; border-top:1px solid var(--pale-blue);}
.nhs-header__nav span{display:inline-block; color:var(--white); font-size:16px; padding:8px 24px 0 0; font-weight:700;}

/* Breadcrumbs */
.nhs-breadcrumb{font-size:16px; line-height:24px; padding:16px 0 0; color:var(--text);}
.nhs-breadcrumb a, .nhs-link{color:var(--link); text-decoration:underline;}
.nhs-breadcrumb .chev{color:var(--grey-2); margin:0 8px;}

/* Type */
.nhs-caption-l{color:var(--secondary); font-size:22px; line-height:30px; margin-top:24px;}
.stApp .nhs-heading-l, .stApp .nhs-heading-m, .stApp .nhs-heading-s, .stApp .stApp .nhs-heading-xs{padding:0 !important; letter-spacing:0; font-family:"Frutiger W01", Arial, sans-serif;}
.stApp .nhs-heading-l{font-size:36px; line-height:42px; font-weight:700; margin:4px 0 16px; color:var(--text);}
.stApp .nhs-heading-m{font-size:26px; line-height:32px; font-weight:700; margin:0 0 16px; color:var(--text);}
.stApp .nhs-heading-s{font-size:22px; line-height:30px; font-weight:700; margin:0 0 16px; color:var(--text);}
.stApp .nhs-heading-xs{font-size:19px; line-height:28px; font-weight:700; margin:0 0 8px; color:var(--text);}
.nhs-body{font-size:19px; line-height:28px; color:var(--text); margin:0 0 16px;}
.nhs-body-s{font-size:16px; line-height:24px; color:var(--text); margin:0 0 16px;}
.nhs-secondary{color:var(--secondary);}
a{color:var(--link);} a:visited{color:var(--link-visited);} a:hover{color:var(--link-hover);} a:active{color:var(--link-active);}

/* Tags */
.nhs-tag{display:inline-block; white-space:nowrap; font-size:14px; line-height:20px; font-weight:700; padding:4px 8px; border:1px solid; margin:0 4px 4px 0;}
.tag-blue{background:var(--pale-blue); color:var(--dark-blue); border-color:var(--blue);}
.tag-white{background:var(--white); color:var(--text); border-color:var(--text);}
.tag-grey{background:var(--grey-5); color:var(--grey-1); border-color:var(--grey-2);}
.tag-green{background:var(--pale-green); color:var(--dark-green); border-color:var(--green);}
.tag-aqua{background:var(--pale-aqua); color:var(--dark-aqua); border-color:var(--aqua);}
.tag-purple{background:var(--pale-purple); color:var(--dark-purple); border-color:var(--purple);}
.tag-red{background:var(--pale-red); color:var(--dark-red); border-color:var(--red);}
.tag-yellow{background:var(--pale-warm-yellow); color:var(--dark-warm-yellow); border-color:var(--warm-yellow);}

/* Cards */
.nhs-card{background:var(--white); border:1px solid var(--border); padding:32px; margin-bottom:32px;}
.nhs-card--feature{position:relative; margin-top:24px; padding-top:40px;}
.nhs-card--feature .nhs-card__tab{position:absolute; top:-20px; left:32px; background:var(--blue); color:var(--white); font-size:22px; line-height:30px; font-weight:700; padding:4px 24px;}
.nhs-card--stat{padding:24px; margin-bottom:24px; height:100%;}
.nhs-card--stat .label{font-size:19px; line-height:28px; color:var(--text); font-weight:700;}
.nhs-card--stat .value{font-size:36px; line-height:42px; white-space:nowrap; font-weight:700; color:var(--text);}
.nhs-card--stat .hint{font-size:16px; line-height:24px; color:var(--secondary);}
.nhs-card--device{border-top:4px solid var(--aqua);}
.stats{display:grid; grid-template-columns:repeat(5,1fr); gap:24px; margin:24px 0 8px;}

/* Inset text */
.nhs-inset{border-left:8px solid var(--blue); padding:16px 24px; margin:16px 0 24px; background:var(--white); font-size:19px; line-height:28px; color:var(--text);}
.nhs-inset p{margin:0 0 16px;} .nhs-inset p:last-child{margin:0;}
.nhs-inset mark{background:var(--pale-yellow); color:var(--text); padding:0 2px; box-shadow:0 2px 0 var(--warm-yellow);}

/* Warning callout */
.nhs-warning{background:var(--pale-yellow); border:1px solid var(--yellow); margin:16px 0 0; padding:0 24px 16px; position:relative;}
.nhs-warning__label{display:inline-block; background:var(--yellow); color:var(--text); font-size:19px; line-height:28px; font-weight:700; padding:4px 24px; margin:0 0 16px -24px;}
.nhs-warning p{font-size:19px; line-height:28px; margin:0;}

/* Error summary (used for exclusions) */
.nhs-error-summary{border:4px solid var(--error); background:var(--white); padding:24px; margin-bottom:24px;}
.nhs-error-summary h2{font-size:22px; line-height:30px; margin:0 0 8px; color:var(--text);}
.nhs-error-summary p{font-size:19px; line-height:28px; margin:0; color:var(--error); font-weight:700;}

/* Summary list */
.nhs-summary{width:100%; border-collapse:collapse; margin:0 0 24px; font-size:19px; line-height:28px;}
.nhs-summary th{text-align:left; font-weight:700; width:35%; padding:8px 16px 8px 0; border-bottom:1px solid var(--border); vertical-align:top;}
.nhs-summary td{padding:8px 0; border-bottom:1px solid var(--border); vertical-align:top;}

/* Table */
.nhs-table{width:100%; border-collapse:collapse; background:var(--white); font-size:19px; line-height:28px;}
.nhs-table caption{text-align:left; font-size:22px; line-height:30px; font-weight:700; padding-bottom:16px; color:var(--text);}
.nhs-table th{text-align:left; font-weight:700; padding:8px 16px 8px 0; border-bottom:2px solid var(--grey-1); vertical-align:bottom;}
.nhs-table td{padding:16px 16px 16px 0; border-bottom:1px solid var(--border); vertical-align:top;}
.nhs-table td.num, .nhs-table th.num{text-align:right; font-variant-numeric:tabular-nums;}
.nhs-table tr.excluded td:first-child{box-shadow:inset 4px 0 0 var(--error); padding-left:12px;}
.marks span{display:inline-block; min-width:28px; text-align:center; font-size:14px; line-height:20px; font-weight:700; padding:2px 0; border:1px solid; margin:0 2px 2px 0;}
.m-met{background:var(--pale-green); color:var(--dark-green); border-color:var(--green);}
.m-not{background:var(--white); color:var(--grey-1); border-color:var(--grey-3);}
.m-unk{background:var(--pale-warm-yellow); color:var(--dark-warm-yellow); border-color:var(--warm-yellow);}
.m-exc{background:var(--pale-red); color:var(--dark-red); border-color:var(--red);}

/* Task list (agent steps) */
.nhs-task-list{list-style:none; padding:0; margin:0 0 24px; border-top:1px solid var(--border);}
.nhs-task-list li{border-bottom:1px solid var(--border); padding:16px 0;}
.nhs-task-list .row{display:flex; gap:16px; align-items:baseline; flex-wrap:wrap;}
.nhs-task-list .name{font-size:19px; line-height:28px; font-weight:700; flex:1; min-width:220px;}
.nhs-task-list .time{font-size:16px; color:var(--secondary); font-variant-numeric:tabular-nums;}
.nhs-task-list .bar{height:8px; background:var(--grey-5); margin:8px 0;} .nhs-task-list .bar i{display:block; height:8px; background:var(--blue);}

/* Details */
details.nhs-details{margin:8px 0 0;}
details.nhs-details summary{color:var(--link); text-decoration:underline; cursor:pointer; font-size:19px; line-height:28px;}
details.nhs-details summary:hover{color:var(--link-hover);}
details.nhs-details .nhs-details__text{border-left:4px solid var(--border); padding:16px 24px; margin-top:8px; font-size:19px; line-height:28px;}
details.nhs-details code, .nhs-code{font-family:monospace; font-size:16px; background:var(--grey-5); padding:0 4px; color:var(--text);}

/* Funnel chart (palette colours used directly for graph series) */
.funnel .fr{margin:0 0 16px;} .funnel .fl{display:flex; justify-content:space-between; font-size:19px; line-height:28px;}
.funnel .fb{height:16px; background:var(--grey-5);} .funnel .fb i{display:block; height:16px; background:var(--blue);}

/* Buttons */
.stButton button{font-size:19px !important; font-weight:700 !important; border-radius:4px !important; padding:12px 16px !important; min-height:auto !important;}
.stButton button p{font-size:19px !important; font-weight:700 !important;}
.stButton button[kind="primary"]{background:var(--button) !important; color:var(--white) !important; border:2px solid transparent !important; box-shadow:0 4px 0 var(--button-shadow) !important;}
.stButton button[kind="primary"]:hover{background:var(--dark-green) !important;}
.stButton button[kind="secondary"]{background:var(--white) !important; color:var(--sec-button) !important; border:2px solid var(--sec-button) !important; box-shadow:0 4px 0 var(--sec-button) !important;}
.stButton button:active{transform:translateY(4px); box-shadow:none !important;}
.stButton button:focus-visible{background:var(--focus) !important; color:var(--focus-text) !important; box-shadow:0 4px 0 var(--focus-text) !important; outline:none !important;}
*:focus-visible{outline:4px solid var(--focus) !important; outline-offset:0 !important; box-shadow:0 4px 0 var(--focus-text) !important;}

/* Form fields */
.stTextInput label p, .stNumberInput label p, .stSelectbox label p, .stRadio > label p, .stToggle label p{font-size:19px !important; color:var(--text) !important;}
[data-baseweb="input"], [data-baseweb="select"] > div{border:2px solid var(--input-border) !important; border-radius:0 !important; background:var(--white) !important;}
[data-baseweb="input"]:focus-within, [data-baseweb="select"] > div:focus-within{outline:4px solid var(--focus) !important; box-shadow:inset 0 0 0 2px var(--text) !important;}
input{background:var(--white) !important; color:var(--text) !important; font-size:19px !important;}
[data-baseweb="select"] div{font-size:19px !important; color:var(--text) !important;}
.stRadio [role="radiogroup"] label{padding:8px 0 !important;}
.stRadio [role="radiogroup"] label p{font-size:19px !important;}

/* Labels, hints, textarea (NHS form patterns) */
.nhs-label{display:block; font-size:19px; line-height:28px; font-weight:700; color:var(--text); margin:24px 0 4px;}
.nhs-hint{font-size:19px; line-height:28px; color:var(--secondary); margin:0 0 8px;}
.nhs-count{font-size:16px; color:var(--secondary); margin:4px 0 24px;}
[data-baseweb="textarea"]{border:2px solid var(--input-border) !important; border-radius:0 !important; background:var(--white) !important;}
[data-baseweb="textarea"]:focus-within{outline:4px solid var(--focus) !important; box-shadow:inset 0 0 0 2px var(--text) !important;}
textarea{font-size:19px !important; line-height:28px !important; color:var(--text) !important; background:var(--white) !important;}
.from mark{background:var(--pale-yellow); color:var(--text); padding:0 2px; box-shadow:0 2px 0 var(--warm-yellow);}
.from sup{font-size:14px; font-weight:700; color:var(--dark-blue); margin-left:2px;}
.nhs-warning ul{margin:0; padding-left:24px;} .nhs-warning li{font-size:19px; line-height:28px; margin-bottom:8px;}
.nhs-warning h3{font-size:22px; line-height:30px; margin:0 0 8px; padding:0 !important;}
.nhs-code{display:block; white-space:pre; padding:16px; border-left:4px solid var(--border); overflow-x:auto; font-size:15px; line-height:22px;}
ol.nhs-steps{padding-left:24px; margin:0;} ol.nhs-steps li{font-size:19px; line-height:28px; margin-bottom:8px;}
.stDownloadButton button{background:var(--white) !important; color:var(--sec-button) !important; border:2px solid var(--sec-button) !important; box-shadow:0 4px 0 var(--sec-button) !important; border-radius:4px !important; font-weight:700 !important;}
.stDownloadButton button p{font-size:19px !important; font-weight:700 !important;}
[data-testid="stExpander"] summary p{font-size:19px !important; color:var(--link); text-decoration:underline;}

/* Tabs (NHS tabs pattern) */
.stTabs [role="tab"] *{font-size:19px !important;}
[data-testid="stTextInputRootElement"]{border:2px solid var(--input-border) !important; border-radius:0 !important;}
[data-testid="stTextInputRootElement"] [data-baseweb="input"]{border:none !important;}
.stTabs [data-baseweb="tab-list"]{gap:0; border-bottom:1px solid var(--border);}
.stApp table th, .stApp table td{border-left:none !important; border-right:none !important; border-top:none !important; background:transparent;}
.stApp table{border:none !important;}
.stTabs [data-baseweb="tab"], .stTabs button[role="tab"]{font-size:19px !important; color:var(--link); text-decoration:underline; padding:8px 24px; background:var(--grey-5); border:1px solid transparent; margin-right:4px;}
.stTabs [data-baseweb="tab"] p, .stTabs button[role="tab"] p{font-size:19px !important;}
.stTabs [data-baseweb="tab"]:hover{color:var(--link-hover);}
.stTabs [aria-selected="true"]{color:var(--text) !important; text-decoration:none; background:var(--white); border-color:var(--border); border-bottom-color:var(--white); margin-bottom:-1px;}
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"]{display:none;}
.stTabs [data-baseweb="tab-panel"]{padding-top:32px;}

/* Sidebar */
[data-testid="stSidebar"]{background:var(--white); border-right:1px solid var(--border);}
[data-testid="stSidebar"] .nhs-card{padding:16px; margin-bottom:16px;}
.device th, .device td{font-size:16px !important; line-height:24px !important;}
.meter{height:8px; background:var(--grey-4); margin:8px 0;} .meter i{display:block; height:8px; background:var(--aqua);}

/* Status (Streamlit) */
[data-testid="stExpander"] details, [data-testid="stStatusWidget"]{border-radius:0 !important; border-color:var(--border) !important; background:var(--white);}

/* Footer */
.nhs-footer{background:var(--grey-4); margin:48px -32px 0; padding:24px 32px; font-size:16px; line-height:24px; color:var(--text);}
.nhs-footer ul{list-style:none; padding:0; margin:0 0 16px; display:flex; gap:24px; flex-wrap:wrap;}
.nhs-footer li{color:var(--link); text-decoration:underline;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

STATUS = {"MET": ("Met", "tag-green"), "NOT_MET": ("Not met", "tag-grey"), "UNKNOWN": ("Not known", "tag-yellow")}


def md(s):
    st.markdown(s, unsafe_allow_html=True)


def tag(text, cls):
    return f'<strong class="nhs-tag {cls}">{html.escape(str(text))}</strong>'


def type_tag(c):
    return tag("Inclusion", "tag-blue") if c["type"] == "inclusion" else tag("Exclusion", "tag-red")


def method_tag(c):
    return tag("Patient record", "tag-purple") if c["kind"] == "structured" else tag("GP notes", "tag-white")


def rule_text(c):
    if c["kind"] != "structured":
        return ""
    v = c["value"]
    field = "BMI" if c["field"] == "bmi" else c["field"].capitalize()
    return f"{field} {v[0]} to {v[1]}" if c["op"] == "between" else f"{field} {c['op']} {v}"


ss = st.session_state
ss.setdefault("ran", False)
ss.setdefault("decisions", {})
ss.setdefault("page", "trial")
ss.setdefault("my_trials", [])
ss.setdefault("draft", None)

trials = ss.my_trials + data.get_trials()
stats = data.STATS

# ---------- Sidebar: search form ----------
with st.sidebar:
    md('<h2 class="nhs-heading-s" style="margin-top:8px">Your trials</h2>')
    labels = [f"{t['id']}: {t['title']}" for t in trials]
    if "pending_pick" in ss:
        ss.trial_pick = ss.pop("pending_pick")
    if ss.get("trial_pick") not in labels:
        ss.trial_pick = labels[0]

    def _picked():
        ss.page = "trial"
        ss.ran = False

    trial = trials[labels.index(st.selectbox("Trial", labels, key="trial_pick", on_change=_picked))]
    st.selectbox("Patient records", ["Demo GP practice (200 synthetic patients)"])
    go = st.button("Find eligible patients", type="primary", use_container_width=True, disabled=ss.page == "create")
    if st.button("Create a new trial", use_container_width=True):
        ss.page = "create"
        ss.draft = None
        st.rerun()
    st.toggle("Use saved demo run", value=True, help="Replays a run saved earlier, in case the live run is slow.")
    mem = stats["gpu_mem_gb"] / 128 * 100
    md('<div class="nhs-card nhs-card--device" style="margin-top:24px">'
       '<h3 class="nhs-heading-xs">Running on this DGX Spark</h3>'
       '<table class="nhs-summary device">'
       '<tr><th>Device</th><td>GB10, 128 GB</td></tr>'
       '<tr><th>Model server</th><td>On this device</td></tr>'
       '<tr><th>Data sent out</th><td>0 bytes</td></tr></table>'
       f'<p class="nhs-body-s" style="margin:0">Memory in use: {stats["gpu_mem_gb"]} GB of 128 GB</p>'
       f'<div class="meter"><i style="width:{mem:.0f}%"></i></div>'
       '<p class="nhs-body-s nhs-secondary" style="margin:0">Patient notes never leave this device.</p></div>')

# ---------- Header ----------
md('<header class="nhs-header"><span class="nhs-header__service">TrialMatch</span>'
   '<span class="nhs-header__org">Clinical trial recruitment</span>'
   '<span class="nhs-header__status">Running on this device</span></header>'
   '<nav class="nhs-header__nav"><span>Your trials</span><span>Create a trial</span><span>Review queue</span><span>How it works</span></nav>')


def footer():
    md('<footer class="nhs-footer"><ul><li>How TrialMatch works</li><li>Data and privacy</li><li>Accessibility</li></ul>'
       f'<p style="margin:0">Models run on {stats["host"]}. No patient data leaves this device. All patient data shown is synthetic. '
       'TrialMatch supports recruiters. It does not decide who is eligible.</p></footer>')


def highlight_description(text, crits):
    out = html.escape(text)
    for c in crits:
        ph = html.escape(c["from"])
        if ph in out:
            out = out.replace(ph, f'<mark>{ph}</mark><sup>{c["id"]}</sup>', 1)
    return out


# ---------- Create a trial ----------
if ss.page == "create":
    md('<nav class="nhs-breadcrumb"><a href="#">Your trials</a><span class="chev">›</span>Create a trial</nav>'
       '<div class="nhs-caption-l">New trial</div><h1 class="nhs-heading-l">Create a trial</h1>')
    main, side = st.columns([2, 1], gap="large")
    with side:
        md('<div class="nhs-card nhs-card--device"><h2 class="nhs-heading-s">What happens next</h2><ol class="nhs-steps">'
           '<li>The agent turns your description into a list of criteria.</li>'
           '<li>You check each criterion and the assumptions it made.</li>'
           '<li>You save the trial and TrialMatch searches your patients.</li>'
           '<li>You review every patient before anyone is contacted.</li></ol>'
           '<p class="nhs-body-s nhs-secondary" style="margin:16px 0 0">Everything you type stays on this device.</p></div>')
    with main:
        name = st.text_input("Trial name", value="Early lung cancer immunotherapy study")
        source = st.radio("How do you want to add the criteria?", ["Describe who you need", "Import from ClinicalTrials.gov"])
        if source == "Import from ClinicalTrials.gov":
            st.text_input("ClinicalTrials.gov ID", placeholder="For example, NCT01234567",
                          help="The ID starts with NCT followed by 8 numbers.")
            text = data.TRIALS[0]["eligibility_text"]
        else:
            md('<label class="nhs-label">Describe who you need</label>'
               '<p class="nhs-hint">Write it as you would to a colleague. Include age, conditions, treatments, '
               'tests, and anything that would rule someone out.</p>')
            text = st.text_area("Describe who you need", value=data.CREATE_EXAMPLE, height=200, label_visibility="collapsed")
            md(f'<p class="nhs-count">You have used {len(text)} characters</p>')
        target = st.number_input("How many participants do you need?", min_value=1, value=40, step=1)
        if st.button("Turn into criteria", type="primary"):
            ss.draft = {"name": name, "text": text, "target": int(target), "criteria": data.parse_description(text)}
            st.rerun()

        if ss.draft:
            d = ss.draft
            crits = d["criteria"]
            md('<h2 class="nhs-heading-m" style="margin-top:32px">Check the criteria</h2>'
               f'<p class="nhs-body">The agent found {len(crits)} criteria in your description. '
               'The highlighted words show where each one came from.</p>'
               f'<div class="nhs-inset from">{highlight_description(d["text"], crits)}</div>')
            asm = [c for c in crits if c.get("assumption")]
            if asm:
                md('<div class="nhs-warning"><span class="nhs-warning__label">Important</span>'
                   f'<h3>Check {len(asm)} assumptions</h3><ul>'
                   + "".join(f'<li><strong>{c["id"]}:</strong> {html.escape(c["assumption"])}</li>' for c in asm)
                   + '</ul></div>')
            rows = "".join(
                f'<tr><td><strong>{c["id"]}</strong></td><td>{html.escape(c["text"])}'
                f'{"<br><span class=nhs-secondary style=font-size:16px>Rule: " + rule_text(c) + "</span>" if rule_text(c) else ""}</td>'
                f'<td>{type_tag(c)}</td><td>{method_tag(c)}</td></tr>' for c in crits)
            md('<table class="nhs-table" style="margin-top:32px"><caption>Criteria for this trial</caption>'
               '<thead><tr><th>ID</th><th>Criterion</th><th>Type</th><th>Checked using</th></tr></thead>'
               f'<tbody>{rows}</tbody></table>')
            with st.expander("Change or add criteria"):
                import pandas as pd
                df = pd.DataFrame([{"ID": c["id"], "Criterion": c["text"],
                                    "Type": c["type"].capitalize(),
                                    "Checked using": "Patient record" if c["kind"] == "structured" else "GP notes"} for c in crits])
                st.data_editor(df, num_rows="dynamic", hide_index=True, use_container_width=True, column_config={
                    "Type": st.column_config.SelectboxColumn("Type", options=["Inclusion", "Exclusion"]),
                    "Checked using": st.column_config.SelectboxColumn("Checked using", options=["Patient record", "GP notes"])})
            b1, b2, _ = st.columns([1.4, 1, 1])
            if b1.button("Save trial and find patients", type="primary", use_container_width=True):
                new = {"id": f"LOCAL-{len(ss.my_trials) + 1:04d}", "title": d["name"], "sponsor": "Created on this device",
                       "phase": "Not registered", "target": d["target"], "eligibility_text": d["text"], "local": True}
                ss.my_trials.insert(0, new)
                ss.pending_pick = f"{new['id']}: {new['title']}"
                ss.page, ss.ran, ss.autorun, ss.draft = "trial", False, True, None
                st.rerun()
            if b2.button("Start again", use_container_width=True):
                ss.draft = None
                st.rerun()
    footer()
    st.stop()

if ss.pop("autorun", False):
    go = True

md(f'<nav class="nhs-breadcrumb"><a href="#">Your trials</a><span class="chev">›</span>{trial["id"]}</nav>'
   f'<div class="nhs-caption-l">{trial["id"]}</div>'
   f'<h1 class="nhs-heading-l">{html.escape(trial["title"])}</h1>'
   f'<p class="nhs-body">{tag(trial["phase"], "tag-blue")}{tag("Recruiting", "tag-green")} '
   f'{"Created on this device" if trial.get("local") else "Sponsor: " + trial["sponsor"]}. Aim: {trial["target"]} participants.</p>')

criteria = data.get_criteria(trial["id"])
run = data.get_run(trial["id"])
crit_by_id = {c["id"]: c for c in criteria}
results = run["results"]

if go:
    with st.status("Checking patients on this device", expanded=True) as status:
        for step in run["trace"]:
            st.write(f"Step {step['step']} of 5: {step['agent']}")
            time.sleep(0.6)
        status.update(label=f"Finished in {stats['runtime_s']} seconds. No data left this device.", state="complete", expanded=False)
    ss.ran = True

# ---------- Summary cards ----------
if ss.ran:
    shortlisted = [r for r in results if not r.get("excluded")]
    approved = sum(1 for d in ss.decisions.values() if d == "approved")
    pending = sum(1 for r in shortlisted if r["patient_id"] not in ss.decisions)
    cards = [("Patients checked", "200", "684 GP notes read"), ("Likely eligible", "12", "6% of patients"),
             ("To review", str(pending), "Waiting for a decision"), ("Approved", str(approved), f"Aim is {trial['target']}"),
             ("Data sent out", "0 bytes", f"{stats['notes_per_min']:,} notes a minute")]
    md('<div class="stats">' + "".join(
        f'<div class="nhs-card nhs-card--stat{" nhs-card--device" if i == 4 else ""}"><div class="label">{l}</div>'
        f'<div class="value">{v}</div><div class="hint">{h}</div></div>' for i, (l, v, h) in enumerate(cards)) + '</div>')

tab_crit, tab_trace, tab_list, tab_review = st.tabs(["Criteria", "Agent steps", "Shortlist", "Review queue"])

# ---------- 1. Criteria ----------
with tab_crit:
    left, right = st.columns([1, 1.4], gap="large")
    with left:
        if trial.get("local"):
            md('<h2 class="nhs-heading-m">Your description</h2>'
               f'<div class="nhs-inset from">{highlight_description(trial["eligibility_text"], data.parse_description(trial["eligibility_text"]))}</div>'
               '<p class="nhs-body-s nhs-secondary">Written by you when you created this trial.</p>')
        else:
            md('<h2 class="nhs-heading-m">Eligibility text</h2>'
               '<div class="nhs-inset">' + "".join(f"<p>{html.escape(p)}</p>" for p in trial["eligibility_text"].split("\n")) + '</div>'
               '<p class="nhs-body-s nhs-secondary">From ClinicalTrials.gov. Saved on this device so the demo works offline.</p>')
    with right:
        rows = "".join(
            f'<tr><td><strong>{c["id"]}</strong></td><td>{html.escape(c["text"])}'
            f'{"<br><span class=nhs-secondary style=font-size:16px>Rule: " + rule_text(c) + "</span>" if rule_text(c) else ""}</td>'
            f'<td>{type_tag(c)}</td><td>{method_tag(c)}</td></tr>' for c in criteria)
        md('<table class="nhs-table"><caption>Criteria found by the agent</caption>'
           '<thead><tr><th>ID</th><th>Criterion</th><th>Type</th><th>Checked using</th></tr></thead>'
           f'<tbody>{rows}</tbody></table>'
           '<p class="nhs-body-s nhs-secondary" style="margin-top:16px">2 criteria are checked against the patient record. '
           '6 need the GP notes to be read by the model on this device.</p>')

# ---------- 2. Agent steps ----------
with tab_trace:
    if not ss.ran:
        md('<div class="nhs-inset">Select <strong>Find eligible patients</strong> to start. Each step the agent takes will be shown here.</div>')
    else:
        c1, c2 = st.columns([1.5, 1], gap="large")
        with c1:
            total = sum(s["ms"] for s in run["trace"])
            items = ""
            for s in run["trace"]:
                model = f'<code>{s["model"]}</code>' if s["model"] else "none (no AI model used)"
                items += (f'<li><div class="row"><span class="name">{s["step"]}. {s["agent"]}</span>'
                          f'<span class="time">{s["ms"]/1000:.2f} seconds · {s["tokens"]}</span>{tag("Completed", "tag-green")}</div>'
                          f'<div class="bar"><i style="width:{max(s["ms"]/total*100, 1):.1f}%"></i></div>'
                          f'<p class="nhs-body" style="margin:0">{html.escape(s["detail"])}</p>'
                          f'<details class="nhs-details"><summary>Show technical details</summary><div class="nhs-details__text">'
                          f'Tool: <code>{s["tool"]}</code><br>Model: {model}</div></details></li>')
            md(f'<h2 class="nhs-heading-m">Agent steps</h2><p class="nhs-body nhs-secondary">All 5 steps finished in {total/1000:.1f} seconds.</p>'
               f'<ol class="nhs-task-list">{items}</ol>')
        with c2:
            top = run["funnel"][0][1]
            fr = "".join(f'<div class="fr"><div class="fl"><span>{l}</span><strong>{n}</strong></div>'
                         f'<div class="fb"><i style="width:{max(n/top*100, 2):.0f}%"></i></div></div>' for l, n in run["funnel"])
            md(f'<div class="nhs-card"><h2 class="nhs-heading-s">How patients were narrowed down</h2><div class="funnel">{fr}</div>'
               '<p class="nhs-body-s nhs-secondary" style="margin:0">Quick record checks run first, so only the remaining patients have their notes read.</p></div>')
            md('<div class="nhs-card nhs-card--device"><h2 class="nhs-heading-s">Models on this device</h2><table class="nhs-summary" style="margin:0">'
               '<tr><th>LLM 2: criteria and shortlist</th><td>gpt-oss-120b (mixture of experts)</td></tr>'
               '<tr><th>LLM 1: screening notes</th><td>Nemotron Nano 9B v2</td></tr>'
               '<tr><th>Data warehouse</th><td>DuckDB, read only</td></tr><tr><th>Server</th><td>vLLM, local only</td></tr></table></div>')
        sqlf = "".join(f'<tr><td>{html.escape(l)}</td><td class="num">{n}</td></tr>' for l, n in run["sql_funnel"])
        md('<div class="nhs-card"><h2 class="nhs-heading-s">SQL query sent to the data warehouse</h2>'
           '<p class="nhs-body">The criteria that can be checked against the patient record become a read-only, '
           'parameterised query. The AI model does not write SQL.</p>'
           f'<code class="nhs-code">{html.escape(run["sql"])}</code>'
           f'<table class="nhs-table" style="margin-top:24px"><caption>Patients left after each check</caption>'
           f'<thead><tr><th>Check</th><th class="num">Patients</th></tr></thead><tbody>{sqlf}</tbody></table></div>')

# ---------- 3. Shortlist ----------
with tab_list:
    if not ss.ran:
        md('<div class="nhs-inset">Find eligible patients first. The shortlist will appear here.</div>')
    else:
        show_excl = st.toggle("Show patients the agent excluded", value=False)
        body = ""
        for r in results:
            if r.get("excluded") and not show_excl:
                continue
            marks = ""
            for c in criteria:
                s = r["criteria"][c["id"]]["status"]
                cls, sym = ("m-exc", "✕") if (c["type"] == "exclusion" and s == "MET") else \
                    {"MET": ("m-met", "✓"), "NOT_MET": ("m-not", "–"), "UNKNOWN": ("m-unk", "?")}[s]
                marks += f'<span class="{cls}" title="{c["id"]}: {STATUS[s][0]}">{sym}</span>'
            d = ss.decisions.get(r["patient_id"])
            dec = (tag("Excluded", "tag-red") if r.get("excluded") else tag("Approved", "tag-green") if d == "approved"
                   else tag("Rejected", "tag-red") if d == "rejected" else tag("To review", "tag-blue"))
            body += (f'<tr class="{"excluded" if r.get("excluded") else ""}"><td><strong>{r["name"]}</strong><br>'
                     f'<span class="nhs-secondary" style="font-size:16px">{r["patient_id"]}, age {r["age"]}, BMI {r["bmi"]}</span></td>'
                     f'<td class="num">{r["score"]*100:.0f}%</td><td class="marks" style="white-space:nowrap">{marks}</td>'
                     f'<td style="font-size:16px;line-height:24px">{html.escape(r["explanation"])}</td><td>{dec}</td></tr>')
        n = sum(1 for r in results if not r.get("excluded"))
        md('<div class="nhs-card" style="padding:24px 32px"><table class="nhs-table">'
           f'<caption>{n} patients likely to be eligible</caption>'
           '<thead><tr><th>Patient</th><th class="num">Match</th><th>Criteria 1 to 8</th><th>Reason</th><th>Status</th></tr></thead>'
           f'<tbody>{body}</tbody></table></div>')
        md('<p class="nhs-body-s"><span class="marks"><span class="m-met">✓</span></span> met &nbsp; '
           '<span class="marks"><span class="m-not">–</span></span> not met (for an exclusion, this is good) &nbsp; '
           '<span class="marks"><span class="m-unk">?</span></span> not known, needs checking &nbsp; '
           '<span class="marks"><span class="m-exc">✕</span></span> exclusion found</p>'
           '<div class="nhs-inset">TrialMatch helps recruiters find patients. A recruiter must check and approve every patient before anyone is contacted.</div>')
        import json
        manifest = {"trial": trial["id"], "generated_on": "this device", "patients": [
            {"patient_id": r["patient_id"], "score": r["score"], "excluded": bool(r.get("excluded")),
             "decision": ss.decisions.get(r["patient_id"], "to review"), "reason": r["explanation"],
             "evidence": {k: {"status": v["status"], "source": v["source"], "quote": v["quote"]} for k, v in r["criteria"].items()}}
            for r in results]}
        d1, d2, _ = st.columns([1, 1, 2])
        d1.download_button("Download shortlist (CSV)", "patient_id,score,decision\n" + "\n".join(
            f'{p["patient_id"]},{p["score"]},{p["decision"]}' for p in manifest["patients"] if not p["excluded"]),
            f"{trial['id']}-shortlist.csv", use_container_width=True)
        d2.download_button("Download manifest (JSON)", json.dumps(manifest, indent=2), f"{trial['id']}-manifest.json",
                           use_container_width=True)

# ---------- 4. Review queue ----------
with tab_review:
    if not ss.ran:
        md('<div class="nhs-inset">Find eligible patients first, then review them one at a time here.</div>')
    else:
        queue = results[:4]
        qcol, dcol = st.columns([1, 2.6], gap="large")
        with qcol:
            def qlabel(i):
                r = queue[i]
                d = ss.decisions.get(r["patient_id"])
                state = "excluded" if r.get("excluded") else "approved" if d == "approved" else "rejected" if d == "rejected" else "to review"
                return f'{r["name"]} ({state})'
            idx = st.radio("Choose a patient to review", range(len(queue)), format_func=qlabel)
            r = queue[idx]
        with dcol:
            md(f'<span class="nhs-caption-l" style="margin:0">{r["patient_id"]}</span><h2 class="nhs-heading-l">{r["name"]}</h2>')
            if r.get("excluded"):
                md('<div class="nhs-error-summary" role="alert"><h2>The agent excluded this patient</h2>'
                   f'<p>{html.escape(r["explanation"])}</p></div>')
            a, b = st.columns([1.6, 1], gap="large")
            with a:
                md('<table class="nhs-summary">'
                   f'<tr><th>Age</th><td>{r["age"]}</td></tr>'
                   f'<tr><th>Sex</th><td>{"Female" if r["sex"] == "F" else "Male"}</td></tr>'
                   f'<tr><th>Body mass index (BMI)</th><td>{r["bmi"]}</td></tr>'
                   f'<tr><th>Match</th><td>{r["score"]*100:.0f}%</td></tr>'
                   f'<tr><th>Reason</th><td>{html.escape(r["explanation"])}</td></tr></table>')
            with b:
                d = ss.decisions.get(r["patient_id"])
                state = (tag("Excluded", "tag-red") if r.get("excluded") else tag("Approved for contact", "tag-green") if d == "approved"
                         else tag("Rejected", "tag-red") if d == "rejected" else tag("Waiting for a decision", "tag-blue"))
                md(f'<h3 class="nhs-heading-xs">Your decision</h3><p class="nhs-body">{state}</p>')
                if st.button("Approve for contact", key=f"ap_{r['patient_id']}", type="primary", use_container_width=True):
                    ss.decisions[r["patient_id"]] = "approved"; st.rerun()
                if st.button("Reject", key=f"rj_{r['patient_id']}", use_container_width=True):
                    ss.decisions[r["patient_id"]] = "rejected"; st.rerun()
                st.text_input("Note (optional)", key=f"why_{r['patient_id']}", help="Saved to the audit log with your decision.")

            md('<h2 class="nhs-heading-m" style="margin-top:16px">Evidence for each criterion</h2>')
            for cid, ev in r["criteria"].items():
                c = crit_by_id[cid]
                s = ev["status"]
                bad = c["type"] == "exclusion" and s == "MET"
                result = tag("Excludes this patient", "tag-red") if bad else tag(*STATUS[s])
                body = ""
                if ev.get("note") and ev.get("quote"):
                    q = html.escape(ev["quote"])
                    note = html.escape(ev["note"]).replace(q, f"<mark>{q}</mark>")
                    body = (f'<p class="nhs-body-s nhs-secondary" style="margin:8px 0 0">{ev["source"]}. The highlighted words are quoted exactly.</p>'
                            f'<div class="nhs-inset" style="margin-bottom:0">{note}</div>')
                elif ev.get("source"):
                    body = f'<p class="nhs-body-s nhs-secondary" style="margin:8px 0 0">{html.escape(ev["source"])}</p>'
                if s == "UNKNOWN":
                    body += ('<div class="nhs-warning"><span class="nhs-warning__label">Important</span>'
                             '<p>The notes do not mention this. Ask the patient or check hospital letters before they join the trial.</p></div>')
                border = "border-left:4px solid var(--error);" if bad else ""
                md(f'<div class="nhs-card" style="padding:24px;margin-bottom:16px;{border}">'
                   f'<h3 class="nhs-heading-xs">{cid}: {html.escape(c["text"])}</h3>'
                   f'<div>{type_tag(c)}{result}<span class="nhs-body-s nhs-secondary">Confidence {ev["confidence"]*100:.0f}%</span></div>{body}</div>')

footer()
