"""
Quick test script for the /chat endpoint. Run with the engine already running on 8001:
  python scripts/test_chat.py
"""

import os
import sys
import requests

ENGINE_URL = os.getenv("ENGINE_URL", "http://127.0.0.1:8001")


def chat(message: str) -> str:
    r = requests.post(f"{ENGINE_URL}/chat", json={"message": message}, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data.get("reply") or data.get("message") or ""


def main():
    tests = [
        "What is my current strategy?",
        "Summarize my portfolio.",
        "What are the latest market trends?",
        "How do earnings reports affect stock prices?",
        "Why might political news move the market?",
    ]
    print("Testing /chat endpoint (engine must be running on 8001)\n")
    for q in tests:
        print(f"Q: {q}")
        try:
            reply = chat(q)
            print(f"A: {reply[:500]}{'...' if len(reply) > 500 else ''}\n")
        except requests.exceptions.ConnectionError:
            print(
                "A: [ERROR] Engine not reachable. Start it first (e.g. run_engine.bat or python -m core.engine)\n"
            )
            sys.exit(1)
        except Exception as e:
            print(f"A: [ERROR] {e}\n")
    print("Done.")


if __name__ == "__main__":
    main()
