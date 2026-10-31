"""Build tests/eval/claims.jsonl from its sources. Deterministic: the same inputs always give the same file.

    python scripts/eval/assemble.py

Inputs: tests/eval/authored.yaml (T, U, R and Hindi rows), eval/claims.yaml (the earlier 22-claim sheet, group B),
tests/eval/official_snapshot.jsonl (the releases the T rows quote) and tests/eval/f_rows.jsonl (false claims from fact-checks that two
people have accepted; empty until then). C rows (one number changed) are made here by code from the T rows that carry a slot.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import CLAIMS, EVAL_DIR, RULES, ROOT, SNAPSHOT, change_slot, read_jsonl, split_of, write_jsonl  # noqa: E402

AUTHORED = EVAL_DIR / "authored.yaml"
OLD_SHEET = ROOT / "eval" / "claims.yaml"
F_ROWS = EVAL_DIR / "f_rows.jsonl"
ONE_LABELLER = "pending_second_labeller"


def _row(**kw) -> dict:
    row = {"lang": "en", "source_url": "", "source_date": "", "source_title": "", "built_from": "", "base_id": "", "slot": "", "change": "",
           "labelled_by": [], "review_status": "from_source", "hindi_reviewed": None, "notes": ""} | kw
    row["truth"] = str(row["truth"]).lower()          # YAML reads an unquoted true or false as a boolean
    rules = RULES[row["truth"]]
    row["expected"], row["wrong"] = rules["expected"], rules["wrong"]
    return row


def _source(snapshot: list[dict], match: str) -> dict:
    if not match:
        return {}
    for p in snapshot:
        if match.lower() in p["title"].lower():
            return {"source_url": p["url"], "source_date": (p["published_at"] or "")[:10], "source_title": p["title"]}
    raise SystemExit(f"no snapshot passage with a title containing {match!r}; run scripts/eval/build_official.py")


def build() -> list[dict]:
    authored = yaml.safe_load(AUTHORED.read_text(encoding="utf-8"))
    snapshot = read_jsonl(SNAPSHOT)
    rows: list[dict] = []

    for c in yaml.safe_load(OLD_SHEET.read_text(encoding="utf-8"))["claims"]:
        if c["id"] == "u01":                                   # the assassination claim is group R (r01); do not count it twice
            continue
        truth = str(c["truth"]).lower()
        rows.append(_row(id=f"b-{c['id']}", group="B", text=c["text"], truth=truth, labelled_by=["earlier sheet, one labeller"],
                         review_status=ONE_LABELLER, notes="from eval/claims.yaml"))

    for t in authored["official_true"]:
        src = _source(snapshot, t["source_match"])
        rows.append(_row(id=t["id"], group="T", text=t["text"], truth="true", slot=t.get("slot", ""), notes=f"slot kind: {t.get('kind', '')}".strip(": "), **src))
        if t.get("slot"):
            new_text, new_token = change_slot(t["text"], t["slot"], t["kind"])
            rows.append(_row(id="c" + t["id"][1:], group="C", text=new_text, truth="false", built_from=t["id"], slot=t["slot"],
                             change=f"{t['slot']} -> {new_token} ({t['kind']})", notes="one number changed by code from the true claim", **src))

    for u in authored["unknowable"]:
        rows.append(_row(id=u["id"], group="U", text=u["text"], truth="unknown", labelled_by=["one labeller"], review_status=ONE_LABELLER))

    for r in authored["rumours"]:
        rows.append(_row(id=r["id"], group="R", text=r["text"], truth="false", labelled_by=["one labeller"], review_status=ONE_LABELLER,
                         notes=r.get("notes", ""), **_source(snapshot, r.get("source_match", ""))))

    for h in authored["hindi"]:
        rows.append(_row(id=h["id"], group="H", text=h["text"], lang="hi", truth=h["truth"], base_id=h["base_id"], slot=h.get("slot", ""),
                         hindi_reviewed=False, labelled_by=["draft translation, not reviewed"], review_status=ONE_LABELLER, notes="Hindi draft; a native reader must review"))
        if h.get("derive_changed"):
            new_text, new_token = change_slot(h["text"], h["slot"], h["kind"])
            rows.append(_row(id="hc" + h["id"][1:], group="H", text=new_text, lang="hi", truth="false", built_from=h["id"], base_id=h["base_id"],
                             slot=h["slot"], change=f"{h['slot']} -> {new_token} ({h['kind']})", hindi_reviewed=False,
                             labelled_by=["draft translation, not reviewed"], review_status=ONE_LABELLER, notes="Hindi draft with one number changed by code"))

    for f in read_jsonl(F_ROWS):                               # false claims from published fact-checks; kept only when two people accepted them
        if f.get("review_status") == "accepted" and len(set(f.get("labelled_by", []))) >= 2:
            rows.append(_row(**{k: f[k] for k in f if k not in ("expected", "wrong")}))

    ids = [r["id"] for r in rows]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise SystemExit(f"duplicate ids: {sorted(dupes)}")
    by_id = {r["id"]: r for r in rows}
    for r in rows:
        r["split"] = split_of(r, by_id)
        r["built_on"] = max((p["fetched_at"][:10] for p in snapshot), default="")
    return sorted(rows, key=lambda r: (r["group"], r["id"]))


def main() -> int:
    rows = build()
    write_jsonl(CLAIMS, rows)
    groups = {}
    for r in rows:
        groups.setdefault(r["group"], {"dev": 0, "test": 0})[r["split"]] += 1
    print(f"wrote {len(rows)} rows to {CLAIMS.relative_to(ROOT)}")
    for g, c in sorted(groups.items()):
        print(f"  {g}: {c['dev'] + c['test']} (dev {c['dev']}, test {c['test']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
