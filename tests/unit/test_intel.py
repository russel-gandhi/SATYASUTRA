from satyasutra.config import load_config
from satyasutra.intel import case_view, global_graph, group_claims, media_lab, overview, summarise
from satyasutra.records import RecordStore

CFG = load_config()


def claim(text, label="REFUTED", lang="en", cid="c", cites=None):
    return {"claim_id": cid, "text": text, "quote": text, "lang": lang, "label": label, "confidence_band": "high", "flags": [], "reasons": [],
            "citations": cites if cites is not None else [{"role": "contradicts", "route": "factcheck", "url": f"https://x.org/{cid}", "tier": 2, "publisher": "BBC", "source_id": "s",
                                                           "reviewed_claim": "t", "reviewed_at": None, "rating": "False", "passage_sha256": "a"}],
            "related": [], "entities": [], "ledger_seq": 1}


def rec(rid, c, at):
    return {"id": rid, "kind": "claims", "created_at": at, "body": {"text": c["text"], "body": {"claims": [c]}, "spread": None}}


def test_same_claim_in_two_languages_groups_because_text_is_english_first():
    a = rec("r1", claim("COVID-19 vaccines contain microchips", cid="a"), "2026-10-31T10:00:00+00:00")
    b = rec("r2", claim("COVID-19 vaccines contain microchips.", lang="hi", cid="b"), "2026-10-31T11:00:00+00:00")
    other = rec("r3", claim("The bridge opened today", label="SUPPORTED", cid="c"), "2026-10-31T12:00:00+00:00")
    groups = group_claims([a, b, other], CFG)
    assert [g["investigations"] for g in groups] == [2, 1]
    assert groups[0]["languages"] == {"en": 1, "hi": 1} and groups[0]["first_seen"].startswith("2026-10-31T10")


def test_overview_counts_only_what_is_stored_and_says_so():
    recs = [rec("r1", claim("A thing happened"), "2026-10-30T10:00:00+00:00"), rec("r2", claim("Another thing", label="SUPPORTED", cid="d"), "2026-10-31T10:00:00+00:00")]
    o = overview(recs, CFG)
    assert o["totals"]["investigations"] == 2 and o["verdicts"] == {"REFUTED": 1, "SUPPORTED": 1} and set(o["per_day"]) == {"2026-10-30", "2026-10-31"}
    assert "not live or nationwide" in o["note"] and "No location data" in o["geography"]


def test_summary_has_no_thumbnails_or_evidence():
    media = {"id": "m1", "kind": "media", "created_at": "x", "body": {"name": "a.jpg", "thumb": "AAAA", "metadata": [], "result": {"label": "EDITED", "reasons": [], "matches": []}}}
    s = summarise(media)
    assert s["verdicts"] == ["EDITED"] and "thumb" not in s and s["title"] == "a.jpg"
    assert media_lab([media])[0]["thumb"] == "AAAA"


def test_shared_source_is_one_node_across_investigations():
    shared = [{"role": "contradicts", "route": "factcheck", "url": "https://x.org/same", "tier": 2, "publisher": "BBC", "source_id": "s", "reviewed_claim": "t",
               "reviewed_at": None, "rating": "False", "passage_sha256": "a"}]
    g = global_graph([rec("r1", claim("Claim one", cid="a", cites=shared), "t1"), rec("r2", claim("Claim two differs", cid="b", cites=shared), "t2")])
    assert sum(n["kind"] == "factcheck" for n in g["nodes"]) == 1 and len(g["edges"]) == 2


def test_cases_point_at_records_and_report_deleted_ones(tmp_path):
    store = RecordStore(tmp_path / "r.sqlite3")
    body = {"text": "A thing happened", "body": {"claims": [claim("A thing happened")]}, "spread": None}
    rid = store.add_record("claims", body, 100000)
    cid = store.save_case({"title": "Case", "record_ids": [rid, "gone"], "notes": [{"at": "t", "text": "n"}]})
    view = case_view(store.get_case(cid), store.list_records(10), CFG)
    assert [t["id"] for t in view["timeline"]] == [rid] and view["missing"] == ["gone"] and view["claims"][0]["text"] == "A thing happened"
    assert store.delete_record(rid) and not store.delete_record(rid)
    assert case_view(store.get_case(cid), store.list_records(10), CFG)["timeline"] == []


def test_history_listing_is_limited_to_the_ids_asked_for(tmp_path):
    store = RecordStore(tmp_path / "r.sqlite3")
    mine = store.add_record("claims", {"text": "mine", "body": {"claims": []}}, 100000)
    store.add_record("claims", {"text": "theirs", "body": {"claims": []}}, 100000)
    assert [r["id"] for r in store.list_records(10, ids=[mine])] == [mine]
    assert store.list_records(10, ids=[]) == []
