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

from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Final
from uuid import UUID

from langsmith import traceable
from pydantic import BaseModel, ConfigDict, Field, field_validator

from taxcalc_mcp_server.app import ctx, mcp
from taxcalc_mcp_server.errors import _map_http
from taxcalc_mcp_server.observability import observe
from taxcalc_mcp_server.tenancy import auth_headers

#: Scale every money value is carried at, matching the Java side's `BigDecimal` discipline
#: (scale 2, HALF_UP). Declared here rather than assumed so the rule is one constant rather than
#: a convention re-derived at each call site.
MONEY_SCALE: Final[int] = 2

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


class CreateRefundArgs(BaseModel):
    """Arguments for ``orders.create_refund`` - the one tool on this server that moves money."""

    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(min_length=1, max_length=64, description="Order to refund.")
    amount: Decimal = Field(
        gt=Decimal("0"),
        description=(
            "Refund amount as a decimal string, e.g. '10.00'. At most two decimal places. "
            "Send a JSON string, not a JSON number."
        ),
    )
    reason: str = Field(min_length=4, max_length=200, description="Why the refund is being issued.")
    tenant_id: str = Field(pattern=TENANT_PATTERN, description="Owning tenant, e.g. tenant-a.")
    idempotency_key: UUID = Field(
        description=(
            "UUID v4. Retrying with the same key returns the original refund instead of "
            "issuing a second one."
        )
    )

    @field_validator("amount", mode="before")
    @classmethod
    def _reject_float(cls, value: object) -> object:
        """Refuse a binary float before pydantic gets a chance to coerce one.

        ``Decimal(0.1)`` is ``0.1000000000000000055511151231257827``: by the time a float reaches
        the model the exact value the caller meant is already gone, and coercing it produces a
        Decimal that merely *looks* precise. The published schema asks for a string for this
        reason; this validator is what makes the request enforceable rather than advisory, and
        what turns "the model sent a JSON number" into a validation error the model can read and
        correct rather than a cent that quietly goes missing from a ledger.

        :param value: The raw ``amount`` as supplied.
        :returns: ``value`` unchanged when it is not a float.
        :raises ValueError: if ``value`` is a float.
        """
        if isinstance(value, float):
            raise ValueError(
                "amount must be a decimal string such as '10.00', not a JSON number: a binary "
                "float cannot represent a money value exactly"
            )
        return value

    @field_validator("amount")
    @classmethod
    def _enforce_money_scale(cls, value: Decimal) -> Decimal:
        """Reject an amount carrying more precision than money has.

        ``10.001`` is not a refundable amount - there is no such coin. Accepting it would push
        the decision about what to do with the third decimal place onto whichever layer rounds
        first, and that layer is different depending on whether the request reaches the ledger
        through this tool, the REST API, or a batch job. Rejecting is the only answer that is
        the same everywhere.

        Note this does NOT quantise: the caller's value is returned untouched when it is valid.
        Silently rounding ``10.001`` to ``10.00`` would refund a different amount than the one
        the caller asked for and tell nobody.

        :param value: The parsed amount.
        :returns: ``value`` unchanged.
        :raises ValueError: if ``value`` has more than :data:`MONEY_SCALE` decimal places.
        """
        if value != value.quantize(Decimal(1).scaleb(-MONEY_SCALE), rounding=ROUND_HALF_UP):
            raise ValueError(
                f"amount must have at most {MONEY_SCALE} decimal places; got {value}"
            )
        return value


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


class RefundView(BaseModel):
    """What a completed refund reports back.

    ``refund_id`` is the field that makes the idempotency contract observable: calling the tool
    twice with one ``idempotency_key`` and comparing the two ``refund_id`` values is how a caller
    - or a test - proves the second call did not issue a second refund.
    """

    model_config = ConfigDict(extra="forbid")

    order_id: str
    refund_id: str
    amount: Decimal
    reason: str
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

