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

# ---- sidebar: user picker (memory is per user) ----
users = router.users()
names = {u['name']: u for u in users}
st.sidebar.title("Asset Intelligence Copilot")
st.sidebar.caption("FlytBase Hack Day · 200 wells · sample dataset (synthetic)")
who = st.sidebar.selectbox("You are", list(names.keys()),
                           index=list(names.keys()).index('Michael Turner') if 'Michael Turner' in names else 0)
user = names[who]
st.sidebar.caption(f"{user['role'].replace('_',' ')} · {user['team_id']}")
st.sidebar.markdown("---")
st.sidebar.markdown("**Try:**\n- Is WELL-0001 producing oil right now? Show its status card.\n- Which wells in Northeast Block 8 are not producing oil? Table.\n- Heatmap of 2026 spills by area and month.\n- Which rules has WELL-0001 broken?\n- The 15 Oct reading for WELL-0164 is wrong — the crew re-measured 41.0 C.")

if "messages" not in st.session_state or st.session_state.get("who") != who:
    st.session_state.messages = []
    st.session_state.who = who

st.title("🛢️ Asset Intelligence Copilot")
st.caption("Ask about wells, spills, downtime, repairs, compliance. Answers cite their records; corrections and notes are remembered.")

def render_visual(resp):
    v, d = resp.get('visual'), resp.get('data') or {}
    if v == 'card' and 'status' in d:
        s, w = d['status'], d['well']
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Producing oil", "YES" if s['producing'] else "NO")
        c2.metric("Wellhead vs air", f"+{s['delta_c']}°C", f"limit +{s['threshold']:.0f}°C ({w['well_type']})")
        c3.metric("Recorded status", s['recorded_status'], "out of date" if s['recorded_status']=='active' and not s['producing'] else None)
        c4.metric("Last inspection", s['inspection']['inspected_at'][:10], s['inspector'])
    elif v == 'table' and d.get('table'):
        st.dataframe(pd.DataFrame(d['table']), use_container_width=True, hide_index=True)
    elif v == 'line' and d.get('series'):
        df = pd.DataFrame(d['series']).set_index('date')[['wellhead_temp_c','ambient_temp_c']]
        st.line_chart(df)
        down = [p['date'] for p in d['series'] if not p['producing']]
        if down: st.caption("Not producing oil on: " + ", ".join(down))
    elif v == 'bar' and d.get('table'):
        df = pd.DataFrame(d['table'])
        col = [c for c in df.columns if 'day' in c.lower() or 'cost' in c.lower() or 'repair' in c.lower()]
        if col: st.bar_chart(df.set_index(df.columns[0])[col[0]])
        st.dataframe(df, use_container_width=True, hide_index=True)
    elif v == 'map' and d.get('pins'):
        st.map(pd.DataFrame(d['pins']), latitude='lat', longitude='lon', size=120)
        st.dataframe(pd.DataFrame(d['pins'])[['well','date','bbl']], use_container_width=True, hide_index=True)
    elif v == 'heatmap' and d.get('grid'):
        areas = sorted(d['grid'].keys())
        months = list(range(1, 13))
        z = [[d['grid'][a].get(m, 0) for m in months] for a in areas]
        try:
            import plotly.graph_objects as go
            fig = go.Figure(go.Heatmap(z=z, x=[pd.Timestamp(2026,m,1).strftime('%b') for m in months],
                                       y=areas, colorscale='YlOrRd', text=z, texttemplate='%{text}'))
            fig.update_layout(height=280, margin=dict(l=0,r=0,t=10,b=0))
            st.plotly_chart(fig, use_container_width=True)
        except Exception:
            df = pd.DataFrame(z, index=areas, columns=[pd.Timestamp(2026,m,1).strftime('%b') for m in months])
            st.dataframe(df.style.background_gradient(cmap='YlOrRd'), use_container_width=True)

for msg in st.session_state.messages:
    with st.chat_message(msg['role']):
        st.markdown(msg['text'])
        if msg.get('resp'): render_visual(msg['resp'])
        if msg.get('evidence'):
            with st.expander("Evidence"): st.caption(" · ".join(str(e) for e in msg['evidence'][:12]))

if q := st.chat_input("Ask about your wells…"):
    st.session_state.messages.append({'role': 'user', 'text': q})
    with st.chat_message("user"): st.markdown(q)
    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            hist = router.user_history(user['user_id'])
            plan = (router.plan_llm(q, who, hist) if router.llm else None) or router.plan_rules(q)
            # post-correction hint for follow-up area queries
            resp = executor.run(plan, user['user_id'], who, q)
            router.log_chat(user['user_id'], 'user', q)
            router.log_chat(user['user_id'], 'assistant', resp['text'] + ' ' +
                            ' '.join(r.get('Well','') for r in (resp.get('data') or {}).get('table', []) if isinstance(r, dict)))
        st.markdown(resp['text'])
        render_visual(resp)
        if resp.get('evidence'):
            with st.expander("Evidence"): st.caption(" · ".join(str(e) for e in resp['evidence'][:12]))
    st.session_state.messages.append({'role': 'assistant', 'text': resp['text'], 'resp': resp, 'evidence': resp.get('evidence')})
