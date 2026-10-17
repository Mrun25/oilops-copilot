# Video narration script (~4.5 minutes) + shot list
Record in your own voice, first person. Screen-record the app while you talk.
Total ~4:30. Speak like you're explaining to a friend, not reading.

## SHOT 1 — title screen / repo (0:00–0:25)
"Hi, I'm Mrunmayee, an AI engineering student, and this is my Asset Intelligence
Copilot for the FlytBase Hack Day. The challenge: an oil company has 200 wells, four
years of weekly inspections, repair records, regulator events, handwritten-style
operator notes, and long manuals — and an operator just wants to ask questions in
plain English and get a correct answer with evidence. Here's how I built it."

## SHOT 2 — the dataset folder + architecture sketch on screen (0:25–1:30)
"First, the data. Everything starts from a well: weekly inspections with wellhead
and air temperatures and spill detections, maintenance jobs with costs, regulator
events, free-text repair reports, and human notes. The rules — like when a well
counts as producing oil — are not in the data. They live in the well-type manuals
and the SOPs, so my system has to read those too.

My design has three layers. One: a loader that turns the whole folder into a SQLite
database — wells, inspections, repairs, events, notes, and the full text of every
report and manual. Two: a deterministic analytics engine. This was a deliberate
choice: the answers judges check are numbers and dates, so instead of hoping the
language model gets arithmetic right, the engine computes downtime, spills, and
compliance breaches in code, with the manual's thresholds as named rules. Three: a
chat layer with a planner — a Gemini model when a key is set, a deterministic router
as fallback — that picks which engine tool to call and which visual fits the answer."

## SHOT 3 — live demo, question 1 (1:30–2:30)
[In the app, pick user "Michael Turner", ask: "Is WELL-0001 producing oil right now?
Show me its status card."]
"Here's the copilot running. Notice three things. It says no — because it derives
production from the temperature rule in the T200 manual: the wellhead was only 3.2
degrees above the air, below the 15-degree limit. It found the reason in a human
note — a planned shut-in for rod string replacement that isn't even in the
maintenance system yet. And it warns that the recorded status still says active,
which is out of date. Every answer cites the exact inspection, note, and manual it
used."

## SHOT 4 — live demo, analysis question (2:30–3:10)
[Ask: "Show a heatmap of 2026 spills by geographic area and month."]
"For field-wide questions it generates the right visual — here a heatmap of spills
by area and month. This one quietly handles a trap: one inspection's 'spill' was
later corrected by a person as rainwater, and corrections win, so it's excluded.
The same engine answers downtime rankings, spill maps, repair costs, and compliance
checks — like a spill reported after 41 hours against a 24-hour SOP limit."

## SHOT 5 — live demo, memory + correction (3:10–4:00)
[Ask: "The 15 Oct reading for WELL-0164 is wrong — the field crew re-measured the
wellhead at 41.0 C." Then switch user to Jessica Reed and ask: "Is WELL-0164
producing oil right now?"]
"The part I'm happiest with is memory. When I correct a reading, the copilot keeps
the original value, records who corrected it and why, and uses the correction from
then on — for every user. It also catches the conflict: the same inspection has a
note saying the pump jack wasn't moving, which disagrees with the correction, so it
asks instead of silently picking one. Memories are private per user, facts people
state about a well are shared with the team, and claims that contradict the data
are marked unconfirmed, not believed."

## SHOT 6 — close (4:00–4:30)
"In the final two hours the organizers release a test dataset in the same format —
my loader just points at the new folder, nothing in the engine changes. If I had
more time I'd add embeddings over the repair reports and a proper agent loop with
tool retries. Thanks for watching — the repo link and write-up are in the
submission."

## Shot list (screen capture)
1. Repo README (5s)
2. File tree: sample-data/, copilot/loader.py, engine.py, router.py, executor.py (10s)
3. App: user picker visible, Q1 status card answer with evidence expanded (~40s)
4. App: heatmap answer (~25s)
5. App: correction flow + conflict warning, then user switch to Jessica Reed showing
   the corrected answer (~40s)
6. README/test-dataset note while narrating the close (15s)
Keep clips tight; cut loading spinners.
