# taxcalc-mcp-server/src/taxcalc_mcp_server/tools/orders.py
"""``orders.*`` tools: typed Pydantic args, ``Decimal`` for money, structured errors.

**No business logic lives here.** Every handler validates, forwards to the W3 D1 order service,
and re-shapes the reply. That is the whole job of an MCP adapter, and keeping to it is what
makes the Java service remain the single place an order's rules are expressed - a second copy of
"can this order be refunded" in Python would drift from the first within a sprint.

**Money is :class:`~decimal.Decimal`, and it travels as a string.** ``0.1 + 0.2`` is not ``0.3``
in binary floating point, and a refund is a ledger entry. The wire format matters as much as the
in-memory type: serialising ``amount`` as ``str(args.amount)`` means the JSON body carries
``"10.00"``, which the Java service's ``BigDecimal`` reads exactly. Sending it as a JSON *number*
would hand the exactness back to whichever float parser sees it first, and would also lose the
scale - ``10.00`` and ``10`` are the same number and a different money value.

**The argument constraints are declared once.** Each tool's input model is the source of truth,
and the handler's signature reuses that model's ``FieldInfo`` objects through ``Annotated``. The
handlers take flat parameters (not a single ``args`` model) because that is what a flat
``tools/call`` ``arguments`` object maps onto - the shape every LLM client emits - while the
model keeps the constraints, the descriptions and ``extra="forbid"`` in one place. Declaring the
constraints a second time in the signature would let the published schema and the validated
model disagree, which is the exact failure the schema exists to prevent.
"""

# NOTE: no `from __future__ import annotations` in this module, deliberately.
#
# With it, every annotation becomes a STRING that something later has to evaluate. FastMCP builds
# each tool's argument model by calling `get_type_hints` on the registered callable - and the
# registered callable is the `@traceable` wrapper, whose `__globals__` are langsmith's module,
# not this one. The strings then fail to resolve ("`_ARGS` is not defined") and tool registration
# dies at import. Real annotation objects need no evaluation and no namespace, so they survive
# being wrapped. Every other module in this package keeps the future import; only the two that
# register decorated handlers go without it.

from decimal import Decimal
from typing import Annotated, Final

from langsmith import traceable
from pydantic import BaseModel, ConfigDict, Field

from taxcalc_mcp_server.app import ctx, log, mcp
from taxcalc_mcp_server.errors import _map_http
from taxcalc_mcp_server.tenancy import auth_headers

#: Tenant ids this deployment serves. A pattern rather than a free string so a malformed tenant
#: (``"tenant-d"``, or an injection attempt) is rejected by schema validation *before* any HTTP
#: call is made - a request never sent cannot be a request that leaked a tenant id into another
#: tenant's logs.
TENANT_PATTERN: Final[str] = r"^tenant-[abc]$"


# ---- Input schemas --------------------------------------------------------------------------


class GetOrderArgs(BaseModel):
    """Arguments for ``orders.get_order``."""

    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(min_length=1, max_length=64, description="Order id, e.g. ord-synth-9001.")
    tenant_id: str = Field(
        pattern=TENANT_PATTERN, description="Owning tenant, e.g. tenant-a."
    )


# ---- Output schemas (pre-shaped; see the module docstring) ----------------------------------


class OrderView(BaseModel):
    """The four fields of an order a model needs, and nothing else.

    Pre-shaped rather than passed through. The upstream order record carries line items,
    addresses, timestamps and audit fields; every one of them that reaches the tool result is
    spent from the model's context window on every subsequent turn of the conversation, forever,
    whether or not the question needed them. Four fields answer "what is the state of this
    order"; the rest is a second tool call away if it is ever wanted.
    """

    model_config = ConfigDict(extra="forbid")

    order_id: str
    tenant_id: str
    #: Decimal, not float - and serialised back out as a string. See the module docstring.
    total: Decimal
    status: str


