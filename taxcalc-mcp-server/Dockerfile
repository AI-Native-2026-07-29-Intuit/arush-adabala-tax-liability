# taxcalc-mcp-server/Dockerfile
#
# Image for the HTTP+SSE transport only. The stdio transport is launched by Claude Desktop as a
# local subprocess via uvx and never runs in a container; building one image that tried to serve
# both would ship a container whose documented entrypoint is a process that reads stdin.
#
# Two stages. The builder resolves and installs dependencies; the runtime carries the virtualenv
# and the source, and none of uv, the lock resolution or the build toolchain. That is not only
# size: a runtime image containing a package manager is a runtime image in which a compromised
# process can install things.

FROM python:3.12-slim AS builder

# uv from its official distroless image rather than `pip install uv`: the binary arrives at a
# pinned version without a pip resolution step, and nothing is added to the runtime image.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# The W7 D3 sidecar is a path dependency, so its source must be present before the lock can be
# installed. Copied first and separately because it changes far less often than this server's
# own code, and Docker's layer cache is keyed on what changed.
COPY taxcalc-ai/ /taxcalc-ai/

# Dependencies before source: the lockfile changes on a dependency bump, the source changes on
# every commit. Installed in this order, an ordinary code change reuses the dependency layer
# instead of re-resolving and re-downloading torch.
COPY taxcalc-mcp-server/pyproject.toml taxcalc-mcp-server/uv.lock /app/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY taxcalc-mcp-server/src/ /app/src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.12-slim AS runtime

# A non-root user, created before the copy so the virtualenv is not owned by the process that
# runs it. A server that cannot write to its own code is a server whose code cannot be rewritten
# by a request that gets further than it should.
RUN useradd --create-home --uid 10001 mcp

WORKDIR /app
COPY --from=builder --chown=mcp:mcp /taxcalc-ai /taxcalc-ai
COPY --from=builder --chown=mcp:mcp /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TAXCALC_MCP_HOST=0.0.0.0 \
    TAXCALC_MCP_PORT=8080

USER mcp
EXPOSE 8080

# Probes /sse - the endpoint the W7 D5 agent actually connects to - rather than a dedicated
# health route that would return 200 from a process whose transport never started. An
# unauthenticated probe is expected to be REFUSED, and the script treats 401 as healthy: it
# proves the server is listening and that the bearer middleware is in front of it. See
# scripts/healthcheck.py for why the alternatives are worse.
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-m", "taxcalc_mcp_server.scripts.healthcheck"]

ENTRYPOINT ["taxcalc-mcp-server-sse"]
