# Asset Intelligence Copilot — FlytBase Hack Day 2026

A chat copilot that answers operators' plain-English questions about 200 oil wells —
correctly, with evidence — over inspections, repairs, regulator events, human notes,
and the fleet manuals/SOPs.

## Run

```bash
pip install -r requirements.txt
# copy the organizers' dataset folder next to the repo as ./sample-data
streamlit run app.py
```

Set `COPILOT_DATA_DIR` to point at a different dataset folder (same format) and
delete `/tmp/copilot.db` to reload. Optional LLM planner: `LLM_API_KEY`,
`LLM_BASE_URL`, `LLM_MODEL` (OpenAI-compatible). Without a key, a deterministic
router handles the questions.

## Design

See `WRITEUP.md` (system design + results).

All field data is synthetic, supplied by the organizers for the challenge.
