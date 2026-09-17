# taxcalc-mcp-server/tests/stub_orders.py
"""A minimal stand-in for the W3 D1 order service, used by the stdio smoke test.

**Why a real HTTP server and not a mocked transport.** The smoke test drives the MCP server as a
*subprocess*, exactly as Claude Desktop does. A subprocess cannot be handed an in-process mock;
the only way to give it an upstream is to give it a URL. That constraint is a feature: what gets
exercised is the real httpx client, the real headers, the real JSON encoding of a Decimal, and
the real response parsing - all the code between the tool handler and the wire that an in-process
mock quietly skips.

**It implements idempotency for real, in the smallest way that is still true.** A refund is
recorded against its ``Idempotency-Key`` and a repeat of that key returns the stored outcome
without appending to the ledger. That makes the double-debit assertion a genuine test of this
server's behaviour: if the MCP tool ever stopped forwarding the key, the stub would mint a
second refund id and the test would fail. A stub that always returned the same id would pass
that test no matter what the tool did.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, ClassVar, Final

#: The one order the stub knows about, matching the fixtures and the E2E.
SEEDED_ORDER: Final[dict[str, Any]] = {
    "order_id": "ord-synth-9001",
    "tenant_id": "tenant-a",
    "total": "42.50",
    "status": "paid",
}


class StubState:
    """Refund ledger and idempotency index, shared across handler instances.

    Class-level rather than per-request because :class:`~http.server.BaseHTTPRequestHandler` is
    instantiated once per connection; state on the instance would reset between calls and the
    idempotency test would pass for the wrong reason.
    """

    #: ``Idempotency-Key`` to the refund payload returned for it.
    by_key: ClassVar[dict[str, dict[str, Any]]] = {}
    #: Every refund actually issued. Its length is the double-debit assertion.
    ledger: ClassVar[list[dict[str, Any]]] = []

    @classmethod
    def reset(cls) -> None:
        """Clear both, so one test's refunds do not leak into another's ledger count."""
        cls.by_key = {}
        cls.ledger = []


class _Handler(BaseHTTPRequestHandler):
    """Serves ``GET /orders/{id}`` and ``POST /orders/{id}/refunds``."""

    def log_message(self, format: str, *args: Any) -> None:
        """Silence the default stderr access log, which would drown the test output."""

    def _send(self, status: int, body: dict[str, Any]) -> None:
        """Write a JSON response.

        :param status: HTTP status.
        :param body: Response payload.
        """
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        """Return the seeded order, or 404."""
        if self.path == f"/orders/{SEEDED_ORDER['order_id']}":
            self._send(200, SEEDED_ORDER)
            return
        self._send(404, {"error": "order not found"})

    def do_POST(self) -> None:
        """Issue a refund, honouring the idempotency key."""
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        key = self.headers.get("Idempotency-Key", "")

        if not key:
            # The tool is required to send it; a stub that tolerated its absence would let a
            # regression through as a pass.
            self._send(400, {"error": "Idempotency-Key header is required"})
            return

        if key in StubState.by_key:
            self._send(200, StubState.by_key[key])
            return

        refund = {
            "order_id": payload.get("orderId", ""),
            "refund_id": f"rfnd-{len(StubState.ledger) + 1:04d}",
            # Echoed back as the exact string received: the point of the money assertion is that
            # "10.00" survives the whole round trip, scale included.
            "amount": payload.get("amount", ""),
            "reason": payload.get("reason", ""),
            "status": "refunded",
        }
        StubState.by_key[key] = refund
        StubState.ledger.append(refund)
        self._send(200, refund)


def start_stub() -> tuple[str, HTTPServer, threading.Thread]:
    """Start the stub on an ephemeral port.

    Port 0 rather than a fixed one so parallel test runs - and a developer who happens to have
    something on 8080 - do not collide.

    :returns: The base URL, the server (pass it to :func:`stop_stub`), and its thread.
    """
    StubState.reset()
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # `server_address` is typed as accepting bytes for the AF_UNIX case, so it is read off the
    # socket and narrowed explicitly rather than interpolated as-is - which would render a
    # bytes host as "b'127.0.0.1'" and produce a URL nothing can connect to.
    host, port = server.socket.getsockname()[:2]
    return f"http://{host!s}:{port:d}", server, thread


def stop_stub(server: HTTPServer, thread: threading.Thread) -> None:
    """Stop the stub and close its listening socket.

    ``shutdown()`` alone only stops the ``serve_forever`` loop - the socket stays open, and under
    this project's ``filterwarnings = error`` the resulting ResourceWarning is raised at some
    arbitrary later point in the session and attributed to whichever test is running then. A
    harness leak that reads as a product failure is worth two extra lines to avoid.

    :param server: The server returned by :func:`start_stub`.
    :param thread: Its serving thread.
    """
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