_DESC_CREATE_REFUND: Final[str] = (
    "Apply a refund to an existing order. Idempotent: pass the same idempotency_key (UUID v4) "
    "when retrying and the server returns the original outcome instead of debiting the ledger "
    "twice. Use this when the user explicitly asks to refund, credit back, or reverse a charge "
    "on an order. Do NOT use it for partial cancellations, order edits, or to check whether a "
    "refund already happened - call orders.get_order for that. Returns the refund id with the "
    "amount and reason as recorded. The caller's JWT must carry the orders.write scope, which "
    "the order service verifies. "
    "Example: order_id='ord-synth-9001', amount='10.00', reason='duplicate', "
    "tenant_id='tenant-a' returns the refund view with status='refunded'."
)

_ARGS: Final = GetOrderArgs.model_fields
_REFUND_ARGS: Final = CreateRefundArgs.model_fields


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
    async with observe("orders.get_order", args.tenant_id) as span:
        r = await c.http.get(
            f"/orders/{args.order_id}",
            headers=auth_headers(c.settings.bearer_jwt.get_secret_value(), args.tenant_id),
        )
        span["http_status"] = r.status_code
        if r.status_code != 200:
            raise _map_http(r.status_code, r.text)
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


@traceable(name="orders.create_refund", project_name="taxcalc-mcp-server")
async def _create_refund(args: CreateRefundArgs) -> dict[str, object]:
    """Issue a refund through the W3 D1 order service. Traced; called only by the tool handler.

    **The idempotency key travels twice, and both copies are load-bearing.** It goes in the JSON
    body as ``idempotencyKey`` because that is what the order service persists alongside the
    ledger entry, and in the ``Idempotency-Key`` HTTP header because that is what any proxy,
    retry middleware or service mesh between here and there reads. Sending only the body field
    leaves an infrastructure-level retry - the kind this code never sees - free to replay the
    request as a second refund.

    The three ways one refund request becomes two, all of which this closes: the Anthropic
    tool-use loop re-issuing a call whose result it did not see, the W7 D5 LangGraph checkpoint
    resuming a run that had already reached this node, and a multi-agent fan-in where two workers
    independently decide the refund is owed.

    ``str(args.amount)`` puts ``"10.00"`` on the wire rather than a JSON number, so the Java
    ``BigDecimal`` reads the caller's exact value and scale. See the module docstring.

    :param args: Validated arguments.
    :returns: A :class:`RefundView` dumped in JSON mode.
    :raises McpError: Mapped from the upstream status - notably 4090 for a conflicting refund.
    """
    c = ctx()
    async with observe("orders.create_refund", args.tenant_id) as span:
        span["idempotency_key"] = str(args.idempotency_key)

        payload: dict[str, object] = {
            "orderId": args.order_id,
            "amount": str(args.amount),
            "reason": args.reason,
            "idempotencyKey": str(args.idempotency_key),
        }
        headers = auth_headers(c.settings.bearer_jwt.get_secret_value(), args.tenant_id)
        headers["Idempotency-Key"] = str(args.idempotency_key)

        r = await c.http.post(f"/orders/{args.order_id}/refunds", json=payload, headers=headers)
        span["http_status"] = r.status_code
        if r.status_code != 200:
            raise _map_http(r.status_code, r.text)
        return RefundView.model_validate(r.json()).model_dump(mode="json")


@mcp.tool(name="orders.create_refund", description=_DESC_CREATE_REFUND)
async def orders_create_refund(
    order_id: Annotated[str, _REFUND_ARGS["order_id"]],
    amount: Annotated[Decimal, _REFUND_ARGS["amount"]],
    reason: Annotated[str, _REFUND_ARGS["reason"]],
    tenant_id: Annotated[str, _REFUND_ARGS["tenant_id"]],
    idempotency_key: Annotated[UUID, _REFUND_ARGS["idempotency_key"]],
) -> dict[str, object]:
    """Refund an order, idempotently. The MCP boundary for :func:`_create_refund`.

    :param order_id: The order to refund.
    :param amount: Refund amount as a decimal string; at most two decimal places.
    :param reason: Why the refund is being issued.
    :param tenant_id: The owning tenant.
    :param idempotency_key: UUID v4; retries carrying the same key never double-debit.
    :returns: The refund view as a JSON-safe dict.
    """
    return await _create_refund(
        CreateRefundArgs(
            order_id=order_id,
            amount=amount,
            reason=reason,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
        )
    )
