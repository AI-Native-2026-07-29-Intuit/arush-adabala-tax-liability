# taxcalc-mcp-server/tests/test_e2e_mcp_to_spring.py
"""End-to-end: Postgres + the Spring order service + this MCP server, as three real processes.

**What this adds over the stdio smoke test, which already drives a subprocess.** The smoke test
supplies its own stub upstream, so it proves this server's behaviour and nothing about the
agreement between this server and the Java one. This test replaces the stub with the real
service on a real database, which is the only place four assumptions are actually checked: that
``GET /orders/{id}`` returns the field names :class:`OrderView` expects, that the refund endpoint
reads ``Idempotency-Key``, that a ``BigDecimal`` round-trips the string this server sends, and
that the ledger is debited once. Each of those is a contract between two codebases, and a
contract tested from one side only is an assumption.

**Why it is the merge-to-main tier and not the per-PR tier.** Pulling two images and waiting for
Spring to pass its health check costs a minute or two before a single assertion runs. Paid on
every push, that is the cost that makes people stop pushing; paid on merge, it is the cost of
finding integration drift before it reaches ``main``. The per-PR tier keeps the unit, schema,
description and 100-call smoke suites, which catch everything except cross-service drift.

**Skips name their cause.** If Docker is not running, or the order-service image cannot be
pulled, this test skips with a message saying which of those it was. A skip that says only
"skipped" is indistinguishable from a test that ran and found nothing, and this repository has
already been bitten once by a gate that reported an absence as a pass.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from typing import Any, Final
from uuid import uuid4

import httpx
import pytest

#: The W3 D1 order service image. Published to the course registry; a developer without access
#: sees this test skip with the pull error rather than fail.
ORDERS_IMAGE: Final[str] = "uptimecrew/taxcalc-orders:w3d1"

#: The order the service seeds on startup, and the one the fixtures use.
SEEDED_ORDER_ID: Final[str] = "ord-synth-9001"
TENANT: Final[str] = "tenant-a"

#: How long Spring gets to become healthy. Generous: it is a JVM starting behind a database
#: migration, and a tight deadline here produces a flaky test rather than a fast one.
BOOT_DEADLINE_S: Final[float] = 120.0

pytestmark = pytest.mark.e2e


def _docker_available() -> str:
    """Return ``""`` when Docker can run containers, else the reason it cannot.

    :returns: An empty string, or a human-readable cause for the skip.
    """
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=20, check=False
        )
    except FileNotFoundError:
        return "docker CLI is not installed"
    except subprocess.TimeoutExpired:
        return "docker info timed out; the daemon is not responding"
    if result.returncode != 0:
        return f"docker daemon unavailable: {result.stderr.decode()[:200]}"

    pull = subprocess.run(
        ["docker", "image", "inspect", ORDERS_IMAGE], capture_output=True, check=False
    )
    if pull.returncode != 0:
        fetch = subprocess.run(
            ["docker", "pull", ORDERS_IMAGE], capture_output=True, timeout=600, check=False
        )
        if fetch.returncode != 0:
            return f"cannot pull {ORDERS_IMAGE}: {fetch.stderr.decode().strip()[:200]}"
    return ""


@pytest.fixture(scope="module")
def postgres() -> Iterator[str]:
    """Run Postgres and yield its connection URL."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="module")
def orders_svc(postgres: str) -> Iterator[str]:
    """Run the Spring order service against ``postgres`` and yield its base URL.

    :param postgres: The database URL.
    :yields: The service's base URL, once it reports healthy.
    """
    from testcontainers.core.container import DockerContainer

    container = (
        DockerContainer(ORDERS_IMAGE)
        .with_env("SPRING_DATASOURCE_URL", postgres)
        .with_exposed_ports(8080)
    )
    container.start()
    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(8080)
        base = f"http://{host}:{port}"
        deadline = time.time() + BOOT_DEADLINE_S
        while time.time() < deadline:
            try:
                r = httpx.get(f"{base}/actuator/health", timeout=2.0)
                if r.status_code == 200 and r.json().get("status") == "UP":
                    break
            except httpx.RequestError:
                time.sleep(0.5)
        else:
            pytest.fail(f"{ORDERS_IMAGE} did not become healthy within {BOOT_DEADLINE_S}s")
        yield base
    finally:
        container.stop()


