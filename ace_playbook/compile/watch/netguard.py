"""Where the fetcher may connect (SSRF guard): never to a loopback, link-local, private, multicast, reserved or unspecified address, and a
redirect is followed only to the SAME host, scheme and port, at most MAX_HOPS times.

`blocked_address(host)` checks an IP literal with `ipaddress` and resolves a name with `socket.getaddrinfo`, refusing when ANY answer is
unsafe or the name does not resolve (fail closed). `resolve_failed(host)` tells the second case from the first, so `scan` can wait once for DNS.
`open_url` is the default opener of `fetch`: `urllib.request` with `SameHost` installed.
The DNS answer can differ between this check and the connect (rebinding); the redirect rule and the address check bound the exposure but
do not remove that race. Network-using, so off the hook path like the rest of the package.
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.request

from . import urlsafe

MAX_HOPS = 3
_PORTS = {"http": 80, "https": 443}
RESOLVE = socket.getaddrinfo                      # looked up at call time so tests can replace it


class RedirectRefused(Exception):
    """A redirect to another host, another scheme or an unsafe address."""


def _unsafe(ip: ipaddress._BaseAddress) -> bool:
    if ip.version == 6 and ip.ipv4_mapped is not None:
        return _unsafe(ip.ipv4_mapped)
    return (ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_multicast or ip.is_reserved or ip.is_unspecified
            or not ip.is_global)


def blocked_address(host: str | None) -> bool:
    """True when `host` (a name or an IP literal) is, or resolves to, an address the watcher must not connect to."""
    if not host:
        return True
    try:
        return _unsafe(ipaddress.ip_address(host.strip("[]").split("%")[0]))
    except ValueError:
        pass
    try:
        answers = {a[4][0] for a in RESOLVE(host, None)}
        return not answers or any(_unsafe(ipaddress.ip_address(str(a).split("%")[0])) for a in answers)
    except (OSError, ValueError, UnicodeError):
        return True


def resolve_failed(host: str | None) -> bool:
    """True only when `host` is a name that did not resolve: `RESOLVE` raised OSError, ValueError or UnicodeError, or gave no answer (a
    network that is not up yet). An unsafe answer, an IP literal and an empty host are False. One more lookup; `blocked_address` is unchanged."""
    if not host:
        return False
    try:
        ipaddress.ip_address(host.strip("[]").split("%")[0])
        return False
    except ValueError:
        pass
    try:
        return not RESOLVE(host, None)
    except (OSError, ValueError, UnicodeError):
        return True


class SameHost(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_HOPS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urlsafe.split(req.full_url), urlsafe.split(newurl)
        if old is None or new is None:
            raise RedirectRefused("url")                                            # never a bare ValueError out of the handler
        try:
            same = (new.scheme == old.scheme and new.scheme in ("http", "https") and new.hostname and old.hostname and not new.username
                    and new.hostname.lower() == old.hostname.lower() and (new.port or _PORTS[new.scheme]) == (old.port or _PORTS[old.scheme]))
        except ValueError:                                                      # a port that is not a number
            same = False
        if not same or blocked_address(new.hostname):
            raise RedirectRefused(new.scheme)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(request, timeout=None):
    """Open `request` with the same-host redirect rule (the default opener of `fetch`)."""
    return urllib.request.build_opener(SameHost).open(request, timeout=timeout)
