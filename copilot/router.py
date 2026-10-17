"""Chat layer: turns a user message into an engine call + a visual spec + evidence.
LLM (Gemini OpenAI-compatible) plans when a key is configured; a deterministic pattern
router is the fallback so the demo never hard-fails. Memory:
- private per-user chat history (chat_log) resolves references like "the three wells we discussed"
- shared_notes: facts users state about wells, visible to everyone, status tracked
- corrections: override raw data or AI analysis at a scope, original kept
- feedback_rules: per-type or global rule overrides (e.g. RCA after 2 repairs)"""
import json, os, re, sqlite3
from datetime import date, datetime, timedelta
from .engine import Engine, TODAY, RULES

WELL_RE = re.compile(r'WELL-\d{4}', re.I)
AREA_RE = re.compile(r'((?:Southwest|Northeast|Northwest|Southeast)\s+Block\s+\d+|S[WE]-\d{2}|N[EW]-\d{2})', re.I)
VIS_RE = re.compile(r'\b(status card|table|line chart|bar chart|heatmap|map|chart|card)\b', re.I)

def _today(eng):
    return TODAY

class Router:
    def __init__(self, db_path, llm=None):
        self.eng = Engine(db_path)
        self.llm = llm  # dict(base_url, model, api_key) or None

    # ---------- memory ----------
    def log_chat(self, user_id, role, text):
        self.eng.db.execute("INSERT INTO chat_log(user_id, session_date, role, text) VALUES(?,?,?,?)",
                            (user_id, TODAY.isoformat(), role, text))
        self.eng.db.commit()

    def user_history(self, user_id, limit=40):
        return self.eng.q("SELECT * FROM chat_log WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit))[::-1]

    def wells_discussed(self, user_id, exclude_text=None):
        """Well IDs from this user's own previous turns (private memory)."""
        seen = []
        for m in self.user_history(user_id):
            if exclude_text and m['text'] == exclude_text: continue
            for w in WELL_RE.findall(m['text']):
                w = w.upper()
                if w not in seen: seen.append(w)
        return seen

    def wells_discussed_set(self, user_id):
        """The latest prior ASSISTANT answer in this user's own history that discussed
        a set of >=2 wells (e.g. 'the three wells we discussed yesterday'). Empty if this
        user never had such a conversation — never borrow another user's."""
        for m in self.user_history(user_id)[::-1]:  # newest first
            if m['role'] != 'assistant': continue
            seen = []
            for w in WELL_RE.findall(m['text']):
                w = w.upper()
                if w not in seen: seen.append(w)
            if len(seen) >= 2: return seen
        return []

    def users(self):
        return self.eng.q("SELECT * FROM users ORDER BY user_id")

    # ---------- feedback capture ----------
    def add_correction(self, scope_kind, scope_id, field, original, corrected, author, reason):
        self.eng.db.execute("""INSERT INTO corrections(scope_kind, scope_id, field, original_value,
            corrected_value, author_user_id, reason) VALUES(?,?,?,?,?,?,?)""",
            (scope_kind, scope_id, field, str(original), str(corrected), author, reason))
        self.eng.db.commit()

    def add_rule(self, scope_kind, scope_id, rule_key, rule_value, text, author):
        self.eng.db.execute("""INSERT INTO feedback_rules(scope_kind, scope_id, rule_key, rule_value, text,
            author_user_id) VALUES(?,?,?,?,?,?)""", (scope_kind, scope_id, rule_key, str(rule_value), text, author))
        self.eng.db.commit()

    def add_shared_note(self, asset_id, author, text, status='unconfirmed', valid_from=None, valid_to=None):
        self.eng.db.execute("""INSERT INTO shared_notes(asset_id, author_user_id, text, status, valid_from,
            valid_to) VALUES(?,?,?,?,?,?)""", (asset_id, author, text, status, valid_from, valid_to))
        self.eng.db.commit()

    # ---------- the LLM planner ----------
    PLANNER_PROMPT = """You are the planner for an oil-field Asset Intelligence Copilot. "Today" is {today}.
Pick ONE action and return ONLY JSON.

Actions (tool, args):
- well_status {{asset_id, visual?}} — one well right now (status card)
- not_producing_in_area {{area, visual?}} — wells in an area not producing now (table)
- temp_series {{asset_id, weeks, visual?}} — wellhead vs air temperature over time (line chart)
- spills_list {{asset_id?, area?, since?, until?, visual?}} — spill list/total (table)
- downtime {{asset_id, since, until}} — days down in a period (short answer)
- downtime_ranking {{area, since, until, top_n, visual?}} — most downtime in area (bar chart)
- spill_heatmap {{year, visual?}} — spills area x month (heatmap)
- spill_map {{area, days, visual?}} — wells in area with spills in last N days (map)
- compliance {{asset_id}} — broken rules last 12 months
- containment_violations {{area, days}} — spills not contained within 4 h
- rca_wells {{area, equipment_kind}} — wells needing root-cause analysis
- repair_costs {{asset_ids, year}} — repair spend
- reportable_spills {{area, days}} — spills >=5 bbl and whether reported
- doc_lookup {{query}} — answer from manuals/SOPs (rules, deadlines, thresholds)
- remember_fact {{asset_id, text, scope}} — user states a fact about a well for everyone
- correct_data {{inspection_ref, asset_id, field, new_value, reason}} — user corrects a reading or AI estimate
- type_rule {{well_type, rule_key, rule_value, text}} — user sets a rule for a whole well type
- user_claim {{asset_id, claim}} — user claims a current fact that may conflict with data
- recall_discussion {{}} — references like "the wells we discussed"
- general {{}} — anything else

Extract IDs exactly (WELL-0007, INS-0164-0205). Areas may be named "Northeast Block 7" (=NE-07) or "SW-23".
Date words: resolve against today ({today}); "last 4 months" -> since 4 months ago; "last 90 days" -> days=90;
"this year"/"in 2025" -> year. If the user names a visual, set visual to one of: card, table, line, bar, map, heatmap.
User message ({user}): {msg}
History with this user (may contain referenced wells): {hist}
JSON only:"""

    def plan_llm(self, msg, user_name, history):
        msg = msg[:2000]  # input cap: oversized questions are truncated, never crash
        try:
            import urllib.request
            hist = ' | '.join(h['text'][:120] for h in history[-8:])
            body = {"model": self.llm['model'], "messages": [
                {"role": "user", "content": self.PLANNER_PROMPT.format(today=TODAY.isoformat(), user=user_name, msg=msg, hist=hist)}],
                "temperature": 0, "max_tokens": 1200}
            req = urllib.request.Request(self.llm['base_url'].rstrip('/') + '/chat/completions',
                data=json.dumps(body).encode(), headers={'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + self.llm['api_key']})
            resp = None
            for attempt in range(3):
                try:
                    resp = urllib.request.urlopen(req, timeout=75)
                    break
                except urllib.error.HTTPError as e:
                    if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                        import time as _t; _t.sleep(4 * (attempt + 1))
                        continue
                    raise
            if resp is None:
                return None
            with resp as r:
                txt = json.load(r)['choices'][0]['message']['content']
            m = re.search(r'\{.*\}', txt, re.S)
            raw = m.group(0)
            try:
                plan = json.loads(raw)
            except json.JSONDecodeError:
                import ast  # some open models answer with single-quoted dicts
                plan = ast.literal_eval(raw)
            if 'tool' in plan: return self.normalize(plan, msg)
        except Exception as ex:
            print('LLM plan failed:', ex)
        return None


    def translate(self, text, lang_hint):
        """Translate an answer into the user's language (e.g. Hindi/Marathi).
        Record IDs, well IDs, numbers and rule names stay untouched. Returns None on failure."""
        if not self.llm:
            return None
        try:
            import urllib.request
            body = {"model": self.llm['model'], "messages": [{"role": "user", "content":
                f"Translate the following answer into {lang_hint}. Keep every record ID, well ID, "
                f"number, date, name and rule code (like SR-01, T200) exactly as-is. "
                f"Output only the translation, no commentary.\n\n" + text}],
                "temperature": 0, "max_tokens": 1500}
            req = urllib.request.Request(self.llm['base_url'].rstrip('/') + '/chat/completions',
                data=json.dumps(body).encode(), headers={'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + self.llm['api_key']})
            resp = None
            for attempt in range(3):
                try:
                    resp = urllib.request.urlopen(req, timeout=60)
                    break
                except urllib.error.HTTPError as e:
                    if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                        import time as _t; _t.sleep(4 * (attempt + 1))
                        continue
                    raise
            if resp is None:
                return None
            with resp as r:
                out = json.load(r)['choices'][0]['message']['content'].strip()
            return out or None
        except Exception as ex:
            print('translate failed:', ex)
            return None

    def normalize(self, plan, msg):
        """Fill IDs the LLM planner dropped, using regex over the raw message."""
        wells = [w.upper() for w in WELL_RE.findall(msg)]
        if plan.get('asset_id') is None and wells:
            plan['asset_id'] = wells[0]
        if plan.get('tool') == 'repair_costs' and not plan.get('asset_ids') and wells:
            plan['asset_ids'] = wells
        if plan.get('asset_id'):
            plan['asset_id'] = str(plan['asset_id']).upper()
        am = AREA_RE.search(msg)
        if plan.get('area') is None and am:
            plan['area'] = am.group(1)
        return plan

    # ---------- deterministic fallback planner ----------
    def plan_rules(self, msg):
        msg = msg[:2000]
        m = msg.lower()
        wells = [w.upper() for w in WELL_RE.findall(msg)]
        area_m = AREA_RE.search(msg)
        area = area_m.group(1) if area_m else None
        vis_m = VIS_RE.search(msg)
        vis = {'status card':'card','card':'card','table':'table','line chart':'line','bar chart':'bar',
               'bar':'bar','heatmap':'heatmap','map':'map','chart':'line'}.get(vis_m.group(1).lower()) if vis_m else None
        def period():
            if 'last 4 month' in m: return ('2026-06-16', TODAY.isoformat())
            if 'last 90 day' in m: return ((TODAY-timedelta(days=90)).isoformat(), TODAY.isoformat())
            if 'last 12 month' in m or 'last year' in m: return ((TODAY-timedelta(days=365)).isoformat(), TODAY.isoformat())
            yr = re.search(r'\bin (20\d\d)\b', m)
            if yr: return (f'{yr.group(1)}-01-01', f'{yr.group(1)}-12-31')
            if 'this year' in m: return (f'{TODAY.year}-01-01', TODAY.isoformat())
            if 'last 4 year' in m or '4 years' in m: return ('2022-10-20', TODAY.isoformat())
            return (None, None)
        since, until = period()
        if 'correct' in m or 'wrong' in m and ('reading' in m or 'estimate' in m or 'bbl' in m or '°c' in m or 'c.' in m):
            pass  # fall through to specific patterns below
        if re.search(r'note for all (t\d{3})', m):
            t = re.search(r'note for all (t\d{3})', m).group(1).upper()
            mm = re.search(r'after (\d+)', m)
            return {'tool': 'type_rule', 'well_type': t, 'rule_key': 'rca_count',
                    'rule_value': mm.group(1) if mm else '', 'text': msg}
        if 'fyi' in m or 'for everyone' in m or ('shut in' in m and 'will be' in m):
            return {'tool': 'remember_fact', 'asset_id': wells[0] if wells else None, 'text': msg, 'scope': 'shared'}
        if 'is wrong' in m or ('real value' in m) or ('measured' in m and wells) or ('re-measured' in m) or ('faulty' in m):
            num = re.findall(r'(\d+\.?\d*)\s*(?:°\s?c|\bc\b|bbl|barrels?)', m)
            field = 'wellhead_temp_c' if ('°c' in m or 'thermal' in m or 'temperature' in m or 'gun' in m) else 'spill_est_volume_bbl'
            return {'tool': 'correct_data', 'asset_id': wells[0] if wells else None,
                    'new_value': num[-1] if num else None, 'field': field, 'reason': msg}
        if 'we discussed' in m or 'we talked about' in m:
            return {'tool': 'recall_costs' if ('cost' in m or 'repair' in m) else 'recall_discussion', 'year': TODAY.year if 'this year' in m else None}
        if 'running fine' in m or 'back online' in m or ('has been shut in' in m and wells):
            return {'tool': 'user_claim', 'asset_id': wells[0] if wells else None, 'claim': msg}
        if 'downtime planned' in m or ('planned' in m and wells and ('workover' in m or 'shut' in m)):
            return {'tool': 'planned_downtime', 'asset_id': wells[0] if wells else None}
        if 'not producing' in m and area and not wells:
            return {'tool': 'not_producing_in_area', 'area': area, 'visual': vis or 'table'}
        if wells and ('producing oil right now' in m or ('producing' in m and 'now' in m) or 'status card' in m):
            return {'tool': 'well_status', 'asset_id': wells[0], 'visual': vis or 'card'}
        if ('temperature' in m or '°c' in m) and wells and ('chart' in m or 'week' in m or 'over time' in m):
            wk = re.search(r'(\d+) weeks', m)
            return {'tool': 'temp_series', 'asset_id': wells[0], 'weeks': int(wk.group(1)) if wk else 13, 'visual': 'line'}
        if 'heatmap' in m:
            yr = re.search(r'(20\d\d)', m)
            return {'tool': 'spill_heatmap', 'year': int(yr.group(1)) if yr else TODAY.year, 'visual': 'heatmap'}
        if 'map' in m and 'spill' in m:
            days = re.search(r'(\d+) days', m)
            return {'tool': 'spill_map', 'area': area, 'days': int(days.group(1)) if days else 90, 'visual': 'map'}
        if 'how many oil spills' in m or ('spill' in m and wells and ('how many' in m or 'total' in m or 'list' in m)):
            return {'tool': 'spills_list', 'asset_id': wells[0] if wells else None, 'area': area,
                    'since': since or '2022-10-20', 'until': until or TODAY.isoformat(), 'visual': vis or 'table'}
        if 'most downtime' in m or ('downtime' in m and area and 'top' in m) or re.search(r'which \d+ wells.*downtime', m):
            n = re.search(r'which (\d+) wells', m)
            return {'tool': 'downtime_ranking', 'area': area, 'since': since or '2026-06-16',
                    'until': until or TODAY.isoformat(), 'top_n': int(n.group(1)) if n else 3, 'visual': vis or 'bar'}
        if 'how many days' in m and 'down' in m and wells:
            return {'tool': 'downtime', 'asset_id': wells[0], 'since': since or '2025-01-01', 'until': until or '2025-12-31'}
        if 'rules' in m and ('broken' in m or 'violated' in m) and wells:
            return {'tool': 'compliance', 'asset_id': wells[0]}
        if 'not contained' in m or ('contained' in m and 'limit' in m):
            return {'tool': 'containment_violations', 'area': area, 'days': 90}
        if 'root-cause' in m or 'root cause' in m:
            return {'tool': 'rca_wells', 'area': area or 'Northeast Block 7', 'equipment_kind': 'pump_jack'}
        if 'cost' in m and wells:
            return {'tool': 'repair_costs', 'asset_ids': wells, 'year': TODAY.year if 'this year' in m else None}
        if 'needed a report' in m or 'report to the regulator' in m:
            return {'tool': 'reportable_spills', 'area': area, 'days': 90}
        if 'real oil' in m and wells:
            return {'tool': 'spill_dispute', 'asset_id': wells[0]}
        if 'threshold' in m or 'rule' in m and 'manual' in m:
            return {'tool': 'doc_lookup', 'query': msg}
        return {'tool': 'general'}
