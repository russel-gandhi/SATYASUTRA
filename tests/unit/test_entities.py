"""Entity grounding: Gemini names the entities, Wikidata resolves them, and the model cannot add names the claim lacks."""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx

from satyasutra.cache import TTLCache
from satyasutra.claims import ground_entities
from satyasutra.config import load_config
from satyasutra.errors import Unreachable
from satyasutra.sources import Entity, WikidataClient

CONFIG = Path(__file__).resolve().parents[2] / "config"
CLAIM = "The Reserve Bank of India banned UPI payments above 1 lakh rupees"


class FakeGemini:
    model = "fake"

    def __init__(self, entities=None, error=None):
        self.entities, self.error, self.prompts = entities, error, []

    def generate_json(self, prompt, schema, system=None):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return SimpleNamespace(data={"entities": self.entities})


def entity(search):
    return Entity(search, "Q" + str(len(search)), search, f"about {search}", "https://www.wikidata.org/wiki/Q1")


def test_the_lookup_uses_the_fuller_search_name_and_the_card_keeps_the_claims_own_words():
    looked = []
    cfg = load_config(CONFIG)
    gem = FakeGemini([{"name": "UPI", "search": "Unified Payments Interface"}, {"name": "Reserve Bank of India", "search": "Reserve Bank of India"}])
    out = ground_entities(gem, cfg, CLAIM, lambda s: looked.append(s) or entity(s))
    assert looked == ["Unified Payments Interface", "Reserve Bank of India"]
    assert [e.name for e in out.entities] == ["UPI", "Reserve Bank of India"] and out.error == ""
    assert CLAIM in gem.prompts[0] and "{claim}" not in gem.prompts[0]


def test_a_name_that_is_not_in_the_claim_is_dropped_and_never_looked_up():
    looked = []
    out = ground_entities(FakeGemini([{"name": "Narendra Modi", "search": "Narendra Modi"}, {"name": "UPI", "search": "UPI"}]), load_config(CONFIG), CLAIM,
                          lambda s: looked.append(s) or entity(s))
    assert looked == ["UPI"] and [e.name for e in out.entities] == ["UPI"]


def test_repeats_blank_names_and_non_objects_are_ignored_and_the_count_is_capped():
    cfg = load_config(CONFIG)
    cap = cfg.runtime.knowledge_graph.max_entities
    claim = " ".join(f"Name{i}" for i in range(cap + 3))
    raw = [{"name": "Name0", "search": ""}, {"name": "name0", "search": "x"}, {"name": "", "search": "y"}, "text", None] + [{"name": f"Name{i}", "search": f"Name{i}"} for i in range(1, cap + 3)]
    out = ground_entities(FakeGemini(raw), cfg, claim, entity)
    assert len(out.entities) == cap and out.entities[0].name == "Name0"


def test_a_name_wikidata_does_not_know_is_reported_as_unmatched():
    out = ground_entities(FakeGemini([{"name": "UPI", "search": "UPI"}]), load_config(CONFIG), CLAIM, lambda s: None)
    assert out.entities == [] and out.unmatched == ["UPI"]


def test_failures_reduce_the_result_and_name_the_class_only():
    cfg = load_config(CONFIG)
    down = ground_entities(FakeGemini(error=Unreachable("gemini_llm", "key=SECRET")), cfg, CLAIM, entity)
    assert down.entities == [] and down.error == "Unreachable" and "SECRET" not in repr(down)

    def flaky(search):
        if search == "UPI":
            raise Unreachable("wikidata", "timed out")
        return entity(search)

    out = ground_entities(FakeGemini([{"name": "UPI", "search": "UPI"}, {"name": "Reserve Bank of India", "search": "Reserve Bank of India"}]), cfg, CLAIM, flaky)
    assert [e.name for e in out.entities] == ["Reserve Bank of India"] and out.error == "Unreachable"
    assert ground_entities(FakeGemini("not a list"), cfg, CLAIM, entity).error == "BadOutput"


def test_the_wikidata_client_parses_a_search_hit_sends_a_descriptive_agent_and_caches(tmp_path):
    cfg = load_config(CONFIG)
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"search": [{"id": "Q1", "label": "Unified Payments Interface", "description": "instant payment system"}]})

    http = httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": "test-agent"})
    client = WikidataClient(cfg, TTLCache(tmp_path / "c.sqlite3"), http, sleep=lambda s: None)
    first = client.search("Unified  Payments Interface")
    again = client.search("unified payments interface")
    assert first.id == "Q1" and first.label == "Unified Payments Interface" and first.url.endswith("/Q1") and again.id == "Q1"
    assert len(seen) == 1 and seen[0].url.params["action"] == "wbsearchentities" and seen[0].url.params["limit"] == "1"


def test_the_wikidata_client_handles_no_hit_bad_shapes_and_outages(tmp_path):
    cfg = load_config(CONFIG)

    def make(response):
        http = httpx.Client(transport=httpx.MockTransport(response))
        return WikidataClient(cfg, TTLCache(tmp_path / f"{id(response)}.sqlite3"), http, sleep=lambda s: None)

    assert make(lambda r: httpx.Response(200, json={"search": []})).search("zzzz") is None
    assert make(lambda r: httpx.Response(200, json={"search": []})).search("   ") is None
    for body in ({"nope": 1}, {"search": [{"label": "no id"}]}):
        try:
            make(lambda r, b=body: httpx.Response(200, json=b)).search("x")
            raise AssertionError("expected SchemaChanged")
        except Exception as exc:
            assert type(exc).__name__ == "SchemaChanged"
    for code, name in ((429, "RateLimited"), (503, "Unreachable"), (404, "SchemaChanged")):
        try:
            make(lambda r, c=code: httpx.Response(c)).search("x")
            raise AssertionError("expected an error")
        except Exception as exc:
            assert type(exc).__name__ == name
    assert json.dumps({}) == "{}"
