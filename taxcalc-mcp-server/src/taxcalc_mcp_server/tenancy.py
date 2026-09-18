# taxcalc-mcp-server/src/taxcalc_mcp_server/tenancy.py
"""Per-request caller identity: the bearer JWT this server forwards, and the tenant it carries.

**Why a ``ContextVar`` and not a tool parameter.** The bearer token is a property of the
*connection*, not of the call. On the SSE transport one process serves many callers, each with
their own JWT arriving on their own handshake; threading it through every tool signature would
put a credential in the published JSON Schema, where a model could read it, invent one, or echo
it back in an answer. A ``ContextVar`` is the right shape for the same reason a thread-local is
in a servlet container: asyncio tasks inherit the context they were spawned in, so a value set
per request is visible to everything that request calls and invisible to everything else.

**Why the stdio path is different.** Under stdio there is exactly one caller - the desktop
client that launched this process - and its token arrives in the launcher's environment. So the
settings value is the fallback, and the per-request variable wins when set. That ordering is
what lets the same handler module serve both transports with no branch in it.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Final

#: The bearer JWT for the in-flight request, set by the SSE transport's middleware. Empty means
#: "no per-request token" and the configured :attr:`Settings.bearer_jwt` is used instead.
_REQUEST_JWT: Final[ContextVar[str]] = ContextVar("taxcalc_mcp_request_jwt", default="")

#: The ``tenant_id`` claim extracted from a validated SSE bearer. Advisory only - it is used to
#: cross-check the ``tenant_id`` a tool was *asked* for, never as a substitute for it, because
#: the authoritative tenant check lives in the Java services that own the data.
_REQUEST_TENANT: Final[ContextVar[str]] = ContextVar("taxcalc_mcp_request_tenant", default="")


def set_request_identity(jwt_token: str, tenant_id: str = "") -> None:
    """Bind the caller's credential and tenant to the current async context.

    :param jwt_token: The raw bearer token, without the ``Bearer `` prefix.
    :param tenant_id: The ``tenant_id`` claim, if the token was validated locally.
    """
    _REQUEST_JWT.set(jwt_token)
    _REQUEST_TENANT.set(tenant_id)


def request_tenant() -> str:
    """Return the tenant claimed by the validated bearer, or ``""`` when unknown.

    :returns: The ``tenant_id`` claim, or the empty string on the stdio transport and whenever
        local JWKS validation is disabled.
    """
    return _REQUEST_TENANT.get()


def cross_tenant_claim(requested: str) -> str:
    """Return the bearer's tenant claim when it disagrees with ``requested``, else ``""``.

    This is the cross-check the module docstring promises, and the *only* reader of
    :data:`_REQUEST_TENANT` outside the transport that sets it. It is phrased as a query rather
    than as an assertion so the one caller -
    :func:`taxcalc_mcp_server.observability.observe` - both logs the disagreement and refuses
    the call; a function that raised on its own behalf would leave the log line to whoever
    remembered to write it.

    **Why an empty claim is not a mismatch.** The claim is empty on the stdio transport and
    whenever local JWKS validation is disabled, which is the default. "Unknown" must read as
    "no local opinion, let the service that owns the data decide" - treating it as a mismatch
    would make every stdio tool call fail, and treating it as agreement is exactly what the
    empty return value says.

    :param requested: The tenant the tool was asked to act on.
    :returns: The conflicting ``tenant_id`` claim, or ``""`` when there is no conflict - either
        because the claim is unknown or because it matches.
    """
    claimed = _REQUEST_TENANT.get()
    if claimed and claimed != requested:
        return claimed
    return ""


def bearer_token(configured: str) -> str:
    """Resolve the token to forward: the per-request one when present, else the configured one.

    :param configured: :attr:`Settings.bearer_jwt` unwrapped by the caller. Taken as a plain
        ``str`` rather than a ``SecretStr`` so this module never holds the secret type and
        cannot accidentally log a partially-unwrapped one.
    :returns: The raw token, possibly empty.
    """
    return _REQUEST_JWT.get() or configured


def auth_headers(configured: str, tenant_id: str) -> dict[str, str]:
    """Build the headers every outbound call to a W3 D1 service carries.

    ``X-Tenant`` accompanies the JWT rather than replacing it. The token is the *authority* -
    the service verifies its signature and scopes - while the header is the *selector*, naming
    which of the tenants that token may reach this call is for. A service that trusted the
    header alone would let any authenticated caller read any tenant.

    :param configured: :attr:`Settings.bearer_jwt` unwrapped.
    :param tenant_id: The tenant the tool was asked to act on.
    :returns: ``Authorization`` and ``X-Tenant`` headers.
    """
    return {
        "Authorization": f"Bearer {bearer_token(configured)}",
        "X-Tenant": tenant_id,
    }


def parse_bearer(header_value: str | None) -> str:
    """Extract the raw token from an ``Authorization`` header value.

    Case-insensitive on the scheme because RFC 7235 says the scheme is, and a client that sends
    ``bearer`` rather than ``Bearer`` is correct even though it is unusual - rejecting it would
    be this server inventing a stricter rule than the standard for no benefit.

    :param header_value: The raw header, or ``None`` when absent.
    :returns: The token, or ``""`` when the header is missing or not a bearer credential.
    """
    if not header_value:
        return ""
    scheme, _, token = header_value.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()
