import io

import httpx
import pytest
from PIL import Image

from satyasutra.config import load_config
from satyasutra.inputs import InputError, fetch_page, html_text, image_metadata, pdf_text

CFG = load_config()
PUBLIC = lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))]
PRIVATE = lambda host, port: [(2, 1, 6, "", ("192.168.1.5", 0))]
LOOPBACK = lambda host, port: [(2, 1, 6, "", ("127.0.0.1", 0))]
PAGE = b"<html><head><title>Bridge opens</title><style>x{}</style></head><body><nav>menu</nav><p>The bridge opened today.</p><script>bad()</script></body></html>"


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_html_text_drops_scripts_nav_and_keeps_title():
    title, text = html_text(PAGE.decode(), CFG)
    assert title == "Bridge opens" and text == "The bridge opened today."


def test_fetch_reads_a_public_page():
    page = fetch_page(CFG, "https://news.example/a", client(lambda r: httpx.Response(200, content=PAGE, headers={"content-type": "text/html; charset=utf-8"})), PUBLIC)
    assert page.kind == "html" and "bridge opened" in page.text


@pytest.mark.parametrize("resolver", [PRIVATE, LOOPBACK])
def test_fetch_refuses_non_public_addresses(resolver):
    called = []
    with pytest.raises(InputError):
        fetch_page(CFG, "http://intranet.example/", client(lambda r: called.append(1) or httpx.Response(200)), resolver)
    assert not called


def test_fetch_refuses_other_schemes():
    for url in ("file:///etc/passwd", "ftp://x.example/a", "javascript:alert(1)"):
        with pytest.raises(InputError):
            fetch_page(CFG, url, client(lambda r: httpx.Response(200)), PUBLIC)


def test_redirect_to_a_private_address_is_refused():
    def resolve(host, port):
        return PRIVATE(host, port) if host == "inner.example" else PUBLIC(host, port)
    handler = lambda r: httpx.Response(302, headers={"location": "http://inner.example/secret"})
    with pytest.raises(InputError, match="not a public website"):
        fetch_page(CFG, "https://news.example/a", client(handler), resolve)


def test_redirect_loop_stops():
    handler = lambda r: httpx.Response(302, headers={"location": "https://news.example/a"})
    with pytest.raises(InputError, match="redirects"):
        fetch_page(CFG, "https://news.example/a", client(handler), PUBLIC)


def test_refusal_and_wrong_type_are_reported_not_bypassed():
    with pytest.raises(InputError, match="403"):
        fetch_page(CFG, "https://x.example/", client(lambda r: httpx.Response(403)), PUBLIC)
    with pytest.raises(InputError, match="not a page or PDF"):
        fetch_page(CFG, "https://x.example/", client(lambda r: httpx.Response(200, content=b"x", headers={"content-type": "application/zip"})), PUBLIC)


def test_oversize_page_is_refused():
    big = b"a" * (CFG.runtime.inputs.max_bytes + 10)
    with pytest.raises(InputError, match="too large"):
        fetch_page(CFG, "https://x.example/", client(lambda r: httpx.Response(200, content=big, headers={"content-type": "text/plain"})), PUBLIC)


def make_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref)


def test_pdf_text_is_extracted_and_garbage_is_refused():
    assert "Circular on deposit rates" in pdf_text(make_pdf("Circular on deposit rates"), CFG)
    with pytest.raises(InputError):
        pdf_text(b"not a pdf", CFG)


def jpeg(exif=None) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, format="JPEG", exif=exif.tobytes() if exif else b"")
    return buf.getvalue()


def test_metadata_absent_is_neutral_and_present_is_reported():
    none = image_metadata(jpeg(), CFG)
    assert [f["kind"] for f in none] == ["no_metadata"] and "nothing either way" in none[0]["text"]
    ex = Image.Exif()
    ex[271], ex[272], ex[305] = "Canon", "EOS", "Adobe Photoshop 25"
    kinds = {f["kind"]: f["text"] for f in image_metadata(jpeg(ex), CFG)}
    assert "Canon EOS" in kinds["device"] and "image editor" in kinds["software"]
