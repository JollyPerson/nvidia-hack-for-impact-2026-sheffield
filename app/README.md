# TrialMatch UI (mockup)

```
pip install -r app/requirements.txt
streamlit run app/app.py
```

Runs entirely on mock data in `app/mock_data.py`. Its three functions (`get_trials`, `get_criteria`, `get_run`)
are the seam for the real pipeline: return the same shapes and the UI keeps working.

Screens (tabs): parsed criteria, live agent trace + funnel, ranked shortlist, recruiter review with
quoted note evidence and approve/reject. Sidebar shows the "running locally on DGX Spark" panel.
