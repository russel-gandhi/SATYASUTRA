"""The verdict dataset and its tooling (spec D1): rules for building rows, scoring, intervals, and the integrity of tests/eval/claims.jsonl itself."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"evalset_{name}", ROOT / "scripts" / "eval" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    sys.path.insert(0, str(ROOT / "scripts" / "eval"))
    spec.loader.exec_module(mod)
    return mod


lib = load("lib")


# ---- number changes -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("token,kind,expected", [
    ("8.96", "percent", "10.46"),
    ("7.1218", "percent", "8.6218"),
    ("14,167", "amount", "21,250"),
    ("625", "count", "650"),
])
def test_perturb_keeps_format_and_changes_value(token, kind, expected):
    assert lib.perturb_number(token, kind) == expected


def test_perturb_is_deterministic_and_always_different():
    for token, kind in [("95", "percent"), ("39,305", "amount"), ("23,000", "amount")]:
        assert lib.perturb_number(token, kind) == lib.perturb_number(token, kind) != token


def test_change_slot_replaces_exactly_one_place_and_refuses_ambiguity():
    text, new = lib.change_slot("Rates stood at 8.96 per cent.", "8.96", "percent")
    assert text == "Rates stood at 10.46 per cent." and new == "10.46"
    with pytest.raises(ValueError, match="exactly once"):
        lib.change_slot("8.96 then 8.96 again", "8.96", "percent")
    with pytest.raises(ValueError, match="exactly once"):
        lib.change_slot("no number here", "8.96", "percent")


def test_unknown_slot_kind_is_an_error():
    with pytest.raises(ValueError):
        lib.perturb_number("5", "colour")


# ---- intervals and outcomes ---------------------------------------------------------------------------------------------------

def test_wilson_interval_is_wide_for_small_samples_and_sane_at_the_edges():
    lo, hi = lib.wilson(28, 30)
    assert 0.78 < lo < 0.80 and 0.97 < hi < 0.99                  # the figure quoted in the spec: 28 of 30
    assert lib.wilson(0, 0) == (0.0, 0.0)
    lo0, hi0 = lib.wilson(0, 30)
    assert lo0 == 0.0 and 0.10 < hi0 < 0.13                      # zero errors in 30 still allows an error rate near 11 percent
    lo1, hi1 = lib.wilson(30, 30)
    assert hi1 == 1.0 and lo1 > 0.88


def test_pct_reports_count_and_range():
    assert lib.pct(28, 30).startswith("28/30 (93%, 79 to 98%)")
    assert lib.pct(0, 0) == "n/a"


def test_outcome_by_truth():
    false_row, true_row, unknown_row = {"truth": "false"}, {"truth": "true"}, {"truth": "unknown"}
    assert lib.outcome(false_row, "REFUTED") == "correct"
    assert lib.outcome(false_row, "SUPPORTED") == "wrong"
    assert lib.outcome(false_row, "UNVERIFIABLE_YET") == "abstain"
    assert lib.outcome(true_row, "REFUTED") == "wrong"          # the error that does the most harm
    assert lib.outcome(true_row, "UNVERIFIABLE_YET") == "abstain"
    assert lib.outcome(unknown_row, "UNVERIFIABLE_YET") == "correct"
    assert lib.outcome(unknown_row, "NO_CLAIM") == "correct"
    assert lib.outcome(unknown_row, "REFUTED") == "wrong"


def test_summarise_counts_named_metrics():
    rows = [
        {"group": "T", "truth": "true", "label": "REFUTED", "band": "medium", "outcome": "wrong", "seconds": 1.0},
        {"group": "T", "truth": "true", "label": "UNVERIFIABLE_YET", "band": "low", "outcome": "abstain", "seconds": 2.0},
        {"group": "C", "truth": "false", "label": "REFUTED", "band": "medium", "outcome": "correct", "seconds": 3.0},
        {"group": "C", "truth": "false", "label": "SUPPORTED", "band": "high", "outcome": "wrong", "seconds": 4.0},
        {"group": "U", "truth": "unknown", "label": "UNVERIFIABLE_YET", "band": "low", "outcome": "correct", "seconds": 5.0},
    ]
    s = lib.summarise(rows)
    assert s["false_refute"] == (1, 2) and s["false_support"] == (1, 2) and s["refute_recall"] == (1, 2)
    assert s["coverage"] == (3, 5) and s["correct_abstention"] == (1, 1)
    assert s["precision_by_band"]["medium"] == (1, 2) and s["precision_by_band"]["high"] == (0, 1)
    assert s["groups"]["T"]["n"] == 2 and s["seconds_median"] == 3.0


# ---- the split ----------------------------------------------------------------------------------------------------------------

def test_split_is_stable_and_derived_rows_follow_their_root():
    by_id = {"t1": {"id": "t1"}, "c1": {"id": "c1", "built_from": "t1"}, "h1": {"id": "h1", "base_id": "t1"}, "hc1": {"id": "hc1", "built_from": "h1", "base_id": "t1"}}
    splits = {i: lib.split_of(r, by_id) for i, r in by_id.items()}
    assert len(set(splits.values())) == 1 and set(splits.values()) <= {"dev", "test"}
    assert lib.split_of({"id": "t1"}) == lib.split_of({"id": "t1"})


def test_split_is_about_a_third_dev():
    dev = sum(lib.split_of({"id": f"row{i}"}) == "dev" for i in range(900))
    assert 250 < dev < 350


# ---- leak guard ---------------------------------------------------------------------------------------------------------------

def test_find_leaks_sees_a_test_claim_in_code_even_with_changed_case_and_quotes(tmp_path):
    (tmp_path / "prompt.txt").write_text('Example: "The Moon is made of GREEN cheese, said everyone."', encoding="utf-8")
    rows = [{"id": "x1", "split": "test", "text": "the moon is made of green cheese said everyone"},
            {"id": "x2", "split": "dev", "text": "the moon is made of green cheese said everyone"},
            {"id": "x3", "split": "test", "text": "something that appears nowhere at all in the folder"}]
    hits = lib.find_leaks(rows, [tmp_path])
    assert [h[0] for h in hits] == ["x1"]                       # dev rows may be used freely; only test rows must stay hidden


# ---- the dataset file itself --------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rows():
    data = lib.read_jsonl(lib.CLAIMS)
    assert data, "tests/eval/claims.jsonl is empty: run scripts/eval/assemble.py"
    return data


def test_ids_are_unique_and_fields_present(rows):
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids))
    for r in rows:
        for field in ("id", "group", "text", "lang", "truth", "expected", "wrong", "split", "review_status"):
            assert r.get(field) not in (None, ""), (r["id"], field)
        assert r["truth"] in ("true", "false", "unknown") and r["split"] in ("dev", "test")
        assert not set(r["expected"]) & set(r["wrong"]), r["id"]


def test_every_changed_number_claim_differs_from_its_true_twin_by_exactly_its_slot(rows):
    by_id = {r["id"]: r for r in rows}
    changed = [r for r in rows if r["group"] in ("C", "H") and r["built_from"]]
    assert changed
    for c in changed:
        origin = by_id[c["built_from"]]
        old, new = c["change"].split(" -> ")[0], c["change"].split(" -> ")[1].split(" (")[0]
        assert origin["text"].replace(old, new, 1) == c["text"], c["id"]
        assert c["truth"] == "false" and origin["truth"] == "true" and c["split"] == origin["split"]


def test_true_official_rows_quote_a_release_we_hold(rows):
    snapshot = {p["url"]: p for p in lib.read_jsonl(lib.SNAPSHOT)}
    for r in (r for r in rows if r["group"] == "T"):
        assert r["source_url"] in snapshot, r["id"]
        if r["slot"]:                                           # the figure a rumour would change must really be in the release text
            assert r["slot"] in snapshot[r["source_url"]]["text"], (r["id"], r["slot"])


def test_hindi_rows_are_flagged_unreviewed_and_follow_their_base_split(rows):
    by_id = {r["id"]: r for r in rows}
    hindi = [r for r in rows if r["group"] == "H"]
    assert hindi
    for h in hindi:
        assert h["lang"] == "hi" and h["hindi_reviewed"] is False
        assert h["split"] == by_id[h["base_id"]]["split"], h["id"]


def test_rows_are_up_to_date_with_their_sources():
    """claims.jsonl must be what assemble.py produces from authored.yaml, so nobody hand-edits the generated file."""
    assemble = load("assemble")
    assert assemble.build() == lib.read_jsonl(lib.CLAIMS)


def test_new_groups_are_free_of_leaks_and_old_sheet_leaks_are_known(rows):
    """Claims we wrote for this dataset must not appear in code, config or unit tests. The earlier sheet (B) was made after the system
    existed, and some of its claims are used as examples in the code's own comments; the harness excludes those rows from the test split
    and lists them in the claim sheet, so they are reported here instead of failing."""
    by_id = {r["id"]: r for r in rows}
    leaks = lib.find_leaks(rows, [ROOT / "src", ROOT / "config", ROOT / "tests" / "unit"])
    fresh = [(rid, path) for rid, path in leaks if by_id[rid]["group"] != "B"]
    assert fresh == [], f"claims written for this dataset appear in files the system or its tests read: {fresh[:5]}"
