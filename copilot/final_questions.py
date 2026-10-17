"""Test-dataset (2027-01-15) final-question tools. Deterministic, evidence-cited.
Rules come from fleet/sops/operations-sop-rev2.md (OPS-1/2/3/5), the recall notice,
the MOC memos and the regulator letter; citations name those documents."""
import sqlite3, re, os, glob, json
from datetime import datetime, timedelta
from collections import defaultdict

TODAY = '2027-01-15'
DS = os.environ.get('COPILOT_DATA_DIR', '/tmp/ahc-dataset/test-data/dataset')
GAS = re.compile(r'gas smell|smelled gas|rotten.?egg|gas odor|H2S', re.I)
BYPASS_IN = re.compile(r'bypass (in place|installed|in\b)|had the bypass installed|put(ting)? the bypass', re.I)
BYPASS_OUT = re.compile(r'(took|taken|removed|pull(ed)?) (the )?bypass out|bypass (removed|out\b)', re.I)

class FQ:
    def __init__(self, db_path='/tmp/test_copilot.db'):
        self.db = sqlite3.connect(db_path); self.db.row_factory = sqlite3.Row

    def q(self, sql, a=()): return self.db.execute(sql, a).fetchall()

    def gas_response_audit(self, since='2026-11-01'):
        """Q3 + Q9.4: every gas report, response found, met deadline? OPS-1 rev2."""
        rows=[]
        for n in self.q("SELECT * FROM notes WHERE created_at >= ? AND note_type='operator_note'", (since,)):
            if not GAS.search(n['text'] or ''): continue
            created = datetime.fromisoformat(n['created_at'].replace('Z',''))
            limit_h = 24 if created.date() < datetime(2027,1,1).date() else 12
            # response: maintenance started_at OR manual inspection after created
            m = self.q("SELECT started_at, maintenance_id FROM maintenance WHERE asset_id=? AND started_at > ? ORDER BY started_at LIMIT 1",
                       (n['asset_id'], n['created_at']))
            i = self.q("SELECT inspected_at, inspection_id FROM inspections WHERE asset_id=? AND method='manual' AND inspected_at > ? ORDER BY inspected_at LIMIT 1",
                       (n['asset_id'], n['created_at']))
            cands = [(datetime.fromisoformat(m[0]['started_at'].replace('Z','')), m[0]['maintenance_id'])] if m else []
            cands += [(datetime.fromisoformat(i[0]['inspected_at'].replace('Z','')), i[0]['inspection_id'])] if i else []
            met=False; ev=None; hrs=None
            if cands:
                t, eid = min(cands)
                hrs = round((t-created).total_seconds()/3600, 1)
                met = hrs <= limit_h; ev=eid
            rows.append({'note': n['note_id'], 'well': n['asset_id'], 'created': n['created_at'],
                         'limit_h': limit_h, 'response_hrs': hrs, 'met': met, 'evidence': ev, 'text': n['text'][:90]})
        return rows

    def bypass_audit(self):
        """Q4: active bypasses now + legality per OPS-2 + MOC memos."""
        moc = {'WELL-0013':'MOC-2026-031 ext. 044 (to 2027-02-28)','WELL-0062':'MOC-2026-031 ext. 044 (to 2027-02-28)',
               'WELL-0118':'MOC-2026-031 ext. 044 (to 2027-02-28)','WELL-0248':'MOC-2026-031 ext. 044 (to 2027-02-28)',
               'WELL-0476':'MOC-2026-031 ext. 044 (to 2027-02-28)','WELL-0087':'MOC-2026-036 (to 2026-12-15)',
               'WELL-0158':'MOC-2026-036 (to 2026-12-15)','WELL-0249':'MOC-2026-036 (to 2026-12-15)',
               'WELL-0284':'MOC-2026-036 (to 2026-12-15)','WELL-0356':'MOC-2026-036 (to 2026-12-15)',
               'WELL-0388':'MOC-2026-036 (to 2026-12-15)','WELL-0461':'MOC-2026-036 (to 2026-12-15)'}
        expiry = {'WELL-0013':'2027-02-28','WELL-0062':'2027-02-28','WELL-0118':'2027-02-28','WELL-0248':'2027-02-28','WELL-0476':'2027-02-28',
                  'WELL-0087':'2026-12-15','WELL-0158':'2026-12-15','WELL-0249':'2026-12-15','WELL-0284':'2026-12-15',
                  'WELL-0356':'2026-12-15','WELL-0388':'2026-12-15','WELL-0461':'2026-12-15'}
        state={}
        for n in self.q("SELECT * FROM notes WHERE text LIKE '%bypass%' ORDER BY created_at"):
            w=n['asset_id']
            if BYPASS_OUT.search(n['text']): state[w]=('out', n['note_id'], n['created_at'])
            elif BYPASS_IN.search(n['text']) or 'bypass' in n['text'].lower() and 'in place' in n['text'].lower():
                state[w]=('in', n['note_id'], n['created_at'])
        out=[]
        for w,(s,nid,ts) in sorted(state.items()):
            if s!='in': continue
            legal = w in moc and expiry[w] >= TODAY
            why = 'covered by '+moc[w] if w in moc else 'NO MOC covers this well'
            if w in moc and expiry[w] < TODAY: why += ' - EXPIRED '+expiry[w]
            out.append({'well':w,'since':ts,'note':nid,'allowed':legal,'why':why})
        return out

    def region_heat(self):
        """Q11: average wellhead-minus-ambient delta by area."""
        rows = self.q("""SELECT a.name area, AVG(i.wellhead_temp_c - i.ambient_temp_c) d, COUNT(*) n
                         FROM inspections i JOIN wells w ON w.asset_id=i.asset_id JOIN areas a ON a.area_id=w.area_id
                         GROUP BY a.name ORDER BY d DESC""")
        return [dict(r) for r in rows]

    def status_disagreements(self):
        """Q6: latest inspection vs operator shut-in/restart notes, field-wide (OPS-3)."""
        res=[]
        for w in self.q("SELECT asset_id, name, well_type FROM wells"):
            li = self.q("SELECT * FROM inspections WHERE asset_id=? ORDER BY inspected_at DESC LIMIT 1",(w['asset_id'],))
            if not li: continue
            insp = dict(li[0])
            # corrections
            corr = self.q("SELECT * FROM notes WHERE asset_id=? AND note_type='data_correction' ORDER BY created_at DESC LIMIT 1",(w['asset_id'],))
            delta = insp['wellhead_temp_c'] - insp['ambient_temp_c']
            thr = {'T200':15.0,'T350':17.0,'T500':17.0}.get(w['well_type'],15.0)
            prod_insp = delta >= thr
            n = self.q("""SELECT * FROM notes WHERE asset_id=? AND note_type='operator_note'
                          AND (text LIKE '%shut%in%' OR text LIKE '%shut-in%' OR text LIKE '%restarted%' OR text LIKE '%back online%' OR text LIKE '%returned to production%')
                          ORDER BY created_at DESC LIMIT 1""",(w['asset_id'],))
            if not n: continue
            note = n[0]
            if note['created_at'] <= insp['inspected_at']: continue
            says_shut = bool(re.search(r'shut.?in', note['text'], re.I))
            if (says_shut and prod_insp) or ((not says_shut) and not prod_insp):
                res.append({'well':w['asset_id'],'insp':insp['inspection_id'],'insp_says':'producing' if prod_insp else 'not producing',
                            'note':note['note_id'],'note_says':'shut in' if says_shut else 'restarted','note_at':note['created_at']})
        return res

    def recall_status(self):
        """Q8: current pump lot per well from maintenance reports; recalled lots still installed."""
        RECALLED = {'LOT-2409-A','LOT-2409-C','LOT-2410-B'}
        pat = re.compile(r'Parts installed:\*\* Highline RP-175 pump, serial (SN-\d+), lot (LOT-[\d-]+[A-Z]?)', re.I)
        latest={}
        for f in glob.glob(DS+'/assets/*/maintenance-reports/*.md'):
            well = f.split('/assets/')[1].split('/')[0]
            txt = open(f).read()
            m = pat.search(txt)
            if not m: continue
            mnt = os.path.basename(f)[:-3]
            if well not in latest or mnt > latest[well][0]:
                latest[well]=(mnt, m.group(2), m.group(1))
        return {w:v for w,v in latest.items() if v[1] in RECALLED}

    def problem_notes_no_repair(self, since_days=31):
        """Q7: problem-sounding notes in last month, no corrective maintenance since."""
        KW = re.compile(r'fluid pound|knock|grind|screech|squeal|rattle|vibrat|clunk|bang|smoke|burning|leak|whistl|humming|strange|wrong|sticking|jerking', re.I)
        since = (datetime(2027,1,15) - timedelta(days=since_days)).isoformat()
        agg=defaultdict(list)
        for n in self.q("SELECT * FROM notes WHERE note_type='operator_note' AND created_at >= ?",(since,)):
            if KW.search(n['text'] or ''): agg[n['asset_id']].append(n)
        out=[]
        for w,ns in agg.items():
            m = self.q("SELECT COUNT(*) c FROM maintenance WHERE asset_id=? AND maintenance_type='corrective' AND started_at > ?",(w, ns[0]['created_at']))
            if m[0]['c']==0:
                out.append({'well':w,'reports':len(ns),'latest':ns[-1]['text'][:80],'note_ids':[x['note_id'] for x in ns]})
        return sorted(out, key=lambda x:-x['reports'])

    def downtime_wait_reasons(self):
        """Q10: for corrective jobs, classify wait cause from report text vs downtime."""
        cats = {'parts':re.compile(r'back.?order|awaiting (the )?part|part(s)? (arriv|on order|shipped)|waiting (on|for) (the )?(part|pump|valve|motor)',re.I),
                'crews':re.compile(r'crew (unavailable|shortage)|short.?staffed|no crew|crew backlog',re.I),
                'contractors':re.compile(r'contractor|third.party vendor|service company',re.I),
                'weather':re.compile(r'weather|storm|ice|frozen roads|wind hold|lightning',re.I)}
        counts=defaultdict(int); ex=defaultdict(list)
        for f in glob.glob(DS+'/assets/*/maintenance-reports/*.md'):
            txt=open(f).read()
            for k,rx in cats.items():
                if rx.search(txt):
                    counts[k]+=1
                    if len(ex[k])<2: ex[k].append(os.path.basename(f))
                    break
        return dict(counts), ex

