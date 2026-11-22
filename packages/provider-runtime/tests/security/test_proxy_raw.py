"""proxy_raw.py (round-2 review probe) as a permanent regression: raw client
tricks against the guard proxy must never reach the refused address."""

from __future__ import annotations

import http.server
import socket
import threading
import time

from arcus.provider_runtime import url_guard as ug


def _srv(hits, slow=False):
    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            hits.append((self.path, self.headers.get("Host")))
            time.sleep(0.5 if slow else 0)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def _recv(c, n=4000, t=2.0):
    c.settimeout(t)
    try:
        return c.recv(n)
    except OSError:
        return b""


def test_raw_proxy_tricks_never_reach_refused():
    internal_hits, public_hits = [], []
    si, sp = _srv(internal_hits), _srv(public_hits, slow=True)
    ip, pp = si.server_address[1], sp.server_address[1]

    def g(u):
        if f":{ip}" in u:
            raise PermissionError("internal")

    try:
        with ug.GuardProxy(g) as px:
            # 1) pipelined second request to the refused host, same client conn
            c = socket.create_connection(px.address)
            c.sendall(f"GET http://127.0.0.1:{pp}/a HTTP/1.1\r\nHost: 127.0.0.1:{pp}\r\n\r\n"
                      f"GET http://127.0.0.1:{ip}/secret HTTP/1.1\r\nHost: 127.0.0.1:{ip}\r\n\r\n"
                      .encode())
            time.sleep(1.5)
            assert b"Connection: close" in _recv(c)
            c.close()
            # 2) public absolute URI, internal Host header -> goes to public only
            c = socket.create_connection(px.address)
            c.sendall(f"GET http://127.0.0.1:{pp}/b HTTP/1.1\r\nHost: 127.0.0.1:{ip}\r\n\r\n".encode())
            _recv(c)
            c.close()
            # 3) CONNECT to the refused address
            c = socket.create_connection(px.address)
            c.sendall(f"CONNECT 127.0.0.1:{ip} HTTP/1.1\r\n\r\n".encode())
            assert _recv(c, 100).startswith(b"HTTP/1.1 403")
            c.close()
            # 4) CONNECT via the name 'localhost' (resolves to the refused address)
            c = socket.create_connection(px.address)
            c.sendall(f"CONNECT localhost:{ip} HTTP/1.1\r\n\r\n".encode())
            assert _recv(c, 100).startswith(b"HTTP/1.1 403")
            c.close()
            # 5) connection cap
            socks = [socket.create_connection(px.address) for _ in range(70)]
            time.sleep(0.5)
            assert px.peak_connections <= px.max_connections
            for s in socks:
                s.close()
            # 6) userinfo trick in the request target
            c = socket.create_connection(px.address)
            c.sendall(f"GET http://127.0.0.1:{pp}@127.0.0.1:{ip}/u HTTP/1.1\r\nHost: x\r\n\r\n".encode())
            _recv(c, t=1.0)
            c.close()
        time.sleep(0.5)
        assert internal_hits == [], internal_hits
        assert ("/a", f"127.0.0.1:{pp}") in public_hits
        assert all(path != "/secret" for path, _ in public_hits), public_hits
        assert not ug._LIVE_PROXIES
    finally:
        si.shutdown()
        sp.shutdown()
