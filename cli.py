"""Terminal chat - quick smoke test without streamlit."""
import json
import sys

from agent import core


def main():
    print("Ops co-pilot CLI. Type a question, or 'quit'.")
    history = []
    while True:
        try:
            q = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q.lower() in ("quit", "exit"):
            break
        ans = core.answer(q, history=history)
        print("agent>", ans["message"])
        for spec in ans["components"]:
            print("  [component]", json.dumps(spec)[:400])
        history.append({"role": "user", "content": q})
        history.append({"role": "assistant", "content": ans["message"]})


if __name__ == "__main__":
    sys.exit(main())
