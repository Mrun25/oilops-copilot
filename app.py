import os, sys, json, sqlite3
import streamlit as st
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from copilot.loader import load
from copilot.router import Router
from copilot.executor import Executor

st.set_page_config(page_title="Asset Intelligence Copilot", page_icon="🛢️", layout="wide")

DATA_DIR = os.environ.get("COPILOT_DATA_DIR", "sample-data")
DB = os.environ.get("COPILOT_DB", "/tmp/copilot.db")

@st.cache_resource
def get_router():
    if not os.path.exists(DB):
        load(DATA_DIR, DB)
    llm = None
    if os.environ.get("LLM_API_KEY"):
        llm = {"base_url": os.environ.get("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"),
               "model": os.environ.get("LLM_MODEL", "gemini-3.8-flash"),
               "api_key": os.environ["LLM_API_KEY"]}
    return Router(DB, llm)

router = get_router()
executor = Executor(router)

# ---------------------------------------------------------------- style
ACCENT, GOOD, BAD, MUTED = "#f5a623", "#22c55e", "#ef4444", "#9aa4b2"
st.markdown(f"""
<style>
  .block-container {{ padding-top: 1.6rem; max-width: 1180px; }}
  .hero {{ padding: 4px 0 10px 0; border-bottom: 1px solid #262b36; margin-bottom: 14px; }}
  .hero h1 {{ font-size: 2.05rem; margin: 0; letter-spacing: .2px; }}
  .hero h1 .flame {{ color: {ACCENT}; }}
  .hero .sub {{ color: {MUTED}; margin-top: 2px; font-size: .98rem; }}
  .chips {{ margin-top: 8px; }}
  .chip {{ display:inline-block; background:#171b23; border:1px solid #2a3140; color:#c8d0dc;
           border-radius:999px; padding:3px 12px; font-size:.8rem; margin-right:8px; }}
  .chip b {{ color:#fff; }}
  .pill {{ display:inline-block; border-radius:999px; padding:4px 16px; font-weight:700;
           font-size:1.02rem; letter-spacing:.4px; }}
  .pill-yes {{ background:rgba(34,197,94,.16); color:{GOOD}; border:1px solid {GOOD}; }}
  .pill-no  {{ background:rgba(239,68,68,.14); color:{BAD};  border:1px solid {BAD}; }}
  .cardhead {{ color:{MUTED}; font-size:.82rem; text-transform:uppercase; letter-spacing:.8px; margin-bottom:2px; }}
  .who-card {{ background:#171b23; border:1px solid #2a3140; border-radius:12px; padding:12px 14px; margin-bottom:10px; }}
  .who-card .nm {{ font-weight:700; font-size:1.02rem; }}
  .who-card .rl {{ color:{MUTED}; font-size:.85rem; }}
  .avatar {{ display:inline-block; width:38px; height:38px; border-radius:10px; background:{ACCENT}; color:#131720;
             font-weight:800; text-align:center; line-height:38px; margin-right:10px; float:left; }}
  .cap {{ background:#171b23; border:1px solid #2a3140; border-radius:12px; padding:14px 16px; height:100%; }}
  .cap .t {{ font-weight:700; margin-bottom:4px; }}
  .cap .d {{ color:{MUTED}; font-size:.86rem; }}
  [data-testid="stSidebar"] .stButton button {{ text-align:left; }}
  .stChatMessage {{ border-radius:12px; }}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------- data
users = router.users()
names = {u['name']: u for u in users}

def kpi(sql, args=()):
    try:
        return router.eng.q(sql, args)[0][0]
    except Exception:
        return None

n_wells = kpi("SELECT COUNT(*) FROM wells")
n_insp  = kpi("SELECT COUNT(*) FROM inspections")
n_notes = kpi("SELECT COUNT(*) FROM notes")
last_insp = None
try:
    last_insp = router.eng.q("SELECT MAX(inspected_at) FROM inspections")[0][0][:10]
except Exception:
    pass

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.markdown("## 🛢️ Asset Intelligence Copilot")
    st.caption("FlytBase Hack Day · Asset Health Challenge")
    st.markdown("---")
    who = st.selectbox("You are", list(names.keys()),
                       index=list(names.keys()).index('Michael Turner') if 'Michael Turner' in names else 0)
    user = names[who]
    initials = "".join(p[0] for p in who.split()[:2])
    st.markdown(f"""<div class="who-card">
        <span class="avatar">{initials}</span>
        <div class="nm">{who}</div>
        <div class="rl">{user['role'].replace('_',' ')} · {user['team_id'].replace('_',' ')}</div>
        <div style="clear:both"></div></div>""", unsafe_allow_html=True)
    st.caption("Conversations and corrections are remembered — private history per person, shared facts for the team.")
    st.markdown("**Try an example**")
    EXAMPLES = [
        "Is WELL-0001 producing oil right now? Show its status card.",
        "Which wells in Northeast Block 8 are not producing oil right now, and why? Show them in a table.",
        "Show a heatmap of 2026 spills by geographic area and month.",
        "Which rules has WELL-0001 broken in the last 12 months, and what is still open?",
        "How many days was WELL-0001 down in 2025?",
        "The 15 Oct reading for WELL-0164 is wrong. The handheld thermal gun was faulty; the crew re-measured the wellhead at 41.0 °C.",
    ]
    for i, ex_q in enumerate(EXAMPLES):
        label = ex_q if len(ex_q) <= 58 else ex_q[:56] + "…"
        if st.button(label, key=f"ex{i}", use_container_width=True):
            st.session_state.pending_q = ex_q

if "messages" not in st.session_state or st.session_state.get("who") != who:
    st.session_state.messages = []
    st.session_state.who = who

# ---------------------------------------------------------------- header
ds_label = os.path.basename(DATA_DIR.rstrip('/'))
planner = "Gemini planner" if router.llm else "deterministic router"
st.markdown(f"""<div class="hero">
  <h1><span class="flame">🛢️</span> Asset Intelligence Copilot</h1>
  <div class="sub">Ask the oilfield anything — production status, spills, downtime, repairs, compliance.
  Every answer is computed from the records and cites its evidence.</div>
  <div class="chips">
    <span class="chip"><b>{n_wells or '—'}</b> wells</span>
    <span class="chip"><b>{(f'{n_insp:,}' if n_insp else '—')}</b> inspections</span>
    <span class="chip"><b>{(f'{n_notes:,}' if n_notes else '—')}</b> notes & corrections</span>
    <span class="chip">latest inspection <b>{last_insp or '—'}</b></span>
    <span class="chip">dataset <b>{ds_label}</b> (synthetic)</span>
    <span class="chip">planner <b>{planner}</b></span>
  </div>
</div>""", unsafe_allow_html=True)

# ---------------------------------------------------------------- visuals
def render_visual(resp):
    v, d = resp.get('visual'), resp.get('data') or {}
    try:
        if v == 'card' and 'status' in d:
            s, w = d['status'], d['well']
            producing = s['producing']
            pill_cls = "pill-yes" if producing else "pill-no"
            pill_txt = "PRODUCING OIL" if producing else "NOT PRODUCING OIL"
            pill = f'<span class="pill {pill_cls}">{pill_txt}</span>'
            with st.container(border=True):
                st.markdown(f'<div class="cardhead">{w["asset_id"]} · {w.get("name","")} · {w["well_type"]}</div>{pill}', unsafe_allow_html=True)
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Wellhead vs air", f"+{s['delta_c']}°C", f"limit +{s['threshold']:.0f}°C ({w['well_type']})")
                c2.metric("Recorded status", s['recorded_status'],
                          "out of date" if s['recorded_status'] == 'active' and not producing else "consistent",
                          delta_color="inverse" if s['recorded_status'] == 'active' and not producing else "normal")
                c3.metric("Last inspection", s['inspection']['inspected_at'][:10], s['inspector'])
                c4.metric("Inspection", s['inspection']['inspection_id'], s['inspection'].get('method', ''))
        elif v == 'table' and d.get('table'):
            st.dataframe(pd.DataFrame(d['table']), use_container_width=True, hide_index=True)
        elif v == 'line' and d.get('series'):
            import plotly.graph_objects as go
            df = pd.DataFrame(d['series'])
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=df['date'], y=df['wellhead_temp_c'], name='Wellhead °C',
                                     line=dict(color=ACCENT, width=2.5), mode='lines+markers'))
            fig.add_trace(go.Scatter(x=df['date'], y=df['ambient_temp_c'], name='Air °C',
                                     line=dict(color='#5b8def', width=2, dash='dot'), mode='lines'))
            down = df[~df['producing'].astype(bool)]
            if len(down):
                fig.add_trace(go.Scatter(x=down['date'], y=down['wellhead_temp_c'], name='Not producing',
                                         mode='markers', marker=dict(color=BAD, size=13, symbol='x-thin', line=dict(width=2, color=BAD))))
            fig.update_layout(height=320, margin=dict(l=0, r=0, t=30, b=0), paper_bgcolor='rgba(0,0,0,0)',
                              plot_bgcolor='rgba(0,0,0,0)', legend=dict(orientation='h', y=1.12),
                              yaxis=dict(gridcolor='#262b36'), xaxis=dict(gridcolor='#262b36'))
            st.plotly_chart(fig, use_container_width=True)
        elif v == 'bar' and d.get('table'):
            import plotly.graph_objects as go
            df = pd.DataFrame(d['table'])
            col = [c for c in df.columns if 'day' in c.lower() or 'cost' in c.lower() or 'repair' in c.lower()]
            if col:
                fig = go.Figure(go.Bar(x=df[df.columns[0]], y=df[col[0]], text=df[col[0]], textposition='outside',
                                       marker_color=[ACCENT, '#e25822', '#c94f1e'][:len(df)]))
                fig.update_layout(height=300, margin=dict(l=0, r=0, t=20, b=0), paper_bgcolor='rgba(0,0,0,0)',
                                  plot_bgcolor='rgba(0,0,0,0)', yaxis=dict(gridcolor='#262b36', title=col[0]))
                st.plotly_chart(fig, use_container_width=True)
            st.dataframe(df, use_container_width=True, hide_index=True)
        elif v == 'map' and d.get('pins'):
            df = pd.DataFrame(d['pins'])
            try:
                import plotly.graph_objects as go
                fig = go.Figure(go.Scattermapbox(
                    lat=df['lat'], lon=df['lon'], mode='markers',
                    marker=dict(size=15, color=BAD, opacity=.9),
                    text=[f"{p['well']} · {p['date']} · {p['bbl']} bbl" for p in d['pins']], hoverinfo='text'))
                fig.update_layout(mapbox=dict(style='carto-darkmatter',
                                              center=dict(lat=float(df['lat'].mean()), lon=float(df['lon'].mean())), zoom=10.5),
                                  height=380, margin=dict(l=0, r=0, t=0, b=0))
                st.plotly_chart(fig, use_container_width=True)
            except Exception:
                st.map(df, latitude='lat', longitude='lon', size=120)
            st.dataframe(df[['well', 'date', 'bbl']], use_container_width=True, hide_index=True)
        elif v == 'heatmap' and d.get('grid'):
            areas = sorted(d['grid'].keys())
            months = list(range(1, 13))
            z = [[d['grid'][a].get(m, 0) for m in months] for a in areas]
            try:
                import plotly.graph_objects as go
                fig = go.Figure(go.Heatmap(z=z, x=[pd.Timestamp(2026, m, 1).strftime('%b') for m in months],
                                           y=areas, colorscale='YlOrRd', text=z, texttemplate='%{text}',
                                           hovertemplate='%{y} · %{x}: %{z} spills<extra></extra>'))
                fig.update_layout(height=300, margin=dict(l=0, r=0, t=10, b=0), paper_bgcolor='rgba(0,0,0,0)',
                                  plot_bgcolor='rgba(0,0,0,0)')
                st.plotly_chart(fig, use_container_width=True)
            except Exception:
                df = pd.DataFrame(z, index=areas, columns=[pd.Timestamp(2026, m, 1).strftime('%b') for m in months])
                st.dataframe(df.style.background_gradient(cmap='YlOrRd'), use_container_width=True)
    except Exception:
        pass

def render_message(msg):
    with st.chat_message(msg['role']):
        st.markdown(msg['text'])
        if msg.get('resp'):
            render_visual(msg['resp'])
        if msg.get('evidence'):
            with st.expander(f"Evidence · {len(msg['evidence'])} records"):
                for e in msg['evidence'][:12]:
                    st.caption("• " + str(e))

# ---------------------------------------------------------------- empty state
if not st.session_state.messages:
    c1, c2, c3, c4 = st.columns(4)
    caps = [
        ("📊 Live status cards", "Is a well producing right now? Rules from the T200/T350 manuals decide — not a guess."),
        ("🗺️ Spills on a map", "Real spills only: corrected detections (rainwater, produced water) are removed automatically."),
        ("🧯 Compliance radar", "SR-01/02/03 reporting, containment and verification deadlines checked against every record."),
        ("🧠 Memory that respects people", "Corrections reach the whole team; private chats stay private; conflicts are shown, not hidden."),
    ]
    for col, (t, dsc) in zip((c1, c2, c3, c4), caps):
        col.markdown(f'<div class="cap"><div class="t">{t}</div><div class="d">{dsc}</div></div>', unsafe_allow_html=True)
    st.markdown("")

for msg in st.session_state.messages:
    render_message(msg)

def handle(q):
    st.session_state.messages.append({'role': 'user', 'text': q})
    with st.chat_message("user"): st.markdown(q)
    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            hist = router.user_history(user['user_id'])
            plan = (router.plan_llm(q, who, hist) if router.llm else None) or router.plan_rules(q)
            resp = executor.run(plan, user['user_id'], who, q)
            router.log_chat(user['user_id'], 'user', q)
            router.log_chat(user['user_id'], 'assistant', resp['text'] + ' ' +
                            ' '.join(r.get('Well','') for r in (resp.get('data') or {}).get('table', []) if isinstance(r, dict)))
        st.markdown(resp['text'])
        render_visual(resp)
        if resp.get('evidence'):
            with st.expander(f"Evidence · {len(resp['evidence'])} records"):
                for e in resp['evidence'][:12]:
                    st.caption("• " + str(e))
    st.session_state.messages.append({'role': 'assistant', 'text': resp['text'], 'resp': resp, 'evidence': resp.get('evidence')})

pending = st.session_state.get('pending_q')
if 'pending_q' in st.session_state:
    del st.session_state['pending_q']
typed = st.chat_input("Ask about your wells…")
if pending:
    handle(pending)
elif typed:
    handle(typed)
