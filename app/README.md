# TrialMatch UI (mockup)

```
pip install -r app/requirements.txt
streamlit run app/app.py
```

Runs entirely on mock data in `app/mock_data.py`. Its three functions (`get_trials`, `get_criteria`, `get_run`)
are the seam for the real pipeline: return the same shapes and the UI keeps working.

Screens (tabs): parsed criteria, live agent trace + funnel, ranked shortlist, recruiter review with
quoted note evidence and approve/reject. Sidebar shows the "running locally on DGX Spark" panel.

## Design system

The UI follows the NHS design system (service-manual.nhs.uk/design-system):

- Colour: only NHS palette values, used in their roles (text #212b32, secondary #4c6272, links #005eb8 with
  hover/visited/active states, yellow #ffeb3b focus with a dark underline, green #007f3b buttons, #d8dde0 borders,
  #f0f4f5 page background, #d5281b errors). The "runs on this device" signal uses the aqua-green family.
- Type: Frutiger with Arial fallback, 19px body, heading sizes 36/26/22/19, secondary text in grey-1.
- Spacing: the NHS scale (4, 8, 16, 24, 32, 48).
- Components: header with service name (no NHS logo), breadcrumbs, caption + heading, tags, cards and feature
  cards, table with caption, summary list, inset text for note quotes, warning callout for unknowns, error summary
  for exclusions, details for technical info, task list for agent steps, tabs, buttons, footer.
- Content: plain English, sentence case, no unexplained abbreviations in UI text, dates as "2 October 2026".

No NHS logo is used and no NHS endorsement is implied.

Layout patterns also borrowed from Dribbble (summary cards, step timeline, queue with detail panel).
