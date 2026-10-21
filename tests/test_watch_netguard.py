"""`compile/watch/netguard.py`: no request to a loopback, link-local, private or metadata address, and redirects only to the same host.
The redirect tests drive the real urllib handler chain with a fake HTTP layer; nothing opens a socket."""

import email.message
import io
import urllib.error
import urllib.request
import urllib.response

import pytest

from ace_playbook.compile.watch import netguard
from ace_playbook.compile.watch.fetch import fetch

from .watch_support import Opener, Resp

NOW = "2026-10-21T12:00:00Z"


def get(url, opener=None, **kw):
    return fetch(url, expect=("text/html",), store={}, now=NOW, opener=opener, **kw)


class Boom:
    """An opener that must never be called."""

    def __call__(self, *a, **k):
        raise AssertionError("a request was made")


@pytest.mark.parametrize("host", ["127.0.0.1", "127.1.2.3", "169.254.169.254", "10.0.0.5", "172.16.0.1", "192.168.1.1", "0.0.0.0", "224.0.0.1",
                                  "240.0.0.1", "100.64.0.1", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:169.254.169.254", "ff02::1",
                                  "[::1]", "fe80::1%en0"])
def test_unsafe_ip_literals_are_dropped_without_a_request_or_a_lookup(host, monkeypatch):
    monkeypatch.setattr(netguard, "RESOLVE", lambda *a, **k: pytest.fail("a literal must not be resolved"))
    shown = f"[{host.strip('[]')}]" if ":" in host else host
    out = get(f"http://{shown.replace('%', '%25')}/x", opener=Boom())
    assert out["dropped"] == "address", host


@pytest.mark.parametrize("host", ["93.184.216.34", "8.8.8.8", "2606:4700:4700::1111"])
def test_public_ip_literals_pass(host):
    shown = f"[{host}]" if ":" in host else host
    assert get(f"http://{shown}/x", opener=Opener({f"http://{shown}/x": Resp(b"<p>ok</p>", "text/html")}))["body"] == "ok"


def test_a_name_is_resolved_and_every_answer_must_be_safe(monkeypatch):
    answers = {"evil.example.test": ["93.184.216.34", "169.254.169.254"], "lo.example.test": ["127.0.0.1"], "six.example.test": ["::1"],
               "ok.example.test": ["93.184.216.34", "2606:4700:4700::1111"]}
    seen = []

    def resolve(host, port, *a, **k):
        seen.append(host)
        return [(2, 1, 6, "", (ip, 0)) for ip in answers[host]]

    monkeypatch.setattr(netguard, "RESOLVE", resolve)
    for name in ("evil.example.test", "lo.example.test", "six.example.test"):
        assert get(f"https://{name}/x", opener=Boom())["dropped"] == "address", name
    ok = get("https://ok.example.test/x", opener=Opener({"https://ok.example.test/x": Resp(b"<p>ok</p>", "text/html")}))
    assert ok["body"] == "ok" and seen == ["evil.example.test", "lo.example.test", "six.example.test", "ok.example.test"]


def test_a_name_that_does_not_resolve_or_resolves_to_nothing_is_refused(monkeypatch):
    def gone(host, port, *a, **k):
        raise OSError("no such host")

    monkeypatch.setattr(netguard, "RESOLVE", gone)
    assert get("https://nowhere.example.test/x", opener=Boom())["dropped"] == "address"
    monkeypatch.setattr(netguard, "RESOLVE", lambda *a, **k: [])
    assert get("https://empty.example.test/x", opener=Boom())["dropped"] == "address"
    assert get("http:///no-host", opener=Boom())["dropped"] == "address"


def test_an_integer_spelling_of_loopback_is_caught_by_the_resolver_answer(monkeypatch):
    monkeypatch.setattr(netguard, "RESOLVE", lambda host, port, *a, **k: [(2, 1, 6, "", ("127.0.0.1", 0))])
    assert get("http://2130706433/x", opener=Boom())["dropped"] == "address"


# ------------------------------------------------------------------ redirects, through the real handler chain
class Reply(urllib.response.addinfourl):
    def __init__(self, url, code, headers, body=b""):
        super().__init__(io.BytesIO(body), headers, url, code)
        self.msg = "x"


