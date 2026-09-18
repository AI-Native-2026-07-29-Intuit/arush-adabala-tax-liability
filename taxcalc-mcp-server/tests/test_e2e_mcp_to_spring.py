# taxcalc-mcp-server/tests/test_e2e_mcp_to_spring.py
"""End-to-end: Postgres + the Spring order service + this MCP server, as three real processes.

**What this adds over the stdio smoke test, which already drives a subprocess.** The smoke test
supplies its own in-process stub upstream, so it proves this server's behaviour and nothing about
the agreement between this server and the Java one. Here the stub is replaced by
``taxcalc-orders`` running in a container against a real Postgres, which is the only place four
assumptions are actually checked: that ``GET /orders/{id}`` returns the field names
:class:`OrderView` expects, that the refund endpoint reads the ``Idempotency-Key`` header this
server sends, that a Java ``BigDecimal`` round-trips the decimal string this server writes, and
that a retry leaves exactly one row in the ledger. Each is a contract between two codebases in
two languages, and a contract tested from one side only is an assumption.

**The idempotency assertion is made against the database, not the response.** Matching
``refund_id`` values would also be produced by a service that issued two refunds and rendered
them identically. The test counts the rows.

**Why it is the merge-to-main tier.** Building the jar, building the image and waiting for a JVM
behind a Flyway migration costs a minute or two before the first assertion. Paid on every push
that is the cost that makes people stop pushing; paid on merge it is the cost of finding
integration drift before it reaches ``main``. The PR tier keeps the unit, schema, description and
100-call smoke suites, which catch everything except cross-service drift.

**Skips name their cause.** Without a Docker daemon, or without a JDK to build the jar, this
module skips with a message saying which. A skip that says only "skipped" is indistinguishable
from a test that ran and found nothing, and the merge-to-main CI tier treats any skip here as a
failure for exactly that reason.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

import httpx
import pytest

#: Repository root, from this file rather than the working directory, so the test behaves the
#: same whether pytest is invoked from the project or the repo root.
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

#: The order service's Gradle project and the artefact its build produces.
ORDERS_PROJECT: Final[Path] = REPO_ROOT / "taxcalc-orders"
ORDERS_JAR: Final[Path] = ORDERS_PROJECT / "build" / "libs" / "taxcalc-orders.jar"

#: The order seeded by ``V1__orders_and_refunds.sql``.
SEEDED_ORDER_ID: Final[str] = "ord-synth-9001"
SEEDED_TOTAL: Final[str] = "42.50"
TENANT: Final[str] = "tenant-a"

#: Hostname Postgres answers to on the shared Docker network. The order service resolves this
#: from inside its own container, where the host's published port does not exist.
PG_ALIAS: Final[str] = "orders-db"

#: How long the service gets to become healthy: a JVM starting behind a Flyway migration. Generous
#: on purpose - a tight deadline here produces a flaky test rather than a fast one.
BOOT_DEADLINE_S: Final[float] = 180.0

pytestmark = pytest.mark.e2e


def _blocker() -> str:
    """Return ``""`` when this module can run, else the specific reason it cannot.

    :returns: An empty string, or a human-readable cause for the skip.
    """
    try:
        docker = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=30, check=False
        )
    except FileNotFoundError:
        return "the docker CLI is not installed"
    except subprocess.TimeoutExpired:
        return "docker info timed out; the daemon is not responding"
    if docker.returncode != 0:
        return f"the docker daemon is unavailable: {docker.stderr.decode().strip()[:200]}"

    if not ORDERS_JAR.exists():
        built = _build_orders_jar()
        if built:
            return built
    return ""


def _build_orders_jar() -> str:
    """Build ``taxcalc-orders.jar`` with the repository's Gradle wrapper.

    Built on the host rather than inside the image: an in-image build would download Gradle and
    the entire dependency graph on every rebuild, through whatever proxy the builder sits behind,
    to produce an artefact the host makes in seconds from a warm cache.

    :returns: An empty string on success, or the reason the build failed.
    """
    gradlew = REPO_ROOT / "gradlew"
    if not gradlew.exists():
        return f"no Gradle wrapper at {gradlew}"
    result = subprocess.run(
        [str(gradlew), "-p", str(ORDERS_PROJECT), "bootJar"],
        capture_output=True,
        timeout=900,
        check=False,
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        tail = result.stderr.decode().strip()[-400:] or result.stdout.decode().strip()[-400:]
        return f"`gradlew -p taxcalc-orders bootJar` failed: {tail}"
    if not ORDERS_JAR.exists():
        return f"the build reported success but {ORDERS_JAR} does not exist"
    return ""


@pytest.fixture(scope="module", autouse=True)
def _require_docker_and_jar() -> None:
    """Skip the whole module, naming the cause, when it cannot run."""
    reason = _blocker()
    if reason:
        pytest.skip(f"E2E needs Docker and a built taxcalc-orders jar: {reason}",
                    allow_module_level=True)


@pytest.fixture(scope="module")
def network() -> Iterator[Any]:
    """A private Docker network so the order service can reach Postgres by name.

    The published host port is not usable from inside another container, so the two are joined on
    their own network and Postgres is given an alias.

    :yields: The network.
    """
    from testcontainers.core.network import Network

    with Network() as net:
        yield net


@pytest.fixture(scope="module")
def postgres(network: Any) -> Iterator[str]:
    """Run Postgres on the shared network and yield the JDBC URL the service should use.

    :param network: The shared network.
    :yields: A JDBC URL naming Postgres by its network alias.
    """
    from testcontainers.community.postgres import PostgresContainer

    container = PostgresContainer("postgres:16-alpine")
    container.with_network(network).with_network_aliases(PG_ALIAS)
    with container as pg:
        # Built from the alias rather than from get_connection_url(), which renders the host's
        # mapped port - correct for a client on the host, unreachable from a sibling container.
        yield (
            f"jdbc:postgresql://{PG_ALIAS}:5432/{pg.dbname}"
            f"?user={pg.username}&password={pg.password}"
        )


@pytest.fixture(scope="module")
def orders_svc(network: Any, postgres: str) -> Iterator[str]:
    """Build the order-service image, run it against ``postgres``, and yield its base URL.

    :param network: The shared network.
    :param postgres: The JDBC URL.
    :yields: The service's base URL on the host, once it reports healthy.
    """
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.image import DockerImage

    with DockerImage(path=str(ORDERS_PROJECT), tag="taxcalc-orders:e2e", clean_up=True) as image:
        container = (
            DockerContainer(str(image))
            .with_env("SPRING_DATASOURCE_URL", postgres)
            .with_exposed_ports(8080)
            .with_network(network)
        )
        container.start()
        try:
            base = (
                f"http://{container.get_container_host_ip()}:"
                f"{container.get_exposed_port(8080)}"
            )
            _await_health(base, container)
            yield base
        finally:
            container.stop()


def _await_health(base: str, container: Any) -> None:
    """Block until the service reports ``UP``, or fail with its logs.

    Polls actuator rather than sleeping a fixed interval: the readiness condition is "Flyway has
    finished and the datasource answers", and a TCP port that accepts before the schema exists
    would let the first request race the migration.

    :param base: The service's base URL.
    :param container: The running container, read for logs on failure.
    :raises pytest.fail.Exception: if the service does not become healthy in time.
    """
    deadline = time.time() + BOOT_DEADLINE_S
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base}/actuator/health", timeout=2.0)
            if r.status_code == 200 and r.json().get("status") == "UP":
                return
        except httpx.RequestError:
            pass
        time.sleep(1.0)
    logs = container.get_logs()[1].decode(errors="replace")[-2000:]
    pytest.fail(f"taxcalc-orders did not become healthy in {BOOT_DEADLINE_S}s. Logs:\n{logs}")


class _Rpc:
    """A minimal JSON-RPC client over the MCP server subprocess's pipes."""

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
        frame = json.dumps(
            {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        )
        self._proc.stdin.write(frame + "\n")
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
        # Not a pipe: stderr carries all the logging, and a pipe nobody drains fills its buffer
        # and deadlocks the server mid-test - which reads as a hang in the code under test.
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


def _ledger_rows(orders_svc: str, order_id: str, key: str) -> int:
    """Count refund rows the service recorded against one idempotency key.

    Asked over HTTP rather than by opening a second connection to Postgres, so the count comes
    from the same service and the same transaction boundary the refund was written through.

    :param orders_svc: The order service's base URL.
    :param order_id: The order.
    :param key: The idempotency key.
    :returns: How many refunds carry that key.
    """
    r = httpx.get(
        f"{orders_svc}/orders/{order_id}/refunds",
        headers={"Authorization": "Bearer test", "X-Tenant": TENANT},
        params={"idempotency_key": key},
        timeout=10.0,
    )
    r.raise_for_status()
    count: int = r.json()["count"]
    return count


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

    This is where the field-name agreement between :class:`OrderView` (Python, ``extra="forbid"``)
    and the Java record's ``@JsonProperty`` names is actually checked. A rename on either side
    fails here, which is the point of having the test at all.
    """
    payload = mcp_client.call(
        "orders.get_order", {"order_id": SEEDED_ORDER_ID, "tenant_id": TENANT}
    )
    assert payload == {
        "order_id": SEEDED_ORDER_ID,
        "tenant_id": TENANT,
        "total": SEEDED_TOTAL,
        "status": "paid",
    }
    assert isinstance(payload["total"], str), "money must cross the wire as a string"


def test_unknown_order_maps_to_the_not_found_code(mcp_client: _Rpc) -> None:
    """A real Spring 404 becomes 4040 by the time it reaches the client."""
    reply = mcp_client.request(
        "tools/call",
        {
            "name": "orders.get_order",
            "arguments": {"order_id": "ord-does-not-exist", "tenant_id": TENANT},
        },
    )
    assert reply["error"]["code"] == 4040, reply


def test_create_refund_is_idempotent_end_to_end(mcp_client: _Rpc, orders_svc: str) -> None:
    """Two calls with one key return one ``refund_id`` and leave one row in the ledger.

    The whole point of the idempotency design, verified against the service that owns the ledger
    and the unique index that enforces it - not against a stub written to agree with the
    assertion. The row count is the half that cannot be satisfied by a service which issued two
    refunds and rendered them identically.
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
    assert first["status"] == "refunded"
    assert _ledger_rows(orders_svc, SEEDED_ORDER_ID, key) == 1, "the ledger was debited twice"


def test_two_different_keys_are_two_refunds(orders_svc: str, mcp_client: _Rpc) -> None:
    """The guarantee is scoped to the key, not to the order.

    Without this, a service that simply refused every second refund would pass the idempotency
    test above while being badly broken - a customer owed two refunds would get one.
    """
    first_key, second_key = str(uuid4()), str(uuid4())
    base = {
        "order_id": SEEDED_ORDER_ID,
        "amount": "1.00",
        "reason": "partial",
        "tenant_id": TENANT,
    }
    first = mcp_client.call("orders.create_refund", {**base, "idempotency_key": first_key})
    second = mcp_client.call("orders.create_refund", {**base, "idempotency_key": second_key})

    assert first["refund_id"] != second["refund_id"]
    assert _ledger_rows(orders_svc, SEEDED_ORDER_ID, first_key) == 1
    assert _ledger_rows(orders_svc, SEEDED_ORDER_ID, second_key) == 1


def test_the_bearer_is_actually_forwarded(orders_svc: str) -> None:
    """The order service refuses a call with no credential, so forwarding it is load-bearing.

    Asserted directly against the service: if this returned 200, every other test in this file
    would still pass with the MCP server's ``Authorization`` header deleted, and the JWT
    pass-through would be untested while appearing to be covered.
    """
    r = httpx.get(
        f"{orders_svc}/orders/{SEEDED_ORDER_ID}",
        headers={"X-Tenant": TENANT},
        timeout=10.0,
    )
    assert r.status_code == 401, r.text
