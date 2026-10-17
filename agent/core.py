"""Agent loop: question -> tool calls -> final generative-UI JSON."""
import json

from . import llm, tools
from .prompts import SYSTEM_PROMPT

MAX_ROUNDS = 6


def _parse_answer(text):
    """Pull the {message, components} object out of the model reply."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return {"message": text, "components": []}
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {"message": text.strip(), "components": []}
    obj.setdefault("message", "")
    obj.setdefault("components", [])
    return obj


def answer(question, history=None):
    """Answer one question. history = prior [{'role','content'}] turns."""
    if not llm.configured():
        from . import demo_mode
        return demo_mode.answer(question)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": question})

    for _ in range(MAX_ROUNDS):
        msg = llm.chat(messages, tools=tools.TOOL_SCHEMAS)
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            return _parse_answer(msg.get("content") or "")
        messages.append(msg)
        for call in tool_calls:
            fn = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            func = tools.TOOL_FUNCS.get(fn)
            result = func(**args) if func else {"error": f"unknown tool {fn}"}
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": json.dumps(result),
            })
    return {"message": "Hit the tool-call limit before I could answer - try a narrower question.",
            "components": []}
