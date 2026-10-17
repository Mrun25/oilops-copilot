# Asset Intelligence Copilot — Write-up

## Page 1 — System design

**Goal.** A chat copilot that answers operators' plain-English questions about 200
oil wells — correctly, with evidence — across weekly inspections, repairs, regulator
events, human notes, and the fleet manuals/SOPs.

**Storage, indexing, retrieval.** A loader ingests the organizers' folder format
(fleet JSON, per-well JSONL, free-text reports, manuals/SOPs) into SQLite in about a
second: one table each for wells, inspections, maintenance, regulator events, notes,
users, areas, plus full-text of every repair report and manual/SOP, indexed by
asset. Pointing it at the test dataset is a path change; nothing else changes.
Structured facts are queried with SQL; free-text facts (e.g. actual containment
time, which exists only inside repair reports) are pulled from the report text and
cited by report ID.

**Rules live in documents, so the engine treats them as named rules.** The
production test (wellhead ≥ 15 °C above ambient for T200, 20 °C for T350), spill
classes and the 24 h / 2 h reporting deadlines, the 4 h containment limit, 60-day
verification, 30-day NOV response, the 3-repair RCA trigger and the yearly flowline
test were read out of OM-T200/OM-T350 and the three SOPs and encoded once, each
annotated with its source section. Answers cite those sections.

**Tools.** The copilot's tools are deterministic engine functions, not raw LLM
reasoning: well status, not-producing-in-area, temperature series, spill list/map/
heatmap, downtime and downtime ranking, repair costs, compliance breaches, RCA
candidates, containment violations, and document lookup. The judging criteria are
numbers, IDs and dates; computing them in code removes the LLM's weakest link
(arithmetic over 40k rows) and makes every answer reproducible.

**Memory at two levels.** (a) Private per user: every conversation turn is logged
against the selected user; references like "the three wells we discussed yesterday"
are resolved only against that user's own history. (b) Shared well knowledge: facts
a user states about a well ("WELL-0007 shut in from 17 Oct") are stored as team
notes visible to everyone, with a status (shared / unconfirmed). Corrections are
first-class records: the original value is kept, the correction stores who, why and
scope (one inspection, one well, or all wells of a type), and the engine applies
corrected values at exactly that scope — e.g. a 41.0 °C re-measure flips WELL-0164
to producing for everyone, and a 2-repair RCA rule applies to T350 wells only.

**Conflicts.** The copilot never silently picks a side. Corrected readings that
disagree with an inspector note ("Pump jack not moving") are flagged for
confirmation; user claims that contradict the latest inspection are recorded as
unconfirmed with the evidence shown; disagreeing human notes (produced water vs
crude) are both shown with the formal-correction rule explained.

**Models and cost.** A small Gemini model (free tier, OpenAI-compatible endpoint)
acts only as a planner/narrator: it maps the question to one engine tool and
phrases the answer. A deterministic router covers the same intents offline, so the
system works at zero cost and never hard-fails mid-demo. No training, no paid APIs.

**Next steps.** Embedding search over repair reports, a multi-step agent loop with
tool retries, photo thumbnails from evidence references, and staleness alerts.

## Page 2 — Results (sample dataset; final questions appended after release)

The copilot was verified against all 18 example questions in
`03-evaluation-queries.md`. Highlights (all match the expected answers):

- **Q1 status card:** WELL-0001 not producing (+3.2 °C vs the 15 °C T200 limit),
  planned-shut-in reason surfaced from note CTX-0001-003, stale recorded status
  flagged.
- **Q4 spills:** 6 real spills / 15.1 bbl — the rainwater-corrected detection
  (INS-0001-0070) excluded automatically.
- **Q5 downtime:** 21 days in 2025, including the 51.6 °C-wellhead-on-a-43.1 °C-day
  trap (only the difference matters).
- **Q6/Q7/Q8:** downtime ranking 49/42/42 days; exact 4 spill pins in NE-8; the
  area × month heatmap totals match (35/43/37/26).
- **Q9 compliance:** all four breaches found (SR-01 41 h late, SR-02 ~7 h
  containment found only in the repair report, SR-03 verification overdue, RC-04
  unanswered NOV with USD 5,000 open) plus the pending RCA and the met RC-02.
- **Q12–Q18 memory:** corrections keep originals and propagate across users at the
  right scope (WELL-0102 correctly excluded from the T350 rule); private recall
  never leaks across users; false claims are marked unconfirmed; disagreeing notes
  are shown, not hidden.

**What worked:** deterministic tools over SQL — every number matches; the
correction/conflict model; document-sourced rules.
**What didn't / hard parts:** targeting a correction to the right inspection needed
date cues from the message; containment facts required reading free-text reports;
scope discipline (inspection vs well vs well-type) needed explicit handling.

**Final questions:** to be run on the test dataset in the last 2 hours and appended
here verbatim with the copilot's recorded answers.
