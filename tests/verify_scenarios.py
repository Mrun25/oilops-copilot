#!/usr/bin/env python3
"""Replay the organizers' 18 example scenarios (03-evaluation-queries.md) against the
deterministic router (no LLM key needed) and assert the key expected facts.

Usage:  python3 tests/verify_scenarios.py
Exit code 0 = every check passed. Set COPILOT_DATA_DIR to point at another dataset
folder in the same format (e.g. the released test dataset)."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop('LLM_API_KEY', None)  # exercise the deterministic path

from copilot.loader import load
from copilot.router import Router
from copilot.executor import Executor

DATA = os.environ.get('COPILOT_DATA_DIR', 'sample-data')
DB = os.path.join(tempfile.mkdtemp(prefix='copilot-verify-'), 'verify.db')
load(DATA, DB)
router = Router(DB)
ex = Executor(router)

users = {u['name']: u['user_id'] for u in router.users()}
MT = users['Michael Turner']; TH = users['Tyler Hughes']; JR = users['Jessica Reed']
JM = users['Jake Morales']; LS = users['Laura Simmons']

results = []

def ask(uid, name, q):
    """Mirror app.py: plan, run, then log both sides (assistant log carries the
    well ids shown in tables, which is what recall queries resolve against)."""
    plan = router.plan_rules(q)
    resp = ex.run(plan, uid, name, q)
    router.log_chat(uid, 'user', q)
    wells = ' '.join(r.get('Well', '') for r in (resp.get('data') or {}).get('table', []) if isinstance(r, dict))
    router.log_chat(uid, 'assistant', resp['text'] + ' ' + wells)
    return resp

def full_text(resp):
    parts = [resp['text']]
    for row in (resp.get('data') or {}).get('table', []):
        if isinstance(row, dict):
            parts.append(' '.join(str(v) for v in row.values()))
    parts += [str(e) for e in (resp.get('evidence') or [])]
    return ' '.join(parts)

def check(label, resp, needles=(), exclude=()):
    text = full_text(resp)
    low = text.lower()
    missing = [n for n in needles if n.lower() not in low]
    present = [x for x in exclude if x.lower() in low]
    ok = not missing and not present
    results.append((ok, label, missing, present, text[:110]))
    return resp

# ---------- A1. Single-user questions ----------
r = ask(MT, 'Michael Turner', "Is WELL-0001 producing oil right now? When was it last inspected, and by whom? Show its status card.")
check('Q1 status card', r, ['NOT producing', 'INS-0001-0203', '2026-10-15', 'Ryan Brooks', '3.2', 'out of date'])

r = ask(MT, 'Michael Turner', "Which wells in Northeast Block 8 are not producing oil right now, and why? Show them in a table.")
check('Q2 not-producing table', r, ['WELL-0164', 'WELL-0170', 'WELL-0172', 'WELL-0175', 'No recorded reason'])

r = ask(MT, 'Michael Turner', "Show a line chart of WELL-0001's wellhead temperature against air temperature for the last 13 weeks, and mark when it was not producing oil.")
series = (r.get('data') or {}).get('series') or []
check('Q3 13-week series', {'text': f'{len(series)} readings'}, ['13 readings'])

r = ask(MT, 'Michael Turner', "How many oil spills has WELL-0001 had in the last 4 years, and how much oil in total? List them in a table.")
check('Q4 spills', r, ['6 real spills', '15.1'])

r = ask(MT, 'Michael Turner', "How many days was WELL-0001 down in 2025?")
check('Q5 downtime', r, ['21 days', '51.6', '43.1'])

r = ask(MT, 'Michael Turner', "Which 3 wells in Northeast Block 7 had the most downtime in the last 4 months? Show a bar chart.")
check('Q6 downtime ranking', r, ['WELL-0121: 49', 'WELL-0138: 42', 'WELL-0107: 42'])

r = ask(MT, 'Michael Turner', "For the three wells we discussed yesterday, what have repairs cost this year?")
check('Q11 private recall', r, ['25,794', '46,942', '5,176'])

r = ask(MT, 'Michael Turner', "Show on a map every well in Northeast Block 8 with a real spill in the last 90 days.")
pins = (r.get('data') or {}).get('pins') or []
check('Q7 spill map', {'text': f"{len(pins)} pins " + ','.join(sorted(p['well'] for p in pins))},
      ['4 pins', 'WELL-0151', 'WELL-0165', 'WELL-0175', 'WELL-0186'])

r = ask(MT, 'Michael Turner', "Show a heatmap of 2026 spills by geographic area and month.")
grid = (r.get('data') or {}).get('grid') or {}
totals = {a: sum(m for m in months.values()) for a, months in grid.items()} if isinstance(grid, dict) else {}
check('Q8 heatmap', {'text': r['text'] + ' ' + str(sorted(totals.values()))},
      ['2026 spills by area and month', '26', '35', '37', '43'])

r = ask(MT, 'Michael Turner', "Which rules has WELL-0001 broken in the last 12 months, and what is still open?")
check('Q9 compliance', r, ['SR-01', '41', 'SR-02', 'SR-03', 'RC-04', '5,000'])

r = ask(MT, 'Michael Turner', "In Southwest Block 24, which spills in the last 90 days were not contained within the SOP time limit?")
check('Q10 containment', r, ['WELL-0082', 'WELL-0100', '7.1', '8.1'])

# ---------- A2. Memory and feedback from one user ----------
r = ask(MT, 'Michael Turner', "The 15 Oct reading for WELL-0164 is wrong. The handheld thermal gun used in that inspection was faulty; the field crew re-measured the wellhead at 41.0 °C.")
check('Q12 correction', r, ['keeping the original', '41.0', 'Pump jack not moving'])

r = ask(MT, 'Michael Turner', "Which wells in Northeast Block 8 are not producing oil right now, and why? Show them in a table.")
check('Q12b correction applied', r, ['WELL-0170', 'WELL-0172', 'WELL-0175'], exclude=['WELL-0164'])

r = ask(MT, 'Michael Turner', "The AI estimated the 24 Sep spill at WELL-0151 as 4.5 bbl. The field crew measured 5.4 bbl.")
check('Q13 corrected volume', r, ['5.4', 'INS-0151-0201', 'SR-01'])

r = ask(MT, 'Michael Turner', "Which wells in Northeast Block 7 need a root-cause analysis on their pump jack?")
check('Q14 RCA baseline', r, ['WELL-0108', 'WELL-0121', 'WELL-0138'], exclude=['WELL-0120', 'WELL-0102'])

ask(MT, 'Michael Turner', "Note for all T350 wells: our team now wants a pump jack root-cause analysis after 2 repairs, not 3.")
r = ask(MT, 'Michael Turner', "Which wells in Northeast Block 7 need a root-cause analysis on their pump jack?")
check('Q14b type-scoped rule', r, ['WELL-0108', 'WELL-0120', 'WELL-0121', 'WELL-0138'], exclude=['WELL-0102'])

# ---------- B. Several users ----------
r = ask(JR, 'Jessica Reed', "Is WELL-0164 producing oil right now?")
check('Q15 correction reaches others', r, ['IS producing', 'Michael Turner', 'Pump jack not moving'])

r = ask(LS, 'Laura Simmons', "Which spills in Northeast Block 8 in the last 90 days needed a report to the regulator?")
check('Q15b reportable spill', r, ['WELL-0151', '5.4'])

ask(JM, 'Jake Morales', "FYI for everyone: WELL-0007 will be shut in from 17 Oct for a 10-day planned workover.")
r = ask(MT, 'Michael Turner', "Is any downtime planned for WELL-0007?")
check('Q16 shared knowledge', r, ['Jake Morales', '17 Oct'])

r = ask(JR, 'Jessica Reed', "For the three wells we discussed yesterday, what have repairs cost this year?")
check('Q16b private memory stays private', r, ['don\'t have an earlier conversation'], exclude=['25,794'])

ask(MT, 'Michael Turner', "WELL-0175 has been shut in for planned work since 14 Oct.")
r = ask(TH, 'Tyler Hughes', "WELL-0175 is running fine.")
check('Q17 conflicting claim', r, ['unconfirmed', '0.4', 'Jessica Reed'])

r = ask(JM, 'Jake Morales', "WELL-0001 is back online since 14 Oct.")
check('Q17b stale claim', r, ['unconfirmed'])

r = ask(MT, 'Michael Turner', "Was the 20 Aug spill at WELL-0101 real oil?")
check('Q18 disagreement shown', r, ['produced water', 'crude', 'CTX-0101-002', 'CTX-0101-003'])

# ---------- report ----------
n_ok = sum(1 for ok, *_ in results if ok)
for ok, label, missing, present, snippet in results:
    mark = 'PASS' if ok else 'FAIL'
    print(f'[{mark}] {label}')
    if not ok:
        print(f'       missing={missing} unexpected={present}')
        print(f'       got: {snippet}…')
print(f'\n{n_ok}/{len(results)} checks passed against {DATA}')
sys.exit(0 if n_ok == len(results) else 1)
