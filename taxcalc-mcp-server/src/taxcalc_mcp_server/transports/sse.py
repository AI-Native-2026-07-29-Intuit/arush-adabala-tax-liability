# taxcalc-mcp-server/src/taxcalc_mcp_server/transports/sse.py
"""HTTP+SSE entry point for the W7 D5 agent and any remote MCP client.

Same handlers as :mod:`taxcalc_mcp_server.transports.stdio`, different wire. What is genuinely
different here is *who is calling*: stdio serves exactly one caller, the desktop client that
launched the process, and its credential arrives in the launcher's environment. This transport
serves many callers over a network, each with their own JWT, so identity becomes per-request.

**Where the bearer is captured, and why that is the SSE handshake and not the POST.**
The SSE transport is two HTTP endpoints. ``GET /sse`` opens the event stream, and *the whole MCP
session runs inside that request's coroutine* - the server loop, and therefore every tool call,
executes in a task created from that request's context. ``POST /messages/`` only drops a frame
into a queue that the session task drains. An ``asyncio`` task inherits a copy of the context
active when it was created, so a :class:`~contextvars.ContextVar` set while handling the GET is
visible to every tool call for the life of that session, and one set while handling a POST is
not. Capturing at the handshake is the only placement that works, and it is also the correct
one: the credential belongs to the connection.

The POSTs are still checked. Not to capture anything, but so that a frame arriving on a session
whose credential has since been withdrawn is refused at the edge rather than executed.

**Why local validation is off by default.** The Java services validate this same token
authoritatively - they own the data and the scopes. A second validator here is defence in depth,
and defence in depth configured with the wrong issuer is not defence, it is an outage: it
rejects tokens the real validator accepts, and the failure looks like a broken service rather
than a misconfigured one. So :attr:`Settings.jwks_url` defaults to empty and validation is opt-in;
with it unset this transport forwards the caller's token and lets the owner of the data decide.

**Why rejection is an HTTP status carrying an MCP code.** At the handshake there is no JSON-RPC
session yet, so there is no frame to put an :class:`McpError` in - the honest answer at that
layer is ``401``. The body carries ``{"code": 4030, ...}`` anyway so that a client which reads
it branches on the same number it would have got from a tool, rather than on two different
vocabularies depending on where the refusal happened.
"""

from __future__ import annotations

from typing import Any, Final

import jwt
import uvicorn
from jwt import PyJWKClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from taxcalc_mcp_server.app import enforce_strict_tool_schemas, log, mcp
from taxcalc_mcp_server.errors import STATUS_TO_CODE
from taxcalc_mcp_server.settings import Settings
from taxcalc_mcp_server.tenancy import parse_bearer, set_request_identity
from taxcalc_mcp_server.tools import _resources, llm, orders, rag  # noqa: F401 - registration

#: Code returned when the bearer is missing, unparseable, or carries the wrong audience. The same
#: number :func:`taxcalc_mcp_server.errors._map_http` produces for an upstream 401/403, so a
#: caller has one meaning for "this credential cannot do this" wherever it is refused.
UNAUTHORIZED_CODE: Final[int] = STATUS_TO_CODE[401]

#: Signing algorithms accepted when local validation is enabled. An explicit allow-list, because
#: `PyJWT` will otherwise honour the token's own `alg` header - and a token that nominates `none`
#: validates against no key at all. Naming the algorithms here is what makes the signature check
#: a check.
ALLOWED_ALGORITHMS: Final[list[str]] = ["RS256", "RS512", "ES256"]


