"""Thin client for any OpenAI-compatible chat endpoint.

Point it at whichever free tier we're using via env vars - defaults are
Groq's free tier with Llama 3.3 70B. Alternatives (see README):
  OpenRouter free models (Gemma etc.):  LLM_BASE_URL=https://openrouter.ai/api/v1
  Google AI Studio (Gemini free tier):  LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
"""
import json
import os
import urllib.request
import urllib.error

BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
MODEL = os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile")


def configured():
    return bool(os.environ.get("LLM_API_KEY"))


def chat(messages, tools=None, temperature=0.2):
    """One chat completion call. Returns the raw message dict."""
    key = os.environ["LLM_API_KEY"]
    body = {"model": MODEL, "messages": messages, "temperature": temperature}
    if tools:
        body["tools"] = tools
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"LLM API error {e.code}: {e.read().decode()[:300]}")
    return data["choices"][0]["message"]
