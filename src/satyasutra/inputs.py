"""What people submit besides plain text: an image thumbnail and its metadata findings, a public web page, a PDF circular.

A pasted link is fetched by this server, so the fetch is the dangerous part: only http and https, every hop (including redirects, followed by hand)
must resolve to public addresses only, the body is read up to a byte cap, and only HTML, plain text and PDF are accepted. Resolution and connection are
two steps, so a hostile DNS server could still answer differently the second time; that gap is stated, not closed.
Metadata findings are facts about the file (who saved it, when), never a verdict: platforms strip metadata, and metadata can be edited.
"""

from __future__ import annotations

import base64
import io
import ipaddress
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx
from PIL import ExifTags, Image, UnidentifiedImageError

from .config import Config
from .sources.probe import build_user_agent

PDF_TYPES = ("application/pdf",)
TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
REDIRECTS = (301, 302, 303, 307, 308)
EXIF_IFD, GPS_IFD = 0x8769, 0x8825
DATE_ORIGINAL = 0x9003


class InputError(ValueError):
    """The submission could not be used; the message is safe to show to the user."""


@dataclass
class Page:
    url: str
    title: str
    text: str
    kind: str                # "html", "text" or "pdf"


def image_thumb(data: bytes, px: int) -> str | None:
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except (UnidentifiedImageError, OSError):
        return None
    img.thumbnail((px, px))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=70)
    return base64.b64encode(out.getvalue()).decode("ascii")


def image_metadata(data: bytes, cfg: Config | None = None) -> list[dict]:
    """Plain facts from the file's own metadata. No metadata is normal (messaging apps and social networks strip it) and proves nothing either way."""
    try:
        exif = Image.open(io.BytesIO(data)).getexif()
    except (UnidentifiedImageError, OSError):
        return []
    names = ExifTags.TAGS
    found = {names.get(k, k): v for k, v in exif.items()}
    found.update({names.get(k, k): v for k, v in exif.get_ifd(EXIF_IFD).items()})
    out: list[dict] = []
    if not found:
        return [{"kind": "no_metadata", "text": "The file carries no camera metadata. That is normal for pictures shared through messaging apps and social networks, which remove it, so it says nothing either way."}]
    device = " ".join(str(found[k]).strip() for k in ("Make", "Model") if found.get(k))
    if device:
        out.append({"kind": "device", "text": f"Metadata names the device: {device}."})
    software = str(found.get("Software", "")).strip()
    if software:
        terms = cfg.runtime.inputs.editor_terms if cfg else []
        edited = any(t.casefold() in software.casefold() for t in terms)
        out.append({"kind": "software", "text": f"The file was last saved by {software}." + (" That is an image editor or generator, so the picture passed through it. Editing is common and is not by itself a sign of deception." if edited else "")})
    taken = str(found.get("DateTimeOriginal") or found.get("DateTime") or "").strip()
    if taken:
        out.append({"kind": "date", "text": f"Metadata gives the date {taken}. Metadata can be edited, so treat the date as a lead, not proof of when it happened."})
    if exif.get_ifd(GPS_IFD):
        out.append({"kind": "gps", "text": "The file carries GPS coordinates. Sharing it as is reveals where it was taken."})
    return out


def _public(host: str, resolve=socket.getaddrinfo) -> bool:
    try:
        addrs = {ipaddress.ip_address(info[4][0]) for info in resolve(host, None)}
    except (socket.gaierror, ValueError, OSError):
        return False
    return bool(addrs) and all(a.is_global for a in addrs)


class _Text(HTMLParser):
    def __init__(self, skip: set[str]):
        super().__init__(convert_charrefs=True)
        self.skip, self.depth, self.title, self.in_title, self.parts = skip, 0, "", False, []

    def handle_starttag(self, tag, attrs):
        if tag in self.skip:
            self.depth += 1
        self.in_title = self.in_title or tag == "title"

    def handle_endtag(self, tag):
        if tag in self.skip and self.depth:
            self.depth -= 1
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.depth and data.strip():
            self.parts.append(data.strip())


def html_text(body: str, cfg: Config) -> tuple[str, str]:
    p = _Text(set(cfg.runtime.inputs.html_skip_tags))
    p.feed(body)
    return " ".join(p.title.split()), " ".join(" ".join(p.parts).split())


def pdf_text(data: bytes, cfg: Config) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = reader.pages[: cfg.runtime.inputs.max_pdf_pages]
        return " ".join(" ".join((p.extract_text() or "").split()) for p in pages).strip()
    except (PyPdfError, ValueError, OSError, KeyError, TypeError):
        raise InputError("this is not a readable PDF") from None


def fetch_page(cfg: Config, url: str, http: httpx.Client | None = None, resolve=socket.getaddrinfo) -> Page:
    s = cfg.runtime.inputs
    client = http or httpx.Client(timeout=s.timeout_s, headers={"User-Agent": build_user_agent(cfg.runtime.probe.user_agent)}, follow_redirects=False)
    current = url.strip()
    try:
        for _ in range(s.max_redirects + 1):
            parts = urlsplit(current)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                raise InputError("only http and https links can be read")
            if not _public(parts.hostname, resolve):
                raise InputError("this address is not a public website, so it was not opened")
            try:
                with client.stream("GET", current) as r:
                    if r.status_code in REDIRECTS and r.headers.get("location"):
                        current = urljoin(current, r.headers["location"])
                        continue
                    if r.status_code >= httpx.codes.BAD_REQUEST:
                        raise InputError(f"the site answered {r.status_code}, so nothing was read (we do not disguise ourselves to get past that)")
                    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                    if ctype not in (*TEXT_TYPES, *PDF_TYPES):
                        raise InputError(f"this link is a {ctype or 'file of unknown type'}, not a page or PDF; upload it instead")
                    body = b""
                    for chunk in r.iter_bytes():
                        body += chunk
                        if len(body) > s.max_bytes:
                            raise InputError("the page is too large to read")
                    encoding = r.encoding or "utf-8"
            except httpx.HTTPError as exc:
                raise InputError(f"the page could not be fetched ({type(exc).__name__})") from None
            if ctype in PDF_TYPES:
                return Page(current, "", pdf_text(body, cfg)[: s.max_text_chars], "pdf")
            decoded = body.decode(encoding, errors="replace")
            if ctype == "text/plain":
                return Page(current, "", " ".join(decoded.split())[: s.max_text_chars], "text")
            title, text = html_text(decoded, cfg)
            return Page(current, title, text[: s.max_text_chars], "html")
        raise InputError("too many redirects")
    finally:
        if http is None:
            client.close()
