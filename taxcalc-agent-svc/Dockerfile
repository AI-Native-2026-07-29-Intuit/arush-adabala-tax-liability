# taxcalc-agent-svc/Dockerfile
#
# The agent service, as a slim two-stage image. Same shape as taxcalc-mcp-server's, and
# deliberately so - two Python services in one repository that are containerised two different
# ways is two sets of build-cache behaviour and two places to fix the next CVE.
#
# Two stages. The builder resolves and installs dependencies; the runtime carries the virtualenv
# and the source and none of uv, the lock resolution or the build toolchain. That is not only
# size: a runtime image containing a package manager is a runtime image in which a compromised
# process can install things.
#
# BUILD CONTEXT IS THE REPOSITORY ROOT, not this directory. Two path dependencies - the W7 D3
# sidecar and the W7 D4 MCP server - live beside this project, and a context rooted here could
# not see either. Build with:
#     docker build -f taxcalc-agent-svc/Dockerfile -t taxcalc-agent-svc:dev .

FROM python:3.12-slim AS builder

# uv from its official distroless image rather than `pip install uv`: the binary arrives at a
# pinned version without a pip resolution step, and nothing is added to the runtime image.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# The two path dependencies must be present before the lock can be installed. Copied first and
# separately because they change far less often than this service's own code, and Docker's layer
# cache is keyed on what changed.
COPY taxcalc-ai/ /taxcalc-ai/
COPY taxcalc-mcp-server/ /taxcalc-mcp-server/

# Dependencies before source: the lockfile changes on a dependency bump, the source changes on
# every commit. In this order an ordinary code change reuses the dependency layer instead of
# re-resolving and re-downloading torch.
COPY taxcalc-agent-svc/pyproject.toml taxcalc-agent-svc/uv.lock /app/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY taxcalc-agent-svc/src/ /app/src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# --no-dev on both syncs, and it is load-bearing rather than tidy. The dev group carries RAGAS,
# which pulls `datasets`, a second LLM client and their transitive graph - hundreds of megabytes
# that exist to GRADE this service in CI and have no business inside the thing being graded. It
# also means `evals/` is absent from the image by construction: a production container cannot
# accidentally run the eval suite against production.


FROM python:3.12-slim AS runtime

# A non-root user, created before the copy so the virtualenv is not owned by the process that
# runs it. A service that cannot write to its own code is a service whose code cannot be
# rewritten by a request that gets further than it should.
RUN useradd --create-home --uid 10001 agent

WORKDIR /app
COPY --from=builder --chown=agent:agent /taxcalc-ai /taxcalc-ai
COPY --from=builder --chown=agent:agent /taxcalc-mcp-server /taxcalc-mcp-server
COPY --from=builder --chown=agent:agent /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TAXCALC_AGENT_HOST=0.0.0.0 \
    TAXCALC_AGENT_PORT=8080

USER agent
EXPOSE 8080

# Probes /healthz, which deliberately does NOT reach the MCP server or Postgres. A health check
# that depended on every downstream would turn one dependency's blip into a cascading restart of
# a service that was working fine - and, because Kubernetes restarts on a failing liveness probe,
# would take the pod down precisely when its dependency needed it to keep serving from cache.
# Readiness for the dependencies belongs in the readiness probe, which the Argo CD manifests own.
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).status == 200 else 1)"]

# The console script from [project.scripts], so the container runs exactly what a developer runs.
ENTRYPOINT ["taxcalc-agent-svc"]
