"""The Raksha corpus: official documents on disk with a manifest, split into cited passages in an SQLite FTS5 index (plan package RR1).

Why a manifest: every passage the guide quotes must be traceable to a document, where it came from and when. The manifest holds each
file's SHA-256; building the index recomputes it and refuses a changed byte, so a quoted passage is the text we recorded, not a later edit.
Statute hosts block our honest User-Agent (403 on 2026-11-01). We do not disguise the client: those PDFs are added by hand with `add`.
"""

from __future__ import annotations

import hashlib
import html
import re
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml
from pydantic import BaseModel, ConfigDict

from ..config import config_dir
from ..errors import ForgeError

FILE = "raksha_rag.yaml"
MANIFEST = "manifest.yaml"
WORD = re.compile(r"\w+", re.UNICODE)
KINDS = ("statute", "rules", "advisory", "portal_help", "service")


class CorpusError(ForgeError):
    """The corpus on disk does not match its manifest, or a file cannot be read."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Text(_Strict):
    en: str
    hi: str


class Source(_Strict):
    id: str
    title: str
    publisher: str
    url: str
    kind: str


class Retrieval(_Strict):
    candidates: int
    top_k: int
    min_word_chars: int
    min_coverage: float
    min_words: int
    min_quote_chars: int
    number_words: dict[str, int]


class Chat(_Strict):
    ladder_terms: list[str]
    draft_terms: list[str]
    short_question_words: int
    history_turns: int


class Safety(_Strict):
    danger_terms: list[str]
    promise_terms: list[str]


class Rag(_Strict):
    corpus_dir: str
    db_path: str
    timeout_s: float
    max_bytes: int
    chunk_chars: int
    min_chunk_chars: int
    allowed_hosts: list[str]
    sources: list[Source]
    retrieval: Retrieval
    stopwords: list[str]
    safety: Safety
    chat: Chat
    refusal: Text
    unreviewed: Text


class Entry(_Strict):
    """One document we hold. `terms_read` stays false until someone reads the publisher's terms of use."""
    id: str
    title: str
    publisher: str
    url: str
    retrieved_on: str
    sha256: str
    kind: str
    file: str
    hand_downloaded: bool = False
    terms_read: bool = False


def load_rag(path: Path | None = None) -> Rag:
    return Rag.model_validate(yaml.safe_load((path or config_dir() / FILE).read_text(encoding="utf-8")))


def root_of(rag: Rag) -> Path:
    p = Path(rag.corpus_dir)
    return p if p.is_absolute() else config_dir().parent / p


def db_of(rag: Rag) -> Path:
    p = Path(rag.db_path)
    return p if p.is_absolute() else config_dir().parent / p


def host_allowed(url: str, allowed: list[str]) -> bool:
    host = (urlparse(url).hostname or "").casefold()
    return any(host == d.casefold() or host.endswith("." + d.casefold()) for d in allowed)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(rag: Rag) -> list[Entry]:
    p = root_of(rag) / MANIFEST
    if not p.is_file():
        return []
    return [Entry.model_validate(e) for e in (yaml.safe_load(p.read_text(encoding="utf-8")) or [])]


def save_manifest(rag: Rag, entries: list[Entry]) -> None:
    root = root_of(rag)
    root.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST).write_text(yaml.safe_dump([e.model_dump() for e in sorted(entries, key=lambda e: e.id)], sort_keys=False, allow_unicode=True), encoding="utf-8")


def _put(entries: list[Entry], entry: Entry) -> list[Entry]:
    return [e for e in entries if e.id != entry.id] + [entry]


def fetch_all(rag: Rag, client: httpx.Client) -> list[tuple[str, str]]:
    """Fetch each configured source over the honest client. A failure is reported by id and class and never stops the others."""
    root = root_of(rag)
    root.mkdir(parents=True, exist_ok=True)
    entries, report = load_manifest(rag), []
    for s in rag.sources:
        if not host_allowed(s.url, rag.allowed_hosts):
            report.append((s.id, "host not allowed"))
            continue
        try:
            r = client.get(s.url, follow_redirects=True)
        except httpx.HTTPError as exc:
            report.append((s.id, type(exc).__name__))
            continue
        if r.status_code != httpx.codes.OK:
            report.append((s.id, f"HTTP {r.status_code}"))
            continue
        if len(r.content) > rag.max_bytes:
            report.append((s.id, "too large"))
            continue
        ext = "pdf" if "pdf" in r.headers.get("content-type", "").casefold() else "html"
        (root / f"{s.id}.{ext}").write_bytes(r.content)
        entries = _put(entries, Entry(id=s.id, title=s.title, publisher=s.publisher, url=s.url, retrieved_on=date.today().isoformat(),
                                      sha256=hashlib.sha256(r.content).hexdigest(), kind=s.kind, file=f"{s.id}.{ext}"))
        report.append((s.id, "ok"))
    save_manifest(rag, entries)
    return report


def add_file(rag: Rag, source: Path, entry_id: str, title: str, publisher: str, url: str, kind: str) -> Entry:
    """Record a document downloaded by hand in a browser (the statute hosts block our client). The file is copied in and hashed."""
    if kind not in KINDS:
        raise CorpusError(f"kind must be one of {KINDS}")
    root = root_of(rag)
    root.mkdir(parents=True, exist_ok=True)
    name = f"{entry_id}{source.suffix.casefold()}"
    shutil.copyfile(source, root / name)
    entry = Entry(id=entry_id, title=title, publisher=publisher, url=url, retrieved_on=date.today().isoformat(),
                  sha256=sha256_file(root / name), kind=kind, file=name, hand_downloaded=True)
    save_manifest(rag, _put(load_manifest(rag), entry))
    return entry