class BearerMiddleware:
    """Capture - and optionally validate - the caller's bearer on every request.

    Written as a raw ASGI middleware rather than a Starlette ``BaseHTTPMiddleware`` on purpose.
    ``BaseHTTPMiddleware`` runs the downstream app in a *separate* task, which breaks exactly the
    context-propagation this middleware exists to achieve: the ``ContextVar`` would be set in one
    task and read in another, and every tool call would see an empty token. A plain ASGI callable
    awaits the downstream app inline, in the same context.
    """

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        """Wrap ``app``.

        :param app: The downstream ASGI application - the SSE app FastMCP builds.
        :param settings: Validated configuration; read once, at construction.
        """
        self._app = app
        self._settings = settings
        # One JWKS client for the process, because it caches signing keys. Constructed per
        # request it would re-fetch the key set on every call, turning the identity provider into
        # a hard dependency of every tool invocation and a latency floor under all of them.
        self._jwks: PyJWKClient | None = (
            PyJWKClient(settings.jwks_url) if settings.jwks_url else None
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Bind the caller's identity to this request's context, or refuse it.

        :param scope: ASGI scope.
        :param receive: ASGI receive callable.
        :param send: ASGI send callable.
        """
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request = Request(scope)
        token = parse_bearer(request.headers.get("Authorization"))
        if not token:
            await self._refuse(scope, receive, send, "missing bearer token")
            return

        tenant = ""
        if self._jwks is not None:
            try:
                tenant = self._validate(token)
            except jwt.PyJWTError as exc:
                # The exception's own text is not echoed to the caller: PyJWT messages can name
                # the expected audience and issuer, which tells an attacker what to forge next.
                # The operator gets the detail on stderr; the caller gets the verdict.
                log.info("sse.auth.rejected", reason=type(exc).__name__)
                await self._refuse(scope, receive, send, "bearer token rejected")
                return

        set_request_identity(token, tenant)
        await self._app(scope, receive, send)

    def _validate(self, token: str) -> str:
        """Verify the token's signature, audience and expiry; return its tenant claim.

        :param token: The raw bearer.
        :returns: The ``tenant_id`` claim, or ``""`` when the token carries none.
        :raises jwt.PyJWTError: on any validation failure.
        """
        assert self._jwks is not None  # noqa: S101 - guarded by the caller
        key = self._jwks.get_signing_key_from_jwt(token).key
        claims: dict[str, Any] = jwt.decode(
            token,
            key,
            algorithms=ALLOWED_ALGORITHMS,
            audience=self._settings.jwt_audience,
            # Explicit rather than relying on defaults: an expired token that validates is the
            # same as no expiry at all, and that is the property most often lost in a refactor.
            options={"require": ["exp"], "verify_exp": True, "verify_aud": True},
        )
        return str(claims.get("tenant_id", ""))

    @staticmethod
    async def _refuse(scope: Scope, receive: Receive, send: Send, message: str) -> None:
        """Send a 401 whose body carries the MCP error code. See the module docstring.

        :param scope: ASGI scope.
        :param receive: ASGI receive callable.
        :param send: ASGI send callable.
        :param message: Caller-facing reason, deliberately non-specific.
        """
        response = JSONResponse({"code": UNAUTHORIZED_CODE, "message": message}, status_code=401)
        await response(scope, receive, send)


def build_app(settings: Settings | None = None) -> Starlette:
    """Assemble the SSE application with the bearer middleware in front of it.

    Separated from :func:`main` so a test can drive the assembled app without binding a port.

    :param settings: Configuration; read from the environment when omitted.
    :returns: The ASGI application.
    :raises RuntimeError: if no tools were registered - the same startup assertion the stdio
        entry point makes, for the same reason.
    """
    s = settings or Settings()
    registered = enforce_strict_tool_schemas(mcp)
    if registered == 0:
        raise RuntimeError(
            "no MCP tools registered: the tool modules did not import, so this server would "
            "publish an empty tools/list"
        )
    app = mcp.sse_app()
    app.add_middleware(BearerMiddleware, settings=s)
    log.info(
        "transport.start",
        transport="sse",
        tools=registered,
        jwks_validation=bool(s.jwks_url),
    )
    return app


def main() -> None:
    """Serve MCP over HTTP+SSE until the process is stopped."""
    s = Settings()
    uvicorn.run(build_app(s), host=s.host, port=s.port, log_config=None)


if __name__ == "__main__":
    main()
