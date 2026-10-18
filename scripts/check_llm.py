"""Check the configured AI provider works: key present, model reachable, one tiny call.

    python scripts/check_llm.py            # list models + 1 tiny request (costs ~nothing)

Never prints the key.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from airline_agent import config  # noqa: E402


def main() -> None:
    provider = config.agent_provider()
    model = config.agent_model(provider)
    print(f"provider: {provider}\nmodel:    {model}")
    if provider == "gemini":
        key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not key:
            sys.exit("GEMINI_API_KEY is empty in .env")
        print(f"key:      set ({len(key)} chars)")
        from google import genai
        from google.genai import errors

        client = genai.Client()
        try:
            names = sorted(m.name.removeprefix("models/") for m in client.models.list()
                           if "generateContent" in (m.supported_actions or []))
        except errors.APIError as e:
            sys.exit(f"API rejected the key / request: HTTP {e.code} {e.status}")
        flash = [n for n in names if "flash" in n and "image" not in n and "tts" not in n and "live" not in n]
        print(f"models available ({len(names)}), text 'flash' ones: {', '.join(flash[:15])}")
        if model not in names:
            print(f"!! configured model '{model}' is not in your list; set GEMINI_MODEL in .env to one above")
            sys.exit(1)
        r = client.models.generate_content(model=model, contents="Reply with exactly: OK")
        print(f"test call: {r.text.strip()!r}")
    else:
        import anthropic

        r = anthropic.Anthropic().messages.create(model=model, max_tokens=50,
                                                  messages=[{"role": "user", "content": "Reply with exactly: OK"}])
        print("test call:", "".join(b.text for b in r.content if b.type == "text"))
    print("LLM check passed.")


if __name__ == "__main__":
    main()
