# taxcalc-mcp-server/src/taxcalc_mcp_server/scripts/healthcheck.py
"""Container health probe for the SSE transport.

**Why it probes ``/sse`` and not a dedicated ``/health`` route.** ``/sse`` is the endpoint the
W7 D5 agent actually connects to, so probing it exercises the path that matters - uvicorn, the
bearer middleware, the session manager, the tool registry. A separate health route returns 200
from a process whose transport never started, which is precisely the failure a health check is
supposed to catch.

**Why 401 is a PASS.** The probe carries no credential, and the bearer middleware refuses
anything without one. That refusal is proof of two things at once: the server is listening, and
the middleware is in front of it. Treating 401 as unhealthy would restart a correctly-secured
container in a loop. Requiring a 2xx would mean baking a real JWT into the image to satisfy a
health check, which is a worse trade than any it buys.

A 200 is also accepted, so that disabling the middleware for a local experiment does not turn
the health check into the thing that breaks.
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request
from typing import Final

#: Statuses that mean "the transport is serving". See the module docstring for why 401 is here.
HEALTHY_STATUSES: Final[frozenset[int]] = frozenset({200, 401})

#: Probe deadline. Short: a health check that waits longer than the interval between checks
#: stacks up rather than reporting.
TIMEOUT_S: Final[float] = 2.0


def probe(url: str) -> int:
    """Return a process exit status for ``url``.

    :param url: The SSE endpoint to probe.
    :returns: ``0`` when the transport is serving, ``1`` otherwise.
    """
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_S) as response:  # noqa: S310 - fixed http
            return 0 if response.status in HEALTHY_STATUSES else 1
    except urllib.error.HTTPError as exc:
        return 0 if exc.code in HEALTHY_STATUSES else 1
    except OSError:
        # Connection refused, DNS failure, timeout - the process is not serving.
        return 1


def main(argv: list[str] | None = None) -> int:
    """Probe the local SSE endpoint.

    :param argv: Optional ``[url]``; defaults to the container's own endpoint.
    :returns: Process exit status.
    """
    args = argv if argv is not None else sys.argv[1:]
    url = args[0] if args else "http://127.0.0.1:8080/sse"
    return probe(url)


if __name__ == "__main__":
    sys.exit(main())
