"""Manage the Raksha corpus (plan RR1).

    python scripts/raksha_corpus.py fetch                 # fetch the configured official pages with our honest User-Agent
    python scripts/raksha_corpus.py add FILE --id ID --title T --publisher P --url U --kind statute|rules|advisory|portal_help|service
    python scripts/raksha_corpus.py build                 # check every hash against the manifest, then rebuild the index
    python scripts/raksha_corpus.py list                  # every document with hash, page count and passages
    python scripts/raksha_corpus.py peek ID               # the text of one document, to read it before writing anything that cites it

`add` is for documents downloaded by hand in a browser (statute hosts answer 403 to our client; we do not disguise it).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402

from satyasutra.config import load_config  # noqa: E402
from satyasutra.envfile import load_env_file  # noqa: E402
from satyasutra.raksha.corpus import (CorpusError, CorpusIndex, KINDS, add_file, build_index, chunks_of, fetch_all, load_manifest, load_rag,  # noqa: E402
                                      pages_of, root_of)
from satyasutra.sources.probe import build_user_agent  # noqa: E402


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch")
    sub.add_parser("build")
    sub.add_parser("list")
    p = sub.add_parser("peek")
    p.add_argument("id")
    p.add_argument("--chars", type=int, default=6000)
    a = sub.add_parser("add")
    a.add_argument("file", type=Path)
    for name in ("id", "title", "publisher", "url"):
        a.add_argument(f"--{name}", required=True)
    a.add_argument("--kind", required=True, choices=KINDS)
    args = ap.parse_args(argv)

    load_env_file(ROOT / ".env")
    rag = load_rag()
    if args.cmd == "fetch":
        cfg = load_config()
        with httpx.Client(headers={"User-Agent": build_user_agent(cfg.runtime.probe.user_agent)}, timeout=rag.timeout_s) as client:
            for sid, state in fetch_all(rag, client):
                print(f"{sid:<22}{state}")
        return 0
    if args.cmd == "add":
        e = add_file(rag, args.file, args.id, args.title, args.publisher, args.url, args.kind)
        print(f"added {e.id}  sha256 {e.sha256}")
        return 0
    if args.cmd == "build":
        try:
            counts = build_index(rag)
        except CorpusError as exc:
            print(f"build refused: {exc}")
            return 1
        for k, v in counts.items():
            print(f"{k:<22}{v} passages")
        return 0
    if args.cmd == "peek":
        e = next((x for x in load_manifest(rag) if x.id == args.id), None)
        if e is None:
            print("no such document")
            return 1
        for page, text in pages_of(root_of(rag) / e.file):
            print(f"--- page {page}")
            print("\n".join(chunks_of(text, rag.chunk_chars, rag.min_chunk_chars))[: args.chars])
        return 0
    idx = CorpusIndex(rag)
    print(f"{'id':<22}{'kind':<12}{'pages':<7}{'retrieved':<12}{'by hand':<9}sha256")
    for e in load_manifest(rag):
        try:
            pages = len(pages_of(root_of(rag) / e.file))
        except (CorpusError, OSError):
            pages = -1
        print(f"{e.id:<22}{e.kind:<12}{pages:<7}{e.retrieved_on:<12}{str(e.hand_downloaded):<9}{e.sha256}")
    print(f"index: {idx.count()} passages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