# ---- Tool descriptions ----------------------------------------------------------------------
#
# These strings are routing logic, not documentation. They are the ONLY thing an LLM client sees
# when deciding which tool to call, so each one states what the tool returns, when to reach for
# it, when explicitly not to (naming the tool that should be used instead), and closes with a
# concrete call and its outcome. tests/test_tool_descriptions.py enforces that shape, because a
# vague description fails silently: the model simply does not call the tool, and no error is
# raised anywhere for anyone to find.

_DESC_GET_ORDER: Final[str] = (
    "Fetch a single order by id for the caller's tenant. Returns the order id, tenant id, "
    "total (a Decimal serialised as a string) and status. Use this when the user asks to look "
    "up, check, view, or read the state of an existing order, or when another step needs the "
    "order's current total or status before acting. Do NOT use this to modify the order and do "
    "NOT use it to issue money back - call orders.create_refund for that. "
    "Example: order_id='ord-synth-9001', tenant_id='tenant-a' returns the order with "
    "status='paid'."
)

_ARGS: Final = GetOrderArgs.model_fields


# ---- Tool handlers --------------------------------------------------------------------------
#
# Each tool is TWO functions, and the split is load-bearing rather than stylistic.
#
# The outer one carries `@mcp.tool` and owns the protocol boundary: its signature IS the
# published JSON Schema, so it takes flat, individually-constrained parameters and validates them
# into the input model. The inner one carries `@traceable` and does the work.
#
# Stacking both decorators on one function does not work here. `@traceable` returns a wrapper
# whose signature carries langsmith's own keyword arguments (`config`, `langsmith_extra`), and
# FastMCP derives the schema from the callable it is handed - so a single stacked function
# publishes a phantom `config` string parameter that a model can see, try to fill, and be
# confused by. Splitting them means the schema comes from a signature this module controls
# completely, and the span still wraps every byte of real work.


@traceable(name="orders.get_order", project_name="taxcalc-mcp-server")
async def _get_order(args: GetOrderArgs) -> dict[str, object]:
    """Read one order through the W3 D1 order service. Traced; called only by the tool handler.

    :param args: Validated arguments.
    :returns: An :class:`OrderView` dumped in JSON mode, so ``total`` is a string and not a
        float. ``model_dump(mode="json")`` rather than ``model_dump()`` is the line that keeps
        the Decimal discipline true all the way out of the process.
    :raises McpError: Mapped from the upstream status by
        :func:`taxcalc_mcp_server.errors._map_http` - 4040 when the order does not exist, 4030
        when the JWT may not read it.
    """
    c = ctx()
    log.info("tool.invoke.start", tool="orders.get_order", tenant_id=args.tenant_id)

    r = await c.http.get(
        f"/orders/{args.order_id}",
        headers=auth_headers(c.settings.bearer_jwt.get_secret_value(), args.tenant_id),
    )
    if r.status_code != 200:
        log.info(
            "tool.invoke.end",
            tool="orders.get_order",
            tenant_id=args.tenant_id,
            http_status=r.status_code,
        )
        raise _map_http(r.status_code, r.text)

    log.info("tool.invoke.end", tool="orders.get_order", tenant_id=args.tenant_id, http_status=200)
    return OrderView.model_validate(r.json()).model_dump(mode="json")


@mcp.tool(name="orders.get_order", description=_DESC_GET_ORDER)
async def orders_get_order(
    order_id: Annotated[str, _ARGS["order_id"]],
    tenant_id: Annotated[str, _ARGS["tenant_id"]],
) -> dict[str, object]:
    """Fetch a single order by id. The MCP boundary for :func:`_get_order`.

    :param order_id: The order to fetch.
    :param tenant_id: The owning tenant; forwarded as ``X-Tenant`` and enforced upstream.
    :returns: The order view as a JSON-safe dict.
    """
    return await _get_order(GetOrderArgs(order_id=order_id, tenant_id=tenant_id))
