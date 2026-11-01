"""Ask the grounded guide a few questions against the real index and Gemini (needs a built index and a key).

    python scripts/raksha_ask_live.py "how long do I have to appeal to the GAC?" "will the police arrest him?"
Prints each answer sentence with its source, or the refusal. A live check, not a measured accuracy.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from satyasutra.config import load_config  # noqa: E402
from satyasutra.envfile import load_env_file  # noqa: E402
from satyasutra.llm import GeminiClient  # noqa: E402
from satyasutra.raksha.ask import answer_question, public  # noqa: E402
from satyasutra.raksha.corpus import CorpusIndex, load_rag  # noqa: E402


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_env_file(ROOT / ".env")
    rag, client = load_rag(), GeminiClient(load_config())
    index = CorpusIndex(rag)
    for q in argv:
        lang = "hi" if any("ऀ" <= c <= "ॿ" for c in q) else "en"
        out = public(answer_question(client, index, rag, q, lang), rag, lang, "DANGER: call 112")
        print(f"\nQ: {q}\n   retrieved {out.get('retrieved')}, dropped {out.get('dropped')}")
        if out.get("danger"):
            print("   ", out["message"])
        for s in out["sentences"]:
            print(f"   - {s['text']}\n     [{s['source']['id']}] \"{s['quote']}\"")
        if out.get("refused"):
            print("   REFUSED:", out["refusal"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