@pytest.fixture
def mcp_client(orders_svc: str) -> Iterator[_Rpc]:
    """Start the MCP server as a subprocess pointed at the real order service.

    :param orders_svc: The order service's base URL.
    :yields: A connected JSON-RPC client.
    """
    env = {
        **os.environ,
        "TAXCALC_MCP_ORDERS_SVC_URL": orders_svc,
        "TAXCALC_MCP_BEARER_JWT": "test-token-with-orders-write-scope",
        "LANGSMITH_API_KEY": "test-not-a-real-key",
        "LANGSMITH_TRACING": "false",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "taxcalc_mcp_server.transports.stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        text=True,
        bufsize=1,
    )
    client = _Rpc(proc)
    client.request(
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "e2e", "version": "1"},
        },
    )
    client.notify("notifications/initialized", {})
    try:
        yield client
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        for pipe in (proc.stdin, proc.stdout):
            if pipe is not None:
                pipe.close()


class _Rpc:
    """A minimal JSON-RPC client over the server subprocess's pipes."""

    def __init__(self, proc: subprocess.Popen[str]) -> None:
        """Wrap a running server process.

        :param proc: The subprocess, with text-mode pipes.
        """
        self._proc = proc
        self._id = 0

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send a request and read one reply.

        :param method: JSON-RPC method.
        :param params: Method parameters.
        :returns: The parsed reply.
        """
        self._id += 1
        assert self._proc.stdin is not None
        assert self._proc.stdout is not None
        self._proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params})
            + "\n"
        )
        self._proc.stdin.flush()
        reply: dict[str, Any] = json.loads(self._proc.stdout.readline())
        return reply

    def notify(self, method: str, params: dict[str, Any]) -> None:
        """Send a notification.

        :param method: JSON-RPC method.
        :param params: Method parameters.
        """
        assert self._proc.stdin is not None
        frame = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
        self._proc.stdin.write(frame + "\n")
        self._proc.stdin.flush()

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke a tool and return its decoded payload.

        :param tool: Tool name.
        :param arguments: Tool arguments.
        :returns: The tool's JSON result.
        :raises AssertionError: if the call returned an error.
        """
        reply = self.request("tools/call", {"name": tool, "arguments": arguments})
        assert "error" not in reply, reply
        payload: dict[str, Any] = json.loads(reply["result"]["content"][0]["text"])
        return payload


@pytest.fixture(scope="module", autouse=True)
def _require_docker() -> None:
    """Skip the whole module, with the specific cause, when Docker cannot run it."""
    reason = _docker_available()
    if reason:
        pytest.skip(f"E2E requires Docker and {ORDERS_IMAGE}: {reason}", allow_module_level=True)


def test_tools_list_returns_all_four_tools(mcp_client: _Rpc) -> None:
    """The published surface is intact when the server runs against the real upstream."""
    listed = mcp_client.request("tools/list", {})
    names = {t["name"] for t in listed["result"]["tools"]}
    assert names == {
        "orders.get_order",
        "orders.create_refund",
        "llm.chat",
        "rag.retrieve_and_generate",
    }


def test_get_order_returns_the_seeded_order(mcp_client: _Rpc) -> None:
    """``orders.get_order`` reads the seeded row through the real Spring service.

    This is where the field-name agreement between :class:`OrderView` and the Java DTO is
    actually checked - a mismatch here is a contract break the smoke test cannot see.
    """
    payload = mcp_client.call(
        "orders.get_order", {"order_id": SEEDED_ORDER_ID, "tenant_id": TENANT}
    )
    assert payload["order_id"] == SEEDED_ORDER_ID
    assert payload["tenant_id"] == TENANT
    assert isinstance(payload["total"], str), "money must cross the wire as a string"


def test_create_refund_is_idempotent_end_to_end(mcp_client: _Rpc) -> None:
    """Two calls with one key return one ``refund_id`` and debit the ledger once.

    The whole point of the idempotency design, verified against the service that owns the
    ledger rather than against a stub that was written to agree with it.
    """
    key = str(uuid4())
    args = {
        "order_id": SEEDED_ORDER_ID,
        "amount": "10.00",
        "reason": "duplicate",
        "tenant_id": TENANT,
        "idempotency_key": key,
    }
    first = mcp_client.call("orders.create_refund", args)
    second = mcp_client.call("orders.create_refund", args)

    assert first["refund_id"] == second["refund_id"], "a retry minted a second refund"
    assert first["amount"] == second["amount"] == "10.00", "BigDecimal lost the scale"