class Fake(urllib.request.BaseHandler):
    """Answers every http and https URL from `routes[url]` = (code, location or None, body) before any real handler can; records the URLs."""

    handler_order = 100

    def __init__(self, routes):
        self.routes, self.asked = routes, []

    def http_open(self, req):
        self.asked.append(req.full_url)
        code, location, body = self.routes[req.full_url]
        headers = email.message.Message()
        headers["Content-Type"] = "text/html"
        if location:
            headers["Location"] = location
        return Reply(req.full_url, code, headers, body)

    https_open = http_open


def chain(routes):
    fake = Fake(routes)
    opener = urllib.request.build_opener(netguard.SameHost, fake)
    return fake, lambda req, timeout=None: opener.open(req, timeout=timeout)


def test_a_redirect_to_the_same_host_is_followed():
    fake, op = chain({"http://example.test/a": (302, "http://example.test/b", b""), "http://example.test/b": (200, None, b"<p>moved</p>")})
    assert get("http://example.test/a", opener=op)["body"] == "moved" and fake.asked == ["http://example.test/a", "http://example.test/b"]


def test_a_relative_redirect_stays_on_the_host_and_case_does_not_matter():
    fake, op = chain({"http://Example.test/a": (301, "/b", b""), "http://Example.test/b": (200, None, b"<p>rel</p>")})
    assert get("http://Example.test/a", opener=op)["body"] == "rel"


@pytest.mark.parametrize("target", ["http://other.example.test/b", "http://127.0.0.1/b", "http://169.254.169.254/latest/meta-data/",
                                    "http://example.test.evil.test/b", "http://user" + "@other.test/b", "ftp://example.test/b", "file:///etc/passwd",
                                    "gopher://example.test/b"])
def test_a_redirect_to_another_host_or_scheme_is_dropped_and_never_requested(target):
    fake, op = chain({"http://example.test/a": (302, target, b"")})
    out = get("http://example.test/a", opener=op)
    assert out["dropped"] == "redirect" and fake.asked == ["http://example.test/a"]


def test_a_redirect_to_the_same_host_that_now_resolves_to_a_private_address_is_dropped(monkeypatch):
    asked = []

    def resolve(host, port, *a, **k):
        asked.append(host)
        return [(2, 1, 6, "", ("93.184.216.34" if len(asked) == 1 else "10.0.0.1", 0))]

    monkeypatch.setattr(netguard, "RESOLVE", resolve)
    fake, op = chain({"http://example.test/a": (302, "http://example.test/b", b""), "http://example.test/b": (200, None, b"<p>x</p>")})
    assert get("http://example.test/a", opener=op)["dropped"] == "redirect" and fake.asked == ["http://example.test/a"]


def test_https_is_never_downgraded_to_http_on_the_same_host():
    fake, op = chain({"https://example.test/a": (302, "http://example.test/b", b""), "http://example.test/b": (200, None, b"<p>x</p>")})
    assert get("https://example.test/a", opener=op)["dropped"] == "redirect" and fake.asked == ["https://example.test/a"]
    fake, op = chain({"http://example.test/a": (302, "https://example.test/b", b""), "https://example.test/b": (200, None, b"<p>up</p>")})
    assert get("http://example.test/a", opener=op)["body"] == "up"                       # an upgrade to https on the same host is fine


def test_more_than_three_hops_is_dropped_and_two_are_fine():
    routes = {f"http://example.test/{n}": (302, f"http://example.test/{n + 1}", b"") for n in range(10)}
    fake, op = chain(routes)
    assert get("http://example.test/0", opener=op)["dropped"] == "redirect" and len(fake.asked) <= 4
    ok = {"http://example.test/0": (302, "http://example.test/1", b""), "http://example.test/1": (302, "http://example.test/2", b""),
          "http://example.test/2": (200, None, b"<p>two hops</p>")}
    assert get("http://example.test/0", opener=chain(ok)[1])["body"] == "two hops"


def test_the_default_opener_installs_the_same_host_handler(monkeypatch):
    built = []
    real = urllib.request.build_opener
    monkeypatch.setattr(urllib.request, "build_opener", lambda *h: built.append(h) or real(*h))
    with pytest.raises(urllib.error.URLError):
        netguard.open_url(urllib.request.Request("xyz://host/x"), timeout=1)                   # an unknown scheme: no handler opens a socket
    assert built and netguard.SameHost in built[0]
