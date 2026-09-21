# taxcalc-agent-svc/Dockerfile
#
# The agent service, as a slim two-stage image. Same shape as taxcalc-mcp-server's, and
# deliberately so - two Python services in one repository that are containerised two different
# ways is two sets of build-cache behaviour and two places to fix the next CVE.
#
# Two stages. The builder resolves and installs dependencies into the system interpreter; the
# runtime carries those site-packages and the source and none of uv or the build toolchain. That
# is not only size: a runtime image containing a package manager is a runtime image in which a
# compromised process can install things.
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

# The two path dependencies must be present before the install can resolve them. Copied first
# and separately because they change far less often than this service's own code, and Docker's
# layer cache is keyed on what changed. The paths are absolute-by-construction: `pyproject.toml`
# names them as `../taxcalc-ai` and `../taxcalc-mcp-server` relative to /app, which is exactly
# where these two land.
COPY taxcalc-ai/ /taxcalc-ai/
COPY taxcalc-mcp-server/ /taxcalc-mcp-server/

COPY taxcalc-agent-svc/pyproject.toml /app/
COPY taxcalc-agent-svc/src/ /app/src/

# `uv pip install --system .` - into the image's own interpreter, with no virtualenv.
#
# A venv inside a container is a layer of indirection with nothing on the other side of it: the
# container IS the isolation boundary, and a venv only adds a PATH that the next person to write
# a `docker exec` line gets wrong. --system says so explicitly rather than relying on uv to guess.
#
# TWO THINGS THIS DEPENDS ON, both verified rather than assumed:
#
# 1. uv's pip interface reads `[tool.uv.sources]` from this pyproject, so the two path
#    dependencies resolve to /taxcalc-ai and /taxcalc-mcp-server instead of being looked up on
#    PyPI - where `taxcalc-ai` is not published and the name is not even reserved. (`--no-sources`
#    is the opt-out, which is the proof the default is to honour them.)
# 2. `[[tool.uv.index]] pytorch-cpu` is honoured the same way, which is what keeps the CUDA
#    wheels out: torch arrives transitively through taxcalc-ai's reranker, and the default PyPI
#    Linux wheel bundles a CUDA runtime this service will never touch. Built and measured:
#    torch 2.14.0+cpu, 1.63 GB of image, against the 9.19 GB the unpinned resolution produced
#    with its 43 nvidia-* packages (PR_BODY.md's table).
#
# NOT installed: the `dev` group, which carries RAGAS and its transitive graph (datasets, a
# second LLM client). That is hundreds of megabytes whose job is to GRADE this service in CI, and
# it has no business inside the thing being graded. `uv pip install .` takes `[project]
# dependencies` only - dependency groups are a project-interface concept - so the exclusion here
# is structural rather than a flag someone can drop.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system .


FROM python:3.12-slim AS runtime

# A non-root user, created before the copy so the installed packages are not owned by the process
# that runs them. A service that cannot write to its own code is a service whose code cannot be
# rewritten by a request that gets further than it should.
RUN useradd --create-home --uid 10001 agent

WORKDIR /app

# The two path dependencies are installed EDITABLE (`editable = true` in [tool.uv.sources]), so
# site-packages holds `.pth` files pointing at /taxcalc-ai and /taxcalc-mcp-server rather than
# copies. Those two trees are therefore part of the runtime image, at the same absolute paths the
# builder installed them from - drop either and every import of taxcalc_ai fails at startup.
#
# Which makes the repository's `.dockerignore` part of THIS file's contract: copying those trees
# wholesale also copies whatever host virtualenv is sitting in them. Measured before `**/.venv`
# was excluded: 4.51 GB of image, 2.9 GB of it macOS-built wheels that a linux container cannot
# execute and that every pull, rollout and CVE scan paid for anyway.
COPY --from=builder --chown=agent:agent /taxcalc-ai /taxcalc-ai
COPY --from=builder --chown=agent:agent /taxcalc-mcp-server /taxcalc-mcp-server
COPY --from=builder --chown=agent:agent /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder --chown=agent:agent /usr/local/bin /usr/local/bin
COPY --from=builder --chown=agent:agent /app/src /app/src

ENV PYTHONUNBUFFERED=1 \
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

# uvicorn directly, on 8080, rather than the `taxcalc-agent-svc` console script. The script is
# `app:run`, which reads host and port from Settings - convenient locally, and one indirection
# too many here: the port the container EXPOSEs, the port the HEALTHCHECK probes and the port the
# server binds should be one visible number, not a number and an environment variable that has to
# agree with it. `--factory` because create_app() builds the app; passing the module attribute
# would import a FastAPI instance that does not exist at module scope.
CMD ["uvicorn", "taxcalc_agent_svc.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