if __name__=='__main__':
    import sys
    fq = FQ(sys.argv[1] if len(sys.argv)>1 else '/tmp/test_copilot.db')
    which = sys.argv[2] if len(sys.argv)>2 else 'all'
    if which in ('all','q3'):
        rows = fq.gas_response_audit()
        met = sum(1 for r in rows if r['met'])
        print(f"Q3: {len(rows)} gas reports since 1 Nov 2026; {met} met deadline, {len(rows)-met} MISSED")
        for r in rows[:12]:
            print(f"  {r['note']} {r['well']} {r['created'][:16]} limit={r['limit_h']}h resp={r['response_hrs']}h {'MET' if r['met'] else 'MISSED'} [{r['evidence']}] {r['text'][:60]}")
    if which in ('all','q4'):
        print("Q4: active bypasses now:")
        for b in fq.bypass_audit(): print(f"  {b['well']} since {b['since'][:10]} [{b['note']}] {'ALLOWED' if b['allowed'] else 'VIOLATION'} ({b['why']})")
    if which in ('all','q11'):
        print("Q11: field temp delta by area (wellhead - ambient, degC avg):")
        for r in fq.region_heat(): print(f"  {r['area']}: +{r['d']:.1f}C over {r['n']} inspections")
    if which in ('all','q6'):
        d = fq.status_disagreements()
        print(f"Q6: {len(d)} wells where inspection and newer operator note disagree on producing:")
        for x in d[:20]: print(f"  {x['well']}: {x['insp']} says {x['insp_says']} vs {x['note']} ({x['note_at'][:10]}) says {x['note_says']}")
    if which in ('all','q8'):
        r = fq.recall_status()
        print(f"Q8: {len(r)} wells still running a recalled-lot Highline RP-175 (deadline was 31 Dec 2026):")
        for w,v in sorted(r.items()): print(f"  {w}: {v[1]} ({v[0]})")
    if which in ('all','q7'):
        p = fq.problem_notes_no_repair()
        print(f"Q7: {len(p)} wells with problem-sounding notes in the last month and no corrective repair since:")
        for x in p[:12]: print(f"  {x['well']}: {x['reports']} reports, latest: {x['latest']}")
    if which in ('all','q10'):
        c, ex = fq.downtime_wait_reasons()
        print("Q10: extended-shutdown wait causes across corrective repair reports:", c)
