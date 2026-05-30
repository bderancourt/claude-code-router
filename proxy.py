#!/usr/bin/env python3
"""claude-code-router — minimal stdlib-only routing proxy for Claude Code.

Routes requests from Claude Code (with ANTHROPIC_BASE_URL=http://localhost:8082)
to one of two backends, chosen per request based on the JSON body's `model` field:

  - model starts with "claude-" -> https://api.anthropic.com
  - anything else               -> a local llama.cpp server (default 127.0.0.1:8080)

Security goals:
  - 127.0.0.1 bind only (no network exposure by default)
  - stdlib only, single file, auditable end-to-end
  - never writes credentials, request bodies, or response bodies to disk
  - forwards Authorization / x-api-key headers transparently (works with Pro OAuth)
  - logs only metadata: method, path, chosen target, model name
"""

import http.client
import http.server
import json
import os
import socketserver
import ssl
import sys

LISTEN_HOST = os.environ.get("CCR_LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("CCR_LISTEN_PORT", "8082"))

LOCAL_HOST = os.environ.get("CCR_LOCAL_HOST", "127.0.0.1")
LOCAL_PORT = int(os.environ.get("CCR_LOCAL_PORT", "8080"))

ANTHROPIC_HOST = os.environ.get("CCR_ANTHROPIC_HOST", "api.anthropic.com")
ANTHROPIC_PORT = int(os.environ.get("CCR_ANTHROPIC_PORT", "443"))

MAX_BODY = 64 * 1024 * 1024  # 64 MiB safety cap

# Hop-by-hop headers (RFC 7230 §6.1) — must not be forwarded by a proxy.
# We also strip Host and Content-Length because we always rewrite them.
HOP_BY_HOP = frozenset(h.lower() for h in (
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host",
    "content-length",
))


def pick_target(model):
    """Return (host, port, use_tls, label) for the given model name."""
    if model.startswith("claude-"):
        return ANTHROPIC_HOST, ANTHROPIC_PORT, True, "anthropic"
    return LOCAL_HOST, LOCAL_PORT, False, "local"


class RouterHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "claude-code-router/1.0"

    def log_request(self, code="-", size="-"):
        # Suppress the default per-request line; we log explicitly in _handle.
        pass

    def log_message(self, fmt, *args):
        sys.stderr.write("%s\n" % (fmt % args))

    def do_POST(self):
        self._handle(read_body=True)

    def do_GET(self):
        self._handle(read_body=False)

    def do_DELETE(self):
        self._handle(read_body=False)

    def _handle(self, read_body):
        body = b""
        if read_body:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length > MAX_BODY:
                self.send_error(413, "Payload too large")
                return
            if length:
                body = self.rfile.read(length)

        model = ""
        if body:
            try:
                parsed = json.loads(body)
                if isinstance(parsed, dict):
                    model = str(parsed.get("model", "") or "")
            except (ValueError, TypeError):
                pass

        host, port, use_tls, label = pick_target(model)

        # Build the forwarded header list (strip hop-by-hop, rewrite Host).
        fwd_headers = []
        for k, v in self.headers.items():
            if k.lower() in HOP_BY_HOP:
                continue
            fwd_headers.append((k, v))
        fwd_headers.append(("Host", host))
        if body:
            fwd_headers.append(("Content-Length", str(len(body))))

        sys.stderr.write(
            "[%s] %s %s model=%s\n" % (label, self.command, self.path, model or "-")
        )

        conn = None
        try:
            if use_tls:
                ctx = ssl.create_default_context()
                conn = http.client.HTTPSConnection(host, port, context=ctx, timeout=600)
            else:
                conn = http.client.HTTPConnection(host, port, timeout=600)

            try:
                conn.putrequest(
                    self.command, self.path,
                    skip_host=True, skip_accept_encoding=True,
                )
                for k, v in fwd_headers:
                    conn.putheader(k, v)
                conn.endheaders()
                if body:
                    conn.send(body)
                resp = conn.getresponse()
            except (ConnectionError, OSError) as e:
                self.send_error(502, "Upstream connection failed: %s" % e)
                return

            self.send_response(resp.status, resp.reason)
            for k, v in resp.getheaders():
                if k.lower() in HOP_BY_HOP:
                    continue
                self.send_header(k, v)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                self.wfile.write(b"%x\r\n" % len(chunk))
                self.wfile.write(chunk)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # client disconnected mid-stream — normal for SSE
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    server = ThreadedServer((LISTEN_HOST, LISTEN_PORT), RouterHandler)
    sys.stderr.write(
        "claude-code-router listening on %s:%d\n"
        "  model 'claude-*' -> https://%s:%d\n"
        "  other models     -> http://%s:%d\n"
        % (LISTEN_HOST, LISTEN_PORT, ANTHROPIC_HOST, ANTHROPIC_PORT, LOCAL_HOST, LOCAL_PORT)
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\nshutting down\n")
        server.shutdown()


if __name__ == "__main__":
    main()
