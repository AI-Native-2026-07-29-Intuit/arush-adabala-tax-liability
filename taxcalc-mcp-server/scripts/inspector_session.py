# taxcalc-mcp-server/scripts/inspector_session.py
"""Drive the MCP Inspector against this server and print the transcript.

**Why this exists as a script rather than a remembered terminal session.** The W7 D4 deliverable
is exercised with ``npx @modelcontextprotocol/inspector``, and the Inspector's default mode is a
browser UI - so the evidence it produces is a screenshot, or a claim. This runs the Inspector's
``--cli`` mode instead, which speaks the same client library over the same stdio transport and
writes JSON to stdout, so the three confirmations the deliverable asks for become a transcript
anyone can regenerate and diff. The recorded run lives in ``PROMPT_JOURNAL.md``.

**Why it starts the stub upstream itself.** ``tools/call`` for ``orders.get_order`` has to reach
*something* that knows ``ord-synth-9001``. The alternatives are the real Spring service, which
wants Docker, Postgres and a Flyway migration before the Inspector can ask its first question
(``tests/test_e2e_mcp_to_spring.py`` does exactly that, on the merge-to-main tier), or
``tests.stub_orders``, which serves the same seeded order over the same HTTP contract in-process.
This uses the stub: what the Inspector run is evidence *for* is that the MCP surface works -
tools registered, resource readable, a call round-tripping - not that the order service is
correct, which is the E2E's job.

It is not part of the shipped package: the wheel contains ``src/taxcalc_mcp_server`` only, and
this reaches into ``tests/`` for the stub.

Run it with ``uv run python scripts/inspector_session.py``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
from http.server import HTTPServer
from pathlib import Path
from typing import Final

REPO: Final[Path] = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tests.stub_orders import StubState, _Handler  # noqa: E402 - after the sys.path insert

#: Pinned so the printed transcript names a stable URL rather than a different ephemeral port on
#: every run, which would make two recorded runs look different when nothing changed.
STUB_PORT: Final[int] = 8791

#: The Inspector, unpinned: ``npx -y`` resolves the published latest, which is what the
#: deliverable's command line does. The recorded transcript notes which version answered.
INSPECTOR: Final[list[str]] = ["npx", "-y", "@modelcontextprotocol/inspector", "--cli"]

#: How the Inspector is told to launch this server. The same ``mcpServers`` shape as
#: ``configs/claude_desktop_config.json`` - deliberately, because the Inspector's job here is to
#: prove the surface a desktop client would see, and a different launch path would prove a
#: different thing. ``uv run python -m`` rather than the ``uvx taxcalc-mcp-server`` the committed
#: Desktop config uses: ``uvx`` would install the *published* wheel, and what wants exercising is
#: this working tree.
#:
#: Written to a temp file per run and never committed. The Inspector reads a config as-is
#: (``--config`` is documented read-only), and the env below points at the local stub with a
#: throwaway token - values that would be wrong in git and dangerous if they were ever real.
SERVER_NAME: Final[str] = "taxcalc-mcp-server"


def _session_config(path: Path) -> None:
    """Write the Inspector session config that launches this server against the stub.

    :param path: Where to write it.
    """
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    SERVER_NAME: {
                        "command": "uv",
                        "args": ["run", "python", "-m", "taxcalc_mcp_server.transports.stdio"],
                        "env": {
                            "TAXCALC_MCP_ORDERS_SVC_URL": f"http://127.0.0.1:{STUB_PORT}",
                            "TAXCALC_MCP_BEARER_JWT": "inspector-bearer-token",
                            "LANGSMITH_TRACING": "false",
                            "LANGSMITH_API_KEY": "inspector-not-a-real-key",
                        },
                    }
                }
            },
            indent=2,
        )
    )


def _inspector(config: Path, *args: str) -> str:
    """Run one Inspector CLI invocation and return what it printed.

    Each invocation is a whole session: the Inspector spawns the server, handshakes, issues the
    one method, and exits. Four sessions rather than one is the Inspector's own model, and it
    strengthens the evidence - the resource is readable on a *fresh* process, not only on one
    already warmed by a ``tools/list``.

    stderr is folded into the returned text rather than discarded. It is where this server's
    structured logs go, so the transcript ends up showing the ``lifespan.start`` line next to the
    JSON-RPC result - which is the stdout/stderr split of the module docstring in
    :mod:`taxcalc_mcp_server.app`, visible rather than asserted.

    :param config: The session config to launch through.
    :param args: Inspector method arguments.
    :returns: The invocation's combined output.
    :raises SystemExit: if the Inspector exits non-zero, with its output attached.
    """
    proc = subprocess.run(  # noqa: S603 - fixed argv, no shell, no caller-supplied input
        [*INSPECTOR, "--config", str(config), "--server", SERVER_NAME, *args],
        capture_output=True,
        text=True,
        cwd=REPO,
        check=False,
    )
    combined = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        raise SystemExit(f"inspector failed ({proc.returncode}):\n{combined}")
    return combined


#: The four methods the deliverable's Inspector step is meant to confirm: the tool list contains
#: the first tool, the resource list contains the catalogue, a call against the synthetic order
#: returns a payload - plus the catalogue read, because a resource that lists and cannot be read
#: is not a resource an agent can fall back to.
METHODS: Final[list[tuple[str, list[str]]]] = [
    ("tools/list", ["--method", "tools/list"]),
    ("resources/list", ["--method", "resources/list"]),
    (
        "tools/call orders.get_order",
        [
            "--method",
            "tools/call",
            "--tool-name",
            "orders.get_order",
            "--tool-arg",
            "order_id=ord-synth-9001",
            "tenant_id=tenant-a",
        ],
    ),
    (
        "resources/read taxcalc://catalogue",
        ["--method", "resources/read", "--uri", "taxcalc://catalogue"],
    ),
]


def main() -> None:
    """Start the stub, run the Inspector calls the deliverable names, print the transcript."""
    StubState.reset()
    stub = HTTPServer(("127.0.0.1", STUB_PORT), _Handler)
    thread = threading.Thread(target=stub.serve_forever, daemon=True)
    thread.start()
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "inspector_session.json"
        _session_config(config)
        try:
            for label, args in METHODS:
                print(f"\n$ npx @modelcontextprotocol/inspector --cli --method {label}")
                print(_inspector(config, *args))
        finally:
            stub.shutdown()
            stub.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
