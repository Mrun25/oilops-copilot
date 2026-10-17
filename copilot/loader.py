"""Load the FlytBase asset-intelligence dataset folder into SQLite."""
import json, os, sqlite3, glob, sys

SCHEMA = """
CREATE TABLE IF NOT EXISTS wells(asset_id TEXT PRIMARY KEY, name TEXT, well_type TEXT,
  area_id TEXT, team_id TEXT, in_charge_user_id TEXT, lat REAL, lon REAL,
  installed_on TEXT, recorded_status TEXT);
CREATE TABLE IF NOT EXISTS inspections(inspection_id TEXT PRIMARY KEY, asset_id TEXT,
  inspected_at TEXT, inspector_user_id TEXT, method TEXT, ambient_temp_c REAL,
  wellhead_temp_c REAL, spill_detected INT, spill_area_pct REAL,
  spill_est_volume_bbl REAL, notes TEXT);
CREATE TABLE IF NOT EXISTS maintenance(maintenance_id TEXT PRIMARY KEY, asset_id TEXT,
  started_at TEXT, completed_at TEXT, maintenance_type TEXT, equipment_kind TEXT,
  issue TEXT, action_taken TEXT, technician_user_id TEXT, downtime_hours REAL,
  cost_total REAL, triggered_by_inspection_id TEXT, report_file TEXT);
CREATE TABLE IF NOT EXISTS reg_events(asset_id TEXT, date TEXT, agency TEXT,
  event_type TEXT, protocol_id TEXT, related_inspection_id TEXT, hours_to_report REAL,
  detail TEXT, fine_usd REAL, status TEXT);
CREATE TABLE IF NOT EXISTS notes(note_id TEXT, asset_id TEXT, created_at TEXT,
  author_user_id TEXT, note_type TEXT, inspection_id TEXT, text TEXT,
  valid_from TEXT, valid_to TEXT);
CREATE TABLE IF NOT EXISTS reports(asset_id TEXT, maintenance_id TEXT, path TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS docs(doc_id TEXT, title TEXT, path TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS users(user_id TEXT PRIMARY KEY, name TEXT, role TEXT, team_id TEXT);
CREATE TABLE IF NOT EXISTS areas(area_id TEXT PRIMARY KEY, name TEXT, team_id TEXT,
  center_lat REAL, center_lon REAL);
-- runtime memory tables
CREATE TABLE IF NOT EXISTS corrections(id INTEGER PRIMARY KEY AUTOINCREMENT,
  scope_kind TEXT, scope_id TEXT, field TEXT, original_value TEXT, corrected_value TEXT,
  author_user_id TEXT, reason TEXT, created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS shared_notes(id INTEGER PRIMARY KEY AUTOINCREMENT,
  asset_id TEXT, author_user_id TEXT, text TEXT, status TEXT DEFAULT 'unconfirmed',
  valid_from TEXT, valid_to TEXT, created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS chat_log(id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT, session_date TEXT, role TEXT, text TEXT,
  created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS feedback_rules(id INTEGER PRIMARY KEY AUTOINCREMENT,
  scope_kind TEXT, scope_id TEXT, rule_key TEXT, rule_value TEXT, text TEXT,
  author_user_id TEXT, created_at TEXT DEFAULT (datetime('now')));
"""

def load(data_dir, db_path):
    if os.path.exists(db_path): os.remove(db_path)
    db = sqlite3.connect(db_path); db.executescript(SCHEMA)
    fleet = os.path.join(data_dir, 'fleet')
    aj = json.load(open(os.path.join(fleet, 'areas.json')))
    for a in aj['areas']:
        db.execute("INSERT INTO areas VALUES(?,?,?,?,?)", (a['area_id'], a['name'], a['team_id'], a['center']['lat'], a['center']['lon']))
    for u in json.load(open(os.path.join(fleet, 'users.json')))['users']:
        db.execute("INSERT INTO users VALUES(?,?,?,?)", (u['user_id'], u['name'], u['role'], u['team_id']))
    # docs: manuals + sops
    for p in sorted(glob.glob(os.path.join(fleet, 'well-types', '*.md')) + glob.glob(os.path.join(fleet, 'sops', '*.md'))):
        db.execute("INSERT INTO docs VALUES(?,?,?,?)", (os.path.basename(p), os.path.basename(p), p, open(p).read()))
    n_insp = n_mnt = 0
    for wdir in sorted(glob.glob(os.path.join(data_dir, 'assets', 'WELL-*'))):
        md = json.load(open(os.path.join(wdir, 'metadata.json')))
        db.execute("INSERT INTO wells VALUES(?,?,?,?,?,?,?,?,?,?)",
            (md['asset_id'], md['name'], md['well_type'], md['area_id'], md['team_id'],
             md['in_charge_user_id'], md['location']['lat'], md['location']['lon'],
             md['installed_on'], md.get('recorded_status')))
        for fn, sql, conv in [
            ('inspections.jsonl', "INSERT INTO inspections VALUES(?,?,?,?,?,?,?,?,?,?,?)",
             lambda r: (r['inspection_id'], r['asset_id'], r['inspected_at'], r['inspector_user_id'], r['method'],
                        r['ambient_temp_c'], r['wellhead_temp_c'], 1 if r['spill_detected'] else 0,
                        r.get('spill_area_pct'), r.get('spill_est_volume_bbl'), r.get('notes'))),
            ('maintenance.jsonl', "INSERT INTO maintenance VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
             lambda r: (r['maintenance_id'], r['asset_id'], r['started_at'], r['completed_at'], r['maintenance_type'],
                        r['equipment_kind'], r['issue'], r['action_taken'], r['technician_user_id'],
                        r.get('downtime_hours'), (r.get('cost_usd') or {}).get('total'),
                        r.get('triggered_by_inspection_id'), r.get('report_file'))),
            ('regulatory-events.jsonl', "INSERT INTO reg_events VALUES(?,?,?,?,?,?,?,?,?,?)",
             lambda r: (r['asset_id'], r['date'], r['agency'], r['event_type'], r.get('protocol_id'),
                        r.get('related_inspection_id'), r.get('hours_to_report'), r.get('detail'),
                        r.get('fine_usd'), r.get('status'))),
            ('context.jsonl', "INSERT INTO notes VALUES(?,?,?,?,?,?,?,?,?)",
             lambda r: (r.get('note_id'), r['asset_id'], r['created_at'], r['author_user_id'],
                        r['note_type'], r.get('inspection_id'), r['text'], r.get('valid_from'), r.get('valid_to'))),
        ]:
            p = os.path.join(wdir, fn)
            if not os.path.exists(p): continue
            for line in open(p):
                line = line.strip()
                if not line: continue
                db.execute(sql, conv(json.loads(line)))
                if fn == 'inspections.jsonl': n_insp += 1
                if fn == 'maintenance.jsonl': n_mnt += 1
        for rp in glob.glob(os.path.join(wdir, 'maintenance-reports', '*.md')):
            db.execute("INSERT INTO reports VALUES(?,?,?,?)",
                       (md['asset_id'], os.path.basename(rp).replace('.md',''), rp, open(rp).read()))
    for t, c in [('inspections','asset_id'), ('maintenance','asset_id'), ('reg_events','asset_id'), ('notes','asset_id'), ('reports','asset_id')]:
        db.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_a ON {t}({c})")
    db.commit()
    print(f"loaded: {n_insp} inspections, {n_mnt} maintenance rows -> {db_path}")
    return db

if __name__ == '__main__':
    load(sys.argv[1], sys.argv[2])
