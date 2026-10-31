from datetime import datetime, timezone

import pytest

from satyasutra.config import load_config
from satyasutra.records import RecordStore
from satyasutra.report import build_graph, build_report, context_findings

NOW = datetime(2026, 10, 31, tzinfo=timezone.utc)
CFG = load_config()


def citation(role="contradicts", date="2020-05-29T00:00:00+00:00", route="factcheck", url="https://www.example.org/a"):
    return {"role": role, "route": route, "url": url, "tier": 2, "publisher": "BBC", "rating": "False", "reviewed_claim": "chip claim",
            "reviewed_at": date, "passage_sha256": "ab"}


def claim(label="REFUTED", text="Vaccines contain microchips", cites=None, **extra):
    return {"claim_id": "c1", "text": text, "quote": text, "lang": "en", "label": label, "confidence_band": "high", "flags": [], "reasons": ["why"],
            "citations": [citation()] if cites is None else cites, "related": [], "ledger_seq": 3, "entities": [], **extra}


def record(c, rid="r1"):
    return {"id": rid, "kind": "claims", "created_at": "x", "body": {"text": c["text"], "body": {"claims": [c]}, "spread": None}}


def test_store_round_trip_and_size_limit(tmp_path):
    store = RecordStore(tmp_path / "r.sqlite3")
    rid = store.add_record("claims", {"text": "x"}, 1000)
    assert store.get_record(rid)["body"] == {"text": "x"}
    assert store.get_record("nope") is None
    with pytest.raises(ValueError):
        store.add_record("claims", {"text": "x" * 2000}, 1000)


def test_report_id_is_content_hash_so_links_are_immutable(tmp_path):
    store = RecordStore(tmp_path / "r.sqlite3")
    rep = build_report([record(claim())], CFG, "en", NOW)
    assert store.add_report(rep) == store.add_report(rep)
    assert store.get_report(store.add_report(rep)) == rep


def test_verdict_names_follow_language_and_no_evidence_is_insufficient():
    en = build_report([record(claim())], CFG, "en", NOW)["sections"]["claims"][0]
    hi = build_report([record(claim())], CFG, "hi", NOW)["sections"]["claims"][0]
    assert en["verdict_name"] == "FALSE" and hi["verdict_name"] != en["verdict_name"]
    empty = build_report([record(claim(label="UNVERIFIABLE_YET", cites=[]))], CFG, "en", NOW)["sections"]["claims"][0]
    assert empty["verdict_name"] == CFG.runtime.report.insufficient_label["en"]


def test_missing_evidence_is_not_called_false():
    c = build_report([record(claim(label="UNVERIFIABLE_YET", cites=[]))], CFG, "en", NOW)["sections"]["claims"][0]
    assert c["verdict_name"] != "FALSE" and not c["contradicting"] and not c["supporting"]


def test_unknown_language_falls_back_to_english():
    assert build_report([record(claim())], CFG, "kn-xx", NOW)["lang"] == "en"


def test_graph_only_has_relationships_from_records():
    g = build_graph([record(claim())], [claim()])
    kinds = {n["kind"] for n in g["nodes"]}
    assert kinds == {"claim", "factcheck"}
    assert [e["relation"] for e in g["edges"]] == ["contradicts"]


def test_media_matches_become_same_image_edges():
    media = {"id": "m1", "kind": "media", "created_at": "x", "body": {"name": "a.jpg", "thumb": None, "metadata": [], "result": {
        "label": "OUT_OF_CONTEXT", "reasons": [], "matches": [{"article_url": "https://x.org/f", "publisher": "AFP", "title": "t", "distance": 3}]}}}
    g = build_graph([media], [])
    assert [e["relation"] for e in g["edges"]] == ["same_image_as"]


def test_old_coverage_for_a_now_claim_is_flagged_recycled():
    c = claim(text="Breaking: bridge collapses today", cites=[citation(role="supports", route="web", date="2026-01-02T00:00:00+00:00")])
    found = context_findings(c, CFG, NOW)
    assert [f["kind"] for f in found] == ["possibly_recycled"]


def test_fresh_coverage_or_no_now_wording_is_not_flagged():
    fresh = claim(text="Breaking: bridge collapses today", cites=[citation(route="web", date="2026-10-30T00:00:00+00:00")])
    assert context_findings(fresh, CFG, NOW) == []
    timeless = claim(text="Vaccines contain microchips", cites=[citation(route="web", date="2020-01-01T00:00:00+00:00")])
    assert context_findings(timeless, CFG, NOW) == []
