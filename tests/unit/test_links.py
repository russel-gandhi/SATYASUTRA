"""The lookalike-link check: names only, never opens a link, and never calls an unknown site safe."""

import pytest
from fastapi.testclient import TestClient

from satyasutra import __version__
from satyasutra.api.app import create_app
from satyasutra.config import load_config
from satyasutra.ledger import SQLiteLedgerStore
from satyasutra.scam import check_links, load_domains
from satyasutra.scam.links import distance, registered, skeleton


def one(text):
    found = check_links(text)
    assert len(found) == 1, found
    return found[0]


@pytest.mark.parametrize("text,status,real", [
    ("https://pmkisan.gov.in/Home", "official", "pmkisan.gov.in"),
    ("https://portal.uidai.gov.in/x", "official", "uidai.gov.in"),
    ("pm-kisan.gov.in/apply", "lookalike", "pmkisan.gov.in"),
    ("pmkisan.gov.in.verify-now.xyz", "lookalike", "pmkisan.gov.in"),
    ("https://www.hdfcbank.com.secure-login.in/x", "lookalike", "hdfcbank.com"),
    ("sbi-kyc-update.com", "lookalike", "sbi.co.in"),
    ("https://5bi.co.in", "lookalike", "sbi.co.in"),
    ("https://uidai.gov.in@evil.example.com/login", "lookalike", "uidai.gov.in"),
    ("https://pmkisаn.gov.in/x", "lookalike", "pmkisan.gov.in"),     # a Cyrillic letter in the name
    ("https://pmkissan.gov.in", "lookalike", "pmkisan.gov.in"),             # near-miss spelling
    ("hdfcbank.in", "similar_name", "hdfcbank.com"),
    ("bit.ly/abc123", "shortened", ""),
    ("https://fake.gov.in", "gov_style", ""),
    ("https://example.org/page", "unknown", ""),
])
def test_each_kind_of_link(text, status, real):
    f = one(text)
    assert f.status == status and f.real_domain == real, f
    if status == "lookalike":
        assert real in f.reason                                             # the real domain is always shown to the reader


def test_unknown_is_never_called_safe_and_prose_with_dots_is_not_a_link():
    f = one("https://example.org/page")
    assert "not a statement that it is safe" in f.reason
    assert check_links("e.g. this, i.e. that. See Fig. 3 and 5.5 per cent. Mr. Rao said so.") == []


def test_one_finding_per_host_and_trailing_punctuation_is_ignored():
    found = check_links("Click https://pmkisan.gov.in/a, then https://pmkisan.gov.in/b). Also pmkisan.gov.in.")
    assert [f.host for f in found] == ["pmkisan.gov.in"] and found[0].status == "official"


def test_helpers():
    suffixes = load_domains().suffixes
    assert registered("portal.pmkisan.gov.in", suffixes) == ("pmkisan", "gov.in", "pmkisan.gov.in")
    assert registered("www.example.com", suffixes) == ("example", "com", "example.com")
    assert distance("kitten", "sitting") == 3 and distance("abc", "abc") == 0
    assert skeleton("pm-k1san", load_domains().homoglyphs) == "pmklsan"


def test_the_route_returns_findings_and_says_links_are_never_opened(tmp_path):
    cfg = load_config()
    client = TestClient(create_app(cfg, SQLiteLedgerStore(tmp_path / "l.sqlite3", code_version=__version__)))
    body = client.get("/links/check", params={"text": "Your Aadhaar is blocked, update at https://uidai-kyc.com now"}).json()
    assert body["links"][0]["status"] == "lookalike" and body["links"][0]["real_domain"] == "uidai.gov.in" and "never opened" in body["note"]
    assert client.get("/links/check", params={"text": "no links here"}).json()["links"] == []
