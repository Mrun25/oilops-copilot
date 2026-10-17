"""Deterministic analytics over the asset DB. Every function returns data + evidence IDs.
Rules (thresholds, deadlines) come from the manuals/SOPs in docs; see RULES."""
import sqlite3, re
from datetime import date, datetime, timedelta

TODAY = date(2026, 10, 16)  # per 03-evaluation-queries.md; dataset-dependent

# Rules extracted from fleet/well-types/*.md and fleet/sops/*.md (cited in answers)
RULES = {
    'production_delta_c': {'T200': 15.0, 'T350': 20.0},   # OM-T200 §9.3 / OM-T350 §3
    'spill_report_min_bbl': 5.0,                          # SOP-SR §3-4 (SR-01)
    'spill_report_deadline_h': 24.0,                      # SR-01
    'major_spill_bbl': 25.0, 'major_deadline_h': 2.0,     # SOP-SR §3/SR-01
    'containment_limit_h': 4.0,                           # SR-02
    'remediation_verify_days': 60,                        # SR-03
    'nov_response_days': 30,                              # RC-04
    'rca_corrective_count': 3,                            # OM-T200 §10 / OM-T350 §6
    'rca_window_months': 12,
    'downtime_days_per_inspection': 7,                    # Dataset Guide
}

class Engine:
    def __init__(self, db_path):
        self.db = sqlite3.connect(db_path, check_same_thread=False)  # Streamlit runs scripts across threads
        self.db.row_factory = sqlite3.Row

    def q(self, sql, args=()):
        return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    # ---- runtime memory lookups ----
    def runtime_corrections(self, inspection_id=None, asset_id=None):
        out = []
        for c in self.q("SELECT * FROM corrections"):
            if c['scope_kind'] == 'inspection' and c['scope_id'] == inspection_id: out.append(c)
            elif c['scope_kind'] == 'asset' and c['scope_id'] == asset_id: out.append(c)
        return out

    def feedback_rule(self, rule_key, well_type=None):
        rows = self.q("SELECT * FROM feedback_rules WHERE rule_key=? ORDER BY id DESC", (rule_key,))
        for r in rows:
            if r['scope_kind'] == 'all': return r
            if r['scope_kind'] == 'well_type' and well_type and r['scope_id'] == well_type: return r
        return None

    def corrected_inspection(self, insp):
        """Apply context.jsonl corrections + runtime corrections. Returns dict with effective
        values and correction metadata."""
        iid, aid = insp['inspection_id'], insp['asset_id']
        eff = dict(insp)
        eff['negated'] = False   # a "not a spill" correction cancels spill detection
        eff['corrections'] = []
        for n in self.q("SELECT * FROM notes WHERE note_type='correction' AND inspection_id=?", (iid,)):
            eff['negated'] = True
            eff['corrections'].append({'by': n['author_user_id'], 'text': n['text'], 'source': n['note_id'] or 'context.jsonl'})
        for c in self.runtime_corrections(iid, aid):
            f = c['field']
            if f == 'spill_negated':
                eff['negated'] = True
            elif f in ('wellhead_temp_c', 'ambient_temp_c', 'spill_est_volume_bbl'):
                eff[f] = float(c['corrected_value'])
            eff['corrections'].append({'by': c['author_user_id'], 'text': c['reason'], 'source': 'runtime correction'})
        return eff

    def producing_threshold(self, well_type):
        r = self.feedback_rule('production_delta_c', well_type)
        return float(r['rule_value']) if r else RULES['production_delta_c'][well_type]

    def is_producing(self, eff_insp, well_type):
        return (eff_insp['wellhead_temp_c'] - eff_insp['ambient_temp_c']) >= self.producing_threshold(well_type)

    def well(self, asset_id):
        w = self.q("SELECT * FROM wells WHERE asset_id=?", (asset_id,))
        return w[0] if w else None

    def wells_in_area(self, area):
        """area can be an area_id (SW-23) or fuzzy name ('Southwest Block 23')."""
        rows = self.q("SELECT * FROM wells WHERE area_id=?", (area,))
        if not rows:
            a = self.q("SELECT * FROM areas WHERE name LIKE ?", (f'%{area}%',))
            if a: rows = self.q("SELECT * FROM wells WHERE area_id=?", (a[0]['area_id'],))
        return rows

    def latest_inspection(self, asset_id):
        r = self.q("SELECT * FROM inspections WHERE asset_id=? ORDER BY inspected_at DESC LIMIT 1", (asset_id,))
        return r[0] if r else None

    # ---- Q1/Q15 status card ----
    def well_status(self, asset_id):
        w = self.well(asset_id)
        li = self.latest_inspection(asset_id)
        eff = self.corrected_inspection(li)
        prod = self.is_producing(eff, w['well_type'])
        delta = round(eff['wellhead_temp_c'] - eff['ambient_temp_c'], 1)
        insp_name = self.q("SELECT name FROM users WHERE user_id=?", (li['inspector_user_id'],))
        # context notes valid now / recent
        notes = self.q("SELECT * FROM notes WHERE asset_id=? ORDER BY created_at DESC LIMIT 5", (asset_id,))
        shared = self.q("SELECT * FROM shared_notes WHERE asset_id=? ORDER BY id DESC", (asset_id,))
        open_repairs = self.q("""SELECT * FROM maintenance WHERE asset_id=? AND
            date(completed_at) >= date(?) ORDER BY started_at DESC LIMIT 3""", (asset_id, TODAY.isoformat()))
        return {'well': w, 'inspection': eff, 'producing': prod, 'delta_c': delta,
                'threshold': self.producing_threshold(w['well_type']),
                'inspector': insp_name[0]['name'] if insp_name else li['inspector_user_id'],
                'recorded_status': w['recorded_status'], 'notes': notes, 'shared_notes': shared,
                'open_repairs': open_repairs}

    # ---- Q2/Q12 not-producing-in-area ----
    def not_producing_in_area(self, area):
        out = []
        for w in self.wells_in_area(area):
            li = self.latest_inspection(w['asset_id'])
            if not li: continue
            eff = self.corrected_inspection(li)
            if not self.is_producing(eff, w['well_type']):
                # find a reason: open repair or recent note
                repair = self.q("""SELECT * FROM maintenance WHERE asset_id=? AND date(completed_at)>=date(?)
                    ORDER BY started_at DESC LIMIT 1""", (w['asset_id'], TODAY.isoformat()))
                note = self.q("SELECT * FROM notes WHERE asset_id=? ORDER BY created_at DESC LIMIT 1", (w['asset_id'],))
                out.append({'well': w, 'inspection': eff,
                            'delta_c': round(eff['wellhead_temp_c']-eff['ambient_temp_c'],1),
                            'recorded_status': w['recorded_status'],
                            'repair': repair[0] if repair else None,
                            'note': note[0] if note else None,
                            'inspector_note': li.get('notes')})
        return out

    # ---- Q3 temperature series ----
    def temp_series(self, asset_id, weeks=13):
        since = (TODAY - timedelta(weeks=weeks)).isoformat()
        rows = self.q("""SELECT * FROM inspections WHERE asset_id=? AND date(inspected_at)>=date(?)
                         ORDER BY inspected_at""", (asset_id, since))
        w = self.well(asset_id)
        return [{'date': r['inspected_at'][:10], 'wellhead_temp_c': r['wellhead_temp_c'],
                 'ambient_temp_c': r['ambient_temp_c'],
                 'producing': self.is_producing(self.corrected_inspection(r), w['well_type']),
                 'inspection_id': r['inspection_id']} for r in rows]

    # ---- Q4/Q7/Q8 spills ----
    def spills(self, asset_id=None, area=None, since=None, until=None):
        sql = "SELECT i.*, w.well_type, w.area_id, w.lat, w.lon FROM inspections i JOIN wells w ON w.asset_id=i.asset_id WHERE i.spill_detected=1"
        args = []
        if asset_id: sql += " AND i.asset_id=?"; args.append(asset_id)
        if since: sql += " AND date(i.inspected_at)>=date(?)"; args.append(since)
        if until: sql += " AND date(i.inspected_at)<=date(?)"; args.append(until)
        rows = self.q(sql, args)
        if area:
            wells = {w['asset_id'] for w in self.wells_in_area(area)}
            rows = [r for r in rows if r['asset_id'] in wells]
        out = []
        for r in rows:
            eff = self.corrected_inspection(r)
            if eff['negated']: continue
            out.append({**r, 'effective_volume_bbl': eff['spill_est_volume_bbl'],
                        'corrections': eff['corrections']})
        return out

    def spill_heatmap(self, year=2026):
        rows = self.spills(since=f'{year}-01-01', until=f'{year}-12-31')
        grid = {}
        for r in rows:
            a = self.q("SELECT name FROM areas WHERE area_id=?", (r['area_id'],))[0]['name']
            m = int(r['inspected_at'][5:7])
            grid.setdefault(a, {}).setdefault(m, 0)
            grid[a][m] += 1
        return grid

    # ---- Q5/Q6 downtime ----
    def downtime(self, asset_id, since, until):
        w = self.well(asset_id)
        rows = self.q("""SELECT * FROM inspections WHERE asset_id=? AND date(inspected_at)>=date(?)
                         AND date(inspected_at)<=date(?) ORDER BY inspected_at""", (asset_id, since, until))
        down = []
        for r in rows:
            eff = self.corrected_inspection(r)
            if not self.is_producing(eff, w['well_type']):
                down.append({**eff, 'delta_c': round(eff['wellhead_temp_c']-eff['ambient_temp_c'],1)})
        return {'days': len(down) * RULES['downtime_days_per_inspection'], 'inspections': down}

    def downtime_ranking(self, area, since, until):
        res = []
        for w in self.wells_in_area(area):
            d = self.downtime(w['asset_id'], since, until)
            res.append({'asset_id': w['asset_id'], 'well_type': w['well_type'], 'days': d['days']})
        res.sort(key=lambda x: -x['days'])
        return res

    # ---- Q9 compliance ----
    def compliance(self, asset_id, months=12):
        since = (TODAY - timedelta(days=int(months*30.5))).isoformat()
        w = self.well(asset_id)
        breaches, pending = [], []
        for s in self.spills(asset_id=asset_id, since=since):
            vol = s['effective_volume_bbl'] or 0
            if vol < RULES['spill_report_min_bbl']: continue
            limit = RULES['major_deadline_h'] if vol >= RULES['major_spill_bbl'] else RULES['spill_report_deadline_h']
            iid = s['inspection_id']
            filed = self.q("SELECT * FROM reg_events WHERE related_inspection_id=? AND event_type='spill_report_filed'", (iid,))
            if filed:
                h = filed[0]['hours_to_report']
                if h and h > limit:
                    nov = self.q("SELECT * FROM reg_events WHERE related_inspection_id=? AND event_type='notice_of_violation'", (iid,))
                    breaches.append({'protocol': 'SR-01', 'detail': f"Spill report filed after {h:.0f} h vs {limit:.0f} h limit (spill {vol} bbl, {s['inspected_at'][:10]})",
                        'evidence': [iid, filed[0].get('detail','')] , 'nov': nov})
            else:
                breaches.append({'protocol': 'SR-01', 'detail': f"Reportable spill ({vol} bbl, {s['inspected_at'][:10]}) has NO written notice on record (limit {limit:.0f} h)", 'evidence': [iid], 'nov': []})
            # SR-02 containment from report text
            mnt = self.q("SELECT * FROM maintenance WHERE triggered_by_inspection_id=?", (iid,))
            for m in mnt:
                rep = self._report_for(m)
                if rep:
                    mt = re.search(r'contained about ([\d.]+) hours', rep[0]['text'])
                    if mt and float(mt.group(1)) > RULES['containment_limit_h']:
                        breaches.append({'protocol': 'SR-02', 'detail': f"Contained after ~{mt.group(1)} h vs 4 h limit (only in report {m['maintenance_id']})", 'evidence': [m['maintenance_id'], iid], 'nov': []})
            # SR-03 verification within 60 days
            disc = date.fromisoformat(s['inspected_at'][:10])
            due = disc + timedelta(days=RULES['remediation_verify_days'])
            ver = self.q("""SELECT * FROM reg_events WHERE asset_id=? AND event_type='site_inspection'
                            AND date(date)>=date(?)""", (asset_id, disc.isoformat()))
            verified = any(v['status'] == 'closed' and v['date'] <= due.isoformat() for v in ver)
            if not verified:
                item = {'protocol': 'SR-03', 'detail': f"Cleanup verification due {due.isoformat()} (60 days after {disc.isoformat()})", 'evidence': [iid]}
                (breaches if due < TODAY else pending).append(item)
            # RC-04 NOV response
            for nov in self.q("SELECT * FROM reg_events WHERE related_inspection_id=? AND event_type='notice_of_violation'", (iid,)):
                due2 = date.fromisoformat(nov['date']) + timedelta(days=RULES['nov_response_days'])
                resp = self.q("""SELECT * FROM reg_events WHERE asset_id=? AND event_type='nov_response_filed'
                                 AND date(date)>=date(?)""", (asset_id, nov['date']))
                if not resp:
                    item = {'protocol': 'RC-04', 'detail': f"NOV ({nov['date']}) unanswered; response was due {due2.isoformat()}; status {nov['status']}, fine USD {nov.get('fine_usd')}", 'evidence': [nov['detail']]}
                    (breaches if due2 < TODAY else pending).append(item)
        # RCA escalation (OM-T200 §10)
        for eq in ('wellhead','pump_jack','rod_string','downhole_pump','flowline','separator'):
            cnt = self.q("""SELECT count(*) c, group_concat(maintenance_id) ids FROM maintenance
                WHERE asset_id=? AND maintenance_type='corrective' AND equipment_kind=?
                AND date(started_at)>=date(?)""", (asset_id, eq, since))[0]
            thresh = self.rca_threshold(w['well_type'])
            if cnt['c'] >= thresh:
                pending.append({'protocol': 'OM-§10 RCA', 'detail': f"{cnt['c']} corrective {eq} repairs in 12 months ({cnt['ids']}) — root-cause analysis due", 'evidence': [cnt['ids']]})
        # RC-02 yearly flowline test
        yr = TODAY.year
        t = self.q("""SELECT * FROM maintenance WHERE asset_id=? AND issue LIKE '%flowline integrity test%'
                      AND strftime('%Y', started_at)=?""", (asset_id, str(yr)))
        return {'breaches': breaches, 'pending': pending,
                'rc02': {'done': bool(t), 'when': t[0]['started_at'][:10] if t else None}}

    def rca_threshold(self, well_type):
        r = self.feedback_rule('rca_count', well_type)
        return int(float(r['rule_value'])) if r else RULES['rca_corrective_count']

    # ---- Q14 RCA across area ----
    def rca_wells(self, area, equipment_kind='pump_jack', months=12):
        since = (TODAY - timedelta(days=int(months*30.5))).isoformat()
        out = []
        for w in self.wells_in_area(area):
            th = self.rca_threshold(w['well_type'])
            cnt = self.q("""SELECT count(*) c, group_concat(maintenance_id) ids FROM maintenance
                WHERE asset_id=? AND maintenance_type='corrective' AND equipment_kind=?
                AND date(started_at)>=date(?)""", (w['asset_id'], equipment_kind, since))[0]
            if cnt['c'] >= th:
                out.append({'asset_id': w['asset_id'], 'well_type': w['well_type'],
                            'count': cnt['c'], 'threshold_used': th, 'ids': cnt['ids']})
        return out

    # ---- Q11 repair costs ----
    def repair_costs(self, asset_id, year=None, since=None):
        sql = "SELECT sum(cost_total) t, count(*) c, group_concat(maintenance_id) ids FROM maintenance WHERE asset_id=?"
        args = [asset_id]
        if year: sql += " AND strftime('%Y', started_at)=?"; args.append(str(year))
        if since: sql += " AND date(started_at)>=date(?)"; args.append(since)
        return self.q(sql, args)[0]

    def containment_violations(self, area=None, since=None, limit_h=None):
        """Q10: spills whose reports show containment over the SR-02 limit."""
        limit_h = limit_h or RULES['containment_limit_h']
        out = []
        for s in self.spills(area=area, since=since, until=TODAY.isoformat()):
            for m in self.q("SELECT * FROM maintenance WHERE triggered_by_inspection_id=?", (s['inspection_id'],)):
                rep = self._report_for(m)
                if rep:
                    mt = re.search(r'contained about ([\d.]+) hours', rep[0]['text'])
                    if mt and float(mt.group(1)) > limit_h:
                        out.append({'asset_id': s['asset_id'], 'hours': float(mt.group(1)),
                                    'maintenance_id': m['maintenance_id'], 'inspection_id': s['inspection_id']})
        return out


    def _report_for(self, maint_row):
        """Find the free-text report for a maintenance row via its report_file basename
        (file names vary; maintenance_id is not the report key)."""
        rf = maint_row.get('report_file')
        if not rf: return None
        base = rf.rsplit('/', 1)[-1].replace('.md', '')
        r = self.q("SELECT text FROM reports WHERE asset_id=? AND maintenance_id=?",
                   (maint_row['asset_id'], base))
        return r if r else None

    def reportable_unreported(self, area, days=90):
        """Q15b: corrected-volume spills >=5 bbl with no filed report."""
        since = (TODAY - timedelta(days=days)).isoformat()
        out = []
        for s in self.spills(area=area, since=since):
            if (s['effective_volume_bbl'] or 0) >= RULES['spill_report_min_bbl']:
                filed = self.q("SELECT * FROM reg_events WHERE related_inspection_id=? AND event_type='spill_report_filed'", (s['inspection_id'],))
                out.append({**s, 'report_filed': bool(filed),
                            'hours_to_report': filed[0]['hours_to_report'] if filed else None})
        return out
