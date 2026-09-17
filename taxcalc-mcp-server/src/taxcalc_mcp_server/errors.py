# taxcalc-mcp-server/src/taxcalc_mcp_server/errors.py
"""The single HTTP-status-to-:class:`McpError` mapping every tool raises through.

**Why one function and not one ``raise`` per call site.** The consumer of these errors is an LLM
client deciding what to do next, and the only thing it can branch on reliably is the numeric
code: a 4290 means "back off and retry", a 4030 means "stop, the credential is wrong, retrying
will not help". If each tool invented its own mapping, the same upstream 429 would surface as
4290 from one tool and 5030 from another, and the client's backoff would fire on half the rate
limits it hits. Centralising it makes the table below the contract, and
``tests/test_schemas.py`` asserts every row of it round-trips.

**Why the upstream body is truncated.** ``message`` is carried back to the model, and the
model's context window is a budget. A Spring validation failure can render several kilobytes of
field errors; the first 200 characters carry the cause, and the rest costs tokens on every retry
without changing the decision.

**What is deliberately not here.** No ``except Exception`` fallback that maps everything to
5030. A tool that raises an unmapped exception should surface as an internal error with a
traceback in the server's stderr log, because that is a bug in this server - not an upstream
condition the model can act on.
"""

from __future__ import annotations

from typing import Final

from mcp import McpError
from mcp.types import ErrorData

#: Longest upstream body echoed back to the model. See the module docstring.
MAX_MESSAGE_CHARS: Final[int] = 200

#: The error code raised when the RAG pipeline misses its deadline. Distinct from
#: :data:`DEFAULT_CODE` so the W7 D5 agent can tell "retrieval was too slow" (retry with a
#: smaller ``top_k``, or answer without grounding) from "the server broke" (do not retry).
RAG_TIMEOUT_CODE: Final[int] = 5040

#: Raised for any status with no explicit row below - 5xx, and anything unexpected.
DEFAULT_CODE: Final[int] = 5030

#: HTTP status to MCP error code. The dict IS the contract; ``tests/test_schemas.py`` asserts
#: every row survives a real round-trip through :func:`_map_http`.
#:
#: 401 and 403 deliberately share 4030. They differ in whether a credential was presented, which
#: matters to an operator reading the upstream's logs and not at all to the caller: neither is
#: fixable by retrying, and both mean "this JWT cannot do this".
STATUS_TO_CODE: Final[dict[int, int]] = {
    400: 4001,
    401: 4030,
    403: 4030,
    404: 4040,
    409: 4090,
    429: 4290,
}


def _map_http(status: int, body: str) -> McpError:
    """Translate an upstream HTTP response into the :class:`McpError` a tool raises.

    :param status: The upstream HTTP status code.
    :param body: The upstream response body. Truncated to :data:`MAX_MESSAGE_CHARS`.
    :returns: An :class:`McpError` carrying the mapped numeric code - returned rather than
        raised so the call site reads ``raise _map_http(...)`` and the raise stays visible in
        the tool's own control flow.
    """
    code = STATUS_TO_CODE.get(status, DEFAULT_CODE)
    return McpError(ErrorData(code=code, message=body[:MAX_MESSAGE_CHARS]))


def rag_timeout(message: str) -> McpError:
    """Build the error raised when retrieval misses :attr:`Settings.tool_timeout_rag_s`.

    Separate from :func:`_map_http` because no HTTP status is involved: the deadline is this
    server's own, enforced around an in-process call.

    :param message: Operator-facing detail, e.g. the deadline that was exceeded.
    :returns: An :class:`McpError` carrying :data:`RAG_TIMEOUT_CODE`.
    """
    return McpError(ErrorData(code=RAG_TIMEOUT_CODE, message=message[:MAX_MESSAGE_CHARS]))
