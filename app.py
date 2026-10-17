"""Streamlit chat UI for the operations co-pilot.

Renders the agent's generative-UI JSON: the model picks components from
the catalog, this file draws them. No model output is executed as code.
"""
import json

import pandas as pd
import plotly.express as px
import streamlit as st

from agent import core

st.set_page_config(page_title="Ops Co-Pilot", page_icon="🛢️", layout="wide")


def render_component(spec):
    kind = spec.get("component")
    if kind == "table":
        st.caption(spec.get("title", ""))
        df = pd.DataFrame(spec["rows"], columns=spec["columns"])
        st.dataframe(df, use_container_width=True, hide_index=True)
    elif kind == "bar_chart":
        st.caption(spec.get("title", ""))
        fig = px.bar(x=spec["x"], y=spec["y"],
                     labels={"x": spec.get("x_label", ""), "y": spec.get("y_label", "")})
        st.plotly_chart(fig, use_container_width=True)
    elif kind == "line_chart":
        st.caption(spec.get("title", ""))
        df = pd.DataFrame({"x": spec["x"]})
        for s in spec["series"]:
            df[s["name"]] = s["y"]
        fig = px.line(df, x="x", y=[s["name"] for s in spec["series"]])
        st.plotly_chart(fig, use_container_width=True)
    elif kind == "heatmap":
        st.caption(spec.get("title", ""))
        fig = px.imshow(spec["z"], x=spec["x_labels"], y=spec["y_labels"],
                        color_continuous_scale="RdBu_r", zmin=-1, zmax=1,
                        text_auto=True, aspect="auto")
        st.plotly_chart(fig, use_container_width=True)
    elif kind == "metric":
        st.metric(spec.get("label", ""), spec.get("value", ""))
    else:
        st.code(json.dumps(spec, indent=2), language="json")


st.title("Operations Co-Pilot")
st.caption("Ask about wells, sensors and maintenance. "
           "Set LLM_API_KEY for the full agent; without it you're in offline demo mode.")

if "chat" not in st.session_state:
    st.session_state.chat = []  # list of {q, answer}

for turn in st.session_state.chat:
    with st.chat_message("user"):
        st.write(turn["q"])
    with st.chat_message("assistant"):
        st.write(turn["answer"]["message"])
        for spec in turn["answer"]["components"]:
            render_component(spec)

question = st.chat_input("e.g. Which wells are non-producing by block, and why?")
if question:
    history = []
    for turn in st.session_state.chat[-6:]:
        history.append({"role": "user", "content": turn["q"]})
        history.append({"role": "assistant", "content": turn["answer"]["message"]})
    with st.spinner("Thinking..."):
        result = core.answer(question, history=history)
    st.session_state.chat.append({"q": question, "answer": result})
    st.rerun()
