"""Tests for url_fetch_service — the SSRF guard is the priority surface.

All tests are offline: real DNS is monkeypatched and HTTP is served by
httpx.MockTransport, so nothing leaves the machine.
"""
import socket

import httpx
import pytest

from app.services import url_fetch_service as ufs
from app.services.url_fetch_service import ReadUrlError


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _fake_getaddrinfo(mapping):
    """Return a getaddrinfo that maps hostnames -> IPs. Unmapped names are
    assumed to already be IP literals (so redirect-to-10.0.0.1 resolves to
    itself and gets blocked)."""
    def fake(host, port, *args, **kwargs):
        ip = mapping.get(host, host)
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port or 0))]
    return fake


# --------------------------------------------------------------------------
# _ip_is_blocked — pure, no network
# --------------------------------------------------------------------------

@pytest.mark.parametrize("ip", [
    "127.0.0.1", "::1", "10.0.0.1", "172.16.0.1", "172.31.255.255",
    "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0",
    "224.0.0.1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "not-an-ip",
])
def test_ip_is_blocked_rejects_internal(ip):
    assert ufs._ip_is_blocked(ip) is True


@pytest.mark.parametrize("ip", [
    "93.184.216.34",   # example.com
    "8.8.8.8",
    "2606:2800:220:1:248:1893:25c8:1946",  # public IPv6
])
def test_ip_is_blocked_allows_public(ip):
    assert ufs._ip_is_blocked(ip) is False


# --------------------------------------------------------------------------
# validate_url — pre-DNS checks (no network needed)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/x",
    "data:text/html,<h1>hi</h1>",
    "javascript:alert(1)",
    "gopher://example.com",
])
def test_validate_rejects_bad_schemes(url):
    with pytest.raises(ReadUrlError):
        ufs.validate_url(url)


def test_validate_rejects_embedded_credentials():
    with pytest.raises(ReadUrlError):
        ufs.validate_url("http://user:pass@example.com/")


def test_validate_rejects_localhost():
    with pytest.raises(ReadUrlError):
        ufs.validate_url("http://localhost/admin")


def test_validate_rejects_nonstandard_port():
    with pytest.raises(ReadUrlError):
        ufs.validate_url("https://example.com:8443/")


# --------------------------------------------------------------------------
# validate_url — DNS-based SSRF (monkeypatched resolution)
# --------------------------------------------------------------------------

def test_validate_blocks_host_resolving_to_private_ip(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo",
                        _fake_getaddrinfo({"evil.example": "127.0.0.1"}))
    with pytest.raises(ReadUrlError):
        ufs.validate_url("http://evil.example/")


def test_validate_allows_host_resolving_to_public_ip(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo",
                        _fake_getaddrinfo({"good.example": "93.184.216.34"}))
    assert ufs.validate_url("http://good.example/page") == "http://good.example/page"


# --------------------------------------------------------------------------
# fetch_and_extract — MockTransport (no real network)
# --------------------------------------------------------------------------

def _public_dns(monkeypatch, *hosts):
    monkeypatch.setattr(socket, "getaddrinfo",
                        _fake_getaddrinfo({h: "93.184.216.34" for h in hosts}))


def test_fetch_extracts_html(monkeypatch):
    _public_dns(monkeypatch, "good.example")
    html = (b"<html><head><title>Benefits</title></head><body>"
            b"<script>var x=1;</script><nav>menu</nav>"
            b"<main><p>Gold tier costs $500 a year.</p></main></body></html>")

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=html)

    out = ufs.fetch_and_extract("http://good.example/benefits",
                                _transport=httpx.MockTransport(handler))
    assert out["title"] == "Benefits"
    assert "Gold tier costs $500 a year." in out["text"]
    assert "var x=1" not in out["text"]   # script stripped
    assert out["chars"] > 0


def test_fetch_rejects_non_html_content_type(monkeypatch):
    _public_dns(monkeypatch, "good.example")

    def handler(request):
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.4")

    with pytest.raises(ReadUrlError):
        ufs.fetch_and_extract("http://good.example/file.pdf",
                              _transport=httpx.MockTransport(handler))


def test_fetch_enforces_size_cap(monkeypatch):
    _public_dns(monkeypatch, "good.example")
    monkeypatch.setattr(ufs, "MAX_BYTES", 50)
    big = b"<html><body>" + (b"x" * 500) + b"</body></html>"

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, content=big)

    with pytest.raises(ReadUrlError):
        ufs.fetch_and_extract("http://good.example/big",
                              _transport=httpx.MockTransport(handler))


def test_fetch_blocks_redirect_to_internal(monkeypatch):
    # good.example public; the redirect target 10.0.0.1 is a literal internal IP.
    monkeypatch.setattr(socket, "getaddrinfo",
                        _fake_getaddrinfo({"good.example": "93.184.216.34"}))

    def handler(request):
        return httpx.Response(302, headers={"location": "http://10.0.0.1/secret"})

    with pytest.raises(ReadUrlError):
        ufs.fetch_and_extract("http://good.example/start",
                              _transport=httpx.MockTransport(handler))


def test_fetch_caps_redirect_chain(monkeypatch):
    _public_dns(monkeypatch, "r.example")

    def handler(request):
        return httpx.Response(302, headers={"location": "http://r.example/next"})

    with pytest.raises(ReadUrlError):
        ufs.fetch_and_extract("http://r.example/start",
                              _transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------
# extraction layering
# --------------------------------------------------------------------------

def test_extract_main_text_strips_boilerplate():
    html = ("<html><head><title>T</title></head><body>"
            "<script>bad()</script><style>x{}</style>"
            "<article><p>Real content here.</p></article></body></html>")
    title, text = ufs.extract_main_text(html, "http://x.example/")
    assert title == "T"
    assert "Real content here." in text
    assert "bad()" not in text


def test_extract_empty_html_is_graceful():
    title, text = ufs.extract_main_text("", "http://x.example/")
    assert title == ""
    assert text == ""
