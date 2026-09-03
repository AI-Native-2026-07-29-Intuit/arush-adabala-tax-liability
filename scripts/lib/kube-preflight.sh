# scripts/lib/kube-preflight.sh - sourced, not executed.
#
# Guarantees the calling script has a WORKING kubectl on PATH, and fails immediately with an
# actionable message if it cannot find one.
#
# Why this exists: Rancher Desktop puts ~/.rd/bin first on PATH, and that kubectl is `kuberlr`, a
# shim that downloads a client matching the cluster's version on first use. On an Apple Silicon
# machine talking to a k3s cluster it tries to fetch a darwin/AMD64 binary and gets a 404, so
# every kubectl invocation dies before doing anything:
#
#   Right kubectl missing, downloading version 1.28.15+k3s1
#   ... returned http status 404 Not Found
#
# Scripts that redirect kubectl's stderr (a backgrounded `port-forward`, say) turn that into a
# silent nothing, and the caller sees a timeout with no cause - which is exactly how this was
# found. Homebrew's kubectl sits second on PATH and works fine; this prefers whichever one can
# actually run, and exports the choice so every later `kubectl` in the script picks it up.
#
# Override explicitly with KUBECTL=/path/to/kubectl if you want a specific client.

_kube_works() { [ -x "$1" ] && "$1" version --client >/dev/null 2>&1; }

if [ -n "${KUBECTL:-}" ]; then
  _kube_works "${KUBECTL}" || { echo "ERROR: KUBECTL=${KUBECTL} cannot run 'version --client'." >&2; exit 1; }
else
  KUBECTL="$(command -v kubectl 2>/dev/null || true)"
  if ! _kube_works "${KUBECTL:-}"; then
    KUBECTL=""
    # `which -a` order, minus the one that just failed.
    for _candidate in $(command -v -a kubectl 2>/dev/null || true) /opt/homebrew/bin/kubectl /usr/local/bin/kubectl; do
      if _kube_works "${_candidate}"; then KUBECTL="${_candidate}"; break; fi
    done
  fi
fi

if [ -z "${KUBECTL}" ]; then
  cat >&2 <<'MSG'
ERROR: no working kubectl found.

The kubectl first on your PATH cannot run. If it is Rancher Desktop's ~/.rd/bin/kubectl, it is a
kuberlr shim that tries to download a client matching the cluster and fails on Apple Silicon:

  Right kubectl missing, downloading version 1.28.15+k3s1
  ... storage.googleapis.com/.../darwin/amd64/kubectl.sha256 returned http status 404 Not Found

Fix it either way:
  export PATH=/opt/homebrew/bin:$PATH      # prefer the real kubectl for this shell
  KUBECTL=/opt/homebrew/bin/kubectl ./scripts/observability-smoke.sh   # or per-invocation
MSG
  exit 1
fi

# Put the working client first so every plain `kubectl` below resolves to it.
PATH="$(cd "$(dirname "${KUBECTL}")" && pwd):${PATH}"
export PATH KUBECTL
unset _candidate