def pages_of(path: Path) -> list[tuple[int, str]]:
    """(page number, text). HTML is one page; scripts, styles and tags are removed."""
    ext = path.suffix.casefold()
    if ext == ".pdf":
        from pypdf import PdfReader
        from pypdf.errors import DependencyError, PyPdfError
        try:
            reader = PdfReader(str(path))
            return [(i, p.extract_text() or "") for i, p in enumerate(reader.pages, start=1)]
        except (PyPdfError, DependencyError) as exc:
            raise CorpusError(f"{path.name}: cannot read PDF ({type(exc).__name__})") from None
    raw = path.read_text(encoding="utf-8", errors="replace")
    if ext in (".htm", ".html"):
        raw = re.sub(r"(?is)<(script|style|noscript|nav|footer|header)\b.*?</\1>", " ", raw)
        raw = re.sub(r"(?i)</(p|div|li|h[1-6]|tr|br|section|article)>|<br\s*/?>", "\n", raw)
        raw = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    return [(1, raw)]


def chunks_of(text: str, size: int, minimum: int) -> list[str]:
    """Paragraphs joined up to `size` characters; an over-long paragraph is cut at sentence ends. Menu-length fragments are dropped."""
    paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n|\n", text)]
    pieces: list[str] = []
    for p in paras:
        if len(p) <= size:
            pieces.append(p)
            continue
        for sent in re.split(r"(?<=[.;:?!।])\s+", p):
            while len(sent) > size:
                pieces.append(sent[:size])
                sent = sent[size:]
            pieces.append(sent)
    out, cur = [], ""
    for p in pieces:
        if not p:
            continue
        if cur and len(cur) + 1 + len(p) > size:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur} {p}".strip()
    if cur:
        out.append(cur)
    return [c for c in out if len(c) >= minimum]


@dataclass
class Chunk:
    pid: str            # doc id : page : number, the citation key
    doc_id: str
    title: str
    publisher: str
    url: str
    page: int
    text: str
    retrieved_on: str
    hand_downloaded: bool


def build_index(rag: Rag) -> dict[str, int]:
    """Verify every file against its manifest hash, then rebuild the index. Any mismatch or missing file stops the build before anything is written."""
    root, entries = root_of(rag), load_manifest(rag)
    if not entries:
        raise CorpusError("the manifest is empty: run fetch or add first")
    for e in entries:
        f = root / e.file
        if not f.is_file():
            raise CorpusError(f"{e.id}: file {e.file} is missing")
        if sha256_file(f) != e.sha256:
            raise CorpusError(f"{e.id}: {e.file} does not match the hash in the manifest")
    rows, counts = [], {}
    for e in entries:
        n = 0
        for page, text in pages_of(root / e.file):
            for k, chunk in enumerate(chunks_of(text, rag.chunk_chars, rag.min_chunk_chars), start=1):
                rows.append((f"{e.id}:{page}:{k}", e.id, e.title, e.publisher, e.url, page, chunk, e.retrieved_on, int(e.hand_downloaded)))
                n += 1
        counts[e.id] = n
    path = db_of(rag)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.execute("DROP TABLE IF EXISTS chunks")
        db.execute("CREATE VIRTUAL TABLE chunks USING fts5(text, pid UNINDEXED, doc_id UNINDEXED, title UNINDEXED, publisher UNINDEXED, url UNINDEXED, "
                   "page UNINDEXED, retrieved_on UNINDEXED, hand UNINDEXED, tokenize='unicode61')")
        db.executemany("INSERT INTO chunks (text, pid, doc_id, title, publisher, url, page, retrieved_on, hand) VALUES (?,?,?,?,?,?,?,?,?)",
                       [(r[6], r[0], r[1], r[2], r[3], r[4], r[5], r[7], r[8]) for r in rows])
        db.commit()
    return counts


class CorpusIndex:
    def __init__(self, rag: Rag):
        self.rag = rag
        self.path = db_of(rag)

    def ready(self) -> bool:
        if not self.path.is_file():
            return False
        with closing(sqlite3.connect(self.path)) as db:
            return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name = 'chunks'").fetchone())

    def _row(self, r) -> Chunk:
        return Chunk(pid=r[1], doc_id=r[2], title=r[3], publisher=r[4], url=r[5], page=int(r[6]), text=r[0], retrieved_on=r[7], hand_downloaded=bool(int(r[8])))

    def candidates(self, words: list[str], limit: int) -> list[Chunk]:
        if not words or not self.ready():
            return []
        match = " OR ".join(f'"{w}"*' for w in dict.fromkeys(w.replace('"', "") for w in words))   # prefix, so "complaint" also finds "complaints"
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("SELECT text, pid, doc_id, title, publisher, url, page, retrieved_on, hand FROM chunks WHERE chunks MATCH ? ORDER BY bm25(chunks) LIMIT ?",
                              (match, limit)).fetchall()
        return [self._row(r) for r in rows]

    def get(self, pid: str) -> Chunk | None:
        if not self.ready():
            return None
        with closing(sqlite3.connect(self.path)) as db:
            r = db.execute("SELECT text, pid, doc_id, title, publisher, url, page, retrieved_on, hand FROM chunks WHERE pid = ?", (pid,)).fetchone()
        return self._row(r) if r else None

    def count(self) -> int:
        if not self.ready():
            return 0
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("SELECT count(*) FROM chunks").fetchone()[0]
