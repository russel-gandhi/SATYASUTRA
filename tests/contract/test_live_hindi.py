"""A Hindi claim through the real agent: Hindi in, English claim and the Hindi original out. Run with `pytest -m contract`.

What the verdict is depends on what fact-checkers have published today, so the test asserts invariants only.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from satyasutra import __version__
from satyasutra.api.app import create_app
from satyasutra.config import load_config
from satyasutra.envfile import load_env_file
from satyasutra.ledger import SQLiteLedgerStore

pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parents[2]
HINDI = "कोविड-19 वैक्सीन में माइक्रोचिप होती है जो लोगों को ट्रैक करती है"
DEVANAGARI = range(0x0900, 0x0980)


def test_a_hindi_claim_keeps_its_original_words_gets_an_english_claim_and_a_ledger_row(tmp_path):
    load_env_file(ROOT / ".env")
    ledger = SQLiteLedgerStore(tmp_path / "l.sqlite3", code_version=__version__)
    client = TestClient(create_app(load_config(), ledger))
    kind, body, tools = None, None, []
    with client.stream("GET", "/agent/stream", params={"text": HINDI}) as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if line.startswith("event: "):
                kind = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
                if kind == "tool_call":
                    tools.append(data["data"]["name"])
                elif kind == "result":
                    body = data
                elif kind == "error":
                    pytest.fail(f"the run failed at {data['node']}: {data['data']}")
    assert body is not None and body["claims"], "no claim was extracted from a plain Hindi factual claim"
    claim = body["claims"][0]
    assert claim["lang"] == "hi" and claim["quote"] in HINDI
    assert not any(ord(ch) in DEVANAGARI for ch in claim["text"]), "the claim text is not English"
    assert claim["label"] in {"REFUTED", "SUPPORTED", "MISSING_CONTEXT", "UNVERIFIABLE_YET"} and "TRANSLATED" in claim["flags"]
    assert ledger.get(claim["ledger_seq"]) is not None and ledger.verify().ok
    assert "fact-check registry" in tools and "rules + ledger" in tools
    print(f"Hindi claim -> {claim['label']} ({claim['confidence_band']}); English: {claim['text']}")
