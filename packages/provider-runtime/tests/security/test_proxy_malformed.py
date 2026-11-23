"""Malformed proxied requests get a 400 and never kill a handler thread
(jivebug/arcus#16, gauntlet F-AR-009)."""

from __future__ import annotations

import socket
import threading

import pytest

from arcus.provider_runtime import url_guard as ug

MALFORMED = [
    b"GET http://exa]mpppple.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n",       # bad IPv6 bracket
    b"GET http://example.com:notaport/ HTTP/1.1\r\nHost: example.com\r\n\r\n",  # non-numeric port
    b"GET http:///path HTTP/1.1\r\nHost: x\r\n\r\n",                            # empty host
    b"CONNECT example.com:notaport HTTP/1.1\r\n\r\n",                           # CONNECT bad port
    b"CONNECT :443 HTTP/1.1\r\n\r\n",                                           # CONNECT empty host
    b"CONNECT [::1:443 HTTP/1.1\r\n\r\n",                                       # unbalanced bracket
    b"GARBAGE\r\n\r\n",                                                         # not a request line
]


@pytest.fixture
def thread_errors(monkeypatch):
    errors = []
    monkeypatch.setattr(threading, "excepthook", lambda args: errors.append(args.exc_value))
    return errors


@pytest.mark.parametrize("raw", MALFORMED, ids=lambda b: b.split(b"\r\n")[0].decode()[:40])
def test_malformed_request_gets_400_and_no_thread_dies(raw, thread_errors):
    def guard(url):
        if "blocked.test" in url:
            raise PermissionError("blocked")

    with ug.GuardProxy(guard) as px:
        c = socket.create_connection(px.address)
        c.sendall(raw)
        c.settimeout(3)
        try:
            resp = c.recv(200)
        except OSError:
            resp = b""
        c.close()
        # The proxy keeps serving afterwards (a refused CONNECT gets its 403).
        ok = socket.create_connection(px.address)
        ok.sendall(b"CONNECT blocked.test:443 HTTP/1.1\r\n\r\n")
        ok.settimeout(3)
        assert ok.recv(20).startswith(b"HTTP/1.1 403")
        ok.close()
    assert resp.startswith(b"HTTP/1.1 400"), resp
    assert b"connection: close" in resp.lower()
    assert thread_errors == [], thread_errors
