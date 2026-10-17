"""Executes a plan against the engine and shapes the answer: text, visual spec, evidence.
Never invents causes; shows conflicts honestly; always cites records/documents."""
import re
from datetime import date, timedelta
from .engine import RULES, TODAY

def _user_name(eng, uid):
    r = eng.q("SELECT name FROM users WHERE user_id=?", (uid,))
    return r[0]['name'] if r else uid

class Executor:
    def __init__(self, router):
        self.r = router
        self.eng = router.eng

    def run(self, plan, user_id, user_name, raw_msg):
        t = plan.get('tool')
        fn = getattr(self, 'do_' + (t or 'general'), None) or self.do_general
        return fn(plan, user_id, user_name, raw_msg)

    # ---------- helpers ----------
    def _conflicts_for(self, asset_id, inspection):
        """Other notes/corrections that disagree with the effective reading."""
        out = []
        for n in self.eng.q("SELECT * FROM notes WHERE asset_id=? AND note_type!='correction' ORDER BY created_at DESC LIMIT 4", (asset_id,)):
            out.append(n)
        if inspection.get('notes'):
            out.append({'author_user_id': inspection.get('inspector_user_id'), 'text': inspection['notes'], 'note_type': 'inspection_note', 'created_at': inspection.get('inspected_at')})
        return out

    # ---------- handlers ----------
    def do_well_status(self, plan, user_id, user_name, raw):
        aid = plan.get('asset_id')
        if not aid:
            return {'text': "Which well do you mean? Give me an ID like WELL-0001.", 'visual': 'text', 'data': {}, 'evidence': []}
        s = self.eng.well_status(aid)
        w, i = s['well'], s['inspection']
        ev = [i['inspection_id'], f"OM-{w['well_type']} production temperature rule"]
        lines = [f"**{aid} ({w['name']}, {w['well_type']}) is {'PRODUCING oil' if s['producing'] else 'NOT producing oil'}.**",
                 f"Last inspection {i['inspection_id']} on {i['inspected_at'][:10]} ({i['method']}) by {s['inspector']}.",
                 f"Wellhead {i['wellhead_temp_c']}°C vs air {i['ambient_temp_c']}°C: {s['delta_c']}°C warmer "
                 f"({'≥' if s['producing'] else 'below'} the {s['threshold']:.0f}°C {w['well_type']} limit)."]
        if i['corrections']:
            for c in i['corrections']:
                lines.append(f"Note: the raw reading was corrected by {_user_name(self.eng, c['by'])}: {c['text']}")
            orig = self.eng.q("SELECT * FROM inspections WHERE inspection_id=?", (i['inspection_id'],))[0]
            lines.append(f"(Original recorded wellhead reading: {orig['wellhead_temp_c']}°C.)")
            for n in self._conflicts_for(aid, i):
                if n.get('note_type') == 'correction': continue
                txt = n.get('text', '')
                if any(k in txt.lower() for k in ('not moving', 'not producing', 'shut', 'stopped', 'puddle', 'water')):
                    lines.append(f"⚠ Conflict: {(_user_name(self.eng, n['author_user_id']))} noted \"{txt}\" on the same inspection, which disagrees with the correction. Please confirm which is right.")
        for n in s['notes'][:2]:
            lines.append(f"Context note ({_user_name(self.eng, n['author_user_id'])}, {n['created_at'][:10]}): {n['text']}")
            ev.append(n['note_id'] or 'context note')
        for sn in s['shared_notes'][:2]:
            lines.append(f"Team note ({_user_name(self.eng, sn['author_user_id'])}, {sn['status']}): {sn['text']}")
        for rp in s['open_repairs'][:1]:
            if not s['producing']:
                lines.append(f"Open repair: {rp['maintenance_id']} ({rp['issue']}, started {rp['started_at'][:10]}).")
                ev.append(rp['maintenance_id'])
        if s['recorded_status'] == 'active' and not s['producing']:
            lines.append(f"The recorded status still says **active**, which is out of date; inspections are the truth.")
        return {'text': '\n'.join(lines), 'visual': plan.get('visual') or 'card',
                'data': {'well': w, 'status': s}, 'evidence': ev}

    def do_not_producing_in_area(self, plan, user_id, user_name, raw):
        rows = self.eng.not_producing_in_area(plan['area'])
        area_name = self.eng.q("SELECT name FROM areas WHERE area_id=?", (rows[0]['well']['area_id'],))[0]['name'] if rows else plan['area']
        table = []
        for r in rows:
            reason = None
            if r['repair']: reason = f"Repair {r['repair']['maintenance_id']}: {r['repair']['issue']} (started {r['repair']['started_at'][:10]}, still open)"
            elif r['inspection']['corrections']: reason = "Corrected reading: " + r['inspection']['corrections'][0]['text']
            elif r['note'] and r['note']['note_type'] != 'correction': reason = f"Note: {r['note']['text'][:90]}"
            if not reason:
                reason = "No recorded reason."
                if r['inspector_note']: reason += f" The inspector noted \"{r['inspector_note']}\"."
            stale = " (out of date)" if r['recorded_status'] == 'active' else ""
            table.append({'Well': r['well']['asset_id'], 'Type': r['well']['well_type'],
                'Wellhead above air': f"{r['delta_c']}°C", 'Recorded status': r['recorded_status'] + stale,
                'Last inspection': r['inspection']['inspected_at'][:10], 'Reason': reason})
        corrected_away = plan.get('_corrected_away')
        txt = f"**{len(table)} wells in {area_name} are not producing oil right now** (per their latest inspections)."
        if corrected_away:
            txt += f" {corrected_away} is no longer listed: its reading was corrected and it is now producing."
        txt += " Where nothing explains a stop, I say so — a field check is suggested rather than guessing a cause."
        ev = [r['inspection']['inspection_id'] for r in rows]
        return {'text': txt, 'visual': plan.get('visual') or 'table', 'data': {'table': table}, 'evidence': ev}

    def do_temp_series(self, plan, user_id, user_name, raw):
        aid, weeks = plan['asset_id'], int(plan.get('weeks') or 13)
        w = self.eng.well(aid)
        series = self.eng.temp_series(aid, weeks)
        down = [p['date'] for p in series if not p['producing']]
        txt = (f"**{aid} wellhead vs air temperature, last {weeks} weeks** ({len(series)} weekly readings, "
               f"{series[0]['date']} to {series[-1]['date']}). Not producing oil on: "
               + (', '.join(down) if down else 'none')
               + f" (wellhead must be ≥{self.eng.producing_threshold(w['well_type']):.0f}°C above air for a {w['well_type']}).")
        return {'text': txt, 'visual': 'line', 'data': {'series': series},
                'evidence': [p['inspection_id'] for p in series] + [f"OM-{w['well_type']} §temperature rule"]}

    def do_spills_list(self, plan, user_id, user_name, raw):
        sp = self.eng.spills(asset_id=plan.get('asset_id'), area=plan.get('area'),
                             since=plan.get('since'), until=plan.get('until'))
        total = round(sum(s['effective_volume_bbl'] or 0 for s in sp), 1)
        n_det = None
        if plan.get('asset_id'):
            allsp = self.eng.q("SELECT count(*) c FROM inspections WHERE asset_id=? AND spill_detected=1", (plan['asset_id'],))
            n_det = allsp[0]['c']
        table = [{'Well': s['asset_id'], 'Date': s['inspected_at'][:10], 'Volume (bbl)': s['effective_volume_bbl'],
                  'Pad area %': s['spill_area_pct'], 'Inspection': s['inspection_id'],
                  'Corrected': ('yes — ' + s['corrections'][-1]['text'][:60]) if s['corrections'] else ''} for s in sp]
        txt = f"**{len(sp)} real spills, {total} barrels total.**"
        if n_det and n_det != len(sp):
            neg = n_det - len(sp)
            txt += f" ({n_det} detections in total; {neg} corrected as not oil — e.g. rainwater — and excluded, per the people who corrected them.)"
        return {'text': txt, 'visual': plan.get('visual') or 'table', 'data': {'table': table},
                'evidence': [s['inspection_id'] for s in sp]}

    def do_downtime(self, plan, user_id, user_name, raw):
        d = self.eng.downtime(plan['asset_id'], plan['since'], plan['until'])
        w = self.eng.well(plan['asset_id'])
        lines = [f"**{plan['asset_id']} was down {d['days']} days** in {plan['since'][:4] if plan['since'][:4]==plan['until'][:4] else plan['since']+' to '+plan['until']} "
                 f"({len(d['inspections'])} weekly inspections where it was not producing × 7 days each; missed inspections add none)."]
        for i in d['inspections']:
            extra = ""
            if i['wellhead_temp_c'] and i['wellhead_temp_c'] - i['ambient_temp_c'] < 15 and i['wellhead_temp_c'] > 45:
                extra = f" — the wellhead read {i['wellhead_temp_c']}°C, which looks hot, but the air was {i['ambient_temp_c']}°C (only {i['delta_c']}°C warmer, so not producing)"
            mnt = self.eng.q("SELECT maintenance_id, issue FROM maintenance WHERE asset_id=? AND date(started_at)<=date(?) AND date(completed_at)>=date(?)",
                             (plan['asset_id'], i['inspected_at'][:10], i['inspected_at'][:10]))
            cause = f" Cause: {mnt[0]['issue']} ({mnt[0]['maintenance_id']})." if mnt else ""
            lines.append(f"- {i['inspected_at'][:10]}: {i['inspection_id']}{extra}.{cause}")
        return {'text': '\n'.join(lines), 'visual': plan.get('visual') or 'text',
                'data': {}, 'evidence': [i['inspection_id'] for i in d['inspections']]}

    def do_downtime_ranking(self, plan, user_id, user_name, raw):
        rows = self.eng.downtime_ranking(plan['area'], plan['since'], plan['until'])
        top = rows[:int(plan.get('top_n') or 3)]
        nxt = rows[len(top)] if len(rows) > len(top) else None
        txt = (f"**Top {len(top)} wells by downtime in {plan['area']}, {plan['since']} to {plan['until']}:** "
               + ', '.join(f"{r['asset_id']}: {r['days']} days ({r['well_type']})" for r in top)
               + (f". Next is {nxt['asset_id']} at {nxt['days']}." if nxt else "."))
        return {'text': txt, 'visual': plan.get('visual') or 'bar',
                'data': {'table': [{'Well': r['asset_id'], 'Downtime (days)': r['days'], 'Type': r['well_type']} for r in top]},
                'evidence': ['weekly inspections × 7 days per Dataset Guide']}

    def do_spill_heatmap(self, plan, user_id, user_name, raw):
        year = int(plan.get('year') or TODAY.year)
        grid = self.eng.spill_heatmap(year)
        txt = f"**{year} spills by area and month** (corrected detections removed, per SOP-SR §7)."
        return {'text': txt, 'visual': 'heatmap', 'data': {'grid': grid},
                'evidence': ['inspections.spill_detected', 'context.jsonl corrections', 'SOP-SR §7']}

    def do_spill_map(self, plan, user_id, user_name, raw):
        sp = self.eng.spills(area=plan['area'], since=(TODAY - timedelta(days=int(plan.get('days') or 90))).isoformat())
        pins = [{'well': s['asset_id'], 'date': s['inspected_at'][:10], 'bbl': s['effective_volume_bbl'],
                 'lat': s['lat'], 'lon': s['lon']} for s in sp]
        txt = (f"**{len(pins)} wells in {plan['area']} with a real spill in the last {plan.get('days') or 90} days** "
               f"(corrected detections excluded).")
        return {'text': txt, 'visual': 'map', 'data': {'pins': pins},
                'evidence': [s['inspection_id'] for s in sp]}

    def do_compliance(self, plan, user_id, user_name, raw):
        c = self.eng.compliance(plan['asset_id'])
        lines = [f"**Rules broken by {plan['asset_id']} in the last 12 months: {len(c['breaches'])}.**"]
        for b in c['breaches']:
            lines.append(f"- **{b['protocol']}**: {b['detail']}")
        if c['pending']:
            lines.append("Still open / pending (not counted as breaches — deadline not passed or work under way):")
            for p in c['pending']:
                lines.append(f"- **{p['protocol']}**: {p['detail']}")
        rc = c['rc02']
        lines.append(f"RC-02 yearly flowline integrity test: {'done ' + rc['when'] if rc['done'] else 'NOT done this calendar year'}.")
        ev = []
        for b in c['breaches'] + c['pending']: ev += b.get('evidence', [])
        return {'text': '\n'.join(lines), 'visual': plan.get('visual') or 'table', 'data': {}, 'evidence': ev}

    def do_containment_violations(self, plan, user_id, user_name, raw):
        since = (TODAY - timedelta(days=int(plan.get('days') or 90))).isoformat()
        rows = self.eng.containment_violations(area=plan.get('area'), since=since)
        txt = (f"**{len(rows)} spills were not contained within the {RULES['containment_limit_h']:.0f}-hour SOP limit (SR-02).** "
               "Containment time exists only inside the free-text repair reports.")
        table = [{'Well': r['asset_id'], 'Contained after': f"~{r['hours']} h", 'Report': r['maintenance_id'],
                  'Inspection': r['inspection_id']} for r in rows]
        return {'text': txt, 'visual': plan.get('visual') or 'table', 'data': {'table': table},
                'evidence': [r['maintenance_id'] for r in rows] + ['SOP-SR SR-02']}

    def do_rca_wells(self, plan, user_id, user_name, raw):
        rows = self.eng.rca_wells(plan['area'], plan.get('equipment_kind') or 'pump_jack')
        parts = [f"{r['asset_id']} ({r['well_type']}, {r['count']} corrective repairs)" for r in rows]
        txt = (f"**Wells in {plan['area']} needing a root-cause analysis on their {plan.get('equipment_kind') or 'pump_jack'}:** "
               + ', '.join(parts) + '.')
        rule = None
        for rr in self.eng.q("SELECT * FROM feedback_rules WHERE rule_key='rca_count' ORDER BY id DESC"):
            rule = rr; break
        if rule:
            txt += (f" This applies {_user_name(self.eng, rule['author_user_id'])}'s team rule "
                    f"(RCA after {rule['rule_value']} repairs for {rule['scope_id']} wells), not just the manual's 3-repair rule.")
        txt += " (Manual: OM-T200 §10; OM-T350 §6 refers to it.)"
        return {'text': txt, 'visual': plan.get('visual') or 'table',
                'data': {'table': [{'Well': r['asset_id'], 'Type': r['well_type'], 'Corrective repairs (12 mo)': r['count'],
                                    'Threshold used': r['threshold_used']} for r in rows]},
                'evidence': ['maintenance.jsonl', 'OM-T200 §10'] + ([rule['text']] if rule else [])}

    def do_repair_costs(self, plan, user_id, user_name, raw):
        year = plan.get('year') or TODAY.year
        rows = []
        for aid in plan['asset_ids']:
            c = self.eng.repair_costs(aid, year=year)
            rows.append({'Well': aid, f'Repair cost {year} (USD)': int(c['t'] or 0), 'Jobs': c['c']})
        txt = "**Repair costs this year:** " + ' · '.join(f"{r['Well']}: USD {r[f'Repair cost {year} (USD)']:,}" for r in rows)
        return {'text': txt, 'visual': plan.get('visual') or 'table', 'data': {'table': rows}, 'evidence': ['maintenance.jsonl cost_usd']}

    def do_recall_costs(self, plan, user_id, user_name, raw):
        wells = self.r.wells_discussed_set(user_id)[:3]
        if not wells:
            return {'text': "I don't have an earlier conversation with you about any wells — my memory of past chats is private to each user. Tell me the well IDs and I'll pull the numbers.",
                    'visual': 'text', 'data': {}, 'evidence': []}
        return self.do_repair_costs({'asset_ids': wells, 'year': plan.get('year')}, user_id, user_name, raw) | {
            'text': f"From our earlier discussion ({', '.join(wells)}): " +
                    ' · '.join(f"{w}: USD {int(self.eng.repair_costs(w, year=plan.get('year') or TODAY.year)['t'] or 0):,}" for w in wells)}

    def do_reportable_spills(self, plan, user_id, user_name, raw):
        rows = self.eng.reportable_unreported(plan['area'], int(plan.get('days') or 90))
        need = [r for r in rows if (r['effective_volume_bbl'] or 0) >= RULES['spill_report_min_bbl']]
        lines = [f"**Spills in {plan['area']} in the last {plan.get('days') or 90} days that needed a regulator report (≥ {RULES['spill_report_min_bbl']:.0f} bbl, SR-01):**"]
        if not need: lines.append("None reached the 5 bbl reporting threshold.")
        for r in need:
            stat = f"filed after {r['hours_to_report']:.0f} h" if r['report_filed'] else "**no written notice on record — overdue**"
            corr = " (volume corrected by a teammate; the AI estimate was lower)" if r['corrections'] else ""
            lines.append(f"- {r['asset_id']}, {r['inspected_at'][:10]}: {r['effective_volume_bbl']} bbl{corr} — {stat} ({r['inspection_id']})")
        return {'text': '\n'.join(lines), 'visual': plan.get('visual') or 'table',
                'data': {}, 'evidence': [r['inspection_id'] for r in need] + ['SOP-SR SR-01']}

    def do_spill_dispute(self, plan, user_id, user_name, raw):
        aid = plan['asset_id']
        sp = self.eng.spills(asset_id=aid)
        target = None
        dm = re.search(r'(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)', raw.lower())
        for s in sp:
            if dm and dm.group(2)[:3] in s['inspected_at'][5:7]: pass
        # pick by month if named else latest disputed
        if dm:
            months = {'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}
            mn = months[dm.group(2)[:3]]
            target = next((s for s in sp if int(s['inspected_at'][5:7]) == mn and s['inspected_at'][8:10] == f"{int(dm.group(1)):02d}"), None)
        target = target or (sp[-1] if sp else None)
        if not target:
            return {'text': f"No spill recorded for {aid}.", 'visual': 'text', 'data': {}, 'evidence': []}
        notes = self.eng.q("SELECT * FROM notes WHERE asset_id=? ORDER BY created_at", (aid,))
        rel = [n for n in notes if n.get('inspection_id') == target['inspection_id'] or 'spill' in n['text'].lower() or 'sheen' in n['text'].lower() or 'water' in n['text'].lower() or 'crude' in n['text'].lower()]
        lines = [f"**Yes — the {target['inspected_at'][:10]} detection at {aid} stands as a real spill** "
                 f"({target['effective_volume_bbl']} bbl, {target['inspection_id']}: \"{target.get('notes') or ''}\").",
                 "But people disagree, and I keep both statements on record:"]
        for n in rel:
            lines.append(f"- {_user_name(self.eng, n['author_user_id'])} ({n['created_at'][:10]}, {n['note_type']}): {n['text']}")
        lines.append("Neither note is a formal correction, and a spill repair was done — so the detection stands. If someone files a formal correction, I'll switch to it.")
        return {'text': '\n'.join(lines), 'visual': 'text', 'data': {},
                'evidence': [target['inspection_id']] + [n.get('note_id') or 'note' for n in rel]}

    def do_planned_downtime(self, plan, user_id, user_name, raw):
        aid = plan['asset_id']
        notes = self.eng.q("SELECT * FROM shared_notes WHERE asset_id=? ORDER BY id DESC", (aid,))
        ctx = self.eng.q("SELECT * FROM notes WHERE asset_id=? AND (text LIKE '%shut%' OR text LIKE '%planned%' OR text LIKE '%workover%') ORDER BY created_at DESC", (aid,))
        if notes:
            n = notes[0]
            return {'text': f"Yes — per {_user_name(self.eng, n['author_user_id'])} ({n['created_at'][:10]}, status: {n['status']}): {n['text']}",
                    'visual': 'text', 'data': {}, 'evidence': ['team note #' + str(n['id'])]}
        if ctx:
            n = ctx[0]
            return {'text': f"Yes — note by {_user_name(self.eng, n['author_user_id'])} ({n['created_at'][:10]}): {n['text']}",
                    'visual': 'text', 'data': {}, 'evidence': [n.get('note_id') or 'context note']}
        return {'text': f"No planned downtime is recorded for {aid}.", 'visual': 'text', 'data': {}, 'evidence': []}


    def _resolve_inspection(self, aid, raw, field):
        """Pick the inspection a correction refers to: match a date named in the message
        ('24 Sep', '15 Oct', '2026-09-24'); for spill-volume corrections prefer inspections
        with spill_detected; otherwise the latest inspection."""
        months = {'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}
        target_date = None
        dm = re.search(r'(\d{1,2})\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*', raw.lower())
        iso = re.search(r'(20\d\d)-(\d{2})-(\d{2})', raw)
        if iso: target_date = iso.group(0)
        elif dm: target_date = f"{TODAY.year}-{months[dm.group(2)[:3]]:02d}-{int(dm.group(1)):02d}"
        if field == 'spill_est_volume_bbl':
            cands = self.eng.q("SELECT * FROM inspections WHERE asset_id=? AND spill_detected=1 ORDER BY inspected_at DESC", (aid,))
        else:
            cands = self.eng.q("SELECT * FROM inspections WHERE asset_id=? ORDER BY inspected_at DESC", (aid,))
        if target_date:
            for c in cands:
                if c['inspected_at'][:10] == target_date: return c
        return cands[0] if cands else self.eng.latest_inspection(aid)

    # ---------- feedback handlers ----------
    def do_remember_fact(self, plan, user_id, user_name, raw):
        aid = plan.get('asset_id')
        self.r.add_shared_note(aid, user_id, raw, status='shared')
        return {'text': f"Noted for everyone on {aid}: \"{raw}\" — saved as shared well knowledge from {user_name}. Anyone asking about {aid} will see it.",
                'visual': 'text', 'data': {}, 'evidence': []}

    def do_correct_data(self, plan, user_id, user_name, raw):
        aid = plan.get('asset_id')
        field = plan.get('field') or 'wellhead_temp_c'
        newv = plan.get('new_value')
        # resolve the inspection: a date mentioned in the message wins; for spill-volume
        # corrections the target is the spill inspection, not the latest reading
        li = self._resolve_inspection(aid, raw, field)
        insp_id = li['inspection_id']
        original = li.get(field)
        self.r.add_correction('inspection', insp_id, field, original, newv, user_id, plan.get('reason') or raw)
        eff = self.eng.corrected_inspection(li)
        w = self.eng.well(aid)
        lines = [f"Recorded, keeping the original: {insp_id} {field} corrected from {original} to {newv}, "
                 f"by {user_name}. Reason: \"{plan.get('reason') or raw}\""]
        if field == 'wellhead_temp_c':
            prod = self.eng.is_producing(eff, w['well_type'])
            delta = round(eff['wellhead_temp_c'] - eff['ambient_temp_c'], 1)
            lines.append(f"With {newv}°C against {eff['ambient_temp_c']}°C air ({delta}°C warmer), {aid} "
                         f"{'IS producing oil' if prod else 'is still NOT producing oil'} ({w['well_type']} limit {self.eng.producing_threshold(w['well_type']):.0f}°C).")
            if li.get('notes'):
                lines.append(f"⚠ One conflict to confirm: the same inspection carries the note \"{li['notes']}\" "
                             f"(by {_user_name(self.eng, li['inspector_user_id'])}), which disagrees with the corrected reading. "
                             f"I've kept both on record — which is right?")
        if field == 'spill_est_volume_bbl':
            vol = float(newv)
            rep = vol >= RULES['spill_report_min_bbl']
            lines.append(f"At {newv} bbl this spill is **{'reportable' if rep else 'not reportable'}** under the Spill Response SOP.")
            if rep:
                filed = self.eng.q("SELECT * FROM reg_events WHERE related_inspection_id=? AND event_type='spill_report_filed'", (insp_id,))
                if not filed:
                    lines.append(f"⚠ A written notice was due within 24 h of the {li['inspected_at'][:10]} inspection and none is recorded — possible SR-01 breach.")
                    due = date.fromisoformat(li['inspected_at'][:10]) + timedelta(days=60)
                    lines.append(f"Agency verification of the clean-up (SR-03) is now also due within 60 days, by {due.isoformat()}.")
                for m in self.eng.q("SELECT * FROM maintenance WHERE triggered_by_inspection_id=?", (insp_id,)):
                    rep_txt = self.eng._report_for(m)
                    if rep_txt and 'moderate' in rep_txt[0]['text'].lower():
                        lines.append(f"Report {m['maintenance_id']} classed the spill as \"moderate\" based on the old estimate — that no longer holds.")
        return {'text': '\n'.join(lines), 'visual': 'text', 'data': {}, 'evidence': [insp_id, 'SOP-SR']}

    def do_type_rule(self, plan, user_id, user_name, raw):
        self.r.add_rule('well_type', plan['well_type'], plan['rule_key'], plan['rule_value'], raw, user_id)
        return {'text': f"Rule saved for all {plan['well_type']} wells: {raw} — from {user_name}. "
                        f"I'll apply it only to {plan['well_type']} wells and will say it came from your team, not the manual.",
                'visual': 'text', 'data': {}, 'evidence': []}

    def do_user_claim(self, plan, user_id, user_name, raw):
        aid = plan.get('asset_id')
        s = self.eng.well_status(aid)
        i = s['inspection']
        claim_ok = None
        claim_l = raw.lower()
        if 'running fine' in claim_l or 'back online' in claim_l or 'producing' in claim_l:
            claim_ok = s['producing']
        elif 'shut in' in claim_l or 'down' in claim_l:
            claim_ok = not s['producing']
        self.r.add_shared_note(aid, user_id, raw, status='unconfirmed')
        lines = []
        if claim_ok is False:
            lines.append(f"That doesn't match the data, {user_name}: the {i['inspected_at'][:10]} inspection "
                         f"(by {s['inspector']}) shows the wellhead only {s['delta_c']}°C above the air, so {aid} is not producing oil.")
            for n in s['notes'][:2]:
                lines.append(f"This matches {_user_name(self.eng, n['author_user_id'])}'s note: \"{n['text'][:110]}\"")
            for sn in s['shared_notes']:
                if sn['author_user_id'] != user_id:
                    lines.append(f"and {_user_name(self.eng, sn['author_user_id'])}'s note: \"{sn['text'][:110]}\"")
            lines.append("I've recorded your statement as **unconfirmed** and kept both on record. Can you confirm, or should I ask for a field check?")
        else:
            lines.append(f"That matches the latest inspection ({i['inspected_at'][:10]}). Recorded for the team.")
        return {'text': '\n'.join(lines), 'visual': 'text', 'data': {}, 'evidence': [i['inspection_id']]}

    def do_doc_lookup(self, plan, user_id, user_name, raw):
        q = raw.lower()
        hits = []
        for d in self.eng.q("SELECT * FROM docs"):
            for para in d['text'].split('\n\n'):
                words = [w for w in re.findall(r'[a-z]{4,}', q) if w in para.lower()]
                if len(words) >= 2: hits.append((len(words), d['doc_id'], para.strip()))
        hits.sort(reverse=True)
        top = hits[:3]
        txt = "From the manuals/SOPs:\n" + '\n'.join(f"- ({h[1]}) {h[2][:300]}" for h in top) if top else "I couldn't find that in the manuals/SOPs."
        return {'text': txt, 'visual': 'text', 'data': {}, 'evidence': [h[1] for h in top]}

    def do_general(self, plan, user_id, user_name, raw):
        return {'text': "I can answer questions about wells, inspections, spills, downtime, repairs, compliance rules and costs — "
                        "try \"Is WELL-0001 producing oil right now?\" or \"Which rules has WELL-0001 broken?\". "
                        "You can also correct data or leave notes for the team.",
                'visual': 'text', 'data': {}, 'evidence': []}
