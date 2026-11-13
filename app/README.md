# TrialMatch UI (mockup)

```
pip install -r app/requirements.txt
streamlit run app/app.py
```

Runs entirely on mock data in `app/mock_data.py`. Its three functions (`get_trials`, `get_criteria`, `get_run`)
are the seam for the real pipeline: return the same shapes and the UI keeps working.

Screens (tabs): parsed criteria, live agent trace + funnel, ranked shortlist, recruiter review with
quoted note evidence and approve/reject. Sidebar shows the "running locally on DGX Spark" panel.

## Design references

- NHS design system (service-manual.nhs.uk): colour palette, tags, inset text for note quotes, summary list,
  warning callout for unknowns, green primary buttons, yellow focus state. No NHS logo, no endorsement implied.
- Dribbble healthcare dashboards: KPI stat cards over a light grey canvas, white cards, patient table with initials avatars.
- Dribbble AI agent workflow shots: vertical numbered step timeline with per-step tool and timing.
- Dribbble approval queue shots: master-detail review (queue on the left, decision panel on the right).
- Mobbin "clean dashboards" (public homepage only): one accent colour, sparse layout.
